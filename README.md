# investment-alert

Investment Alert 분리 개발 착수를 위한 최소 도메인 스캐폴드입니다.

## 포함 모듈
- `models.py`: 레벨 판정 및 품질 저하 시 강등 규칙
- `routing.py`: L1/L2/L3 채널 라우팅 및 중복 발송 cooldown
- `audit.py`: alert_id 단위 감사로그 저장/조회
- `policy.py`: ToS/재배포 정책 게이트
- `quota.py`: 사용량 임계치(80/90/95%) 기반 자동 강등 모드
- `pilot.py`: 파일럿 시나리오 실행기(정책/쿼터/라우팅/감사로그 최소 오케스트레이션)

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
