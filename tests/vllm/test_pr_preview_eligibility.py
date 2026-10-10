"""Execute the privileged preview's inline current-PR guards without GitHub calls."""

import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github/workflows/pr-preview.yml"
LOCAL_NODE = Path("/tmp/node-v22.18.0-linux-x64/bin/node")
NODE = str(LOCAL_NODE) if LOCAL_NODE.is_file() else shutil.which("node")
GUARD_IDS = (
    "preview-start",
    "preview-compose-current",
    "preview-publish-current",
    "preview-comment-current",
)
HEAD_SHA = "a" * 40
BASE_SHA = "b" * 40
REPOSITORY = "owner/dashboard"


def _steps():
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]["deploy-preview"]["steps"]


def _current():
    return {
        "number": 697,
        "state": "open",
        "merged": False,
        "author_association": "COLLABORATOR",
        "head": {"sha": HEAD_SHA, "repo": {"full_name": REPOSITORY}},
        "base": {"sha": BASE_SHA, "repo": {"full_name": REPOSITORY}},
    }


def _execute_guard(response=None, *, api_error=False, event=None, guard_id=GUARD_IDS[0]):
    assert NODE, "Node is required to execute the actual privileged preview guard"
    script = next(step for step in _steps() if step.get("id") == guard_id)["with"]["script"]
    payload = {
        "script": script,
        "context": {
            "repo": {"owner": "owner", "repo": "dashboard"},
            "payload": {"pull_request": _current() if event is None else event},
        },
        "response": {"status": 200, "data": _current()} if response is None else response,
        "api_error": api_error,
    }
    harness = r"""
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const result = {outputs: {}, calls: [], messages: [], error: null};
const core = {
  setOutput: (key, value) => { result.outputs[key] = value; },
  info: message => { result.messages.push(message); }
};
const github = {rest: {pulls: {get: async options => {
  result.calls.push(options);
  if (input.api_error) throw new Error('PROVIDER_SECRET must not reach output');
  return input.response;
}}}};
const AsyncFunction = Object.getPrototypeOf(async function() {}).constructor;
(async () => {
  try {
    await new AsyncFunction('github', 'context', 'core', input.script)(github, input.context, core);
  } catch (error) {
    result.error = error.message;
  }
  process.stdout.write(JSON.stringify(result));
})();
"""
    completed = subprocess.run(
        [NODE, "-e", harness],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
    )
    return json.loads(completed.stdout)


@pytest.mark.parametrize("guard_id", GUARD_IDS)
def test_each_publication_boundary_checks_exact_captured_current_pr(guard_id):
    result = _execute_guard(guard_id=guard_id)
    assert result["outputs"] == {"eligible": "true"}
    assert result["error"] is None
    assert result["calls"] == [{"owner": "owner", "repo": "dashboard", "pull_number": 697}]


@pytest.mark.parametrize(
    "change",
    ["closed", "merged", "head_changed", "base_changed", "fork", "base_repo", "untrusted"],
)
def test_stale_or_untrusted_current_pr_is_a_successful_noop(change):
    current = _current()
    if change == "closed":
        current["state"] = "closed"
        # Merged/deleted branches need no remaining head repository to skip.
        current["head"] = None
    elif change == "merged":
        current["merged"] = True
    elif change == "head_changed":
        current["head"]["sha"] = "c" * 40
    elif change == "base_changed":
        current["base"]["sha"] = "c" * 40
    elif change == "fork":
        current["head"]["repo"]["full_name"] = "fork/dashboard"
    elif change == "base_repo":
        current["base"]["repo"]["full_name"] = "other/dashboard"
    else:
        current["author_association"] = "CONTRIBUTOR"
    result = _execute_guard({"status": 200, "data": current})
    assert result["outputs"] == {"eligible": "false"}
    assert result["error"] is None
    assert len(result["calls"]) == 1


@pytest.mark.parametrize(
    "change",
    ["api_error", "http_404", "missing_data", "wrong_number", "state", "merged",
     "short_head", "missing_base", "missing_repo", "missing_trust"],
)
def test_unknown_current_pr_fails_closed_without_provider_error_details(change):
    current = _current()
    response = {"status": 200, "data": current}
    if change == "http_404":
        response["status"] = 404
    elif change == "missing_data":
        response["data"] = None
    elif change == "wrong_number":
        current["number"] += 1
    elif change == "state":
        current["state"] = "unknown"
    elif change == "merged":
        del current["merged"]
    elif change == "short_head":
        current["head"]["sha"] = "a" * 12
    elif change == "missing_base":
        del current["base"]
    elif change == "missing_repo":
        del current["head"]["repo"]
    elif change == "missing_trust":
        del current["author_association"]
    result = _execute_guard(response, api_error=change == "api_error")
    assert result["outputs"] == {"eligible": "false"}
    assert result["error"]
    assert "PROVIDER_SECRET" not in json.dumps(result)


