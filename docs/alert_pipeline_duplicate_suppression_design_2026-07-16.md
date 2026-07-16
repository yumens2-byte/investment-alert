# Alert Pipeline 중복 발행 억제 고도화 상세설계

> 작성일: 2026-07-16  
> 대상: `run_alert.py` 기반 Macro-News Alert Pipeline  
> 핵심 문제: 비슷한 시점에 동일/유사 내용이 X에 반복 발행되어 사용자 피로도와 플랫폼 안티봇 리스크가 증가한다.

---

## 1. 현황 요약

현재 파이프라인은 `MacroNewsLayer.detect()`가 수집 결과를 판정하고, `AlertEngine.process()`가 `AlertSignal`을 만든 뒤 `run_alert.py`가 X/TG 채널에 발행한다. `L1`만 X 발행 대상이며, `L2/L3/SYSTEM_DEGRADED`는 X 발행 대상이 아니다.

이미 다음 방어 장치가 존재한다.

1. **레벨 쿨다운**  
   `ia_cooldown_state` 기반으로 `L1=60분`, `L2=90분`, `L3=120분` 등 레벨 단위 발행을 제한한다.
2. **topic_hash 쿨다운 코드 일부**  
   `AlertEngine`에 matched keyword 기반 topic hash 산출 및 `ia_topic_cooldown` 저장/조회 로직이 존재한다.
3. **X 템플릿 강제 fallback**  
   `run_alert.py`에서 최근 top news 제목과 현재 top news 제목의 정규화 hash가 같으면 Gemini 문구 대신 템플릿을 강제하여 본문 유사성을 낮춘다.
4. **채널 간 jitter**  
   채널별 발행 사이에 2~5초 랜덤 지연을 둔다.

그러나 위 장치만으로는 “비슷한 시점의 동일 내용 X 반복”을 안정적으로 막기 어렵다. 특히 현재 topic hash가 matched keyword에 강하게 의존하고, X 발행 직전의 최종 텍스트 중복 검증이 독립된 정책으로 존재하지 않는다.

---

## 2. 문제 정의

### 2.1 사용자 관점 문제

- 같은 시장 이슈가 짧은 시간 내 여러 번 감지되면 X 타임라인에 거의 같은 메시지가 반복 노출된다.
- 사용자는 신규 정보가 아닌 재알림으로 인식하여 알림 신뢰도가 낮아진다.
- 반복 발행이 많아질수록 고위험 시그널의 희소성이 떨어진다.

### 2.2 운영/플랫폼 관점 문제

- X에 유사 본문이 짧은 간격으로 반복되면 자동화/스팸성 패턴으로 보일 수 있다.
- Gemini가 같은 input에 대해 유사 output을 만들 경우, 문구 셔플만으로는 중복성이 충분히 낮아지지 않는다.
- 레벨 쿨다운은 `L1` 전체를 막는 단순 장치라서, 다른 이슈까지 함께 막는 부작용이 있다.

### 2.3 기술적 원인

| 구분 | 원인 | 상세 |
| --- | --- | --- |
| 중복 판단 기준 부족 | keyword hash 중심 | 제목/URL/본문 의미 유사도 반영이 약함 |
| X 전용 가드 부재 | publish 직전 최종 본문 검사 없음 | 최종 `x_msg`가 이전 발행과 비슷해도 차단하지 못함 |
| 저장 모델 부족 | topic state와 publish history 분리 미흡 | 동일 주제의 관측/발행/요약 상태를 일관되게 추적하기 어려움 |
| 정책 분기 부족 | suppress/update/escalate 구분 약함 | 반복 감지 시 무조건 차단 또는 무조건 신규 발행으로 흐르기 쉬움 |

---

## 3. 고도화 목표

### 3.1 목표

1. **동일/유사 주제의 X 반복 발행 차단**  
   짧은 시간 내 동일 이슈는 신규 X 발행 대신 억제한다.
2. **새 정보가 추가된 경우만 업데이트 발행**  
   단순 반복은 suppress, 점수/레벨/새 근거가 의미 있게 바뀐 경우 update로 발행한다.
3. **레벨 쿨다운보다 정교한 topic 단위 제어**  
   전체 L1을 막지 않고 같은 topic만 억제한다.
