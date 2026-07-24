# YouTube Shorts 운영 전 비차단 위험 종료 상세설계

> **상태**: v1.0 — 구현 기준선<br>
> **작성일**: 2026-07-24<br>
> **상위 문서**: `youtube_shorts_detailed_design_2026-07-24.md`<br>
> **목적**: offline pilot 이후 남아 있는 6개 위험을 운영 공개 전에 제거한다.

---

## 1. 위험 종료 원칙

현재 Shorts 코드는 기존 Alert 파이프라인과 격리된 offline pilot이므로 즉시 장애를 만드는 위험은 없다. 그러나 자동 스케줄, 공개 업로드, 동시 실행, 제3자 자산, 연도별 휴장일, 실제 생성 모델을 연결하는 순간 위험이 차단 위험으로 바뀐다.

따라서 위험별 상태를 다음 네 단계로 관리한다.

```text
OPEN → DESIGNED → VERIFIED → CLOSED
```

- `OPEN`: 분석만 존재하거나 보호장치가 없음
- `DESIGNED`: 인터페이스·실패 정책·테스트가 승인됨
- `VERIFIED`: 구현 및 자동 테스트가 완료됨
- `CLOSED`: 테스트 채널 canary와 운영 runbook 훈련까지 완료됨

어떤 위험도 `CLOSED`가 아니면 production environment의 `SHORTS_PUBLIC_ENABLED=true`를 허용하지 않는다. 일정 또는 일 2건 목표보다 안전 종료 조건이 우선이다.

### 1.1 위험 레지스터

| ID | 위험 | 현재 | 목표 | 공개 차단 |
|---|---|---|---|---|
| R1 | GitHub Actions 자동 스케줄 미연결 | OPEN | 별도 workflow와 reconciliation 검증 | Yes |
| R2 | YouTube OAuth uploader 미구현 | OPEN | resumable upload/read-back/revoke 검증 | Yes |
| R3 | DB 슬롯 claim·멱등성 미구현 | OPEN | 원자적 claim과 upload unknown 복구 | Yes |
| R4 | 회사 마크/BGM 라이선스 검증 미구현 | OPEN | 승인 registry 외 자산 사용 0건 | Yes |
| R5 | 휴장일 데이터가 2026년에 고정 | OPEN | 연도 독립 calendar와 freshness gate | Yes |
| R6 | 실제 Claude/Gemini/TTS 품질 pilot 미수행 | OPEN | 30건 offline + 7일 test-channel canary | Yes |

---

## 2. R1 — 별도 GitHub Actions 스케줄

### 2.1 위험 분석

- workflow가 없으면 08:00/22:00 자동 실행이 발생하지 않는다.
- GitHub scheduled workflow는 정각 지연·누락 가능성이 있으므로 cron 자체를 정확한 시계로 간주할 수 없다.
- 기존 Alert workflow에 Shorts를 추가하면 timeout, dependency, secret, concurrency가 결합되어 기존 알림을 방해할 수 있다.
- workflow 재실행이나 `workflow_dispatch`가 같은 슬롯을 중복 처리할 수 있다.

### 2.2 격리 구조

새 파일은 `.github/workflows/shorts.yml` 하나로 분리한다.

```yaml
name: YouTube Shorts

on:
  schedule:
    - cron: "7,22,37,52 * * * *"
  workflow_dispatch:
    inputs:
      dry_run: {type: boolean, default: true}
      local_date: {type: string, required: false}
      slot: {type: choice, options: [auto, morning, night], default: auto}

concurrency:
  group: shorts-${{ github.event.inputs.local_date || 'scheduled' }}-${{ github.event.inputs.slot || 'auto' }}
  cancel-in-progress: false

permissions:
  contents: read
```

cron은 매시간 네 번 dispatcher를 깨우는 용도다. `America/New_York`의 08:00/22:00 판정과 중복 차단은 애플리케이션+DB가 담당한다. 정각 부하를 피하도록 7분 offset을 사용하고 30분 slot window 안에서 두 번 기회가 생기게 한다.

### 2.3 Job 분리

```text
preflight → claim → generate → validate → render → publish → reconcile → notify
```