@pytest.mark.parametrize("change", ["short_head", "missing_base", "fork", "untrusted", "number"])
def test_invalid_captured_event_never_requests_or_publishes(change):
    event = _current()
    if change == "short_head":
        event["head"]["sha"] = "a" * 12
    elif change == "missing_base":
        del event["base"]
    elif change == "fork":
        event["head"]["repo"]["full_name"] = "fork/dashboard"
    elif change == "untrusted":
        event["author_association"] = "CONTRIBUTOR"
    else:
        event["number"] = "697"
    result = _execute_guard(event=event)
    assert result["outputs"] == {"eligible": "false"}
    assert result["error"]
    assert result["calls"] == []


def test_all_preview_work_is_guarded_after_pages_lease_and_sources_are_immutable():
    workflow = yaml.safe_load(WORKFLOW.read_text())
    job = workflow["jobs"]["deploy-preview"]
    assert job["concurrency"] == {
        "group": "gh-pages-deploy", "queue": "max", "cancel-in-progress": False,
    }
    steps = job["steps"]
    assert steps[0]["id"] == "preview-start"
    for step in steps[1:]:
        assert "steps.preview-start.outputs.eligible == 'true'" in step["if"]
        assert "always()" not in step["if"]
    guards = [step for step in steps if step.get("id") in GUARD_IDS]
    assert len({step["with"]["script"] for step in guards}) == 1
    for index, guard in enumerate(guards):
        assert guard["uses"] == "actions/github-script@3a2844b7e9c422d3c10d287c895573f7108da1b3"  # pinned action commit SHA
        assert "${{" not in guard["with"]["script"]
        for prior in GUARD_IDS[:index]:
            assert f"steps.{prior}.outputs.eligible == 'true'" in guard["if"]
    trusted = next(step for step in steps if step.get("name") == "Checkout immutable trusted base")
    static = next(step for step in steps if step.get("name") == "Checkout pull request as static input")
    assert trusted["with"]["ref"] == "${{ github.event.pull_request.base.sha }}"
    assert static["with"]["ref"] == "${{ github.event.pull_request.head.sha }}"
    assert trusted["with"]["persist-credentials"] is False
    assert static["with"]["persist-credentials"] is False
    for name, guard_id in [
        ("Compose exact bounded Pages tree", "preview-compose-current"),
        (None, "preview-publish-current"),
        ("Comment preview URL", "preview-comment-current"),
    ]:
        index = next(
            i for i, step in enumerate(steps)
            if (step.get("id") == "preview-publish" if name is None else step.get("name") == name)
        )
        assert steps[index - 1]["id"] == guard_id
        assert f"steps.{guard_id}.outputs.eligible == 'true'" in steps[index]["if"]
    comment_guard = next(step for step in guards if step["id"] == "preview-comment-current")
    assert "steps.preview-publish.outcome == 'success'" in comment_guard["if"]


@pytest.mark.parametrize("stale_boundary", GUARD_IDS)
def test_current_pr_changing_during_wait_or_build_stops_remaining_side_effects(stale_boundary):
    reached = []
    # Execute the actual guards at each ordered side-effect boundary.
    for guard_id, side_effect in zip(GUARD_IDS, ["checkout", "compose", "publish", "comment"]):
        current = _current()
        if guard_id == stale_boundary:
            current["state"] = "closed"
        result = _execute_guard({"status": 200, "data": current}, guard_id=guard_id)
        assert result["error"] is None
        if result["outputs"]["eligible"] != "true":
            break
        reached.append(side_effect)
    expected = ["checkout", "compose", "publish", "comment"][:GUARD_IDS.index(stale_boundary)]
    assert reached == expected


def test_changed_head_after_build_and_api_failure_before_publish_stop_publication():
    assert _execute_guard()["outputs"]["eligible"] == "true"
    current = copy.deepcopy(_current())
    current["head"]["sha"] = "d" * 40
    result = _execute_guard({"status": 200, "data": current}, guard_id="preview-compose-current")
    assert result["outputs"]["eligible"] == "false"
    assert result["error"] is None
    result = _execute_guard(api_error=True, guard_id="preview-publish-current")
    assert result["outputs"]["eligible"] == "false"
    assert result["error"]
