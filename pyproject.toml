# ────────────────────────────────────────────────────────
# pyproject.toml
# GTT팀 공통지침 11(ruff+pytest 세트) 완전 준수
# ────────────────────────────────────────────────────────

[project]
name = "investment-alert"
version = "0.4.0"
description = "미국 증시 긴급 Alert 감지 시스템"
requires-python = ">=3.11"

# ────────────────────────────────────────────────────────
# ruff 설정 (공통지침 11)
# ────────────────────────────────────────────────────────
[tool.ruff]
line-length = 100
target-version = "py311"
exclude = [
    ".venv",
    "venv",
    "__pycache__",
    "build",
    "dist",
]

[tool.ruff.lint]
select = [
    "E",    # pycodestyle errors
    "F",    # pyflakes
    "I",    # isort
    "W",    # pycodestyle warnings
    "B",    # flake8-bugbear
    "UP",   # pyupgrade
]
ignore = [
    "E501",  # line too long (ruff format이 처리)
    "B008",  # Function call in default argument
]

[tool.ruff.lint.isort]
# 공통지침 11: known-first-party 필수 설정
known-first-party = [
    "publishers",
    "db",
    "collectors",
    "config",
    "core",
    "detection",
    "validators",
    "tests",
]

[tool.ruff.lint.per-file-ignores]
"tests/*" = ["B011"]  # 테스트에서는 assert False 허용
"__init__.py" = ["F401"]  # re-export 허용
