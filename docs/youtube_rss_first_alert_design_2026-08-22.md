# Alert Pipeline YouTube RSS-First 상세설계

> 작성일: 2026-08-22  
> 범위: `collectors/youtube_collector.py`, `detection/macro_news_layer.py`, Alert Pipeline 운영 설정  
> 결정: `YOUTUBE_API_KEY`는 필수가 아니라 선택적 장애 복구 수단으로 취급한다.

## 1. 결론

공개 채널의 최신 업로드를 수집하는 현재 목적에는 YouTube 채널 Atom RSS만으로 정상 경로를 구성할 수 있다.
RSS URL은 `https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}`이며 API key를 요구하지 않는다.
현재 구현도 RSS를 먼저 파싱하고 HTTP 4xx/5xx 또는 파싱 예외가 발생한 경우에만 YouTube Data API를 호출한다.

따라서 운영 계약은 다음과 같이 정의한다.

- `YOUTUBE_CHANNELS`: 필수. `채널명:채널ID` 목록이다.
- `YOUTUBE_API_KEY`: 선택. RSS 장애 시 가용성을 높이는 보조 수단이다.
- API key가 없어도 RSS가 정상이라면 모든 등록 채널을 순회하고 최신 feed entry를 수집한다.
- 여기서 “전부 수집”은 등록 채널의 RSS가 노출하는 최신 entry 전부를 의미한다. 채널의 전체 영상 이력, 검색 결과,
  비공개·멤버십·삭제 영상까지 수집한다는 의미는 아니다.

## 2. 현재 동작 계약

### 2.1 정상 경로

1. `YOUTUBE_CHANNELS`를 쉼표로 분리한다.
2. 각 채널의 RSS URL을 `feedparser`로 조회한다.
3. HTTP 오류가 없으면 feed entry를 순회한다.
4. 수집 시간 범위 밖 entry, 제목 또는 video ID가 없는 entry를 제거한다.
5. 제목과 RSS summary를 `CollectorEvent`로 변환한다.
6. 제외 패턴을 먼저 적용하고 긴급 키워드 점수 2.0 이상만 남긴다.
7. `keyword_score × channel_weight` 내림차순으로 정렬한다.

이 경로에서는 `YOUTUBE_API_KEY`를 읽기만 하고 외부 API 요청에는 사용하지 않는다.

### 2.2 장애 경로

RSS가 HTTP 4xx/5xx를 반환하거나 파싱 호출이 예외를 발생시키면 Data API `search` endpoint를 최대 10건으로 호출한다.
API key가 없으면 해당 채널의 fallback만 생략하고 다른 등록 채널의 RSS 수집은 계속한다.

### 2.3 보장하지 않는 범위

- RSS feed가 제공하는 개수보다 오래된 영상 이력
- 라이브 방송의 모든 상태 변화와 예약 방송 상태
- Shorts, 일반 영상, 라이브의 엄격한 유형 분류
- RSS가 HTTP 200이지만 비어 있거나 지연된 경우의 완전한 복구
- 제목·summary에 없는 영상 본문, 자막 또는 댓글 분석

## 3. 목표 아키텍처

```text
등록 채널 레지스트리
        |
        v
채널 RSS 병렬/순차 수집 ---- 실패----> 선택적 Data API fallback
        |                                |
        +---------------+----------------+
                        v
              원본 이벤트 정규화
                        |
              freshness / 중복 검사
                        |
             시장 관련성 + 긴급도 필터
                        |
          뉴스 교차확인 + 채널 신뢰도 적용
                        |
       internal candidate / L3 / L2 후보
```

핵심 원칙은 **RSS를 데이터 수집 계층**, 키워드·뉴스 교차확인을 **판단 계층**으로 분리하는 것이다.
RSS 장애와 “유효 Alert 없음”을 동일한 빈 리스트로 취급하지 않는다.

## 4. 효율성 설계

### 4.1 인증 없는 RSS를 기본값으로 유지

- API quota와 secret 관리 비용이 없다.
- 채널별 한 번의 작은 Atom feed 요청이면 충분하다.
- 45분 주기에서는 채널 수가 수십 개여도 Data API 검색보다 단순하고 예측 가능하다.
- API key는 운영상 필요할 때만 추가한다. 미설정을 장애로 경보하지 않는다.

