# 미국 증시 세계관 YouTube Shorts 완전 자동화 요구사항 정의서

> **문서 상태**: Approved v0.2 — 사용자 결정 반영, 상세설계 착수<br>
> **작성일**: 2026-07-24<br>
> **대상 저장소**: `investment-alert`<br>
> **목표**: 미국 시장 이슈를 소재로 한 30초 내외 세로형 숏폼을 하루 2회 자동 기획·제작·검증·즉시 공개한다.

---

## 1. Executive summary

기존 미국 증시 Alert 파이프라인의 뉴스·섹터 데이터를 재사용해, **“하나의 시장 사건이 다른 선택을 했다면?”**이라는 대체 현실 형식의 오리지널 금융 히어로 앤솔로지를 만든다. Claude는 사실 정리·대본·검수, Gemini는 콘티·이미지/영상 소재 생성을 우선 담당하고, 결정론적 Python 검증기가 사실성·길이·IP·투자 권유·중복 여부를 최종 통제한다.

“100% 자동화”는 사람이 매 영상 승인하지 않는 **hands-off 정상 운영**을 뜻한다. 다만 정책·저작권·사실성 검증에 실패한 결과를 억지로 게시하지 않는다. 실패 건은 자동 재생성 후에도 기준을 충족하지 못하면 `SKIPPED` 또는 `QUARANTINED`로 전환하고 운영 채널에 알린다. 즉, **무조건 2개 게시**보다 **안전하게 최대 2개 게시**가 우선이다.

### 핵심 결정 제안

1. 1차 릴리스는 28~35초가 아니라 **27~32초**, 9:16, 1080×1920, 30fps로 고정한다.
2. Marvel, What If, 기존 캐릭터·로고·대사·의상은 사용하지 않는다. 외부 표현도 “Marvel풍”이 아니라 **오리지널 대체 현실 금융 히어로 앤솔로지**로 정의한다.
3. 실존 CEO·정치인을 히어로/빌런으로 묘사하지 않는다. 기업 표시는 승인된 공식 회사 마크 자산만 사실 보도·식별 목적으로 제한해 사용하고, Gemini가 회사 마크를 생성·변형하지 않게 한다.
4. 하루 2회는 미국 동부 현지시각 오전 08:00과 오후 22:00에 운영한다. 설정 명칭은 EDT 요청을 따르되 구현은 `America/New_York`를 사용해 겨울철 EST까지 자동 처리한다.
5. YouTube 업로드에는 단순 API key가 아니라 **OAuth 2.0 Client + refresh token**이 필요하다. 기존 `YOUTUBE_API_KEY`는 조회용으로 계속 분리한다.
6. MVP는 정지 이미지 기반 motion comic + TTS + 자막으로 시작한다. 생성형 text-to-video는 품질·비용이 안정된 뒤 선택적으로 교체한다.

---

## 2. 배경과 문제 정의

### 2.1 현재 자산

저장소에는 다음 재사용 가능 자산이 이미 있다.

- 미국 뉴스 및 YouTube 수집기
- 시장 이벤트 점수화, 데이터 품질, 중복 억제 로직
- Gemini 이미지 생성 경로와 `GEMINI_API_KEY`
- Claude 텍스트 생성 경로와 `ANTHROPIC_API_KEY`
- Supabase 저장소, Telegram 운영 알림, GitHub Actions 스케줄링
- 친절한 톤, 이미지, 페르소나 가이드

새 파이프라인은 기존 Alert의 발행 경로를 변경하지 않고, 검증된 **읽기 전용 입력**으로 활용해야 한다.

### 2.2 해결할 문제

- 매일 소재 발굴, 대본, 이미지, 음성, 자막, 편집, 업로드를 수작업으로 수행하기 어렵다.
- 시장 콘텐츠는 시의성이 높지만 환각, 수치 오류, 투자 권유, 저작권 위험이 크다.
- 하루 2회 자동 게시 시 중복 소재, 생성 실패, API 비용 폭주, 잘못된 업로드의 확산 위험이 있다.
- “유명 프랜차이즈풍” 요구를 그대로 구현하면 캐릭터·트레이드 드레스·상표 관련 위험이 생긴다.

---

## 3. 목표와 비목표

### 3.1 목표

| ID | 목표 | 성공 기준 |
|---|---|---|
| G-01 | 일 2회 무인 실행 | 월간 예약 슬롯의 98% 이상 파이프라인 실행 |
| G-02 | 자동 제작 | 정상 케이스에서 사람 개입 없이 MP4와 메타데이터 생성 |
| G-03 | 안전한 자동 게시 | 모든 게시물이 필수 검증 게이트 통과 |
| G-04 | 시장 적시성 | 기준 이벤트 발생 후 목표 슬롯까지 반영 |
| G-05 | 일관된 브랜드 | 동일 캐릭터·색·내레이션·구조 유지 |
| G-06 | 관측 가능성 | 모든 단계의 입력, 모델, 비용, 결과, 실패 이유 추적 |
| G-07 | 실험 가능성 | 훅·길이·업로드 시간 A/B 테스트와 성과 피드백 지원 |

### 3.2 비목표

- 개별 종목 매수·매도 신호 제공
- 수익률 보장 또는 개인화 투자 자문
- Marvel/Disney 캐릭터, 명칭, 로고, 음악, 영상 클립의 사용
- 뉴스 기사·방송 화면의 무단 재업로드
- 실시간 장중 초단타 알림 대체
- 1차 버전에서 완전한 립싱크 3D 애니메이션 구현
- YouTube 수익화 승인 보장

