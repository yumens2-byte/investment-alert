"""
제목: 뉴스/YouTube 키워드 규칙 (감지 정책 v1 사전 + v2 규칙)
내용: v1 사전은 collectors/news_collector.py 에서 값·순서 그대로 이동(Q2, 결과 불변).
      v2 규칙은 단어 경계(\b) 정규식 + 토픽/가중치/앵커(일반어 감쇠 해제) 구조.

주요 상수:
  - URGENT_KEYWORDS_*: v1 키워드 사전 10종 (news_collector 가 재노출)
  - KEYWORD_THRESHOLD / AI_SCORE_MIN_KEYWORD: v1 임계값
  - V2_RULES: v2 규칙 (rule_id, topic, weight, anchor, pattern)
  - KEYWORD_THRESHOLD_V2 / GENERIC_SOLO_FACTOR / GEO_COMBO_BONUS: v2 상수
  - YT_TOKEN_MAP: YouTube 한글/영문 키워드 → 공통 토픽 토큰
"""

from __future__ import annotations

import re

VERSION = "1.0.0"

# ────────────────────────────────────────────────────────
# v1 긴급 키워드 정의 (news_collector.py 에서 이동, 값·순서 동일)
# ────────────────────────────────────────────────────────
# 제목: L1 긴급 키워드
# 내용: 3인 전문가 협의 설계서 Round 1 확정값
URGENT_KEYWORDS_L1: dict[str, float] = {
    "emergency": 5.0,
    "crisis": 4.5,
    "unprecedented": 4.0,
    "extraordinary measures": 5.0,
    "circuit breaker": 5.0,
    "trading halt": 4.5,
    "market closed": 5.0,
    "flash crash": 4.5,
    "federal reserve announces": 5.0,
}

# 제목: L2 긴급 키워드
# 내용: L1보다 낮은 충격, 세부 분석 필요
URGENT_KEYWORDS_L2: dict[str, float] = {
    "significant": 2.5,
    "concern": 2.0,
    "volatility spike": 3.0,
    "sell-off": 2.5,
    "plunge": 3.0,
    "tumble": 2.5,
    "surge": 2.5,
    "rate hike": 3.0,
    "rate cut": 3.0,
    "recession": 3.5,
}

# ────────────────────────────────────────────────────────
# 확장 키워드 v1.1.0 (2026-05-03)
# 추가 영역: 관세·무역 / 지정학 / 미국 신용·재정 / AI·반도체 규제
# 변경 없음: URGENT_KEYWORDS_L1, URGENT_KEYWORDS_L2 (기존 유지)
# ────────────────────────────────────────────────────────

# 관세·무역정책 충격
URGENT_KEYWORDS_TARIFF: dict[str, float] = {
    # L1급 — 즉각 충격 표현 (단독 Tier A 1건으로 keyword_score >= 3.5)
    "tariff war": 4.0,
    "trade war": 3.5,
    "trade war escalates": 4.5,
    "export ban": 4.0,
    "import embargo": 4.5,
    "reciprocal tariff": 4.0,
    "trade deal collapse": 4.5,
    # L2급 — 단독 threshold 초과하나 Score 낮음 (복합 시 상승)
    "tariff": 2.0,
    "new tariff": 2.5,
    "export control": 2.5,
    "trade sanction": 3.0,
    "section 301": 3.5,
    "section 232": 3.5,
    "trade restriction": 2.5,
}

# 지정학적 긴급사태
URGENT_KEYWORDS_GEO: dict[str, float] = {
    # L1급
    "nuclear threat": 5.0,
    "nuclear attack": 5.0,
    "nuclear weapon": 4.5,
    "military strike": 5.0,
    "oil embargo": 4.5,
    "strait of hormuz": 4.5,
    "war declared": 5.0,
    # L2급 — 복합 표현만 수록 (단독 일반어 제외)
    "invasion": 3.5,
    "airstrike": 3.0,
    "missile attack": 3.5,
    "military escalation": 3.5,
    "geopolitical crisis": 3.5,
    "conflict escalation": 3.0,
}

