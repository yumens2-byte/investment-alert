# YouTube Shorts 운영 가이드

> **문서 상태**: v1.0<br>
> **대상**: 운영자, 배포 담당자, 장애 대응 담당자<br>
> **현재 단계**: Offline Pilot — YouTube 실업로드 비활성<br>

---

## 1. 현재 가능한 것과 불가능한 것

### 현재 가능

- 한국어 30초 pilot 대본과 FactPack 생성
- 대본 길이·장면 수·근거·IP·투자 권유 검증
- FFmpeg가 설치된 환경에서 1080×1920 H.264/AAC MP4 생성
- JSON manifest와 검증 결과 보존
- 미국 동부 현지시각 08:00/22:00 슬롯 판정
- 주말·2026년 휴장일 evergreen 모드 판정
- 전용 GitHub Actions에서 슬롯 기반 offline pilot 자동 실행

### 현재 불가능

- Claude/Gemini/TTS를 이용한 실제 콘텐츠 생성
- 회사 마크 및 무료 BGM 자동 승인·합성
- Supabase를 이용한 원자적 slot claim
- production 생성·업로드 GitHub Actions 실행
- GitHub Actions 자동 실행
- YouTube OAuth 업로드 및 즉시 공개

따라서 현재 코드는 **기술 파일럿**이며 운영 공개 준비 완료 상태가 아니다. `SHORTS_ENABLED`, `SHORTS_UPLOAD_ENABLED`, `SHORTS_PUBLIC_ENABLED`를 활성화해도 uploader가 구현되기 전에는 운영하지 않는다.

---

## 2. 운영 기준

| 항목 | 확정값 |
|---|---|
| 플랫폼 | YouTube Shorts |
| 언어 | 한국어 (`ko-KR`) |
| 발행량 | 미국 동부 현지 날짜 기준 최대 2건 |
| 발행 시각 | `America/New_York` 08:00, 22:00 |
| 영상 | 27~32초, 1080×1920, 30fps, H.264/AAC |
| 휴장일 | 동일 2슬롯, evergreen 콘텐츠 |
| 운영 공개 | 자동 QC 통과 후 public |
| BGM | 생성·취득비 0원 + 상업적 YouTube 사용 근거 필수 |
| 회사 마크 | 공식 원본 registry 승인분만 deterministic overlay |
| 원본 | FactPack부터 최종 MP4까지 자동 삭제하지 않음 |

운영 목표가 일 2건이어도 안전 검증 실패 영상을 강제로 공개하지 않는다. 정상 결과는 최대 2건이며, 0~1건은 장애가 아니라 `SKIPPED`/`QUARANTINED` 사유를 확인해야 하는 운영 상태다.

---

## 3. 역할과 책임

| 역할 | 책임 |
|---|---|
| Product Owner | 소재 경계, 채널 정책, 공개 승격 최종 승인 |
| Operations Owner | 일일 상태 확인, Telegram 경보, kill switch 운영 |
| Content Safety Owner | 사실/IP/투자 권유 및 canary 품질 승인 |
| Asset Owner | 회사 마크/BGM 출처·조건·hash 승인 및 분기 재검토 |
| Credential Owner | YouTube OAuth 발급·회수·교체, GitHub Environment 관리 |
| Engineering Owner | 배포, DB migration, 상태 복구, 회귀 테스트 |

한 사람이 여러 역할을 맡을 수 있지만 OAuth token 발급자와 콘텐츠 공개 승인자를 가능한 한 분리한다.

---

## 4. 환경 구분

| Environment | 모델 호출 | YouTube 업로드 | 공개 상태 |
|---|---:|---:|---|
| Local/PR | mock 또는 선택 | 금지 | 없음 |
| `shorts-dev` | 허용 | 금지 | 없음 |
| `shorts-canary` | 허용 | 테스트 채널 | private/unlisted |
| `shorts-production` | 허용 | 운영 채널 | public |

- canary와 production은 서로 다른 YouTube 채널/token을 사용한다.
- 기존 Alert workflow의 Secret과 concurrency group을 변경하지 않는다.
- production Secret은 `shorts-production` GitHub Environment에만 둔다.
- 로그에는 Secret 값, 길이, 앞/뒤 문자, OAuth 응답 원문을 출력하지 않는다.

