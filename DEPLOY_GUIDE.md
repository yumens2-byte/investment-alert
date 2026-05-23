# DEPLOY-ALL 통합 적용 가이드

> **일자**: 2026-05-23
> **범위**: alert v1.1.1 + sector v1.0.0 + phase2 v1.3.0 + CI Fix 누락 파일 commit
> **총 파일**: 18개 (신규 14 + 수정 4)
> **테스트 통과**: 488 passed, coverage 87.64%

---

## 1. 전체 파일 목록

### 🆕 신규 14개

| # | 경로 | 용도 |
|---|------|------|
| 1 | `docs/alert_kind_tone_guidelines.md` | alert 친근 톤 가이드라인 |
| 2 | `docs/ci_fix_guide.md` | CI 누락 파일 해결 가이드 |
| 3 | `publishers/alert_formatter_kind.py` | alert 친근 톤 헬퍼 (850줄) |
| 4 | `publishers/sector_formatter_kind.py` | sector 친근 톤 헬퍼 (310줄) |
| 5 | `publishers/prompts/alert_kind_l1_claude.md` | L1 Claude 프롬프트 |
| 6 | `publishers/prompts/alert_kind_l2_l3_gemini.md` | L2/L3 Gemini 프롬프트 |
| 7 | `publishers/prompts/alert_kind_news_translate.md` | 영문→한국어 번역 프롬프트 |
| 8 | `publishers/prompts/alert_kind_image_gemini.md` | 이미지 생성 프롬프트 |
| 9 | `publishers/prompts/sector_kind_gemini.md` | sector 친근 톤 프롬프트 |
| 10 | `tests/test_alert_formatter_kind.py` | 35 tests |
| 11 | `tests/test_sector_formatter_kind.py` | 14 tests |
| 12 | `tests/test_run_alert_image.py` | 15 tests (PHASE 2) |
| 13 | `tests/test_persona_voice.py` | 21 tests (v1.2.0 누락분) |
| 14 | `tests/test_image_gen_gemini.py` | 22 tests (v1.2.0 누락분) |

### ✏️ 수정 4개

| # | 경로 | 변경 |
|---|------|------|
| 1 | `publishers/alert_formatter.py` | v1.3.x → v1.4.0 (+41줄, KIND_TONE 분기) |
| 2 | `publishers/telegram_publisher.py` | v1.0.0 → v1.1.0 (+110줄, publish_with_photo) |
| 3 | `run_alert.py` | v1.2.0 → v1.3.0 (+57줄, 이미지 통합) |
| 4 | `run_sector_alert.py` | v1.0.1 → v1.1.0 (+44줄, KIND 분기) |

⚠️ **변경 없는 파일 잘못 포함**: 0건 (검증 완료)

---

## 2. 적용 권장 순서

### Step 1: 기존 v1.2.0 누락분 먼저 (CI 통과 우선)

가장 먼저 적용해야 다른 변경사항 CI도 통과합니다.

| 파일 | 목적 |
|------|------|
| `tests/test_persona_voice.py` | v1.2.0 시기 누락분 |
| `tests/test_image_gen_gemini.py` | v1.2.0 시기 누락분 |

→ commit + push → CI 자동 실행 → coverage 76.28% → 87%+ 회복 확인

### Step 2: alert v1.1.1 (9개 파일)

| 파일 |
|------|
| `docs/alert_kind_tone_guidelines.md` |
| `publishers/alert_formatter.py` (수정) |
| `publishers/alert_formatter_kind.py` (신규) |
| `publishers/telegram_publisher.py` (수정) |
| `publishers/prompts/alert_kind_l1_claude.md` |
| `publishers/prompts/alert_kind_l2_l3_gemini.md` |
| `publishers/prompts/alert_kind_news_translate.md` |
| `publishers/prompts/alert_kind_image_gemini.md` |
| `tests/test_alert_formatter_kind.py` |

### Step 3: sector v1.0.0 (4개 파일)

| 파일 |
|------|
| `publishers/sector_formatter_kind.py` |
| `publishers/prompts/sector_kind_gemini.md` |
| `run_sector_alert.py` (수정) |
| `tests/test_sector_formatter_kind.py` |

### Step 4: PHASE 2 v1.3.0 (2개 파일)

| 파일 |
|------|
| `run_alert.py` (수정) |
| `tests/test_run_alert_image.py` |

### Step 5: 가이드 문서 (1개)

| 파일 |
|------|
| `docs/ci_fix_guide.md` (참고용) |

---

## 3. GitHub Variables/Secrets 등록

### Variables (Settings → Secrets and variables → Variables)

| Key | 값 | 단계 |
|-----|-----|------|
| `KIND_TONE_ENABLED` | `true` | Step 2 적용 후 |
| `SECTOR_KIND_TONE_ENABLED` | `true` | Step 3 적용 후 |
| `KIND_TONE_IMAGE_ENABLED` | `false` (초기) → `true` (Beta-2) | Step 4 적용 후, 점진 활성화 |