### 4.2 rolling window와 event cursor

UTC 자정 기준 `today_only`는 자정 직후 관측 범위를 거의 0으로 만든다. 목표 상태는 다음과 같다.

- 수집 window: rolling 24시간, 휴장일은 48시간
- overlap: 직전 성공 시각보다 90분 앞에서 재수집
- 기본 멱등 키: YouTube `video_id`
- 저장 상태: 채널별 `last_seen_video_id`, `last_published_at`, `last_success_at`
- 같은 video ID의 재수집은 정상이며 판단 단계 전에 제거한다.

이 방식은 RSS 갱신 지연과 GitHub Actions 지연을 흡수하면서 날짜 경계 누락을 방지한다.

### 4.3 채널별 회로 차단

한 채널 장애가 전체 실행 시간을 소모하지 않도록 채널 상태를 둔다.

| 상태 | 조건 | 동작 |
|---|---|---|
| healthy | 최근 RSS 성공, fresh entry 존재 | 정상 주기 |
| quiet | RSS 성공, 최근 업로드 없음 | 정상 상태로 기록 |
| stale | RSS 성공이나 마지막 entry가 비정상적으로 오래됨 | 경고, 다음 주기 재확인 |
| failed | HTTP/파싱 실패 | API key가 있으면 fallback |
| quarantined | 연속 실패 임계 초과 | 짧은 timeout으로 probe만 수행 |

`quiet`와 `failed`를 구분해야 업로드가 없는 채널 때문에 불필요한 장애 알림이 발생하지 않는다.

### 4.4 제한된 동시성

채널 수가 늘면 4~6개의 제한된 worker로 RSS를 병렬 조회한다. 전체 job timeout 10분보다 짧은 수집 budget을 둔다.

- 채널 요청 timeout: 8~10초
- RSS 재시도: 최대 1회, jitter 포함
- 전체 YouTube 수집 budget: 60초
- 실패 채널만 별도 기록하고 성공 채널 결과는 보존

## 5. Alert 정확도 설계

### 5.1 2단계 필터

단순 키워드 합계만으로 외부 Alert를 만들지 않는다.

1. 시장 관련성 gate
   - 대상: 미국 증시, 지수, 금리, 채권, 달러, 원유, 주요 정책기관 또는 대형 종목
   - 이벤트: 정책 결정, 지표 발표, 거래 중단, 지정학 충격, 실적 충격
2. 긴급도 score
   - 충격 단어, 숫자 변화, 속보 표현, 복수 채널 확인, 뉴스 일치 여부를 합산

`급등`, `급락`, `발표` 같은 일반 단어 하나만으로 통과시키지 않고 대상+이벤트 조합을 요구한다.

### 5.2 제외 규칙

- `정리` 단독 제외를 제거하고 `오늘의 시황 정리`, `주간 정리`처럼 구체화한다.
- `[25년`, `[26년` 하드코딩 대신 날짜형 브리핑 정규식을 사용한다.
- `서킷브레이커`, `거래정지`, `긴급속보` 같은 강한 신호가 있으면 일상 브리핑 제외보다 우선하는 override를 둔다.
- 영문은 `casefold()` 후 비교한다.

### 5.3 채널 신뢰도

수동 가중치는 초기 prior로만 사용하고 90일 지표로 조정한다.

- 뉴스 확인률
- 실제 시장 영향 precision
- 뉴스 대비 lead time
- 과장 긴급어 오탐률
- 삭제·정정률
- 미국 시장/매크로 관련 콘텐츠 비율

공식기관 채널, 전문 해설 채널, 종목·대중 채널을 별도 tier로 관리한다.

### 5.4 외부 발행 정책

- 단일 YouTube 영상: 외부 발행 금지, 내부 candidate만 생성
- 서로 다른 고신뢰 채널 2개 이상이 동일 사건을 확인: 내부 L2 candidate
- 신뢰 뉴스와 주제·시간이 일치: 기존 Macro-News 점수에 confirmation bonus
- 공식기관 채널의 직접 발표: Tier S 뉴스와 유사한 별도 정책 후보이나 초기에는 내부 채널로 제한