# 미국 신용·재정 위기
URGENT_KEYWORDS_FISCAL: dict[str, float] = {
    # L1급
    "us credit downgrade": 5.0,
    "credit downgrade us": 5.0,
    "debt default": 5.0,
    "debt ceiling crisis": 4.5,
    "government shutdown begins": 4.5,
    "moody's downgrades": 4.0,
    "fitch downgrades": 4.0,
    "treasury auction fails": 4.5,
    "treasury auction tail": 4.0,
    # L2급
    "debt ceiling": 3.5,
    "government shutdown": 3.0,
    "credit downgrade": 3.0,
    "fiscal cliff": 3.0,
    "bond auction": 2.5,
    "deficit warning": 2.5,
}

# AI·반도체 규제 충격
URGENT_KEYWORDS_TECH_REG: dict[str, float] = {
    # L1급
    "chip export ban": 4.5,
    "semiconductor ban": 4.5,
    "semiconductor export ban": 4.5,
    "nvidia ban": 4.5,
    "tsmc restriction": 4.0,
    "chip export control": 4.0,
    "ai executive order": 4.0,
    # L2급
    "semiconductor restriction": 3.5,
    "chip restriction": 3.0,
    "entity list": 2.5,
    "antitrust ruling tech": 3.5,
    "big tech breakup": 3.5,
    "ai regulation": 2.0,
    "export control ai": 3.0,
}
# ────────────────────────────────────────────────────────
# B시스템 보완 키워드 v1.2.0 (2026-05-09)
# 추가 영역: 매크로 지표 / 중앙은행 / 실적 / 정책 일반
# 목적: A시스템(가격 기반)이 놓치는 선행 정보 신호 포착
# ────────────────────────────────────────────────────────

# 매크로 지표 발표 — A가 못 잡는 선행 신호
URGENT_KEYWORDS_MACRO_DATA: dict[str, float] = {
    # L2급
    "cpi report": 3.0,
    "inflation report": 3.0,
    "nonfarm payrolls": 3.5,
    "non-farm payrolls": 3.5,
    "jobs report": 2.5,
    "unemployment rate": 2.5,
    "ppi report": 2.5,
    "pce inflation": 3.0,
    "gdp report": 2.5,
    "ism manufacturing": 2.0,
    "retail sales": 2.0,
    "consumer confidence": 2.0,
    # L1급 — 시장 충격
    "inflation surge": 3.5,
    "hot cpi": 4.0,
    "jobs miss": 3.0,
    "jobs beat": 2.5,
}

# Fed/중앙은행 인물·일정 — A가 못 잡는 정책 신호
URGENT_KEYWORDS_CENTRAL_BANK: dict[str, float] = {
    "powell speech": 3.5,
    "powell testimony": 4.0,
    "powell remarks": 3.0,
    "fomc minutes": 3.0,
    "fomc meeting": 2.5,
    "fed dot plot": 3.0,
    "jackson hole": 3.5,
    "fed pivot": 4.0,
    "hawkish": 2.0,
    "dovish": 2.0,
    "ecb decision": 2.5,
    "boj decision": 2.5,
    "bank of japan": 2.5,
}

# 실적 시즌 — A는 가격 반응 후에야 잡음
URGENT_KEYWORDS_EARNINGS: dict[str, float] = {
    "earnings beat": 2.0,
    "earnings miss": 2.5,
    "guidance cut": 3.5,
    "guidance raised": 2.5,
    "revenue miss": 3.0,
    "revenue beat": 2.0,
    "profit warning": 4.0,
    "earnings season": 1.5,
    "preliminary results": 2.5,
}

# 정책·발표 일반 — 헤드라인 충격
URGENT_KEYWORDS_POLICY: dict[str, float] = {
    "trump speech": 3.0,
    "trump announces": 3.0,
    "white house statement": 2.5,
    "presidential order": 3.5,
    "executive order": 3.0,
    "treasury statement": 2.5,
    "fed statement": 3.0,
}

# 제목: 키워드 매칭 최소 임계값
# 내용: 이 점수 미만의 이벤트는 제외
KEYWORD_THRESHOLD: float = 2.0

# 제목: AI 분석 임계값
# 내용: keyword_score가 이 이상인 이벤트만 AI 분석 수행 (쿼터 절약)
AI_SCORE_MIN_KEYWORD: float = 2.5



# ────────────────────────────────────────────────────────
# v2 규칙 (Q2, 2026-10-12)
# ────────────────────────────────────────────────────────
# 제목: v2 키워드 임계값 / 일반어 단독 감쇠 / 지정학 결합 보너스
KEYWORD_THRESHOLD_V2: float = 3.0
GENERIC_SOLO_FACTOR: float = 0.3
GEO_COMBO_BONUS: float = 1.0
GENERIC_TOPIC: str = "generic"

