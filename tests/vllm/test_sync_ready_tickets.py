"""Tests for ``scripts/vllm/sync_ready_tickets.py``.

We isolate the script from disk + GitHub by:

    * Pointing ``RESULTS_DIR``, ``OUT`` and ``STATE`` at tmp_path.
    * Seeding synthetic AMD nightly JSONL files across dates so the history
      aggregator has real input.
    * Never supplying ``PROJECTS_TOKEN`` so the ``live`` branch never fires.
      The live branch talks to GitHub GraphQL and REST — we exercise it
      indirectly via the dry-run plan instead. Integration against the real
      projects API is not something unit tests should do.

The tests focus on the *logic* the team lead pushed back on:
    - canonical title format is locked to upstream's ``[CI Failure]: <agent>: <test>``
      convention — the exact string vllm-project engineers grep for on board #39
    - hardware prefix is PRESERVED so each (agent, test) pair gets its own
      ticket (matching how the board is already populated by hand)
    - summary metrics (first failure, streak start, last success, break count)
      match what a human would draw from a timeline
    - plan JSON only contains currently-failing groups
    - dry-run writes a plan WITHOUT touching the network even if PROJECTS_TOKEN
      is set to garbage (we still force dry-run via READY_TICKETS_LIVE unset)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vllm import sync_ready_tickets as srt


@pytest.fixture(autouse=True)
def _no_upstream_yaml_fetch(monkeypatch):
    # Every ``_collect_group_history`` call now tries to fetch test-amd.yaml
    # from upstream to learn which labels use ``%N`` parallelism. Unit tests
    # must not touch the network — default to no templates so grouping is
    # an identity function, and individual tests can override as needed.
    monkeypatch.setattr(srt, "_fetch_shard_templates", lambda: [])


@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    results = tmp_path / "test_results"
    results.mkdir()
    out = tmp_path / "ready_tickets.json"
    state = tmp_path / "ready_tickets_state.json"
    monkeypatch.setattr(srt, "RESULTS_DIR", results, raising=False)
    monkeypatch.setattr(srt, "OUT", out, raising=False)
    monkeypatch.setattr(srt, "STATE", state, raising=False)
    return results, out, state


def _write_jsonl(path: Path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _today_minus(days: int) -> str:
    from datetime import datetime, timezone, timedelta
    return (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

class TestGroupKey:
    # Upstream's project-#39 convention is one ticket per agent/test pair.
    # The key MUST preserve the agent prefix — dropping it would collapse
    # mi250/mi300/mi325 failures into one ticket and break title-lookup
    # parity with tickets that vllm-project engineers file by hand.

    def test_preserves_agent_prefix(self):
        assert srt._group_key("mi250_1: EPLB Algorithm") == "mi250_1: EPLB Algorithm"
        assert srt._group_key("mi300_2: Pipeline parallel") == "mi300_2: Pipeline parallel"

    def test_no_prefix_returns_as_is(self):
        assert srt._group_key("EPLB Algorithm") == "EPLB Algorithm"

    def test_empty_string(self):
        assert srt._group_key("") == ""

    def test_none_tolerated(self):
        assert srt._group_key(None) == ""

    def test_trims_surrounding_whitespace(self):
        assert srt._group_key("  mi325_1: Quantized MoE Test (B200-MI325)  ") == (
            "mi325_1: Quantized MoE Test (B200-MI325)"
        )


class TestShardCanonicalization:
    # Buildkite expands ``parallelism: N`` with ``%N`` label substitution, so
    # ``Kernels MoE Test %N`` renders as ``...Test 1`` / ``...Test 2`` /
    # ``...Test 3`` / ``...Test 4``. Upstream authors it as ONE step — we
    # should file ONE ticket, not four. The YAML is the authority: we can't
    # strip trailing integers heuristically because non-parallelized groups
    # legitimately end in digits (e.g. ``LoRA 4`` when it's its own step).

    def _patterns(self, templates):
        return srt._compile_shard_patterns(templates)

    def test_collapses_parallel_shards_to_template(self):
        patterns = self._patterns(["Kernels MoE Test %N"])
        assert srt._group_key("mi325_1: Kernels MoE Test 1", patterns) == (
            "mi325_1: Kernels MoE Test %N"
        )
        assert srt._group_key("mi325_1: Kernels MoE Test 4", patterns) == (
            "mi325_1: Kernels MoE Test %N"
        )
        # Double-digit shard indices still collapse.
        assert srt._group_key("mi325_1: Kernels MoE Test 12", patterns) == (
            "mi325_1: Kernels MoE Test %N"
        )

    def test_non_parallel_trailing_digit_is_preserved(self):
        # ``LoRA 4`` is a real non-parallelized step — must NOT be swallowed
        # by a ``LoRA %N`` template that doesn't exist.
        patterns = self._patterns(["Kernels MoE Test %N"])
        assert srt._group_key("mi250_1: LoRA 4", patterns) == "mi250_1: LoRA 4"

    def test_without_templates_is_identity(self):
        assert srt._group_key("mi325_1: Kernels MoE Test 1", []) == (
            "mi325_1: Kernels MoE Test 1"
        )
        assert srt._group_key("mi325_1: Kernels MoE Test 1", None) == (
            "mi325_1: Kernels MoE Test 1"
        )

    def test_agent_prefix_preserved_across_shards(self):
        # Different HW agents keep separate tickets even if the test template
        # matches — mi325 and mi355 failures are triaged independently.
        patterns = self._patterns(["Kernels MoE Test %N"])
        assert srt._group_key("mi325_1: Kernels MoE Test 1", patterns) == (
            "mi325_1: Kernels MoE Test %N"
        )
        assert srt._group_key("mi355_1: Kernels MoE Test 1", patterns) == (
            "mi355_1: Kernels MoE Test %N"
        )

    def test_partial_match_does_not_collapse(self):
        # Template matching is full-string: ``Kernels MoE Test 1 Extended``
        # is not a shard of ``Kernels MoE Test %N``.
        patterns = self._patterns(["Kernels MoE Test %N"])
        assert srt._group_key("mi325_1: Kernels MoE Test 1 Extended", patterns) == (
            "mi325_1: Kernels MoE Test 1 Extended"
        )

    def test_collapsed_shards_aggregate_in_history(self, isolated_paths, monkeypatch):
        # End-to-end: four failing shards → one group in the plan, with
        # build numbers and hardware merged.
        results, _, _ = isolated_paths
        d = _today_minus(1)
        _write_jsonl(results / f"{d}_amd.jsonl", [
            {"job_name": f"mi325_1: Kernels MoE Test {i}",
             "classname": "mi325_1: kernels.moe",
             "status": "failed", "build_number": 100 + i}
            for i in range(1, 5)
        ])
        monkeypatch.setattr(
            srt, "_fetch_shard_templates", lambda: ["Kernels MoE Test %N"]
        )
        hist = srt._collect_group_history(days=60)
        assert set(hist.keys()) == {"mi325_1: Kernels MoE Test %N"}
        bucket = hist["mi325_1: Kernels MoE Test %N"][d]
        assert bucket["fail"] == 4
        assert bucket["build_numbers"] == [101, 102, 103, 104]


class TestIsFailing:
    def test_failing_statuses(self):
        for s in ("failed", "FAILED", "error", "broken", "timed_out", "soft_failed"):
            assert srt._is_failing(s) is True

    def test_passing_statuses_not_failing(self):
        for s in ("passed", "xpassed", "skipped", "", None):
            assert srt._is_failing(s) is False


class TestCanonicalTitle:
    def test_exact_format_matches_upstream_convention(self):
        # CRITICAL: this title is both the dedup key AND the string vllm-project
        # engineers grep for on the board. Must match their own [CI Failure]:
        # prefix character-for-character.
        assert srt._canonical_title("mi325_1: Quantized MoE Test (B200-MI325)") == (
            "[CI Failure]: mi325_1: Quantized MoE Test (B200-MI325)"
        )

    def test_title_unique_per_group(self):
        a = srt._canonical_title("A")
        b = srt._canonical_title("B")
        assert a != b


class TestLinkedPrExtraction:
    def test_extracts_pull_urls_and_pr_style_hash_refs(self):
        repo = "vllm-project/vllm"
        text = (
            "PR for this here #40176\n"
            "Expected to be solved after: https://github.com/vllm-project/vllm/pull/39531\n"
            "Ignore this other repo https://github.com/openai/openai/pull/1\n"
        )
        assert srt._extract_linked_prs_from_text(text, repo) == [
            {"number": 39531, "url": "https://github.com/vllm-project/vllm/pull/39531"},
            {"number": 40176, "url": "https://github.com/vllm-project/vllm/pull/40176"},
        ]


class TestNormalizeTitle:
    # Secondary lookup so a hand-filed ``[CI Failure]: Transformers ...``
    # collides with our machine-generated ``[CI Failure]: mi325_1: Transformers
    # ...`` — preventing the very-real failure the team lead flagged where
    # we duplicated a ticket that already existed on the backlog.

    def test_strips_ci_failure_prefix(self):
        assert srt._normalize_title("[CI Failure]: Foo") == "foo"

    def test_strips_hw_agent_prefix(self):
        assert srt._normalize_title("[CI Failure]: mi325_1: Foo Bar") == "foo bar"
        assert srt._normalize_title("[CI Failure]: mi250_4: Something") == "something"

    def test_strips_trailing_shard_template(self):
        assert srt._normalize_title("[CI Failure]: mi325_1: Kernels MoE Test %N") == (
            "kernels moe test"
        )

    def test_hand_filed_and_generated_collide(self):
        # The real-world bug: upstream filed a HW-agnostic ticket and the
        # sync filed an HW-qualified duplicate. Normalization MUST treat
        # them as equivalent.
        hand = "[CI Failure]: Transformers Nightly Models Test"
        ours = "[CI Failure]: mi325_1: Transformers Nightly Models Test"
        assert srt._normalize_title(hand) == srt._normalize_title(ours)

    def test_case_insensitive(self):
        assert srt._normalize_title("[CI Failure]: Foo BAR") == (
            srt._normalize_title("[ci failure]: foo bar")
        )

    def test_collapses_internal_whitespace(self):
        assert srt._normalize_title("[CI Failure]:  Foo   Bar  ") == "foo bar"

    def test_does_not_strip_non_shard_trailing_digits(self):
        # ``LoRA 4`` is its own test step (not a shard), so the trailing
        # digit must remain to keep it distinct from ``LoRA 1``.
        assert srt._normalize_title("[CI Failure]: mi250_1: LoRA 4") == "lora 4"
        assert srt._normalize_title("[CI Failure]: mi250_1: LoRA 1") == "lora 1"
        assert srt._normalize_title("[CI Failure]: mi250_1: LoRA 4") != (
            srt._normalize_title("[CI Failure]: mi250_1: LoRA 1")
        )

    def test_empty_and_none(self):
        assert srt._normalize_title("") == ""
        assert srt._normalize_title(None) == ""


class TestHwPrefix:
    # Distinct pools need distinct tickets — same test group on mi325 and
    # mi355 fail and recover on different schedules. The team lead flagged
    # the bug directly: "mi355_1: Kernels MoE Test %N" was resolving to
    # issue 40212 which is the mi325 ticket.

    def test_extracts_lowercased_prefix(self):
        assert srt._hw_prefix("[CI Failure]: mi325_1: Kernels MoE Test %N") == "mi325_1"
        assert srt._hw_prefix("[CI Failure]: MI355_1: Foo") == "mi355_1"
        assert srt._hw_prefix("mi250_4: LoRA") == "mi250_4"

    def test_none_when_absent(self):
        assert srt._hw_prefix("[CI Failure]: Transformers Nightly Models Test") is None
        assert srt._hw_prefix("Plain title") is None
        assert srt._hw_prefix("") is None
        assert srt._hw_prefix(None) is None


class TestNormalizedMatchCompatible:
    # The normalized-match fallback was introduced so a hand-filed
    # HW-agnostic ticket wouldn't get duplicated. But it has to reject
    # cross-pool matches or ``mi355_1: Kernels MoE Test %N`` ends up
    # wired to the ``mi325_1`` ticket — which is what shipped before.

    def test_existing_no_prefix_adopts(self):
        # Upstream filed a pool-agnostic ticket; our synthesized title
        # has the pool — adopt.
        assert srt._normalized_match_compatible(
            "[CI Failure]: Transformers Nightly Models Test",
            "[CI Failure]: mi325_1: Transformers Nightly Models Test",
        ) is True

    def test_same_prefix_adopts(self):
        # Same pool, slightly different wording — adopt. (Exact match
        # would usually hit first, but this path is the fallback.)
        assert srt._normalized_match_compatible(
            "[CI Failure]: mi325_1: Kernels MoE Test %N",
            "[CI Failure]: mi325_1: Kernels MoE Test %N",
        ) is True

    def test_different_prefix_rejects(self):
        # The exact bug: mi355 must NOT adopt mi325's ticket.
        assert srt._normalized_match_compatible(
            "[CI Failure]: mi325_1: Kernels MoE Test %N",
            "[CI Failure]: mi355_1: Kernels MoE Test %N",
        ) is False
        assert srt._normalized_match_compatible(
            "[CI Failure]: mi250_1: LoRA",
            "[CI Failure]: mi325_1: LoRA",
        ) is False

    def test_different_pool_number_same_family_rejects(self):
        # mi250_1 and mi250_4 are separate pools of the same family —
        # still distinct tickets.
        assert srt._normalized_match_compatible(
            "[CI Failure]: mi250_1: Entrypoints Integration (LLM)",
            "[CI Failure]: mi250_4: Entrypoints Integration (LLM)",
        ) is False

    def test_incoming_no_prefix_adopts_either_way(self):
        # Symmetric-ish: if someone manually synthesizes a title without
        # a pool and the existing ticket has one, we still adopt. Losing
        # the pool info is on the caller; the guard exists to prevent
        # cross-pool collapse, not to enforce pool presence.
        assert srt._normalized_match_compatible(
            "[CI Failure]: mi325_1: Kernels MoE Test %N",
            "[CI Failure]: Kernels MoE Test %N",
        ) is False  # existing has prefix, incoming doesn't — reject to be safe

    def test_enrich_dry_run_plan_rejects_cross_pool(self, tmp_path):
        # End-to-end: mi355 in the plan must not get wired to mi325's
        # existing ticket just because they share a normalized key.
        existing = {
            "[CI Failure]: mi325_1: Kernels MoE Test %N": {
                "number": 40562,
                "html_url": "https://github.com/vllm-project/vllm/issues/40562",
            },
        }
        plan = [
            {"title": "[CI Failure]: mi325_1: Kernels MoE Test %N",
             "action": "would_create", "issue_number": None, "issue_url": None},
            {"title": "[CI Failure]: mi355_1: Kernels MoE Test %N",
             "action": "would_create", "issue_number": None, "issue_url": None},
        ]
        srt._enrich_dry_run_plan(plan, existing)
        # mi325 — exact match, wired to the post-tracker issue.
        assert plan[0]["issue_number"] == 40562
        assert plan[0]["action"] == "would_update_existing"
        # mi355 — would have hit the bug, must remain unmatched so the
        # live sync creates a fresh ticket for the mi355 pool.
        assert plan[1]["issue_number"] is None
        assert plan[1]["action"] == "would_create"

    def test_enrich_dry_run_plan_picks_same_pool_with_whitespace_quirk(self):
        # The production bug that shipped: issue #35126 exists on upstream
        # with title ``[CI Failure]:  mi355_1: Kernels MoE Test %N`` —
        # note the double space after the colon (filed by hand, a common
        # whitespace quirk). The exact-match step misses because of the
        # extra space. The normalized-match step finds TWO candidates
        # under key ``kernels moe test``: mi325's single-space title and
        # mi355's double-space title. The matcher MUST pick the mi355
        # candidate by HW prefix, not whichever dict iteration yields first.
        existing = {
            "[CI Failure]: mi325_1: Kernels MoE Test %N": {
                "number": 40562,
                "html_url": "https://github.com/vllm-project/vllm/issues/40562",
            },
            "[CI Failure]:  mi355_1: Kernels MoE Test %N": {  # double space
                "number": 40563,
                "html_url": "https://github.com/vllm-project/vllm/issues/40563",
            },
        }
        plan = [
            {"title": "[CI Failure]: mi355_1: Kernels MoE Test %N",  # single space
             "action": "would_create", "issue_number": None, "issue_url": None},
        ]
        srt._enrich_dry_run_plan(plan, existing)
        assert plan[0]["issue_number"] == 40563, (
            "mi355 plan entry must adopt #40563 (the mi355 existing title), "
            "not #40562 (the mi325 one)"
        )
        assert plan[0]["action"] == "would_update_existing"


class TestPickNormalizedCandidate:
    def test_same_hw_prefix_wins_over_other_pool(self):
        # Multiple existing titles collide under the same normalized key
        # (e.g. different whitespace). Pick the one whose HW prefix matches
        # the incoming, regardless of list order.
        candidates = [
            "[CI Failure]: mi325_1: Kernels MoE Test %N",
            "[CI Failure]:  mi355_1: Kernels MoE Test %N",
        ]
        assert srt._pick_normalized_candidate(
            candidates, "[CI Failure]: mi355_1: Kernels MoE Test %N"
        ) == "[CI Failure]:  mi355_1: Kernels MoE Test %N"
        # And the reverse — order-independence.
        assert srt._pick_normalized_candidate(
            list(reversed(candidates)),
            "[CI Failure]: mi325_1: Kernels MoE Test %N",
        ) == "[CI Failure]: mi325_1: Kernels MoE Test %N"

    def test_hw_agnostic_existing_adopted_when_no_pool_match(self):
        # Upstream filed a pool-agnostic ticket; no HW-prefixed existing
        # matches our incoming pool — fall back to the pool-agnostic one
        # (the original design case that motivated the normalized match).
        candidates = [
            "[CI Failure]: Transformers Nightly Models Test",
            "[CI Failure]: mi325_1: Transformers Nightly Models Test",
        ]
        assert srt._pick_normalized_candidate(
            candidates, "[CI Failure]: mi355_1: Transformers Nightly Models Test"
        ) == "[CI Failure]: Transformers Nightly Models Test"

    def test_no_match_when_only_different_pools_exist(self):
        candidates = [
            "[CI Failure]: mi325_1: Kernels MoE Test %N",
            "[CI Failure]: mi250_4: Kernels MoE Test %N",
        ]
        assert srt._pick_normalized_candidate(
            candidates, "[CI Failure]: mi355_1: Kernels MoE Test %N"
        ) is None

    def test_empty_candidates(self):
        assert srt._pick_normalized_candidate(
            [], "[CI Failure]: mi355_1: anything"
        ) is None


# ---------------------------------------------------------------------------
# History aggregation
# ---------------------------------------------------------------------------

class TestCollectGroupHistory:
    def test_aggregates_across_dates_and_hardware(self, isolated_paths):
        results, _, _ = isolated_paths
        d1 = _today_minus(3)
        d2 = _today_minus(2)
        d3 = _today_minus(1)
        _write_jsonl(results / f"{d1}_amd.jsonl", [
            {"job_name": "mi250_1: EPLB", "classname": "mi250_1: tests.eplb",
             "status": "failed", "build_number": 100},
            {"job_name": "mi300_1: EPLB", "classname": "mi300_1: tests.eplb",
             "status": "passed", "build_number": 100},
        ])
        _write_jsonl(results / f"{d2}_amd.jsonl", [
            {"job_name": "mi250_1: EPLB", "classname": "mi250_1: tests.eplb",
             "status": "passed", "build_number": 101},
        ])
        _write_jsonl(results / f"{d3}_amd.jsonl", [
            {"job_name": "mi250_1: EPLB", "classname": "mi250_1: tests.eplb",
             "status": "failed", "build_number": 102},
            {"job_name": "mi300_1: EPLB", "classname": "mi300_1: tests.eplb",
             "status": "failed", "build_number": 102},
        ])

        hist = srt._collect_group_history(days=60)
        # Agent-qualified keys (one ticket per {agent: test} pair, matching
        # upstream project #39 convention).
        assert set(hist.keys()) == {"mi250_1: EPLB", "mi300_1: EPLB"}

        mi250 = hist["mi250_1: EPLB"]
        mi300 = hist["mi300_1: EPLB"]

        # mi250 timeline: fail → pass → fail
        assert mi250[d1]["fail"] == 1 and mi250[d1]["pass"] == 0
        assert mi250[d2]["fail"] == 0 and mi250[d2]["pass"] == 1
        assert mi250[d3]["fail"] == 1 and mi250[d3]["pass"] == 0
        # mi300 timeline: pass → (absent d2) → fail
        assert mi300[d1]["fail"] == 0 and mi300[d1]["pass"] == 1
        assert mi300[d3]["fail"] == 1 and mi300[d3]["pass"] == 0
        # Build numbers kept so the dashboard can link back.
        assert 102 in mi250[d3]["build_numbers"]
        assert 102 in mi300[d3]["build_numbers"]
        assert mi250[d3]["build_refs"] == [
            {
                "pipeline": "amd-ci",
                "build_number": 102,
                "url": "https://buildkite.com/vllm/amd-ci/builds/102",
            }
        ]
        assert mi300[d3]["build_refs"] == [
            {
                "pipeline": "amd-ci",
                "build_number": 102,
                "url": "https://buildkite.com/vllm/amd-ci/builds/102",
            }
        ]

    def test_ignores_files_outside_window(self, isolated_paths):
        results, _, _ = isolated_paths
        old = _today_minus(200)  # outside 60d window
        fresh = _today_minus(5)
        _write_jsonl(results / f"{old}_amd.jsonl", [
            {"job_name": "mi250_1: stale", "classname": "mi250_1: stale",
             "status": "failed"},
        ])
        _write_jsonl(results / f"{fresh}_amd.jsonl", [
            {"job_name": "mi250_1: fresh", "classname": "mi250_1: fresh",
             "status": "failed"},
        ])
        hist = srt._collect_group_history(days=60)
        assert "mi250_1: stale" not in hist
        assert "mi250_1: fresh" in hist

    def test_ignores_malformed_date_filenames(self, isolated_paths):
        results, _, _ = isolated_paths
        (results / "not-a-date_amd.jsonl").write_text(
            json.dumps({"job_name": "x", "status": "failed"}) + "\n"
        )
        assert srt._collect_group_history(days=60) == {}

    def test_ignores_malformed_json_lines(self, isolated_paths):
        results, _, _ = isolated_paths
        d = _today_minus(1)
        (results / f"{d}_amd.jsonl").write_text(
            "not-json\n"
            + json.dumps({"job_name": "mi250_1: keep", "status": "failed"}) + "\n"
            + "{broken\n"
        )
        hist = srt._collect_group_history(days=60)
        assert "mi250_1: keep" in hist


# ---------------------------------------------------------------------------
# Summary metrics
# ---------------------------------------------------------------------------

class TestSummarizeGroup:
    def test_currently_failing_when_latest_date_has_fail(self):
        history = {
            "2026-04-15": {"pass": 0, "fail": 1, "hardware": {"mi250_1": "fail"}, "build_numbers": [10]},
            "2026-04-16": {"pass": 1, "fail": 0, "hardware": {"mi250_1": "pass"}, "build_numbers": [11]},
            "2026-04-17": {"pass": 0, "fail": 1, "hardware": {"mi250_1": "fail"}, "build_numbers": [12]},
        }
        s = srt._summarize_group("G", history)
        assert s["currently_failing"] is True
        assert s["latest_date"] == "2026-04-17"
        # First failure in window is the *earliest* fail, not the current streak.
        assert s["first_failure_in_window"] == "2026-04-15"
        # Current streak starts only after the most recent pass.
        assert s["current_streak_started"] == "2026-04-17"
        assert s["last_successful"] == "2026-04-16"
        # pass↔fail flips: F→P + P→F = 2 flips.
        assert s["break_frequency"] == 2
        assert s["hardware_latest"] == {"mi250_1": "fail"}
        assert s["builds_latest"] == [12]

    def test_not_currently_failing_when_latest_is_pass(self):
        history = {
            "2026-04-15": {"pass": 0, "fail": 1, "hardware": {}, "build_numbers": []},
            "2026-04-16": {"pass": 1, "fail": 0, "hardware": {}, "build_numbers": []},
        }
        s = srt._summarize_group("G", history)
        assert s["currently_failing"] is False
        assert s["current_streak_started"] is None

    def test_all_passing_no_failures(self):
        history = {
            "2026-04-15": {"pass": 1, "fail": 0, "hardware": {"mi250_1": "pass"}, "build_numbers": []},
            "2026-04-16": {"pass": 1, "fail": 0, "hardware": {"mi250_1": "pass"}, "build_numbers": []},
        }
        s = srt._summarize_group("G", history)
        assert s["currently_failing"] is False
        assert s["first_failure_in_window"] is None
        assert s["break_frequency"] == 0
        assert s["current_streak_started"] is None

    def test_empty_history(self):
        s = srt._summarize_group("G", {})
        assert s["currently_failing"] is False
        assert s["latest_date"] is None
        assert s["builds_latest"] == []

    def test_current_streak_walks_back_through_only_failures(self):
        history = {
            "2026-04-14": {"pass": 1, "fail": 0, "hardware": {}, "build_numbers": []},
            "2026-04-15": {"pass": 0, "fail": 1, "hardware": {}, "build_numbers": []},
            "2026-04-16": {"pass": 0, "fail": 1, "hardware": {}, "build_numbers": []},
            "2026-04-17": {"pass": 0, "fail": 1, "hardware": {}, "build_numbers": []},
        }
        s = srt._summarize_group("G", history)
        assert s["current_streak_started"] == "2026-04-15"
        assert s["first_failure_in_window"] == "2026-04-15"


class TestBuildkiteIssueRendering:
    def _summary(self):
        return {
            "group": "mi250_1: Entrypoints Integration (API Server 2)",
            "current_streak_started": "2026-04-16",
            "first_failure_in_window": "2026-04-16",
            "last_successful": "2026-04-15",
            "break_frequency": 1,
            "latest_date": "2026-04-19",
            "hardware_latest": {"mi250_1": "fail"},
            "builds_latest": [7806],
            "build_refs_latest": [
                {
                    "pipeline": "amd-ci",
                    "build_number": 7806,
                    "url": "https://buildkite.com/vllm/amd-ci/builds/7806",
                }
            ],
        }

    def test_issue_body_links_buildkite_builds(self):
        body = srt._issue_body(
            self._summary(),
            "https://github.com/AndreasKaratzas/vllm-ci-dashboard/actions/runs/24641501904",
        )
        assert "[amd-ci #7806](https://buildkite.com/vllm/amd-ci/builds/7806)" in body
        assert "**Latest build(s):** #7806" not in body


class TestFindOrCreateIssue:
    def test_existing_issue_refreshes_body(self, monkeypatch):
        calls = []

        def _fake_sync(token, repo, issue_number, body, reopen=False):
            calls.append({
                "token": token,
                "repo": repo,
                "issue_number": issue_number,
                "body": body,
                "reopen": reopen,
            })

        monkeypatch.setattr(srt, "_sync_issue_body", _fake_sync)
        project_items = {
            "[CI Failure]: mi250_1: Broken Group": {
                "issueState": "open",
                "repo": "vllm-project/vllm",
                "issueNumber": 77777,
                "url": "https://github.com/vllm-project/vllm/issues/77777",
            }
        }

        issue_number, issue_url, created, matched_title = srt._find_or_create_issue(
            "fake-token",
            "[CI Failure]: mi250_1: Broken Group",
            "fresh body",
            project_items,
            {},
        )

        assert (issue_number, issue_url, created, matched_title) == (
            77777,
            "https://github.com/vllm-project/vllm/issues/77777",
            False,
            "[CI Failure]: mi250_1: Broken Group",
        )
        assert calls == [{
            "token": "fake-token",
            "repo": "vllm-project/vllm",
            "issue_number": 77777,
            "body": "fresh body",
            "reopen": False,
        }]


# ---------------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------------

class TestRunDryRun:
    def test_dry_run_emits_plan_and_never_hits_network(
        self, isolated_paths, monkeypatch
    ):
        results, out, _ = isolated_paths
        d = _today_minus(1)
        _write_jsonl(results / f"{d}_amd.jsonl", [
            {"job_name": "mi250_1: Broken Group", "classname": "mi250_1: x",
             "status": "failed", "build_number": 200},
            {"job_name": "mi250_1: Clean Group", "classname": "mi250_1: y",
             "status": "passed", "build_number": 200},
        ])

        # Explode if _graphql is called — dry-run must not touch GitHub.
        def _boom(*a, **kw):
            raise AssertionError("dry-run must not call GitHub GraphQL")
        monkeypatch.setattr(srt, "_graphql", _boom)
        monkeypatch.setattr(srt, "_fetch_project_meta", _boom)
        # And the REST preflight must not fire either when no token is set.
        def _boom_rest(*a, **kw):
            raise AssertionError("dry-run without a token must not hit REST")
        monkeypatch.setattr(srt, "_fetch_existing_ci_failure_issues", _boom_rest)

        monkeypatch.delenv("READY_TICKETS_LIVE", raising=False)
        monkeypatch.delenv("PROJECTS_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

        rc = srt.run()
        assert rc == 0
        data = json.loads(out.read_text())
        assert data["mode"] == "dry_run"
        # Only failing groups show up as tickets.
        titles = {t["title"] for t in data["tickets"]}
        assert titles == {"[CI Failure]: mi250_1: Broken Group"}
        # Canonical shape of a dry-run ticket.
        ticket = data["tickets"][0]
        assert ticket["action"] == "would_create_or_update"
        assert ticket["issue_number"] is None
        # The dashboard's "create ↗" link builds a pre-filled GitHub
        # ``issues/new?title=&body=&labels=`` URL from these three fields.
        # If any go missing the admin would land on an empty compose form
        # and file a ticket without the hardware / streak / build context.
        assert ticket["labels"] == ["ci-failure"]
        assert ticket["body"], "dry-run ticket must carry a ready-to-post body"
        assert "mi250_1: Broken Group" in ticket["body"]
        # The body must identify it as a CI-failure template so we're not
        # pre-filling the compose form with something unrelated.
        assert "AMD nightly" in ticket["body"]
        # PII guarantee: the roster must NOT be in this file. The plan is
        # served publicly on gh-pages; the admin dropdown reads the roster
        # from the encrypted engineers.enc.json instead.
        assert "engineers" not in data
        assert data["failing_groups_total"] == 1

    def test_live_requested_but_no_token_forces_dry_run(
        self, isolated_paths, monkeypatch
    ):
        results, out, _ = isolated_paths
        d = _today_minus(1)
        _write_jsonl(results / f"{d}_amd.jsonl", [
            {"job_name": "mi250_1: G", "classname": "mi250_1: c",
             "status": "failed", "build_number": 1},
        ])

        def _boom(*a, **kw):
            raise AssertionError("must not reach GitHub without PROJECTS_TOKEN")
        monkeypatch.setattr(srt, "_graphql", _boom)
        monkeypatch.setattr(srt, "_fetch_existing_ci_failure_issues", _boom)

        monkeypatch.setenv("READY_TICKETS_LIVE", "1")
        monkeypatch.delenv("PROJECTS_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

        rc = srt.run()
        assert rc == 0
        data = json.loads(out.read_text())
        assert data["mode"] == "dry_run_forced"

    def test_live_requested_without_explicit_allow_stays_paused(
        self, isolated_paths, monkeypatch, tmp_path
    ):
        results, out, _ = isolated_paths
        monkeypatch.setattr(
            srt, "PROJECT_ITEMS_OUT", tmp_path / "project_items.json", raising=False
        )
        d = _today_minus(1)
        _write_jsonl(results / f"{d}_amd.jsonl", [
            {"job_name": "mi250_1: G", "classname": "mi250_1: c",
             "status": "failed", "build_number": 1},
        ])

        def _boom(*a, **kw):
            raise AssertionError("paused mode must not reach upstream GitHub")
        monkeypatch.setattr(srt, "_graphql", _boom)
        monkeypatch.setattr(srt, "_fetch_existing_ci_failure_issues", _boom)

        monkeypatch.setenv("READY_TICKETS_LIVE", "1")
        monkeypatch.setenv("PROJECTS_TOKEN", "dummy-token")
        monkeypatch.delenv("READY_TICKETS_ALLOW_UPSTREAM_WRITES", raising=False)

        rc = srt.run()
        assert rc == 0
        data = json.loads(out.read_text())
        assert data["mode"] == "paused"
        assert data["feature_paused"] is True
        assert data["tickets"] == []

        project_items = json.loads((tmp_path / "project_items.json").read_text())
        assert project_items["feature_paused"] is True
        assert project_items["items_by_number"] == {}

    def test_no_failing_groups_produces_empty_plan(self, isolated_paths, monkeypatch):
        results, out, _ = isolated_paths
        d = _today_minus(1)
        _write_jsonl(results / f"{d}_amd.jsonl", [
            {"job_name": "mi250_1: OK", "classname": "mi250_1: x",
             "status": "passed", "build_number": 1},
        ])
        monkeypatch.delenv("READY_TICKETS_LIVE", raising=False)
        monkeypatch.delenv("PROJECTS_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        rc = srt.run()
        assert rc == 0
        data = json.loads(out.read_text())
        assert data["failing_groups_total"] == 0
        assert data["tickets"] == []

    def test_plan_includes_all_groups_in_groups_all(
        self, isolated_paths, monkeypatch
    ):
        # Design intent: `tickets` only has failing groups, but `groups_all`
        # has the full window so the dashboard can show "tracked 150 groups".
        results, out, _ = isolated_paths
        d = _today_minus(1)
        _write_jsonl(results / f"{d}_amd.jsonl", [
            {"job_name": "mi250_1: pass-me", "classname": "mi250_1: x",
             "status": "passed", "build_number": 1},
            {"job_name": "mi250_1: fail-me", "classname": "mi250_1: y",
             "status": "failed", "build_number": 1},
        ])
        monkeypatch.delenv("READY_TICKETS_LIVE", raising=False)
        monkeypatch.delenv("PROJECTS_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        srt.run()
        data = json.loads(out.read_text())
        all_groups = {g["group"] for g in data["groups_all"]}
        assert all_groups == {"mi250_1: pass-me", "mi250_1: fail-me"}
        # tickets only contains the failing one
        assert {t["summary"]["group"] for t in data["tickets"]} == {"mi250_1: fail-me"}

    def test_plan_uses_latest_build_only_and_collapses_percent_n_shards(
        self, isolated_paths, monkeypatch
    ):
        results, out, _ = isolated_paths
        d = _today_minus(0)
        _write_jsonl(results / f"{d}_amd.jsonl", [
            # Older build on the same date: must not keep this group alive.
            {"job_name": "mi300_1: stale-group", "classname": "mi300_1: stale-group",
             "status": "failed", "build_number": 100},
            # Latest build on the same date: shard-expanded failures should
            # collapse to one logical group in the tracker.
            {"job_name": "mi300_1: Kernels MoE Test 1", "classname": "mi300_1: Kernels MoE Test 1",
             "status": "failed", "build_number": 101, "pipeline": "amd-ci"},
            {"job_name": "mi300_1: Kernels MoE Test 2", "classname": "mi300_1: Kernels MoE Test 2",
             "status": "failed", "build_number": 101, "pipeline": "amd-ci"},
            {"job_name": "mi300_1: Kernels MoE Test 3", "classname": "mi300_1: Kernels MoE Test 3",
             "status": "failed", "build_number": 101, "pipeline": "amd-ci"},
            {"job_name": "mi300_1: Kernels MoE Test 4", "classname": "mi300_1: Kernels MoE Test 4",
             "status": "failed", "build_number": 101, "pipeline": "amd-ci"},
            {"job_name": "mi300_1: Real Broken Group", "classname": "mi300_1: Real Broken Group",
             "status": "error", "build_number": 101, "pipeline": "amd-ci"},
            # Latest build pass: should not appear in the issue tracker count.
            {"job_name": "mi300_1: Healthy Group", "classname": "mi300_1: Healthy Group",
             "status": "passed", "build_number": 101, "pipeline": "amd-ci"},
        ])

        monkeypatch.setattr(
            srt, "_fetch_shard_templates", lambda: ["Kernels MoE Test %N"]
        )
        monkeypatch.delenv("READY_TICKETS_LIVE", raising=False)
        monkeypatch.delenv("PROJECTS_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

        rc = srt.run()
        assert rc == 0
        data = json.loads(out.read_text())

        assert data["failing_groups_total"] == 2
        assert {t["summary"]["group"] for t in data["tickets"]} == {
            "mi300_1: Kernels MoE Test %N",
            "mi300_1: Real Broken Group",
        }
        assert all("stale-group" not in t["summary"]["group"] for t in data["tickets"])

        moe = next(t for t in data["tickets"] if t["summary"]["group"] == "mi300_1: Kernels MoE Test %N")
        assert moe["summary"]["builds_latest"] == [101]
        assert moe["summary"]["build_refs_latest"] == [
            {
                "pipeline": "amd-ci",
                "build_number": 101,
                "url": "https://buildkite.com/vllm/amd-ci/builds/101",
            }
        ]


# ---------------------------------------------------------------------------
# Dry-run preflight — read-only lookup of already-filed upstream issues.
#
# This is the regression the dashboard surfaced: every ticket rendered
# "pending" even when an engineer had already filed a matching issue on
# vllm-project/vllm. The preflight uses the REST ``search/issues``
# endpoint (cheap, public-read) to annotate those plan entries with
# their live ``issue_number`` / ``issue_url`` so the UI can link to them.
# ---------------------------------------------------------------------------

class TestDryRunPreflight:

    def _setup_one_failing(self, isolated_paths, monkeypatch):
        results, out, _ = isolated_paths
        d = _today_minus(1)
        _write_jsonl(results / f"{d}_amd.jsonl", [
            {"job_name": "mi250_1: Broken Group", "classname": "mi250_1: x",
             "status": "failed", "build_number": 200},
        ])
        monkeypatch.delenv("READY_TICKETS_LIVE", raising=False)
        monkeypatch.delenv("PROJECTS_TOKEN", raising=False)
        # Graphql must never be touched; only the REST preflight runs.
        def _boom(*a, **kw):
            raise AssertionError("dry-run preflight must not call GraphQL")
        monkeypatch.setattr(srt, "_graphql", _boom)
        monkeypatch.setattr(srt, "_fetch_project_meta", _boom)
        return out

    def test_exact_title_match_links_existing_issue(
        self, isolated_paths, monkeypatch
    ):
        out = self._setup_one_failing(isolated_paths, monkeypatch)

        # Canonical title built by ``_canonical_title``.
        existing = {
            "[CI Failure]: mi250_1: Broken Group": {
                "number": 77777,
                "html_url": "https://github.com/vllm-project/vllm/issues/77777",
                "state": "open",
            }
        }
        monkeypatch.setattr(
            srt, "_fetch_existing_ci_failure_issues",
            lambda token, repo: existing,
        )
        monkeypatch.setenv("GITHUB_TOKEN", "fake-read-only-token")

        rc = srt.run()
        assert rc == 0
        data = json.loads(out.read_text())
        ticket = data["tickets"][0]
        # The enrichment must link the exact issue we returned.
        assert ticket["issue_number"] == 77777
        assert ticket["issue_url"].endswith("/issues/77777")
        # The action flips to surface the distinction to readers: the
        # live syncer would reopen/update, not create anew.
        assert ticket["action"] == "would_update_existing"

    def test_normalized_title_fallback_matches_hand_filed_ticket(
        self, isolated_paths, monkeypatch
    ):
        """Engineers often file ``[CI Failure]: Broken Group`` (no HW prefix).
        The normalized-title fallback must still link it to the syncer's
        ``[CI Failure]: mi250_1: Broken Group`` plan entry so we don't
        recommend filing a duplicate.
        """
        out = self._setup_one_failing(isolated_paths, monkeypatch)
        existing = {
            # Note: no "mi250_1:" prefix — the hand-filed shape.
            "[CI Failure]: Broken Group": {
                "number": 88888,
                "html_url": "https://github.com/vllm-project/vllm/issues/88888",
                "state": "open",
            }
        }
        monkeypatch.setattr(
            srt, "_fetch_existing_ci_failure_issues",
            lambda token, repo: existing,
        )
        monkeypatch.setenv("GITHUB_TOKEN", "fake")

        rc = srt.run()
        assert rc == 0
        ticket = json.loads(out.read_text())["tickets"][0]
        assert ticket["issue_number"] == 88888
        assert ticket["action"] == "would_update_existing"

    def test_preflight_network_failure_falls_through_silently(
        self, isolated_paths, monkeypatch
    ):
        """If the REST search 5xx's or the token is wrong, we must still
        emit a valid plan (with ``pending`` rows) rather than crash the
        hourly workflow. A stale preview beats a broken one."""
        out = self._setup_one_failing(isolated_paths, monkeypatch)

        # Exercise the real function's error-handling path by giving it
        # a requests.get that always raises.
        import requests
        class _FakeResp:
            status_code = 500
            text = "internal error"
        def _raise(*a, **kw): raise requests.RequestException("connection reset")
        monkeypatch.setattr(srt.requests, "get", _raise)
        monkeypatch.setenv("GITHUB_TOKEN", "fake")

        rc = srt.run()
        assert rc == 0
        ticket = json.loads(out.read_text())["tickets"][0]
        # Falls back to pending shape, not a crash.
        assert ticket["issue_number"] is None
        assert ticket["action"] == "would_create_or_update"

    def test_no_match_leaves_ticket_pending(
        self, isolated_paths, monkeypatch
    ):
        """An existing CI-failure issue for an unrelated test must not
        accidentally adopt our ticket. Without a title match we stay
        pending."""
        out = self._setup_one_failing(isolated_paths, monkeypatch)
        monkeypatch.setattr(
            srt, "_fetch_existing_ci_failure_issues",
            lambda token, repo: {
                "[CI Failure]: mi325_1: Totally Different Test": {
                    "number": 1, "html_url": "https://example", "state": "open",
                }
            },
        )
        monkeypatch.setenv("GITHUB_TOKEN", "fake")

        rc = srt.run()
        assert rc == 0
        ticket = json.loads(out.read_text())["tickets"][0]
        assert ticket["issue_number"] is None
        assert ticket["action"] == "would_create_or_update"

    def test_live_mode_updates_one_master_issue_and_writes_minimal_snapshot(
        self, isolated_paths, monkeypatch, tmp_path
    ):
        results, out, state = isolated_paths
        items_path = tmp_path / "project_items.json"
        monkeypatch.setattr(srt, "PROJECT_ITEMS_OUT", items_path, raising=False)
        monkeypatch.setattr(srt, "MASTER_ISSUE_NUMBER", 40554, raising=False)
        monkeypatch.setattr(srt, "MASTER_ISSUE_TITLE", "[AMD][CI Failure][Tracker] Static dashboard tracker for current CI failures", raising=False)
        monkeypatch.setattr(srt, "MASTER_ISSUE_URL", "https://github.com/vllm-project/vllm/issues/40554", raising=False)

        d = _today_minus(1)
        _write_jsonl(results / f"{d}_amd.jsonl", [
            {"job_name": "mi250_1: Broken Group", "classname": "mi250_1: x",
             "status": "failed", "build_number": 200},
            {"job_name": "mi355_1: Other Broken Group", "classname": "mi355_1: y",
             "status": "failed", "build_number": 200},
        ])

        calls = []
        monkeypatch.setattr(
            srt,
            "_upsert_master_issue_comment",
            lambda token, **kw: calls.append(kw) or {
                "id": 321,
                "url": "https://github.com/vllm-project/vllm/issues/40554#issuecomment-321",
                "action": "updated",
            },
        )
        monkeypatch.setattr(srt, "_comment_issue", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not comment")))
        monkeypatch.setattr(srt, "_close_issue", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not close")))
        monkeypatch.setattr(srt, "_find_or_create_issue", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not create per-group issues")))

        monkeypatch.setenv("READY_TICKETS_LIVE", "1")
        monkeypatch.setenv("PROJECTS_TOKEN", "dummy-token")
        monkeypatch.setenv("READY_TICKETS_ALLOW_UPSTREAM_WRITES", "1")

        rc = srt.run()
        assert rc == 0
        assert len(calls) == 1
        assert "### MI250" in calls[0]["body"]
        assert "### MI355" in calls[0]["body"]
        assert "#### `mi250_1: Broken Group`" in calls[0]["body"]
        assert "#### `mi355_1: Other Broken Group`" in calls[0]["body"]
        assert "Do not open per-group automated tickets from this pipeline" not in calls[0]["body"]

        output = json.loads(out.read_text())
        assert output["issue_mode"] == "single_master"
        assert output["master_issue"]["number"] == 40554
        assert output["master_issue"]["url"].endswith("/issues/40554")
        assert output["master_issue_comment"]["id"] == 321
        assert output["failing_groups_total"] == 2
        for ticket in output["tickets"]:
            assert ticket["issue_number"] == 40554
            assert ticket["action"] == "updated_master_issue_comment"
            assert ticket["project_status"] == "Tracked in master issue"

        snap = json.loads(items_path.read_text())
        assert snap["project"] == "vllm-project/projects/39"
        assert snap["items_by_number"] == {}

        state_data = json.loads(state.read_text())
        assert state_data["master_issue"]["issue_number"] == 40554
        assert state_data["master_issue"]["comment_id"] == 321

    def test_live_mode_master_issue_body_clears_when_nothing_is_failing(
        self, isolated_paths, monkeypatch
    ):
        results, out, _ = isolated_paths
        monkeypatch.setattr(srt, "MASTER_ISSUE_NUMBER", 40554, raising=False)
        monkeypatch.setattr(srt, "MASTER_ISSUE_TITLE", "[AMD][CI Failure][Tracker] Static dashboard tracker for current CI failures", raising=False)
        monkeypatch.setattr(srt, "MASTER_ISSUE_URL", "https://github.com/vllm-project/vllm/issues/40554", raising=False)

        d = _today_minus(1)
        _write_jsonl(results / f"{d}_amd.jsonl", [
            {"job_name": "mi250_1: Healthy Group", "classname": "mi250_1: x",
             "status": "passed", "build_number": 200},
        ])

        calls = []
        monkeypatch.setattr(
            srt,
            "_upsert_master_issue_comment",
            lambda token, **kw: calls.append(kw) or {
                "id": 321,
                "url": "https://github.com/vllm-project/vllm/issues/40554#issuecomment-321",
                "action": "updated",
            },
        )

        monkeypatch.setenv("READY_TICKETS_LIVE", "1")
        monkeypatch.setenv("PROJECTS_TOKEN", "dummy-token")
        monkeypatch.setenv("READY_TICKETS_ALLOW_UPSTREAM_WRITES", "1")

        rc = srt.run()
        assert rc == 0
        assert len(calls) == 1
        assert "No AMD nightly test groups are currently failing." in calls[0]["body"]

        output = json.loads(out.read_text())
        assert output["failing_groups_total"] == 0
        assert output["tickets"] == []

    def test_live_mode_enriches_group_from_matching_manual_issue(
        self, isolated_paths, monkeypatch, tmp_path
    ):
        results, out, state = isolated_paths
        items_path = tmp_path / "project_items.json"
        monkeypatch.setattr(srt, "PROJECT_ITEMS_OUT", items_path, raising=False)
        monkeypatch.setattr(srt, "MASTER_ISSUE_NUMBER", 40554, raising=False)
        monkeypatch.setattr(srt, "MASTER_ISSUE_TITLE", "[AMD][CI Failure][Tracker] Static dashboard tracker for current CI failures", raising=False)
        monkeypatch.setattr(srt, "MASTER_ISSUE_URL", "https://github.com/vllm-project/vllm/issues/40554", raising=False)

        d = _today_minus(1)
        _write_jsonl(results / f"{d}_amd.jsonl", [
            {"job_name": "mi355_1: V1 Spec Decode", "classname": "mi355_1: x",
             "status": "failed", "build_number": 200},
        ])

        monkeypatch.setattr(
            srt,
            "_upsert_master_issue_comment",
            lambda token, **kw: {
                "id": 321,
                "url": "https://github.com/vllm-project/vllm/issues/40554#issuecomment-321",
                "action": "updated",
            },
        )
        monkeypatch.setattr(
            srt,
            "_fetch_project_meta",
            lambda token: ("PROJ_ID", "STATUS_FIELD_ID", {"In Progress": "OPT_IN_PROGRESS"}),
        )
        monkeypatch.setattr(
            srt,
            "_fetch_project_items_by_title",
            lambda token, pid: {
                "[CI Failure]: mi355_1: V1 Spec Decode": {
                    "itemId": "ITEM_1",
                    "issueNumber": 40560,
                    "issueState": "open",
                    "status": "In Progress",
                    "url": "https://github.com/vllm-project/vllm/issues/40560",
                    "repo": "vllm-project/vllm",
                }
            },
        )
        monkeypatch.setattr(
            srt,
            "_collect_issue_metadata",
            lambda token, repo, issue_number: {
                "linked_prs": [
                    {"number": 40176, "url": "https://github.com/vllm-project/vllm/pull/40176"}
                ],
                "assignees": ["AndreasKaratzas"],
                "assignee": "AndreasKaratzas",
            },
        )

        monkeypatch.setenv("READY_TICKETS_LIVE", "1")
        monkeypatch.setenv("PROJECTS_TOKEN", "dummy-token")
        monkeypatch.setenv("READY_TICKETS_ALLOW_UPSTREAM_WRITES", "1")

        rc = srt.run()
        assert rc == 0

        output = json.loads(out.read_text())
        ticket = output["tickets"][0]
        assert ticket["issue_number"] == 40560
        assert ticket["issue_url"].endswith("/issues/40560")
        assert ticket["action"] == "tracked_manual_issue"
        assert ticket["project_status"] == "In Progress"
        assert ticket["linked_prs"] == [
            {"number": 40176, "url": "https://github.com/vllm-project/vllm/pull/40176"}
        ]
        assert ticket["assignee"] == "AndreasKaratzas"
        assert ticket["assignees"] == ["AndreasKaratzas"]

        snap = json.loads(items_path.read_text())
        assert "40560" in snap["items_by_number"]
        assert snap["items_by_number"]["40560"]["status"] == "In Progress"

        state_data = json.loads(state.read_text())
        assert state_data["master_issue"]["issue_number"] == 40554

    def test_live_mode_ignores_pre_tracker_project_issue_matches(
        self, isolated_paths, monkeypatch
    ):
        results, out, _ = isolated_paths
        monkeypatch.setattr(srt, "MASTER_ISSUE_NUMBER", 40554, raising=False)
        monkeypatch.setattr(srt, "MASTER_ISSUE_TITLE", "[AMD][CI Failure][Tracker] Static dashboard tracker for current CI failures", raising=False)
        monkeypatch.setattr(srt, "MASTER_ISSUE_URL", "https://github.com/vllm-project/vllm/issues/40554", raising=False)

        d = _today_minus(1)
        _write_jsonl(results / f"{d}_amd.jsonl", [
            {"job_name": "mi355_1: V1 Spec Decode", "classname": "mi355_1: x",
             "status": "failed", "build_number": 200},
        ])

        monkeypatch.setattr(
            srt,
            "_upsert_master_issue_comment",
            lambda token, **kw: {
                "id": 321,
                "url": "https://github.com/vllm-project/vllm/issues/40554#issuecomment-321",
                "action": "updated",
            },
        )
        monkeypatch.setattr(
            srt,
            "_fetch_project_meta",
            lambda token: ("PROJ_ID", "STATUS_FIELD_ID", {"In review": "OPT_IN_REVIEW"}),
        )
        monkeypatch.setattr(
            srt,
            "_fetch_project_items_by_title",
            lambda token, pid: {
                "[CI Failure]: mi355_1: V1 Spec Decode": {
                    "itemId": "ITEM_1",
                    "issueNumber": 40240,
                    "issueState": "open",
                    "status": "In review",
                    "url": "https://github.com/vllm-project/vllm/issues/40240",
                    "repo": "vllm-project/vllm",
                }
            },
        )

        monkeypatch.setenv("READY_TICKETS_LIVE", "1")
        monkeypatch.setenv("PROJECTS_TOKEN", "dummy-token")
        monkeypatch.setenv("READY_TICKETS_ALLOW_UPSTREAM_WRITES", "1")

        rc = srt.run()
        assert rc == 0

        ticket = json.loads(out.read_text())["tickets"][0]
        assert ticket["issue_number"] == 40554
        assert ticket["issue_url"].endswith("/issues/40554")
        assert ticket["action"] == "updated_master_issue_comment"
        assert ticket["project_status"] == "Tracked in master issue"

    def test_validate_master_issue_target_rejects_non_dashboard_owned_issue(
        self, monkeypatch
    ):
        monkeypatch.setattr(srt, "MASTER_ISSUE_NUMBER", 40554, raising=False)
        monkeypatch.setattr(srt, "MASTER_ISSUE_TITLE", "[AMD][CI Failure][Tracker] Static dashboard tracker for current CI failures", raising=False)
        monkeypatch.setattr(srt, "MASTER_ISSUE_OWNER", "AndreasKaratzas", raising=False)
        monkeypatch.setattr(srt, "MASTER_ISSUE_BODY_SENTINEL", "single dashboard-managed umbrella issue", raising=False)
        monkeypatch.setattr(
            srt,
            "_issue_details",
            lambda token, repo, issue_number: {
                "title": "Someone else's tracker",
                "user": {"login": "other-user"},
                "body": "wrong body",
            },
        )

        with pytest.raises(RuntimeError, match="Refusing to update the configured master issue"):
            srt._validate_master_issue_target("dummy-token")

    def test_upsert_master_issue_comment_fails_if_github_returns_any_comment_other_than_written_one(
        self, monkeypatch
    ):
        class _PatchResp:
            status_code = 200
            text = ""

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "id": 99999,
                    "html_url": "https://github.com/vllm-project/vllm/issues/27680#issuecomment-99999",
                }

        calls = {"comments": 0}
        def _fake_issue_comments(*a, **kw):
            calls["comments"] += 1
            if calls["comments"] == 1:
                return []
            return [{"id": 123, "body": "other body", "html_url": "https://example.com"}]

        monkeypatch.setattr(srt, "_validate_master_issue_target", lambda token: {})
        monkeypatch.setattr(srt, "_issue_comments", _fake_issue_comments)
        monkeypatch.setattr(srt.requests, "post", lambda *a, **kw: _PatchResp())

        with pytest.raises(RuntimeError, match="verification failed"):
            srt._upsert_master_issue_comment(
                "dummy-token",
                body="body",
            )

    def test_upsert_master_issue_comment_updates_existing_managed_comment(
        self, monkeypatch
    ):
        existing = [{
            "id": 321,
            "body": f"{srt.MASTER_COMMENT_MARKER}\n\nold",
            "html_url": "https://github.com/vllm-project/vllm/issues/27680#issuecomment-321",
        }]

        class _PatchResp:
            status_code = 200
            text = ""

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "id": 321,
                    "html_url": "https://github.com/vllm-project/vllm/issues/27680#issuecomment-321",
                }

        calls = {"patch": 0}
        monkeypatch.setattr(
            srt,
            "_issue_comments",
            lambda *a, **kw: existing if not calls["patch"] else [{
                "id": 321,
                "body": "expected body",
                "html_url": "https://github.com/vllm-project/vllm/issues/27680#issuecomment-321",
            }],
        )
        monkeypatch.setattr(
            srt.requests,
            "patch",
            lambda *a, **kw: calls.__setitem__("patch", calls["patch"] + 1) or _PatchResp(),
        )
        monkeypatch.setattr(srt, "_validate_master_issue_target", lambda token: {})

        result = srt._upsert_master_issue_comment("dummy-token", body="expected body")
        assert result == {
            "id": 321,
            "url": "https://github.com/vllm-project/vllm/issues/27680#issuecomment-321",
            "action": "updated",
        }

    def test_pagination_stops_at_short_page(
        self, isolated_paths, monkeypatch
    ):
        """Direct test of the paginator: a page with fewer than 100 items
        is the terminal page — we must not keep requesting and we must
        surface every item we saw. A broken break condition would either
        hang or silently drop results."""
        calls = []
        class _FakeResp:
            def __init__(self, items):
                self._items = items
                self.status_code = 200
                self.text = ""
            def json(self):
                return {"items": self._items}
        def _fake_get(url, headers=None, params=None, timeout=None):
            calls.append(params["page"])
            if params["page"] == 1:
                return _FakeResp([
                    {"title": f"[CI Failure]: mi250_1: G{i}",
                     "number": i, "html_url": f"http://x/{i}", "state": "open"}
                    for i in range(5)  # < 100 → terminal
                ])
            raise AssertionError(f"should not fetch page {params['page']}")
        monkeypatch.setattr(srt.requests, "get", _fake_get)
        res = srt._fetch_existing_ci_failure_issues("fake", "vllm-project/vllm")
        assert calls == [1]
        assert len(res) == 5
        assert "[CI Failure]: mi250_1: G3" in res
