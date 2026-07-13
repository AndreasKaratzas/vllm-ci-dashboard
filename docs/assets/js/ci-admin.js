/**
 * Admin tab — visible only when the signed-in user's github_id matches
 * ``admin_id`` in ``data/users.json``.
 *
 * Lists legacy/manual signup requests plus signed-up users. Because we have
 * no backend, approvals / rejections label the audit issue via the GitHub
 * API, and deletions commit a new ``data/users.json`` to main, all using the
 * admin's own PAT — the same PAT the session was authenticated with, pulled
 * from ``window.__authGate.getGithubPat()``. No token is held server-side.
 *
 * All other users (non-admins, guests) can still discover the tab, but
 * the renderer itself stays read-only and explains the access rule.
 */
window.__OPS_CONTROL_V2_ADMIN__ = true;

(function() {
  const _s = getComputedStyle(document.documentElement);
  const C = {
    g: _s.getPropertyValue('--accent-green').trim() || '#238636',
    y: _s.getPropertyValue('--accent-orange').trim() || '#d29922',
    r: _s.getPropertyValue('--badge-closed').trim() || '#da3633',
    b: _s.getPropertyValue('--accent-blue').trim() || '#1f6feb',
    m: _s.getPropertyValue('--text-muted').trim() || '#8b949e',
    t: _s.getPropertyValue('--text').trim() || '#e6edf3',
    bg: _s.getPropertyValue('--card-bg').trim() || '#161b22',
    bd: _s.getPropertyValue('--border').trim() || '#30363d',
  };
  const h = el;

  const DASHBOARD_REPO = 'AndreasKaratzas/vllm-ci-dashboard';
  const USERS_PATH = 'data/users.json';
  const SIGNUP_PENDING = 'signup-pending';
  const SIGNUP_APPROVED = 'signup-approved';
  const SIGNUP_REJECTED = 'signup-rejected';
  const SIGNUP_PROCESSED = 'signup-processed';
  const SIGNUP_JSON_RE = /```json\s*(\{.*?\})\s*```/s;

  async function gh(pat, path, opts) {
    opts = opts || {};
    const url = path.startsWith('http') ? path : ('https://api.github.com' + path);
    const headers = Object.assign({
      'Accept': 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
    }, opts.headers || {});
    if (pat) headers['Authorization'] = 'token ' + pat;
    const r = await fetch(url, Object.assign({}, opts, { headers }));
    const text = await r.text();
    let data = null; try { data = text ? JSON.parse(text) : null; } catch (e) {}
    return { ok: r.ok, status: r.status, data, text };
  }

  async function loadUsers() {
    const empty = { admin_id: 0, users: [] };
    try {
      const r = await fetch('data/users.json?_=' + Math.floor(Date.now()/1000));
      if (!r.ok) return empty;
      return await r.json();
    } catch (e) {
      return empty;
    }
  }

  function parseSignupAudit(body) {
    if (!body) return null;
    const match = body.match(SIGNUP_JSON_RE);
    if (!match) return null;
    try {
      const parsed = JSON.parse(match[1]);
      if (!parsed || typeof parsed !== 'object') return null;
      return {
        requested_at: String(parsed.requested_at || '').trim(),
      };
    } catch (e) {
      return null;
    }
  }

  function hasLabel(issue, label) {
    return !!((issue && issue.labels) || []).find((entry) => {
      const name = typeof entry === 'string' ? entry : (entry && entry.name);
      return name === label;
    });
  }

  async function loadPendingSignups(pat) {
    const r = await gh(
      pat,
      `/repos/${DASHBOARD_REPO}/issues?state=open&labels=${encodeURIComponent(SIGNUP_PENDING)}&per_page=100`
    );
    if (!r.ok || !Array.isArray(r.data)) return [];
    return r.data
      .filter((issue) => !issue.pull_request)
      .filter((issue) => !hasLabel(issue, SIGNUP_PROCESSED))
      .map((issue) => {
        const audit = parseSignupAudit(issue.body || '');
        return {
          number: issue.number,
          title: issue.title || '',
          html_url: issue.html_url || '',
          login: (issue.user && issue.user.login) || '',
          github_id: (issue.user && issue.user.id) || 0,
          requested_at: audit && audit.requested_at || issue.created_at || '',
          labels: ((issue.labels || []).map((entry) => typeof entry === 'string' ? entry : entry.name)).filter(Boolean),
        };
      })
      .sort((a, b) => (a.requested_at || '').localeCompare(b.requested_at || ''));
  }

  // Resolve numeric GitHub ids to logins. Uses the public endpoint
  // ``GET /user/:id`` which returns the current profile — no auth needed,
  // but we send the session PAT when available to avoid unauth rate limits.
  async function resolveLogins(ids, pat) {
    const out = {};
    await Promise.all(ids.map(async (id) => {
      if (!id) return;
      try {
        const r = await gh(pat, '/user/' + id);
        if (r.ok && r.data && r.data.login) out[id] = r.data.login;
      } catch (e) {}
    }));
    return out;
  }

  function _b64EncodeUtf8(str) {
    return btoa(unescape(encodeURIComponent(str)));
  }

  async function writeUsersJson(pat, nextDb, commitMessage) {
    const meta = await gh(pat, `/repos/${DASHBOARD_REPO}/contents/${USERS_PATH}?ref=main`);
    if (!meta.ok) return { ok: false, status: meta.status, error: 'Could not fetch existing users.json sha' };
    const sha = meta.data && meta.data.sha;
    const body = {
      message: commitMessage,
      content: _b64EncodeUtf8(JSON.stringify(nextDb, null, 2) + '\n'),
      sha: sha,
      branch: 'main',
    };
    const r = await gh(pat, `/repos/${DASHBOARD_REPO}/contents/${USERS_PATH}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    return r;
  }

  async function labelIssue(pat, issueNumber, label) {
    return gh(pat, `/repos/${DASHBOARD_REPO}/issues/${issueNumber}/labels`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ labels: [label] }),
    });
  }

  function renderAccessDenied(container, reason, offerSignIn) {
    container.innerHTML = '';
    container.append(h('h2', { text: 'Admin', style: { marginBottom: '6px' } }));
    const card = h('div', { style: { background: C.bg, border: `1px solid ${C.bd}`, borderRadius: '6px', padding: '14px', marginTop: '10px' } });
    card.append(h('strong', { text: 'Admin access required', style: { color: C.r } }));
    card.append(h('p', { text: reason || 'Sign in as the dashboard admin to manage users.', style: { color: C.m, marginTop: '6px', fontSize: '13px' } }));
    if (offerSignIn) {
      const unlock = h('button', {
        text: 'Sign in',
        style: { marginTop: '8px', padding: '7px 12px', borderRadius: '6px', border: `1px solid ${C.bd}`, background: C.bg, color: C.t, cursor: 'pointer', fontWeight: '600' },
      });
      unlock.addEventListener('click', () => {
        const auth = window.__authGate;
        if (auth && auth.promptSignIn) auth.promptSignIn();
      });
      card.append(unlock);
    }
    container.append(card);
  }

  function renderPatBanner(container, state) {
    const card = h('div', { style: { background: C.bg, border: `1px solid ${C.bd}`, borderRadius: '6px', padding: '10px 14px', marginBottom: '14px', fontSize: '13px' } });
    card.append(h('strong', { text: 'Admin operations', style: { color: C.t } }));
    card.append(h('div', {
      text: 'Signup approval / rejection labels audit issues with your PAT, and user deletion rewrites data/users.json on main. Nothing is saved server-side.',
      style: { color: C.m, marginTop: '4px', fontSize: '12px' },
    }));
    const status = h('div', { style: { fontSize: '11px', marginTop: '6px' } });
    const gate = window.__authGate;
    const pat = gate && gate.getGithubPat ? gate.getGithubPat() : '';
    if (pat) {
      status.textContent = 'Session PAT available — deletions will use it directly.';
      status.style.color = C.g;
    } else {
      status.textContent = 'Session PAT not in memory (tab reloaded). Sign out and back in to re-enter it.';
      status.style.color = C.y;
    }
    card.append(status);
    container.append(card);
  }

  function renderPendingSignups(container, state) {
    const requests = state.pending || [];
    const card = h('div', { style: { background: C.bg, border: `1px solid ${C.bd}`, borderRadius: '8px', padding: '14px 18px', marginBottom: '16px' } });
    card.append(h('h3', { text: `Pending Signup Requests (${requests.length})`, style: { marginTop: 0, fontSize: '15px' } }));
    card.append(h('p', {
      text: 'A request stays pending until you explicitly approve or reject it. Approval adds the user to data/users.json through the signup workflow; rejection leaves the issue as an audit record. The normal path is still manual owner-managed allowlist edits.',
      style: { color: C.m, fontSize: '13px', marginTop: '4px', marginBottom: '12px' },
    }));

    if (!requests.length) {
      card.append(h('p', { text: 'No pending signup requests right now.', style: { color: C.m, fontSize: '13px' } }));
      container.append(card);
      return;
    }

    const table = h('table', { style: { width: '100%', borderCollapse: 'collapse', fontSize: '12px' } });
    const thead = h('thead');
    const hr = h('tr');
    ['Issue', 'Requester', 'GitHub id', 'Requested', 'Action'].forEach((c) => {
      hr.append(h('th', { text: c, style: { textAlign: 'left', padding: '6px 8px', borderBottom: `1px solid ${C.bd}`, color: C.m, fontWeight: '600', textTransform: 'uppercase', fontSize: '10px', letterSpacing: '0.04em' } }));
    });
    thead.append(hr);
    table.append(thead);

    const tbody = h('tbody');
    const gate = window.__authGate;
    const pat = gate && gate.getGithubPat ? gate.getGithubPat() : '';
    for (const req of requests) {
      const tr = h('tr');
      const issueCell = h('td', { style: { padding: '6px 8px', borderBottom: `1px solid ${C.bd}` } });
      issueCell.append(h('a', {
        text: `#${req.number}`,
        href: req.html_url,
        target: '_blank',
        rel: 'noopener',
        style: { color: C.b, textDecoration: 'none', fontWeight: '600' },
      }));
      issueCell.append(h('div', { text: req.title || 'signup request', style: { color: C.m, fontSize: '11px', marginTop: '3px' } }));
      tr.append(issueCell);
      tr.append(h('td', { text: req.login ? '@' + req.login : '—', style: { padding: '6px 8px', borderBottom: `1px solid ${C.bd}`, fontFamily: 'monospace', fontSize: '11px' } }));
      tr.append(h('td', { text: String(req.github_id || '—'), style: { padding: '6px 8px', borderBottom: `1px solid ${C.bd}`, fontFamily: 'monospace', fontSize: '11px', color: C.m } }));
      tr.append(h('td', { text: (req.requested_at || '').slice(0, 10) || '—', style: { padding: '6px 8px', borderBottom: `1px solid ${C.bd}`, color: C.m } }));

      const actionCell = h('td', { style: { padding: '6px 8px', borderBottom: `1px solid ${C.bd}`, whiteSpace: 'nowrap' } });
      const approveBtn = h('button', {
        text: 'Approve',
        style: { padding: '4px 10px', background: C.g, color: '#fff', border: 'none', borderRadius: '4px', cursor: 'pointer', fontSize: '11px', marginRight: '6px' },
      });
      const rejectBtn = h('button', {
        text: 'Reject',
        style: { padding: '4px 10px', background: C.r, color: '#fff', border: 'none', borderRadius: '4px', cursor: 'pointer', fontSize: '11px' },
      });
      const helper = h('div', { style: { color: C.m, fontSize: '11px', marginTop: '5px' } });

      const queueAction = async (label, btn, verb) => {
        if (!pat) { alert('Session PAT not in memory. Sign out and back in, then retry.'); return; }
        if (!confirm(`${verb} @${req.login || req.github_id}? This labels issue #${req.number} and lets the signup workflow finish the state change.`)) return;
        approveBtn.disabled = true;
        rejectBtn.disabled = true;
        btn.textContent = verb + '…';
        const res = await labelIssue(pat, req.number, label);
        if (res.ok) {
          helper.textContent = `${verb} queued. The signup workflow will update the issue and refresh access shortly.`;
          helper.style.color = C.g;
          setTimeout(render, 1500);
        } else {
          alert(`${verb} failed: HTTP ${res.status}\n${(res.text || '').slice(0, 200)}`);
          approveBtn.disabled = false;
          rejectBtn.disabled = false;
          approveBtn.textContent = 'Approve';
          rejectBtn.textContent = 'Reject';
        }
      };

      approveBtn.addEventListener('click', () => queueAction(SIGNUP_APPROVED, approveBtn, 'Approve'));
      rejectBtn.addEventListener('click', () => queueAction(SIGNUP_REJECTED, rejectBtn, 'Reject'));

      actionCell.append(approveBtn, rejectBtn, helper);
      tr.append(actionCell);
      tbody.append(tr);
    }
    table.append(tbody);
    card.append(table);
    container.append(card);
  }

  function renderUsersTable(container, state) {
    const users = state.db.users || [];
    const card = h('div', { style: { background: C.bg, border: `1px solid ${C.bd}`, borderRadius: '8px', padding: '14px 18px' } });
    card.append(h('h3', { text: `Users (${users.length})`, style: { marginTop: 0, fontSize: '15px' } }));
    if (!users.length) {
      card.append(h('p', { text: 'No signed-up users yet. Add users by editing data/users.json on main, or process a legacy/manual signup issue here.', style: { color: C.m, fontSize: '13px' } }));
      container.append(card);
      return;
    }

    const table = h('table', { style: { width: '100%', borderCollapse: 'collapse', fontSize: '12px' } });
    const thead = h('thead');
    const hr = h('tr');
    ['GitHub login', 'GitHub id', 'Signed up', 'Action'].forEach((c) => {
      hr.append(h('th', { text: c, style: { textAlign: 'left', padding: '6px 8px', borderBottom: `1px solid ${C.bd}`, color: C.m, fontWeight: '600', textTransform: 'uppercase', fontSize: '10px', letterSpacing: '0.04em' } }));
    });
    thead.append(hr);
    table.append(thead);

    const tbody = h('tbody');
    for (const u of users) {
      const isAdmin = state.db.admin_id && state.db.admin_id === u.github_id;
      const login = state.loginsById[u.github_id] || '';
      const tr = h('tr');
      tr.append(h('td', {
        text: (login ? '@' + login : '(unresolved)') + (isAdmin ? ' (admin)' : ''),
        style: { padding: '6px 8px', borderBottom: `1px solid ${C.bd}`, fontFamily: 'monospace', fontSize: '11px' },
      }));
      tr.append(h('td', { text: String(u.github_id || '—'), style: { padding: '6px 8px', borderBottom: `1px solid ${C.bd}`, fontFamily: 'monospace', fontSize: '11px', color: C.m } }));
      tr.append(h('td', { text: (u.requested_at || '').slice(0, 10) || '—', style: { padding: '6px 8px', borderBottom: `1px solid ${C.bd}`, color: C.m } }));

      const actionCell = h('td', { style: { padding: '6px 8px', borderBottom: `1px solid ${C.bd}` } });
      if (isAdmin) {
        actionCell.append(h('span', { text: '— protected —', style: { color: C.m, fontSize: '11px' } }));
      } else {
        const btn = h('button', {
          text: 'Delete',
          style: { padding: '4px 10px', background: C.r, color: '#fff', border: 'none', borderRadius: '4px', cursor: 'pointer', fontSize: '11px' },
        });
        btn.addEventListener('click', async () => {
          const gate = window.__authGate;
          const pat = gate && gate.getGithubPat ? gate.getGithubPat() : '';
          if (!pat) { alert('Session PAT not in memory. Sign out and back in, then retry.'); return; }
          const displayLogin = login || ('id=' + u.github_id);
          if (!confirm(`Delete user ${displayLogin}? This commits a new data/users.json to main.`)) return;
          btn.disabled = true;
          btn.textContent = 'Deleting…';
          const next = Object.assign({}, state.db, {
            users: (state.db.users || []).filter((x) => x.github_id !== u.github_id),
          });
          const r = await writeUsersJson(pat, next, `admin: remove user ${displayLogin}`);
          if (r.ok) {
            state.db = next;
            render();
          } else {
            alert(`Delete failed: HTTP ${r.status}\n${(r.text || '').slice(0, 200)}`);
            btn.disabled = false;
            btn.textContent = 'Delete';
          }
        });
        actionCell.append(btn);
      }
      tr.append(actionCell);
      tbody.append(tr);
    }
    table.append(tbody);
    card.append(table);
    container.append(card);
  }

  async function render() {
    if (window.__OPS_CONTROL_V2_ADMIN__) return;
    const container = document.getElementById('ci-admin-view');
    if (!container) return;

    const gate = window.__authGate;
    if (!gate || !gate.isAuthed()) {
      renderAccessDenied(container, 'Sign in first.', true);
      return;
    }
    if (!gate.isAdmin()) {
      renderAccessDenied(container, `You're signed in as @${gate.getLogin()}, not the dashboard admin.`, false);
      return;
    }

    container.innerHTML = '';
    container.append(h('h2', { text: 'Admin', style: { marginBottom: '6px' } }));
    container.append(h('p', { text: 'Review legacy/manual signup requests, then manage dashboard users. Approvals and deletions use your signed-in PAT. The primary access path is manual allowlist management in data/users.json.', style: { color: C.m, marginTop: 0, marginBottom: '14px', fontSize: '13px' } }));

    const db = await loadUsers();
    const pat = gate.getGithubPat ? gate.getGithubPat() : '';
    const pending = await loadPendingSignups(pat);
    const ids = (db.users || []).map((u) => u.github_id).filter(Boolean);
    const loginsById = await resolveLogins(ids, pat);
    const state = { db, loginsById, pending, render };
    renderPatBanner(container, state);
    renderPendingSignups(container, state);
    renderUsersTable(container, state);
  }

  window.OpsAdminActions = {
    DASHBOARD_REPO,
    USERS_PATH,
    SIGNUP_APPROVED,
    SIGNUP_REJECTED,
    loadUsers,
    loadPendingSignups,
    resolveLogins,
    writeUsersJson,
    labelIssue,
  };

  // Lifecycle registration intentionally belongs only to the v2 renderer.
})();