- `preflight`: Python/FFmpeg, required secret 존재, kill switch, calendar freshness 확인
- `claim`: DB에서 슬롯을 원자적으로 점유. claim 실패는 정상 skip
- `generate`: 외부 모델 호출. 기존 Alert DB는 read-only
- `validate`: hard gate; 실패 시 publish step 실행 금지
- `render`: MP4/SRT/manifest 생성 후 artifact hash 저장
- `publish`: production environment에서만 OAuth secret 접근
- `reconcile`: remote video read-back 및 DB 상태 확정
- `notify`: always 실행, Telegram에는 secret이 아닌 trace ID만 전달

### 2.4 Environment 분리

| Environment | upload | privacy | 목적 |
|---|---:|---|---|
| `shorts-dev` | false | none | PR 및 dry-run |
| `shorts-canary` | true | private/unlisted | 테스트 채널 검증 |
| `shorts-production` | true | public | 운영 채널 |

OAuth secret은 canary/production environment에 따로 저장한다. 기존 `alert.yml`의 Secret 및 concurrency group은 변경하지 않는다.

### 2.5 Reconciliation workflow

별도 `.github/workflows/shorts_reconcile.yml`을 미국 동부 현지시각 08:45/22:45에 해당하는 넓은 UTC 범위로 실행하거나 동일 dispatcher에서 `--reconcile`을 수행한다.

- 예정 slot이 없으면 `MISSED`
- `UPLOADING`이 20분 이상이면 session 상태 조회
- `UPLOAD_UNKNOWN`이면 remote ID/채널 최근 업로드를 읽되 자동 재업로드 금지
- 하루 공개 수가 0이면 critical, 1이면 warning, 2이면 healthy
- 2개를 초과하면 kill switch를 끄고 critical

### 2.6 종료 조건

- [ ] 기존 Alert/Sector workflow diff 0
- [ ] 7일 dry-run에서 슬롯당 claim 1개
- [ ] cron 45분 지연 simulation에도 `MISSED` 또는 안전 실행
- [ ] workflow 재실행 10회에서 생성/업로드 1개
- [ ] Shorts failure가 기존 workflow status에 영향 0

---

## 3. R2 — YouTube OAuth uploader

### 3.1 위험 분석

- 기존 `YOUTUBE_API_KEY`는 조회용이며 채널 업로드 권한을 제공하지 않는다.
- access token은 만료되므로 비대화형 실행에는 offline access로 받은 refresh token이 필요하다.
- 응답 유실 후 신규 업로드를 시작하면 중복 영상이 생긴다.
- 프로젝트/채널/API 정책 상태에 따라 공개 업로드가 제한될 수 있다.
- revoke된 token, quota 부족, processing failure는 재시도 성격이 서로 다르다.

### 3.2 컴포넌트

```python
class VideoPublisher(Protocol):
    def start_upload(self, request: UploadRequest) -> UploadSession: ...
    def resume(self, session: UploadSession) -> UploadResult: ...
    def read_back(self, video_id: str) -> RemoteVideo: ...
```

- `youtube_oauth.py`: refresh token으로 short-lived access token 생성
- `youtube_publisher.py`: `videos.insert` resumable session과 chunk 전송
- `youtube_reconciler.py`: video ID read-back, processing/privacy 검사
- SDK 객체와 HTTP response 원문은 도메인·DB에 저장하지 않는다.

### 3.3 Secret

```text
YOUTUBE_CLIENT_ID
YOUTUBE_CLIENT_SECRET
YOUTUBE_REFRESH_TOKEN
YOUTUBE_CHANNEL_ID
```

- token은 GitHub Environment Secret에만 저장
- 로그에는 설정 여부만 기록하고 길이·앞/뒤 문자도 출력하지 않음
- canary와 production은 서로 다른 채널/token 사용
- 90일마다 revoke/reissue drill, 유출 시 즉시 environment disable

### 3.4 Upload request

```json
{
  "content_id": "2026-07-24:night",
  "file_sha256": "...",
  "title": "한국어 제목",
  "description": "출처·기준시각·AI 고지·투자 면책",
  "tags": ["미국증시", "Shorts"],
  "language": "ko",
  "category_id": "27",
  "privacy_status": "public",
  "made_for_kids": false,
  "contains_synthetic_media": true
}
```

실제 API 필드 이름과 지원 여부는 구현 시 공식 YouTube Data API 문서를 다시 확인한다. 미지원 필드는 추측해 보내지 않고 channel Studio 설정/runbook으로 분리한다.

### 3.5 오류 정책

