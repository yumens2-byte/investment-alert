# Investment-alert Phase 1 — 위기감지 고도화 최종 배포

## X Following Engagement Agent

기존 Alert/X 발행 흐름과 독립된 `following_engagement` 패키지가 공식 X Home
Timeline API로 팔로잉 게시물을 증분 수집하고, 저비용 필터 → OpenAI 구조화 분석 →
공통 의사결정 → 실행 모드 Router 순서로 처리한다. 브라우저 자동화는 사용하지 않는다.

### 안전한 초기 설정

신규 Workflow(`.github/workflows/following-agent.yml`)는 아래 값이 기본값이므로 merge만으로
수집 또는 게시되지 않는다.

```text
FOLLOWING_ENABLED=false
EXECUTION_MODE=DRY_RUN
```

Repository Variable `FOLLOWING_ENABLED=true`로 명시해야 실행된다. 이후
`FOLLOWING_EXECUTION_MODE`를 `DRY_RUN` → `SHADOW` 순서로 검증하고, 운영 승인 후에만
`LIVE`로 변경한다. 알 수 없는 실행 모드는 fail-safe로 `DRY_RUN`이 된다.

| 모드 | 판단 파이프라인 | SQLite 감사 기록 | X Write |
|---|---|---|---|
| `DRY_RUN` | 실행 | 미적재 | 0 |
| `SHADOW` | 실행 | `would_execute=1` | 0 |
| `LIVE` | 실행 + 최종 Guard | 실행 결과 적재 | allowlist만 |

### GitHub 설정

필수 Secrets는 `X_USER_ID`, `OPENAI_API_KEY`와 기존 X 발행/대댓글 파이프라인에서 사용하는
`X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_TOKEN_SECRET`이다. 신규 Timeline
Read도 동일한 OAuth 1.0a Key 이름과 인증 방식을 사용한다. 값은 코드, 로그, state DB에
저장하지 않는다. 선택 Variables는 `FOLLOWING_INCLUDE_TOPICS`,
`FOLLOWING_EXCLUDE_TOPICS`, `FOLLOWING_BLOCKED_AUTHORS`, `FOLLOWING_PRIORITY_AUTHORS`,
`MAX_FETCH_COUNT`, `MAX_ACTIONS_PER_RUN`, `MAX_ACTIONS_PER_DAY`,
`SAME_AUTHOR_COOLDOWN_HOURS`, `LIVE_ACTION_ALLOWLIST`이다.

Checkpoint와 Shadow 이력은 `state/following_agent.sqlite3`에 저장되고 Workflow cache로 다음
실행에 복원된다. SHADOW/LIVE 실행 DB는 30일 artifact로도 보관된다. 실행 결과는
`$GITHUB_STEP_SUMMARY`에서 fetch/AI/candidate/would-execute/write/error/skip 통계를 확인한다.

로컬 안전 확인:

```bash
FOLLOWING_ENABLED=false EXECUTION_MODE=DRY_RUN python -m following_engagement.main
python -m pytest tests/test_following_engagement.py --no-cov
```

Rollback은 Workflow를 disable하거나 `FOLLOWING_ENABLED=false`로 되돌린 뒤
`following_engagement/`, Workflow 및 state cache를 제거하면 된다. 기존 `XPublisher`와 기존
Workflow는 변경하지 않았으므로 기존 발행 동작에는 영향이 없다.

> **작업 기간**: 2026-04-26 ~ Day 6
> **검증**: 193건 테스트 PASS, ruff clean, DRY_RUN E2E 4 시나리오 PASS
> **회귀**: 0건

---

## 배포 순서 (마스터 직접 적용)

### 1. Supabase 마이그레이션 (필수 — 코드 적용 *전*)

`db/migrations/` 의 두 SQL을 Supabase SQL Editor에서 순서대로 실행:

```
1) db/migrations/001_add_reasoning_json.sql
2) db/migrations/002_add_data_quality_state.sql
```

각 SQL 파일에는 롤백 SQL이 주석으로 포함되어 있음.

### 2. GitHub Secret 등록 (필수)

```
TELEGRAM_INTERNAL_CHANNEL_ID  = <D-1 결정에 따른 채널 ID>
```

