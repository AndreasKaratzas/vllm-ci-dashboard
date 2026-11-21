# cspell:ignore heka elts

"""heka's ticket collectors read the fields in config/consumer_contracts/heka_tickets_v1.json.

Path syntax: `a.b` keys, `a[]` every list item, `*` every value of an object, `a?` skips a null.
"""

from __future__ import annotations

import ast
import inspect
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.vllm import test_agent_health as agent_tests
from tests.vllm import test_duration_regression_watcher as duration_tests
from tests.vllm import test_main_ci_monitoring as main_ci_tests
from tests.vllm import test_main_pulse as pulse_tests
from tests.vllm import test_test_group_parity as parity_tests
from tests.vllm.test_agent_health import offline_runtime_source_scope  # noqa: F401
from tests.vllm.test_test_group_parity import isolated_runtime_source  # noqa: F401
from vllm import amd_main_failure_watcher as amd
from vllm import build_queue_section
from vllm import build_test_group_parity as parity
from vllm import check_site_health as health
from vllm import collect_agent_health as agent_health
from vllm import collect_queue_snapshot as cqs
from vllm import main_ci_section
from vllm.ci import main_pulse_overlay
from vllm.constants import AGENT_HEALTH_ATTRIBUTIONS
from vllm.operations_bundle_contract import OPERATIONS_SECTION_NAMES
from vllm.publication_surfaces import PUBLICATION_SURFACE_LABELS
from vllm.reviewed_definition_labels import resolve_reviewed_definition_labels

CONTRACT = json.loads(
    (Path(__file__).resolve().parents[2] / "config/consumer_contracts/heka_tickets_v1.json").read_text()
)
SOURCES = CONTRACT["sources"]
NOT_OPERATIONS_SECTIONS = {"publication_status", "main_pulse"}


def walk(node, path: str):
    """Yield `(None, value)` for each value `path` reaches, else `(problem, None)`."""
    return _walk(node, path.split("."), "")


def _walk(node, segments, prefix):
    raw, rest = segments[0], segments[1:]
    each = raw.endswith("[]")
    key = raw.removesuffix("[]")
    nullable = key.endswith("?")
    key = key.removesuffix("?")
    where = f"{prefix}.{key}" if prefix else key
    if not isinstance(node, dict):
        yield f"{prefix or 'payload'} is not an object", None
        return
    if key != "*" and key not in node:
        yield f"{where} missing", None
        return
    where += "?" if nullable else ""
    for value in node.values() if key == "*" else [node[key]]:
        if value is None and nullable:
            continue
        if each and not isinstance(value, list):
            yield f"{where} is not a list", None
            continue
        item_where = f"{where}[]" if each else where
        for item in value if each else [value]:
            if not rest:
                yield None, item
            elif item is None:
                yield f"{item_where} is null", None
            else:
                yield from _walk(item, rest, item_where)


def _assert_paths(source: str, payloads: list[dict]) -> None:
    spec = SOURCES[source]
    problems, unreached = [], []
    for path in spec["required"]:
        results = [result for payload in payloads for result in walk(payload, path)]
        problems += sorted({problem for problem, _ in results if problem})
        if not any(problem is None for problem, _ in results):
            unreached.append(path)
    for path, allowed in spec.get("enums", {}).items():
        values = {repr(value) for payload in payloads for problem, value in walk(payload, path) if problem is None}
        problems += [f"{path} unknown value {value}" for value in sorted(values - {repr(v) for v in allowed})]
    assert problems == []
    # A path over an empty list passes vacuously, so every path must reach a real value.
    assert unreached == []


def _ts(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_contract_sections_and_surfaces_are_published():
    assert CONTRACT["contract"] == "heka_tickets_v1" and CONTRACT["contract_version"] == 1
    for name, spec in SOURCES.items():
        if name in NOT_OPERATIONS_SECTIONS:
            assert "wrapper" not in spec
            continue
        assert name in OPERATIONS_SECTION_NAMES
        assert spec["wrapper"] == name
        assert spec["freshness"]["path"] in spec["required"]
    for key, label in CONTRACT["publication_surfaces"].items():
        assert PUBLICATION_SURFACE_LABELS[key] == label
    assert {surface for spec in SOURCES.values() for surface in spec.get("surfaces", [])} <= set(
        CONTRACT["publication_surfaces"]
    )
    assert set(SOURCES["publication_status"]["required"]) <= health.PUBLICATION_STATUS_FIELDS


def _literal_strings(function, target_names: set[str]) -> set[str]:
    """String constants a function assigns to any of ``target_names`` (tuples by position)."""
    found = set()
    for node in ast.walk(ast.parse(inspect.getsource(function))):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in target_names for target in node.targets
        ):
            found |= {
                leaf.value for leaf in ast.walk(node.value)
                if isinstance(leaf, ast.Constant) and isinstance(leaf.value, str)
            }
    return found


def _verdict_reasons() -> set[str]:
    reasons = set()
    for node in ast.walk(ast.parse(inspect.getsource(agent_health._attribute_failures))):
        if (
            isinstance(node, ast.Tuple)
            and len(node.elts) == 3
            and isinstance(node.elts[2], ast.Constant)
            and isinstance(node.elts[2].value, str)
        ):
            reasons.add(node.elts[2].value)
    return reasons


