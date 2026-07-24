# YouTube Shorts 자동화 상세설계서

> **상태**: v0.1 — 개발 착수 기준선<br>
> **관련 요구사항**: `youtube_shorts_automation_requirements_2026-07-24.md`<br>
> **확정 운영값**: 한국어, YouTube, 미국 동부 현지시각 08:00/22:00, 일 2건, 휴장일 운영, QC 후 즉시 공개

---

## 1. 설계 목표

기존 투자 Alert를 변경하지 않고 검증된 시장 이벤트를 읽어 27~32초 한국어 motion-comic Shorts를 만든다. 정상 흐름은 소재 선정부터 YouTube 공개까지 무인으로 진행하지만, 사실·IP·라이선스·기술 검증 실패 시에는 게시하지 않는 fail-closed 구조로 구현한다.

### 1.1 확정값과 구현 해석

| 결정 | 구현값 |
|---|---|
| 플랫폼 | YouTube Shorts only |
| 발행 수 | 현지 날짜별 최대 2건 |
| 시간 | `America/New_York` 08:00, 22:00 |
| 서머타임 | 여름 EDT/겨울 EST 자동 전환, 현지 벽시계 시간 유지 |
| 휴장일 | 2건 유지, evergreen 소재만 선택 |
| 공개 | 최종 QC 후 `public` 직접 업로드 |
| 언어 | ko-KR, 티커/고유명사만 영문 허용 |
| 회사 마크 | 공식 원본 asset registry 승인분만 합성 |
| BGM | 취득비·생성비 0원 + 상업적 YouTube 사용 근거 필수 |
| 원본 | 입력 근거와 모든 생성 중간/최종 산출물 보존 |

`EDT`를 고정 UTC-04:00 offset으로 구현하면 겨울의 미국 시장 현지시각과 한 시간 어긋난다. 따라서 설정에는 요청 의도를 보존하되 기술적으로 `America/New_York` IANA timezone을 사용한다.

---

## 2. 시스템 경계

```text
┌──────────────── 기존 investment-alert (변경 금지) ────────────────┐
│ Alert/Sector DB · NewsCollector · MarketCalendar                  │
└────────────────────── read-only ───────────────────────────────────┘
                              │
                              ▼
┌──────────────────── shorts pipeline ──────────────────────────────┐
│ Dispatcher → TopicSelector → FactPack → Script → Storyboard       │
│ → Image/Logo Composer → TTS/BGM → FFmpeg → QC → YouTube Publisher │
└───────────────┬────────────────┬──────────────────┬───────────────┘
                ▼                ▼                  ▼
          Supabase state   Object Storage     Telegram ops
```

### 2.1 원칙

1. 기존 DB와 수집기는 read-only adapter로 접근한다.
2. LLM은 상태를 직접 변경하거나 업로드하지 않는다.
3. 각 단계는 입력 hash가 같으면 같은 artifact를 재사용한다.
4. 외부 입력은 prompt injection 가능성이 있는 데이터로 취급한다.
5. 업로드는 모든 hard gate를 통과한 `READY_TO_PUBLISH` job만 받는다.

---

## 3. 코드 구조

```text
shorts/
  __init__.py
  config.py
  domain/
    models.py             # dataclass/Pydantic schemas
    states.py             # 상태와 전이
    errors.py
  scheduling/
    dispatcher.py         # 현지 날짜/슬롯 claim
  sourcing/
    alert_reader.py       # 기존 DB read-only
    topic_selector.py
    fact_pack.py
  generation/
    text_provider.py
    claude_script.py
    image_provider.py
    gemini_storyboard.py
    speech_provider.py
    bgm_provider.py
  assets/
    registry.py
    logo_composer.py
    license_validator.py
  rendering/
    timeline.py
    ffmpeg_renderer.py
    subtitles.py
  validation/
    script_validator.py
    evidence_validator.py
    ip_validator.py
    media_validator.py
    publish_gate.py
  publishing/
    youtube_oauth.py
    youtube_publisher.py
  storage/
    job_store.py
    artifact_store.py
  observability/
    metrics.py
    notifier.py
run_youtube_shorts.py
```

Provider별 SDK 객체가 도메인에 노출되지 않도록 protocol adapter로 감싼다. 모델명과 prompt version은 환경설정으로 pin한다.

---

## 4. 스케줄러 상세

### 4.1 GitHub Actions

workflow는 15분마다 실행한다. UTC cron은 실행 신호일 뿐 게시 시각 판정은 Python이 수행한다.