---

## 5. 설정과 Kill Switch

### 안전 기본값

```text
SHORTS_ENABLED=false
SHORTS_GENERATION_ENABLED=true
SHORTS_UPLOAD_ENABLED=false
SHORTS_PUBLIC_ENABLED=false
SHORTS_TIMEZONE=America/New_York
SHORTS_SLOT_TIMES=08:00,22:00
SHORTS_DAILY_LIMIT=2
SHORTS_HOLIDAY_MODE=evergreen
SHORTS_DEFAULT_PRIVACY=public
BGM_GENERATION_MAX_COST_USD=0
BGM_REQUIRED=false
COMPANY_MARKS_ENABLED=true
```

### Switch 사용법

| Switch | false로 전환하는 상황 | 영향 |
|---|---|---|
| `SHORTS_ENABLED` | 보안·정책 중대 사고 | 신규 job 전체 중단 |
| `SHORTS_GENERATION_ENABLED` | 모델 비용 폭주/API 장애 | 신규 생성 중단 |
| `SHORTS_UPLOAD_ENABLED` | OAuth/quota/중복 위험 | YouTube 전송 중단 |
| `SHORTS_PUBLIC_ENABLED` | 사실/IP/품질 문제 | public 공개 중단 |
| `COMPANY_MARKS_ENABLED` | 상표·사용 조건 변경 | 티커 텍스트로 fallback |
| `BGM_ENABLED` | Content ID/라이선스 문제 | no-BGM으로 fallback |

### 활성화 순서

```text
generation → canary upload → canary 검증 → production upload → public
```

세 개의 주요 switch를 동시에 처음 활성화하지 않는다. 긴급 상황에서는 가장 먼저 `SHORTS_PUBLIC_ENABLED=false`, 중복·OAuth 사고이면 추가로 `SHORTS_UPLOAD_ENABLED=false`를 적용한다.

---

## 6. Secret 준비

### 기존 재사용

```text
ANTHROPIC_API_KEY
GEMINI_API_KEY
SUPABASE_URL
SUPABASE_KEY
TELEGRAM_BOT_TOKEN
TELEGRAM_INTERNAL_CHANNEL_ID
```

### YouTube 신규 필수

```text
YOUTUBE_CLIENT_ID
YOUTUBE_CLIENT_SECRET
YOUTUBE_REFRESH_TOKEN
YOUTUBE_CHANNEL_ID
```

`YOUTUBE_API_KEY`는 공개 데이터 조회용이며 영상 업로드용 자격증명이 아니다.

### Secret 점검 원칙

- 존재 여부만 출력한다.
- refresh token은 로컬 1회성 bootstrap에서 발급하고 즉시 GitHub Secret에 저장한다.
- `.env`, manifest, DB, artifact, Git history에 token을 넣지 않는다.
- canary와 production token을 같은 Secret 값으로 사용하지 않는다.
- 90일마다 revoke/reissue 훈련을 한다.

---

## 7. 현재 Offline Pilot 실행

### 7.0 GitHub Actions 실행

Actions 화면에서 `YouTube Shorts Pilot`을 선택한다.

- `Run workflow → render_video=true`: manifest와 30초 MP4 생성
- `Run workflow → render_video=false`: manifest-only 실행
- schedule: 매시 UTC 07분에 wake-up하며 미국 동부 08:00/22:00의 30분 window에서만 생성
- artifact: `shorts-pilot-<run_number>`, 보존 14일

workflow는 `SHORTS_ENABLED=false`, `SHORTS_UPLOAD_ENABLED=false`, `SHORTS_PUBLIC_ENABLED=false`를 강제하며 Secret을 참조하지 않는다. 따라서 현재 workflow 결과는 어떤 경우에도 YouTube로 업로드되지 않는다.

### 7.1 사전 점검

```bash
python --version
ffmpeg -version
ffprobe -version
```

Python 3.11 이상이 필요하다. FFmpeg가 없으면 manifest-only pilot을 실행한다.

### 7.2 영상 포함 실행