현재 YouTube-only L2는 YouTube-only health 최대값과 L2 health 임계값이 충돌하므로 그대로 신뢰하지 않는다.
별도의 `youtube_confirmation_health`를 만들기 전까지 YouTube 단독 외부 발행은 비활성화한다.

## 6. 관측성과 SLO

채널별로 다음 값을 한 실행 단위로 남긴다.

- `rss_http_status`
- `rss_entry_count`
- `fresh_entry_count`
- `accepted_count`
- `excluded_count`
- `below_threshold_count`
- `latest_published_at`
- `feed_lag_seconds`
- `fallback_attempted`, `fallback_success`
- `failure_reason`

초기 SLO는 등록 채널 RSS 성공률 95% 이상, 전체 수집 p95 60초 이하, 날짜 경계 누락 0건으로 둔다.
API key 미설정은 SLO 실패 사유가 아니다.

## 7. 단계별 적용 계획

### Phase 0 — 운영 계약 정정

- 진단에서 `YOUTUBE_API_KEY`를 필수가 아닌 선택 환경변수로 표시한다.
- 운영 문서에 “RSS 정상 시 API 호출 없음”을 명시한다.
- 실제 `YOUTUBE_CHANNELS` 채널 ID를 dry-run에서 검증한다.

### Phase 1 — 누락 방지

- `today_only` 기본값을 rolling 24시간으로 변경한다.
- video ID 기반 run 간 cursor와 멱등 처리를 추가한다.
- HTTP 200 + empty/bozo/stale feed를 명시적으로 분류한다.

### Phase 2 — 정확도 개선

- 시장 관련성 gate와 구체적 제외 규칙을 적용한다.
- 채널 tier와 측정 가능한 신뢰도 지표를 도입한다.
- 뉴스 교차확인에 entity, 사건 종류, 시간 근접성을 반영한다.

### Phase 3 — 선택적 복구

- RSS 장애율이 실제 운영 목표를 위협할 때만 `YOUTUBE_API_KEY`를 설정한다.
- API fallback 사용량과 복구 성공률을 측정한다.
- 필요하면 API 검색보다 uploads playlist 조회 방식의 비용·정확도를 별도 평가한다.

## 8. 수용 기준

1. `YOUTUBE_API_KEY` 없이 정상 RSS fixture의 모든 entry가 수집된다.
2. 한 채널 RSS 실패가 다른 채널의 결과를 제거하지 않는다.
3. API key가 없을 때 fallback 생략은 warning이며 전체 collector 실패가 아니다.
4. API key가 있을 때만 RSS 실패 채널에 API 요청이 발생한다.
5. rolling window와 overlap으로 UTC 자정 전후 영상을 모두 재수집하고 video ID로 중복 제거한다.
6. raw 0건, 전부 필터됨, 채널 quiet, RSS failed를 서로 다른 운영 상태로 구분한다.
7. 단일 비공식 YouTube 영상은 외부 L1/L2를 발생시키지 않는다.

## 9. 후속 구현 우선순위

가장 먼저 Alert workflow가 실제 Macro-News collector 경로를 호출하도록 entrypoint를 통합해야 한다. 그 다음 Phase 1을
구현한다. entrypoint가 레거시 모듈 import에서 중단되는 상태에서는 RSS 정책을 개선해도 운영 Alert에는 반영되지 않는다.

## 10. 최근 영상 요약·비교 설계와의 관계

현재 코드는 RSS의 제목과 최대 500자 description을 `summary` 필드에 복사할 뿐, 최근 영상들을 LLM으로 요약하거나
영상 간 주장을 비교하지 않는다. `top_youtube`도 점수가 높은 이벤트 3개를 보관하는 목록이며 비교 결과가 아니다.

최근 영상 요약·비교는 RSS 수집과 분리된 후처리 계층으로 추가한다. RSS는 후보 발견과 메타데이터 제공을 담당하고,
후처리 계층은 근거 텍스트의 품질을 표시한 뒤 개별 요약, 사건별 군집화, 합의·차이·신규성 비교를 수행한다.
상세 데이터 계약, 비교 알고리즘, 프롬프트, 비용 제어, 실패 정책과 수용 기준은
`docs/youtube_recent_video_summary_comparison_design_2026-08-22.md`를 따른다.
