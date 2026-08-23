# YouTube 최근 영상 요약·비교 상세분석 및 상세설계

> 작성일: 2026-08-22  
> 선행 설계: `youtube_rss_first_alert_design_2026-08-22.md`  
> 상태: Phase A 규칙 기반 MVP 구현 완료, Phase B 이후 구현 전
> 핵심 결정: RSS 수집과 영상 요약·비교를 분리하고, 근거가 부족한 경우 요약을 추측하지 않는다.

## 1. 현행 구현 분석

현행 `YouTubeCollector`가 실제로 수행하는 작업은 다음 범위다.

1. 채널 RSS에서 최신 entry를 수집한다.
2. `title`, RSS의 `summary` 또는 API fallback의 `description` 앞 500자를 저장한다.
3. 제목+description에 제외 패턴과 긴급 키워드 점수를 적용한다.
4. `keyword_score × channel_weight` 순서로 정렬한다.
5. `MacroNewsLayer`가 상위 3개 이벤트를 `top_youtube`에 넣는다.
6. 발행기는 주로 상위 영상의 **제목**을 표시한다.

따라서 현재 `CollectorEvent.summary`는 시스템이 생성한 영상 요약이 아니라 RSS description의 잘린 복사본이다.
또한 뉴스와 YouTube의 공통 키워드가 2개 이상인지 검사할 뿐, 최근 영상끼리 주장·근거·전망을 비교하지 않는다.

### 1.1 구현되어 있지 않은 기능

- 영상별 핵심 주장 요약
- 동일 채널의 직전 영상 대비 입장 변화 탐지
- 서로 다른 채널 간 합의·반대·독자 주장 비교
- 같은 사건을 다루는 영상 군집화
- 영상과 뉴스 사이의 사실·수치·시간 비교
- 자막에 근거한 인용 위치 또는 근거 범위 표시
- 이전 실행 결과와 비교한 신규 정보 탐지

## 2. 목표와 비목표

### 2.1 목표

최근 수집된 영상 중 Alert와 관련성이 높은 후보를 낮은 비용으로 요약하고, 같은 사건에 대한 여러 채널의 공통점과
차이점을 구조화한다. 결과는 사람이 빠르게 검토할 수 있는 내부 브리핑과 Macro-News의 보조 근거로 사용한다.

### 2.2 비목표

- RSS 제목만 보고 영상 전체 내용을 아는 것처럼 요약하지 않는다.
- 단일 크리에이터의 전망을 사실로 판정하지 않는다.
- 요약 결과만으로 L1/L2 외부 Alert를 발행하지 않는다.
- 모든 채널의 모든 영상을 전사하거나 LLM에 전달하지 않는다.
- 저작권 있는 자막 전문을 저장하거나 발행하지 않는다.

## 3. 근거 수준 모델

요약 품질은 입력 근거에 제한된다. 모든 결과에 `evidence_level`을 넣는다.

| 수준 | 사용 가능한 근거 | 허용 결과 | 금지 결과 |
|---|---|---|---|
| `metadata` | 제목, 채널, 발행시각 | 제목 기반 주제 라벨, 후보 분류 | 영상 내용 요약, 주장 단정 |
| `description` | RSS description | 게시자가 명시한 내용 요약 | description 밖의 세부 주장 추측 |
| `transcript_partial` | 일부 자막 | 확보 구간 요약, 구간 한계 표시 | 영상 전체 결론으로 일반화 |
| `transcript_full` | 충분한 자막 | 핵심 주장·근거·전망 요약 | 근거 없는 사실 확인 |

RSS-only 운영에서도 `metadata`와 `description` 비교는 가능하다. 그러나 “영상 내용 요약” 품질을 원한다면 합법적이고
안정적인 자막 확보 경로를 별도 검증해야 한다. 자막을 얻지 못한 영상은 제목 기반 후보 분석으로 명시적으로 강등한다.

## 4. 전체 처리 흐름

