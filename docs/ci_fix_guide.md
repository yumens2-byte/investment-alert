# CI Fix 가이드 — 누락 테스트 파일 commit

> **대상**: investment-alert repo
> **증상**: GitHub Actions CI에서 `Required test coverage of 80% not reached. Total coverage: 76.28%` fail
> **원인**: 신규 테스트 파일 일부가 repo에 commit되지 않음

---

## 1. 진단

### 1.1 정상 상태 (Claude 검증 환경)
```
============================= 473 passed in 29.49s =============================
Total coverage: 87.64%

publishers/weekly_news_x/image_gen_gemini.py     176     29    84%
publishers/weekly_news_x/persona_voice.py        176     23    87%
```

### 1.2 비정상 상태 (마스터 GitHub CI — 가장 최근 fail 시점)
```
============================= 381 passed in 28.15s =============================
FAIL Required test coverage of 80% not reached. Total coverage: 76.28%

publishers/weekly_news_x/image_gen_gemini.py     176    176     0%
publishers/weekly_news_x/persona_voice.py        176    176     0%
```

### 1.3 원인 분석 (팩트)

| 지표 | Claude 정상 | 마스터 fail | 의미 |
|------|------------|------------|------|
| `Stmts` (모듈 라인 수) | 176 | 176 | **모듈 코드는 정상 commit됨** |
| `Cover` (커버리지 %) | 84%/87% | **0%** | **테스트가 실행 안 됨** |
| 테스트 총수 | 473 | 381 | -92건 누락 |

차이 92건 = persona 21 + image 22 + alert_kind 35 + sector_kind 14 = **정확히 일치**

→ 결론: **4개 테스트 파일이 repo에 commit되지 않음**

---

## 2. 점검 — 마스터 측 어떤 파일이 누락됐는지 확인

### 2.1 GitHub 웹에서 직접 확인

다음 URL을 브라우저에서 열고 4개 파일 존재 여부 확인:

```
https://github.com/yumens2-byte/investment-alert/blob/main/tests/test_persona_voice.py
https://github.com/yumens2-byte/investment-alert/blob/main/tests/test_image_gen_gemini.py
https://github.com/yumens2-byte/investment-alert/blob/main/tests/test_alert_formatter_kind.py
https://github.com/yumens2-byte/investment-alert/blob/main/tests/test_sector_formatter_kind.py
```

| 결과 | 의미 |
|------|------|
| 404 페이지 | **누락** → 아래 해결 단계 진행 |
| 정상 표시 | 이미 있음 → 다른 원인 점검 필요 |

### 2.2 로컬에서 git status로 확인 (가능한 경우)

```bash
cd /path/to/investment-alert
git ls-files tests/ | grep -E "test_persona_voice|test_image_gen_gemini|test_alert_formatter_kind|test_sector_formatter_kind"
```

출력에 4개 파일이 모두 보이면 정상. 일부 빠지면 아래 해결.

---

## 3. 해결 — 누락 파일 commit (3가지 방법 중 하나 선택)

### 방법 A: GitHub 웹 에디터로 직접 업로드 (가장 쉬움)

| 단계 | 액션 |
|------|------|
| 1 | 다운로드한 zip 압축 해제 |
| 2 | zip 내 `tests/test_persona_voice.py` 파일 내용 복사 |
| 3 | GitHub → repo → `tests/` 디렉토리 진입 |
| 4 | 우상단 "Add file" → "Create new file" |
| 5 | 파일명에 `test_persona_voice.py` 입력 |
| 6 | 본문에 복사한 내용 붙여넣기 |
| 7 | 페이지 하단 "Commit new file" 클릭 |
| 8 | 나머지 3개 파일도 동일 반복 |

### 방법 B: zip 직접 업로드 (GitHub Desktop 사용 시)

| 단계 | 액션 |
|------|------|
| 1 | GitHub Desktop 열기 |
| 2 | repo의 `tests/` 폴더로 zip에서 4개 파일 드래그 |
| 3 | 변경사항 확인 (4 files added) |
| 4 | Commit message: `Add missing test files for persona_voice, image_gen_gemini, alert_formatter_kind, sector_formatter_kind` |
| 5 | "Commit to main" → "Push origin" |

### 방법 C: 커맨드라인 (git CLI 사용 시)

```bash
cd /path/to/investment-alert

# zip 압축 해제 후 tests/ 파일 4개를 repo의 tests/ 디렉토리로 복사
cp /path/to/extracted/tests/test_persona_voice.py tests/
cp /path/to/extracted/tests/test_image_gen_gemini.py tests/
cp /path/to/extracted/tests/test_alert_formatter_kind.py tests/
cp /path/to/extracted/tests/test_sector_formatter_kind.py tests/

# git에 추가
git add tests/test_persona_voice.py
git add tests/test_image_gen_gemini.py
git add tests/test_alert_formatter_kind.py
git add tests/test_sector_formatter_kind.py

# commit + push
git commit -m "Add missing test files for v1.1.1 + sector v1.0.0"
git push origin main
```

---

## 4. 검증 — CI 자동 재실행

commit + push 직후:

| 단계 | 액션 |
|------|------|
| 1 | GitHub → repo → Actions 탭 |
| 2 | 최상단 새 워크플로우 실행 발견 (push 트리거로 자동 시작) |
| 3 | 10~15분 대기 |
| 4 | CI workflow 결과 확인 |

### 정상 통과 기준
```
============================= 473 passed in 29.XX s =============================
Total coverage: 87.64%
Required test coverage of 80% reached.
```

### 만약 여전히 fail
- 어떤 파일이 추가됐는지 GitHub Actions Artifact의 `htmlcov.zip` 다운로드 → `index.html` 확인
- coverage가 80% 넘는지 확인
- 80% 넘어도 다른 이유로 fail이면 로그 상세 확인 후 보고

---

## 5. 예방 — 향후 동일 문제 방지

| 권장 | 방법 |
|------|------|
| zip 적용 시 체크리스트 사용 | 적용 후 GitHub에서 변경 파일 수 == zip 내 파일 수 확인 |
| 빈 디렉토리 체크 | `publishers/prompts/`, `tests/` 폴더 안에 파일 누락 자주 발생 |
| commit 전 로컬 pytest 실행 권장 | `pytest tests/ -q` 한 줄로 사전 검증 |

---

## 6. 관련 문서

- [alert v1.0.0 설계서](https://www.notion.so/3689208cbdc381dbb5e1f722273f90c5)
- [운영 점검 가이드](https://www.notion.so/3689208cbdc38197868acb72595e2b12)

---

## 작성 이력

| 일자 | 버전 | 변경 |
|------|------|------|
| 2026-05-23 | 1.0.0 | 초안 — 4개 테스트 파일 누락 진단 + 3가지 해결 방법 |