| 오류 | 정책 |
|---|---|
| 401 invalid/revoked credential | 재시도 0, `AUTH_BLOCKED`, production kill switch |
| 403 quotaExceeded | 해당 일 `SKIPPED_QUOTA`, critical 알림 |
| 403 policy/channel restriction | 재시도 0, 공개 중단 |
| 429/5xx/network | 동일 resumable session으로 최대 4회 backoff+jitter |
| response timeout after finalize | `UPLOAD_UNKNOWN`, 새 session 금지 |
| processing failed | 공개/삭제 runbook, 자동 재업로드 금지 |

### 3.6 Read-back gate

업로드 API 응답만으로 `PUBLISHED`로 전환하지 않는다.

1. video ID 저장
2. `videos.list` read-back
3. channel ID, title hash, privacy, processing 상태 확인
4. 일치하면 `PUBLISHED`
5. 불일치/불명확하면 `UPLOAD_UNKNOWN`

### 3.7 종료 조건

- [ ] OAuth bootstrap은 로컬 1회성 도구로 수행하고 token commit 0건
- [ ] test channel private 10건, unlisted 10건 중 중복 0건
- [ ] 401/403/429/5xx/timeout fixture contract test
- [ ] finalize 응답 유실 simulation에서 업로드 1개
- [ ] revoke drill 후 15분 안에 알림·차단
- [ ] 구현일의 공식 API/정책 확인 일자와 링크 기록

---

## 4. R3 — DB claim, 상태 전이와 멱등성

### 4.1 위험 분석

GitHub concurrency는 workflow 단위 완화책일 뿐, 재실행·API timeout·수동 실행까지 포괄하는 영속 멱등성을 보장하지 않는다. DB가 최종 조정자여야 한다.

### 4.2 Migration 설계

```sql
create table shorts_jobs (
  id uuid primary key default gen_random_uuid(),
  content_id text not null unique,
  local_publish_date date not null,
  slot_name text not null check (slot_name in ('morning','night')),
  content_mode text not null check (content_mode in ('market_day','evergreen')),
  state text not null,
  state_version bigint not null default 0,
  owner_run_id text,
  lease_expires_at timestamptz,
  topic_key text,
  input_hash text,
  last_error_code text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(local_publish_date, slot_name)
);

create table shorts_publications (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references shorts_jobs(id),
  platform text not null default 'youtube',
  remote_video_id text,
  file_sha256 text not null,
  upload_session_hash text,
  privacy_status text,
  processing_status text,
  created_at timestamptz not null default now(),
  unique(platform, remote_video_id),
  unique(job_id, file_sha256)
);
```

실제 migration에는 RLS, index, update timestamp trigger, rollback 주석을 포함한다. refresh token과 resumable session URL 원문은 저장하지 않고 keyed hash 또는 암호화된 short-lived store를 사용한다.

### 4.3 Claim RPC

```text
claim_slot(content_id, run_id, lease_seconds=1200)
```

하나의 transaction에서 다음을 수행한다.

1. `(local_publish_date, slot_name)` insert-on-conflict
2. 새 job 또는 만료 lease만 owner 획득
3. 현재 state와 `state_version` 반환
4. active lease면 `ALREADY_CLAIMED`

### 4.4 상태 compare-and-set

```sql
update shorts_jobs
set state = :next_state,
    state_version = state_version + 1,
    updated_at = now()
where id = :id
  and state = :expected_state
  and state_version = :expected_version;
```

영향 row가 0이면 다른 worker가 선점한 것이므로 실패가 아니라 안전 중단한다. 상태를 역행하거나 `VALIDATED`를 건너뛰어 `UPLOADING`으로 갈 수 없다.

### 4.5 Artifact 멱등성

- generation cache key: `sha256(fact_pack + prompt_version + model + policy_version)`
- render cache key: `sha256(script + scene_assets + audio + renderer_version)`
- publish key: `content_id + final_mp4_sha256`
- 원본 artifact는 immutable revision path로 저장
- 같은 key의 PASS artifact가 있으면 외부 API를 다시 호출하지 않음

### 4.6 장애 복구

| 발견 상태 | 복구 |
|---|---|
| lease 만료 + 생성 전 | 새 owner가 재개 |
| artifact 저장 완료 + DB 전이 전 | hash로 artifact 재연결 |
| `UPLOADING` + session 존재 | 동일 session resume |
| `UPLOADING` + session 불명 | `UPLOAD_UNKNOWN`, 운영 확인 |
| `PUBLISHED` + remote missing | critical, 자동 재게시 금지 |