### 3. GitHub Actions env 추가 (alert.yml)

`.github/workflows/alert.yml` 의 `env:` 블록에 다음 추가:

```yaml
TELEGRAM_INTERNAL_CHANNEL_ID: ${{ secrets.TELEGRAM_INTERNAL_CHANNEL_ID }}
POLICY_VERSION: "v1.0.0"
```

### 4. 코드 파일 복사

본 zip의 모든 파일을 `investment-alert/` 레포의 동일 경로에 덮어쓰기:

| 신규 (5개) | 경로 |
| --- | --- |
| audit_fallback.py | core/ |
| dq_monitor.py | detection/ |
| reasoning_builder.py | detection/ |
| dq_store.py | db/ |
| reasoning_v1.json | docs/ |

| 수정 (6개) | 경로 |
| --- | --- |
| macro_news_layer.py | detection/ |
| alert_engine.py | detection/ |
| alert_store.py | db/ |
| telegram_publisher.py | publishers/ |
| alert_formatter.py | publishers/ |
| run_alert.py | / (루트) |

| 테스트 (5개) | 경로 |
| --- | --- |
| test_dq_monitor.py | tests/ |
| test_reasoning_builder.py | tests/ |
| test_dq_store.py | tests/ |
| test_macro_news_layer.py | tests/ |
| test_day5_integration.py | tests/ |

### 5. 적용 검증

```bash
python -m pytest tests/ --no-cov
# 기대: 193 passed
```

### 6. DRY_RUN 운영

`alert.yml` 워크플로우를 `workflow_dispatch` 로 수동 실행:
```
Actions → Alert Pipeline → Run workflow → dry_run: true → Run
```

로그에서 다음 키워드 확인:
- `[DQMonitor] 정상` 또는 `[DQMonitor] DEGRADED 감지`
- `[DQStore] save_dq_state 완료: id=N`
- `[ReasoningBuilder] v1.0.0 schema=1.0`
- `[run_alert] 완료: ... audit_persisted=True`

### 7. 본 운영 전환

DRY_RUN 3일 안정 확인 → `DRY_RUN=false` 전환.

---

## 핵심 변화 요약

| FR | 내용 | 영향 |
| --- | --- | --- |
| FR-03 | SYSTEM_DEGRADED 단계 신설 | 수집 실패가 silent 아닌 운영 채널 경보로 전환 |
| FR-04 | L3 내부 발행 | 조기 전조를 운영자가 사전 인지 |
| FR-05 | reasoning_json 표준화 | ia_alert_history.reasoning_json + policy_version 컬럼 |
| B3 (코드리뷰 발견) | feedparser timeout 수정 | 네트워크 hang 위험 제거 — 패치 가이드만 제공, 별도 작업 |
| B5 (코드리뷰 발견) | save_alert 실패 시 audit fallback | 발행됐는데 미기록되는 silent 누락 방지 |

## 신규 동작

### PUBLISH_POLICY (5×4)

| Level | x | tg_free | tg_paid | tg_internal |
| --- | --- | --- | --- | --- |
| L1 | T | T | T | T |
| L2 | F | T | T | T |
| L3 | F | F | F | **T** (신규) |
| SYSTEM_DEGRADED | F | F | F | **T** (신규) |
| NONE | F | F | F | F |

### audit fallback

`save_alert` 실패 시 `logs/alert_audit_fallback.jsonl`에 1줄 기록. GitHub Actions artifacts 14일 보관으로 사후 추적 가능.

---

## 패치 가이드 (참고)

`_patches/` 디렉토리에는 본 작업의 단계별 적용 가이드가 포함되어 있음:
- `M01_PATCH_GUIDE.md` — macro_news_layer.py 9단계 패치
- `M13_PATCH_GUIDE.md` — test_macro_news_layer.py 7+3건 갱신
- `B3_PATCH_GUIDE.md` — feedparser timeout 수정 (옵션 B)
- `B5_PATCH_GUIDE.md` — save_alert 실패 시 audit fallback (이미 본 zip에 적용 완료)

B3는 본 zip에 *미적용*. 별도 작업으로 분리 권장.

---

## 롤백

