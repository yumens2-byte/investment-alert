# Investment Alert 분석/설계 및 개발착수안 (v1.0)

## 1) 요구사항 분석 요약
- 핵심 가치: 시장 급변 이벤트를 빠르게 감지하고, L1/L2/L3 정책에 따라 채널별로 안정적으로 발송.
- 가장 큰 리스크: 데이터 소스 품질 저하/ToS 위반/쿼터 고갈 시 오탐 또는 미발송.
- 2주 내 우선순위: ToS 정책게이트, 감사로그, 라우팅/쿨다운, 쿼터 강등을 먼저 코드화.

## 2) 도메인 분해
- Ingestion: 소스별 timeout/retry/circuit-breaker + 메타데이터(api key requirements)
- Normalization/Quality: raw→normalized→feature + quality_check 결과 저장
- Detection: 레이어 점수(price/derivatives/macro-news) 및 레벨 판정
- Delivery: 레벨별 채널 라우팅 + dedupe cooldown
- Governance: ToS 정책 게이트, 감사로그, quota_manager

## 3) 개발착수 범위 (이번 커밋)
- 정책게이트(SourcePolicyGate) 최소 구현
- 쿼터 임계 기반 모드 전환(QuotaManager) 구현
- 레벨 판정/건강도 강등(AlertEvent.level) 구현
- 채널 라우팅/쿨다운(ChannelRouter) 구현
- 감사로그 저장/조회(InMemoryAuditStore) 구현
- 요구사항 Acceptance Criteria 중심 단위테스트 추가

## 4) 다음 단계
- 영속 스토리지(PostgreSQL/Redis) 및 스케줄러 연동
- SLO 지표 노출(Prometheus)
- 리플레이 파이프라인 및 shadow mode 운영 자동화