# 제목: v2 핵심 규칙
# 내용: (rule_id, topic, weight, anchor, pattern). 모두 re.IGNORECASE.
#       anchor=True 규칙이 1개라도 매칭되면 generic 규칙은 정상 가중치, 아니면 ×GENERIC_SOLO_FACTOR.
_V2_CORE_RULES: list[tuple[str, str, float, bool, str]] = [
    # 금리·중앙은행
    ("fed_actor", "fed_policy", 1.0, True,
     r"\b(fed(?!\s+up\b)|fomc|federal reserve|powell|warsh|central banks?|ecb|boj|boe"
     r"|bank of japan|bank of england)\b"),
    ("rate_change", "fed_policy", 3.0, True,
     r"\b(rate|rates|interest[- ]rates?)\s+(cut|cuts|hike|hikes|increase|increases|decrease"
     r"|hold|holds)\b"),
    # 동사+rates: 중앙은행 앵커(fed_actor) 동반 시만 인정 (V2_RULE_REQUIRES)
    ("rate_change_v", "fed_policy", 3.0, True,
     r"\b(cut|cuts|cutting|hike|hikes|hiking|raise|raises|raising|lower|lowers|lowering"
     r"|holds?|holding|keeps?|leaves?)\s+(interest\s+)?rates?\b"),
    # 동사+interest rates 명시: 앵커 없이 인정 (rate_change_v 와 같은 그룹 — 1회만 가산)
    ("rate_change_vi", "fed_policy", 3.0, True,
     r"\b(cut|cuts|cutting|hike|hikes|hiking|raise|raises|raising|lower|lowers|lowering"
     r"|holds?|holding|keeps?|leaves?)\s+interest\s+rates?\b"),
    # 소비자 금리(모기지·카드 등): 약신호, 비앵커. 매칭 시 rate_change* 무효 (V2_RULE_EXCLUDED_BY)
    ("consumer_rates", "consumer_rates", 1.0, False,
     r"\b(mortgage|credit[- ]card|card|auto[- ]loan|car[- ]loan|student[- ]loan|deposit|savings"
     r"|cd)\s+(interest\s+)?rates?\b"),
    ("emergency_rate", "fed_policy", 3.0, True,
     r"\b(emergency|surprise|intermeeting)\s+(rate\s+)?(cut|hike)s?\b"),
    ("jackson", "fed_policy", 3.0, True, r"\bjackson hole\b"),
    ("fed_doc", "fed_policy", 2.0, True,
     r"\b(fomc|fed|ecb|boj|bank of japan)\s+(statement|decision|minutes|meeting|dot plot)\b"
     r"|\bpowell\s+(speech|testimony|remarks|says)\b"),
    ("fed_stance", "fed_policy", 2.0, False, r"\b(hawkish|dovish|pivot)\b"),
    # 국채 수익률
    ("yield_noun", "yields", 1.5, True,
     r"\b(treasury|bond|10-year|2-year|30-year)\s+(treasury\s+)?yields?\b"),
    ("yield_move", "yields", 2.0, True,
     r"\byields?\s+(jumps?|jumped|surges?|surged|spikes?|spiked|soars?|soared|climbs?|climbed"
     r"|rises?|rose|hits?|tumbles?|falls?|fell|plunges?)\b"),
    # 물가·고용·성장
    ("cpi_pce", "inflation", 3.0, True,
     r"\b(cpi|pce|ppi|core inflation|consumer prices?|inflation (report|data|reading))\b"),
    ("inflation", "inflation", 1.5, True, r"\binflation\b"),
    ("infl_move", "inflation", 1.5, True,
     r"\b(cpi|pce|ppi|inflation|prices)\s+(rises?|rose|jumps?|accelerates?|surges?|hotter"
     r"|cools?|slows?|stays sticky|sticky)\b|\b(hotter|cooler|more|less)\s+than\s+expected\b"),
    ("jobs", "jobs", 3.0, True,
     r"\b(jobs report|jobs data|jobs (miss|beat)|payrolls?|nonfarm|non-farm|jobless claims"
     r"|unemployment rate)\b"),
    ("gdp", "growth", 3.0, True, r"\bgdp\b"),
    ("recession", "growth", 3.5, True, r"\brecession\b"),
    # 에너지
    ("oil", "energy", 1.0, True, r"\b(oil|crude|brent|wti|opec)\b"),
    ("oil_move", "energy", 3.0, True,
     r"\b(oil|crude|brent|wti)\s+(prices?\s+)?(spikes?|spiked|surges?|surged|soars?|jumps?"
     r"|plunges?|tumbles?|crash(es)?)\b"),
    ("hormuz", "energy", 4.5, True, r"\b(strait of )?hormuz\b|\boil embargo\b"),
    # 지정학
    ("geo_attack", "geo_conflict", 1.5, False,
     r"\b(attacks?|attacked|drones?|missiles?|airstrikes?|bombing|explosions?)\b"),
    # strike 류: 노동 문맥 동반 시 제외 (V2_RULE_VETO), "strikes (a) deal" 류 제외
    ("geo_attack_s", "geo_conflict", 1.5, False,
     r"\b(strikes?|struck)\b(?!\s+(a\s+|an\s+)?(deal|agreement|accord|balance|tone|chord|out)\b)"),
    # 시설어: geo_attack(_s) 동시 매칭일 때만 점수·앵커 인정 (V2_RULE_REQUIRES)
    ("geo_target", "geo_conflict", 2.0, True,
     r"\b(airport|refinery|pipeline|oil facility|oil field|facility|tanker|embassy"
     r"|military base|port)\b"),
    ("geo_strike", "geo_conflict", 3.0, True,
     r"\b(airstrikes?|missile (strikes?|attacks?)|drone (strikes?|attacks?))\b"),
    ("geo_major", "geo_conflict", 4.5, True,
     r"\b(nuclear (threat|attack|weapon)|military strike|war declared|invasion|missile attack"
     r"|military escalation)\b"),
    # 항공 보안
    ("avsec", "aviation_security", 3.0, True,
     r"\bfaa\b.{0,40}\b(warns?|warning|bans?|halts?|grounds?|restricts?)\b"
     r"|\bairspace\s+(closed|closure|closes)\b|\b(flight|flights)\s+(suspended|halted|banned)\b"),
    # 시장
    ("index", "market", 1.0, False, r"\b(s&p 500|dow|nasdaq|wall street)\b"),
    ("index_move", "market", 3.0, True,
     r"\b(s&p 500|dow|nasdaq|stocks)\s+(drops?|falls?|plunges?|tumbles?|sinks?|slides?"
     r"|crash(es)?)\s+[\d,.]+\s*(%|points)"),
    ("halt", "market", 5.0, True, r"\b(circuit breaker|trading halt|market closed|flash crash)\b"),
    # 일반어 (단독 매칭 시 ×GENERIC_SOLO_FACTOR)
    ("g_emergency", GENERIC_TOPIC, 3.0, False, r"\bemergency\b"),
    ("g_crisis", GENERIC_TOPIC, 3.0, False, r"\bcrisis\b"),
    ("g_move", GENERIC_TOPIC, 2.0, False,
     r"\b(surges?|surged|plunges?|plunged|tumbles?|tumbled|soars?|soared|spikes?|spiked"
     r"|sell-off)\b"),
    ("g_signif", GENERIC_TOPIC, 1.0, False, r"\b(significant|unprecedented|concerns?)\b"),
]

