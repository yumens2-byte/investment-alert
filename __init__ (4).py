[pytest]
# ────────────────────────────────────────────────────────
# pytest 설정
# Day 1 완료 보고서의 coverage fail_under=80 준수
# ────────────────────────────────────────────────────────
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*

# 출력 옵션
addopts =
    -v
    --strict-markers
    --tb=short
    --cov=collectors
    --cov=validators
    --cov=detection
    --cov=core
    --cov=config
    --cov-report=term-missing
    --cov-report=html
    --cov-fail-under=80

# 마커
markers =
    slow: 실행 시간 긴 테스트 (외부 API 호출 등)
    integration: 통합 테스트 (실제 RSS/API 호출)
    unit: 단위 테스트 (mock 기반)

# 경고 필터
filterwarnings =
    ignore::DeprecationWarning
