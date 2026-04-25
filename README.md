# ────────────────────────────────────────────────────────
# investment-alert 환경변수 템플릿
# ────────────────────────────────────────────────────────
# 사용 방법:
#   cp .env.example .env
#   각 값을 실제 값으로 채운 후 저장
# ────────────────────────────────────────────────────────

# ── YouTube 채널 설정 ──────────────────────────────────
# 포맷: "이름1:채널ID1,이름2:채널ID2,..."
# 예시:


# ── AI API 키 ─────────────────────────────────────────
# Gemini API (Impact Scoring 용)
GEMINI_API_KEY=
GEMINI_API_SUB_KEY=
GEMINI_API_SUB_SUB_KEY=

# Claude API (fallback 용, 선택)
ANTHROPIC_API_KEY=

# ── 발행 API ───────────────────────────────────────────
# X (Twitter) API
X_API_KEY=
X_API_SECRET=
X_ACCESS_TOKEN=
X_ACCESS_TOKEN_SECRET=

# Telegram Bot
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHANNEL_ID=

# ── 운영 제어 ──────────────────────────────────────────
# true=모의 실행, false=실 발행
DRY_RUN=true

# true=휴무일도 실행
FORCE_RUN=false

# 로그 레벨: DEBUG, INFO, WARNING, ERROR
LOG_LEVEL=INFO

# ── 임계값 (옵션 — 기본값 사용 시 생략 가능) ───────────
# MacroNewsLayer 레벨 판정 임계값
THRESHOLD_L1_SCORE=7.0
THRESHOLD_L2_SCORE=5.0
THRESHOLD_L3_SCORE=3.0
THRESHOLD_HEALTH_L1=0.90
THRESHOLD_HEALTH_L2=0.80
THRESHOLD_HEALTH_L3=0.70
