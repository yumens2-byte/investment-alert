# Macro Alert 고도화 사전분석 (개발착수 전)

작성일: 2026-05-03 (UTC)

## 1) 현재 로그 기반 결론
- 이번 미발행은 시스템 장애보다는 **정상 정책 흐름**으로 판단된다.
- 근거:
  - RSS/YouTube 소스 연결은 정상(HTTP 200)이나,
  - 수집 후 `키워드 필터` 단계에서 뉴스/유튜브 모두 0건,
  - 결과적으로 `score=0`, `level=NONE`, 발행 대상 전 채널 스킵.
- 즉, "감지 엔진 실패"가 아니라 "이벤트 적합도 부족" 케이스다.

## 2) 코드 구조 관점의 현재 한계

### 2.1 룰베이스 키워드 편향
- 뉴스/유튜브 모두 키워드 기반 1차 필터에서 탈락하면 점수 계산 자체가 0으로 수렴.
- 현재 임계치(`KEYWORD_THRESHOLD=2.0`)와 키워드 사전이 환경 변화(신규 이슈 표현, 우회적 표현)에 둔감할 수 있음.

### 2.2 휴장일 프로파일의 보수적 임계값
- `holiday` 프로파일에서 임계값이 높아, 주말/휴장일의 이벤트 감지가 더 어려움.
- 실제로 장외 급변(지정학, 규제, 대형 실적 쇼크) 발생 시 선제 탐지가 약해질 위험.

### 2.3 단일 소스 신선도/커버리지 품질은 보지만 "이벤트 의미" 품질 지표는 약함
- DQ 모니터는 수집 성공률/신선도/지연 중심.
- "수집은 됐지만 의미 이벤트가 하나도 안 잡히는 상태"를 장기적으로 감시하는 메트릭이 부족.

### 2.4 YouTube 탐지의 구조적 공백
- YouTube는 당일(UTC) 윈도우 + 제외 패턴이 강함.
- 채널별 업로드 시간대, 제목 스타일 변화 시 감지율 하락 가능.

## 3) "특별한 경제 변화" 대응을 위한 우선 고도화 후보

## A. Event Scarcity Guard (이벤트 희소 경보)
**목표:** 소스는 정상인데 "유효 이벤트 0건"이 일정 시간 이상 지속되면 운영 경고.

- 신호:
  - `raw_count > 0` AND `filtered_count == 0`가 N회 연속
  - 최근 7일 동시간대 대비 유효 이벤트 비율 급락
- 액션:
  - 발행 채널(X/TG)이 아닌 **운영 채널/관리자 DM**으로만 "탐지 희소" 알림
  - alert level은 유지하되 `ops_warning` 트랙 신설
- 기대효과:
  - "정상 종료지만 실질 탐지력 저하"를 조기 발견

## B. Market Shock Override Layer (장외 충격 오버라이드)
**목표:** 휴장일/야간에도 대형 이슈는 완화된 별도 룰로 통과.

- 입력 후보:
  - Fed/FOMC/긴급성명/국가 신용등급/제재/전쟁/대형 파산 키워드군
  - 소스 신뢰도(Tier S/A) 가중
- 정책:
  - `holiday`일 때도 특정 키워드 조합 + Tier 조건 충족 시 최소 L3 후보 부여
- 보호장치:
  - 단일 저신뢰 소스 단독 통과 금지(2-source corroboration)

## C. Cross-source Confirmation Score (교차검증 점수)
**목표:** 동일 이슈가 뉴스+유튜브(또는 복수 뉴스)에서 동시 관측되면 보너스.

- 방식:
  - 제목/본문 키프레이즈 정규화 후 토픽 해시 생성
  - 일정 시간 창 내 동일 토픽 출현 수를 보너스화
- 기대효과:
  - 단발성 노이즈보다 실제 시장 영향 이슈를 우선

## D. Dynamic Keyword Expansion (동적 키워드 확장)
**목표:** 신규 이슈 용어를 놓치지 않기.

- 방식:
  - 최근 30일 상위 이벤트 제목 n-gram 통계 + 사람이 승인한 키워드 추가
  - 월 1회 사전 업데이트 + 회귀 테스트
- 운영:
  - "제안 키워드"와 "활성 키워드"를 분리 관리

## E. Economic Regime Inputs 추가 (선물/금리/변동성)
**목표:** 텍스트 외 시장 데이터 급변을 별도 트리거로 반영.

- 후보 지표:
  - VIX 급등률, 미 10Y 금리 변동(bp), DXY 급등, S&P/NQ 선물 갭
- 정책:
  - 텍스트 이벤트가 약해도 시장 급변이 명확하면 `watch` 상태 승격
