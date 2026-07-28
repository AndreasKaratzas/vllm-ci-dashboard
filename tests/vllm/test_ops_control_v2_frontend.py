"""Static contracts for the v2 Test Build, Ready Tickets, and Admin controls."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
JS = ROOT / "docs" / "assets" / "js"
INDEX = (ROOT / "docs" / "index.html").read_text()
CSS = (ROOT / "docs" / "assets" / "css" / "ops-control-v2.css").read_text()
TESTBUILD = (JS / "ci-testbuild.js").read_text()
READY = (JS / "ci-ready.js").read_text()
ADMIN = (JS / "ci-admin.js").read_text()
REGISTRY = ROOT / "data" / "vllm" / "ci" / "test_builds" / "index.json"
READY_DATA = json.loads((ROOT / "data" / "vllm" / "ci" / "ready_tickets.json").read_text())


def _active(source: str, marker: str) -> str:
    assert marker in source
    return source.split(marker, 1)[1]


TESTBUILD_V2 = _active(TESTBUILD, "(function renderTestBuildControlV2()")
READY_V2 = _active(READY, "(function renderReadyControlV2()")
ADMIN_V2 = _active(ADMIN, "(function renderAdminControlV2()")


def test_control_assets_use_the_operational_shell_without_inline_layouts():
    assert "assets/css/ops-control-v2.css" in INDEX
    assert "window.OpsControlV2" in TESTBUILD
    assert "classList.add('ops-page', 'ops-control-page')" in TESTBUILD
    for source in (TESTBUILD_V2, READY_V2, ADMIN_V2):
        assert "ui.page(" in source
        assert "style:" not in source


def test_shared_evidence_dialog_is_keyboard_and_source_accessible():
    assert "role: 'dialog'" in TESTBUILD
    assert "'aria-modal': 'true'" in TESTBUILD
    assert "event.key === 'Escape'" in TESTBUILD
    assert "prior.focus()" in TESTBUILD
    assert "event.key !== 'Tab'" in TESTBUILD
    assert "target: '_blank'" in TESTBUILD
    assert "rel: 'noopener'" in TESTBUILD
    assert "No matching evidence" in TESTBUILD


def test_only_v2_registers_each_control_render_loop():
    routes = (
        (TESTBUILD, "(function renderTestBuildControlV2()"),
        (READY, "(function renderReadyControlV2()"),
        (ADMIN, "(function renderAdminControlV2()"),
    )
    for source, marker in routes:
        legacy, active = source.split(marker, 1)
        assert "Lifecycle registration intentionally belongs only to the v2 renderer" in legacy
        assert "document.addEventListener('DOMContentLoaded', render)" not in legacy
        assert "document.addEventListener('auth:changed', render)" not in legacy
        assert active.count("document.addEventListener('click'") == 1
    for source in (TESTBUILD, ADMIN):
        assert source.count("document.addEventListener('DOMContentLoaded', render)") == 1
        assert source.count("document.addEventListener('auth:changed', render)") == 1


def test_ready_defers_large_evidence_until_its_panel_is_active():
    assert "function renderIfActive()" in READY_V2
    assert "panel.classList.contains('active')" in READY_V2
    assert "function initializeReadyRoute()" in READY_V2
    assert "exposeReadyNavigation" not in READY_V2
    assert "document.addEventListener('DOMContentLoaded', initializeReadyRoute)" in READY_V2
    assert "document.addEventListener('auth:changed', initializeReadyRoute)" in READY_V2
    assert "window.addEventListener('hashchange'" in READY_V2
    assert "loadOperations(['reliability'])" in READY_V2
    assert "loadOperations(['ownership'])" in READY_V2


def test_ready_evidence_is_signed_in_and_strictly_read_only():
    assert "Signed-in, read-only failure evidence" in READY_V2
    assert "const plan = await actions.loadPlan()" in READY_V2
    assert "Read only" in READY_V2
    for prohibited in (
        "getGithubPat",
        "/assignees",
        "issues/new",
        "assignIssue",
        "issueCreateUrl",
        "Review draft",
        "loadEngineers",
    ):
        assert prohibited not in READY
    assert "gate.canAccessTab('ci-ready')" in READY_V2


def test_ready_v2_checks_auth_before_any_protected_fetch():
    render_body = READY_V2.split("async function render()", 1)[1].split(
        "\n  function renderIfActive()", 1
    )[0]
    auth_check = render_body.index("gate.canAccessTab('ci-ready')")
    auth_return = render_body.index("signInRequired(container);", auth_check)
    ownership_fetch = render_body.index("loadOperations(['ownership'])")
    ticket_fetch = render_body.index("actions.loadPlan()")
    reliability_fetch = render_body.index("loadOperations(['reliability'])")
    assert auth_check < auth_return < min(ownership_fetch, ticket_fetch, reliability_fetch)
    assert "promptSignIn" in READY_V2
    assert "classList.remove('__gate-locked')" not in READY_V2


def test_ready_routes_tickets_and_ci_ownership_under_one_protected_page():
    for contract in (
        "new Set(['tickets', 'ownership'])",
        "ops_ready_view",
        "'aria-label': 'Ready Tickets view'",
        "role: 'group'",
        "'aria-pressed': active ? 'true' : 'false'",
        "{ id: 'tickets', label: 'Tickets' }",
        "{ id: 'ownership', label: 'CI ownership' }",
        "window.OpsV2.renderOwnership(ownershipHost, ownershipOperations)",
        "window.OpsV2.loadSections(names)",
        "operationsPromises.delete('ownership')",
    ):
        assert contract in READY_V2


def test_ready_migrates_legacy_ci_health_ownership_links():
    migration = READY_V2.split("function migrateLegacyOwnershipRoute()", 1)[1].split(
        "\n  function readyTabs", 1
    )[0]
    assert "url.searchParams.get('ops_health_view') !== 'ownership'" in migration
    assert "url.searchParams.delete('ops_health_view')" in migration
    assert "url.searchParams.set('ops_ready_view', 'ownership')" in migration
    assert "url.hash = 'ci-ready'" in migration
    assert "window.__dashboardNav.switchTab('ci-ready', { updateHash: false })" in migration


def test_ready_has_bounded_filters_and_exact_retained_build_sources():
    for contract in (
        "type: 'search'",
        "Filter by group status",
        "Filter by Buildkite build number",
        "pageSize: 15",
        "filtered.slice(start, start + viewState.pageSize)",
        "build_refs_latest",
        "ref.url || ref.build_url",
        "-day retained summary",
        "ui.dialog(displayGroupIdentity(summary.group)",
    ):
        assert contract in READY_V2


def test_ready_distinguishes_normalized_groups_from_exact_amd_variants():
    assert "Failing Ready groups" in READY_V2
    assert "exact job variants in Analytics" in READY_V2
    assert "amd_test_health.summary" in READY_V2
    assert "displayGroupIdentity(row.summary.group)" in READY_V2
    assert "ui.dialog(displayGroupIdentity(summary.group)" in READY_V2
    assert "identity.hasPlaceholder ? ' (sharded)'" in READY_V2


def test_ready_dialog_deep_links_to_canonical_all_main_group_history():
    assert "function analyticsGroupQuery(group)" in READY_V2
    assert "normalizeGroupIdentity" in READY_V2
    assert ".replace(/\\s*%N" in READY_V2
    assert "ops_analytics_search" in READY_V2
    assert "url.hash = 'ci-analytics'" in READY_V2
    assert "Open all-main Test Groups history" in READY_V2
    assert "analyticsHistoryUrl(summary.group)" in READY_V2
    assert "build_refs_latest" in READY_V2
    assert "replace(/^(?:amd_)?mi" not in READY_V2


def test_ready_separates_latest_failures_from_stale_last_known_state():
    ticket_groups = {
        ticket["summary"]["group"] for ticket in READY_DATA.get("tickets", [])
    }
    stale = [
        summary
        for summary in READY_DATA.get("groups_all", [])
        if summary.get("currently_failing") and summary.get("group") not in ticket_groups
    ]
    assert READY_DATA["failing_groups_total"] == len(ticket_groups)
    assert "cohort: ticket ? 'current' : summary.currently_failing ? 'stale'" in READY_V2
    assert "value: stale" in READY_V2
    assert "Stale last-known failures" in READY_V2
    assert stale, "the retained snapshot should keep stale last-known evidence separately"


def test_ready_ignores_closed_project_items():
    assert "issueState && issueState !== 'open'" in READY


def test_ready_resolves_strict_operations_evidence_and_exact_group_urls():
    for contract in (
        "data/vllm/ci/operations_v2/reliability.json",
        "operations.reliability.group_catalog",
        "entry.raw_names",
        "entry.queues",
        "observation.job_url",
        "observation.step_url",
        "matchedReadyBuild",
        "Exact Buildkite group evidence",
    ):
        assert contract in READY_V2
    assert "Dedicated issues" in READY_V2
    assert "shared master #" in READY_V2


def test_testbuild_retains_launch_and_links_every_available_source():
    for contract in (
        "createBuildkiteBuild",
        "dispatchWorkflow",
        "entry.web_url",
        "ui.githubCommit(repo, entry.commit)",
        "ui.githubBranch(repo, entry.branch)",
        "entry.pr_url || entry.pull_request_url",
        "imageSourceUrl(entry.base_image)",
        "inspectComparison(entry)",
        "The comparison payload does not retain an exact baseline Buildkite URL",
        "verify the checked-out revision in Buildkite",
    ):
        assert contract in TESTBUILD_V2


def test_testbuild_registry_and_launch_credentials_are_release_safe():
    assert REGISTRY.exists()
    assert json.loads(REGISTRY.read_text()) == []
    assert "async function credentialReadiness()" in TESTBUILD_V2
    assert "submit.disabled = !state.credentials.ready" in TESTBUILD_V2
    assert "Launch unavailable: the GitHub credential is not in memory" in TESTBUILD_V2
    assert "Launch unavailable: save a Buildkite token" in TESTBUILD_V2
    assert "const credentials = await credentialReadiness()" in TESTBUILD_V2


def test_admin_is_race_guarded_private_and_auditable():
    assert "let renderSeq = 0" in ADMIN_V2
    assert "const seq = ++renderSeq" in ADMIN_V2
    assert ADMIN_V2.count("if (seq !== renderSeq) return") >= 5
    assert ADMIN_V2.index("renderAccess(container, gate)") < ADMIN_V2.index("Promise.all")
    assert "gate.isAdmin" in ADMIN_V2
    assert "ui.githubUser" in ADMIN_V2
    assert "request.html_url" in ADMIN_V2
    assert "response.data.commit.html_url" in ADMIN_V2


def test_admin_never_renders_contact_fields():
    assert "req.email" not in ADMIN
    assert "u.email" not in ADMIN
    assert "parsed.email" not in ADMIN
    assert "'Email'" not in ADMIN
    assert '"Email"' not in ADMIN
    assert "USERS_SOURCE" not in ADMIN_V2
    assert "Privacy boundary" not in ADMIN_V2
    assert "without exposing contact data" not in ADMIN_V2
    assert "repository records, not protected storage" in ADMIN_V2


def test_control_css_scopes_tables_and_all_required_viewports():
    assert ".ops-page .ocv2-view" in CSS
    assert ".ops-page .ocv2-table-wrap" in CSS
    assert "overflow-x: auto" in CSS
    assert "@media (min-width: 768px) and (max-width: 1100px)" in CSS
    assert "@media (max-width: 767px)" in CSS
    assert ".ocv2-dialog-backdrop" in CSS
    assert "width: 100%" in CSS
    assert "min-width: 0" in CSS
    assert ".ops-page .ocv2-scroll-cue" in CSS
    assert "Scroll horizontally for all columns" in TESTBUILD
    assert "scrollCue: true" in READY_V2
    assert "scrollCue: true" in ADMIN_V2


def test_release_assets_are_cache_busted():
    assert "ops-control-v2.css?v=4" in INDEX
    assert "ci-testbuild.js?v=5" in INDEX
    assert "ci-ready.js?v=8" in INDEX
    assert "ci-admin.js?v=3" in INDEX
