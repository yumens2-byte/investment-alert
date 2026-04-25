# Investment-alert Phase 1 — 위기감지 고도화 최종 배포

> **작업 기간**: 2026-04-26 ~ Day 6
> **검증**: 193건 테스트 PASS, ruff clean, DRY_RUN E2E 4 시나리오 PASS
> **회귀**: 0건

---

## 배포 순서 

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

각 패치 가이드에 단계별 롤백 SQL/코드 포함. 또한 본 zip 적용 *전*의 원본 파일을   환경에서 백업 후 진행 권장.

---

## Notion 산출물

- 인덱스: https://www.notion.so/34d9208cbdc38109ab46fb1b365dd048
- 06  보고서: https://www.notion.so/34d9208cbdc38138aeb5d32914c2b396

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