각 패치 가이드에 단계별 롤백 SQL/코드 포함. 또한 본 zip 적용 *전*의 원본 파일을 마스터 환경에서 백업 후 진행 권장.

---

## Notion 산출물

- 인덱스: https://www.notion.so/34d9208cbdc38109ab46fb1b365dd048
- 06 마스터 보고서: https://www.notion.so/34d9208cbdc38138aeb5d32914c2b396

---

## 검증 결과 요약

```
신규 테스트:       31건 (N-06 10 + N-07 8 + N-03 5 + Day-5 8)
회귀 테스트:       162건
전체 슈트:         193건 PASS
회귀 발생:         0건

ruff lint:        All checks passed
DRY_RUN E2E:      4/4 시나리오 PASS
  - 정상 L1 흐름
  - SYSTEM_DEGRADED (수집 실패)
  - save_alert 실패 → audit fallback
  - NONE (회귀 보호)
```


# Sector Flow Alert — 배포 패키지

> **버전**: sector-v1.0.0
> **빌드일**: 2026-05-11
> **테스트**: 파일럿 21/21 PASS + 전수 353/353 PASS + ruff clean + coverage 96%

---

## 신규 파일 (9개)

| # | 경로 | 용도 |
| --- | --- | --- |
| 1 | `config/sector_groups.py` | defensive/cyclical 그룹 정의 |
| 2 | `collectors/sector_collector.py` | Yahoo Finance API 수집기 |
| 3 | `db/sector_flow_store.py` | ia_sector_flow_daily 적재/조회 |
| 4 | `db/migrations/003_add_sector_flow_daily.sql` | Supabase 테이블 마이그레이션 |
| 5 | `detection/sector_flow_layer.py` | 변화 감지 알고리즘 |
| 6 | `detection/sector_alert_engine.py` | SectorSignal 생성 + 발행 정책 |
| 7 | `publishers/sector_formatter.py` | TG HTML 메시지 포맷 |
| 8 | `run_sector_alert.py` | 엔트리포인트 |
| 9 | `.github/workflows/sector_alert.yml` | GitHub Actions 워크플로우 |

## 수정 파일 (2개)

| # | 경로 | 변경 내용 |
| --- | --- | --- |
| 1 | `db/alert_store.py` | `COOLDOWN_MINUTES` dict에 sector key 2개 추가 (line 32~37 부근) |
| 2 | `scripts/ci_preflight.sh` | sector 핵심 테스트 1건 추가 (마지막 줄) |

## 신규 테스트 파일 (6개)

| # | 경로 | 케이스 수 |
| --- | --- | --- |
| 1 | `tests/test_sector_collector.py` | 7 |
| 2 | `tests/test_sector_flow_store.py` | 8 |
| 3 | `tests/test_sector_flow_layer.py` | 9 |
| 4 | `tests/test_sector_alert_engine.py` | 7 |
| 5 | `tests/test_sector_formatter.py` | 6 |
| 6 | `tests/test_run_sector_alert.py` | 4 |
| **합계** | | **41건** |

---

## 적용 순서 (마스터 직접 진행)

### 1단계: Supabase 마이그레이션 (코드 적용 *전*)

Supabase SQL Editor에서 다음 파일 실행:

```
db/migrations/003_add_sector_flow_daily.sql
```

확인 쿼리:
```sql
SELECT COUNT(*) FROM ia_sector_flow_daily;  -- 0 반환되어야 함
```

### 2단계: 파일 업로드 (GitHub 웹에디터)

zip의 17개 파일을 각각의 경로에 그대로 업로드.
**수정 파일 2건 주의** — 기존 파일을 덮어쓰기:
- `db/alert_store.py` (line 32~37 dict 확장됨)
- `scripts/ci_preflight.sh` (마지막 pytest 라인 확장됨)

### 3단계: CI 통과 확인

`test.yml` 워크플로우가 자동 실행됨. 결과 확인:
- ruff check: All checks passed
- pytest: 353+ passed (회귀 0건)
- coverage: >=80% (신규 모듈 평균 96%)

### 4단계: DRY_RUN 검증