```bash
python run_youtube_shorts.py --pilot --output-dir logs/shorts/pilot
python run_shorts.py --pilot --output-dir logs/shorts/pilot
```

기대 산출물:

```text
logs/shorts/pilot/pilot_manifest.json
logs/shorts/pilot/pilot_short.mp4
```

### 7.3 FFmpeg 없는 실행

```bash
python run_youtube_shorts.py --pilot --no-render --output-dir logs/shorts/pilot
python run_shorts.py --pilot --no-render --output-dir logs/shorts/pilot
```

### 7.4 Manifest 확인

```bash
python -m json.tool logs/shorts/pilot/pilot_manifest.json
```

반드시 다음 값이어야 한다.

```json
{
  "validation": {"passed": true},
  "metadata": {
    "dry_run": true,
    "upload_attempted": false,
    "bgm": "none",
    "privacy_status": null
  }
}
```

### 7.5 영상 기술 검사

```bash
ffprobe -v error \
  -show_entries stream=codec_name,width,height,pix_fmt \
  -show_entries format=duration \
  -of json logs/shorts/pilot/pilot_short.mp4
```

기대값은 H.264, AAC, 1080×1920, `yuv420p`, 27~32초다.

---

## 8. 배포 전 필수 검사

Shorts 관련 파일을 수정했으면 문서 변경만 있더라도 전수 테스트를 수행한다.

```bash
ruff check . --line-length=100
pytest tests/ -v --cov-fail-under=80
bash scripts/ci_preflight.sh
git diff --check
```

### 통과 조건

- ruff error 0
- pytest failure 0
- 전체 coverage 80% 이상
- 기존 Alert/Sector 핵심 preflight failure 0
- conflict marker 0
- working tree에 예상하지 않은 생성 파일 0

FFmpeg-dependent test가 skip되면 코드 PASS로만 보고하지 않는다. 환경 제한을 기록하고 FFmpeg가 설치된 canary runner에서 media test를 다시 수행한다.

---

## 9. 운영 공개 전 승격 절차

### Gate A — 안전 기반

- [ ] 연도 독립 market calendar와 다음 400일 coverage
- [ ] Supabase migration, slot claim, lease, compare-and-set 상태 전이
- [ ] 회사 마크/BGM asset registry와 LicenseValidator
- [ ] 기존 Alert/Sector 전체 회귀 통과

### Gate B — 실제 생성 Offline 30건

- [ ] 거래일 오전 8건
- [ ] 거래일 밤 8건
- [ ] 휴장/주말 6건
- [ ] 고변동성/속보 4건
- [ ] provider 오류/fallback 4건
- [ ] 사실 오류, evidence 없는 숫자, IP high-risk, 투자 권유 각각 0건
- [ ] 영상 규격 30/30 통과

### Gate C — 테스트 채널 7일

```text
Day 1~2: private 1건/일
Day 3~4: private 2건/일
Day 5~7: unlisted 2건/일
```

- [ ] 중복 업로드 0건
- [ ] 사실/IP/정책 사고 0건
- [ ] OAuth read-back 100%
- [ ] 429/5xx/timeout 복구 확인
- [ ] 비용 cap 확인

### Gate D — 운영 공개

```text
Day 1~3: public 1건/일
Day 4 이후: public 2건/일
```

R1~R6 readiness가 모두 `CLOSED`이고 Go/No-Go 체크리스트가 완성된 경우에만 Gate D로 이동한다.

---

## 10. 정상 운영 절차

### 10.1 일일 확인

미국 동부 현지시각 기준:

| 시각 | 확인 |
|---|---|
| 07:45 | calendar freshness, API/DB health, kill switch |
| 08:00~08:30 | morning claim/generation/render/publish |
| 08:45 | remote read-back, 중복, Telegram 결과 |
| 21:45 | calendar/event freshness, 일일 비용 |
| 22:00~22:30 | night claim/generation/render/publish |
| 22:45 | read-back, 일일 0/1/2건 reconciliation |

### 10.2 정상 상태

```text
PLANNED → SOURCED → SCRIPTED → STORYBOARDED → MEDIA_READY
→ RENDERED → VALIDATED → READY_TO_PUBLISH → UPLOADING → PUBLISHED
```

### 10.3 운영자가 확인할 항목