```text
YouTube RSS Collector
        |
        v
raw/fresh event + video_id
        |
        v
Candidate Gate
  - 시간 범위
  - 시장 관련성
  - 중복 video_id
  - 채널 tier
        |
        +---- 탈락 ----> 메트릭만 기록
        v
Evidence Resolver
  metadata -> description -> optional transcript
        |
        v
Per-video Structured Summary
        |
        v
Event Clustering
  entity + event type + time + semantic similarity
        |
        v
Cross-video Comparator
  consensus / disagreement / unique / changed stance
        |
        v
News Cross-check + Confidence Gate
        |
        +---- low confidence ----> internal only
        v
Comparison Brief / Macro-News confirmation input
```

## 5. 후보 선택

모든 RSS entry를 LLM으로 보내지 않는다. 먼저 결정론적 gate를 적용한다.

### 5.1 기본 범위

- 기본 rolling window: 최근 24시간
- 휴장일 또는 월요일 보강: 최근 48시간
- 채널당 비교 후보: 최대 3개
- 실행당 전체 후보: 최대 12개
- 동일 video ID: 한 번만 요약하고 저장 결과 재사용

### 5.2 우선순위 점수

```text
candidate_score =
    0.30 * market_relevance
  + 0.25 * urgency_score
  + 0.20 * channel_trust
  + 0.15 * recency
  + 0.10 * news_overlap
```

각 구성요소는 0~1로 정규화한다. 이 점수는 L1/L2 판정 점수가 아니라 요약 비용을 어디에 쓸지 정하는 값이다.

### 5.3 강제 포함

- 공식기관 또는 공식 거래소 채널의 정책 발표
- 신뢰 뉴스와 entity+event type이 일치하는 영상
- 서로 다른 고신뢰 채널 2개 이상이 2시간 안에 같은 사건을 다룬 경우

## 6. 데이터 계약

### 6.1 영상 요약

```python
@dataclass
class VideoSummary:
    video_id: str
    channel_name: str
    published_at: datetime
    title: str
    url: str
    evidence_level: str
    evidence_chars: int
    topic: str
    entities: list[str]
    event_type: str
    factual_claims: list[str]
    opinions_or_forecasts: list[str]
    numeric_claims: list[dict]
    market_direction: str       # bullish|bearish|mixed|neutral|unknown
    time_horizon: str           # intraday|days|weeks|long_term|unknown
    concise_summary: str
    uncertainty_notes: list[str]
    source_fingerprint: str
    model_version: str
```

`factual_claims`와 `opinions_or_forecasts`를 반드시 분리한다. `metadata` 수준에서는 두 필드를 비우고
`concise_summary`도 “제목상 ○○을 다루는 영상” 형식으로 제한한다.

### 6.2 비교 결과

```python
@dataclass
class VideoComparison:
    comparison_id: str
    window_start: datetime
    window_end: datetime
    topic_key: str
    topic_label: str
    video_ids: list[str]
    channels: list[str]
    common_claims: list[str]
    disagreements: list[dict]
    unique_claims: list[dict]
    stance_changes: list[dict]
    news_confirmed_claims: list[str]
    unverified_claims: list[str]
    comparison_summary: str
    confidence: float
    evidence_coverage: float
    publish_scope: str          # internal|confirmation_only|none
```

## 7. 개별 영상 요약

### 7.1 전처리

- HTML, tracking URL, 반복 해시태그와 boilerplate를 제거한다.
- 자막이 있으면 timestamp를 유지한 채 문장 단위로 정리한다.
- 언어를 감지하되 원문 entity, ticker, 숫자와 단위를 보존한다.
- 입력 fingerprint가 기존과 같으면 저장된 요약을 재사용한다.

### 7.2 긴 영상

자막이 모델 입력 한도를 넘으면 map-reduce를 사용한다.

1. 5~8분 또는 토큰 기준 chunk 생성
2. 각 chunk에서 주장·숫자·entity·전망과 timestamp 추출
3. 중복 주장을 병합
4. 전체 구조화 요약 생성
5. 결론과 반대 근거가 함께 존재하면 둘 다 보존

### 7.3 요약 프롬프트 계약

시스템 프롬프트는 다음을 강제한다.

- 입력 텍스트에 없는 사실을 추가하지 않는다.
- 사실 주장과 진행자의 의견·예측을 분리한다.
- 수치에는 대상, 값, 단위, 기준시점이 모두 있을 때만 기록한다.
- 불명확하면 `unknown` 또는 `uncertainty_notes`에 기록한다.
- 투자 권유 문장으로 바꾸지 않는다.
- JSON schema 외 텍스트를 출력하지 않는다.