---

## 4. 대상 시청자와 콘텐츠 포지셔닝

### 4.1 1차 타깃

- 한국어를 사용하는 20~45세 미국 주식 관심자
- 금융 뉴스를 길게 읽기보다 30초 안에 분위기와 원인을 알고 싶은 사람
- 출근 전, 점심, 퇴근 후 모바일로 Shorts를 소비하는 사람

### 4.2 가치 제안

> “오늘 미국 시장의 핵심 갈등을 30초짜리 오리지널 대체 현실 이야기로 이해한다.”

### 4.3 편집 원칙

- **사실과 상상 분리**: 실제 데이터는 명시하고, 대체 현실 장면은 “만약”으로 구분한다.
- **정보 우선**: 이야기 장치가 사실을 압도하지 않는다.
- **한 영상 한 메시지**: 이벤트 1개, 핵심 수치 최대 2개, 결론 1개.
- **불안 조장 금지**: 폭락·파산·전쟁을 선정적으로 단정하지 않는다.
- **행동 권유 대신 관찰 포인트**: “사라/팔라”가 아니라 다음 지표를 제시한다.

---

## 5. IP·브랜드·정책 가드레일

### 5.1 오리지널 세계관 원칙

사용자 아이디어의 매력은 “슈퍼히어로 코믹 + 대체 현실” 문법에 있다. 이를 다음처럼 안전하게 재정의한다.

**허용**

- 분기된 시간선, 내레이터, 선택의 결과를 보여주는 앤솔로지 구조
- 역동적 패널 전환, halftone 질감, 속도선, 영화적 조명 등 일반적 코믹 문법
- 완전히 새로 만든 캐릭터명·실루엣·색상·능력·도시
- 금융 개념의 의인화: 유동성, 변동성, 금리, 심리, 반도체 사이클

**금지**

- Marvel, What If, Avengers 및 소속 캐릭터·대사·로고·고유 소품 언급
- 특정 유명 캐릭터를 연상시키는 의상, 방패, 망치, 헬멧, 색 조합, 포즈
- 프롬프트에 “in the style of Marvel/Disney/[특정 작가]” 사용
- 영화 스틸, 만화 컷, 방송 클립, 저작권 음악의 입력 또는 출력 사용
- 실제 기업 로고를 캐릭터 가슴 문양으로 사용

**회사 마크 사용 결정**

- 회사 마크는 뉴스 대상 식별을 위한 편집적 용도로만 허용한다.
- AI가 로고를 새로 그리거나 변형하지 않고, 회사 공식 미디어/브랜드 페이지에서 받은 원본만 `asset_registry` 승인 후 합성한다.
- 로고는 장면의 10% 이내 보조 요소로 사용하며 캐릭터, 채널 브랜드, 썸네일의 주인공으로 사용하지 않는다.
- 사용 조건을 확인할 수 없거나 변형 금지·영상 사용 금지 조건이 있으면 티커 텍스트와 일반 회사명으로 대체한다.
- 회사와 채널의 제휴·승인을 암시하는 배치, 색상 변경, 캐릭터 의상 문양화는 금지한다.

### 5.2 자체 브랜드 초안

- **작업명**: `Market Multiverse` / `월스트리트 평행우주`
- **포맷명**: `오늘의 분기점`
- **내레이터**: 비인간적 관찰자 `Ticker`(상표 검색 후 확정)
- **반복 캐릭터 후보**: `Pulse`(시장 심리), `Yield`(금리), `Flux`(변동성)
- **비주얼**: 딥 네이비, 앰버, 시안 중심. 기존 유명 히어로의 대표 팔레트 조합 회피.

캐릭터명과 채널명은 출시 전 상표·채널 중복 검색 및 법률 검토가 필요하다.

### 5.3 금융·플랫폼 안전

- 영상/설명란에 “정보 제공 목적이며 투자 조언이 아닙니다” 고지
- 수익 보장, 긴급 매수, 공포 유도, 내부정보 암시 금지
- 가격·지수·금리 데이터에는 기준 시각과 출처를 메타데이터에 보존
- AI 합성 콘텐츠 표시는 YouTube의 최신 공개 필드/정책에 맞춰 설정
- 아동용이 아닌 일반 금융 교육 콘텐츠로 분류하되 실제 채널 설정은 운영자가 최종 확인
- 자동 생성의 반복성이 높아지지 않도록 소재·대본·비주얼의 실질적 변주와 교육적 가치를 유지

---

## 6. 콘텐츠 포맷 요구사항

### 6.1 30초 타임라인

| 구간 | 길이 | 목적 | 예시 구조 |
|---|---:|---|---|
| Hook | 0~3초 | 즉시 관심 확보 | “금리가 한 번 더 올랐다면?” |
| Fact | 3~9초 | 실제 시장 사실 | 지수/수익률/이벤트 1~2개 |
| Branch | 9~21초 | 대체 현실 전개 | 한 선택이 섹터에 미친 영향 |
| Insight | 21~27초 | 현실 복귀·해석 | 오늘 관찰할 연결고리 |
| Close | 27~30초 | 고지·브랜드 | 다음 분기점 예고 + 짧은 고지 |

### 6.2 영상 사양