### Secrets (이미 등록되어 있을 것)

| Key | 비고 |
|-----|------|
| `GEMINI_API_KEY` | 기존 |
| `ANTHROPIC_API_KEY` | 기존 |
| `TELEGRAM_BOT_TOKEN` | 기존 |
| `TELEGRAM_FREE_CHANNEL_ID` | 기존 |
| `TELEGRAM_PAID_CHANNEL_ID` | 기존 |

신규 추가: **없음** (기존 Secret 재활용)

---

## 4. 빠른 적용 (방법 A: GitHub 웹 에디터)

각 파일에 대해:

```
1. GitHub → repo → 해당 디렉토리 진입
2. 우상단 "Add file" → "Upload files"
3. 파일 드래그
4. 페이지 하단 commit message 작성
5. "Commit changes"
```

⚠️ **신규 디렉토리 주의**: `publishers/prompts/` 디렉토리가 repo에 없으면:
1. 빈 폴더 생성 불가 → 첫 파일 업로드 시 자동 생성
2. 또는 "Add file" → "Create new file" → 경로에 `publishers/prompts/alert_kind_l1_claude.md` 입력하면 디렉토리 자동 생성

---

## 5. 빠른 적용 (방법 B: 로컬 git CLI)

```bash
cd /path/to/investment-alert

# zip 압축 해제
unzip /path/to/investment-alert-DEPLOY-ALL-20260523.zip -d /tmp/

# 18개 파일을 repo로 복사 (덮어쓰기 OK)
cp -r /tmp/investment-alert-DEPLOY-ALL-20260523/* ./

# git에 추가
git add docs/ publishers/ tests/ run_alert.py run_sector_alert.py

# 변경사항 확인
git status

# 다음과 같이 표시되어야 함:
#   new file:   docs/alert_kind_tone_guidelines.md
#   new file:   docs/ci_fix_guide.md
#   modified:   publishers/alert_formatter.py
#   new file:   publishers/alert_formatter_kind.py
#   new file:   publishers/prompts/alert_kind_image_gemini.md
#   new file:   publishers/prompts/alert_kind_l1_claude.md
#   new file:   publishers/prompts/alert_kind_l2_l3_gemini.md
#   new file:   publishers/prompts/alert_kind_news_translate.md
#   new file:   publishers/prompts/sector_kind_gemini.md
#   new file:   publishers/sector_formatter_kind.py
#   modified:   publishers/telegram_publisher.py
#   modified:   run_alert.py
#   modified:   run_sector_alert.py
#   new file:   tests/test_alert_formatter_kind.py
#   new file:   tests/test_image_gen_gemini.py
#   new file:   tests/test_persona_voice.py
#   new file:   tests/test_run_alert_image.py
#   new file:   tests/test_sector_formatter_kind.py

# 로컬 사전 검증 (선택)
ruff check . --line-length=100
pytest tests/ -q

# commit + push
git commit -m "feat: alert v1.1.1 (KIND tone) + sector v1.0.0 + PHASE 2 image integration + CI fix"
git push origin main
```

---

## 6. 적용 후 검증

### 6.1 CI 자동 실행 결과 (push 후 10~15분)

```
============================= 488 passed in 29.XX s =============================
Total coverage: 87.64%
Required test coverage of 80% reached.
```

### 6.2 정상 통과 시 확인 사항

- [ ] CI workflow 통과
- [ ] coverage 87.64% (이전 76.28%에서 회복)
- [ ] 신규 모듈 정상 import (`alert_formatter_kind`, `sector_formatter_kind`)
- [ ] Variables `KIND_TONE_ENABLED=true` 등록
- [ ] Variables `SECTOR_KIND_TONE_ENABLED=true` 등록
- [ ] Variables `KIND_TONE_IMAGE_ENABLED=false` (초기 안전망)

### 6.3 실패 시 트러블슈팅

| 에러 메시지 | 원인 | 해결 |
|------------|------|------|
| `Module 'publishers.alert_formatter_kind' not found` | 파일 누락 | 해당 파일 commit 누락 — 재확인 |
| `Module 'publishers.sector_formatter_kind' not found` | 동일 | 동일 |
| `Required test coverage of 80% not reached` | 테스트 파일 일부 누락 | tests/ 디렉토리 5개 파일 모두 있는지 확인 |
| ruff F401/I001 | import 정렬 | 로컬에서 `ruff check . --fix` 후 재push |

---

## 7. 단계별 활성화 로드맵 (적용 후)

### Phase 1: 텍스트 톤만 활성화 (즉시 가능)

```
Variables:
  KIND_TONE_ENABLED=true
  SECTOR_KIND_TONE_ENABLED=true
  KIND_TONE_IMAGE_ENABLED=false  ← 이미지 비활성 유지
```