4. **감사 가능성 확보**  
   suppress/update/escalate 판단 근거를 DB와 로그에 남긴다.
5. **장애 시 발행 우선 fail-open 유지**  
   중복 가드 장애가 전체 alert 발행 장애로 전이되지 않게 한다.

### 3.2 비목표

- L1 탐지 알고리즘 자체의 위험 점수 산식을 전면 재설계하지 않는다.
- 모든 언어/모든 뉴스 본문에 대해 완전한 의미 유사도 모델을 도입하지 않는다.
- X 외 Telegram 채널의 반복 정책은 1차 범위에서 제외하고, X 안정화 후 확장한다.

---

## 4. TO-BE 개요

중복 억제 기능을 `DuplicateGuard`로 분리하고, Alert 발행 전 단계에 명시적으로 삽입한다.

```text
Collectors
  -> MacroNewsLayer.detect()
  -> AlertEngine.process()
  -> DuplicateGuard.evaluate(signal, result, channel="x")
  -> AlertFormatter.format_x()
  -> XPublisher.publish()
  -> AlertStore.update_publish_result()
```

핵심은 **topic identity**와 **publish decision**을 분리하는 것이다.

- `topic_key`: 같은 사건/주제인지 판단하는 안정 키
- `content_fingerprint`: 실제 발행 본문이 얼마나 유사한지 판단하는 키
- `decision`: `publish_new`, `suppress_duplicate`, `publish_update`, `publish_escalation`

---

## 5. 상세설계

### 5.1 컴포넌트 설계

#### 5.1.1 `detection/duplicate_guard.py` 신설

책임:

- AlertSignal과 MacroNewsResult를 입력받아 X 발행 여부를 판단한다.
- topic key, content fingerprint를 산출한다.
- DB의 최근 topic/publish state를 조회한다.
- 정책에 따라 `DuplicateDecision`을 반환한다.

주요 인터페이스:

```python
@dataclass
class DuplicateDecision:
    action: Literal[
        "publish_new",
        "suppress_duplicate",
        "publish_update",
        "publish_escalation",
        "guard_unavailable",
    ]
    topic_key: str | None
    content_fingerprint: str | None
    reason: str
    suppress_x: bool
    update_suffix: str | None = None
    similarity_score: float | None = None
    previous_alert_id: str | None = None
```

#### 5.1.2 `db/alert_store.py` 확장

책임:

- topic state 저장/조회
- X publish fingerprint 저장/조회
- duplicate decision 감사 로그 저장

추가 메서드:

```python
def get_topic_state(topic_key: str) -> dict | None: ...
def upsert_topic_state(...): ...
def get_recent_x_fingerprints(window_minutes: int, limit: int) -> list[dict]: ...
def save_duplicate_decision(...): ...
```

#### 5.1.3 `run_alert.py` 통합

X 발행 전 다음 순서로 처리한다.

1. `DuplicateGuard.evaluate(...)` 호출
2. `suppress_x=True`이면 `signal.publish_x=False`로 변경
3. `publish_update`이면 formatter에 `update_suffix` 또는 update context 전달
4. 발행 결과와 duplicate decision을 함께 저장

---

### 5.2 데이터 모델 설계

#### 5.2.1 `ia_topic_state`

동일 주제의 관측/발행 상태를 추적한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `topic_key` | text primary key | 정규화된 주제 키 |
| `canonical_title` | text | 대표 제목 |
| `keywords` | jsonb | 핵심 키워드 |
| `source_urls` | jsonb | 근거 URL 목록 |
| `first_seen_at` | timestamptz | 최초 감지 시각 |
| `last_seen_at` | timestamptz | 최근 감지 시각 |
| `last_alert_id` | text | 최근 연결 alert_id |
| `last_x_published_at` | timestamptz | 최근 X 발행 시각 |
| `last_level` | text | 최근 레벨 |
| `last_score` | numeric | 최근 점수 |
| `seen_count` | integer | 감지 누적 횟수 |
| `update_count` | integer | 업데이트 발행 횟수 |
| `created_at` | timestamptz | 생성 시각 |
| `updated_at` | timestamptz | 수정 시각 |

#### 5.2.2 `ia_x_publish_fingerprint`