Phase 1 구현 파일은 `.github/workflows/shorts_pilot.yml`이다. 기존 Alert/Sector workflow와 다른 concurrency group을 사용하고 Secret, 모델 API, YouTube uploader를 호출하지 않는다. 스케줄 실행은 `run_youtube_shorts.py --dispatch`를 호출해 슬롯 밖에서는 성공적으로 skip하며, 수동 실행은 영상 포함 또는 manifest-only pilot을 선택한다.

```yaml
on:
  schedule:
    - cron: "7 * * * *"
  workflow_dispatch:
    inputs:
      local_date: {required: false, type: string}
      slot: {required: false, type: choice, options: [auto, morning, night]}
      dry_run: {required: true, type: boolean, default: true}
```

Phase 1은 DB claim이 없으므로 슬롯당 중복 실행을 피하기 위해 30분 window 안의 7분 한 번만 사용한다. workflow concurrency가 같은 repository 내 pilot 중첩을 차단한다. DB claim 구현 후에는 7분·22분 두 번의 재시도형 wake-up과 DB unique constraint를 함께 사용한다.

### 4.2 슬롯 claim 알고리즘

```python
now_local = now_utc.astimezone(ZoneInfo("America/New_York"))
for slot in ("08:00", "22:00"):
    if slot_start <= now_local < slot_start + 30_minutes:
        content_id = f"{now_local.date()}:{slot}"
        insert shorts_jobs(content_id) on conflict do nothing
```

- 허용 window는 30분이다. GitHub cron 지연을 흡수한다.
- `(local_date, slot_name)` unique constraint가 동시 실행을 차단한다.
- 해당 날짜가 휴장일이면 `content_mode=EVERGREEN`, 거래일이면 `MARKET_DAY`다.
- slot miss를 소급 공개하지 않는다. 운영 알림 후 `MISSED`로 기록한다.
- `workflow_dispatch` 강제 실행도 별도 `manual_run_id` 없이는 기존 public job을 덮어쓰지 않는다.

---

## 5. 상태 머신과 멱등성

```text
PLANNED
  → SOURCED
  → SCRIPTED
  → STORYBOARDED
  → MEDIA_READY
  → RENDERED
  → VALIDATED
  → READY_TO_PUBLISH
  → UPLOADING
  → PUBLISHED
```

예외 상태는 `RETRYABLE_FAILED`, `QUARANTINED`, `SKIPPED`, `MISSED`, `UPLOAD_UNKNOWN`이다.

### 5.1 전이 규칙

- store의 compare-and-set으로 `expected_state → next_state`만 허용한다.
- 단계 완료 시 `input_hash`, `artifact_hash`, `provider`, `model`, `prompt_version`을 함께 기록한다.
- 같은 `input_hash`의 성공 artifact가 있으면 API를 다시 호출하지 않는다.
- 생성 단계별 최대 2회, 네트워크 단계 최대 4회 재시도한다.
- `UPLOADING` timeout은 `UPLOAD_UNKNOWN`으로 보내며 새 upload를 시작하지 않는다.

### 5.2 공개 멱등 키

YouTube API가 application-level idempotency key를 보장한다고 가정하지 않는다. DB의 `content_id`, resumable session 식별 정보의 hash, 원격 `video_id`를 한 transaction 경계로 관리한다. 원격 결과가 불명확하면 운영 확인 전 자동 재업로드하지 않는다.

---

## 6. 소재와 FactPack

### 6.1 거래일 슬롯

- 08:00: overnight 지수/선물 분위기, 공식 발표 예정, 전일 마감 핵심 원인
- 22:00: 당일 마감 결과, 섹터 rotation, 다음 거래일 관찰점

### 6.2 휴장일 슬롯

- 08:00: 지난 거래일의 핵심 개념 해설 또는 미국 시장 역사
- 22:00: 다음 거래일 경제 캘린더 또는 지난주 sector 회고
- 실시간인 것처럼 표현하지 않고 첫 5초 안에 휴장/evergreen임을 알린다.

### 6.3 FactPack schema

```json
{
  "schema_version": "1.0",
  "as_of": "RFC3339",
  "market_status": "OPEN|CLOSED|WEEKEND",
  "topic_key": "yield:us10y",
  "facts": [
    {
      "id": "F1",
      "claim": "검증된 한국어 요약",
      "value": 0.0,
      "unit": "%",
      "observed_at": "RFC3339",
      "source_url": "https://...",
      "source_tier": "OFFICIAL"
    }
  ],
  "forbidden_inferences": []
}
```

