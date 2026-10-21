"""Structural guarantees for the bounded site-health issue index."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]


def _reconcile_script() -> str:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "health-check.yml").read_text()
    )
    steps = workflow["jobs"]["check"]["steps"]
    return next(
        step["with"]["script"]
        for step in steps
        if step.get("name") == "Reconcile marker-owned site health issue"
    )


def test_site_health_owner_label_is_the_one_request_common_path():
    script = _reconcile_script()
    conditional = script[
        script.index("if (indexedOwned.length === 0)") : script.index(
            "const owned = indexedOwned"
        )
    ]

    assert conditional.count("github.rest.issues.listForRepo") == 1
    assert "sort: 'updated'" in conditional
    assert "const recovered" in conditional
    assert "recentResponse.data.length >= 100 && recovered.length === 0" in (
        conditional
    )


def test_site_health_completes_bounded_discovery_before_first_mutation():
    script = _reconcile_script()
    discovery_complete = script.index("const existing = owned[0] || null")

    for mutation in (
        "await github.rest.issues.createLabel",
        "await github.rest.issues.update",
        "await github.rest.issues.create(",
        "await github.rest.issues.addLabels",
    ):
        assert discovery_complete < script.index(mutation)


MARKER = "<!-- vllm-ci-dashboard:site-health:v1 -->"
NODE = shutil.which("node")
HARNESS = r"""
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const calls = [];
const record = (name, result) => async options => {
  calls.push([name, options]);
  if (name === 'getLabel' && !input.labels.includes(options.name)) {
    throw Object.assign(new Error('missing'), {status: 404});
  }
  return result;
};
const github = {rest: {issues: {
  listForRepo: record('listForRepo', {data: input.issues}),
  getLabel: record('getLabel', {}),
  createLabel: record('createLabel', {}),
  update: record('update', {}),
  addLabels: record('addLabels', {}),
  addAssignees: record('addAssignees', {}),
  create: record('create', {data: {number: 2}}),
}}};
const core = {info() {}, warning() {}, setOutput() {}};
const context = {repo: {owner: 'owner', repo: 'dashboard'}};
const AsyncFunction = Object.getPrototypeOf(async function() {}).constructor;
new AsyncFunction('require', 'github', 'context', 'core', input.script)(
  require, github, context, core,
).then(() => process.stdout.write(JSON.stringify(calls)));
"""


def _reconcile(
    tmp_path,
    *,
    healthy,
    issues,
    labels=("site-health", "automated"),
    automation_observed=True,
):
    assert NODE, "Node is required to execute the actual reconciliation script"
    body_path = tmp_path / "body.md"
    body_path.write_text(
        f"{MARKER}\n<!-- SITE_HEALTH_STATE -->\n<!-- SITE_HEALTH_RECOVERY_NOTE -->\n"
    )
    completed = subprocess.run(
        [NODE, "-e", HARNESS],
        input=json.dumps(
            {"script": _reconcile_script(), "issues": issues, "labels": list(labels)}
        ),
        env={
            **os.environ,
            "HEALTHY": str(healthy).lower(),
            "CONFIRMED": "true",
            "AUTOMATION_OBSERVED": str(automation_observed).lower(),
            "NORMALIZE_OUTCOME": "success",
            "BODY_PATH": str(body_path),
            "OWNERSHIP_MARKER": MARKER,
        },
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
    )
    return json.loads(completed.stdout)


def _calls(calls, name):
    return [options for call, options in calls if call == name]


@pytest.mark.parametrize(
    ("healthy", "state", "assigned"),
    [(False, "closed", True), (False, "open", False), (True, "open", False)],
)
def test_site_health_assigns_owner_only_on_transition_to_unhealthy(
    tmp_path, healthy, state, assigned
):
    issue = {"number": 486, "state": state, "body": MARKER}

    calls = _reconcile(tmp_path, healthy=healthy, issues=[issue])

    assert _calls(calls, "addLabels") == [
        {
            "owner": "owner",
            "repo": "dashboard",
            "issue_number": 486,
            "labels": ["site-health", "automated"],
        }
    ]
    assert _calls(calls, "addAssignees") == (
        [
            {
                "owner": "owner",
                "repo": "dashboard",
                "issue_number": 486,
                "assignees": ["owner"],
            }
        ]
        if assigned
        else []
    )


def test_new_site_health_incident_is_assigned_and_labelled_automated(tmp_path):
    calls = _reconcile(tmp_path, healthy=False, issues=[], labels=("site-health",))

    assert [options["name"] for options in _calls(calls, "createLabel")] == [
        "automated"
    ]
    [created] = _calls(calls, "create")
    assert created["labels"] == ["site-health", "automated"]
    assert created["assignees"] == ["owner"]


def test_unobserved_automation_does_not_close_an_open_incident(tmp_path):
    issue = {"number": 486, "state": "open", "body": MARKER}

    calls = _reconcile(
        tmp_path, healthy=True, issues=[issue], automation_observed=False
    )

    assert calls == []