→ alert (TG Free/Paid/Internal) + sector (TG Free/Paid) 친근 톤 발행
→ X는 변경 없음
→ 비용 추가: ~$0.06/월

### Phase 2: 이미지 활성화 (Beta-2 단계)

조건: Phase 1 운영 2~3일 안정 + 검증 실패 0건 확인 후

```
Variables:
  KIND_TONE_IMAGE_ENABLED=true  ← 활성화
```

→ alert TG Free/Paid에 Gemini 이미지 첨부 추가
→ 비용 추가: ~$4.22/월 (총)

### 긴급 Rollback

| 시나리오 | 조치 (10초) |
|---------|-----------|
| alert 톤 문제 | `KIND_TONE_ENABLED=false` |
| sector 톤 문제 | `SECTOR_KIND_TONE_ENABLED=false` |
| 이미지 문제 | `KIND_TONE_IMAGE_ENABLED=false` |
| 전체 비활성화 | 위 3개 모두 false |

본 발행 영향: **0건** (모두 graceful fallback)

---

## 8. 운영 점검 가이드 (별도 노션)

자세한 운영 점검 단계 (Phase 0~6, 자연 트리거 대기, 로그 확인, 비용 모니터링)는 별도 노션 페이지 참조:

🔗 https://www.notion.so/3689208cbdc38197868acb72595e2b12

---

## 9. 변경 이력 노션 페이지

| 페이지 | URL |
|--------|-----|
| alert v1.0.0 설계서 | https://www.notion.so/3689208cbdc381dbb5e1f722273f90c5 |
| v1.1.0 변경 이력 | https://www.notion.so/3689208cbdc381dba150c21234443863 |
| v1.1.1 변경 이력 | https://www.notion.so/3689208cbdc3819091c1db8dc5e2e29c |
| sector v1.0.0 설계서 | https://www.notion.so/3689208cbdc3812d80c1f6386224c8fa |
| **PHASE 2 설계서** | https://www.notion.so/3689208cbdc38149892fc96a60772db4 |
| **CI Fix 가이드** | https://www.notion.so/3689208cbdc3814cb13dd7701e850ddb |
| **운영 점검 가이드** | https://www.notion.so/3689208cbdc38197868acb72595e2b12 |
| **DEPLOY-ALL 통합 가이드** | (본 페이지) |

---

## 10. 적용 체크리스트

```
사전
  [ ] zip 파일 다운로드
  [ ] zip 압축 해제
  [ ] 18개 파일 모두 존재 확인

Step 1: 누락 v1.2.0 파일 commit
  [ ] tests/test_persona_voice.py
  [ ] tests/test_image_gen_gemini.py
  [ ] commit + push
  [ ] CI 통과 확인

Step 2: alert v1.1.1 적용
  [ ] docs/alert_kind_tone_guidelines.md
  [ ] publishers/alert_formatter.py (덮어쓰기)
  [ ] publishers/alert_formatter_kind.py
  [ ] publishers/telegram_publisher.py (덮어쓰기)
  [ ] publishers/prompts/alert_kind_l1_claude.md
  [ ] publishers/prompts/alert_kind_l2_l3_gemini.md
  [ ] publishers/prompts/alert_kind_news_translate.md
  [ ] publishers/prompts/alert_kind_image_gemini.md
  [ ] tests/test_alert_formatter_kind.py
  [ ] commit + push
  [ ] CI 통과 확인 (473 passed)

Step 3: sector v1.0.0 적용
  [ ] publishers/sector_formatter_kind.py
  [ ] publishers/prompts/sector_kind_gemini.md
  [ ] run_sector_alert.py (덮어쓰기)
  [ ] tests/test_sector_formatter_kind.py
  [ ] commit + push
  [ ] CI 통과 확인 (473 passed)

Step 4: PHASE 2 v1.3.0 적용
  [ ] run_alert.py (덮어쓰기)
  [ ] tests/test_run_alert_image.py
  [ ] commit + push
  [ ] CI 통과 확인 (488 passed)

Step 5: 가이드 문서
  [ ] docs/ci_fix_guide.md

Variables 등록
  [ ] KIND_TONE_ENABLED=true
  [ ] SECTOR_KIND_TONE_ENABLED=true
  [ ] KIND_TONE_IMAGE_ENABLED=false (초기 안전망)

최종 확인
  [ ] alert.yml 다음 실행 시 친근 톤 정상 발행
  [ ] sector_alert.yml 다음 실행 시 친근 톤 정상 발행
  [ ] 로그에 [alert_formatter_kind] v1.1.1 호출 흔적
  [ ] 로그에 [sector_formatter_kind] v1.0.0 호출 흔적
```

---

## 작성 이력

| 일자 | 변경 |
|------|------|
| 2026-05-23 | 초안 — DEPLOY-ALL 통합 적용 가이드 |