GitHub Actions UI에서 `Sector Flow Alert` 워크플로우를 `workflow_dispatch`로 수동 실행:
- Inputs: `dry_run=true`
- 로그 확인:
  - `[run_sector_alert] v1.0.0 시작`
  - `[SectorCollector] 수집 완료: 6/6 ticker`
  - `[SectorFlowStore] upsert 완료: 6 rows`
  - `[SectorFlowLayer] 감지 결과: level=NONE...` (첫 실행은 5일 데이터 부족 → NONE 예상)

### 5단계: cron 자동 실행 1주 모니터링

- 평일 KST 08:00 자동 실행
- 매일 6 row씩 적재 (5일 채워지면 감지 동작)
- 로그 artifact 14일 보관됨

### 6단계: SHADOW 운영 (Phase 1, 2주)

`.github/workflows/sector_alert.yml`에서:
- `DRY_RUN: 'false'` (실제 발행)
- `SECTOR_SHADOW_MODE: 'true'` (TG Internal만)

2주 검증 항목:
- L2 발생 빈도 (목표: 주 1~2회 이하)
- Yahoo API 응답 안정성 (목표 >=95%)
- 휴장일 스킵 동작
- False positive 비율

### 7단계: Phase 2 정식 전환

`.github/workflows/sector_alert.yml`에서:
- `SECTOR_SHADOW_MODE: 'false'` (TG Free + Paid 활성화)

---

## 검증 결과 요약

```
파일럿 1회차 (알고리즘):       15/15 PASS
파일럿 2회차 (Yahoo 파싱):     6/6 PASS
전수 1회차 (sector 신규):      41/41 PASS
전수 2회차 (전체 회귀):        353/353 PASS, 회귀 0건
ruff check:                    All checks passed
coverage (신규 6 모듈 평균):    96%
```

## 환경변수 (기존 secrets 재사용, 신규 등록 0건)

기존 alert.yml과 동일:
- `SUPABASE_URL`, `SUPABASE_KEY`
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_FREE_CHANNEL_ID`, `TELEGRAM_PAID_CHANNEL_ID`, `TELEGRAM_INTERNAL_CHANNEL_ID`
- `X_API_KEY` 등 (Phase 2 대비)

신규 secret 등록: **없음**

## 롤백 절차

문제 발생 시:
1. `.github/workflows/sector_alert.yml`에서 cron 주석 처리 (즉시 자동 실행 중단)
2. 필요시 Supabase에서 `003_add_sector_flow_daily.sql` 하단 ROLLBACK 섹션 실행
3. 코드 파일 9개 제거 + 수정 파일 2개를 이전 버전으로 복원


# Sector Flow Alert v1.2.0 — 5일 가드 + Supabase 재시도 + cron 추가

> **빌드일**: 2026-05-11
> **변경 사유**: dry_run 정상 동작 검증 후 발견된 잠재 이슈 3건 종합 해결
> **테스트**: 파일럿 1회차 5/5 + 파일럿 2회차 5/5 + 전수 53/53 + 회귀 365/365 + coverage 88%

---

## v1.1.0 → v1.2.0 변경 내역 (5개 파일)

| 파일 | 변경 내용 | 변경 라인 |
| --- | --- | --- |
| `detection/sector_flow_layer.py` | 5일 데이터 충분성 가드 추가 (MIN_ROWS_FOR_5D=24) | 약 18줄 |
| `db/sector_flow_store.py` | upsert/fetch에 1회 재시도 메커니즘 추가 | 약 60줄 (재구조화 포함) |
| `.github/workflows/sector_alert.yml` | 두 번째 cron 추가 (1시간 뒤 자동 재실행) | 1줄 |
| `tests/test_sector_flow_layer.py` | 5일 가드 테스트 3건 추가 | 약 60줄 |
| `tests/test_sector_flow_store.py` | 재시도 테스트 4건 추가 | 약 110줄 |

다른 13개 파일은 v1.1.0과 동일 — 재적용 불필요.

---

## 1) 5일 데이터 충분성 가드 (sector_flow_layer.py)

### 문제

dry_run에서 발견: 1일치 데이터만 누적된 상태에서 `1d_spread == 5d_spread`로 수렴. 만약 1일치 spread만으로 임계 돌파 시 "5일 누적 spread"라는 misleading한 메시지로 알람이 발행될 수 있음.

### 해결

```python
# detection/sector_flow_layer.py v1.1.0
MIN_ROWS_FOR_5D = 24  # 5일 × 6 ticker × 80%

