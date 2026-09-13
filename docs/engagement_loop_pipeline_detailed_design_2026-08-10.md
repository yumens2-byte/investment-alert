# 신규 참여 루프 파이프라인 상세설계

> 작성일: 2026-08-10
>
> 상태: Supabase 기반 Phase A 개발 착수
>
> 결정: 기존 `alert`, `sector_alert`, `weekly_news_x`, `shorts` 파이프라인은 수정하지 않는다. 신규 `engagement_loop`가 독립적으로 생성·검수·발행·측정한다.

---

## 1. 설계 목표와 비목표

### 1.1 목표

신규 파이프라인은 하나의 시장 관찰을 월·수·금 콘텐츠로 연결한다.

1. 월요일 `THREE_NUMBERS`: 숫자 3개와 사전 판정 기준 공개
2. 수요일 `SCENARIO_POLL`: A/B/C 선택과 이유 수집
3. 금요일 `SCORECARD`: 월요일 기준을 변경하지 않고 결과·오판·응답 분포 공개
4. 일요일 `NEXT_WEEK_CALENDAR`: 다음 루프의 공식 일정 후보 제공

핵심 산출물은 게시물 4개가 아니라 동일 `loop_id`로 연결된 하나의 검증 가능한 루프다.

### 1.2 비목표

- 기존 워크플로우, 프롬프트, 발행 모듈, DB 스키마의 수정
- 기존 주말 뉴스 발행 빈도 축소 또는 대체
- 실시간 급등락·섹터 경보의 우선순위 변경
- 개인 포트폴리오 추천, 목표가, 매수·매도 신호 생성
- 1차 버전에서 자유서술 답글을 LLM만으로 자동 인용
- 1차 버전에서 무인 실발행

### 1.3 성공 조건

| 구분 | 조건 |
|---|---|
| 격리 | 기존 파이프라인 파일 변경 0건, 기존 테스트 회귀 0건 |
| 완결성 | 시작한 루프의 금요일 결산 누락 0건(명시적 `CANCELLED` 제외) |
| 안전성 | 숫자 출처·시점 누락 시 발행 차단, 중복 발행 0건 |
| 운영성 | 4주 파일럿 기준 운영자 검수 주 90분 이내 |
| 성과 | 기준선 대비 의미 있는 답글률 또는 저장률 20% 이상 개선 |

---

## 2. 격리 원칙

### 2.1 변경 금지 영역

구현 PR에서 다음 경로는 수정하지 않는다.

```text
run_alert.py
run_sector_alert.py
run_shorts.py
detection/
collectors/
publishers/weekly_news_x/
publishers/x_publisher.py
.github/workflows/alert.yml
.github/workflows/sector_alert.yml
.github/workflows/weekly_news_*.yml
```

공통 유틸리티에 기능이 필요하더라도 첫 버전에서는 복사하지 않고 신규 패키지 내부 adapter로 감싼다. 충분히 안정화된 뒤 공통화는 별도 PR에서 수행한다.

### 2.2 허용되는 단방향 읽기

신규 파이프라인은 아래 데이터를 **읽기 전용**으로 참조할 수 있다.

- `logs/weekly_news/**/*.md`: 후보 이슈와 출처 탐색 보조
- 기존 alert/sector 발행 sidecar 또는 운영 로그: 발행 충돌 확인
- `config/market_calendar.py`: 직접 import하지 않고 adapter 인터페이스 뒤에서 호출

읽기 실패는 기존 파이프라인에 영향을 주지 않는다. 입력을 확보하지 못하면 신규 회차만 `SKIPPED_INPUT`으로 종료한다.

### 2.3 저장·실행 격리

