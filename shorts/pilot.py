"""외부 API와 공개 업로드를 호출하지 않는 Phase 1 pilot."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from shorts.domain.models import (
    ContentMode,
    Evidence,
    FactPack,
    JobState,
    PilotManifest,
    Scene,
    Script,
    SlotName,
)
from shorts.rendering.ffmpeg_renderer import render_pilot
from shorts.scheduling.dispatcher import current_utc, due_slot
from shorts.validation.script_validator import validate_script


def build_pilot_inputs(now: datetime | None = None) -> tuple[FactPack, Script]:
    """사실 수치를 주장하지 않는 deterministic 휴장일용 샘플을 만든다."""
    now = now or datetime.now(UTC)
    evidence = Evidence(
        id="F1",
        claim="미국 주식시장은 정규 거래일에 동부시간을 기준으로 운영된다.",
        source_url="https://www.nyse.com/markets/hours-calendars",
        observed_at=now,
    )
    fact_pack = FactPack(
        schema_version="1.0",
        as_of=now,
        market_status="PILOT",
        topic_key="pilot:market-clock",
        facts=(evidence,),
    )
    scene_specs = (
        ("FACT", "오늘 시장의 시계부터 확인합니다.", ("F1",)),
        ("FACT", "미국 시장은 동부 현지시각을 기준으로 움직입니다.", ("F1",)),
        ("HYPOTHESIS", "만약 시차를 놓치면 같은 뉴스도 늦게 보일 수 있습니다.", ()),
        ("FACT", "그래서 오전과 밤, 두 번 분위기를 정리합니다.", ("F1",)),
        ("HYPOTHESIS", "가정과 실제 사실은 화면에서 분리해 설명합니다.", ()),
        ("DISCLAIMER", "이 영상은 시스템 파일럿이며 투자 조언이 아닙니다.", ()),
    )
    scenes = tuple(
        Scene(
            index=index,
            duration_ms=5_000,
            narration=narration,
            subtitle=narration,
            visual_prompt="original abstract financial motion panel, no logo, no franchise",
            claim_type=claim_type,
            evidence_ids=evidence_ids,
        )
        for index, (claim_type, narration, evidence_ids) in enumerate(scene_specs, start=1)
    )
    script = Script(
        title="미국 시장의 시계는 언제 움직일까?",
        hook=scenes[0].narration,
        scenes=scenes,
        description=(
            "미국 시장 시간대 자동화 파일럿입니다.\n"
            "출처: NYSE Hours & Calendars\n정보 제공 목적이며 투자 조언이 아닙니다."
        ),
        hashtags=("#미국증시", "#Shorts"),
    )
    return fact_pack, script


def run_pilot(output_dir: Path, render_video: bool = True) -> Path:
    """검증 결과, 원본 입력, 선택적 MP4를 보존하고 manifest 경로를 반환한다."""
    output_dir.mkdir(parents=True, exist_ok=True)
    fact_pack, script = build_pilot_inputs()
    validation = validate_script(fact_pack, script)
    if not validation.passed:
        codes = ", ".join(issue.code for issue in validation.issues)
        raise RuntimeError(f"pilot hard gate 실패: {codes}")

    source_hash = hashlib.sha256(repr((fact_pack, script)).encode()).hexdigest()[:16]
    content_id = f"pilot:{source_hash}"
    video_path = output_dir / "pilot_short.mp4"
    state = JobState.VALIDATED
    if render_video:
        render_pilot(script, video_path)
        state = JobState.RENDERED

    manifest = PilotManifest(
        content_id=content_id,
        slot=SlotName.MORNING,
        mode=ContentMode.EVERGREEN,
        state=state,
        fact_pack=fact_pack,
        script=script,
        validation=validation,
        video_path=str(video_path) if render_video else None,
        metadata={
            "dry_run": True,
            "upload_attempted": False,
            "bgm": "none",
            "company_marks": [],
            "privacy_status": None,
        },
    )
    manifest_path = output_dir / "pilot_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest_path


def run_due_pilot(
    output_root: Path,
    now: datetime | None = None,
    render_video: bool = True,
) -> Path | None:
    """현재 시각이 운영 슬롯 window인 경우에만 격리된 pilot을 실행한다."""
    claim = due_slot(now or current_utc())
    if claim is None:
        return None
    output_dir = output_root / claim.local_time.date().isoformat() / claim.slot.value
    manifest_path = run_pilot(output_dir, render_video=render_video)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["content_id"] = claim.content_id
    payload["slot"] = claim.slot.value
    payload["mode"] = claim.mode.value
    payload["metadata"]["scheduled_local_time"] = claim.local_time.isoformat()
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path