- 컨테이너/코덱: MP4, H.264 video, AAC audio
- 캔버스: 1080×1920, 9:16, 30fps
- 목표 길이: 27~32초, hard fail 범위 25~35초
- 오디오: 내레이션이 항상 BGM보다 명확해야 하며 최종 loudness 기준은 파일럿에서 확정
- 자막: 한국어 burned-in 자막 + 선택적으로 SRT 업로드
- 안전 영역: 상·하단 YouTube UI와 우측 액션 영역을 피하도록 템플릿화
- 장면: 5~7개, 장면당 2.5~5초, 미세 줌·팬·패럴랙스 적용
- 첫 프레임: 별도 썸네일이 없어도 주제 인지가 가능한 텍스트 8~14자

### 6.3 언어 및 음성

- MVP 주언어: 한국어
- 미국 티커·기관명은 통용 표기 사용, 어려운 약어는 첫 등장 시 풀어쓴다.
- TTS 화자는 1명으로 고정하고 속도·톤·발음 사전을 버전 관리한다.
- 기본 내레이션 분량은 한국어 115~150음절 상당으로 시작하고, 실제 TTS duration을 기준으로 자동 축약한다.
- 음성 복제는 명시적 권리 확보 없이 사용하지 않는다.

### 6.4 메타데이터

- 제목: 핵심 질문 + 시장 키워드, 낚시성 단정 금지
- 설명: 2~4줄 요약, 데이터 출처, 기준 시각, 면책, AI 생성 고지
- 해시태그: 3~5개 이내 (`#미국주식`, `#미국증시`, `#Shorts` 등)
- 태그/카테고리/언어/공개 상태를 설정 파일로 관리
- 영상별 `content_id`, `source_event_ids`, `policy_version`, `prompt_version` 저장

---

## 7. 소재 선정 정책

### 7.1 입력 후보

- 기존 Alert의 L1/L2 이벤트와 `reasoning_json`
- Sector Flow의 방어주/경기민감주 회전 신호
- Fed 등 1차 출처 발표
- 가격 데이터: S&P 500, Nasdaq, VIX, 미 국채 수익률, 달러, 주요 섹터 ETF
- 실적 시즌의 확정 발표 데이터

### 7.2 후보 점수

`topic_score = freshness 30 + market_impact 25 + explainability 15 + visuality 15 + novelty 10 + source_quality 5`

다음 페널티를 적용한다.

- 7일 내 동일 원인/티커: -20~-50
- 출처 1개뿐인 속보: 보류
- 정치적 주장, 루머, 미확인 M&A: 기본 제외
- 숫자 기준 시각이 불명확: 제외
- 비극·재난을 오락화할 위험: 제외

### 7.3 슬롯 전략

| 슬롯 | ET 기준 제안 | 역할 | KST 참고 |
|---|---|---|---|
| A | 08:00 | 개장 전: overnight와 오늘의 관찰점 | DST에 따라 변동 |
| B | 22:00 | 마감 후: 오늘의 분기점과 다음 거래일 관찰점 | DST에 따라 변동 |

스케줄은 고정 UTC cron 세 개로만 처리하지 않는다. 15분 단위 dispatcher가 `America/New_York` 현지 시각을 계산해 해당 슬롯을 한 번만 claim해야 한다. 주말과 미국 시장 휴장일에도 동일한 2개 슬롯을 유지하되 주간 회고, 경제사, 다음 거래일 캘린더 같은 evergreen 포맷으로 대체한다.

---

## 8. 기능 요구사항

### 8.1 오케스트레이션

| ID | 요구사항 | 우선순위 | 인수 기준 |
|---|---|---|---|
| FR-001 | 시스템은 미국 동부 현지시각 기준 일 2개 슬롯(08:00, 22:00)을 생성한다. | Must | DST 전환일에도 슬롯 중복/누락 없음 |
| FR-002 | 각 슬롯은 DB unique key로 한 실행만 claim한다. | Must | workflow 재실행에도 영상 중복 생성/게시 없음 |
| FR-003 | 단계별 상태 머신을 유지한다. | Must | 실패 위치와 재개 지점 식별 가능 |
| FR-004 | 수동 dispatch는 `dry_run`, 날짜, 슬롯, 강제 재생성 입력을 받는다. | Must | 업로드 없이 전체 산출물 검증 가능 |
| FR-005 | 기존 Alert 파이프라인 장애와 격리한다. | Must | Shorts 실패가 Alert 발행에 영향 없음 |

권장 상태:

`PLANNED → SOURCED → SCRIPTED → STORYBOARDED → RENDERED → VALIDATED → UPLOADING → PUBLISHED`

예외 상태:

`RETRYABLE_FAILED`, `QUARANTINED`, `SKIPPED`, `UPLOAD_UNKNOWN`

### 8.2 리서치와 팩트 패키지

| ID | 요구사항 | 우선순위 | 인수 기준 |
|---|---|---|---|
| FR-010 | 후보마다 최소 2개 독립 출처 또는 1개 공식 1차 출처를 요구한다. | Must | 출처 조건 미달 시 대본 생성 안 함 |
| FR-011 | 원문에서 수치·단위·시각·URL을 구조화한다. | Must | 모든 사실 문장에 evidence ID 연결 |
| FR-012 | LLM 입력은 허용된 fact pack으로 제한한다. | Must | 대본의 사실 claim이 evidence와 매핑됨 |
| FR-013 | 데이터 freshness TTL을 자산별로 관리한다. | Must | 만료 데이터 사용 시 validation fail |
| FR-014 | 뉴스 원문 전체를 영상에 복제하지 않는다. | Must | 인용문 길이와 사용 라이선스 검사 |