def detect(self) -> SectorRotationResult:
    # ... Step 2: 5일치 row 조회 ...
    rows = self.store.fetch_latest_n_days(n=5)

    # Step 2-1 (v1.1.0): 5일 누적 데이터 충분성 가드
    if len(rows) < MIN_ROWS_FOR_5D:
        return self._build_none_result(
            reason=f"5일 데이터 누적 중 (rows_used={len(rows)} < {MIN_ROWS_FOR_5D}) — 알람 보류",
            rows_used=len(rows),
            health_score=health_score,
        )
```

### 효과

| 시점 | rows_used | v1.0.0 (이전) | v1.1.0 (v1.2.0 포함) |
| --- | --- | --- | --- |
| Day 1 (오늘) | 6 | 큰 1d_spread → L2 알람 발생 가능 | NONE 강제 |
| Day 2 | 12 | 동일 | NONE 강제 |
| Day 3 | 18 | 동일 | NONE 강제 |
| Day 4 | 24 | 동일 | **가드 통과**, 정상 판정 |
| Day 5 | 30 | 정상 | 정상 |

평일 4회 cron 누적 후 정상 알람 시작. 첫 1주 자동 안전 모드.

---

## 2) Supabase 재시도 메커니즘 (sector_flow_store.py)

### 문제

기존 v1.0.0:
- `upsert_daily_rows`: 1회 시도 후 실패 시 `False` 반환 → **메모리 데이터 영구 소실** (가장 큰 위험)
- `fetch_latest_n_days`: 1회 시도 후 빈 리스트 → 알람 누락 위험

### 해결

```python
# db/sector_flow_store.py v1.1.0
_RETRY_MAX = 1        # 1회 재시도
_RETRY_WAIT_SEC = 3   # 3초 대기 (timeout-minutes=10 내 안전)

# 재시도 루프
for attempt in range(_RETRY_MAX + 1):
    try:
        # ... Supabase 호출 ...
        return True
    except Exception as e:
        if attempt < _RETRY_MAX:
            logger.warning(f"... attempt={attempt+1}/{_RETRY_MAX+1} ... 3초 후 재시도")
            time.sleep(_RETRY_WAIT_SEC)
            continue
return False  # 최종 실패
```

### 위험 시나리오 보호

| 시나리오 | v1.0.0 | v1.2.0 |
| --- | --- | --- |
| Supabase 일시 장애 (3초 이내 회복) | 데이터 소실 | 재시도 성공 → 정상 적재 |
| Supabase 장기 장애 (3초 후도 실패) | False 반환 | False 반환 (동일) |
| 정상 호출 | 1회 호출 | 1회 호출 (재시도 발동 안 함) |
| 최대 추가 지연 | 0초 | 3초 (재시도 1회) |

---

## 3) GitHub Actions cron 추가 (sector_alert.yml)

### 문제

기존: `0 23 * * 1-5` 평일 1일 1회만 — 그날 실행 실패 시 24시간 데이터 공백.

### 해결

```yaml
schedule:
  - cron: '0 23 * * 1-5'   # 1차: 평일 23:00 UTC (KST 08:00)
  - cron: '0 0 * * 2-6'    # 2차: 1시간 뒤 자동 재실행 (UNIQUE 멱등성)