| 항목 | 기존 | 신규 |
|---|---|---|
| Python package | `publishers/weekly_news_x` 등 | `engagement_loop` |
| 실행 진입점 | 기존 `run_*.py` | `run_engagement_loop.py` |
| GitHub Actions group | 기존 group 유지 | `engagement-loop-{slot}` |
| 영속 저장 | 기존 DB·`logs/...` | 신규 Supabase `ia_engagement_*` 테이블 |
| 설정 prefix | 혼합 기존 변수 | 기능 설정은 `ENGAGEMENT_LOOP_*`, Supabase는 기존 `SUPABASE_*` 재사용 |
| Notion | 기존 DB | 초기 미사용, 필요 시 별도 DB |
| 중복 방지 | 기존 sidecar | `ia_engagement_contents` unique constraint |

---

## 3. 제안 디렉터리 구조

```text
engagement_loop/
├── __init__.py
├── config.py                 # 신규 prefix 환경설정과 기본값
├── models.py                 # Loop, Fact, Criterion, Draft, Metrics
├── ids.py                    # loop_id/content_id 결정적 생성
├── clock.py                  # KST/미국시장 기준 시각
├── sources/
│   ├── base.py               # MarketSource Protocol
│   ├── market_data.py        # 숫자·시점·출처 수집 adapter
│   ├── economic_calendar.py  # 공식 일정 adapter
│   └── existing_archives.py  # 기존 archive read-only adapter
├── compose/
│   ├── three_numbers.py
│   ├── scenario_poll.py
│   ├── scorecard.py
│   └── next_week_calendar.py
├── validation/
│   ├── facts.py              # value/unit/as_of/source 검증
│   ├── content.py            # 금칙어/CTA/선택지/길이
│   └── continuity.py         # 월→수→금 불변 조건 검증
├── supabase_repository.py    # 전용 service role 기반 Supabase 저장소
├── publishing/
│   ├── gateway.py            # Publisher Protocol
│   ├── x_gateway.py          # X API 격리
│   └── dry_run_gateway.py
├── metrics/
│   ├── gateway.py            # MetricsGateway Protocol
│   ├── x_metrics.py
│   └── reply_classifier.py   # 규칙 기반 1차 분류
└── service.py                # use case orchestration

run_engagement_loop.py
.github/workflows/engagement_loop_monday.yml
.github/workflows/engagement_loop_wednesday.yml
.github/workflows/engagement_loop_friday.yml
.github/workflows/engagement_loop_sunday.yml
tests/engagement_loop/
db/migrations/005_add_engagement_loop_tables.sql
```

워크플로우를 하나의 복잡한 YAML로 합치지 않고 슬롯별로 분리한다. 한 슬롯 장애가 다른 슬롯의 수동 재실행과 운영 판단을 방해하지 않게 하기 위함이다. 공통 로직은 모두 `service.py`에 두고 YAML에는 스케줄과 secret wiring만 둔다.

---

## 4. 도메인 모델과 불변조건

### 4.1 주요 타입

```python
class Slot(str, Enum):
    THREE_NUMBERS = "three_numbers"
    SCENARIO_POLL = "scenario_poll"
    SCORECARD = "scorecard"
    NEXT_WEEK_CALENDAR = "next_week_calendar"


class LoopStatus(str, Enum):
    PLANNED = "planned"
    OPEN = "open"
    POLLING = "polling"
    READY_TO_SCORE = "ready_to_score"
    CLOSED = "closed"
    SKIPPED_INPUT = "skipped_input"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Fact:
    key: str
    value: Decimal
    unit: str
    as_of: datetime
    source_url: str
    source_name: str
    retrieved_at: datetime


@dataclass(frozen=True)
class Criterion:
    fact_key: str
    operator: Literal["gt", "gte", "lt", "lte", "between"]
    threshold: tuple[Decimal, ...]
    interpretation: str


@dataclass
class EngagementLoop:
    schema_version: str
    loop_id: str
    week_start_kst: date
    status: LoopStatus
    facts: list[Fact]
    criteria: list[Criterion]
    scenario_options: list[ScenarioOption]
    publications: dict[Slot, Publication]
```

### 4.2 ID 규칙

```text
loop_id    = YYYY-Www                       예: 2026-W33
content_id = {loop_id}:{slot}:v{revision}   예: 2026-W33:three_numbers:v1
```