## 8. 최근 영상 비교

### 8.1 사건 군집화

제목 키워드 교집합만 사용하지 않고 다음 특성을 결합한다.

```text
topic_similarity =
    0.35 * entity_overlap
  + 0.25 * event_type_match
  + 0.20 * semantic_similarity
  + 0.10 * numeric_context_match
  + 0.10 * temporal_proximity
```

- hard gate: 핵심 entity 또는 event type 중 하나는 일치해야 한다.
- 같은 채널의 반복 업로드도 군집에는 포함하되 채널 수는 하나로 센다.
- threshold 이하 영상은 별도 topic으로 유지해 억지 비교를 방지한다.

### 8.2 비교 축

각 군집에서 다음 순서로 비교한다.

1. **공통 사실 주장**: 둘 이상의 독립 채널이 같은 내용을 말하는가
2. **수치 차이**: 값, 단위, 기준시점이 다른가
3. **인과 해석 차이**: 같은 사건의 원인을 다르게 설명하는가
4. **시장 방향 차이**: bullish, bearish, mixed가 갈리는가
5. **시간 범위 차이**: 당일 충격과 장기 전망을 혼동하고 있지 않은가
6. **독자 주장**: 한 채널만 말한 내용은 무엇인가
7. **입장 변화**: 동일 채널의 이전 영상과 결론이 바뀌었는가

`common_claims`는 사실 확정이 아니라 “복수 영상에서 공통으로 제시된 주장”이다. 신뢰 뉴스 또는 공식자료로 확인되기
전에는 `news_confirmed_claims`로 승격하지 않는다.

### 8.3 이전 영상과의 비교

교차 채널 비교와 별도로 동일 채널의 최근 기준 요약과 비교한다.

- 기준: 동일 topic의 최근 7일 내 가장 최신 요약
- 변경 유형: `new`, `strengthened`, `weakened`, `reversed`, `unchanged`
- 변경 근거: 이전/현재 주장 ID와 evidence level
- title만 달라지고 주장이 같으면 입장 변화로 세지 않는다.

### 8.4 뉴스 교차확인

영상 주장과 뉴스는 다음 항목이 맞을 때 확인된 것으로 표시한다.

- 핵심 entity
- 사건 종류
- 수치와 단위(수치 주장인 경우)
- 사건 발생 또는 발표 시각
- 원문 URL이 서로 독립적인가

뉴스와 불일치하면 영상을 삭제하지 않고 `unverified` 또는 `conflicting`으로 표시한다. 공식자료와 명백히 충돌하는 경우
외부 Alert 기여 점수는 0으로 둔다.

## 9. 신뢰도와 발행 정책

### 9.1 비교 신뢰도

```text
confidence =
    0.30 * evidence_coverage
  + 0.25 * independent_channel_score
  + 0.20 * news_confirmation
  + 0.15 * extraction_quality
  + 0.10 * temporal_alignment
```

감점:

- 모든 영상이 `metadata` 수준: 최종 confidence 상한 0.30
- 한 채널의 복수 영상뿐임: independent score 0
- 숫자/단위 충돌 미해결: -0.20
- 자막 일부만 확보: evidence coverage에 반영

### 9.2 결과 사용

| 조건 | 사용 범위 |
|---|---|
| confidence < 0.45 | 저장·운영 메트릭만 |
| 0.45 ≤ confidence < 0.70 | 내부 비교 브리핑 |
| confidence ≥ 0.70 + 뉴스 확인 | Macro-News confirmation 후보 |
| 단일 영상 또는 metadata-only | 외부 Alert 기여 금지 |

비교 요약은 L1 자동 트리거가 아니다. 기존 뉴스 점수를 대체하지 않고 최대 제한된 confirmation bonus만 제공한다.

## 10. 출력 예시