X 본문 기준 중복을 추적한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `id` | bigserial primary key | 내부 ID |
| `alert_id` | text | alert id |
| `topic_key` | text | topic state 키 |
| `content_fingerprint` | text | 정규화 본문 fingerprint |
| `normalized_text` | text | 비교용 정규화 텍스트 |
| `tweet_id` | text | X 발행 ID |
| `published_at` | timestamptz | 발행 시각 |

#### 5.2.3 `ia_duplicate_decision_log`

차단/업데이트 판단 근거를 감사한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `id` | bigserial primary key | 내부 ID |
| `alert_id` | text | alert id |
| `channel` | text | 1차는 `x` |
| `topic_key` | text | topic key |
| `action` | text | publish/suppress/update/escalation |
| `reason` | text | 판단 사유 |
| `similarity_score` | numeric | 유사도 |
| `previous_alert_id` | text | 비교 대상 alert |
| `created_at` | timestamptz | 생성 시각 |

---

### 5.3 Topic Key 산출 설계

topic key는 다음 요소를 우선순위로 조합한다.

1. URL canonical host + slug 핵심 토큰
2. 상위 뉴스 제목 정규화 token
3. matched keyword set
4. 시장 이벤트 카테고리

정규화 규칙:

- 소문자화
- 숫자/특수문자 제거 또는 공백 치환
- stopword 제거
- ticker, 국가명, 기관명 등 핵심 entity 보존
- token 정렬 후 상위 N개만 사용

예시:

```text
원문: Fed rate-cut hopes fade as CPI comes in hot
정규화 token: [cpi, fed, hot, ratecut]
topic_key seed: cpi|fed|hot|ratecut
hash: sha256(seed)[:16]
```

기존 matched keyword 기반 `_compute_topic_hash()`는 fallback으로 유지하되, 1차 key는 `DuplicateGuard`가 생성한다.

---

### 5.4 중복 판단 정책

#### 5.4.1 기본 window

| 항목 | 기본값 | 설명 |
| --- | ---: | --- |
| topic suppress window | 180분 | 같은 topic 신규 X 발행 억제 |
| content fingerprint window | 24시간 | 매우 유사한 본문 재발행 방지 |
| update minimum interval | 45분 | 업데이트 발행 최소 간격 |
| score delta threshold | +0.15 | 점수 상승 시 update 후보 |
| level escalation | L2→L1 또는 L3→L1 | escalation 발행 허용 |

#### 5.4.2 action 결정표

| 조건 | action | X 발행 |
| --- | --- | --- |
| topic 없음 + 본문 유사 없음 | `publish_new` | 허용 |
| 같은 topic이 suppress window 안에 있고 점수/근거 변화 없음 | `suppress_duplicate` | 차단 |
| 같은 topic이지만 새 source URL 또는 핵심 근거가 추가됨 | `publish_update` | 허용 |
| 같은 topic이지만 level이 상향됨 | `publish_escalation` | 허용 |
| 최종 X 본문 fingerprint가 최근 발행과 매우 유사 | `suppress_duplicate` | 차단 |
| DB/guard 장애 | `guard_unavailable` | fail-open 허용, warning 기록 |

---

### 5.5 X 메시지 생성 정책

중복 guard는 가능하면 `format_x()` 이전에 판단하지만, 본문 fingerprint는 최종 텍스트가 있어야 정확하다. 따라서 2단계 검사를 적용한다.

1. **Pre-format guard**  
   topic state 기준으로 X 발행 필요 여부를 1차 판단한다.
2. **Post-format guard**  
   `x_msg` 생성 후 normalized text fingerprint를 최근 발행 이력과 비교한다.

Post-format에서 중복이 발견되면 발행 직전 차단한다.

업데이트 발행 시 formatter는 다음 방향으로 본문을 구성한다.

- “업데이트”임을 명시
- 기존 내용 반복 대신 새 근거/변화량 중심
- 같은 해시태그 반복 최소화
- 문장 구조를 신규 발행과 다르게 구성

---

### 5.6 운영 설정값

환경변수로 정책을 조정할 수 있게 한다.