- 공식 1차 출처 1개 또는 독립 출처 2개가 없으면 topic을 폐기한다.
- 모든 숫자는 값, 단위, 기준시각을 가져야 한다.
- 기사 본문은 저장하거나 prompt에 통째로 넣지 않고 검증된 claim만 전달한다.
- 최근 30일 `topic_key`/semantic fingerprint 중복을 검사한다.

---

## 7. 대본·콘티 생성

### 7.1 Claude 출력 계약

```json
{
  "title": "한국어 제목",
  "hook": "0~3초 발화",
  "scenes": [
    {
      "index": 1,
      "duration_ms": 3500,
      "narration": "한국어",
      "subtitle": "한국어",
      "visual_prompt": "오리지널 장면 설명",
      "claim_type": "FACT|HYPOTHESIS|DISCLAIMER",
      "evidence_ids": ["F1"],
      "company_asset_ids": []
    }
  ],
  "description": "출처·면책 포함",
  "hashtags": ["#미국증시", "#Shorts"]
}
```

### 7.2 규칙

- 전체 27~32초, 5~7장면, 사실 수치 최대 2개
- `FACT`는 evidence ID 필수, `HYPOTHESIS`에는 “만약/가정” 표현 필수
- 매수·매도·수익 보장 표현과 실존 인물 히어로/빌런화 금지
- prompt에서 Marvel, What If, Disney, 캐릭터명, 특정 작가명 금지
- Claude writer 출력은 JSON schema와 deterministic validator를 거친 후 별도 critic 호출로 검수한다.

### 7.3 길이 보정

TTS를 먼저 생성해 실제 duration을 측정한다. 32초를 넘으면 Claude에 초과 millisecond와 보존해야 할 evidence를 주어 한 번 축약한다. 27초 미만이면 침묵을 늘리지 않고 정보 없는 수식어를 제외한 hook/insight를 보강한다.

---

## 8. 회사 마크 자산 설계

### 8.1 원칙

회사 마크 사용은 라이선스 회피가 아니다. **공식 원본을 출처와 사용 조건이 확인된 범위에서 식별 목적으로 제한 사용**하는 방식이다. 조건이 불명확하면 자동으로 회사명/티커 텍스트로 대체한다.

### 8.2 `asset_registry.yaml`

```yaml
assets:
  - asset_id: company_example_primary
    type: company_mark
    company: Example Inc.
    source_url: https://company.example/brand
    source_sha256: "..."
    obtained_at: 2026-07-24
    allowed_use: editorial_identification
    modification: none
    attribution: false
    expires_at: null
    approved: false
```

`approved=true`인 파일만 renderer가 읽을 수 있다. Gemini 입력에 로고를 넣어 변형시키지 않고, 장면 생성이 끝난 뒤 deterministic overlay로 합성한다.

### 8.3 배치 제한

- 원본 종횡비·색·clear space 유지
- 화면 면적 10% 이내, 한 장면 최대 1개
- 회전, 왜곡, 애니메이션 morph, 캐릭터 의상 적용 금지
- 제휴/후원/공식 채널로 오인될 제목과 배치 금지
- registry 미승인/만료/파일 hash 불일치 시 ticker fallback

---

## 9. 무료 BGM 설계

### 9.1 허용 조건

BGM은 아래 조건을 **모두** 만족해야 한다.

1. 다운로드·생성 요청 비용이 0원이다.
2. YouTube 상업 채널 및 수익화 영상 사용이 허용된다.
3. 사용 기간·지역·영상 수 제한이 없다.
4. Content ID claim 처리 조건이 명확하다.
5. 출처 URL, 취득일, 약관 snapshot/hash 또는 라이선스 파일을 보존한다.

“무료 다운로드”만으로는 허용하지 않는다. 비상업용, 출처 불명, share-alike 의무를 파이프라인이 이행할 수 없는 자산은 제외한다.

### 9.2 선택 순서

1. 비용 0원의 생성 provider가 있고 출력의 상업 이용 근거가 확인되면 오리지널 instrumental 생성 요청
2. 없으면 승인 registry의 무료 상업 이용 BGM 사용
3. 둘 다 없으면 **BGM 없이** 내레이션+자체 합성 단순 효과음으로 렌더

무료 생성 API가 존재한다고 가정하지 않는다. 유료 호출로 자동 전환하지 않으며 `BGM_GENERATION_MAX_COST_USD=0`을 hard gate로 둔다.

### 9.3 생성 prompt 제한

- “특정 곡/가수/작곡가 스타일” 금지
- 보컬·가사·샘플링 금지
- 30초 original instrumental, 90~110 BPM, narration-safe dynamics
- 생성 결과 audio fingerprint와 provider receipt/terms version 저장