```

### 동작 시나리오

| cron 1 (23:00 UTC) | cron 2 (00:00 UTC 다음날) | 효과 |
| --- | --- | --- |
| ✅ 성공 | ✅ 성공 → UNIQUE 충돌 → UPDATE (멱등) | 안전 |
| ❌ 실패 | ✅ 성공 | **1시간 뒤 자동 복구** |
| ✅ 성공 | ❌ 실패 | 1차 데이터 보존, 영향 없음 |
| ❌ 실패 | ❌ 실패 | 마스터에게 워크플로우 알림 |

토요일 00:00 UTC = KST 토 09:00 = 미국 EST 금 19:00 (시장 마감 후) — 정상 처리. 휴장일 가드는 `run_sector_alert.py` Step 1이 자동 처리.

---

## 적용 순서 (v1.1.0에서 v1.2.0 업그레이드)

5개 파일 덮어쓰기:

| # | 경로 | 액션 |
| --- | --- | --- |
| 1 | `detection/sector_flow_layer.py` | **덮어쓰기** (v1.0.0 → v1.1.0) |
| 2 | `db/sector_flow_store.py` | **덮어쓰기** (v1.0.0 → v1.1.0) |
| 3 | `.github/workflows/sector_alert.yml` | **덮어쓰기** (cron 1줄 추가) |
| 4 | `tests/test_sector_flow_layer.py` | **덮어쓰기** (12건으로 확장) |
| 5 | `tests/test_sector_flow_store.py` | **덮어쓰기** (12건으로 확장) |

나머지 13개 파일은 v1.1.0 그대로 유지.

---

## 검증 결과

### 파일럿 1회차 — 5일 가드 동작 (5건)

| 검증 | 결과 |
| --- | --- |
| MIN_ROWS_FOR_5D 상수값 (=24) | ✓ |
| 1일치 + 큰 spread → NONE 강제 (dry_run 재현) | ✓ |
| 5일치 30 row → 정상 L2 판정 | ✓ |
| 3일치 18 row → NONE 유지 + reasoning에 누적 중 표시 | ✓ |
| 경계값 24 row 정확히 → 가드 통과 | ✓ |

### 파일럿 2회차 — Supabase 재시도 (5건)

| 검증 | 결과 |
| --- | --- |
| 재시도 상수 (_RETRY_MAX=1, _RETRY_WAIT_SEC=3) | ✓ |
| upsert 1차 실패 → 2차 성공 → True (sleep 3초) | ✓ |
| upsert 둘 다 실패 → False (raise 안 함) | ✓ |
| fetch 1차 실패 → 2차 성공 → 데이터 반환 | ✓ |
| 정상 호출 — 재시도 발동 안 함 (sleep 0회) | ✓ |

### 전수 1회차 — sector 신규 모듈 (53건)

```
pytest tests/test_sector_*.py tests/test_run_sector_alert.py --no-cov
→ 53 passed in 6.45s
```

| 테스트 파일 | v1.1.0 | v1.2.0 |
| --- | --- | --- |
| test_sector_collector.py | 12 | 12 |
| test_sector_flow_store.py | 8 | **12** (+4 재시도) |
| test_sector_flow_layer.py | 9 | **12** (+3 가드) |
| test_sector_alert_engine.py | 7 | 7 |
| test_sector_formatter.py | 6 | 6 |
| test_run_sector_alert.py | 4 | 4 |
| **합계** | **46** | **53** |

### 전수 2회차 — 전체 회귀 + coverage

```
ruff check                       → All checks passed!
yml syntax                       → OK
pytest tests/ --cov-fail-under=80 → 365 passed (회귀 0건)
Total coverage                    → 88.29% (80% 통과)
```

---

## 운영 효과 예측

### 시나리오 A — 매일 정상 (가장 빈번)
- 23:00 UTC 1차 cron 성공
- 00:00 UTC 2차 cron — UNIQUE 충돌 → UPDATE (멱등, 데이터 동일)
- 결과: 정상 동작, 추가 알람 없음

### 시나리오 B — 1차 실패 + 2차 성공
- 23:00 UTC Yahoo 일시 차단
- 00:00 UTC 1시간 뒤 재시도 → 정상 데이터 적재
- 결과: 데이터 공백 없음

### 시나리오 C — 둘 다 실패
- 23:00 UTC + 00:00 UTC 모두 Yahoo 차단
- 그날 데이터 누락 → 5일 가드가 알람 발행 차단
- 결과: 다음날 cron 정상 시 데이터 복구 (Yahoo 5일 history)

### 시나리오 D — Supabase 일시 장애
- Yahoo 수집 정상
- Supabase upsert 1차 실패 → 3초 대기 → 2차 성공
- 결과: 데이터 보존, 5초 추가 지연만

---

## 향후 일정

- 마스터 v1.2.0 적용 (5개 파일 덮어쓰기) → 즉시 자동 cron 적용
- 평일 4회 cron 실행으로 MIN_ROWS_FOR_5D=24 도달 (약 2026-05-15 목요일까지)
- 그 시점부터 정상 spread 판정 시작
- Shadow 2주 완료 → Phase 2 정식 전환 검토

- # Sector Flow Alert v1.2.1 — 마이너 개선 + 트랙 C 점검 결과 반영

> **빌드일**: 2026-05-11
> **변경 사유**: dry_run 점검 후 발견된 마이너 개선 2건 통합
> **테스트**: ruff clean + yml OK + sector 54/54 + 회귀 366/366 + coverage 88.29%

---

## v1.2.0 → v1.2.1 변경 내역 (3개 파일)

| 파일 | 변경 내용 | 변경 라인 |
| --- | --- | --- |
| `run_sector_alert.py` | VERSION 1.0.0 → 1.0.1, NONE 사유 로그 1줄 추가 | 3줄 |
| `.github/workflows/sector_alert.yml` | timeout-minutes 10 → 15 (worst case 여유 확보) | 1줄 |
| `tests/test_run_sector_alert.py` | reasoning 로그 검증 테스트 1건 추가 | 약 50줄 |

---

## 1) NONE 사유 로그 출력 (`run_sector_alert.py` v1.0.1)

### 문제

기존 Step 5 로그:
```
[run_sector_alert] 감지 결과: level=NONE, rotation=NONE, 5d_spread=None, 1d_spread=None, rows=6, health=0.20
```
→ NONE 사유(5일 가드 발동 / 임계 미달 / 데이터 0건)를 운영자가 즉시 식별 어려움.

### 해결

```python
# run_sector_alert.py v1.0.1
logger.info(f"[run_sector_alert] 감지 결과: level={result.level}, ...")
# v1.0.1: NONE 레벨일 때 사유 명시 (5일 가드 발동/임계 미달 구분)
if result.level == "NONE":
    logger.info(f"[run_sector_alert] NONE 사유: {result.reasoning}")