# 제목: 규칙 간 의존성 (Q2 리뷰 M5 반영)
# 내용:
#   - V2_RULE_VETO[rid]: 텍스트에 문맥 패턴이 있으면 rid 매칭 무효
#   - V2_RULE_EXCLUDED_BY[rid]: 나열 규칙 중 하나라도 매칭되면 rid 무효
#     (단 V2_RULE_EXCLUSION_OVERRIDE[rid] 규칙이 매칭되면 배제하지 않음)
#   - V2_RULE_REQUIRES[rid]: 나열 규칙 중 하나 이상 (유효) 매칭돼야 rid 인정 (점수·앵커 모두)
#   - V2_RULE_GROUP[rid]: 같은 그룹은 1회만 가산(최대 가중치)
#   판정 범위 = 매칭 대상 텍스트 전체(뉴스 title + summary).
LABOR_CONTEXT_PATTERN: str = (
    r"\b(workers?|unions?|labou?r|employees|staff|walkouts?|picket\w*|rail|railway|dockworkers?"
    r"|teachers|nurses|drivers|pilots|machinists|teamsters|uaw|wage|contract talks)\b"
)
V2_RULE_VETO: dict[str, str] = {
    "geo_attack_s": LABOR_CONTEXT_PATTERN,
}
V2_RULE_EXCLUDED_BY: dict[str, tuple[str, ...]] = {
    "rate_change": ("consumer_rates",),
    "rate_change_vi": ("consumer_rates",),
}
# 제목: 배제 무효화 — 중앙은행 앵커가 같은 텍스트에 있으면 EXCLUDED_BY 를 적용하지 않음
#       (예: "Fed cuts rates; mortgage rates fall" 은 정책금리 기사로 인정)
V2_RULE_EXCLUSION_OVERRIDE: dict[str, tuple[str, ...]] = {
    "rate_change": ("fed_actor",),
    "rate_change_vi": ("fed_actor",),
}
V2_RULE_REQUIRES: dict[str, tuple[str, ...]] = {
    "geo_target": ("geo_attack", "geo_attack_s"),
    "rate_change_v": ("fed_actor",),
}
V2_RULE_GROUP: dict[str, str] = {
    "geo_attack_s": "geo_attack",
    "rate_change_vi": "rate_change_v",
}

