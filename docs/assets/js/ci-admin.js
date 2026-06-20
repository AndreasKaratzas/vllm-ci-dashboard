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
        email: String(parsed.email || '').trim(),
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
          email: audit && audit.email || '',
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
    ['Issue', 'Requester', 'GitHub id', 'Email', 'Requested', 'Action'].forEach((c) => {
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
      tr.append(h('td', { text: req.email || '—', style: { padding: '6px 8px', borderBottom: `1px solid ${C.bd}` } }));
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
    ['GitHub login', 'GitHub id', 'Email', 'Signed up', 'Action'].forEach((c) => {
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
      tr.append(h('td', { text: u.email || '—', style: { padding: '6px 8px', borderBottom: `1px solid ${C.bd}` } }));
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

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', render);
  } else {
    render();
  }

  document.addEventListener('click', (e) => {
    const btn = e.target.closest && e.target.closest('[data-tab="ci-admin"]');
    if (btn) setTimeout(render, 50);
  });
  document.addEventListener('auth:changed', render);
})();
