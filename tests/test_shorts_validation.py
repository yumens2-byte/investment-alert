from __future__ import annotations

from dataclasses import replace

from shorts.domain.models import Scene, Script
from shorts.pilot import build_pilot_inputs
from shorts.validation.script_validator import validate_script


def test_pilot_script_passes_hard_gates() -> None:
    fact_pack, script = build_pilot_inputs()
    result = validate_script(fact_pack, script)
    assert result.passed is True
    assert result.issues == ()


def test_unknown_evidence_fails() -> None:
    fact_pack, script = build_pilot_inputs()
    bad_scene = replace(script.scenes[0], evidence_ids=("UNKNOWN",))
    result = validate_script(fact_pack, replace(script, scenes=(bad_scene, *script.scenes[1:])))
    assert "UNKNOWN_EVIDENCE" in {issue.code for issue in result.issues}


def test_fact_without_evidence_fails() -> None:
    fact_pack, script = build_pilot_inputs()
    bad_scene = replace(script.scenes[0], evidence_ids=())
    result = validate_script(fact_pack, replace(script, scenes=(bad_scene, *script.scenes[1:])))
    assert "EVIDENCE_REQUIRED" in {issue.code for issue in result.issues}


def test_unlabelled_hypothesis_fails() -> None:
    fact_pack, script = build_pilot_inputs()
    bad_scene = replace(script.scenes[2], narration="시차를 놓치면 늦게 보입니다", subtitle="늦습니다")
    scenes = (*script.scenes[:2], bad_scene, *script.scenes[3:])
    result = validate_script(fact_pack, replace(script, scenes=scenes))
    assert "HYPOTHESIS_LABEL" in {issue.code for issue in result.issues}


def test_franchise_term_fails() -> None:
    fact_pack, script = build_pilot_inputs()
    result = validate_script(fact_pack, replace(script, title="마블 스타일 시장"))
    assert "IP_TERM" in {issue.code for issue in result.issues}


def test_financial_advice_fails() -> None:
    fact_pack, script = build_pilot_inputs()
    result = validate_script(fact_pack, replace(script, hook="지금 사세요"))
    assert "FINANCIAL_ADVICE" in {issue.code for issue in result.issues}


def test_duration_and_scene_count_fail() -> None:
    fact_pack, script = build_pilot_inputs()
    short_scene = Scene(
        index=1,
        duration_ms=1_000,
        narration="고지",
        subtitle="고지",
        visual_prompt="original",
        claim_type="DISCLAIMER",
    )
    bad_script = Script("제목", "훅", (short_scene,), "설명", ("#Shorts",))
    result = validate_script(fact_pack, bad_script)
    assert {"DURATION", "SCENE_COUNT"}.issubset({issue.code for issue in result.issues})
