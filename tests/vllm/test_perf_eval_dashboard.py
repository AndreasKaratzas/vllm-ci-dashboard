"""Frontend + data-contract tests for the Perf Eval tab.

Guards the wiring between the collector's payload and the view: the tab is
registered, the module is loaded after utils.js, the view reads the right
fields (direction, status, provenance), and the committed perf_eval.json keeps
the shape the JS depends on.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
DOCS = ROOT / "docs"
JS = DOCS / "assets" / "js"
PERF_EVAL_JSON = ROOT / "data" / "vllm" / "perf_eval" / "perf_eval.json"


class TestTabRegistration:
    def test_tab_registered_in_registry(self):
        text = (JS / "utils.js").read_text(encoding="utf-8")
        assert "id: 'ci-perf-eval'" in text, "utils.js DashboardTabs must register the Perf Eval tab"
        assert "label: 'Perf Eval'" in text

    def test_index_loads_module_after_utils(self):
        html = (DOCS / "index.html").read_text(encoding="utf-8")
        assert "ci-perf-eval.js" in html, "index.html must load the perf-eval view module"
        assert html.find("utils.js") < html.find("ci-perf-eval.js"), (
            "ci-perf-eval.js must load after utils.js (it uses the shared el()/fetchJSON helpers)"
        )

    def test_module_file_exists(self):
        assert (JS / "ci-perf-eval.js").exists()


class TestViewModule:
    def setup_method(self):
        self.text = (JS / "ci-perf-eval.js").read_text(encoding="utf-8")

    def test_reads_perf_eval_data_file(self):
        assert "data/vllm/perf_eval/perf_eval.json" in self.text

    def test_wires_into_its_tab_panel(self):
        assert "tab-ci-perf-eval" in self.text
        assert "ci-perf-eval-view" in self.text
        assert "MutationObserver" in self.text

    def test_uses_shared_element_factory(self):
        assert re.search(r"\bconst\s+h\s*=\s*el\b", self.text), "should reuse the shared el() factory"

    def test_renders_direction_and_status_semantics(self):
        # The whole point of the view: higher/lower-is-better hints + red/green.
        assert "directionHint" in self.text
        assert "Higher is better" in self.text and "Lower is better" in self.text
        assert "statusColor" in self.text
        assert "deltaBadge" in self.text

    def test_surfaces_commit_and_image_provenance(self):
        assert "provenanceLine" in self.text
        assert "vllm_commit" in self.text
        assert "image" in self.text
        assert "build_url" in self.text

    def test_excludes_nvidia_in_copy(self):
        assert "NVIDIA workloads are excluded" in self.text


class TestPerfEvalDataContract:
    """The committed perf_eval.json must match what the view reads."""

    def _load(self):
        if not PERF_EVAL_JSON.exists():
            pytest.skip("perf_eval.json not generated in this checkout")
        return json.loads(PERF_EVAL_JSON.read_text(encoding="utf-8"))

    def test_top_level_shape(self):
        d = self._load()
        for key in ("generated_at", "metric_meta", "models", "summary", "pipeline", "thresholds"):
            assert key in d, f"perf_eval.json missing {key}"

    def test_metric_meta_carries_direction(self):
        d = self._load()
        for metric, meta in d["metric_meta"].items():
            assert meta.get("direction") in {"higher", "lower"}, f"{metric} missing valid direction"
        assert d["metric_meta"]["tput_per_gpu"]["direction"] == "higher"
        assert d["metric_meta"]["mean_ttft"]["direction"] == "lower"

    def test_models_are_amd_only(self):
        d = self._load()
        for model in d["models"]:
            for dev in model["devices"]:
                assert dev.lower().startswith("mi"), (
                    f"{model['model']} has non-AMD device {dev!r}; NVIDIA must be excluded"
                )

    def test_each_model_traces_to_a_commit(self):
        d = self._load()
        for model in d["models"]:
            latest = model.get("latest") or {}
            assert latest.get("vllm_commit") or latest.get("build_commit"), (
                f"{model['model']} latest run lacks a vLLM commit"
            )

    def test_metric_blocks_have_status_and_series(self):
        d = self._load()
        for model in d["models"]:
            for cfg in model["perf_configs"]:
                for metric, block in cfg["metrics"].items():
                    assert block["status"] in {"good", "bad", "neutral"}
                    assert block["direction"] in {"higher", "lower"}
                    assert isinstance(block["series"], list) and block["series"], (
                        f"{model['model']}/{metric} has empty series"
                    )
                    # every point must carry provenance for traceability
                    assert "vllm_commit" in block["series"][-1]

    def test_no_nvidia_models_present(self):
        # Whether the log is empty (fresh cutover) or populated with real
        # nightlies, NVIDIA workloads must never surface in the AMD-only view.
        d = self._load()
        for m in d["models"]:
            name = m["model"].lower()
            assert not any(gpu in name for gpu in ("h200", "b200", "a100")), (
                f"NVIDIA model {m['model']!r} must be excluded"
            )