(function renderAdminControlV2() {
  'use strict';
  window.__OPS_CONTROL_V2_ADMIN__ = true;

  const ui = window.OpsControlV2;
  const actions = window.OpsAdminActions;
  if (!ui || !actions) return;

  const h = ui.h;
  const PENDING_SOURCE = `https://github.com/${actions.DASHBOARD_REPO}/issues?q=is%3Aissue+is%3Aopen+label%3Asignup-pending+-label%3Asignup-processed`;
  const loginCache = new Map();
  let renderSeq = 0;

  function dateOnly(value) {
    return value ? String(value).slice(0, 10) : 'Unavailable';
  }

  function profileSource(login, githubId) {
    if (login) return ui.githubUser(login);
    return githubId ? `https://api.github.com/user/${encodeURIComponent(githubId)}` : '';
  }

  function focusSection(id) {
    const node = document.getElementById(id);
    if (!node) return;
    node.hidden = false;
    node.setAttribute('tabindex', '-1');
    node.scrollIntoView({ behavior: 'smooth', block: 'start' });
    node.focus({ preventScroll: true });
  }

  function inspectRequest(request) {
    const sources = h('div', { cls: 'ocv2-source-list' });
    ui.append(sources, [
      ui.external('Open audit issue', request.html_url),
      ui.external('Requester profile', ui.githubUser(request.login)),
    ]);
    ui.dialog(
      `Signup audit #${request.number}`,
      request.login ? `Requested by @${request.login}` : 'Requester profile could not be resolved',
      [
        ui.definitionList([
          { label: 'State', value: ui.badge('pending', 'is-warning') },
          { label: 'GitHub id', value: request.github_id || 'Unavailable' },
          { label: 'Requested', value: dateOnly(request.requested_at) },
          { label: 'Audit title', value: request.title || 'Signup request' },
        ]),
        ui.state('is-info', 'Exact audit evidence', 'Approval and rejection add a label to this issue; the issue remains the durable audit source.'),
        sources,
      ]
    );
  }

  function inspectUser(user, login, isAdmin) {
    const sources = h('div', { cls: 'ocv2-source-list' });
    sources.append(ui.external('GitHub profile', profileSource(login, user.github_id)));
    ui.dialog(
      login ? `@${login}` : `GitHub user ${user.github_id}`,
      isAdmin ? 'Protected dashboard administrator' : 'Dashboard allowlist member',
      [
        ui.definitionList([
          { label: 'GitHub id', value: user.github_id || 'Unavailable' },
          { label: 'Role', value: ui.badge(isAdmin ? 'admin' : 'member', isAdmin ? 'is-info' : '') },
          { label: 'Added', value: dateOnly(user.requested_at) },
          { label: 'Mutation policy', value: isAdmin ? 'Protected from deletion' : 'Admin PAT required for deletion' },
        ]),
        ui.state('is-info', 'Control identity', 'Access operations use the GitHub profile and numeric id shown above.'),
        sources,
      ]
    );
  }

  function resultDialog(title, message, tone, sourceLabel, sourceUrl) {
    const content = [ui.state(tone, title, message)];
    if (sourceUrl) content.push(h('div', { cls: 'ocv2-source-list' }, [ui.external(sourceLabel, sourceUrl)]));
    ui.dialog(title, 'Admin mutation result', content);
  }

  function renderAccess(container, gate) {
    const signedIn = !!(gate && gate.isAuthed && gate.isAuthed());
    ui.page(container, {
      id: 'admin',
      title: 'Admin Control',
      eyebrow: 'AMD CI OPERATIONS',
      description: 'Audit signup requests and manage dashboard access through repository-backed GitHub records.',
    });

    if (!signedIn) {
      const signIn = ui.button('Sign in', function() {
        if (gate && gate.promptSignIn) gate.promptSignIn();
      }, 'is-primary');
      container.append(ui.state('is-warning', 'Admin authentication required', 'Read-only operational views remain public; access mutations require the configured dashboard administrator.', signIn));
      return true;
    }
    if (!gate.isAdmin || !gate.isAdmin()) {
      container.append(ui.state(
        'is-warning',
        'Administrator role required',
        `Signed in as @${gate.getLogin ? gate.getLogin() : 'unknown'}. This view does not load its user or audit inventory for non-admin sessions.`
      ));
      return true;
    }
    return false;
  }

  function renderAdminDashboard(container, state, seq) {
    const users = Array.isArray(state.db.users) ? state.db.users : [];
    const pending = Array.isArray(state.pending) ? state.pending : [];
    const adminCount = users.filter((user) => Number(user.github_id) === Number(state.db.admin_id)).length;
    let scope = 'all';
    let role = 'all';

    const search = h('input', {
      cls: 'ocv2-input',
      type: 'search',
      placeholder: 'Search login, id, issue, or title',
      'aria-label': 'Search admin evidence',
    });
    const scopeSelect = h('select', { cls: 'ocv2-select', 'aria-label': 'Evidence scope' }, [
      h('option', { value: 'all', text: 'All evidence' }),
      h('option', { value: 'pending', text: 'Pending audits' }),
      h('option', { value: 'users', text: 'Current users' }),
    ]);
    const roleSelect = h('select', { cls: 'ocv2-select', 'aria-label': 'User role' }, [
      h('option', { value: 'all', text: 'All roles' }),
      h('option', { value: 'admin', text: 'Administrators' }),
      h('option', { value: 'member', text: 'Members' }),
    ]);

    let pendingPanel;
    let usersPanel;

    function selectEvidence(nextScope, sectionId, nextRole) {
      scope = nextScope;
      scopeSelect.value = nextScope;
      if (nextRole) {
        role = nextRole;
        roleSelect.value = nextRole;
      }
      refreshTables();
      window.requestAnimationFrame(() => focusSection(sectionId));
    }

    container.append(ui.kpis([
      {
        label: 'Pending audits',
        value: pending.length,
        meta: 'open signup requests',
        tone: pending.length ? 'is-warning' : 'is-success',
        onClick: () => selectEvidence('pending', 'ocv2-admin-pending'),
      },
      {
        label: 'Allowlisted users',
        value: users.length,
        meta: 'inspect current directory',
        onClick: () => selectEvidence('users', 'ocv2-admin-users', 'all'),
      },
      {
        label: 'Administrators',
        value: adminCount,
        meta: 'protected identities',
        tone: 'is-info',
        onClick: () => selectEvidence('users', 'ocv2-admin-users', 'admin'),
      },
      {
        label: 'Audit source',
        value: 'GitHub',
        meta: 'open pending issue log',
        href: PENDING_SOURCE,
        external: true,
      },
    ]));

    const patAvailable = !!state.pat;
    container.append(ui.state(
      patAvailable ? 'is-info' : 'is-warning',
      patAvailable ? 'Admin mutation session ready' : 'Mutation credential unavailable',
      patAvailable
        ? 'Actions use the signed-in PAT directly. GitHub issues and commits are repository records, not protected storage.'
        : 'This authenticated session has no PAT in memory. Sign out and back in before attempting an approval, rejection, or deletion.',
      ui.external('Pending issue log', PENDING_SOURCE)
    ));

    const controls = h('div', { cls: 'ocv2-toolbar' });
    ui.append(controls, [ui.field('Search', search), ui.field('Evidence', scopeSelect), ui.field('User role', roleSelect)]);
    const filterPanel = ui.panel('Evidence controls', 'Filters are local to the loaded sources', controls);
    container.append(filterPanel.root);

    pendingPanel = ui.panel('Pending signup audits', 'GitHub issues awaiting an explicit decision', []);
    pendingPanel.root.id = 'ocv2-admin-pending';
    usersPanel = ui.panel('Dashboard users', 'GitHub identities used by the access controls', []);
    usersPanel.root.id = 'ocv2-admin-users';
    container.append(pendingPanel.root, usersPanel.root);

    function pendingActions(request) {
      const cell = h('div', { cls: 'ocv2-actions' });
      const approve = ui.button('Approve', () => mutateRequest(actions.SIGNUP_APPROVED, 'Approval', request, approve, reject), 'is-primary');
      const reject = ui.button('Reject', () => mutateRequest(actions.SIGNUP_REJECTED, 'Rejection', request, reject, approve), 'is-danger');
      approve.disabled = !patAvailable;
      reject.disabled = !patAvailable;
      if (!patAvailable) {
        approve.title = 'Sign in again to restore the in-memory PAT';
        reject.title = 'Sign in again to restore the in-memory PAT';
      }
      ui.append(cell, [approve, reject]);
      return cell;
    }

    async function mutateRequest(label, noun, request, primary, secondary) {
      if (seq !== renderSeq || !state.pat) return;
      if (!window.confirm(`${noun} for @${request.login || request.github_id}? This labels audit issue #${request.number}.`)) return;
      primary.disabled = true;
      secondary.disabled = true;
      primary.textContent = noun + ' pending';
      const response = await actions.labelIssue(state.pat, request.number, label);
      if (seq !== renderSeq) return;
      if (response.ok) {
        resultDialog(
          `${noun} queued`,
          `Audit issue #${request.number} received the ${label} label. The signup workflow owns the resulting allowlist update.`,
          'is-success',
          'Open audit issue',
          request.html_url
        );
        window.setTimeout(() => { if (seq === renderSeq) render(); }, 900);
        return;
      }
      primary.disabled = false;
      secondary.disabled = false;
      primary.textContent = noun === 'Approval' ? 'Approve' : 'Reject';
      resultDialog(
        `${noun} failed`,
        `GitHub returned HTTP ${response.status}. No successful state change is being claimed.`,
        'is-danger',
        'Inspect audit issue',
        request.html_url
      );
    }

    async function removeUser(user, login, button) {
      if (seq !== renderSeq || !state.pat) return;
      const identity = login ? `@${login}` : `GitHub id ${user.github_id}`;
      if (!window.confirm(`Remove ${identity} from the dashboard allowlist? This commits ${actions.USERS_PATH} to main.`)) return;
      button.disabled = true;
      button.textContent = 'Removing';
      const nextDb = Object.assign({}, state.db, {
        users: users.filter((candidate) => Number(candidate.github_id) !== Number(user.github_id)),
      });
      const response = await actions.writeUsersJson(state.pat, nextDb, `admin: remove user ${identity}`);
      if (seq !== renderSeq) return;
      if (response.ok) {
        const sourceUrl = response.data && response.data.commit && response.data.commit.html_url
          || response.data && response.data.content && response.data.content.html_url
          || '';
        resultDialog('User removed', `${identity} was removed from the allowlist on main.`, 'is-success', 'Open mutation source', sourceUrl);
        render();
        return;
      }
      button.disabled = false;
      button.textContent = 'Remove';
      resultDialog('Removal failed', `GitHub returned HTTP ${response.status}. The loaded allowlist remains unchanged.`, 'is-danger');
    }

    function refreshTables() {
      const query = search.value.trim().toLowerCase();
      scope = scopeSelect.value;
      role = roleSelect.value;

      const visiblePending = pending.filter((request) => {
        const haystack = [request.number, request.title, request.login, request.github_id].join(' ').toLowerCase();
        return !query || haystack.includes(query);
      });
      const visibleUsers = users.filter((user) => {
        const login = state.loginsById[user.github_id] || '';
        const isAdmin = Number(user.github_id) === Number(state.db.admin_id);
        if (role === 'admin' && !isAdmin) return false;
        if (role === 'member' && isAdmin) return false;
        return !query || [login, user.github_id, isAdmin ? 'admin' : 'member'].join(' ').toLowerCase().includes(query);
      });

      pendingPanel.root.hidden = scope === 'users';
      usersPanel.root.hidden = scope === 'pending';
      pendingPanel.header.querySelector('.ocv2-panel-meta').textContent = `${visiblePending.length} of ${pending.length} open audits`;
      usersPanel.header.querySelector('.ocv2-panel-meta').textContent = `${visibleUsers.length} of ${users.length} identities`;

      pendingPanel.body.replaceChildren(ui.table([
        {
          label: 'Audit',
          render: (request) => ui.linkButton(`#${request.number} ${request.title || 'Signup request'}`, () => inspectRequest(request), 'Inspect signup audit'),
        },
        {
          label: 'Requester',
          render: (request) => ui.external(request.login ? `@${request.login}` : `GitHub id ${request.github_id}`, profileSource(request.login, request.github_id)),
        },
        { label: 'GitHub id', key: 'github_id', nowrap: true },
        { label: 'Requested', nowrap: true, render: (request) => dateOnly(request.requested_at) },
        { label: 'Audit source', nowrap: true, render: (request) => ui.external('Open issue', request.html_url) },
        { label: 'Decision', nowrap: true, render: pendingActions },
      ], visiblePending, {
        compact: true,
        scrollCue: true,
        caption: 'Open signup audit issues and their exact GitHub action records.',
        empty: 'No pending audit matches the current search.',
      }));

      usersPanel.body.replaceChildren(ui.table([
        {
          label: 'Identity',
          render: (user) => {
            const login = state.loginsById[user.github_id] || '';
            const isAdmin = Number(user.github_id) === Number(state.db.admin_id);
            return ui.linkButton(login ? `@${login}` : `GitHub user ${user.github_id}`, () => inspectUser(user, login, isAdmin), 'Inspect allowlist identity');
          },
        },
        {
          label: 'Profile',
          render: (user) => {
            const login = state.loginsById[user.github_id] || '';
            return ui.external(login ? 'GitHub profile' : `Resolve id ${user.github_id}`, profileSource(login, user.github_id));
          },
        },
        {
          label: 'Role',
          render: (user) => {
            const isAdmin = Number(user.github_id) === Number(state.db.admin_id);
            return ui.badge(isAdmin ? 'admin' : 'member', isAdmin ? 'is-info' : '');
          },
        },
        { label: 'Added', nowrap: true, render: (user) => dateOnly(user.requested_at) },
        {
          label: 'Action',
          nowrap: true,
          render: (user) => {
            const isAdmin = Number(user.github_id) === Number(state.db.admin_id);
            if (isAdmin) return ui.badge('protected', 'is-info');
            const login = state.loginsById[user.github_id] || '';
            const button = ui.button('Remove', () => removeUser(user, login, button), 'is-danger');
            button.disabled = !patAvailable;
            if (!patAvailable) button.title = 'Sign in again to restore the in-memory PAT';
            return button;
          },
        },
      ], visibleUsers, {
        compact: true,
        scrollCue: true,
        caption: 'Current dashboard identities available to access controls.',
        empty: 'No allowlist identity matches the current filters.',
      }));
    }

    search.addEventListener('input', refreshTables);
    scopeSelect.addEventListener('change', refreshTables);
    roleSelect.addEventListener('change', refreshTables);
    refreshTables();
  }

  async function render() {
    const seq = ++renderSeq;
    const container = document.getElementById('ci-admin-view');
    if (!container) return;
    const gate = window.__authGate;
    if (renderAccess(container, gate)) return;

    const loading = ui.state('', 'Loading admin evidence', 'Reading the allowlist, open signup audits, and GitHub profile identities.');
    container.append(loading);
    try {
      const pat = gate.getGithubPat ? gate.getGithubPat() : '';
      const [db, pending] = await Promise.all([
        actions.loadUsers(),
        actions.loadPendingSignups(pat),
      ]);
      if (seq !== renderSeq) return;

      for (const request of pending) {
        if (request.github_id && request.login) loginCache.set(Number(request.github_id), request.login);
      }
      const ids = Array.from(new Set([
        ...(db.users || []).map((user) => Number(user.github_id)).filter(Boolean),
        Number(db.admin_id) || 0,
      ].filter(Boolean)));
      const missingIds = ids.filter((id) => !loginCache.has(id));
      if (missingIds.length) {
        const resolved = await actions.resolveLogins(missingIds, pat);
        if (seq !== renderSeq) return;
        for (const [id, login] of Object.entries(resolved)) loginCache.set(Number(id), login);
      }

      const loginsById = {};
      for (const id of ids) if (loginCache.has(id)) loginsById[id] = loginCache.get(id);
      loading.remove();
      renderAdminDashboard(container, { db, pending, loginsById, pat }, seq);
    } catch (error) {
      if (seq !== renderSeq) return;
      loading.replaceWith(ui.state('is-danger', 'Admin evidence unavailable', error && error.message ? error.message : 'The source requests did not complete.'));
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', render);
  else render();

  document.addEventListener('click', function(event) {
    const tab = event.target.closest && event.target.closest('[data-tab="ci-admin"]');
    if (tab) window.setTimeout(render, 50);
  });
  document.addEventListener('auth:changed', render);
})();