# 제목: v1 사전 흡수 (토픽 매핑)
# 내용: 해당 v1 사전의 모든 키를 \b{키}(?:e?s)?\b 로 흡수. 가중치는 v1 값 그대로(동기화).
#       earnings 는 기업 개별 이슈이므로 앵커 아님(일반어 감쇠 대상 유지).
_V1_IMPORT_DICTS: list[tuple[dict[str, float], str, bool]] = [
    (URGENT_KEYWORDS_TARIFF, "tariff", True),
    (URGENT_KEYWORDS_FISCAL, "fiscal", True),
    (URGENT_KEYWORDS_TECH_REG, "tech_reg", True),
    (URGENT_KEYWORDS_EARNINGS, "earnings", False),
    (URGENT_KEYWORDS_POLICY, "policy", True),
]

# 제목: v1 개별 키 이월 (핵심 규칙에 없는 다단어 표현)
_V1_CARRY: list[tuple[str, str, float]] = [
    ("extraordinary measures", "fed_policy", 5.0),
    ("volatility spike", "market", 3.0),
    ("geopolitical crisis", "geo_conflict", 3.5),
    ("conflict escalation", "geo_conflict", 3.0),
    ("retail sales", "growth", 2.0),
    ("ism manufacturing", "growth", 2.0),
    ("consumer confidence", "growth", 2.0),
]


def _build_v2_rules() -> list[tuple[str, str, float, bool, str]]:
    """v2 전체 규칙 목록 조립 (핵심 + v1 사전 흡수 + v1 이월)."""
    rules = list(_V2_CORE_RULES)
    for source, topic, anchor in _V1_IMPORT_DICTS:
        for keyword, weight in source.items():
            rules.append((f"v1:{keyword}", topic, weight, anchor,
                          r"\b" + re.escape(keyword) + r"(?:e?s)?\b"))
    for keyword, topic, weight in _V1_CARRY:
        rules.append((f"v1c:{keyword}", topic, weight, True, r"\b" + re.escape(keyword) + r"\b"))
    return rules


V2_RULES: list[tuple[str, str, float, bool, str]] = _build_v2_rules()


# ────────────────────────────────────────────────────────
# YouTube 토큰 정규화 (v2 yt bonus 교집합용)
# ────────────────────────────────────────────────────────
# 제목: YouTube 키워드 → 공통 토픽 토큰
# 내용: youtube_collector.YOUTUBE_URGENT_KEYWORDS_KR 전 키(29개) 대응. None = 교집합 비대상.
#       조회는 casefold() 키 기준 (영문 대소문자 무시).
YT_TOKEN_MAP: dict[str, str | None] = {
    "FOMC": "fed_policy", "파월": "fed_policy", "긴축": "fed_policy", "완화": "fed_policy",
    "CPI": "inflation", "인플레": "inflation",
    "고용지표": "jobs",
    "관세": "tariff",
    "전쟁": "geo_conflict", "북한": "geo_conflict", "공격": "geo_conflict",
    "실적": "earnings", "어닝": "earnings",
    "서킷브레이커": "market", "거래정지": "market", "대폭락": "market", "폭락장": "market",
    "긴급": None, "속보": None, "급등": None, "급락": None, "위기": None, "경고": None,
    "위협": None, "버블": None, "조선": None, "주의보": None, "발표": None, "예고": None,
}