### 4.7 종료 조건

- [ ] 10개 병렬 worker에서 claim 1개
- [ ] 모든 비허용 상태 전이 차단
- [ ] 각 단계 직후 process kill injection 후 안전 재개
- [ ] 동일 content/file hash upload 호출 1회
- [ ] DB 장애가 기존 Alert DB write 경로에 영향 0

---

## 5. R4 — 회사 마크와 무료 BGM asset registry

### 5.1 위험 분석

- “인터넷에서 찾은 로고/무료 음악”은 사용 허가를 의미하지 않는다.
- AI가 회사 마크를 재생성하면 왜곡, 혼동, 상표 훼손이 발생할 수 있다.
- 무료 BGM도 비상업용, 표시 의무, Content ID 등록, 기간 제한이 있을 수 있다.
- registry가 파일명만 검사하면 파일 교체로 승인되지 않은 자산이 들어갈 수 있다.

### 5.2 Asset schema

```json
{
  "asset_id": "company:nvda:primary:2026-01",
  "asset_type": "company_mark",
  "source_url": "https://official.example/brand-assets",
  "source_sha256": "...",
  "file_sha256": "...",
  "obtained_at": "2026-07-24T00:00:00Z",
  "terms_version": "captured hash",
  "commercial_youtube": true,
  "editorial_identification": true,
  "modification": "none",
  "attribution_text": null,
  "content_id_policy": "not_applicable",
  "expires_at": null,
  "reviewed_by": "owner-id",
  "approved": true
}
```

### 5.3 회사 마크 pipeline

1. 공식 회사 brand/media 페이지 allowlist에서만 수동 등록
2. 원본 파일·source URL·terms evidence snapshot 저장
3. malware/MIME/dimension/hash 검사
4. `approved=true` 이전 renderer 접근 금지
5. Gemini prompt/input에서 제외
6. 생성 이미지 완료 후 deterministic overlay
7. 원본 색·비율·clear space 유지, 장면 면적 10% 이하
8. registry 실패 시 회사명/티커 텍스트 fallback

회사 마크를 못 쓰는 것은 job 실패가 아니다. fallback은 콘텐츠 정확성을 유지하면서 IP 위험을 낮춘다.

### 5.4 BGM pipeline

허용식은 다음과 같다.

```text
approved
AND acquisition_cost_usd == 0
AND generation_cost_usd == 0
AND commercial_youtube == true
AND territory_limit == none
AND expiry == none
AND content_id_policy in (none, documented_release)
AND file_sha256 == registry.file_sha256
```

- 특정 음악가·곡 스타일 prompt, 보컬, 가사, 샘플링 금지
- 표시 의무가 있으면 description builder가 정확히 삽입 가능한 경우만 허용
- 승인 자산이 없으면 BGM 없는 내레이션으로 진행
- 승인 근거가 바뀌면 asset을 `REVOKED`로 바꾸고 신규 사용 즉시 중단
- 과거 영상까지 영향이 있으면 remote video 목록을 산출해 운영 알림

### 5.5 자동 validator

`LicenseValidator.validate(asset_id, file_path, publish_at)`는 다음 code를 반환한다.

- `ASSET_UNKNOWN`
- `ASSET_NOT_APPROVED`
- `HASH_MISMATCH`
- `TERMS_EXPIRED`
- `COMMERCIAL_USE_DENIED`
- `ATTRIBUTION_MISSING`
- `BGM_NONZERO_COST`
- `CONTENT_ID_RISK`

회사 마크 오류는 ticker fallback, BGM 오류는 no-BGM fallback으로 처리한다. fallback 결과도 manifest에 기록한다.

### 5.6 종료 조건

- [ ] registry 미승인 파일 renderer 접근 0건
- [ ] hash 교체·만료·non-commercial fixture 차단
- [ ] 로고가 Gemini API payload에 포함되지 않음을 contract test
- [ ] 무료 BGM 근거/attribution/Content ID runbook 검토
- [ ] registry 전체를 분기별 재검토하는 owner 지정

---

## 6. R5 — 연도 독립 시장 캘린더

### 6.1 위험 분석

현재 기존 캘린더의 휴장일 set은 2026년에 고정되어 있다. 연도가 바뀌면 공휴일을 거래일로 오인해 “장중/마감” 콘텐츠를 만들 수 있고, 특별 휴장과 조기 폐장도 반영하지 못한다.