- KST 월요일이 속한 ISO week를 사용한다.
- 재시도는 같은 `content_id`를 사용한다.
- 사람이 본문을 수정해 새 승인이 필요할 때만 revision을 올린다.
- 같은 revision에 `publication.post_id`가 있으면 발행을 거부한다.

### 4.3 핵심 불변조건

1. `THREE_NUMBERS`에는 서로 다른 `fact_key`가 정확히 3개다.
2. 모든 Fact는 timezone-aware `as_of`, HTTPS `source_url`, 단위를 가진다.
3. 월요일 발행 후 `criteria`는 수정할 수 없다.
4. `SCENARIO_POLL`은 2~3개 선택지이며 마지막 선택지는 보류/판단 유보다.
5. `SCORECARD`는 월요일 criterion 각각에 `HIT`, `MISS`, `PENDING` 중 하나를 기록한다.
6. 응답 비율에는 집계 시각과 유효 표본 수 `n`을 반드시 병기한다.
7. 발행물 한 개의 CTA는 1개 이하이다.
8. 매수·매도·수익 보장 표현이 검출되면 자동 발행할 수 없다.

---

## 5. 상태 머신

```text
                         입력 부족
PLANNED ── 월 초안 승인 ─────────────> SKIPPED_INPUT
   │
   └── 월 발행 성공 → OPEN
                         │
                         ├── 수 발행 성공 → POLLING
                         │                    │
                         │                    └── 집계 마감 → READY_TO_SCORE
                         │                                          │
                         └── 수 미발행 ─────────────────────────────┤
                                                                    └── 금 발행 → CLOSED

어느 상태에서든 운영자 중단 → CANCELLED
```

- 수요일 게시물이 없어도 금요일에는 월요일 기준을 자체 채점해 루프를 닫는다.
- 월요일이 발행되지 않았다면 수·금 게시물을 새로 만들지 않는다.
- 금요일 데이터가 확정되지 않은 항목은 실패가 아니라 `PENDING`으로 남긴다.
- `CLOSED`, `SKIPPED_INPUT`, `CANCELLED`는 종결 상태이며 자동으로 되돌리지 않는다.

### 상태 전이 기록

각 전이는 `ia_engagement_events`에 append-only로 기록한다.

```json
{"event_id":"uuid","loop_id":"2026-W33","from":"open","to":"polling","reason":"poll_published","at":"2026-08-12T21:00:11+09:00","run_id":"github-run-id"}
```

`ia_engagement_loops`는 빠른 조회를 위한 현재 상태 projection이고, 복구·감사의 원장은 `ia_engagement_events`다.

---

## 6. Supabase 데이터 저장 계약

### 6.1 테이블

```text
ia_engagement_loops       # 주간 aggregate와 불변 criterion
├── ia_engagement_facts   # 월/금/calendar 시점별 원자료
├── ia_engagement_contents# 초안·승인 hash·X 발행 결과
├── ia_engagement_events  # append-only 상태 전이 감사로그
├── ia_engagement_metrics # 콘텐츠별 24h/72h snapshot
└── ia_engagement_responses # 원문 없는 해시 기반 A/B/C 분류
```

모든 테이블은 `ia_engagement_` prefix를 사용하고 기존 테이블과 FK를 맺지 않는다. 세부 DDL, check/unique constraint, index와 rollback은 `db/migrations/005_add_engagement_loop_tables.sql`을 단일 원본으로 사용한다.

### 6.2 보안 경계

- 여섯 테이블 모두 RLS를 활성화하고 `anon`, `authenticated` 권한을 전부 회수한다.
- 클라이언트 정책은 만들지 않는다. GitHub Actions의 server-side service role만 접근한다.
- 저장소에 이미 등록된 `SUPABASE_URL`, `SUPABASE_KEY`를 재사용한다. 단, 클라이언트 RLS policy가 없으므로 `SUPABASE_KEY`는 server-side service role key여야 한다.
- service role key, 응답 원문, X user ID는 DB·로그·artifact·PR에 기록하지 않는다.
- 팔로워 식별자는 저장소 밖의 salt를 이용한 SHA-256 hash만 저장하며 원문은 저장하지 않는다.
- 마이그레이션은 관리자 SQL Editor에서 수행하고 key는 GitHub Environment 보호 규칙을 거친다.