```

### 효과

| 시나리오 | 새로 출력되는 로그 |
| --- | --- |
| 5일 가드 발동 | `NONE 사유: 5일 데이터 누적 중 (rows_used=6 < 24) — 알람 보류` |
| 임계 미달 | `NONE 사유: NONE — 임계값 미달 또는 데이터 부족 (rows_used=30, health=1.00)` |
| 데이터 0건 | `NONE 사유: 데이터 0건 — DB 조회 실패 또는 미적재` |
| 휴장일 (참고: 휴장일은 NONE 도달 전 sys.exit) | 해당 없음 |

운영자가 로그만으로 즉시 원인 진단 가능.

---

## 2) timeout-minutes 10 → 15 (`sector_alert.yml`)

### 문제

트랙 C-5 점검 결과, worst case 시나리오에서 timeout 11초 초과 가능:

| 단계 | worst case 소요 |
| --- | --- |
| pip install (캐시 사용) | 30초 |
| anti-bot random delay | 400초 (max) |
| Yahoo 수집 (6 ticker × 10초) | 60초 |
| Supabase upsert + 재시도 | 13초 |
| Supabase fetch + 재시도 | 13초 |
| Telegram 3 채널 발행 | 90초 |
| AlertStore set_cooldown | 5초 |
| **합계** | **611초** > 600초 (timeout) |

### 해결

```yaml
# v1.2.1: 10 → 15분 — worst case 여유 확보
timeout-minutes: 15
```

### 효과

| 평균 시나리오 | worst case 시나리오 |
| --- | --- |
| 약 220-250초 (4분) | 약 611초 (10분 11초) |
| **여유 720초** (12분) | **여유 289초** (4분 49초) |

평균 운영 영향 없음 (실제 실행 시간은 변하지 않음). 극단 시나리오에서만 buffer 작동.

---

## 트랙 C 점검 결과 종합

직전 메시지에서 8개 항목 점검. 결과:

| # | 점검 항목 | 결과 |
| --- | --- | --- |
| C-1 | anti-bot delay (1차+2차 cron 양쪽 적용) | ✓ OK |
| C-2 | health_score 계산 (가드 통과 후 정상 호출) | ✓ OK |
| C-3 | sector cooldown 키 등록 | ✓ OK |
| C-4 | snapshot_date timezone | ✓ OK |
| C-5 | timeout-minutes=10 충분성 | ⚠️ **v1.2.1로 15분 증가** |
| C-6 | DRY_RUN env fallback | ✓ OK |
| C-7 | market_calendar 토요일 처리 | ✓ OK |
| C-8 | ci_preflight.sh sector 포함 | ✓ OK |

발견된 잠재 이슈 1건은 v1.2.1로 즉시 해결.

---

## 검증 결과

```
ruff check                       → All checks passed!
yml syntax                       → OK
pytest tests/test_sector_*.py    → 54 passed (이전 53 + 신규 1)
pytest tests/ --cov-fail-under=80 → 366 passed (회귀 0건)
Total coverage                    → 88.29% (80% 통과)
```

---

## 적용 순서 (v1.2.0에서 v1.2.1 업그레이드)

3개 파일 덮어쓰기:

| # | 경로 | 액션 |
| --- | --- | --- |
| 1 | `run_sector_alert.py` | 덮어쓰기 (VERSION 1.0.0 → 1.0.1) |
| 2 | `.github/workflows/sector_alert.yml` | 덮어쓰기 (timeout 1줄 변경) |
| 3 | `tests/test_run_sector_alert.py` | 덮어쓰기 (테스트 1건 추가) |

다른 파일은 v1.2.0 그대로 유지.

---

## 운영 시작 일정 (트랙 A)

오늘 = **2026-05-11 (월)**

### 자동 cron 실행 일정

| 일자 (UTC) | 1차 cron 23:00 UTC | 2차 cron 00:00 UTC (다음날) | 누적 row |
| --- | --- | --- | --- |
| 5/11 (월) — 오늘 | **첫 자동 실행 (KST 5/12 08:00)** | 5/12 00:00 UTC (KST 5/12 09:00) | 12 (6 + 6) |
| 5/12 (화) | KST 5/13 08:00 | KST 5/13 09:00 | 18 |
| 5/13 (수) | KST 5/14 08:00 | KST 5/14 09:00 | **24 ← 가드 첫 통과** |
| 5/14 (목) | KST 5/15 08:00 | KST 5/15 09:00 | 30 (완전 5일치) |
| 5/15 (금) | KST 5/16 08:00 (마지막 평일 cron) | KST 토 09:00 | 36 (cap, 운영 안정) |

**핵심 마일스톤**:
- **2026-05-14 (목) KST 08:00 — 첫 정상 spread 판정 가능 시점** (24 row 도달)
- 2026-05-15 (금) — 완전한 5일치 데이터로 안정 운영
- 2026-05-25 (월) — Shadow 2주 완료, Phase 2 정식 전환 검토 시점

### 모니터링 체크리스트

매일 확인:
1. GitHub Actions `Sector Flow Alert` 워크플로우 실행 성공 여부
2. Supabase `ia_sector_flow_daily` row count 증가 (+6/일 정상)
3. TG Internal 채널 발행 메시지 (Phase 1 Shadow 모드)
4. logs/ artifact 업로드 확인 (14일 보존)

환경변수 유지:
- `SECTOR_SHADOW_MODE=true` (Phase 2 정식 전환 전까지)
- `DRY_RUN=false` (schedule 실행 시 자동)

---

## v1.0.0 → v1.2.1 누적 변경 요약

| 버전 | 핵심 변경 | 적용 효과 |
| --- | --- | --- |
| v1.0.0 | 초기 빌드 (18 파일) | Sector Flow Alert 파이프라인 완성 |
| v1.1.0 | sector_collector requests + yfinance fallback | Yahoo 429 차단 우회 |
| v1.2.0 | 5일 가드 + Supabase 재시도 + cron 추가 | false alarm 차단 + 일시 장애 자동 복구 |
| **v1.2.1** | reasoning 로그 + timeout 15분 | 운영자 가독성 + worst case 여유 |

총 **운영 시스템 안정성 4단계 진화** 완료.