def test_contract_enums_match_their_producers():
    assert set(SOURCES["test_group_parity"]["enums"]["groups[].state"]) == _literal_strings(
        parity.build_payload, {"state"}
    )
    agent_enums = SOURCES["amd_agent_health"]["enums"]
    assert set(agent_enums["failing_runs[].a"]) == set(AGENT_HEALTH_ATTRIBUTIONS)
    assert set(agent_enums["failing_runs[].ar"]) == _verdict_reasons()
    assert set(SOURCES["main_pulse"]["enums"]["groups.*.status"]) == main_pulse_overlay.GROUP_STATUSES


def test_main_ci_paths_exist_in_the_published_section(monkeypatch, tmp_path):
    builds = main_ci_tests._flaky_builds(
        main_ci_tests.NOW - timedelta(days=6), 8, outcome=lambda i: "failed" if i % 2 else "passed"
    )
    reliability = main_ci_tests._reliability(builds)
    state = amd.advance_incidents(
        amd.mi_scoped_reliability(reliability), amd._default_state(),
        track_commit_range=True, initialize_from_history=True,
    )
    state.update(hardware_scope="amd_mi_gpu", timeline=[])
    incidents = main_ci_section.build_main_ci(
        {"all_main_reliability": reliability}, state, _ts(main_ci_tests.NOW)
    )

    now = datetime.now(timezone.utc).replace(microsecond=0)
    slow = duration_tests._run(
        monkeypatch, tmp_path,
        duration_tests._builds(duration_tests.STEADY + duration_tests.SLOW, end=now - timedelta(hours=1)), now,
    )
    duration_state = json.loads(duration_tests.duration.STATE.read_text())
    slowdowns = main_ci_section.build_main_ci(
        {"all_main_reliability": slow}, {}, _ts(now), duration_state=duration_state
    )

    _assert_paths("main_ci", [incidents, slowdowns])


def test_test_group_parity_paths_exist_in_the_published_section():
    definition_parity = {"source": {"commit_sha": parity_tests.RUNTIME_SHA}, "matches": []}
    payload = parity.build_payload(
        parity_tests.policy(), snapshot=parity_tests.snapshot(), generated_at=parity_tests.GENERATED_AT,
        definition_parity=definition_parity,
    )
    # As build_operations_snapshot publishes it.
    payload["groups"] = resolve_reviewed_definition_labels(payload["groups"], definition_parity, label_field="title")
    assert {group["state"] for group in payload["groups"]} == {"existing", "unsupported", "action"}
    _assert_paths("test_group_parity", [payload])


def test_amd_agent_health_paths_exist_in_the_payload():
    row = agent_tests._attempt("j1", "a", "G", 1, "failed", 1)
    agent_health._attribute_failures([row])
    payload = agent_health._assemble([], [agent_health._failing_row(row)], agent_tests.NOW, pipelines=("ci",))
    _assert_paths("amd_agent_health", [payload])


def test_queue_paths_exist_in_the_section(monkeypatch, tmp_path):
    monkeypatch.setattr(cqs, "OUTPUT", tmp_path / "unused.jsonl", raising=False)
    monkeypatch.setattr(cqs, "fetch_cluster_queue_metrics", lambda token: {})
    monkeypatch.setattr(cqs, "fetch_active_cluster_jobs", lambda token, queue_ids_by_key=None: [])
    snapshot = cqs.collect_snapshot("fake-token")
    start = datetime.fromisoformat(snapshot["ts"].replace("Z", "+00:00"))
    history = [{**snapshot, "ts": _ts(start - timedelta(minutes=10 * index))} for index in range(3, -1, -1)]
    (tmp_path / "queue_timeseries.jsonl").write_text("".join(json.dumps(row) + "\n" for row in history))
    (tmp_path / "queue_jobs.json").write_text(json.dumps({"ts": history[-1]["ts"], "pending": [], "running": []}))

    section = build_queue_section.build_queue_section(tmp_path)

    _assert_paths("queue", [section["queue"]])


def test_main_pulse_paths_exist_in_the_overlay():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    authority = pulse_tests._authority(pulse_tests._history(now), now - timedelta(hours=1))
    overlay, _ = pulse_tests._step(authority, None, [pulse_tests._failing(now)], now)
    _assert_paths("main_pulse", [overlay])


@pytest.mark.parametrize(
    "path, payload, problem",
    [
        ("a[].b", {"a": [{"b": 1}, {}]}, "a[].b missing"),
        ("a.*.b", {"a": {"x": {"b": 1}, "y": None}}, "a.* is null"),
        ("a[].b?.c", {"a": [{"b": None}, {"b": {}}]}, "a[].b?.c missing"),
        ("a[]", {"a": {}}, "a is not a list"),
    ],
    ids=["list-item", "map-null", "nullable", "not-list"],
)
def test_walker_reports_where_a_path_breaks(path, payload, problem):
    assert problem in {found for found, _ in walk(payload, path)}