### 6.2 Calendar provider interface

```python
class MarketCalendarProvider(Protocol):
    def session(self, local_date: date) -> MarketSession: ...

@dataclass(frozen=True)
class MarketSession:
    status: Literal["open", "closed", "early_close", "unknown"]
    open_at: datetime | None
    close_at: datetime | None
    source: str
    source_version: str
    fetched_at: datetime
```

### 6.3 Source 우선순위

1. 저장된 연도별 검증 calendar snapshot
2. 운영 중인 신뢰 가능한 calendar library/provider
3. 기존 수동 holiday set은 2026 fallback으로만 사용
4. 모두 실패하면 `unknown`; 시장일 콘텐츠를 만들지 않고 evergreen으로 degrade

특정 외부 provider 하나에 의존하지 않도록 adapter로 분리한다. 연도 snapshot은 코드 리뷰 가능한 JSON으로 보존하고 source URL, 확인일, hash를 기록한다.

### 6.4 Freshness gate

- 매년 10월 1일까지 다음 해 snapshot 필요
- 현재 날짜 이후 최소 400일 coverage 요구
- workflow preflight에서 coverage/fetched_at 확인
- special closure override는 별도 signed config로 추가
- early close는 22:00 콘텐츠에서 정상 마감과 구분

### 6.5 시간대 정확성

- 모든 DB timestamp는 UTC
- slot 계산과 시장 session 표현만 `America/New_York`
- 고정 `UTC-4` 사용 금지
- DST 전환일에도 현지 08:00/22:00을 유지
- tz database 누락 시 fail-safe evergreen, 공개 금지 가능하도록 error code 기록

### 6.6 종료 조건

- [ ] 2026·2027 snapshot fixture
- [ ] 주말, 정규 휴장, observed holiday, early close, unknown 테스트
- [ ] DST 시작/종료 전후 slot 테스트
- [ ] calendar coverage 400일 미만이면 preflight fail
- [ ] provider 장애 시 evergreen degrade 및 “현재 장중” 표현 0건

---

## 7. R6 — Claude/Gemini/TTS 실제 콘텐츠 파일럿

### 7.1 위험 분석

현재 단색 offline pilot은 MP4 기술 규격만 증명한다. 실제 모델을 연결하면 JSON 파손, 사실 환각, 캐릭터 불일치, IP 유사성, 한국어 발음, 32초 초과, API 비용·rate limit 문제가 새로 생긴다.

### 7.2 Provider 경계

```python
class ScriptProvider(Protocol):
    def create(self, fact_pack: FactPack, context: PromptContext) -> Script: ...

class ImageProvider(Protocol):
    def create_scene(self, prompt: ScenePrompt) -> ImageArtifact: ...

class SpeechProvider(Protocol):
    def synthesize(self, text: str, voice: VoiceConfig) -> AudioArtifact: ...
```

각 adapter는 timeout, retry, model name, request ID, usage, cost estimate를 표준 결과로 반환한다. API key, 원문 header, 모델 내부 reasoning은 저장하지 않는다.

### 7.3 Claude 단계

1. 검증된 FactPack만 prompt에 포함
2. JSON schema 강제 또는 JSON 추출 후 schema validation
3. FACT/HYPOTHESIS/DISCLAIMER 구분
4. evidence ID가 없는 사실 문장 차단
5. deterministic validator
6. writer와 분리된 critic pass
7. 오류 code를 포함한 재작성 최대 2회

모델이 기사 본문의 지시를 따르지 않도록 source text가 아닌 normalized fact만 전달한다.

### 7.4 Gemini 단계

- 캐릭터 bible hash와 scene seed 저장
- 프롬프트 앞뒤에 clean-room/IP negative block을 코드로 강제 삽입
- Marvel/Disney/작가명/실존 캐릭터명 입력 차단
- 회사 로고/실존 인물 이미지를 입력하지 않음
- OCR/visual moderation/유사성 critic 실패 시 장면 1회 재생성
- 2회 실패 시 승인된 추상 템플릿 fallback

### 7.5 TTS 단계

- 한국어 고정 voice ID, speaking rate, 발음 사전 version 저장
- 생성 후 실제 duration 측정
- 32초 초과 시 대본 축약 1회 후 TTS 재생성
- clipping, leading/trailing silence, peak, integrated loudness 검사
- 권리 확보되지 않은 voice clone 금지