### 6.3 발행 멱등성

```json
{
  "content_id": "2026-W33:three_numbers:v1",
  "content_sha256": "...",
  "x_post_id": "...",
  "published_at": "2026-08-10T07:30:12+09:00"
}
```

`content_id`, `(loop_id, slot, revision)`, `content_sha256` unique constraint를 DB 최종 방어선으로 둔다. 발행 전 published row를 조회하며, API 호출이 성공했으나 row 갱신 여부가 불명확하면 status를 `unknown_publication`으로 두고 자동 재시도하지 않는다.

### 6.4 트랜잭션과 동시성

- loop 생성과 Fact snapshot 저장은 별도 단계로 기록하되, Fact가 완결되기 전 콘텐츠 상태를 `approved`로 올리지 않는다.
- 상태 전이는 향후 Supabase RPC로 `expected_status`를 함께 비교하는 optimistic locking을 적용한다.
- Phase A repository는 upsert/get/fact snapshot/event append부터 구현하며 발행 상태 RPC는 실발행 PR 전에 추가한다.
- 이벤트 `event_id`와 모든 콘텐츠 식별자는 재실행에도 동일하게 생성해 네트워크 재시도를 멱등하게 만든다.

### 6.5 적용 순서와 검증

1. Supabase SQL Editor에서 `db/migrations/005_add_engagement_loop_tables.sql`을 관리자 역할로 실행한다.
2. 기존 GitHub 보호 Environment의 `SUPABASE_URL`, `SUPABASE_KEY`가 해당 프로젝트의 service role credential인지 확인한다.
3. service role key는 Environment 승인자만 사용할 수 있게 하고 fork PR 및 일반 pull request에는 주입하지 않는다.
4. 아래 쿼리로 테이블과 RLS를 확인한다.

```sql
SELECT tablename, rowsecurity
FROM pg_tables
WHERE schemaname = 'public'
  AND tablename LIKE 'ia_engagement_%'
ORDER BY tablename;

SELECT grantee, table_name, privilege_type
FROM information_schema.role_table_grants
WHERE table_schema = 'public'
  AND table_name LIKE 'ia_engagement_%'
ORDER BY table_name, grantee, privilege_type;
```

첫 결과는 6개 테이블 모두 `rowsecurity = true`, 두 번째 결과는 `anon`과 `authenticated`에 부여된 권한이 없어야 한다. 실제 migration 적용은 외부 Supabase 관리자 작업이므로 이 코드 PR에서는 수행하지 않는다.

---

## 7. 슬롯별 처리 상세

### 7.1 월요일: THREE_NUMBERS

1. 기존 archive와 시장 데이터 adapter에서 후보 Fact를 수집한다.
2. 중요도, 출처 품질, 금요일 판정 가능 여부로 후보를 정렬한다.
3. 서로 다른 성격의 Fact 3개를 선택한다. 기본 구성은 금리 1, 변동성/지수 1, 주간 이벤트 1이다.
4. 각 Fact에 정량 criterion과 해석을 만든다.
5. validator가 값·단위·시점·출처와 금칙어를 확인한다.
6. markdown 초안은 `ia_engagement_contents`, loop 상태는 `ia_engagement_loops`에 저장한다.
7. 운영자 승인 후 발행하고 상태를 `OPEN`으로 전이한다.

**실패 정책:** 3개의 검증된 Fact를 확보하지 못하면 1~2개로 축약하지 않고 `SKIPPED_INPUT` 처리한다. 형식의 약속을 지키기 위함이다.

### 7.2 수요일: SCENARIO_POLL