- `content_id`, local date, slot
- 시장일/evergreen mode
- FactPack 기준시각과 source tier
- hard gate PASS 여부
- 최종 MP4 hash
- 사용 company mark/BGM asset ID 및 승인 상태
- YouTube video ID, privacy, processing 상태
- 생성·업로드 비용과 retry 수

### 10.4 일일 판정

| 결과 | 상태 | 조치 |
|---|---|---|
| 2건 공개 | Healthy | 일일 요약 확인 |
| 1건 공개 | Warning | skip/quarantine 원인 확인 |
| 0건 공개 | Critical | generation/upload/public switch와 장애 확인 |
| 3건 이상 | Incident | 즉시 upload/public 차단, 중복 runbook |

---

## 11. 상태별 대응

| 상태 | 의미 | 자동 처리 | 운영 조치 |
|---|---|---|---|
| `SKIPPED` | 소재/비용/slot 조건 미충족 | 업로드 없음 | code 확인 |
| `QUARANTINED` | 사실/IP/기술 hard gate 실패 | 업로드 차단 | artifact 검토 |
| `MISSED` | 허용 window 내 claim 실패 | 소급 공개 금지 | cron/DB 확인 |
| `RETRYABLE_FAILED` | 429/5xx/network | 제한 재시도 | 반복 시 provider 차단 |
| `AUTH_BLOCKED` | OAuth revoke/인증 실패 | 재시도 없음 | upload 차단·token 교체 |
| `SKIPPED_QUOTA` | YouTube quota 부족 | 해당 일 중단 | quota/호출량 확인 |
| `UPLOAD_UNKNOWN` | finalize 결과 불명확 | 새 upload 금지 | remote 조회·수동 판정 |
| `PUBLISHED` | read-back 완료 | 종료 | URL/metadata 확인 |

---

## 12. 장애 Runbook

### 12.1 잘못된 영상 공개

1. `SHORTS_PUBLIC_ENABLED=false`
2. video ID와 `content_id` 확인
3. 영상을 우선 비공개 전환
4. job을 `INCIDENT`로 표시
5. FactPack, script, prompt, asset, validation 원본 보존
6. 같은 prompt/topic/asset을 사용한 영상 검색
7. gate 수정 및 5건 canary 후 재개

### 12.2 중복 업로드

1. `SHORTS_UPLOAD_ENABLED=false`
2. 중복 video ID 두 개와 file hash 비교
3. 후발 영상을 비공개
4. DB claim, lease, state version, upload session audit
5. 병렬 10-worker 테스트와 finalize-timeout 테스트 재실행
6. 원인이 제거될 때까지 upload 재활성화 금지

### 12.3 OAuth 유출 또는 revoke

1. `SHORTS_UPLOAD_ENABLED=false`
2. GitHub production Environment 비활성화
3. Google 계정에서 refresh token revoke
4. client secret 교체
5. GitHub log/artifact/DB secret scan
6. canary token 재발급
7. 테스트 채널 private 1건 read-back 후 운영 복구

### 12.4 회사 마크/BGM 권리 변경

1. 해당 asset을 `REVOKED`로 변경
2. `COMPANY_MARKS_ENABLED=false` 또는 `BGM_ENABLED=false`
3. asset ID로 영향 영상 목록 추출
4. 약관에 따라 설명 수정, 음소거, 비공개 또는 삭제
5. ticker/no-BGM fallback 검증
6. asset owner 승인 후 재활성화

### 12.5 API 비용 폭주

1. `SHORTS_GENERATION_ENABLED=false`
2. job별 model call, retry, image count 확인
3. 일일 비용 cap 초과 여부 확인
4. 무한 재시도·cache miss 조사
5. 동일 FactPack cache key 검증
6. canary 한 건으로 복구 확인

### 12.6 Calendar 오류

1. 시장 상태를 `unknown`으로 강제
2. 시장일 콘텐츠 공개 중단
3. evergreen fallback 또는 안전 skip
4. snapshot source/hash/fetched_at 확인
5. 다음 400일 coverage 복구 후 preflight 재실행

---

## 13. 원본과 감사 기록

다음 파일은 자동 삭제하지 않는다.

