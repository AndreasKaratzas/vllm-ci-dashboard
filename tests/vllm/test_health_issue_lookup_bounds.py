"""Structural guarantees for the bounded site-health issue index."""

from pathlib import Path

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