### 8.3 대본과 콘티

| ID | 요구사항 | 우선순위 | 인수 기준 |
|---|---|---|---|
| FR-020 | Claude가 구조화 JSON 대본을 생성한다. | Must | schema validation 100% 통과 |
| FR-021 | 대본은 `fact`, `hypothesis`, `narration`, `visual`, `evidence_ids`를 구분한다. | Must | 상상 문장이 사실로 표시되지 않음 |
| FR-022 | 결정론적 검사 후 별도 LLM critic을 수행한다. | Must | 두 게이트 모두 통과해야 렌더링 |
| FR-023 | 실패 시 오류 피드백을 포함해 최대 2회 재작성한다. | Must | 무한 재시도 없음 |
| FR-024 | 최근 30일 대본과 의미 중복을 검사한다. | Must | 설정 임계값 초과 시 다른 소재 선택 |
| FR-025 | 5~7개 장면 콘티와 자막 cue를 만든다. | Must | 장면 합계가 목표 duration과 일치 |

### 8.4 미디어 생성 및 합성

| ID | 요구사항 | 우선순위 | 인수 기준 |
|---|---|---|---|
| FR-030 | Gemini가 캐릭터 bible과 장면 프롬프트로 원본 이미지를 생성한다. | Must | 캐릭터 핵심 속성 일관성 검사 통과 |
| FR-031 | 모든 프롬프트에 IP negative constraints를 삽입한다. | Must | 금지 키워드가 최종 프롬프트에 없음 |
| FR-032 | TTS 음성과 실제 길이를 생성한다. | Must | hard fail 길이 범위 충족 |
| FR-033 | FFmpeg로 장면, 전환, 음성, BGM, 자막을 합성한다. | Must | ffprobe 사양 검사 통과 |
| FR-034 | BGM은 생성·취득 비용 0원이고 상업적 YouTube 사용 근거가 확인된 원본/무료 자산만 쓴다. | Must | 비용 0원과 asset ledger 근거가 모두 존재 |
| FR-035 | 이미지 생성 실패 시 승인된 추상 모션 템플릿으로 fallback한다. | Should | 저품질 이미지를 게시하지 않고 영상 완성 가능 |

### 8.5 품질·안전 검증

| ID | 요구사항 | 우선순위 | 인수 기준 |
|---|---|---|---|
| FR-040 | JSON schema, 길이, 금칙어, claim/evidence, disclaimer를 검사한다. | Must | 한 항목 실패 시 업로드 차단 |
| FR-041 | OCR로 영상 내 텍스트 오탈자와 금칙어를 검사한다. | Must | 검출 결과가 audit에 저장됨 |
| FR-042 | 샘플 프레임으로 로고·유명 캐릭터 유사성 위험을 검사한다. | Must | 고위험은 quarantine |
| FR-043 | 무음, clipping, black frame, 해상도, fps를 검사한다. | Must | 기술 QC 실패 시 자동 재렌더링 1회 |
| FR-044 | 금융 조언·과장·정치·혐오·성적·폭력 표현을 검사한다. | Must | high severity 0건 |
| FR-045 | 품질 점수와 차단 사유를 코드화한다. | Must | 운영자가 실패 추세를 집계 가능 |

### 8.6 YouTube 업로드

| ID | 요구사항 | 우선순위 | 인수 기준 |
|---|---|---|---|
| FR-050 | OAuth refresh token으로 YouTube Data API 업로드를 수행한다. | Must | 비대화형 runner에서 token refresh 가능 |
| FR-051 | 운영 모드는 검증 완료 직후 `public`으로 바로 업로드한다. 테스트 환경만 private/unlisted를 허용한다. | Must | QC 통과 전 업로드 없음, 운영 업로드의 privacy가 public |
| FR-052 | `content_id`별 remote video ID를 저장해 중복 업로드를 방지한다. | Must | timeout 재시도에도 원격 중복 없음 |
| FR-053 | resumable upload와 지수 backoff를 사용한다. | Must | 일시 오류 후 동일 세션 재개 |
| FR-054 | 제목·설명·언어·카테고리·아동용 여부·합성 콘텐츠 관련 설정을 적용한다. | Must | 업로드 후 API read-back 값 일치 |
| FR-055 | quota 부족 또는 인증 오류 시 게시를 중단하고 알림을 보낸다. | Must | 다른 슬롯의 무한 실패를 유발하지 않음 |
| FR-056 | 게시 후 처리 상태와 재생 가능 여부를 확인한다. | Must | 성공 확인 뒤에만 `PUBLISHED` 기록 |

> **인증 메모**: `YOUTUBE_API_KEY`는 공개 데이터 조회용이다. 영상 업로드에는 사용자 권한이 있는 OAuth 2.0 자격증명과 장기 실행을 위한 refresh token이 필요하다. 향후 “YouTube key”를 추가할 때 이 둘을 혼동하지 않아야 한다.

### 8.7 알림·운영·분석

| ID | 요구사항 | 우선순위 | 인수 기준 |
|---|---|---|---|
| FR-060 | 성공/skip/quarantine/failure를 Telegram 내부 채널에 요약한다. | Must | video URL 또는 trace ID 포함 |
| FR-061 | API 호출 수, 토큰, 이미지 수, 렌더 시간, 예상 비용을 기록한다. | Must | 영상별 원가 조회 가능 |
| FR-062 | 24/48시간 성과를 수집한다. | Should | views, retention 계열 가용 지표, likes, comments 저장 |
| FR-063 | 성과는 프롬프트를 직접 자기수정하지 않고 실험 설정에 반영한다. | Must | 승인되지 않은 prompt drift 없음 |
| FR-064 | 일일 2개 모두 미게시 시 운영 경보를 발송한다. | Must | 마지막 슬롯 이후 30분 내 경보 |
| FR-065 | kill switch로 생성/업로드/공개를 각각 중단할 수 있다. | Must | GitHub Variable 변경 후 다음 실행부터 적용 |