---

## 10. 영상 합성

### 10.1 Timeline

각 장면은 배경 생성 이미지, 선택적 회사 마크 overlay, 자막, motion instruction으로 구성한다. FFmpeg filter graph는 코드로 생성하고 shell string interpolation 대신 argument list로 실행한다.

### 10.2 출력

- MP4 / H.264 / yuv420p / AAC
- 1080×1920 / 30fps / 27~32초
- 한국어 burned-in 자막과 별도 SRT
- 음성 우선 mix, BGM은 speech ducking 적용
- 첫/마지막 0.2초 이상 black frame 금지

### 10.3 기술 QC

`ffprobe`로 codec, pixel format, resolution, fps, duration, audio stream을 검사한다. 추가로 black frame, 무음, clipping, OCR, 자막 safe-area를 검사한다. 렌더 오류는 동일 입력으로 1회 재시도하고 다시 실패하면 quarantine한다.

---

## 11. 공개 게이트와 YouTube 업로드

### 11.1 Hard gate

```text
schema_pass
AND evidence_pass
AND finance_safety_pass
AND ip_pass
AND asset_license_pass
AND media_technical_pass
AND duplicate_pass
AND cost_pass
AND upload_enabled
AND public_enabled
```

한 항목이라도 false면 uploader를 호출하지 않는다. “바로 공개”는 검수 생략이 아니라 **자동 검수 완료 후 별도 비공개 대기 없이 공개**한다는 의미다.

### 11.2 인증

- `YOUTUBE_API_KEY`: 기존 조회 기능 전용
- `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN`: 업로드용
- refresh token과 access token을 로그/DB/artifact에 기록하지 않는다.
- OAuth scope는 업로드에 필요한 최소 범위로 확정한다.

### 11.3 업로드 순서

1. job을 compare-and-set으로 `READY_TO_PUBLISH → UPLOADING`
2. resumable upload session 시작
3. MP4 전송, `privacyStatus=public`, 한국어 metadata 적용
4. 응답의 `video_id` 즉시 저장
5. API read-back으로 privacy, title, processing 상태 확인
6. 성공 시 `PUBLISHED`, 불명확 시 `UPLOAD_UNKNOWN`

description에는 데이터 기준시각, 출처 링크, AI 생성 고지, “정보 제공 목적이며 투자 조언이 아닙니다”를 포함한다.

---

## 12. 데이터베이스

### 12.1 핵심 제약

```sql
unique (local_publish_date, slot_name);
unique (content_id);
unique (platform, remote_video_id);
check (slot_name in ('morning', 'night'));
```

### 12.2 추가 테이블

- `shorts_jobs`: 슬롯, 상태, topic, 시도, error code
- `shorts_evidence`: claim, 값, 단위, 기준시각, 출처
- `shorts_artifacts`: URI, hash, 모델/prompt/license metadata
- `shorts_validations`: validator version, pass, severity, code
- `shorts_publications`: remote ID, privacy, processing status
- `shorts_asset_registry`: logo/BGM/SFX 출처, 조건, 승인, hash

원본 artifact는 immutable path에 저장한다. 재생성 시 덮어쓰지 않고 revision을 추가한다.

### 12.3 보존 정책

사용자가 “원본 보존”을 선택했으므로 v1에서는 자동 삭제하지 않는다. fact pack, script/storyboard JSON, 원본 이미지, 로고 overlay source, TTS, BGM/SFX, SRT, 최종 MP4, validation report를 보존한다. 저장 비용이 확인된 뒤 보존 기간 변경은 별도 승인 항목으로 다룬다.

---

## 13. 설정

```text
SHORTS_ENABLED=false
SHORTS_GENERATION_ENABLED=true
SHORTS_UPLOAD_ENABLED=false
SHORTS_PUBLIC_ENABLED=false
SHORTS_LANGUAGE=ko-KR
SHORTS_TIMEZONE=America/New_York
SHORTS_SLOT_TIMES=08:00,22:00
SHORTS_DAILY_LIMIT=2
SHORTS_HOLIDAY_MODE=evergreen
SHORTS_DEFAULT_PRIVACY=public
SHORTS_MAX_DURATION_SEC=32
SHORTS_MIN_DURATION_SEC=27
BGM_GENERATION_MAX_COST_USD=0
BGM_REQUIRED=false
COMPANY_MARKS_ENABLED=true
SHORTS_POLICY_VERSION=v1
```