- FactPack 및 evidence URL/hash/기준시각
- Claude script/critic 결과와 prompt/model/policy version
- Gemini 원본 이미지와 scene prompt/seed
- 회사 마크 overlay 원본과 asset registry record
- TTS 원본, 발음 사전 version
- BGM/SFX 원본과 사용 근거
- SRT, timeline, FFmpeg command manifest
- 최종 MP4와 SHA-256
- validation report와 publish/read-back 결과

기사 원문 전체, OAuth token, access token, request header, provider 내부 reasoning은 보존하지 않는다.

---

## 14. 정기 점검

### 매일

- 2개 slot reconciliation
- 실패·skip·retry 사유
- 일일 비용과 quota
- remote processing/privacy

### 매주

- 중복 topic과 캐릭터 품질
- provider 오류율과 p95 생성 시간
- 사실 정정·댓글 신고·Content ID claim
- artifact storage 증가량

### 매월

- 모델/prompt/policy version 현황
- kill switch 훈련 1회
- OAuth 권한과 Environment 접근자
- 일/월 비용 cap 적정성

### 분기

- 회사 마크/BGM registry 전체 재검토
- token revoke/reissue 훈련
- 잘못된 영상 비공개 runbook 훈련
- 다음 400일 market calendar coverage

---

## 15. 변경 관리

다음 변경은 5건 이상 재-canary가 필요하다.

- Claude/Gemini/TTS 모델 변경
- system prompt 또는 character bible 변경
- renderer/FFmpeg major 설정 변경
- 음성 voice ID·속도·발음 사전 변경
- asset license policy 변경
- YouTube metadata/publication 정책 변경
- market calendar provider 변경

변경 기록에는 이전/신규 version, 이유, 테스트 결과, 비용 변화, 승인자를 남긴다. 성과가 좋다는 이유만으로 모델이 prompt를 자동 수정하게 하지 않는다.

---

## 16. 운영 시작 최종 체크리스트

- [ ] R1~R6가 모두 `CLOSED`
- [ ] 전체 pytest와 80% coverage gate 통과
- [ ] 기존 Alert/Sector preflight 통과
- [ ] production/canary OAuth 분리
- [ ] YouTube API/정책 최신 공식 문서 재확인
- [ ] 400일 calendar freshness
- [ ] DB 병렬 claim·process kill·upload timeout 테스트
- [ ] 회사 마크/BGM registry 검증
- [ ] 30건 offline quality pilot 통과
- [ ] 테스트 채널 7일 canary 통과
- [ ] kill switch 및 장애 runbook 훈련
- [ ] Telegram critical 알림 수신 확인
- [ ] 초기 3일 public 1건/일 계획 승인

체크되지 않은 항목이 하나라도 있으면 `SHORTS_PUBLIC_ENABLED`는 `false`로 유지한다.

---

## 17. 빠른 명령 모음

```bash
# Offline pilot
python run_youtube_shorts.py --pilot --output-dir logs/shorts/pilot

# Manifest only
python run_youtube_shorts.py --pilot --no-render --output-dir logs/shorts/pilot
python run_shorts.py --pilot --output-dir logs/shorts/pilot

# Manifest only
python run_shorts.py --pilot --no-render --output-dir logs/shorts/pilot

# Lint
ruff check . --line-length=100

# 전수 테스트 + coverage
pytest tests/ -v --cov-fail-under=80

# 기존 핵심 파이프라인 preflight
bash scripts/ci_preflight.sh

# 변경 파일 확인
git status --short
git diff --check
```

---

## 18. 관련 문서

- `docs/youtube_shorts_automation_requirements_2026-07-24.md`: 제품 요구사항
- `docs/youtube_shorts_detailed_design_2026-07-24.md`: 기술 상세설계
- `docs/youtube_shorts_risk_closure_design_2026-07-24.md`: R1~R6 위험 종료 설계
- `docs/youtube_shorts_pilot_report_2026-07-24.md`: Phase 1 pilot 결과

이 운영 가이드는 실행 절차를 설명한다. API 필드, quota, 플랫폼 정책은 구현·배포 당일 공식 문서를 다시 확인하고 확인일을 감사 기록에 남긴다.
