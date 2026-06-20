"""Tests for the dashboard's auth gate + admin-token isolation.

The dashboard is a static GitHub Pages site, so these aren't Selenium tests.
They're static-analysis tests on the committed source files:

    1. Admin tokens (BUILDKITE_TOKEN, PROJECTS_TOKEN) are never used on any
       per-user write path. User-initiated writes must use the user's own
       tokens, never the admin's.
    2. The Test Build browser flow uses the user's BK token to create the
       build, and the workflow's job never touches BUILDKITE_TOKEN.
    3. The auth gate:
         - is loaded before any ci-*.js tab script
         - hides the protected tabs for guests / non-admins
         - stores no password material (PAT-paste architecture, no passwords)
         - the signup issue body carries only ``{email, requested_at}`` —
           identity (github_id, login) comes from github.event.issue.user
    4. The signup workflow uses github.event.issue.user.id as the anti-spoof
       anchor (GitHub itself authenticates the issue author). Without that,
       anyone could spoof another engineer's signup.
    5. The Ready Tickets live sync requires PROJECTS_TOKEN which only the
       admin can set at the repo secrets level; the script fails open into
       dry-run otherwise.

If any of these asserts break, security review the change before merging.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS = ROOT / "scripts" / "vllm"
JS = ROOT / "docs" / "assets" / "js"
WORKFLOWS = ROOT / ".github" / "workflows"


def _read(path: Path) -> str:
    return path.read_text()


def _python_code_contains(path: Path, needle: str) -> bool:
    """Return True iff ``needle`` appears in the Python file's *code*, i.e.
    string literals or identifiers — not module/class/function docstrings
    and not comments. This is the right primitive for "the script does not
    USE this token", which is what security isolation tests really mean.

    A raw ``needle in file_text`` check would fire on a docstring that
    explains *why* the file does not touch the token — a false positive
    that obscures real regressions.
    """
    src = path.read_text()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        # If the file doesn't parse, fall back to the naive check; callers
        # will notice the bigger problem.
        return needle in src

    # Walk the tree, but skip docstrings (which are expression statements
    # whose value is a string constant immediately following a def/class/
    # module header).
    def _is_docstring(node, parent):
        if not isinstance(node, ast.Expr):
            return False
        if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
            return False
        body = getattr(parent, "body", None)
        return bool(body) and body[0] is node

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.found = False

        def _visit_body(self, parent):
            body = getattr(parent, "body", []) or []
            for i, child in enumerate(body):
                if i == 0 and _is_docstring(child, parent):
                    continue
                self.visit(child)

        def visit_Module(self, node):
            self._visit_body(node)

        def visit_FunctionDef(self, node):
            self._visit_body(node)

        def visit_AsyncFunctionDef(self, node):
            self._visit_body(node)

        def visit_ClassDef(self, node):
            self._visit_body(node)

        def visit_Constant(self, node):
            if isinstance(node.value, str) and needle in node.value:
                self.found = True
            self.generic_visit(node)

        def visit_Name(self, node):
            if needle in node.id:
                self.found = True
            self.generic_visit(node)

        def visit_Attribute(self, node):
            if needle in node.attr:
                self.found = True
            self.generic_visit(node)

    v = Visitor()
    v.visit(tree)
    return v.found


# ---------------------------------------------------------------------------
# 1. Admin BUILDKITE_TOKEN is NEVER sent through a per-user write path.
# ---------------------------------------------------------------------------

class TestBuildkiteTokenIsolation:
    def test_register_test_build_has_no_buildkite_token(self):
        # Check the script's *code* (AST string literals + identifiers),
        # not the docstring which explicitly documents that this path
        # does not use BUILDKITE_TOKEN. A naive substring check fires on
        # that documentation and buries any real regression.
        path = SCRIPTS / "register_test_build.py"
        assert not _python_code_contains(path, "BUILDKITE_TOKEN"), (
            "register_test_build.py code must not reference BUILDKITE_TOKEN — "
            "the user's own BK token creates the build in the browser."
        )
        assert not _python_code_contains(path, "api.buildkite.com"), (
            "register_test_build.py must not call the Buildkite API directly."
        )

    def test_register_test_build_workflow_has_no_buildkite_token(self):
        # The dispatch workflow that runs register_test_build.py must not
        # pass BUILDKITE_TOKEN into any step — a user-triggered dispatch
        # must not carry the admin token. We walk the parsed YAML and only
        # inspect step ``env:`` blocks and ``run:`` bodies; comments are
        # documentation (they often *say* we do not use the token, which
        # would fool a naive substring check).
        data = yaml.safe_load(_read(WORKFLOWS / "test-build.yml"))
        for job_name, job in (data.get("jobs") or {}).items():
            for step in job.get("steps", []) or []:
                env = step.get("env", {}) or {}
                for key, val in env.items():
                    assert "BUILDKITE_TOKEN" not in str(key), (
                        f"test-build.yml step {step.get('name')!r} has "
                        f"BUILDKITE_TOKEN in env keys"
                    )
                    assert "BUILDKITE_TOKEN" not in str(val), (
                        f"test-build.yml step {step.get('name')!r} has "
                        f"BUILDKITE_TOKEN in env values"
                    )
                run = step.get("run", "") or ""
                # Allow the literal token name to appear inside run: blocks
                # only in comments (# ...). A reference in actual shell code
                # would indicate the admin token leaking into this path.
                for raw_line in run.splitlines():
                    line = raw_line.split("#", 1)[0]
                    assert "BUILDKITE_TOKEN" not in line, (
                        f"test-build.yml step {step.get('name')!r} run: block "
                        f"uses BUILDKITE_TOKEN in executable code: "
                        f"{raw_line.strip()!r}"
                    )

    def test_testbuild_js_uses_vault_for_bk_token(self):
        src = _read(JS / "ci-testbuild.js")
        # The BK token must go through the encrypted vault — never a raw
        # ``sessionStorage.setItem`` with a plaintext token key. The vault
        # holds AES-GCM ciphertext with a key that lives only in memory.
        assert "window.__tokenVault" in src or "__tokenVault" in src, (
            "ci-testbuild.js must delegate BK token I/O to window.__tokenVault"
        )
        assert "bk_token" in src, (
            "ci-testbuild.js must reference the vault entry name bk_token"
        )
        # The direct API call must be present — browser does the build creation.
        # URL may use templated vars (${BK_ORG}/${BK_PIPELINE}) so we match the
        # structural BK builds endpoint rather than a single hardcoded form.
        assert re.search(
            r"api\.buildkite\.com/v2/organizations/[^/\s'\"`]+/pipelines/[^/\s'\"`]+/builds",
            src,
        ), (
            "ci-testbuild.js must POST to "
            "api.buildkite.com/v2/organizations/<org>/pipelines/<pipeline>/builds "
            "— the browser creates the build with the user's own BK token"
        )
        assert "Retry-After" in src and "data.reset" in src, (
            "ci-testbuild.js should honor short Buildkite 429 reset windows instead of failing immediately"
        )
        assert "quota is shared" in src, (
            "ci-testbuild.js should explain that reusing the same Buildkite token in GitHub Actions shares rate limits"
        )
        # The legacy plaintext keys must NOT appear in committed code — if
        # they do, the dashboard would write raw tokens to sessionStorage.
        assert "setItem('vllm_dashboard_bk_token'" not in src
        assert "setItem(\"vllm_dashboard_bk_token\"" not in src
        assert "setItem('vllm_dashboard_gh_pat'" not in src

    def test_user_signup_workflow_does_not_touch_buildkite_token(self):
        wf = _read(WORKFLOWS / "user-signup.yml")
        assert "BUILDKITE_TOKEN" not in wf

    def test_hourly_master_passes_bk_token_only_to_known_collectors(self):
        """BUILDKITE_TOKEN in hourly-master.yml only reaches read-only steps.

        These are the admin's polling paths — acceptable because no user can
        trigger them. But it must NEVER land in a step that handles
        user-supplied input (register_test_build, process_signup, etc.).
        """
        text = _read(WORKFLOWS / "hourly-master.yml")
        data = yaml.safe_load(text)
        allowed_scripts = {
            "collect_queue_snapshot.py",
            "collect_hotness.py",
            "collect_ci.py",
            "collect_analytics.py",
            "collect_test_builds.py",
        }
        # Walk every step; for any step with BUILDKITE_TOKEN in env, its run:
        # body must only invoke scripts from the allowlist.
        job = data["jobs"]["collect-and-deploy"]
        for step in job["steps"]:
            env = step.get("env", {}) or {}
            if "BUILDKITE_TOKEN" not in env:
                continue
            run_cmd = step.get("run", "") or ""
            assert run_cmd, "step with BUILDKITE_TOKEN must have a run: block"
            invoked = re.findall(r"python\s+scripts/(?:vllm/)?(\S+\.py)", run_cmd)
            invoked = [Path(p).name for p in invoked]
            for script in invoked:
                assert script in allowed_scripts, (
                    f"BUILDKITE_TOKEN reached unauthorized script {script!r} "
                    f"in step {step.get('name')!r}"
                )


# ---------------------------------------------------------------------------
# 2. PROJECTS_TOKEN — admin-only, never user-reachable.
# ---------------------------------------------------------------------------

class TestProjectsTokenIsolation:
    def test_projects_token_only_in_ready_tickets_script(self):
        # Only sync_ready_tickets.py should read PROJECTS_TOKEN.
        hits = []
        for py in SCRIPTS.rglob("*.py"):
            if "PROJECTS_TOKEN" in _read(py):
                hits.append(py.name)
        assert hits == ["sync_ready_tickets.py"], (
            f"Unexpected PROJECTS_TOKEN consumers: {hits}"
        )

    def test_projects_token_only_in_scheduled_workflow(self):
        # PROJECTS_TOKEN must only appear in workflows that are cron/scheduled
        # — never in ones that users can trigger via workflow_dispatch with
        # untrusted inputs.
        for wf in WORKFLOWS.glob("*.yml"):
            text = _read(wf)
            if "PROJECTS_TOKEN" not in text:
                continue
            data = yaml.safe_load(text)
            triggers = data.get(True) or data.get("on") or {}
            assert isinstance(triggers, dict), (
                f"{wf.name}: PROJECTS_TOKEN-using workflow should have a "
                "structured on: trigger list"
            )
            # The only acceptable triggers for a PROJECTS_TOKEN-carrying workflow
            # are schedule, workflow_dispatch (admin-only), and repository_dispatch.
            allowed = {"schedule", "workflow_dispatch", "repository_dispatch"}
            disallowed = set(triggers.keys()) - allowed
            assert not disallowed, (
                f"{wf.name}: PROJECTS_TOKEN-carrying workflow cannot be triggered by {disallowed}"
            )

    def test_sync_ready_tickets_falls_back_to_dry_run_without_token(self):
        src = _read(SCRIPTS / "sync_ready_tickets.py")
        # The script must check for the PAT and force dry-run if missing.
        assert "READY_TICKETS_LIVE" in src
        assert "PROJECTS_TOKEN not set" in src or "PROJECTS_TOKEN" in src
        assert "dry_run_forced" in src


# ---------------------------------------------------------------------------
# 3. Auth gate static integrity (PAT-paste architecture).
# ---------------------------------------------------------------------------

class TestAuthGateWiring:
    def test_auth_js_is_loaded_before_ci_tabs(self):
        html = _read(ROOT / "docs" / "index.html")
        auth_idx = html.find("auth.js")
        assert auth_idx > 0, "auth.js must be included in index.html"
        # auth.js must come before every ci-*.js so the overlay paints first
        # and tab scripts can read window.__authGate during registration.
        for tab in ("ci-testbuild.js", "ci-ready.js", "ci-admin.js"):
            tab_idx = html.find(tab)
            assert tab_idx > auth_idx, (
                f"auth.js must be loaded before {tab} in index.html"
            )

    def test_auth_js_verifies_pat_against_github(self):
        # Signin proves identity by calling GET api.github.com/user with the
        # pasted PAT, not by hashing a password. The endpoint has CORS so
        # it works from the static page.
        src = _read(JS / "auth.js")
        assert "api.github.com/user" in src, (
            "auth.js must verify the PAT via GET api.github.com/user"
        )
        # Must match the returned numeric id against the allowlist, not just
        # the login (logins can be renamed; numeric ids are stable).
        assert "github_id" in src
        assert "me.id" in src

    def test_auth_js_no_longer_builds_public_signup_issue(self):
        # Access is now granted manually by the owner. The browser must not
        # synthesize a GitHub issue body client-side anymore.
        src = _read(JS / "auth.js")
        assert "issues/new" not in src
        assert "public signup issues" in src, (
            "auth.js should explain why repo-backed public signup is disabled"
        )

    def test_auth_js_never_persists_pat(self):
        # The PAT is held in a module-local closure var only. Never written
        # to sessionStorage/localStorage in cleartext.
        src = _read(JS / "auth.js")
        assert re.search(
            r"sessionStorage\.setItem\([^)]*pat", src, re.IGNORECASE
        ) is None
        assert re.search(
            r"localStorage\.setItem\([^)]*pat", src, re.IGNORECASE
        ) is None

    def test_auth_js_declares_protected_tab_rules(self):
        src = _read(JS / "auth.js")
        for tab in ("ci-testbuild", "ci-ready", "ci-admin"):
            assert f"'{tab}'" in src or f'"{tab}"' in src, (
                f"auth.js must include {tab} in the protected-tab rules"
            )
        assert "getProtectedTabs" in src
        assert "getTabMeta" in src

    def test_auth_js_exposes_global_gate_api(self):
        src = _read(JS / "auth.js")
        # ci-*.js scripts depend on these surface members.
        for symbol in (
            "isAuthed",
            "isAdmin",
            "isGuest",
            "getGithubPat",
            "__authGate",
        ):
            assert symbol in src, f"auth.js must expose {symbol}"


class TestTabGatingHardening:
    """Regression tests for the simplified public-shell auth model.

    Protected tabs must stay discoverable in the sidebar, but their
    renderers still need to defend the real tools. Navigation should
    remain simple and auth-agnostic; access control lives in the shared
    tab registry plus the per-tab render guards.
    """

    def test_auth_js_exposes_can_access_tab(self):
        # dashboard.js, utils.js, and the gated renderers call this.
        src = _read(JS / "auth.js")
        assert "canAccessTab" in src, (
            "auth.js must expose canAccessTab(id) on __authGate so "
            "callers can interrogate the session without duplicating "
            "GATED_TABS / ADMIN_ONLY_TABS logic (which drifts)."
        )

    def test_auth_js_exposes_apply_tab_visibility(self):
        src = _read(JS / "auth.js")
        assert "applyTabVisibility" in src, (
            "auth.js must expose applyTabVisibility() on __authGate"
        )

    def test_auth_js_uses_shared_tab_registry(self):
        src = _read(JS / "auth.js")
        assert "window.__dashboardTabs" in src, (
            "auth.js should read tab rules from the shared dashboard registry"
        )
        assert "getProtectedTabs" in src
        assert "getTabMeta" in src

    def test_auth_keeps_identity_and_unlocks_pat_separately(self):
        src = _read(JS / "auth.js")
        assert "Re-enter PAT" in src, (
            "auth.js should preserve the signed-in identity across reloads "
            "and offer an explicit PAT re-entry path for token-backed actions"
        )
        m = re.search(r"function boot\(\) \{(.*?)\n  \}", src, re.DOTALL)
        assert m, "auth.js should define boot()"
        assert "clearSession();" not in m.group(1), (
            "boot() should not erase the stored identity on reload; only the PAT should stay ephemeral"
        )

    def test_auth_js_stamps_body_auth_class(self):
        src = _read(JS / "auth.js")
        assert "__auth-guest" in src, (
            "auth.js must toggle body.__auth-guest on guest sessions"
        )
        assert "__auth-nonadmin" in src, (
            "auth.js must toggle body.__auth-nonadmin on non-admin sessions"
        )

    def test_protected_tabs_are_marked_locked_not_hidden(self):
        auth_src = _read(JS / "auth.js")
        html = _read(ROOT / "docs" / "index.html")
        css = _read(ROOT / "docs" / "assets" / "css" / "dashboard.css")
        assert "__gate-locked" in auth_src, (
            "auth.js should decorate protected tabs with a locked state instead of removing them"
        )
        assert "nav-lock-chip" in css, (
            "dashboard.css should style the protected-tab lock badge"
        )
        assert 'body.__auth-guest [data-tab="ci-testbuild"]' not in auth_src
        assert 'body.__auth-guest [data-tab="ci-testbuild"]' not in html

    def test_dashboard_owns_navigation_shell(self):
        src = _read(JS / "dashboard.js")
        assert "__dashboardNav" in src, (
            "dashboard.js should expose a shared navigation helper for the sidebar"
        )
        assert "_canAccess" not in src, (
            "dashboard.js should no longer own tab access policy; protected views enforce auth internally"
        )

    def test_dashboard_hashchange_listener_present(self):
        src = _read(JS / "dashboard.js")
        assert "hashchange" in src, (
            "dashboard.js must react to hashchange so manual hash edits "
            "activate the right tab in the shared shell"
        )

    def test_utils_registers_tabs_but_does_not_own_auth(self):
        src = _read(JS / "utils.js")
        assert "DashboardTabs" in src, (
            "utils.js should host the shared tab registry metadata"
        )
        assert "data-requires-auth" in src
        assert "data-admin-only" in src
        assert "canAccessTab" not in src, (
            "utils.js should not duplicate auth policy inside registerCISection"
        )

    def test_index_loads_utils_before_auth(self):
        html = _read(ROOT / "docs" / "index.html")
        script_srcs = [
            Path(m.group(1).split("?", 1)[0]).name
            for m in re.finditer(
                r'<script[^>]*\ssrc=["\']([^"\']+)["\']', html, flags=re.IGNORECASE
            )
        ]
        assert script_srcs.index("utils.js") < script_srcs.index("auth.js"), (
            "index.html should load utils.js before auth.js so the shared tab registry exists first"
        )

    def test_testbuild_render_refuses_guests(self):
        src = _read(JS / "ci-testbuild.js")
        assert "canAccessTab" in src or "isAuthed" in src, (
            "ci-testbuild.js render() must check the auth gate before "
            "drawing the Buildkite dispatch form — this is the only "
            "layer that actually touches the user's BK token"
        )
        # The guard has to sit inside render(), not just at module scope.
        # Check there's a render function that early-returns on no-auth.
        m = re.search(
            r"(?:async\s+)?function\s+render\s*\([^)]*\)\s*\{([\s\S]+?)\n\s*\}\s*\n",
            src,
        )
        assert m, "ci-testbuild.js must define a render() function"
        render_body = m.group(1)
        assert (
            "canAccessTab" in render_body
            or "isAuthed" in render_body
            or "__authGate" in render_body
        ), (
            "ci-testbuild.js render() body must gate on auth state; "
            "a module-scope guard is insufficient because render() "
            "is re-called on tab activation"
        )
        assert "promptSignIn" in src, (
            "ci-testbuild.js should offer a direct sign-in path from the locked state"
        )

    def test_ready_render_refuses_guests(self):
        src = _read(JS / "ci-ready.js")
        assert "canAccessTab" in src or "isAuthed" in src, (
            "ci-ready.js render() must check the auth gate before "
            "drawing the triage view (which decrypts the engineer roster)"
        )
        assert "promptSignIn" in src, (
            "ci-ready.js should offer a direct sign-in path from the locked state"
        )

    def test_admin_tab_still_checks_is_admin(self):
        # This was already in place; lock it in as part of the gating
        # contract so a future refactor of ci-admin.js can't silently
        # drop it.
        src = _read(JS / "ci-admin.js")
        assert "isAdmin" in src, (
            "ci-admin.js must check __authGate.isAdmin() before "
            "rendering admin UI"
        )

    def test_index_html_cache_busts_bumped_for_gating_fix(self):
        # Returning visitors to GH Pages cache the old JS aggressively,
        # so the version suffix has to bump when the gating contract
        # changes. These minimums lock in the post-fix versions so a
        # future edit that forgets to bump is caught here.
        html = _read(ROOT / "docs" / "index.html")
        minimums = {
            "auth.js": 3,
            "utils.js": 57,
            "dashboard.js": 53,
            "ci-testbuild.js": 4,
            "ci-ready.js": 3,
        }
        for fname, floor in minimums.items():
            m = re.search(
                re.escape(fname) + r"\?v=(\d+)",
                html,
            )
            assert m, f"index.html must load {fname} with a ?v=N cache-bust"
            got = int(m.group(1))
            assert got >= floor, (
                f"{fname} ?v= must be at least {floor} (the version that "
                f"ships the tab-gating hardening); got {got}"
            )


class TestSignupAntiSpoof:
    def test_workflow_exposes_issue_author_id_to_script(self):
        wf = _read(WORKFLOWS / "user-signup.yml")
        # GitHub itself authenticates the issue author, and its numeric id is
        # the anti-spoof anchor (stable across renames).
        assert "github.event.issue.user.id" in wf, (
            "user-signup.yml must pass the authenticated author's numeric id "
            "into the processor — this is the anti-spoof anchor."
        )
        assert "ISSUE_AUTHOR_ID" in wf
        assert "github.event.issue.user.login" in wf
        assert "ISSUE_AUTHOR" in wf
        assert "github.event.sender.id" in wf
        assert "ISSUE_SENDER_ID" in wf
        assert "TRIGGER_LABEL" in wf

    def test_workflow_has_contents_write_for_users_json(self):
        # The processor commits data/users.json via the Contents API, so the
        # workflow needs contents: write. Without it, the PUT fails and no
        # signups ever land.
        wf = yaml.safe_load(_read(WORKFLOWS / "user-signup.yml"))
        perms = wf.get("permissions") or {}
        assert perms.get("contents") == "write", (
            "user-signup.yml must grant contents: write so process_signup.py "
            "can PUT a new data/users.json via the Contents API."
        )
        assert perms.get("issues") == "write"

    def test_processor_uses_author_id_not_body_login(self):
        src = _read(SCRIPTS / "process_signup.py")
        # Identity must come from the env (authenticated issue author id),
        # never from the issue body.
        assert "ISSUE_AUTHOR_ID" in src
        assert "ISSUE_AUTHOR" in src
        # The committed entry must be github_id-keyed.
        assert "github_id" in src

    def test_processor_parser_drops_identity_fields_from_body(self):
        # Even if a client sneaks ``github_id`` or ``github_login`` into the
        # body, the parser must only surface {email, requested_at}. This is
        # asserted via the unit test suite too, but pin it structurally here
        # so the defence cannot be deleted accidentally.
        src = _read(SCRIPTS / "process_signup.py")
        # parse_signup_body returns only the two audit fields.
        m = re.search(
            r"def parse_signup_body.+?return\s*\{(.+?)\}",
            src,
            re.DOTALL,
        )
        assert m, "parse_signup_body must exist and return a dict literal"
        returned = m.group(1)
        assert '"email"' in returned
        assert '"requested_at"' in returned
        assert "github_login" not in returned
        assert "github_id" not in returned

    def test_processor_only_triggered_by_signup_state_labels(self):
        wf = yaml.safe_load(_read(WORKFLOWS / "user-signup.yml"))
        process_job = wf["jobs"]["process"]
        assert "signup-request" in process_job["if"], (
            "user-signup workflow must run on signup-request events"
        )
        assert "signup-approved" in process_job["if"], (
            "user-signup workflow must also handle explicit admin approvals"
        )
        assert "signup-rejected" in process_job["if"], (
            "user-signup workflow must also handle explicit admin rejections"
        )


# ---------------------------------------------------------------------------
# 4. Admin-only tab & data/users.json structure.
# ---------------------------------------------------------------------------

class TestAdminTab:
    def test_admin_tab_gated_by_isAdmin(self):
        src = _read(JS / "ci-admin.js")
        # Must refuse to render for non-admins.
        assert "isAdmin" in src
        assert "isAuthed" in src

    def test_admin_tab_uses_session_pat_not_sessionstorage(self):
        src = _read(JS / "ci-admin.js")
        # Deletions must PUT users.json with the admin's own PAT, read from
        # the in-memory session via __authGate.getGithubPat(). Never from a
        # raw sessionStorage key, never a hard-coded token.
        assert "getGithubPat" in src, (
            "ci-admin.js must read the session PAT via __authGate.getGithubPat()"
        )
        # Must NOT contain a hard-coded bearer token or API key.
        assert not re.search(r"ghp_[A-Za-z0-9]{20,}", src)
        # Must not write a plaintext token under any of the legacy keys.
        assert "setItem('vllm_dashboard_gh_pat'" not in src
        assert "setItem(\"vllm_dashboard_gh_pat\"" not in src

    def test_admin_tab_surfaces_pending_signup_requests(self):
        src = _read(JS / "ci-admin.js")
        assert "signup-pending" in src, (
            "ci-admin.js should surface pending signup requests for admin review"
        )
        assert "signup-approved" in src and "signup-rejected" in src, (
            "ci-admin.js should let the admin approve or reject pending requests"
        )
        assert "Pending Signup Requests" in src, (
            "ci-admin.js should render a dedicated pending signup section"
        )


class TestAuthEntryGate:
    def test_auth_ui_no_longer_opens_public_signup_issue(self):
        src = _read(JS / "auth.js")
        assert "issues/new" not in src, (
            "auth.js should not invite public users to open signup issues in the repo"
        )
        assert "public signup issues" in src, (
            "auth.js should explain that access is handled manually now"
        )

    def test_users_json_has_expected_shape(self):
        import json
        db = json.loads(_read(ROOT / "data" / "users.json"))
        # New schema: admin_id (numeric GitHub id) + users: [{github_id,
        # email, requested_at}]. No password material anywhere.
        assert isinstance(db.get("admin_id"), int)
        assert db["admin_id"] > 0
        assert isinstance(db.get("users"), list)
        allowed_keys = {"github_id", "email", "requested_at"}
        for u in db["users"]:
            assert set(u.keys()) <= allowed_keys, (
                f"users.json entry has unexpected keys: "
                f"{set(u.keys()) - allowed_keys}"
            )
            assert isinstance(u.get("github_id"), int)
            assert isinstance(u.get("email"), str)
            # No credential fields allowed — the whole point of this migration.
            for forbidden in ("password_hash", "password", "salt", "iterations", "pat"):
                assert forbidden not in u


# ---------------------------------------------------------------------------
# 5. Secrets hygiene in committed files.
# ---------------------------------------------------------------------------

class TestNoLeakedSecrets:
    def test_no_pat_in_any_js(self):
        pat_patterns = [
            re.compile(r"ghp_[A-Za-z0-9]{36,}"),
            re.compile(r"github_pat_[A-Za-z0-9_]{50,}"),
            re.compile(r"bkua_[a-f0-9]{40}"),
        ]
        for js in JS.glob("*.js"):
            text = _read(js)
            for pat in pat_patterns:
                m = pat.search(text)
                assert not m, (
                    f"Possible token leak in {js.name}: {m.group(0)[:10]}…"
                )

    def test_users_json_has_no_plaintext_fields(self):
        text = _read(ROOT / "data" / "users.json")
        for forbidden in ("plaintext", "\"password\":", "\"password_hash\":", "\"salt\":", "pat\":"):
            assert forbidden not in text


# ---------------------------------------------------------------------------
# 6. Encrypted token vault.
#
#    Tokens sit in sessionStorage as AES-GCM ciphertext; the unwrap key is
#    derived from the user's PAT + numeric GitHub id at sign-in and held
#    only in memory. These are structural checks on the committed vault
#    source — the actual crypto runs in the browser and is exercised
#    manually. We assert the file is wired correctly so an accidental
#    refactor doesn't regress the protection.
# ---------------------------------------------------------------------------

class TestTokenVault:
    def test_vault_file_exists_and_is_loaded_first(self):
        vault_path = JS / "token-vault.js"
        assert vault_path.exists(), "token-vault.js must exist"
        html = _read(ROOT / "docs" / "index.html")
        # Extract ``<script src="assets/js/<name>">`` in document order. Raw
        # substring search would fire on HTML comments that *mention* script
        # filenames (e.g. "token-vault MUST load before auth.js"), reporting
        # a false regression.
        script_srcs = [
            # Strip any cache-busting ``?v=...`` query so we compare filenames.
            Path(m.group(1).split("?", 1)[0]).name
            for m in re.finditer(
                r'<script[^>]*\ssrc=["\']([^"\']+)["\']', html, flags=re.IGNORECASE
            )
        ]
        assert "token-vault.js" in script_srcs, (
            "index.html has no <script src=...token-vault.js> tag"
        )
        assert "auth.js" in script_srcs, (
            "index.html has no <script src=...auth.js> tag"
        )
        vault_i = script_srcs.index("token-vault.js")
        auth_i = script_srcs.index("auth.js")
        assert vault_i < auth_i, (
            "token-vault.js must load before auth.js so the unlock call at "
            "sign-in can reach __tokenVault. Order was: "
            f"{script_srcs}"
        )
        for tab in ("ci-testbuild.js", "ci-ready.js", "ci-admin.js"):
            assert tab in script_srcs, f"index.html missing <script> for {tab}"
            assert vault_i < script_srcs.index(tab), (
                f"token-vault.js must load before {tab} (order: {script_srcs})"
            )

    def test_vault_uses_aes_gcm_and_pbkdf2(self):
        src = _read(JS / "token-vault.js")
        assert "AES-GCM" in src, "token-vault must use AES-GCM"
        assert "PBKDF2" in src, "wrap key must be derived via PBKDF2"
        # Non-extractable key is the whole point — reject if someone flips
        # the extractable flag to true (makes raw key readable from JS).
        assert re.search(r"false,[^\n]*non-extractable", src), (
            "deriveKey must be called with extractable=false (non-extractable)"
        )
        # Iteration count — vault wrap key protects session-lifetime ciphertext.
        m = re.search(r"KDF_ITERATIONS\s*=\s*(\d+)", src)
        assert m and int(m.group(1)) >= 100_000, (
            "token-vault KDF_ITERATIONS must be ≥ 100k"
        )

    def test_vault_uses_domain_separator_for_wrap_key(self):
        # The wrap key salt must be scoped with a domain separator so it can
        # never collide with another PBKDF2 derivation that reuses the same
        # (pat, id) inputs.
        src = _read(JS / "token-vault.js")
        assert "|vault" in src, (
            "wrap-key derivation must use a '|vault' domain separator"
        )

    def test_vault_unlock_requires_pat_and_user_id(self):
        # The new KDF signature is ``unlock(pat, userId)`` — both required.
        src = _read(JS / "token-vault.js")
        assert re.search(r"async function unlock\(\s*pat\s*,\s*userId", src), (
            "token-vault.unlock must take (pat, userId) — the new KDF inputs"
        )
        # Validates both arguments before deriving.
        assert "pat required" in src
        assert "userId" in src

    def test_vault_never_persists_pat_or_password(self):
        src = _read(JS / "token-vault.js")
        # The PAT is used only to derive the wrap key and must not be stashed.
        assert "sessionStorage.setItem('pat'" not in src
        assert "localStorage.setItem(" not in src
        assert not re.search(r"^\s*(var|let|const)\s+_pat\b", src, re.MULTILINE)
        assert not re.search(r"^\s*(var|let|const)\s+_password\b", src, re.MULTILINE)

    def test_vault_fresh_iv_per_put(self):
        src = _read(JS / "token-vault.js")
        # AES-GCM demands a fresh IV per (key, plaintext); reusing an IV
        # leaks plaintext xor. Confirm getRandomValues is invoked inside put.
        put_block = re.search(r"async function put\([^)]*\) \{(.+?)\n  \}", src, re.DOTALL)
        assert put_block, "put() must exist in token-vault.js"
        assert "getRandomValues" in put_block.group(1), (
            "put() must draw a fresh IV via crypto.getRandomValues for every call"
        )

    def test_auth_js_unlocks_vault_on_signin(self):
        src = _read(JS / "auth.js")
        assert "__tokenVault" in src
        assert ".unlock(" in src, (
            "auth.js must call window.__tokenVault.unlock at sign-in"
        )
        assert ".lock(" in src, (
            "auth.js must call window.__tokenVault.lock on sign-out"
        )

    def test_auth_js_evicts_legacy_plaintext_keys(self):
        # Older dashboard builds stored the PAT / BK token in plaintext
        # sessionStorage. Any session carried forward from that build must be
        # wiped on boot so the very first load of this version gives us a
        # clean slate. If this assert breaks, the migration is incomplete.
        src = _read(JS / "auth.js")
        assert "removeItem" in src
        assert "vllm_dashboard_gh_pat" in src
        assert "vllm_dashboard_bk_token" in src


# ---------------------------------------------------------------------------
# 7. CSP — the network-layer guard against malicious-script injection.
# ---------------------------------------------------------------------------

class TestContentSecurityPolicy:
    def _csp(self) -> str:
        html = _read(ROOT / "docs" / "index.html")
        # The outer attribute uses double quotes; the CSP body contains
        # single-quoted keywords like 'self' / 'none' / 'unsafe-inline'. A
        # ``[^"']+`` class captures only up to the first apostrophe, yielding
        # "default-src " — a silent false negative that made every CSP
        # assertion pass vacuously or fail misleadingly. Match attribute
        # bodies with a quote-aware capture instead.
        m = re.search(
            r'<meta\s+http-equiv=(["\'])Content-Security-Policy\1'
            r'\s+content=(["\'])(?P<body>(?:(?!\2).)+)\2',
            html,
        )
        assert m, "index.html must declare a Content-Security-Policy meta tag"
        return m.group("body")

    def test_connect_src_is_allowlisted(self):
        csp = self._csp()
        # If connect-src ever becomes '*' or 'https:', a malicious injected
        # script could POST tokens to any host. Assert the two concrete
        # origins are present and no wildcard is.
        connect = re.search(r"connect-src\s+([^;]+)", csp)
        assert connect, "CSP must declare connect-src"
        directive = connect.group(1)
        assert "api.github.com" in directive
        assert "api.buildkite.com" in directive
        assert " * " not in directive and not directive.strip().endswith("*")
        # A bare ``https:`` token (not ``https://<host>``) would allow any
        # HTTPS origin — that's the regression we really want to catch.
        # ``https://api.github.com`` is fine because the scheme is bound to
        # a concrete host.
        bare_https = re.search(r"(?:^|\s)https:(?:\s|$|;)", directive)
        assert not bare_https, (
            "connect-src must enumerate hosts, not open bare 'https:' scheme"
        )

    def test_frame_ancestors_none(self):
        # Defeat clickjacking — the dashboard cannot be framed.
        csp = self._csp()
        assert "frame-ancestors 'none'" in csp

    def test_script_src_is_allowlisted(self):
        csp = self._csp()
        script_src = re.search(r"script-src\s+([^;]+)", csp)
        assert script_src
        directive = script_src.group(1)
        # Only 'self' and the Chart.js CDN are allowed.
        assert "'self'" in directive
        assert "cdn.jsdelivr.net" in directive
        # No arbitrary https: or unsafe-eval.
        assert "unsafe-eval" not in directive
