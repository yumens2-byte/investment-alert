# investment-alert

Investment Alert 분리 개발 착수를 위한 최소 도메인 스캐폴드입니다.

## 포함 모듈
- `models.py`: 레벨 판정 및 품질 저하 시 강등 규칙
- `routing.py`: L1/L2/L3 채널 라우팅 및 중복 발송 cooldown
- `audit.py`: alert_id 단위 감사로그 저장/조회
- `policy.py`: ToS/재배포 정책 게이트
- `quota.py`: 사용량 임계치(80/90/95%) 기반 자동 강등 모드
- `pilot.py`: 파일럿 시나리오 실행기(정책/쿼터/라우팅/감사로그 최소 오케스트레이션)
- `state.py`: 이전 발송 이력 기반 재발송 방지(sha256 digest + 원자적 저장)
- `runner.py`: 실제 실행 엔트리(이전 안내 발송 차단 포함)

## 테스트
```bash
pytest -q
```

## 파일럿 테스트 3회
```bash
pytest -q tests/test_pilot_runs.py
```
- Pilot 1: L1 이벤트 정상 발송
- Pilot 2: 미검토 소스 정책 차단
- Pilot 3: quota 95%+ CORE_ONLY에서 비-L1 차단

## GitHub Action (1시간 주기)
- 워크플로우: `.github/workflows/hourly-alert.yml`
- 스케줄: `0 * * * *` (매 정각, 1시간 주기)
- 실행 스크립트: `python scripts/run_hourly_job.py`

## 이전 안내 재발송 방지(보완)
- dedupe key를 SHA-256으로 저장해 원문 key 평문 노출을 최소화
- 상태 파일 `.state/sent_alerts.json` 원자적 저장(`os.replace`) 적용
- 상태 파일 권한 `0600` 적용


## 파일럿 테스트 2회 수행
```bash
PYTHONPATH=src python scripts/run_pilot_twice.py
```
- 1회차: sent=True
- 2회차: already_sent로 차단