| 환경변수 | 기본값 | 설명 |
| --- | --- | --- |
| `DUP_GUARD_ENABLED` | `true` | 중복 가드 활성화 |
| `DUP_TOPIC_WINDOW_MINUTES` | `180` | topic suppress window |
| `DUP_CONTENT_WINDOW_MINUTES` | `1440` | content fingerprint window |
| `DUP_UPDATE_MIN_INTERVAL_MINUTES` | `45` | 업데이트 발행 최소 간격 |
| `DUP_SCORE_DELTA_THRESHOLD` | `0.15` | update 판단 점수 상승폭 |
| `DUP_FAIL_OPEN` | `true` | guard 장애 시 발행 허용 |

---

## 6. 적용 단계

### Phase 1: 관측 모드

- `DuplicateGuard`와 DB 테이블을 추가한다.
- 실제 X 차단은 하지 않고 decision만 로그/DB에 저장한다.
- 3~7일 동안 false positive/false negative를 점검한다.

### Phase 2: X suppress 활성화

- `suppress_duplicate`에 한해 X 발행을 차단한다.
- TG Internal에는 “X 중복 억제됨” 운영 메시지를 남긴다.
- 업데이트/상향 발행은 계속 허용한다.

### Phase 3: 업데이트 발행 최적화

- `publish_update` 전용 formatter context를 추가한다.
- 기존 내용 반복 대신 신규 source, score 변화, level 변화만 압축 발행한다.

### Phase 4: Telegram 확장 검토

- X 안정화 후 TG Free/Paid에도 topic suppress 또는 digest 정책을 적용할지 판단한다.

---

## 7. 테스트 설계

### 7.1 단위 테스트

- topic key 정규화 테스트
- 같은 제목/다른 source URL의 동일 topic 판정
- 다른 제목/같은 keyword의 과잉 중복 방지
- level escalation 허용
- score delta update 허용
- guard DB 장애 시 fail-open 확인

### 7.2 통합 테스트

- 첫 L1은 X 발행 허용
- 30분 내 동일 topic 재감지는 X 차단
- 30분 내 동일 topic이지만 L2→L1 상향이면 X 허용
- 동일 topic에 새 URL 2개 이상 추가 시 update 허용
- 최종 X 본문 fingerprint 충돌 시 발행 직전 차단

### 7.3 운영 검증 지표

| 지표 | 목표 |
| --- | --- |
| X duplicate suppress count | 추적 지표로 신규 수집 |
| suppress false positive | 주간 리뷰에서 5% 이하 |
| X 발행 건수 대비 suppress 비율 | 초기 10~40% 예상 |
| guard failure count | 0 또는 즉시 알림 |
| duplicate decision 저장 성공률 | 99% 이상 |

---

## 8. 리스크와 대응

| 리스크 | 영향 | 대응 |
| --- | --- | --- |
| 과도한 suppress | 중요한 업데이트 누락 | 관측 모드 후 threshold 조정, escalation 항상 허용 |
| DB 장애 | 발행 차단 또는 오류 | `DUP_FAIL_OPEN=true` 기본값 유지 |
| topic key 충돌 | 다른 이슈가 같은 topic으로 묶임 | URL/entity/token 복합 key 사용 |
| Gemini 유사 문구 반복 | X 안티봇 리스크 | post-format fingerprint 검사 추가 |
| 마이그레이션 미적용 | 런타임 오류 | 기능 flag로 비활성 가능, 저장 실패 시 warning 후 계속 |

---

## 9. 최종 권장안

1. 기존 레벨 쿨다운은 유지한다.
2. X 반복 문제는 레벨 쿨다운이 아니라 **topic 단위 DuplicateGuard**로 해결한다.
3. 1차 릴리스는 관측 모드로 배포하여 decision 품질을 확인한다.
4. 이후 `suppress_duplicate`만 실제 차단하고, `publish_update`/`publish_escalation`은 허용한다.
5. 최종 X 본문 fingerprint 검사를 반드시 추가하여 Gemini 유사 출력까지 방어한다.

이 방식은 “같은 내용 반복”은 막으면서도, 실제로 시장 위험도가 커졌거나 새로운 근거가 추가된 경우에는 업데이트 발행을 허용한다. 따라서 알림 신뢰도와 플랫폼 안정성을 동시에 높일 수 있다.