배포 순서는 generation → upload → public kill switch 순서다. 세 변수를 한 번에 활성화하지 않는다.

---

## 14. 관측과 알림

### 14.1 구조화 로그

모든 로그는 `trace_id`, `content_id`, `slot`, `stage`, `attempt`, `duration_ms`, `result_code`를 포함한다. prompt 전문, 기사 전문, secret, OAuth 응답은 기록하지 않는다.

### 14.2 Telegram

- 성공: 슬롯, 제목, video ID/URL, duration, 비용
- skip: 규칙 code와 선택된 다음 행동
- quarantine: 실패 gate, artifact trace ID
- 일일 요약: 예정 2, 공개 수, 실패 수, 비용, 중복 0/1
- 22:30 현지시각까지 2건 모두 미게시이면 critical 알림

---

## 15. 테스트 설계

### 15.1 Unit

- EDT→EST/EST→EDT 경계에서도 08:00/22:00 각각 한 번 claim
- 거래일/주말/휴장일 mode 결정
- 동시 10개 dispatcher 중 1개만 insert 성공
- fact/evidence 수치·단위·시각 검사
- 금지 IP prompt와 투자 권유 차단
- 미승인/변형/만료 회사 마크 ticker fallback
- BGM 비용이 0보다 크거나 라이선스 근거가 없으면 차단
- 26.9/32.1초 영상 차단

### 15.2 Integration

- provider mock으로 FactPack → MP4 → validation report 생성
- FFmpeg fixture의 black/silence/clipping 탐지
- YouTube 429/5xx retry와 OAuth revoked fail-fast
- upload timeout 후 중복 호출 없이 `UPLOAD_UNKNOWN`
- `SHORTS_PUBLIC_ENABLED=false`에서 uploader 호출 0회

### 15.3 출시 gate

1. 로컬 fixture 30건
2. Actions dry-run 7일
3. 별도 테스트 채널 private/unlisted 7일
4. 운영 채널 public 1건/일 3일
5. 운영 채널 public 2건/일 전환

운영 모드는 바로 공개하지만, 개발 검증까지 생략하는 것은 아니다.

---

## 16. 구현 작업 분해

### Sprint 1 — 기반

- `shorts/domain`, config, 상태 머신, DB migration
- timezone dispatcher와 휴장일 mode
- 기존 Alert read-only adapter와 FactPack schema
- 단위 테스트

### Sprint 2 — 생성

- Claude structured script adapter와 validators
- Gemini storyboard/image adapter와 IP negative prompt
- TTS adapter, 무료 BGM registry/provider
- 회사 마크 registry와 deterministic overlay

### Sprint 3 — 렌더/QC

- timeline compiler, FFmpeg renderer, SRT
- ffprobe/OCR/audio/license QC
- immutable artifact storage

### Sprint 4 — 발행/운영

- YouTube OAuth bootstrap 문서와 uploader
- resumable upload, read-back, `UPLOAD_UNKNOWN`
- Telegram, metrics, daily reconciliation
- GitHub Actions dry-run workflow

### Sprint 5 — 공개 전환

- 30개 콘텐츠/IP/사실 품질 리뷰
- 테스트 채널 canary
- kill switch 훈련 후 public 1건 → 2건 단계 전환

---

## 17. 착수 완료 조건

- [ ] 이 상세설계와 PRD v0.2가 기준선으로 승인됨
- [ ] YouTube OAuth client와 refresh token 준비 책임자가 지정됨
- [ ] object storage와 원본 보존 비용이 확인됨
- [ ] 최초 회사 마크 asset registry 항목의 사용 조건이 검토됨
- [ ] 비용 0원 BGM source/provider의 상업 이용 근거가 등록됨
- [ ] TTS provider와 목소리가 확정됨
- [ ] `SHORTS_ENABLED=false` 상태로 Sprint 1 구현 시작

미확정 항목이 있어도 Sprint 1의 도메인·상태·스케줄러·FactPack은 착수할 수 있다. 반면 회사 마크와 BGM은 registry 승인이 없으면 각각 ticker fallback과 무음 BGM fallback을 사용한다.

남아 있는 운영 전 위험의 분석, 종료 조건과 구현 순서는 `docs/youtube_shorts_risk_closure_design_2026-07-24.md`를 따른다. 해당 문서의 R1~R6가 모두 `CLOSED`가 되기 전에는 `SHORTS_PUBLIC_ENABLED`를 활성화하지 않는다.

일상 점검, 배포 전 검사, 환경 승격, kill switch 및 장애 대응 절차는 `docs/youtube_shorts_operations_guide.md`를 따른다.