---

## 9. 비기능 요구사항

| ID | 영역 | 요구사항 |
|---|---|---|
| NFR-01 | 신뢰성 | 단계별 idempotency, 원자적 상태 전이, 최대 재시도 횟수 적용 |
| NFR-02 | 성능 | 슬롯 시작 후 20분 내 업로드 준비 완료를 목표로 함 |
| NFR-03 | 보안 | secret은 GitHub Secrets에만 저장하고 로그·artifact·DB에 원문 출력 금지 |
| NFR-04 | 최소 권한 | YouTube OAuth scope와 GitHub permissions를 필요한 범위로 제한 |
| NFR-05 | 비용 | 일/영상/제공자별 budget cap과 circuit breaker 제공 |
| NFR-06 | 감사성 | 입력 해시, 모델명, prompt version, 산출물 해시, 검증 결과 보존 |
| NFR-07 | 유지보수 | provider adapter로 Claude/Gemini/TTS/스토리지를 교체 가능하게 설계 |
| NFR-08 | 재현성 | seed, 설정, prompt, 모델 버전을 기록하되 모델 출력 완전 재현은 보장하지 않음 |
| NFR-09 | 개인정보 | 불필요한 개인 데이터 수집 금지, OAuth token 암호화 및 즉시 폐기 가능 |
| NFR-10 | 접근성 | 핵심 발화를 자막으로 제공하고 과도한 섬광·빠른 전환 금지 |
| NFR-11 | 가용성 | 단일 provider 장애 시 fallback 또는 안전 skip, 기존 Alert 영향 0 |
| NFR-12 | 보존 | fact pack과 생성 원본·중간·최종 산출물은 자동 삭제하지 않으며 원본 기사 전문은 저장하지 않음 |

### 권장 SLO

- 파이프라인 실행 성공률: 월 98% 이상
- 안전 게이트 우회: 0건
- 중복 공개 업로드: 0건
- 사실 오류로 인한 삭제: 월 0건 목표
- 장애 탐지 시간: 15분 이내
- kill switch 반영: 다음 스케줄 또는 15분 이내

---

## 10. 제안 아키텍처

```text
Existing Collectors / Alert DB / Market Data
                    │
                    ▼
             Topic Selector
                    │
          Fact Pack + Evidence Ledger
                    │
          ┌─────────┴─────────┐
          ▼                   ▼
   Claude Script Writer   Policy Context
          │
   Deterministic Validator → Claude Critic
          │
          ▼
   Gemini Storyboard / Image Generator
          │
      TTS + BGM/SFX
          │
          ▼
     FFmpeg Composer
          │
   Technical + Content QC
          │
          ▼
 YouTube OAuth Uploader → Read-back Verify
          │
 Supabase Audit + Object Storage + Telegram
```

### 10.1 권장 디렉터리

```text
shorts/
  domain/             # schemas, enums, state machine
  collectors/         # 기존 이벤트를 fact pack으로 변환
  planning/           # topic selector, scheduler
  generation/         # Claude/Gemini/TTS adapters
  rendering/          # FFmpeg composition
  validation/         # fact, policy, IP, media QC
  publishing/         # YouTube OAuth uploader
  analytics/          # post-publish metrics
  prompts/            # versioned prompts
  assets/             # licensed templates; license ledger
  config/
run_shorts.py
```

모델 SDK를 도메인 로직에서 직접 호출하지 않고 `TextProvider`, `ImageProvider`, `SpeechProvider`, `VideoPublisher` protocol 뒤에 둔다.

### 10.2 책임 분배

- **Claude**: fact pack 기반 대본, 압축/재작성, 별도 critic
- **Gemini**: 장면 콘티 보강, 원본 이미지 생성, 선택적 visual critic
- **Python 규칙 엔진**: schema, 수치, evidence, 금칙어, 중복, 시간, 상태 전이
- **FFmpeg/ffprobe**: 결정론적 영상 합성 및 기술 검사
- **YouTube API**: 업로드·상태 확인·메타데이터

같은 모델이 작성과 최종 승인을 동시에 맡지 않도록 한다. LLM 판단만으로 업로드를 허용하지 않는다.

---

## 11. 데이터 모델 초안

### 11.1 `shorts_jobs`

| 필드 | 설명 |
|---|---|
| `id` | UUID |
| `content_id` | 날짜·슬롯·주제 기반 멱등 키, UNIQUE |
| `slot_at` | ET 슬롯의 UTC timestamp |
| `state` | 상태 머신 값 |
| `topic_type`, `topic_key` | 이벤트 분류와 중복 키 |
| `source_event_ids` | 기존 Alert/sector 이벤트 참조 |
| `attempt_count` | 전체 및 단계별 시도 횟수 |
| `policy_version` | 정책 버전 |
| `created_at`, `updated_at` | 감사 시각 |

### 11.2 `shorts_artifacts`

- `job_id`, `artifact_type`, `storage_uri`
- `sha256`, `mime_type`, `duration_ms`, `width`, `height`
- `model_provider`, `model_name`, `prompt_version`, `seed`
- `cost_estimate`, `created_at`