1. `OPEN` 루프와 월요일 게시물 URL을 불러온다.
2. 월요일 이후 변화를 요약하되 criterion은 변경하지 않는다.
3. A/B/C 선택지를 생성하고 C를 보류로 둔다.
4. CTA가 하나인지, 선택지가 중복되지 않는지 검증한다.
5. 승인·발행 후 `POLLING`으로 전이한다.

네이티브 poll 지원 여부나 API 권한이 확인되지 않으면 텍스트 A/B/C 답글 방식이 기본이다. X API 기능과 요금제별 metrics/reply 조회 범위는 구현 착수 시 공식 문서와 실제 앱 권한으로 discovery test를 수행하며, 지원되지 않는 값은 추정하지 않는다.

### 7.3 금요일: SCORECARD

1. 월요일 원본 Fact와 criterion을 읽는다.
2. 같은 source adapter에서 금요일 Fact를 새로 수집한다.
3. 순수 함수 `evaluate(criterion, fact) -> HIT | MISS | PENDING`으로 판정한다.
4. 수요일 post의 응답을 집계한다.
5. 규칙 기반으로 `A`, `B`, `C`, 무효 응답을 분류한다.
6. 자유서술 이유는 자동 인용하지 않고 운영자에게 표본만 제공한다.
7. 오판 변수와 다음 주 변경점을 사람이 승인한다.
8. 발행 성공 후 `CLOSED`로 전이한다.

응답 조회 권한이 없거나 조회에 실패하면 분포를 생략하고 `응답 집계 미제공`을 기록한다. 시장 채점 자체는 계속 진행한다.

### 7.4 일요일: NEXT_WEEK_CALENDAR

- 다음 주 공식 경제지표, FOMC 관련 일정, 주요 실적 후보를 수집한다.
- 날짜·시각·timezone·공식 출처가 있는 일정만 포함한다.
- 기존 주말 뉴스와 내용이 겹쳐도 기존 게시물을 변경하지 않는다.
- 월요일 루프의 Fact를 미리 확정하지 않으며 후보만 저장한다.

---

## 8. 승인 계약

### 8.1 승인 row

```json
{
  "schema_version": "1.0",
  "content_id": "2026-W33:three_numbers:v1",
  "content_sha256": "...",
  "decision": "approved",
  "reviewer": "github-user",
  "reviewed_at": "2026-08-10T07:12:00+09:00",
  "expires_at": "2026-08-10T09:00:00+09:00"
}
```

승인은 정확한 content hash에 결합한다. 승인 후 본문이 한 글자라도 바뀌면 승인은 무효다. 승인 만료 뒤에는 시세가 달라졌을 가능성이 있으므로 Fact를 재수집하고 revision을 올린다.

### 8.2 1차 운영 방식

1차 파일럿은 draft PR 방식을 사용한다.

1. workflow가 초안·Fact·loop state를 Supabase에 저장하고 검수용 PR 생성
2. CI가 schema, continuity, length, prohibited language를 검증
3. 운영자가 숫자·출처·문장을 확인하고 approval label 부여
4. 별도 `workflow_dispatch`에서 `content_id`와 revision을 명시해 발행

기존 weekly news의 merge-trigger publish는 재사용하지 않는다. 신규 파이프라인의 승인과 발행은 독립된 workflow_dispatch로 제한한다.

---

## 9. GitHub Actions 상세

### 9.1 공통 입력과 환경변수

```text
ENGAGEMENT_LOOP_ENABLED=false          # kill switch, 기본 비활성
ENGAGEMENT_LOOP_DRY_RUN=true           # 기본 dry-run
ENGAGEMENT_LOOP_AUTO_PUBLISH=false     # 1차 버전에서는 true 금지
ENGAGEMENT_LOOP_TIMEZONE=Asia/Seoul
ENGAGEMENT_LOOP_COLLISION_WINDOW_MIN=180
ENGAGEMENT_LOOP_MAX_FACT_AGE_MIN=30
SUPABASE_URL=<existing-project-url>
SUPABASE_KEY=<existing-service-role-secret>
```