```text
주제: 예상 밖 CPI 발표와 금리 전망
범위: 최근 6시간 / 3개 채널 / 4개 영상
근거: full transcript 2, description 1, metadata 1

공통:
- 3개 채널 모두 CPI가 시장 예상보다 높았다고 설명함.
- 단기 금리 인하 기대가 약해졌다는 해석이 공통적임.

차이:
- 채널 A: 주식시장 조정이 수일 지속될 가능성을 강조.
- 채널 B: 장기 추세 변화보다 당일 포지션 정리 영향으로 해석.

미확인:
- 채널 C의 “다음 회의 인상 가능성” 주장은 확인 뉴스 없음.

이전 대비:
- 채널 A는 전일의 neutral 전망에서 단기 bearish로 변경.

판정:
- confidence 0.78, 내부 비교 브리핑 + 뉴스 confirmation 후보
```

## 11. 저장과 캐시

권장 테이블은 다음과 같다.

### `ia_youtube_video_summary`

- unique: `video_id`, `source_fingerprint`, `model_version`
- 원문 전체 대신 허용된 근거의 hash, 길이, evidence level을 저장한다.
- 구조화 요약과 생성시각을 저장한다.

### `ia_youtube_comparison`

- unique: `topic_key`, `window_end_bucket`, `policy_version`
- 포함 video ID, 비교 구조, confidence, publish scope를 저장한다.

캐시 hit이면 LLM을 다시 호출하지 않는다. description이 수정되거나 더 높은 수준의 자막을 확보하면 fingerprint 또는
evidence level 변화로 재요약한다.

## 12. 비용과 실행시간 제어

- 결정론적 gate 이전에는 LLM을 호출하지 않는다.
- metadata-only 후보는 규칙 기반으로 분류하고 기본적으로 LLM을 호출하지 않는다.
- 요약은 신규 또는 변경된 video ID만 수행한다.
- 비교는 군집 구성 또는 구성원의 요약이 바뀐 경우에만 수행한다.
- 한 실행의 LLM 입력 토큰·호출 수·벽시계 budget을 환경변수로 제한한다.
- budget 초과 시 낮은 candidate score부터 다음 실행으로 이월한다.
- 요약/비교 실패가 RSS 수집과 기존 뉴스 Alert를 중단시키지 않는다.

초기값:

| 항목 | 값 |
|---|---:|
| 실행당 영상 요약 | 최대 8개 |
| 실행당 군집 비교 | 최대 4개 |
| 전체 후처리 budget | 90초 |
| 개별 모델 호출 timeout | 20초 |
| 재시도 | transient 오류 1회 |

## 13. 보안·저작권·감사

- API key, OAuth token 또는 쿠키를 프롬프트·로그에 넣지 않는다.
- 자막 전문을 Alert, 로그, reasoning JSON에 저장하지 않는다.
- 짧은 근거 조각이 필요하면 timestamp와 hash 중심으로 저장하고 발행문은 재서술한다.
- 모든 결과에 video URL, evidence level, model/prompt/policy version을 기록한다.
- 삭제된 영상의 원문 캐시는 보존 정책에 따라 제거하되 감사용 구조화 결과의 처리 기준은 별도로 정한다.

## 14. 장애와 강등 정책

| 장애 | 동작 |
|---|---|
| RSS 실패 | 기존 선택적 API fallback; 요약 단계 미진입 |
| description 없음 | metadata 수준으로 강등 |
| 자막 없음 | description 또는 metadata 수준으로 강등 |
| 모델 timeout/schema 오류 | 해당 영상 `summary_failed`; 다음 후보 계속 |
| 일부 영상 요약 실패 | 성공 영상만 비교하되 coverage 하락 |
| 비교 모델 실패 | 개별 요약 저장, Alert bonus 없음 |
| 저장소 실패 | 발행 기여 금지, 기존 뉴스 파이프라인 계속 |

## 15. 관측성

추가 메트릭:

- `summary_candidates_total`
- `summary_generated_total`, `summary_cache_hit_total`, `summary_failed_total`
- `evidence_level_count`
- `comparison_clusters_total`, `comparison_generated_total`, `comparison_failed_total`
- `comparison_confidence_histogram`
- `news_confirmed_claims_total`, `unverified_claims_total`
- `llm_input_tokens`, `llm_output_tokens`, `llm_wall_seconds`
- `budget_deferred_total`

운영 로그에는 원문 자막이 아니라 ID, 개수, 상태, latency와 실패 사유만 남긴다.