- 주의:
  - 과민 반응 방지를 위해 z-score + 최소 지속시간 필요

## 4) 개발착수 전 준비물 (DoR)

### 4.1 요구사항 명세
- [ ] "발행 알람"과 "운영 경고" 채널을 분리 정의
- [ ] false positive/false negative 허용 기준 수치화
- [ ] holiday/extended/intraday 프로파일별 목표 탐지율 정의

### 4.2 데이터/로그 설계
- [ ] `filtered_out_count`, `raw_count`, `source_hit_map` 저장 스키마 정의
- [ ] `ops_warning_reason` enum 설계 (`event_scarcity`, `regime_spike`, ...)
- [ ] 백테스트용 샘플 기간(최소 3개월) 확정

### 4.3 실험 설계
- [ ] 오프라인 리플레이 파이프라인에서 기존 정책 vs 후보 정책 A/B 비교
- [ ] 지표: Precision/Recall, 평균 최초 감지 지연, 알람 빈도, 야간 탐지율
- [ ] 실패 기준: 일평균 노이즈 알람 X건 초과 시 폐기

### 4.4 배포/롤백 설계
- [ ] 기능 플래그 도입(`ENABLE_SCARCITY_GUARD`, `ENABLE_SHOCK_OVERRIDE`)
- [ ] 1주 shadow mode (발행 없이 로그만)
- [ ] 롤백 기준/절차(runbook) 작성

## 5) 1차 착수 권장 우선순위
1. **A(Event Scarcity Guard)**: 구현 난이도 낮고 운영효과 즉시 큼.
2. **B(Shock Override)**: 휴장일 miss 리스크 대응 핵심.
3. **C(Cross-source Score)**: 품질 개선 효과 큼.
4. **D(Keyword Expansion)**: 중장기 유지보수성 강화.
5. **E(Regime Inputs)**: 외부 데이터 의존이 있어 마지막 단계.

## 6) 착수 직전 체크리스트
- [ ] 운영팀과 "운영 경고" 수신 채널 확정
- [ ] 최근 miss 사례 20건 라벨링 완료
- [ ] 플래그 OFF 기본 배포 가능 상태
- [ ] 관측 대시보드(경고 건수/탐지율/지연) 초안 준비

---

본 문서는 개발 시작 전 합의용 사전분석 문서이며, 코드 변경 없이 요구사항/실험/릴리즈 준비 수준까지를 범위로 한다.

## 7) 노션 업로드용 상세설계 (착수 버전 v0.1)

### 7.1 페이지 구조(권장)
1. 배경/문제정의
2. 목표/비목표
3. 기능 상세설계
4. 데이터 모델
5. 운영정책
6. 테스트 계획
7. 롤아웃 계획

### 7.2 기능 상세설계 — Event Scarcity Guard (이번 개발 반영)
| 항목 | 내용 |
|---|---|
| 기능명 | Event Scarcity Guard |
| 목적 | 수집 정상(raw>0)인데 필터 결과 0건인 상태를 운영 경고로 표면화 |
| 트리거 | `(raw_news + raw_yt) > 0` AND `(filtered_news + filtered_yt) == 0` |
| 출력 | `MacroNewsResult.ops_warnings[]`에 `event_scarcity...` 문자열 추가 |
| 발행 영향 | 없음(레벨 승격/강등 없음) |
| 관측 위치 | `MacroNewsLayer` warning 로그 + `DataLogger` score breakdown 하단 |

### 7.3 구현 범위(이번 사이클)
- [x] `MacroNewsResult`에 `ops_warnings` 필드 추가
- [x] `MacroNewsLayer.detect()`에 scarcity 판단 로직 추가
- [x] `DataLogger.log_score_breakdown()`에 운영 경고 출력 추가
- [x] 단위 테스트 2건 추가

### 7.4 다음 사이클 TODO
- [ ] N회 연속 scarcity 누적 카운터(메모리/DB) 추가
- [ ] 운영 채널(관리자 TG)로 `ops_warning` 전용 전송기 추가
- [ ] 경고 심각도(sev1/sev2) 및 suppression window 설계

### 7.5 테스트 플랜(노션 체크리스트 변환용)
- 파일럿 테스트(3회)
  - [ ] `python run_diagnostics.py`
  - [ ] `python run_alert.py`
  - [ ] 로그에서 `ops_warnings`/발행정책 확인
- 전수 테스트(2회)
  - [ ] `pytest -q --no-cov`
  - [ ] `pytest -q tests/test_bfix_dq_input.py tests/test_event_scarcity_guard.py --no-cov`