X credential과 Supabase credential은 기존 secret 이름을 재사용한다. 신규 파이프라인의 격리는 별도 `ia_engagement_*` 테이블, RLS, 코드 경로와 publication 저장 경계로 보장한다.

### 9.2 스케줄 초안

| workflow | cron(UTC) | KST | 동작 |
|---|---:|---:|---|
| monday | 일 22:30 | 월 07:30 | 수집·초안·PR |
| wednesday | 수 12:00 | 수 21:00 | 선택형 초안·PR |
| friday | 목 22:30 | 금 07:30 | 재수집·채점·PR |
| sunday | 일 00:00 | 일 09:00 | 다음 주 캘린더 초안·PR |

cron은 초안 생성만 수행한다. 실발행 시각은 승인 완료 시점이므로 1차 버전에서 정시 SLA를 약속하지 않는다.

### 9.3 collision guard

신규 파이프라인만 다음을 수행한다.

1. 기존 sidecar와 신규 publication의 최근 발행 시각을 검색한다.
2. 예정 시각 ±180분 이내 기존 경보가 있으면 신규 정기물을 `DEFERRED` 처리한다.
3. 최대 6시간 내 다음 슬롯으로 한 번만 연기한다.
4. 재확인에도 충돌하면 해당 회차를 취소하고 운영 알림을 남긴다.

기존 파이프라인은 신규 상태를 알 필요가 없다. 우선순위는 항상 `실시간 경보 > 기존 뉴스 > 신규 참여 루프`다.

### 9.4 concurrency

```yaml
concurrency:
  group: engagement-loop-${{ inputs.slot || 'scheduled-slot' }}
  cancel-in-progress: false
```

동일 슬롯 중복 실행을 막되 월요일 장애가 수요일 수동 점검을 기술적으로 막지는 않는다. 도메인 상태 머신이 실행 가능 여부를 최종 판정한다.

---

## 10. 발행과 지표 adapter

### 10.1 Publisher 계약

```python
class Publisher(Protocol):
    def publish(self, *, content_id: str, text: str) -> PublishResult: ...


@dataclass(frozen=True)
class PublishResult:
    post_id: str
    post_url: str
    published_at: datetime
```

서비스 계층은 tweepy나 HTTP 응답을 알지 못한다. `DryRunPublisher`는 결정적인 가짜 post ID를 반환하지 않고 `published=False`를 명시해 실 publication과 혼동되지 않게 한다.

### 10.2 Metrics 계약

```python
class MetricsGateway(Protocol):
    def get_post_metrics(self, post_id: str, as_of: datetime) -> PostMetrics: ...
    def list_replies(self, post_id: str, until: datetime) -> list[Reply]: ...
```

권한 또는 플랜에 따라 얻을 수 없는 필드는 `0`이 아니라 `None`으로 저장한다. `None`은 미지원/미수집이고 `0`은 실제 0이므로 구분해야 한다.

### 10.3 응답 분류 v1

- 첫 비공백 토큰이 `A`, `B`, `C` 중 하나이면 해당 선택으로 집계
- 한 사용자의 여러 답글은 마감 전 마지막 유효 선택만 반영
- bot, 삭제, 작성자 본인, 명백한 중복은 제외
- A와 B를 동시에 고른 답글은 무효 처리
- 이유 텍스트는 저장 최소화 원칙에 따라 원문 대신 reply ID와 분류만 보존

사용자 식별이 필요한 중복 제거는 플랫폼 user ID의 salted hash를 사용하고 salt는 저장소에 커밋하지 않는다. 원문 보존이 꼭 필요하면 보존 기간과 삭제 절차를 별도 승인받는다.

---

## 11. 검증과 실패 정책

### 11.1 검증 계층

