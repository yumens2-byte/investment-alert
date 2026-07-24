"""LLM 출력에 의존하지 않는 대본 hard gate."""

from __future__ import annotations

from shorts.domain.models import FactPack, Script, ValidationIssue, ValidationResult

_FORBIDDEN_IP = ("marvel", "what if", "avengers", "disney", "마블", "어벤져스")
_FORBIDDEN_ADVICE = ("매수하세요", "사세요", "매도하세요", "수익 보장", "무조건 오릅니다")


def validate_script(
    fact_pack: FactPack,
    script: Script,
    min_duration_ms: int = 27_000,
    max_duration_ms: int = 32_000,
) -> ValidationResult:
    issues: list[ValidationIssue] = []
    evidence_ids = {fact.id for fact in fact_pack.facts}
    combined = " ".join(
        [script.title, script.hook, script.description]
        + [f"{scene.narration} {scene.subtitle} {scene.visual_prompt}" for scene in script.scenes]
    ).lower()

    if not min_duration_ms <= script.duration_ms <= max_duration_ms:
        issues.append(ValidationIssue("DURATION", f"duration_ms={script.duration_ms}"))
    if not 5 <= len(script.scenes) <= 7:
        issues.append(ValidationIssue("SCENE_COUNT", f"scenes={len(script.scenes)}"))
    if any(term in combined for term in _FORBIDDEN_IP):
        issues.append(ValidationIssue("IP_TERM", "금지된 프랜차이즈 표현이 포함되었습니다"))
    if any(term in combined for term in _FORBIDDEN_ADVICE):
        issues.append(ValidationIssue("FINANCIAL_ADVICE", "투자 권유 표현이 포함되었습니다"))

    for scene in script.scenes:
        if scene.claim_type == "FACT":
            if not scene.evidence_ids:
                issues.append(ValidationIssue("EVIDENCE_REQUIRED", f"scene={scene.index}"))
            unknown = set(scene.evidence_ids) - evidence_ids
            if unknown:
                issues.append(
                    ValidationIssue("UNKNOWN_EVIDENCE", f"scene={scene.index}, ids={sorted(unknown)}")
                )
        elif scene.claim_type == "HYPOTHESIS":
            text = f"{scene.narration} {scene.subtitle}"
            if "만약" not in text and "가정" not in text:
                issues.append(ValidationIssue("HYPOTHESIS_LABEL", f"scene={scene.index}"))
        elif scene.claim_type != "DISCLAIMER":
            issues.append(ValidationIssue("CLAIM_TYPE", f"scene={scene.index}"))

    return ValidationResult(passed=not issues, issues=tuple(issues))
