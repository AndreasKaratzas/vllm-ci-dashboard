/**
 * Entry-gate for the dashboard — PAT-based auth, no passwords.
 *
 * Flow
 * ----
 *   1. Sign in — user pastes a GitHub PAT. We verify via GET api.github.com/user
 *      (which has CORS), match the returned numeric ``id`` against the allowlist
 *      in ``data/users.json``, and create a session. The PAT stays in memory
 *      only; reload keeps the identity record but asks again only when the user
 *      wants token-backed actions.
 *   2. Access help — the dashboard explains that access is granted manually
 *      by the repo owner. We no longer route public access requests through
 *      GitHub issues because this repository is operated as an owner /
 *      collaborator control plane rather than an open intake queue.
 *   3. Continue as guest — writes a session flag and keeps protected tabs
 *      visible-but-locked so the tool surface stays discoverable.
 *
 * Why PAT-paste instead of OAuth Device Flow
 * ------------------------------------------
 * GitHub's OAuth endpoints (``/login/device/code``, ``/login/oauth/access_token``)
 * do not send CORS headers, so a pure static site cannot complete the device
 * flow from the browser. PAT-paste is the only static-site-compatible way to
 * prove "I am this GitHub user" without an auxiliary backend. ``users.json``
 * therefore stores only the allowlist (github_id + email + requested_at); no
 * password hashes, no salts, no iteration counts.
 *
 * Threat model
 * ------------
 * This is NOT strong access control. The protected tabs contain no sensitive
 * data — they are gated against accidental clicks, not determined attackers.
 * A user with DevTools can set sessionStorage manually and bypass everything.
 * That's fine for the stated threat model.
 */