### 11.3 `shorts_evidence`

- `job_id`, `evidence_id`, `source_name`, `source_url`
- `published_at`, `observed_at`, `claim`, `value`, `unit`
- `content_hash`, `source_tier`, `expires_at`

### 11.4 `shorts_publications`

- `job_id`, `platform`, `remote_id`, `upload_session_uri_hash`
- `privacy_status`, `processing_status`, `published_at`
- `title`, `metadata_hash`, `last_verified_at`

### 11.5 `shorts_validations`

- `job_id`, `validator`, `validator_version`, `severity`
- `passed`, `code`, `details_json`, `created_at`

DB에는 secret, refresh token, 기사 전문, 원본 API 응답의 민감 필드를 저장하지 않는다.

---

## 12. 설정과 Secret 요구사항

### 12.1 기존 재사용 예상

- `ANTHROPIC_API_KEY`
- `GEMINI_API_KEY` 또는 기존 호환 경로의 `GOOGLE_API_KEY`
- `SUPABASE_URL`, `SUPABASE_KEY`
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_INTERNAL_CHANNEL_ID`

실제 값의 존재 여부를 문서나 로그에 노출하지 않고 GitHub repository Secrets 화면에서 확인한다.

### 12.2 신규 필수 Secret

- `YOUTUBE_CLIENT_ID`
- `YOUTUBE_CLIENT_SECRET`
- `YOUTUBE_REFRESH_TOKEN`
- 필요 시 `YOUTUBE_CHANNEL_ID`
- TTS provider를 외부 서비스로 선택할 경우 해당 API key
- object storage를 별도로 쓸 경우 storage credential

### 12.3 GitHub Variables 제안

```text
SHORTS_ENABLED=false
SHORTS_GENERATION_ENABLED=true
SHORTS_UPLOAD_ENABLED=false
SHORTS_PUBLIC_ENABLED=false
SHORTS_DAILY_LIMIT=2
SHORTS_TIMEZONE=America/New_York
SHORTS_SLOT_TIMES=08:00,22:00
SHORTS_DEFAULT_PRIVACY=public
SHORTS_MAX_DAILY_COST_USD=<결정 필요>
SHORTS_TEXT_MODEL=<핀 고정>
SHORTS_IMAGE_MODEL=<핀 고정>
SHORTS_TTS_PROVIDER=<결정 필요>
SHORTS_POLICY_VERSION=v1
```

Secret preflight는 값 자체가 아니라 존재 여부만 출력한다. refresh token 발급용 1회성 로컬 도구는 저장소에 넣을 수 있지만 token은 절대 commit하지 않는다.

---

## 13. 실패 처리와 멱등성

### 13.1 재시도 분류

- **재시도 가능**: 429, 5xx, 네트워크 timeout, 일시적 렌더 오류
- **재시도 불가**: schema 오류 반복, 정책 위반, OAuth revoked, quota cap, source 부족
- API retry: exponential backoff + jitter, 최대 횟수와 deadline 적용
- 생성 retry: 동일 출력 반복을 막기 위해 오류 피드백 포함, 단계별 최대 2회

### 13.2 업로드 불확실 상태

업로드 요청 후 timeout이 발생했다고 즉시 새 영상을 올리지 않는다.

1. 업로드 세션 재개를 먼저 시도한다.
2. 저장된 remote ID가 있으면 API로 확인한다.
3. 확인 불가능하면 `UPLOAD_UNKNOWN`으로 격리한다.
4. 운영 알림 후 자동 중복 업로드를 금지한다.

### 13.3 Fallback 순서

1. 대본 실패 → 같은 주제 재작성 최대 2회
2. 주제 실패 → 차순위 후보 1회
3. 이미지 실패 → 재생성 1회 → 승인된 추상 템플릿
4. TTS 실패 → 보조 provider 또는 skip
5. 업로드 실패 → 재시도 후 다음 슬롯과 독립적으로 격리

Fallback이 안전 기준을 낮춰서는 안 된다.

---

## 14. 비용·용량 계획

비용은 모델과 음성 provider가 확정된 뒤 실제 단가로 계산한다. 단가는 코드에 하드코딩하지 않고 dated pricing config로 관리한다.

### 영상 1개 비용 항목

- Claude input/output tokens 및 critic 호출
- Gemini 장면 이미지 5~7장과 재생성
- TTS 문자/음성 길이
- object storage 및 egress
- GitHub Actions 실행 시간
- YouTube API quota 사용량

### 제어 요구사항

- 사전 예상 비용이 영상 cap을 넘으면 생성 중단
- 누적 일일 비용이 daily cap을 넘으면 남은 슬롯 `SKIPPED_BUDGET`
- 이미지 재생성 횟수 제한
- 동일 fact pack/model response 캐시
- 주간 비용·성공률·영상당 비용 보고서

YouTube API quota 단위와 업로드 제한은 구현 시점의 공식 Console/문서 값으로 확인하며, 프로젝트가 업로드 공개 요건을 충족하는지 출시 전에 검증한다.

---

## 15. 보안과 컴플라이언스

- GitHub Actions는 최소 `contents: read` 권한을 기본으로 한다.
- production environment에 승인 규칙을 둘 수 있으나 최종 목표는 자동 승인이므로 초기 canary 단계에서만 수동 보호를 사용한다.
- 모든 외부 입력은 prompt injection 비신뢰 데이터로 취급한다. 기사 본문의 지시문을 수행하지 않는다.
- URL allowlist/source tier와 response size 제한을 적용한다.
- artifact에 환경변수, request header, OAuth response 원문을 남기지 않는다.
- token은 즉시 revoke/rotate 가능한 운영 절차를 문서화한다.
- 생성 자산별 provider 약관, 상업 이용 범위, 프롬프트/출력 보존 정책을 출시 전 검토한다.
- 저작권 신고·사실 오류 발생 시 `SHORTS_PUBLIC_ENABLED=false`로 즉시 차단하고 대상 영상을 비공개/삭제하는 runbook을 제공한다.

---

## 16. 테스트 전략과 인수 기준

### 16.1 자동 테스트

- Unit: 상태 전이, 슬롯/DST, 점수, schema, 금칙어, claim mapping, 비용 cap
- Contract: Claude/Gemini/YouTube adapter의 fixture 기반 응답
- Integration: fact pack → MP4까지 외부 API mock E2E
- Media: ffprobe duration/codec/resolution/audio stream, black/silence 검사
- Security: secret redaction, malicious article prompt injection fixture
- Idempotency: 동일 job 10회 병렬 실행 시 artifact/upload 1개
- Failure injection: 429, timeout, malformed JSON, revoked token, upload unknown

### 16.2 출시 인수 기준

- [ ] 30개 dry-run 영상 중 기술 QC 통과율 95% 이상
- [ ] 내부 리뷰 30개에서 사실 오류 0건, IP high-risk 0건
- [ ] 7일 private canary 동안 중복 업로드 0건
- [ ] DST 경계와 휴장일 테스트 통과
- [ ] 비용 cap 및 kill switch 실제 동작 확인
- [ ] YouTube OAuth token refresh 및 read-back 검증 통과
- [ ] 72시간 unlisted canary 후 7일 public pilot 완료
- [ ] 장애·삭제·token revoke runbook 훈련 완료

### 16.3 콘텐츠 품질 점수 예시

총 100점 중 85점 이상이며 hard gate를 모두 통과해야 게시한다.

- 사실성과 근거 30
- 이해도와 한 메시지 원칙 20
- 훅과 유지 가능성 15
- 시각 일관성 15
- 음성/자막/기술 품질 10
- 독창성 10

사실 오류, 증거 없는 숫자, IP high-risk, 투자 권유, 기술 사양 오류는 점수와 무관하게 hard fail이다.

---

## 17. 단계별 구현 로드맵

### Phase 0 — 결정과 계정 준비 (2~3일)

- 채널 언어, 이름, 캐릭터 bible, 음성, 예산 확정
- YouTube 프로젝트/OAuth consent/client/refresh token 준비
- 업로드 API와 채널 권한, quota, 공개 제한 확인
- 자산 라이선스 ledger 생성

**Exit**: Decision log 완료, secret preflight 설계 승인.

### Phase 1 — Offline MVP (약 1주)

- topic selector, fact pack, 대본 JSON, validators
- Gemini 정지 이미지 + TTS + FFmpeg motion comic
- 로컬/Actions artifact까지만 생성, 업로드 없음

**Exit**: 30개 샘플 기준 자동 QC 및 내부 품질 기준 통과.

### Phase 2 — Private/Unlisted canary (약 1주)

- OAuth uploader, resumable upload, read-back, DB 상태
- 테스트 채널에서 하루 1개 private → 하루 2개 unlisted 확대
- 비용, 실패, 처리 시간 측정

**Exit**: 7일 중복 0건, 사실/IP 사고 0건, 성공률 목표 충족.

### Phase 3 — Public pilot (2주)

- `SHORTS_PUBLIC_ENABLED=true`
- 운영 채널에서 즉시 공개 방식으로 하루 1개 → 2개까지 점진 확대
- 성과 수집과 시간/훅 실험

**Exit**: 운영 SLO 및 콘텐츠 KPI 베이스라인 확보.

### Phase 4 — 최적화

- 장면별 retention 기반 실험
- 선택적 text-to-video 또는 고급 패럴랙스
- 영어/이중언어 채널은 별도 채널 전략 검토 후 진행
- 자동 댓글은 스팸·오답 위험 때문에 별도 요구사항으로 분리

---

## 18. KPI

### 운영 KPI

- 슬롯 실행률, 생성 성공률, 게시 성공률
- 단계별 p50/p95 소요 시간
- retry/quarantine/skip 비율과 사유
- 영상당 비용, 일/월 누적 비용
- 중복 업로드 및 삭제 건수

### 콘텐츠 KPI

- 노출 대비 조회, 평균 시청 시간, 평균 시청 비율
- 초반 이탈과 완주/반복 시청 관련 가용 지표
- 좋아요·댓글·구독 전환
- 슬롯, 주제, hook template, 길이, 캐릭터별 성과

초기 30일은 고정 목표 수치보다 베이스라인 수집을 우선한다. 조회 수만 최적화해 과장 제목이나 반복 콘텐츠로 기울지 않도록 안전 KPI와 함께 평가한다.

---

## 19. 주요 위험과 대응

| 위험 | 영향 | 대응 |
|---|---|---|
| 유명 프랜차이즈 유사성 | 저작권/상표 분쟁 | 오리지널 bible, negative prompt, 프레임 검사, 출시 전 법률 검토 |
| 시장 사실 환각 | 신뢰 훼손 | evidence ledger, claim mapping, 결정론적 검사, 안전 skip |
| YouTube 인증/공개 제한 | 자동 게시 실패 | Phase 0 OAuth/프로젝트 검증, private canary |
| 반복·저부가 AI 콘텐츠 인식 | 배포/수익화 악영향 | 실질적 정보 가치, 포맷 변주, 원본 해설, 품질 gate |
| API 단가/모델 변화 | 비용/품질 변동 | 모델 pin, adapter, budget cap, canary upgrade |
| GitHub cron 지연 | 슬롯 지연 | 15분 dispatcher, DB claim, 허용 window |
| DST/휴장일 | 잘못된 게시 시각/소재 | ET timezone 계산, 기존 market calendar 재사용 |
| 업로드 timeout 후 중복 | 채널 스팸 | resumable session, `UPLOAD_UNKNOWN`, idempotency |
| Secret 유출 | 채널 탈취 | Secrets, redaction, 최소 권한, rotation runbook |
| 100% 자동화 압박 | 위험 결과 강제 게시 | fail-closed, 최대 2개 원칙, quarantine 알림 |

---

## 20. 확정된 제품 결정

| 항목 | 확정값 | 상세설계 해석 |
|---|---|---|
| 언어 | 한국어 | 자막·내레이션·제목·설명 모두 한국어, 티커는 영문 유지 |
| 플랫폼 | YouTube | Shorts 전용 업로드, 다른 SNS 동시 게시 제외 |
| 기준 시간 | EDT 요청 | `America/New_York` 현지시각 사용. 여름 EDT·겨울 EST에서도 08:00/22:00 유지 |
| 기업 표시 | 회사 마크 사용 | 공식 원본·승인 registry·편집적 식별 용도만 허용 |
| BGM | 무료만 | 생성 요청 비용과 라이선스 비용이 모두 0원이며 상업적 YouTube 이용 근거가 있는 자산만 사용 |
| 발행량 | 일 2건 | 오전/오후 각 1건, 안전 게이트 실패 시 강제 대체 게시 금지 |
| 발행 시각 | 08:00, 22:00 | 미국 동부 현지시각 기준 |
| 휴장일 | 운영 | 동일 2슬롯에 evergreen/주간 회고/다음 거래일 콘텐츠 게시 |
| 공개 방식 | 바로 공개 | 모든 자동 QC 완료 후 `privacyStatus=public`으로 업로드 |
| 보존 | 원본 보존 | fact pack, 대본, 이미지, 음성, 자막, MP4, 검증 기록을 원본으로 보존 |
| 라이선스 | 회피형 제작 | 라이선스를 우회하지 않고, 제3자 저작물을 애초에 배제하는 clean-room 원본 제작 |

상세 컴포넌트, 인터페이스, 상태 전이와 개발 순서는 `docs/youtube_shorts_detailed_design_2026-07-24.md`에서 관리한다.

---

## 21. Definition of Done

이 프로젝트의 v1은 다음 조건을 모두 충족할 때 완료로 본다.

- 미국 동부 현지시각 기준 두 슬롯을 DST/휴장일 포함 정확히 orchestration한다.
- 신뢰 가능한 fact pack에서 27~32초 오리지널 영상을 자동 생성한다.
- 모든 사실 claim, 생성 자산, prompt/model/policy 버전을 추적할 수 있다.
- IP·금융·콘텐츠·기술 hard gate 실패 시 업로드가 fail-closed 된다.
- OAuth 기반 업로드가 멱등적이며 처리 결과를 read-back 검증한다.
- 7일 canary와 2주 public pilot에서 중복·사실 오류·정책 사고가 없다.
- kill switch, 비용 cap, 알림, 삭제/비공개, token revoke runbook이 검증된다.
- 기존 투자 Alert 파이프라인의 테스트와 운영 동작에 회귀가 없다.

---

## 부록 A. 예시 에피소드 구조

**제목**: “10년물 금리가 멈추지 않았다면?”

- 0~3초: 도시의 전광판이 노란 파동으로 흔들린다.
- 3~9초: 기준 시각과 실제 10년물 금리 변화 1개를 제시한다.
- 9~21초: 가상 존재 `Yield`가 성장주의 미래 현금흐름을 더 멀리 밀어내는 대체 현실을 보여준다.
- 21~27초: 현실로 돌아와 금리와 성장주 밸류에이션의 연결을 한 문장으로 설명한다.
- 27~30초: “다음 분기점은 고용지표입니다. 투자 조언이 아닌 시장 해설입니다.”

실제 생성 시 수치와 결론은 당일 evidence에만 근거해야 하며, 위 예시는 포맷 설명용이다.

## 부록 B. 구현 전 공식 문서 확인 목록

API와 플랫폼 정책은 변경될 수 있으므로 구현 및 출시 당일 다음 공식 문서를 재확인하고 확인 일자를 decision log에 남긴다.

- YouTube Data API `videos.insert`, resumable upload, quota calculator
- Google OAuth 2.0 installed/web application 및 offline access 지침
- YouTube altered/synthetic content, spam/deceptive practices, reused/repetitious content, made-for-kids 정책
- YouTube 지원 영상 규격 및 Shorts 분류 기준
- Anthropic Messages API, 모델 lifecycle, rate limit, data usage 정책
- Google Gemini API 이미지 생성, safety, 모델 lifecycle, rate limit, 데이터 사용 정책
- GitHub Actions scheduled workflow와 Secrets/Environments 지침

이 목록의 확인은 법률 자문을 대체하지 않는다.