| 계층 | 검사 | 실패 동작 |
|---|---|---|
| Schema | 필수 필드, enum, timezone | 신규 회차 중단 |
| Fact | 값·단위·시점·출처·freshness | 초안 생성 금지 |
| Continuity | loop_id, 월 criterion 불변 | 수·금 발행 금지 |
| Content | X 길이, CTA 1개, 금칙어 | 승인 불가 |
| Approval | hash, reviewer, expiry | 실발행 금지 |
| Idempotency | content ID/hash/post ID | 중복 호출 금지 |
| Collision | 기존 게시물과 시간 간격 | 연기 또는 취소 |

### 11.2 fail-closed / fail-open

**Fail-closed:** 시장 숫자, 출처, 상태 파일, 승인, 발행 결과가 불확실한 경우 신규 게시물을 내보내지 않는다.

**Fail-open:** metrics와 답글 조회 실패는 게시물 발행을 막지 않는다. 단, 확보하지 못한 응답 분포를 만들어내지 않으며 금요일 본문에 집계 생략을 반영한다.

### 11.3 재시도

- source 조회: 지수 백오프 최대 3회
- metrics 조회: 최대 3회, 이후 `METRICS_UNAVAILABLE`
- publish: 네트워크 timeout 뒤 자동 재시도 금지. 먼저 post 존재 여부를 조회하고 사람이 재개
- 파일 쓰기: API 호출 전에는 안전 재시도, API 성공 뒤에는 수동 복구

---

## 12. 관측성과 운영 알림

### 12.1 구조화 로그

모든 로그에 다음 필드를 포함한다.

```text
pipeline=engagement_loop
loop_id=2026-W33
content_id=2026-W33:scorecard:v1
slot=scorecard
run_id=...
stage=validate|approve|publish|metrics
result=success|skip|fail|unknown
```

secret, access token, 팔로워 원문, 사용자 ID는 로그에 남기지 않는다.

### 12.2 운영 알림 우선순위

| 등급 | 예시 | 알림 |
|---|---|---|
| INFO | dry-run, 입력 부족 skip | GitHub Summary |
| WARN | metrics 미지원, 발행 충돌 연기 | Summary + 운영 채널 |
| ERROR | schema/continuity/approval 실패 | 운영 채널 |
| CRITICAL | 발행 성공 여부 불명, 중복 의심 | 즉시 운영 채널 + 자동 중단 |

신규 notifier가 실패해도 기존 notifier나 기존 파이프라인에는 영향을 주지 않는다.

---

## 13. 테스트 전략

### 13.1 단위 테스트

- ISO week 경계와 KST 날짜로 ID 생성
- Fact timezone, URL, freshness 검증
- Decimal 경계값에서 `gt/gte/lt/lte/between` 판정
- 월요일 criterion 변경 탐지
- A/B/C 및 보류 선택지 검증
- 답글 중복·복수 선택·무효 응답 분류
- content hash와 승인 만료
- publication idempotency와 원자적 저장
- 기존 발행 충돌 시 defer/cancel

### 13.2 통합 테스트

1. 월→수→금 정상 루프와 `CLOSED`
2. 월 입력 부족 시 후속 회차 차단
3. 수요일 생략 후 금요일 자체 채점
4. metrics 미지원 상태의 금요일 축약 본문
5. publish timeout 후 `UNKNOWN_PUBLICATION`
6. 승인 뒤 본문 변경 시 발행 차단
7. 기존 alert 발행 직후 신규 게시물 연기
8. 같은 workflow 재실행 시 중복 발행 차단

### 13.3 회귀·격리 테스트

```bash
git diff --exit-code BASE_SHA -- \
  run_alert.py run_sector_alert.py run_shorts.py \
  detection collectors publishers/weekly_news_x publishers/x_publisher.py \
  .github/workflows/alert.yml .github/workflows/sector_alert.yml \
  .github/workflows/weekly_news_draft.yml \
  .github/workflows/weekly_news_draft_sunday.yml \
  .github/workflows/weekly_news_publish.yml

python -m pytest tests/ --no-cov
python -m pytest tests/engagement_loop/ --cov=engagement_loop --cov-fail-under=90
ruff check engagement_loop run_engagement_loop.py tests/engagement_loop
```