## 16. 구현 단계

### Phase A — metadata/description 비교 MVP

1. `VideoSummary`, `VideoComparison` domain model 추가
2. video ID 캐시와 fingerprint 추가
3. candidate gate와 규칙 기반 entity/event 추출
4. description 근거의 구조화 요약
5. 같은 사건의 최근 영상 비교
6. internal-only 브리핑 출력

API key나 자막 없이 구현 가능하다. 다만 출력에 evidence 수준을 명확히 표시한다.

현재 `detection/youtube_video_analysis.py`에 규칙 기반 MVP가 반영되었다. 최근 24시간 후보 선택, video ID 중복 제거,
metadata/description 근거 구분, 사실/전망 문장 분리, 사건 유형 군집화, 독립 채널 비교와 internal-only confidence 제한을
구현했다. LLM 요약, 영속 캐시, 입장 변화, 숫자 충돌 판정과 뉴스 교차확인은 후속 Phase에서 구현한다.

### Phase B — 선택적 자막 근거

1. 허용 가능한 자막 획득 방식을 별도 기술 검증
2. transcript resolver를 interface로 격리
3. partial/full coverage 계산
4. timestamp 보존 chunk 요약
5. description 결과와 품질 비교

### Phase C — 뉴스 교차확인

1. entity/event/time/numeric claim matcher
2. verified/unverified/conflicting 분류
3. 제한된 confirmation bonus
4. reasoning JSON에 비교 근거 추가

### Phase D — 운영 최적화

1. 채널별 요약 precision과 비용 측정
2. candidate threshold 튜닝
3. 입장 변화 탐지
4. SLO와 budget 자동 조절

## 17. 테스트 전략과 수용 기준

### 단위 테스트

1. RSS description이 시스템 생성 요약으로 오인되지 않는다.
2. metadata-only 결과에는 factual claim이 생성되지 않는다.
3. 사실과 의견·전망이 분리된다.
4. 동일 video ID와 fingerprint는 cache hit가 된다.
5. entity 또는 event type이 전혀 다른 영상은 같은 군집에 들어가지 않는다.
6. 같은 채널의 여러 영상은 독립 채널 확인으로 세지 않는다.
7. 수치가 같아도 단위·기준시점이 다르면 공통 주장으로 병합하지 않는다.
8. partial transcript는 full coverage로 표시되지 않는다.
9. metadata-only 비교 confidence는 0.30을 넘지 않는다.

### 통합 테스트

1. 3개 채널 6개 영상 중 같은 사건 4개가 하나의 군집으로 묶인다.
2. 공통 주장, 반대 주장, 독자 주장, 입장 변화가 예상 schema로 출력된다.
3. 뉴스 확인이 없으면 external publish scope가 생성되지 않는다.
4. 한 영상의 모델 실패가 다른 요약과 RSS 수집을 중단시키지 않는다.
5. budget 초과 후보는 손실되지 않고 다음 실행 대상으로 남는다.
6. 자막 원문과 secret이 로그·DB·발행문에 포함되지 않는다.

### 운영 승인 기준

- 최소 2주 shadow mode
- 사람이 라벨링한 사건 군집 precision 90% 이상
- 핵심 주장 요약 precision 90% 이상
- 사실/의견 오분류 5% 이하
- 외부 발행 자동화 전 미확인 주장의 외부 노출 0건
- p95 후처리 90초 이하, RSS 수집 성공률에 영향 없음

## 18. 최종 권고

첫 구현은 **API key와 자막 없이 metadata/description 기반 내부 비교 MVP**로 제한한다. 이 단계에서도 여러 채널이 같은
사건을 다루는지, 제목·description에서 어떤 차이가 있는지, 신규 영상이 직전 입장에서 달라졌는지를 비교할 수 있다.
단, 이를 영상 전체 요약이라고 부르지 않고 근거 수준을 함께 표시해야 한다.

영상 내용 자체의 신뢰도 높은 요약은 자막 근거가 확보된 Phase B에서 활성화한다. 이후 뉴스 교차확인까지 통과한 결과만
Macro-News confirmation 후보로 사용하며, YouTube 요약·비교 결과는 독립적인 외부 Alert 트리거로 사용하지 않는다.
