"""
제목: .github/workflows/alert.yml 회귀 검사 (v1.4.0 P1/P3/P4)
내용: PyYAML 이 requirements 에 없으므로(venv311 미설치 확인) 텍스트 기반으로 검사한다.
      cron / gate job / alert job needs·if / DRY_RUN 식 / step 순서 / step id 를 고정한다.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).parent.parent / ".github/workflows/alert.yml"


@pytest.fixture(scope="module")
def text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _job_block(text: str, job: str) -> str:
    """jobs: 아래 2칸 들여쓰기 job 블록 추출 (다음 2칸 들여쓰기 key 전까지)."""
    match = re.search(rf"^  {job}:\n(.*?)(?=^  [A-Za-z_-]+:\n|\Z)", text, re.MULTILINE | re.DOTALL)
    assert match, f"job {job} 없음"
    return match.group(1)


def _step_names(block: str) -> list[str]:
    return re.findall(r"^      - name: (.+)$", block, re.MULTILINE)


@pytest.mark.unit
def test_on_key_quoted_and_cron(text) -> None:
    assert '"on":' in text
    crons = re.findall(r"^\s+- cron: '([^']+)'$", text, re.MULTILINE)
    assert crons == ["21,33 13-15,19-20 * * 1-5"]
    assert "workflow_dispatch:" in text


@pytest.mark.unit
def test_window_env_is_explicit(text) -> None:
    assert "ALERT_WINDOWS: '09:30,10:30,15:30'" in text
    assert "ALERT_GRACE_MINUTES: '12'" in text
    assert "ALERT_TZ: 'America/New_York'" in text


@pytest.mark.unit
def test_gate_job(text) -> None:
    gate = _job_block(text, "gate")
    assert "python -m core.alert_window --margin-sec 210" in gate
    assert "id: decide" in gate
    for key in ("inside", "target", "et_date"):
        assert f"{key}: ${{{{ steps.decide.outputs.{key} }}}}" in gate
    code_lines = [ln for ln in gate.splitlines() if not ln.strip().startswith("#")]
    assert not any("pip install" in ln for ln in code_lines)  # stdlib only


@pytest.mark.unit
def test_alert_job_needs_and_if(text) -> None:
    alert = _job_block(text, "alert")
    assert re.search(r"^    needs: gate$", alert, re.MULTILINE)
    assert (
        "if: ${{ !cancelled() && (github.event_name == 'workflow_dispatch' "
        "|| needs.gate.outputs.inside == 'true') }}"
    ) in alert


@pytest.mark.unit
def test_dry_run_expression(text) -> None:
    alert = _job_block(text, "alert")
    assert (
        "DRY_RUN: ${{ github.event_name == 'workflow_dispatch' "
        "&& github.event.inputs.dry_run || 'false' }}"
    ) in alert


@pytest.mark.unit
def test_pipeline_step_id_and_pipefail(text) -> None:
    alert = _job_block(text, "alert")
    step = alert.split("      - name: Run Alert Pipeline\n")[1].split("      - name:")[0]
    assert "id: alert" in step
    assert "set -o pipefail" in step
    assert "python run_alert.py" in step


@pytest.mark.unit
def test_diagnostics_runs_after_pipeline(text) -> None:
    names = _step_names(_job_block(text, "alert"))
    assert names.index("Run Diagnostics") > names.index("Run Alert Pipeline")
    assert names.index("Run Diagnostics") < names.index("Upload log artifact")


@pytest.mark.unit
def test_diagnostics_step_always_and_non_blocking(text) -> None:
    alert = _job_block(text, "alert")
    step = alert.split("      - name: Run Diagnostics\n")[1].split("      - name:")[0]
    assert (
        "if: ${{ always() && (github.event_name == 'workflow_dispatch' "
        "|| needs.gate.outputs.inside == 'true') }}"
    ) in step
    assert "continue-on-error: true" in step


@pytest.mark.unit
def test_detection_policy_env_from_vars(text) -> None:
    """Q2: alert job env 에 DETECTION_POLICY 를 repository vars 로 전달 (미설정=빈 문자열 → v1)."""
    alert = _job_block(text, "alert")
    assert re.search(r"^      DETECTION_POLICY: \$\{\{ vars\.DETECTION_POLICY \}\}", alert, re.MULTILINE)