첫 명령의 비교 기준 `BASE_SHA`는 구현 브랜치가 갈라진 기준 커밋으로 CI가 주입한다.

---

## 14. 단계별 구현 계획

### Phase A — discovery와 골격

- X 앱의 실제 권한으로 post metrics, reply 조회, rate limit을 확인한다.
- 지원 필드 표를 작성하고 미지원 값의 `None` 계약을 확정한다.
- package, models, Supabase migration/repository, dry-run CLI를 만든다.
- 기존 파이프라인 무변경 CI guard를 추가한다.

**종료 기준:** 외부 발행 없이 Supabase test project에서 샘플 loop/fact/event를 생성·복구할 수 있다.

### Phase B — 월요일 dry-run

- source adapter, Fact validator, THREE_NUMBERS composer를 구현한다.
- 초안 PR과 승인 hash를 검증한다.
- 2주간 dry-run으로 숫자·출처·검수 시간을 측정한다.

**종료 기준:** 4회 연속 Fact 검증 통과, 잘못된 숫자 자동 통과 0건.

### Phase C — 수·금 폐쇄 루프

- SCENARIO_POLL, reply 집계, SCORECARD를 구현한다.
- criterion 불변과 상태 전이를 테스트한다.
- 여전히 수동 workflow_dispatch만으로 발행한다.

**종료 기준:** 테스트 계정에서 2개 루프를 중복·누락 없이 닫는다.

### Phase D — 일요일과 성과 수집

- NEXT_WEEK_CALENDAR와 24h/72h metrics snapshot을 추가한다.
- 4주 파일럿 후 기준선과 비교한다.
- 운영자 시간, 정정률, 재참여율로 계속 여부를 판단한다.

**종료 기준:** 성공 조건 충족 시에만 제한적 자동 발행을 별도 설계·승인한다.

---

## 15. 구현 PR 분할

| PR | 범위 | 실발행 |
|---|---|---|
| 1 | models, IDs, Supabase migration/repository, 단위 테스트 | 없음 |
| 2 | source adapters, Fact validation, 월요일 composer | 없음 |
| 3 | draft workflow, approval contract, dry-run summary | 없음 |
| 4 | isolated X publisher, idempotency, 수동 발행 | 수동만 |
| 5 | 수요일 composer와 reply adapter | 수동만 |
| 6 | 금요일 evaluator와 scorecard | 수동만 |
| 7 | metrics snapshots, 일요일 calendar, 운영 대시보드 입력 | 수동만 |

각 PR은 기존 파이프라인 무변경 guard와 전체 회귀 테스트를 통과해야 한다. `AUTO_PUBLISH=true` 전환은 위 구현 PR에 포함하지 않고 4주 운영 보고서 이후 별도 승인 대상으로 둔다.

---

## 16. 구현 전 결정 필요 사항

| ID | 질문 | 기본안 | 결정 시점 |
|---|---|---|---|
| D-01 | X reply/metrics 조회 권한이 충분한가? | discovery 후 미지원은 `None` | Phase A |
| D-02 | draft 승인을 PR label로 할지 별도 승인 파일로 할지? | hash가 남는 승인 파일 | Phase A |
| D-03 | source provider와 공식 출처 우선순위는? | 공식 1차 출처 + 보조 1곳 | Phase B |
| D-04 | 기존 발행 이력의 표준 조회 위치는? | adapter가 여러 sidecar를 read-only 탐색 | Phase B |
| D-05 | 답글 원문 보존이 필요한가? | 저장하지 않음 | Phase C |
| D-06 | 신규 콘텐츠용 Notion DB가 필요한가? | 4주 파일럿까지 파일 저장 | Phase D |

이 결정들이 끝나기 전에도 models, repository, dry-run, validator는 구현할 수 있다. 반대로 X API 권한이 확인되기 전에는 reply/metrics 기능을 가정해 자동화 범위를 약속하지 않는다.