(function() {
  'use strict';

  var SESSION_KEY = 'vllm_dashboard_auth';
  var DASHBOARD_REPO = 'AndreasKaratzas/vllm-ci-dashboard';
  var DASHBOARD_OWNER = (DASHBOARD_REPO.split('/')[0] || 'AndreasKaratzas');

  // Older builds wrote plaintext tokens under these keys. Evict on every
  // boot so a stale browser profile cannot leak them post-migration.
  var LEGACY_KEYS = [
    'vllm_dashboard_gh_pat',
    'vllm_dashboard_bk_token',
  ];
  try {
    LEGACY_KEYS.forEach(function(k) { sessionStorage.removeItem(k); });
  } catch (e) {}

  // In-memory PAT. Never persisted. Used by gated tabs for api.github.com
  // calls and by the vault for wrap-key derivation. Dropped on signOut/reload.
  var _currentPat = '';

  function getTabMeta(id) {
    var reg = window.__dashboardTabs;
    if (reg && typeof reg.get === 'function') {
      var hit = reg.get(id);
      if (hit) return hit;
    }
    if (id === 'ci-admin') return { id: id, requiresAuth: true, adminOnly: true, gateLabel: 'Admin' };
    if (id === 'ci-testbuild' || id === 'ci-ready') {
      return { id: id, requiresAuth: true, gateLabel: 'Sign in' };
    }
    return { id: id };
  }

  function getProtectedTabs() {
    var reg = window.__dashboardTabs;
    if (reg && typeof reg.getProtectedTabs === 'function') {
      var tabs = reg.getProtectedTabs();
      if (tabs && tabs.length) return tabs;
    }
    return ['ci-testbuild', 'ci-ready', 'ci-admin'].map(getTabMeta);
  }

  function emitAuthChanged() {
    applyTabVisibility();
    renderEntryControl();
    document.dispatchEvent(new CustomEvent('auth:changed'));
  }

  function lockEphemeralAuth() {
    _currentPat = '';
    try { if (window.__tokenVault) window.__tokenVault.lock(); } catch (e) {}
  }

  function getSession() {
    try { return JSON.parse(sessionStorage.getItem(SESSION_KEY) || 'null'); }
    catch (e) { return null; }
  }
  function setSession(obj) {
    try { sessionStorage.setItem(SESSION_KEY, JSON.stringify(obj || null)); } catch (e) {}
  }
  function clearSession() {
    try { sessionStorage.removeItem(SESSION_KEY); } catch (e) {}
  }

  window.__authGate = {
    isAuthed: function() {
      var s = getSession();
      return !!(s && s.mode === 'user' && s.login);
    },
    isAdmin: function() {
      var s = getSession();
      return !!(s && s.mode === 'user' && s.login && s.admin === true);
    },
    isGuest: function() {
      var s = getSession();
      return !!(s && s.mode === 'guest');
    },
    getLogin: function() {
      var s = getSession();
      return (s && s.login) || '';
    },
    getGithubId: function() {
      var s = getSession();
      return (s && s.id) || 0;
    },
    // Exposes the in-memory PAT to gated-tab code. Returns '' if the PAT has
    // not been provided this tab-session (fresh reload before unlock).
    getGithubPat: function() {
      return _currentPat;
    },
    promptSignIn: function() {
      var s = getSession();
      if (s && s.mode === 'user' && s.login && !_currentPat) {
        buildUnlockOverlay(s.login);
        return;
      }
      buildOverlay();
    },
    signOut: function() {
      lockEphemeralAuth();
      clearSession();
      document.body.classList.remove('__auth-locked');
      var gate = document.getElementById('auth-gate');
      if (gate) gate.remove();
      emitAuthChanged();
    },
    gatedTabs: getProtectedTabs().map(function(tab) { return tab.id; }),
    adminOnlyTabs: getProtectedTabs().filter(function(tab) { return !!tab.adminOnly; }).map(function(tab) { return tab.id; }),
    canAccessTab: function(id) { return canAccessTab(id); },
    applyTabVisibility: function() { applyTabVisibility(); },
    getTabMeta: function(id) { return getTabMeta(id); },
  };

  // ── GitHub API: verify a PAT and return the caller's identity ──────
  async function verifyPat(pat) {
    var r = await fetch('https://api.github.com/user', {
      headers: {
        'Authorization': 'token ' + pat,
        'Accept': 'application/vnd.github+json',
      },
    });
    if (!r.ok) {
      if (r.status === 401) throw new Error('PAT rejected by GitHub — check the token or regenerate it.');
      throw new Error('GitHub /user returned ' + r.status + '.');
    }
    var me = await r.json();
    if (!me || typeof me.id !== 'number' || !me.login) {
      throw new Error('GitHub returned an unexpected /user payload.');
    }
    return me;
  }

  // Fetch users.json fresh off main so a brand-new signup (via the workflow)
  // is immediately visible. Contents API is always real-time; raw + relative
  // are fallbacks for when the API is rate-limited.
  async function loadUsers() {
    var empty = { admin_id: 0, users: [] };
    var bust = Math.floor(Date.now() / 1000);

    try {
      var r = await fetch(
        'https://api.github.com/repos/' + DASHBOARD_REPO + '/contents/data/users.json?ref=main&_=' + bust,
        { headers: { 'Accept': 'application/vnd.github+json' } }
      );
      if (r.ok) {
        var meta = await r.json();
        var text = atob((meta.content || '').replace(/\n/g, ''));
        return JSON.parse(text);
      }
    } catch (e) {}

    try {
      var r2 = await fetch(
        'https://raw.githubusercontent.com/' + DASHBOARD_REPO + '/main/data/users.json?_=' + bust
      );
      if (r2.ok) return await r2.json();
    } catch (e) {}

    try {
      var r3 = await fetch('data/users.json?_=' + bust);
      if (r3.ok) return await r3.json();
    } catch (e) {}

    return empty;
  }

  // ── Styles ─────────────────────────────────────────────────────────
  function injectStyles() {
    if (document.getElementById('auth-gate-styles')) return;
    var s = document.createElement('style');
    s.id = 'auth-gate-styles';
    s.textContent = `
      #auth-gate {
        position: fixed; inset: 0; z-index: 99999;
        background: rgba(8, 12, 18, 0.92); backdrop-filter: blur(4px);
        display: flex; align-items: center; justify-content: center;
        font-family: inherit; color: #e6edf3;
      }
      #auth-gate-card {
        background: #161b22; border: 1px solid #30363d; border-radius: 10px;
        padding: 28px 32px; width: min(92vw, 460px);
        box-shadow: 0 20px 60px rgba(0,0,0,0.5);
      }
      #auth-gate h2 { margin: 0 0 6px 0; font-size: 20px; }
      #auth-gate .sub { color: #8b949e; font-size: 13px; margin-bottom: 18px; line-height: 1.5; }
      #auth-gate label { display: block; font-size: 11px; color: #8b949e;
        text-transform: uppercase; letter-spacing: 0.04em; margin: 10px 0 4px; }
      #auth-gate input { width: 100%; box-sizing: border-box; padding: 8px 10px;
        background: #0d1117; border: 1px solid #30363d; border-radius: 5px;
        color: #e6edf3; font-size: 13px; font-family: inherit; }
      #auth-gate input:focus { outline: none; border-color: #1f6feb; }
      #auth-gate .btn-row { display: flex; gap: 8px; margin-top: 14px; flex-wrap: wrap; }
      #auth-gate button { padding: 8px 14px; border: none; border-radius: 5px;
        font-size: 13px; font-weight: 600; cursor: pointer; font-family: inherit; }
      #auth-gate button.primary { background: #238636; color: white; }
      #auth-gate button.primary:disabled { background: #1a1f26; color: #8b949e; cursor: wait; }
      #auth-gate button.link { background: transparent; color: #58a6ff; padding: 4px 0;
        border-radius: 0; font-weight: 500; text-decoration: underline; }
      #auth-gate button.guest { background: transparent; color: #8b949e;
        border: 1px solid #30363d; }
      #auth-gate .status { font-size: 12px; min-height: 16px; margin-top: 10px; line-height: 1.5; }
      #auth-gate .status.err { color: #f85149; }
      #auth-gate .status.ok { color: #3fb950; }
      #auth-gate .status.info { color: #8b949e; }
      #auth-gate .divider { margin: 16px 0; border-top: 1px solid #30363d; }
      #auth-gate .hint { color: #8b949e; font-size: 12px; margin-top: 6px; line-height: 1.5; }
      #auth-gate .hint code { background: #0d1117; padding: 1px 4px; border-radius: 3px; }
      #auth-gate .hint a { color: #58a6ff; }
      body.__auth-locked { overflow: hidden; }
    `;
    document.head.appendChild(s);
  }

  function canAccessTab(id) {
    var meta = getTabMeta(id);
    if (!meta || !(meta.requiresAuth || meta.adminOnly)) return true;
    var session = getSession();
    var isAuthed = !!(session && session.mode === 'user' && session.login);
    if (meta.adminOnly) {
      return !!(isAuthed && session.admin === true);
    }
    return isAuthed;
  }

  function applyTabVisibility() {
    var session = getSession();
    var isAuthed = !!(session && session.mode === 'user' && session.login);
    var isAdmin = !!(isAuthed && session.admin === true);

    // Body-level markers drive the guest/member/admin styling and give
    // renderers a simple way to reflect current auth state in the shell.
    var body = document.body;
    if (body) {
      body.classList.toggle('__auth-guest', !isAuthed);
      body.classList.toggle('__auth-nonadmin', !isAdmin);
      body.classList.toggle('__auth-admin', isAdmin);
    }

    getProtectedTabs().forEach(function(meta) {
      var locked = !canAccessTab(meta.id);
      var label = locked
        ? (meta.adminOnly && isAuthed ? 'Admin only' : (meta.gateLabel || 'Sign in'))
        : '';
      var btns = document.querySelectorAll('[data-tab="' + meta.id + '"]');
      for (var i = 0; i < btns.length; i++) {
        btns[i].classList.toggle('__gate-locked', locked);
        if (locked) {
          btns[i].setAttribute('aria-disabled', 'true');
          btns[i].setAttribute('title', label);
        } else {
          btns[i].removeAttribute('aria-disabled');
          btns[i].removeAttribute('title');
        }
        var chip = btns[i].querySelector('.nav-lock-chip');
        if (chip) chip.textContent = label;
      }
      var panel = document.getElementById('tab-' + meta.id);
      if (panel) {
        panel.setAttribute('data-locked', locked ? 'true' : 'false');
      }
    });
  }

  // ── DOM helpers ────────────────────────────────────────────────────
  function _el(tag, attrs) {
    var n = document.createElement(tag);
    if (attrs) for (var k in attrs) {
      if (k === 'text') n.textContent = attrs[k];
      else if (k === 'html') n.innerHTML = attrs[k];
      else if (k === 'on') for (var ev in attrs.on) n.addEventListener(ev, attrs.on[ev]);
      else n.setAttribute(k, attrs[k]);
    }
    return n;
  }

  function renderEntryControl() {
    var host = document.querySelector('#sidebar .sidebar-header');
    if (!host) return;

    var existing = document.getElementById('auth-entry-control');
    if (!existing) {
      existing = document.createElement('div');
      existing.id = 'auth-entry-control';
      existing.style.marginTop = '10px';
      existing.style.display = 'flex';
      existing.style.flexDirection = 'column';
      existing.style.gap = '6px';
      host.appendChild(existing);
    }

    existing.innerHTML = '';
    var session = getSession();
    var isAuthed = !!(session && session.mode === 'user' && session.login);
    var role = !!(session && session.admin) ? 'admin' : 'member';

    function makeButton(label) {
      var button = document.createElement('button');
      button.type = 'button';
      button.textContent = label;
      button.style.padding = '7px 10px';
      button.style.borderRadius = '6px';
      button.style.border = '1px solid #30363d';
      button.style.background = '#0d1117';
      button.style.color = '#e6edf3';
      button.style.cursor = 'pointer';
      button.style.fontSize = '12px';
      button.style.fontWeight = '600';
      button.style.textAlign = 'left';
      return button;
    }

    if (isAuthed) {
      var meta = document.createElement('div');
      meta.textContent = '@' + session.login + ' (' + role + ')';
      meta.style.fontSize = '12px';
      meta.style.color = '#8b949e';
      existing.appendChild(meta);

      var note = document.createElement('div');
      note.style.fontSize = '11px';
      note.style.lineHeight = '1.35';
      note.style.color = '#8b949e';
      note.textContent = _currentPat
        ? 'Session unlocked for this tab. Build dispatches and encrypted-token actions can use the in-memory PAT.'
        : 'Session restored. Re-enter your PAT only when you want token-backed actions such as launching builds or writing admin changes.';
      existing.appendChild(note);

      var row = document.createElement('div');
      row.style.display = 'flex';
      row.style.gap = '6px';
      row.style.flexWrap = 'wrap';

      if (!_currentPat) {
        var unlockBtn = makeButton('Re-enter PAT');
        unlockBtn.addEventListener('click', function() {
          window.__authGate.promptSignIn();
        });
        row.appendChild(unlockBtn);
      }

      var signOutBtn = makeButton('Sign Out');
      signOutBtn.addEventListener('click', function() {
        window.__authGate.signOut();
      });
      row.appendChild(signOutBtn);
      existing.appendChild(row);
    } else {
      var hint = document.createElement('div');
      hint.textContent = 'Public views stay open. Sign in when you want to launch Buildkite runs, manage ready tickets, or edit dashboard access.';
      hint.style.fontSize = '11px';
      hint.style.lineHeight = '1.35';
      hint.style.color = '#8b949e';
      existing.appendChild(hint);

      var button = makeButton('Sign In');
      button.addEventListener('click', function() {
        window.__authGate.promptSignIn();
      });
      existing.appendChild(button);
    }
  }

  // ── Overlay UI ─────────────────────────────────────────────────────
  function buildOverlay() {
    injectStyles();
    var old = document.getElementById('auth-gate');
    if (old) old.remove();
    document.body.classList.add('__auth-locked');

    var gate = document.createElement('div');
    gate.id = 'auth-gate';
    var card = document.createElement('div');
    card.id = 'auth-gate-card';
    gate.appendChild(card);
    document.body.appendChild(gate);

    function renderSignin() {
      card.innerHTML = '';
      card.appendChild(_el('h2', { text: 'vLLM CI Dashboard' }));
      card.appendChild(_el('p', {
        class: 'sub',
        text: 'Sign in with a GitHub personal access token, or continue as guest.'
      }));

      card.appendChild(_el('label', { text: 'GitHub PAT' }));
      var patI = _el('input', {
        type: 'password',
        autocomplete: 'off',
        placeholder: 'github_pat_… or ghp_…',
      });
      card.appendChild(patI);
      var hint = _el('div', { class: 'hint' });
      hint.innerHTML =
        'A fine-grained token with no extra scopes is enough — we only call ' +
        '<code>GET /user</code>. ' +
        '<a href="https://github.com/settings/personal-access-tokens/new" target="_blank" rel="noopener">Generate one</a>.';
      card.appendChild(hint);

      var status = _el('div', { class: 'status' });

      var row = _el('div', { class: 'btn-row' });
      var signIn = _el('button', { class: 'primary', text: 'Sign in' });
      var guest = _el('button', { class: 'guest', text: 'Continue as guest' });
      row.appendChild(signIn);
      row.appendChild(guest);
      card.appendChild(row);
      card.appendChild(status);

      card.appendChild(_el('div', { class: 'divider' }));
      var signupRow = _el('div');
      signupRow.appendChild(_el('span', { class: 'sub', text: "Don't have an account? " }));
      var signupBtn = _el('button', { class: 'link', text: 'How to get access' });
      signupRow.appendChild(signupBtn);
      card.appendChild(signupRow);

      signIn.addEventListener('click', async function() {
        var pat = (patI.value || '').trim();
        if (!pat) { status.className = 'status err'; status.textContent = 'Paste a PAT to sign in.'; return; }
        signIn.disabled = true;
        status.className = 'status info'; status.textContent = 'Verifying with GitHub…';
        try {
          var me = await verifyPat(pat);
          var db = await loadUsers();
          var user = (db.users || []).find(function(u) { return u.github_id === me.id; });
          if (!user) {
            throw new Error('@' + me.login + ' is not on the allowlist. Contact @' + DASHBOARD_OWNER + ' to be added.');
          }
          _currentPat = pat;
          setSession({
            mode: 'user',
            login: me.login,
            id: me.id,
            email: user.email,
            admin: (db.admin_id && db.admin_id === me.id) ? true : false,
            signed_in_at: new Date().toISOString(),
          });
          if (window.__tokenVault) {
            try { await window.__tokenVault.unlock(pat, me.id); }
            catch (ve) { console.warn('vault unlock failed:', ve && ve.message); }
          }
          teardown();
        } catch (e) {
          status.className = 'status err'; status.textContent = e.message || 'Sign-in failed.';
        } finally {
          signIn.disabled = false;
        }
      });

      guest.addEventListener('click', function() {
        setSession({ mode: 'guest', signed_in_at: new Date().toISOString() });
        teardown();
      });

      signupBtn.addEventListener('click', renderSignup);
      patI.focus();
    }

    function renderSignup() {
      card.innerHTML = '';
      card.appendChild(_el('h2', { text: 'Access is managed manually' }));
      card.appendChild(_el('p', {
        class: 'sub',
        text:
          'This repo does not accept public signup issues or PRs. Dashboard access ' +
          'is granted directly by @' + DASHBOARD_OWNER + ' after manual review.'
      }));
      card.appendChild(_el('p', {
        class: 'sub',
        text:
          'If you need access, contact the admin directly and ask to be added ' +
          'to the dashboard allowlist. Once you have been added, come back and sign in with your GitHub PAT.'
      }));

      var row = _el('div', { class: 'btn-row' });
      var back = _el('button', { class: 'guest', text: 'Back to sign in' });
      row.appendChild(back);
      card.appendChild(row);

      back.addEventListener('click', renderSignin);
    }

    renderSignin();
  }

  // Shown on reload when the session record is present but the in-memory
  // PAT is gone. Re-prompts for the PAT to re-derive the vault key.
  function buildUnlockOverlay(login) {
    injectStyles();
    var old = document.getElementById('auth-gate');
    if (old) old.remove();
    document.body.classList.add('__auth-locked');

    var gate = document.createElement('div');
    gate.id = 'auth-gate';
    var card = document.createElement('div');
    card.id = 'auth-gate-card';
    gate.appendChild(card);
    document.body.appendChild(gate);

    card.appendChild(_el('h2', { text: 'Welcome back, @' + login }));
    card.appendChild(_el('p', {
      class: 'sub',
      text:
        'Re-enter your GitHub PAT to unlock saved Buildkite / HF tokens for ' +
        'this tab. The PAT is never stored on disk.'
    }));

    card.appendChild(_el('label', { text: 'GitHub PAT' }));
    var patI = _el('input', {
      type: 'password',
      autocomplete: 'off',
      placeholder: 'github_pat_… or ghp_…',
    });
    card.appendChild(patI);

    var status = _el('div', { class: 'status' });
    var row = _el('div', { class: 'btn-row' });
    var unlockBtn = _el('button', { class: 'primary', text: 'Unlock' });
    var skipBtn = _el('button', { class: 'guest', text: 'Skip — use dashboard without tokens' });
    row.appendChild(unlockBtn);
    row.appendChild(skipBtn);
    card.appendChild(row);
    card.appendChild(status);

    card.appendChild(_el('div', { class: 'divider' }));
    var signOutRow = _el('div');
    var signOutBtn = _el('button', { class: 'link', text: 'Sign out' });
    signOutRow.appendChild(signOutBtn);
    card.appendChild(signOutRow);

    signOutBtn.addEventListener('click', function() { window.__authGate.signOut(); });
    skipBtn.addEventListener('click', function() {
      try { if (window.__tokenVault) window.__tokenVault.lock(); } catch (e) {}
      teardown();
    });

    unlockBtn.addEventListener('click', async function() {
      var pat = (patI.value || '').trim();
      if (!pat) { status.className = 'status err'; status.textContent = 'Paste a PAT.'; return; }
      unlockBtn.disabled = true;
      status.className = 'status info'; status.textContent = 'Verifying with GitHub…';
      try {
        var me = await verifyPat(pat);
        var session = getSession() || {};
        if (session.id && me.id !== session.id) {
          throw new Error('PAT belongs to @' + me.login + ', not the signed-in account.');
        }
        _currentPat = pat;
        if (window.__tokenVault) {
          await window.__tokenVault.unlock(pat, me.id);
        }
        teardown();
      } catch (e) {
        status.className = 'status err'; status.textContent = e.message || 'Unlock failed.';
      } finally {
        unlockBtn.disabled = false;
      }
    });

    patI.focus();
  }

  function teardown() {
    document.body.classList.remove('__auth-locked');
    var g = document.getElementById('auth-gate');
    if (g) g.remove();
    emitAuthChanged();
  }

  function boot() {
    injectStyles();
    var s = getSession();
    if (s && s.mode === 'user' && s.login && !_currentPat) {
      lockEphemeralAuth();
    }
    emitAuthChanged();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
