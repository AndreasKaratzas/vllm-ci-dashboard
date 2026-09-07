from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github/workflows"


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def test_deploy_tries_previous_after_every_deterministic_candidate_gate() -> None:
    workflow = _workflow("deploy-pages.yml")
    steps = workflow["jobs"]["deploy"]["steps"]
    by_name = {step.get("name"): step for step in steps}
    reproduction = by_name["Reproduce current candidate failure in isolation"]
    fallback = by_name["Try fully deployable rollback candidate"]
    script = fallback["run"]

    assert "steps.current-local-projection.outcome != 'success'" in fallback["if"]
    assert "steps.current-reproduction.outputs.same_failure == 'true'" in fallback["if"]
    assert "steps.current-reproduction.outputs.ambiguous == 'false'" in fallback["if"]
    ordered_gates = (
        "dashboard_state.py\" \\",
        "build_operations_snapshot.py",
        "audit_dashboard_data.py",
        "scripts/build_site.py",
        "public_projection.py create",
        "write-public-marker",
        'public_projection.py" verify-local',
        "repair-slots",
    )
    positions = [script.index(gate) for gate in ordered_gates]
    assert positions == sorted(positions)
    assert '"$TRUSTED_DASHBOARD_VALIDATOR_DIR/public_projection.py" verify-local' in script
    assert script.index("git clean -ffdx") < script.index(
        "git checkout --force --detach"
    )
    assert '--current-sha "$DASHBOARD_STATE_SHA"' in script
    assert '--previous-sha "$DASHBOARD_PREVIOUS_STATE_SHA"' in script
    assert "Promoted fully deployable rollback state" in script

    dependency = by_name["Install state-pinned candidate dependencies"]["run"]
    assert '-c "$GITHUB_WORKSPACE/constraints.txt"' in dependency
    assert steps.index(reproduction) < steps.index(fallback) < steps.index(
        by_name["Deploy exact state to GitHub Pages"]
    )


def test_deploy_reproduces_the_same_stage_cleanly_before_rollback_authority() -> None:
    workflow = _workflow("deploy-pages.yml")
    steps = workflow["jobs"]["deploy"]["steps"]
    by_name = {step.get("name"): step for step in steps}
    reproduction = by_name["Reproduce current candidate failure in isolation"]
    script = reproduction["run"]

    assert reproduction["continue-on-error"] is True
    assert set(reproduction["env"]) == {
        "OPERATIONS_OUTCOME",
        "OPERATIONS_FAILED_STAGE",
        "OPERATIONS_FAILED_STATUS",
        "AUDIT_OUTCOME",
        "AUDIT_FAILED_STAGE",
        "AUDIT_FAILED_STATUS",
        "ASSEMBLE_OUTCOME",
        "ASSEMBLE_FAILED_STAGE",
        "ASSEMBLE_FAILED_STATUS",
        "MANIFEST_OUTCOME",
        "MANIFEST_FAILED_STAGE",
        "MANIFEST_FAILED_STATUS",
        "MARKER_OUTCOME",
        "MARKER_FAILED_STAGE",
        "MARKER_FAILED_STATUS",
        "PROJECTION_OUTCOME",
        "PROJECTION_FAILED_STAGE",
        "PROJECTION_FAILED_STATUS",
    }
    for stage in (
        "operations-timestamp",
        "operations-rebuild",
        "operations-diff",
        "audit",
        "assemble",
        "manifest",
        "marker",
        "projection",
    ):
        assert f"run_retry_stage {stage}" in script
    assert "OPERATIONS_FAILED_STAGE" in script
    assert "OPERATIONS_FAILED_STATUS" in script
    assert "Initial failure has an infrastructure-class status" in script
    assert "git worktree add --detach" in script
    assert '"$RETRY_ROOT" "$DASHBOARD_STATE_SHA"' in script
    assert "GIT_NO_LAZY_FETCH=1" in script
    assert '"$CANDIDATE_PYTHON"' in script
    assert "pip install" not in script
    assert "storage_healthy" in script
    assert "MemAvailable:" in script
    assert '"$status" -ge 128' in script
    assert '"$status" -eq 74' in script and '"$status" -eq 75' in script
    assert script.index('if [ "$REPRODUCED_STAGE" != "$ORIGINAL_FAILED_STAGE" ]') < (
        script.index('echo "same_failure=true"')
    )
    assert 'echo "ambiguous=true"' in script
    assert 'echo "current_recovered=true"' in script
    assert 'mv "$RETRY_ROOT/_site" "$GITHUB_WORKSPACE/_site"' in script
    assert "repair-slots" not in script

    preserve = by_name["Preserve only bounded PR previews"]
    deploy = by_name["Deploy exact state to GitHub Pages"]
    for step in (preserve, deploy):
        assert "steps.current-reproduction.outputs.current_recovered == 'true'" in step["if"]


