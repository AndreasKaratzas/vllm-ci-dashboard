"""Static contracts for the public publication-status banner."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INDEX = (ROOT / "docs" / "index.html").read_text()
SCRIPT = (ROOT / "docs" / "assets" / "js" / "publication-status.js").read_text()
CSS = (ROOT / "docs" / "assets" / "css" / "dashboard.css").read_text()
MANIFEST = (ROOT / "config" / "public_data_manifest.json").read_text()


def test_publication_status_banner_is_global_accessible_and_cache_busted() -> None:
    banner = 'id="publication-status-banner"'
    assert banner in INDEX
    assert INDEX.index(banner) < INDEX.index('id="tab-projects"')
    assert 'aria-live="polite"' in INDEX
    assert 'aria-atomic="true"' in INDEX
    assert "assets/js/publication-status.js?v=1" in INDEX


def test_publication_status_script_handles_every_nonhealthy_mode() -> None:
    assert "data/vllm/ci/publication_status.json" in SCRIPT
    for mode in ("degraded", "fallback", "mixed", "blocked"):
        assert f"{mode}: Object.freeze" in SCRIPT
    assert "Publication status unavailable" in SCRIPT
    assert "no-store" in SCRIPT


def test_publication_status_rendering_does_not_inject_remote_content() -> None:
    assert ".textContent = view.title" in SCRIPT
    assert ".textContent = view.message" in SCRIPT
    assert "meta.textContent = details.join" in SCRIPT
    assert "innerHTML" not in SCRIPT


def test_publication_status_banner_has_warning_and_critical_styles() -> None:
    assert ".publication-status-banner" in CSS
    assert ".publication-status-banner[hidden]" in CSS
    assert ".publication-status-banner.is-critical" in CSS
    assert ".publication-status-banner.is-critical .publication-status-icon" in CSS


def test_public_status_is_generated_while_private_state_remains_private() -> None:
    assert '"vllm/ci/publication_state.json"' in MANIFEST
    assert '"vllm/ci/publication_status.json"' in MANIFEST
    assert '"vllm/ci/*_state.json"' in MANIFEST