### 7.6 30건 offline quality pilot

샘플 구성:

| 유형 | 건수 |
|---|---:|
| 거래일 오전 | 8 |
| 거래일 밤 | 8 |
| 휴장/주말 evergreen | 6 |
| 고변동성/속보 | 4 |
| provider 오류/fallback | 4 |

모든 건은 업로드하지 않고 artifact로만 검토한다.

Hard acceptance:

- 사실 오류 0
- evidence 없는 숫자 0
- IP high-risk 0
- 투자 권유 0
- 27~32초 충족 30/30
- 1080×1920 H.264/AAC 충족 30/30
- secret/log 유출 0
- 회사 마크/BGM registry 위반 0

Soft acceptance:

- 내부 평가 평균 85/100 이상
- 캐릭터 일관성 평균 4/5 이상
- 자막 오탈자 건당 0.2 이하
- 한국어 고유명사 발음 pass 95% 이상
- 영상 생성 p95 20분 이내

### 7.7 테스트 채널 canary

```text
Day 1~2: private 1건/일
Day 3~4: private 2건/일
Day 5~7: unlisted 2건/일
운영 전: public 1건/일 3일 → public 2건/일
```

각 단계 승격은 자동이 아니라 canary report의 hard acceptance 통과 후 config 변경으로 수행한다. 운영 목표는 즉시 공개지만, production 이전 테스트 채널에서 검증하는 것은 유지한다.

### 7.8 종료 조건

- [ ] provider contract test와 recorded fixture
- [ ] 30건 offline hard acceptance 전부 통과
- [ ] test-channel 7일 중복/정책/사실 사고 0
- [ ] 비용 cap 및 429/5xx fallback 확인
- [ ] model version 변경 시 5건 재-canary 정책 적용

---

## 8. 통합 공개 게이트

### 8.1 Machine-readable readiness

`shorts_release_readiness` 레코드 또는 signed JSON으로 다음을 관리한다.

```json
{
  "policy_version": "v1",
  "r1_workflow": "CLOSED",
  "r2_oauth": "CLOSED",
  "r3_idempotency": "CLOSED",
  "r4_assets": "CLOSED",
  "r5_calendar": "CLOSED",
  "r6_content_pilot": "CLOSED",
  "approved_at": null,
  "approved_by": null
}
```

`PublishGate`는 환경변수만 신뢰하지 않는다.

```text
config.can_publish
AND release_readiness.all_closed
AND calendar.fresh
AND job.state == READY_TO_PUBLISH
AND validation.all_hard_gates_passed
AND artifact.hash_verified
AND asset_registry.all_assets_approved
AND daily_publication_count < 2
```

### 8.2 Kill switch

| Switch | 차단 범위 | 사용 상황 |
|---|---|---|
| `SHORTS_ENABLED` | 전체 job 시작 | 보안/정책 사고 |
| `SHORTS_GENERATION_ENABLED` | 모델 호출 | 비용/API 장애 |
| `SHORTS_UPLOAD_ENABLED` | YouTube 전송 | OAuth/quota 장애 |
| `SHORTS_PUBLIC_ENABLED` | public 공개 | canary/품질 문제 |
| `COMPANY_MARKS_ENABLED` | 회사 마크 overlay | 상표/약관 이슈 |
| `BGM_ENABLED` | BGM mix | Content ID/라이선스 이슈 |

switch 평가 결과와 source(GitHub Variable/default)는 manifest에 저장한다.

### 8.3 Go/No-Go 회의 입력

- R1~R6 종료 증거 링크
- 최근 전체 테스트와 coverage
- 30건 quality report 및 7일 canary report
- OAuth revoke, duplicate, delete/private, asset revoke runbook 결과
- 월 예상비용과 일 cap
- 다음 400일 calendar coverage
- 책임자와 on-call 연락 경로

하나라도 없으면 No-Go다.

---

## 9. 구현 순서와 의존성

```text
R3 DB/idempotency ─┬─→ R1 workflow
                   └─→ R2 uploader
R5 calendar ─────────→ R1 scheduler
R4 asset registry ───→ R6 real pilot
R2 uploader ─────────→ R6 channel canary
R1~R6 CLOSED ────────→ production public
```

### Milestone A — 안전 기반

1. 연도 독립 calendar interface와 fixture
2. DB migration, claim RPC, 상태 CAS
3. asset registry와 validator

### Milestone B — 외부 provider