def test_deploy_composes_and_certifies_bounded_previews_before_orphan_push() -> None:
    workflow = _workflow("deploy-pages.yml")
    steps = workflow["jobs"]["deploy"]["steps"]
    by_name = {step.get("name"): step for step in steps}
    preserve = by_name["Preserve only bounded PR previews"]
    script = preserve["run"]
    deploy = by_name["Deploy exact state to GitHub Pages"]

    assert "--materialize-prefix pr-preview/" in script
    assert "bound-pages-directory" in script
    assert "preview_status" in script and "exit 90" in script
    assert "DASHBOARD_PREVIEW_DIGEST" in script
    assert steps.index(preserve) < steps.index(deploy)
    assert "steps.preview-preservation.outcome == 'success'" in deploy["if"]
    assert deploy["with"]["keep_files"] is False
    assert deploy["with"]["force_orphan"] is True

    postdeploy = by_name["Post-deploy state and bundle validation"]["run"]
    recovery = by_name["Confirm exact-state recovery"]["run"]
    assert "--profile pages-orphan" in postdeploy
    assert "DEPLOYED_PREVIEW_DIGEST" in postdeploy
    assert '!= "$DASHBOARD_PREVIEW_DIGEST"' in postdeploy
    assert "--profile pages-orphan" in recovery
    assert 'RECOVERED_PREVIEW_DIGEST" = "$DASHBOARD_PREVIEW_DIGEST' in recovery

    redeploy = by_name["Redeploy exact state if validation failed"]
    assert redeploy["with"]["keep_files"] is False
    assert redeploy["with"]["force_orphan"] is True


def test_preview_publication_replaces_exact_bounded_root_not_append_only_branch() -> None:
    workflow = _workflow("pr-preview.yml")
    deploy_steps = workflow["jobs"]["deploy-preview"]["steps"]
    compose = next(
        step for step in deploy_steps if step.get("name") == "Compose exact bounded Pages tree"
    )["run"]
    publish = next(step for step in deploy_steps if "peaceiris/actions-gh-pages" in step.get("uses", ""))

    assert "--materialize-root \"$PAGES_NEXT\"" in compose
    assert "--protect-preview \"$PREVIEW_NAME\"" in compose
    assert "bound-pages-directory" in compose
    assert publish["with"]["publish_dir"] == "./trusted-base/_pages_publish"
    assert "destination_dir" not in publish["with"]
    assert publish["with"]["keep_files"] is False
    assert publish["with"]["force_orphan"] is True


def test_hourly_canonical_publish_preserves_and_certifies_exact_previews() -> None:
    workflow = _workflow("hourly-master.yml")
    steps = next(iter(workflow["jobs"].values()))["steps"]
    by_name = {step.get("name"): step for step in steps}
    preserve = by_name["Preserve only bounded PR previews"]
    deploy = by_name["Deploy to GitHub Pages"]
    script = preserve["run"]

    assert "--materialize-prefix pr-preview/" in script
    assert "bound-pages-directory" in script
    assert "DASHBOARD_PREVIEW_DIGEST" in script
    assert "BUILDKITE" not in script
    assert "steps.preview-preservation.outcome == 'success'" in deploy["if"]
    assert steps.index(preserve) < steps.index(deploy)
    assert deploy["with"]["keep_files"] is False
    assert deploy["with"]["force_orphan"] is True

    postdeploy = by_name["Post-deploy validation (check gh-pages for corruption)"]["run"]
    recovery = by_name["Confirm corruption recovery"]["run"]
    assert "--profile pages-orphan" in postdeploy
    assert 'DEPLOYED_PREVIEW_DIGEST" != "$DASHBOARD_PREVIEW_DIGEST' in postdeploy
    assert "--profile pages-orphan" in recovery
    assert 'RECOVERED_PREVIEW_DIGEST" = "$DASHBOARD_PREVIEW_DIGEST' in recovery

    redeploy = by_name["Redeploy if corrupted"]
    assert redeploy["with"]["keep_files"] is False
    assert redeploy["with"]["force_orphan"] is True