4. Claude adapter + schema/critic
5. Gemini adapter + IP guard
6. TTS adapter + duration/audio QC

### Milestone C — 발행

7. YouTube OAuth bootstrap/uploader/reconciler
8. Shorts 전용 GitHub Actions workflow
9. Telegram/reconciliation/runbooks

### Milestone D — 검증과 공개

10. 30건 offline pilot
11. 7일 test-channel canary
12. readiness R1~R6 close
13. production 1건/일 → 2건/일

---

## 10. 필수 테스트 매트릭스

| 영역 | Unit | Contract | Integration | Failure injection | Canary |
|---|---:|---:|---:|---:|---:|
| Scheduler/calendar | Yes | Yes | Yes | provider down | 7일 |
| DB claim/state | Yes | n/a | Yes | process kill/race | 7일 |
| Claude/Gemini/TTS | Yes | Yes | Yes | 429/5xx/malformed | 30건 |
| Asset registry | Yes | Yes | Yes | hash/terms revoke | 30건 |
| Renderer/QC | Yes | n/a | Yes | corrupt media | 30건 |
| YouTube uploader | Yes | Yes | Yes | auth/quota/timeout | 7일 |
| Existing pipelines | regression | n/a | full suite | Shorts failure | 매 PR |

모든 구현 PR은 다음을 통과해야 한다.

```bash
ruff check . --line-length=100
pytest tests/ -v --cov-fail-under=80
bash scripts/ci_preflight.sh
```

Shorts 변경이 기존 `alert.yml`, `sector_alert.yml`, weekly-news workflow를 수정한다면 별도 회귀 사유와 해당 workflow 테스트가 필요하다.

---

## 11. Runbook 요구사항

### 11.1 잘못된 영상 공개

1. `SHORTS_PUBLIC_ENABLED=false`
2. 대상 video ID 확인
3. 비공개 전환 우선, 필요 시 삭제
4. job `INCIDENT` 표시와 evidence/artifact 보존
5. 원인 gate 수정 및 전체 유사 영상 검색
6. 재개 전 5건 canary

### 11.2 중복 영상

1. upload switch 차단
2. 두 video의 content/file hash 비교
3. 후발 영상을 비공개
4. claim/upload session audit
5. concurrency test 재실행

### 11.3 OAuth 유출/revoke

1. GitHub Environment 비활성화
2. Google 계정에서 token revoke
3. client secret rotate
4. 로그/artifact secret scan
5. canary token 재발급 후 private 1건 검증

### 11.4 Asset 권리 변경

1. asset `REVOKED`
2. company mark/BGM switch 차단
3. 영향 video 목록 추출
4. 조건에 따라 description 수정·음소거·비공개
5. ticker/no-BGM fallback 검증

---

## 12. 공식 문서 재확인 목록

구현자는 API/정책이 변할 수 있으므로 구현일에 다음 공식 문서를 직접 확인하고 evidence에 확인일을 기록한다.

- YouTube Data API: `videos.insert`, `videos.list`, resumable uploads, quota
- Google OAuth 2.0: web/installed app, offline access, token revoke
- YouTube: altered/synthetic content, spam, reused/repetitious content, made for kids
- GitHub Actions: scheduled workflows, concurrency, environments, secrets
- Anthropic: Messages API, structured output 지원 여부, model lifecycle, rate limits
- Google Gemini API: image generation, safety, model lifecycle, rate limits, data use

문서에 적힌 request 예시는 구현 힌트이며 공식 API schema보다 우선하지 않는다.

---

## 13. 최종 Definition of Ready

- [ ] R1~R6 상태가 모두 `CLOSED`
- [ ] 기존 파이프라인 전체 테스트 및 핵심 preflight 통과
- [ ] Shorts package coverage가 repository 80% gate에 포함
- [ ] production OAuth와 test OAuth가 분리됨
- [ ] DB race/timeout/finalize unknown 테스트 통과
- [ ] 회사 마크/BGM registry와 fallback 검증
- [ ] 400일 calendar freshness 충족
- [ ] 30건 offline + 7일 test-channel canary 통과
- [ ] kill switch와 4개 incident runbook 훈련 완료
- [ ] production 초기 3일은 1건/일, 이후 2건/일 승격 승인

이 체크리스트가 완료되기 전까지 현재 offline pilot은 “기술 검증 성공”일 뿐 “운영 공개 준비 완료”로 간주하지 않는다.
