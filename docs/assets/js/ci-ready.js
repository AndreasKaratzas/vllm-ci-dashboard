/**
 * Ready Tickets — dashboard view for the AMD nightly failure summary.
 *
 * Reads ``data/vllm/ci/ready_tickets.json`` written by
 * ``scripts/vllm/sync_ready_tickets.py`` and renders the one upstream master
 * issue plus the per-group failure table that feeds that issue body.
 */

window.__OPS_CONTROL_V2_READY__ = true;
(function() {
  const _s = getComputedStyle(document.documentElement);
  let renderSeq = 0;
  const C = {
    g:_s.getPropertyValue('--accent-green').trim()||'#238636',
    y:_s.getPropertyValue('--accent-orange').trim()||'#d29922',
    r:_s.getPropertyValue('--badge-closed').trim()||'#da3633',
    b:_s.getPropertyValue('--accent-blue').trim()||'#1f6feb',
    p:_s.getPropertyValue('--accent-purple').trim()||'#8957e5',
    m:_s.getPropertyValue('--text-muted').trim()||'#8b949e',
    t:_s.getPropertyValue('--text').trim()||'#e6edf3',
    bg:_s.getPropertyValue('--card-bg').trim()||'#161b22',
    bd:_s.getPropertyValue('--border').trim()||'#30363d',
  };
  const h = el;

  const DASHBOARD_REPO = 'AndreasKaratzas/vllm-ci-dashboard';
  let readyTableSort = { key: null, dir: 'asc' };
  const CI_FAILURE_PREFIX_RE = /^\[CI Failure\]:\s*/i;
  const HW_PREFIX_RE = /^mi\d+_\d+:\s*/i;
  // Session PAT is held in memory by auth.js after signin; roster ciphertext
  // unlocks via the token vault (wrap key derived from that same PAT).
  function _vault() { return window.__tokenVault; }
  function _authPat() {
    const g = window.__authGate;
    return g && g.getGithubPat ? g.getGithubPat() : '';
  }

  async function ghFetch(pat, path, opts) {
    const url = path.startsWith('http') ? path : ('https://api.github.com' + path);
    const headers = Object.assign({
      'Accept': 'application/vnd.github+json',
      'Authorization': 'token ' + pat,
      'X-GitHub-Api-Version': '2022-11-28',
    }, (opts && opts.headers) || {});
    const resp = await fetch(url, Object.assign({}, opts || {}, { headers }));
    return resp;
  }
  async function ghJson(pat, path, opts) {
    const r = await ghFetch(pat, path, opts);
    const text = await r.text();
    let data = null; try { data = text ? JSON.parse(text) : null; } catch (e) {}
    return { ok: r.ok, status: r.status, data, text };
  }

  async function loadPlan() {
    try {
      const r = await fetch('data/vllm/ci/ready_tickets.json?_=' + Math.floor(Date.now()/1000));
      if (!r.ok) return null;
      return await r.json();
    } catch (e) { return null; }
  }

  async function loadProjectItems() {
    try {
      const r = await fetch('data/vllm/ci/project_items.json?_=' + Math.floor(Date.now()/1000));
      if (!r.ok) return null;
      return await r.json();
    } catch (e) { return null; }
  }

  // The engineer roster used to ride along in ``ready_tickets.json`` as
  // plaintext ``{github_login, display_name}``. That file is served
  // publicly on gh-pages, so we now ship the roster as AES-GCM ciphertext
  // at ``engineers.enc.json`` — generated locally by
  // ``scripts/vllm/encrypt_roster.py`` with the admin's vault key. Only a
  // signed-in user whose vault is unlocked can decrypt it; guests and
  // non-admin viewers see an empty dropdown (and the dropdown itself is
  // disabled for them anyway).
  async function loadEngineers() {
    const v = _vault();
    if (!v || !v.isUnlocked() || !v.decryptExternal) return [];
    let record;
    try {
      const r = await fetch('data/vllm/ci/engineers.enc.json?_=' + Math.floor(Date.now()/1000));
      if (!r.ok) return [];
      record = await r.json();
    } catch (e) { return []; }
    let pt;
    try { pt = await v.decryptExternal(record); } catch (e) { return []; }
    if (!pt) return [];
    try {
      const list = JSON.parse(pt);
      return Array.isArray(list) ? list : [];
    } catch (e) { return []; }
  }


  function renderBanner(container, plan) {
    const paused = !!(plan && (plan.feature_paused || plan.mode === 'paused'));
    const dryRun = plan && plan.mode !== 'live' && !paused;
    const msg = paused
      ? (plan.pause_reason || 'Ready Tickets automation is paused. This dashboard will not create or update upstream CI issues.')
      : plan && plan.mode === 'live' && plan.issue_mode === 'single_master'
        ? 'Live mode updates one managed comment on the upstream master issue instead of opening per-group tickets.'
        : dryRun
          ? 'Dry-run mode — no issues will be created or modified.'
          : `Live mode — the syncer is managing tickets on ${plan.project}.`;
    const bg = paused ? '#2b161b' : dryRun ? '#1f2933' : '#0f2a1a';
    const bd = paused ? C.r : dryRun ? C.y : C.g;
    const card = h('div', { style: { background: bg, border: `1px solid ${bd}`, borderRadius: '6px', padding: '10px 14px', marginBottom: '14px', fontSize: '13px' } });
    card.append(h('strong', { text: paused ? 'Paused' : dryRun ? 'Preview (dry-run)' : 'Active (live sync)', style: { color: paused ? C.r : dryRun ? C.y : C.g } }));
    card.append(h('span', { text: ' — ' + msg, style: { color: C.m } }));
    if (plan && plan.generated_at) {
      card.append(h('div', { text: `Last sync attempt: ${plan.generated_at}`, style: { fontSize: '11px', color: C.m, marginTop: '4px' } }));
    }
    container.append(card);
  }

  function renderMasterIssueCard(container, plan) {
    const master = plan && plan.master_issue;
    if (!master || !master.url) return;
    const card = h('div', { style: { background: C.bg, border: `1px solid ${C.bd}`, borderRadius: '8px', padding: '14px 18px', marginBottom: '12px' } });
    card.append(h('div', { text: 'Master Issue', style: { fontSize: '10px', color: C.m, textTransform: 'uppercase', letterSpacing: '0.05em' } }));
    const row = h('div', { style: { display: 'flex', gap: '10px', alignItems: 'baseline', flexWrap: 'wrap', marginTop: '6px' } });
    row.append(h('a', {
      href: master.url,
      target: '_blank',
      rel: 'noopener',
      text: `#${master.number || '—'}`,
      style: { color: C.b, fontWeight: '700', fontSize: '18px' },
    }));
    row.append(h('span', { text: master.title || 'AMD CI Issues Master', style: { color: C.t, fontWeight: '600' } }));
    card.append(row);
    card.append(h('p', {
      text: 'Every currently failing AMD nightly group is tracked in this single upstream issue. The table below is the detailed breakdown that gets published there.',
      style: { color: C.m, marginTop: '8px', marginBottom: 0, fontSize: '13px' },
    }));
    const comment = plan && plan.master_issue_comment;
    if (comment && comment.url) {
      card.append(h('a', {
        href: comment.url,
        target: '_blank',
        rel: 'noopener',
        text: 'Open latest automation update',
        style: { display: 'inline-block', marginTop: '10px', color: C.b, fontSize: '13px' },
      }));
    }
    container.append(card);
  }

  function renderAdminStatus(container, state) {
    const card = h('div', { style: { background: C.bg, border: `1px solid ${C.bd}`, borderRadius: '6px', padding: '10px 14px', marginBottom: '14px', fontSize: '13px' } });
    card.append(h('strong', { text: 'Assignment control', style: { color: C.t } }));
    const msg = state.isAdmin
      ? `Signed in as admin @${state.login} — assignment dropdown enabled. Writes use your session PAT.`
      : state.login
        ? `Signed in as @${state.login}. Assignment requires the dashboard admin account; this tab is read-only for you.`
        : 'Sign in to enable assignment.';
    const color = state.isAdmin ? C.g : C.m;
    card.append(h('div', { text: msg, style: { color, marginTop: '4px' } }));
    container.append(card);
  }

  function fmtDate(d) { return d || '—'; }
  function daysSince(d) {
    if (!d) return '—';
    const t = new Date(d + 'T12:00:00Z').getTime();
    if (!t) return '—';
    const diff = Math.floor((Date.now() - t) / 86400000);
    return diff + 'd';
  }

  async function assignIssue(pat, repo, issueNumber, login) {
    const r = await ghJson(pat, `/repos/${repo}/issues/${issueNumber}/assignees`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ assignees: [login] }),
    });
    return r;
  }

  function latestBuildText(summary) {
    const refs = summary && Array.isArray(summary.build_refs_latest) ? summary.build_refs_latest : [];
    if (refs.length) {
      return refs.slice(0, 3).map((ref) => `${ref.pipeline || 'build'} #${ref.build_number}`).join(', ');
    }
    const builds = summary && Array.isArray(summary.builds_latest) ? summary.builds_latest : [];
    return builds.length ? builds.slice(0, 3).map((n) => `build #${n}`).join(', ') : '—';
  }

  function normalizeIssueTitle(title) {
    const raw = String(title || '').trim();
    if (!raw) return '';
    return raw
      .replace(CI_FAILURE_PREFIX_RE, '')
      .replace(HW_PREFIX_RE, '')
      .replace(/\s+%N\s*$/i, '')
      .replace(/\s+/g, ' ')
      .trim()
      .toLowerCase();
  }

  function buildProjectIssueIndexes(projectItems, masterIssueNumber) {
    const byTitle = {};
    const byNorm = {};
    const items = projectItems && projectItems.items_by_number ? projectItems.items_by_number : {};
    Object.keys(items).forEach(function(key) {
      const item = items[key] || {};
      const num = Number(item.issue_number || key);
      if (!(num > masterIssueNumber)) return;
      const title = String(item.title || '').trim();
      if (!title) return;
      byTitle[title] = item;
      const norm = normalizeIssueTitle(title);
      if (!norm) return;
      if (!byNorm[norm]) byNorm[norm] = [];
      byNorm[norm].push(item);
    });
    return { byTitle, byNorm, masterIssueNumber };
  }

  function pickProjectIssueForTicket(ticket, indexes) {
    if (!ticket) return null;
    const ticketNum = Number(ticket.issue_number);
    if (ticketNum && ticket.project_status && ticketNum > indexes.masterIssueNumber) {
      return {
        issue_number: ticketNum,
        url: ticket.issue_url,
        status: ticket.project_status,
      };
    }
    const exact = indexes.byTitle[ticket.title || ''];
    if (exact) return exact;
    const norm = normalizeIssueTitle(ticket.title || '');
    const candidates = indexes.byNorm[norm] || [];
    return candidates.length ? candidates[0] : null;
  }

  function _readySortDate(d) {
    if (!d) return null;
    const ts = new Date(d + 'T12:00:00Z').getTime();
    return Number.isFinite(ts) ? ts : null;
  }

  function _readySortBuild(summary) {
    const refs = summary && Array.isArray(summary.build_refs_latest) ? summary.build_refs_latest : [];
    for (const ref of refs) {
      const n = Number(ref && ref.build_number);
      if (Number.isFinite(n)) return n;
    }
    const builds = summary && Array.isArray(summary.builds_latest) ? summary.builds_latest : [];
    for (const b of builds) {
      const n = Number(b);
      if (Number.isFinite(n)) return n;
    }
    return null;
  }

  function readyTicketSortValue(ticket, key) {
    const s = ticket && ticket.summary ? ticket.summary : {};
    switch (key) {
      case 'group': return String(s.group || '').toLowerCase();
      case 'streak_start': return _readySortDate(s.current_streak_started);
      case 'first_fail': return _readySortDate(s.first_failure_in_window);
      case 'last_success': return _readySortDate(s.last_successful);
      case 'break_freq': {
        const n = Number(s.break_frequency);
        return Number.isFinite(n) ? n : null;
      }
      case 'latest_builds': return _readySortBuild(s);
      case 'project_issue': {
        const n = Number(ticket && ticket.issue_number);
        return Number.isFinite(n) && ticket && ticket.project_status !== 'Tracked in master issue' ? n : null;
      }
      case 'issue': {
        const n = Number(ticket && ticket.issue_number);
        return Number.isFinite(n) ? n : null;
      }
      case 'assignee': return ticket && ticket.assignee ? String(ticket.assignee).toLowerCase() : null;
      default: return null;
    }
  }

  function sortReadyTickets(tickets) {
    const items = Array.isArray(tickets) ? tickets.slice() : [];
    if (!readyTableSort.key) return items;
    const dir = readyTableSort.dir === 'desc' ? -1 : 1;
    return items
      .map((ticket, index) => ({ ticket, index }))
      .sort((a, b) => {
        const av = readyTicketSortValue(a.ticket, readyTableSort.key);
        const bv = readyTicketSortValue(b.ticket, readyTableSort.key);
        const aEmpty = av == null || av === '';
        const bEmpty = bv == null || bv === '';
        if (aEmpty && bEmpty) return a.index - b.index;
        if (aEmpty) return 1;
        if (bEmpty) return -1;
        let cmp = 0;
        if (typeof av === 'number' && typeof bv === 'number') cmp = av - bv;
        else cmp = String(av).localeCompare(String(bv));
        if (cmp === 0) return a.index - b.index;
        return cmp * dir;
      })
      .map((entry) => entry.ticket);
  }

  function renderMetricsTable(container, plan, state, projectItems) {
    const card = h('div', { style: { background: C.bg, border: `1px solid ${C.bd}`, borderRadius: '8px', padding: '14px 18px', marginBottom: '12px' } });
    card.append(h('h3', { text: `Failing test groups (${(plan.tickets || []).length})`, style: { marginTop: 0, fontSize: '15px' } }));
    const singleMaster = plan && plan.issue_mode === 'single_master';
    const masterIssueNumber = Number(plan && plan.master_issue && plan.master_issue.number) || 40554;
    const projectIssueIndexes = buildProjectIssueIndexes(projectItems, masterIssueNumber);

    if (!plan.tickets || !plan.tickets.length) {
      card.append(h('p', { text: 'No AMD nightly test groups currently failing. Nothing to triage.', style: { color: C.m, fontSize: '13px' } }));
      container.append(card);
      return;
    }

    const columns = singleMaster
      ? [
          { key: 'group', label: 'Group', defaultDir: 'asc' },
          { key: 'streak_start', label: 'Streak start', defaultDir: 'desc' },
          { key: 'first_fail', label: 'First fail', defaultDir: 'desc' },
          { key: 'last_success', label: 'Last success', defaultDir: 'desc' },
          { key: 'break_freq', label: 'Break freq', defaultDir: 'desc' },
          { key: 'latest_builds', label: 'Latest build(s)', defaultDir: 'desc' },
          { key: 'project_issue', label: 'Project issue', defaultDir: 'desc' },
        ]
      : [
          { key: 'group', label: 'Group', defaultDir: 'asc' },
          { key: 'streak_start', label: 'Streak start', defaultDir: 'desc' },
          { key: 'first_fail', label: 'First fail', defaultDir: 'desc' },
          { key: 'last_success', label: 'Last success', defaultDir: 'desc' },
          { key: 'break_freq', label: 'Break freq', defaultDir: 'desc' },
          { key: 'issue', label: 'Issue', defaultDir: 'desc' },
          { key: 'assignee', label: 'Assignee', defaultDir: 'asc' },
        ];

    const tableMount = h('div');
    card.append(tableMount);

    function renderTable() {
      tableMount.innerHTML = '';
      const table = h('table', { style: { width: '100%', borderCollapse: 'collapse', fontSize: '12px' } });
      const thead = h('thead');
      const hr = h('tr');
      for (const col of columns) {
        const th = h('th', { style: { textAlign: 'left', padding: '6px 8px', borderBottom: `1px solid ${C.bd}`, color: C.m, fontWeight: '600', textTransform: 'uppercase', fontSize: '10px', letterSpacing: '0.04em' } });
        const active = readyTableSort.key === col.key;
        const marker = active ? (readyTableSort.dir === 'asc' ? ' ▲' : ' ▼') : '';
        const btn = h('button', {
          text: col.label + marker,
          title: `Sort by ${col.label.toLowerCase()}`,
          style: {
            background: 'none',
            border: 'none',
            padding: 0,
            color: active ? C.t : C.m,
            font: 'inherit',
            textTransform: 'inherit',
            letterSpacing: 'inherit',
            cursor: 'pointer',
          },
        });
        btn.addEventListener('click', () => {
          if (readyTableSort.key === col.key) readyTableSort.dir = readyTableSort.dir === 'asc' ? 'desc' : 'asc';
          else {
            readyTableSort.key = col.key;
            readyTableSort.dir = col.defaultDir || 'asc';
          }
          renderTable();
        });
        th.append(btn);
        hr.append(th);
      }
      thead.append(hr);
      table.append(thead);

      const tbody = h('tbody');
      for (const t of sortReadyTickets(plan.tickets)) {
        const tr = h('tr');
        const s = t.summary || {};
        const cell = (text, extra) => h('td', Object.assign({ text: String(text == null ? '—' : text), style: Object.assign({ padding: '6px 8px', borderBottom: `1px solid ${C.bd}`, verticalAlign: 'top' }, (extra && extra.style) || {}) }, extra || {}));

        tr.append(cell(s.group || '—', { style: { fontFamily: 'monospace', fontSize: '11px' } }));
        tr.append(cell(fmtDate(s.current_streak_started)));
        tr.append(cell(fmtDate(s.first_failure_in_window)));
        tr.append(cell(`${fmtDate(s.last_successful)} (${daysSince(s.last_successful)})`));
        tr.append(cell(s.break_frequency == null ? '—' : s.break_frequency, { style: { textAlign: 'right' } }));
        if (singleMaster) {
          tr.append(cell(latestBuildText(s)));
          const issueCell = h('td', { style: { padding: '6px 8px', borderBottom: `1px solid ${C.bd}`, whiteSpace: 'nowrap' } });
          const linked = pickProjectIssueForTicket(t, projectIssueIndexes);
          if (linked && linked.url && linked.issue_number) {
            issueCell.append(h('a', {
              href: linked.url,
              target: '_blank',
              rel: 'noopener',
              text: `#${linked.issue_number}`,
              style: { color: C.b, fontWeight: '600' },
            }));
            if (linked.status) {
              issueCell.append(h('span', {
                text: ' · ' + linked.status,
                style: { color: C.m, fontSize: '11px' },
              }));
            }
          } else {
            issueCell.append(h('span', { text: '—', style: { color: C.m } }));
          }
          tr.append(issueCell);
        } else {
          const issueCell = h('td', { style: { padding: '6px 8px', borderBottom: `1px solid ${C.bd}`, whiteSpace: 'nowrap' } });
          renderIssueCell(issueCell, t, plan, state);
          tr.append(issueCell);

          const assignCell = h('td', { style: { padding: '6px 8px', borderBottom: `1px solid ${C.bd}` } });
          renderAssignControl(assignCell, t, plan, state);
          tr.append(assignCell);
        }

        tbody.append(tr);
      }
      table.append(tbody);
      tableMount.append(table);
    }

    renderTable();
    container.append(card);
  }

  // ---------------------------------------------------------------------
  // Issue cell: when the syncer has already filed a ticket (live mode),
  // show the ``#NNN`` link. When the ticket is still pending (dry-run, or
  // live-mode before the first successful POST), show two compact actions:
  //
  //   * ``search``  — opens a GitHub Issues search filtered by the canonical
  //                   title on ``plan.issue_repo``. Lets an admin spot a
  //                   pre-existing issue before filing a duplicate.
  //   * ``create ↗`` — opens GitHub's new-issue form pre-filled with the
  //                   exact title / body / label the syncer *would* POST.
  //                   The admin reviews the compose page and clicks
  //                   "Submit new issue" to file it by hand.
  //
  // Using pre-filled URLs instead of a direct POST is deliberate: the
  // admin sees the whole body before it lands on ``vllm-project/vllm`` and
  // can edit or abandon. No extra auth is needed — GitHub's own compose
  // page gates creation.
  // ---------------------------------------------------------------------
  function _issueSearchUrl(repo, title) {
    const q = `is:issue in:title "${title}"`;
    return `https://github.com/${repo}/issues?q=` + encodeURIComponent(q);
  }
  function _issueCreateUrl(repo, title, body, labels) {
    const params = new URLSearchParams();
    params.set('title', title);
    if (body) params.set('body', body);
    if (labels && labels.length) params.set('labels', labels.join(','));
    // GitHub caps URL length around 8k; a typical body is <1.5k so this is
    // fine, but fall back to title-only if we somehow exceed it.
    const url = `https://github.com/${repo}/issues/new?` + params.toString();
    if (url.length > 7500) {
      return `https://github.com/${repo}/issues/new?title=` + encodeURIComponent(title);
    }
    return url;
  }
  function renderIssueCell(cell, ticket, plan, state) {
    if (ticket.issue_number) {
      cell.append(h('a', { href: ticket.issue_url, target: '_blank', rel: 'noopener', text: `#${ticket.issue_number}`, style: { color: C.b } }));
      return;
    }
    const repo = plan.issue_repo || 'vllm-project/vllm';
    const title = ticket.title || '';
    const body = ticket.body || '';
    const labels = ticket.labels || ['ci-failure'];
    cell.append(h('span', { text: 'pending', style: { color: C.y, fontSize: '11px' } }));
    cell.append(h('span', { text: ' \u00b7 ', style: { color: C.m, fontSize: '11px' } }));
    cell.append(h('a', {
      href: _issueSearchUrl(repo, title), target: '_blank', rel: 'noopener',
      text: 'search', title: `Check ${repo} for an existing issue with this title`,
      style: { color: C.m, fontSize: '11px' },
    }));
    cell.append(h('span', { text: ' \u00b7 ', style: { color: C.m, fontSize: '11px' } }));
    cell.append(h('a', {
      href: _issueCreateUrl(repo, title, body, labels), target: '_blank', rel: 'noopener',
      text: 'create \u2197',
      title: `Open GitHub's new-issue form on ${repo} with this title + body pre-filled`,
      style: { color: C.b, fontSize: '11px', fontWeight: '600' },
    }));
  }

  function renderAssignControl(cell, ticket, plan, state) {
    const current = ticket.assignee || '';
    const select = h('select', { style: { padding: '4px 6px', background: '#0d1117', color: C.t, border: `1px solid ${C.bd}`, borderRadius: '4px', fontSize: '11px', maxWidth: '180px' } });
    // The shared ``el()`` helper sets every non-function prop via
    // ``setAttribute``, so plain top-level keys are correct — an earlier
    // ``attr: { value: ... }`` wrapper here was a no-op (stored an attribute
    // literally named "attr"), which would have made ``select.value`` fall
    // back to the visible text like "Jane Doe (@jane)" instead of the login.
    select.append(h('option', { value: '', text: '\u2014 unassigned \u2014' }));
    for (const e of (plan.engineers || [])) {
      const opt = h('option', { value: e.github_login, text: `${e.display_name} (@${e.github_login})` });
      if (current && current === e.github_login) opt.selected = true;
      select.append(opt);
    }
    select.disabled = !state.isAdmin || !ticket.issue_number;
    if (!state.isAdmin) select.title = 'Sign in as the dashboard admin to assign';
    else if (!ticket.issue_number) select.title = 'Issue not yet created (dry-run)';

    select.addEventListener('change', async () => {
      const login = select.value;
      if (!login) return;
      const pat = _authPat();
      if (!pat) { cell.append(h('span', { text: ' ✗ no PAT', style: { color: C.r, marginLeft: '6px', fontSize: '11px' } })); return; }
      select.disabled = true;
      const r = await assignIssue(pat, plan.issue_repo, ticket.issue_number, login);
      if (r.ok) {
        ticket.assignee = login;
        cell.append(h('span', { text: ' ✓', style: { color: C.g, marginLeft: '6px', fontSize: '11px' } }));
      } else {
        cell.append(h('span', { text: ` ✗ ${r.status}`, style: { color: C.r, marginLeft: '6px', fontSize: '11px' } }));
      }
      select.disabled = false;
    });
    cell.append(select);
  }

  function renderSummaryCards(container, plan) {
    const wrap = h('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: '8px', marginBottom: '14px' } });
    const items = [
      { label: 'Failing groups', value: plan.failing_groups_total || 0, color: C.r },
      { label: 'Tracked (window)', value: (plan.groups_all || []).length, color: C.m },
      { label: 'Window', value: `${plan.window_days || 60}d`, color: C.m },
      { label: 'Mode', value: plan.mode || '—', color: plan.mode === 'live' ? C.g : plan.mode === 'paused' ? C.r : C.y },
    ];
    for (const it of items) {
      const card = h('div', { style: { background: C.bg, border: `1px solid ${C.bd}`, borderRadius: '6px', padding: '10px 12px' } });
      card.append(h('div', { text: it.label, style: { fontSize: '10px', color: C.m, textTransform: 'uppercase', letterSpacing: '0.05em' } }));
      card.append(h('div', { text: String(it.value), style: { fontSize: '20px', color: it.color, marginTop: '4px', fontWeight: '600' } }));
      wrap.append(card);
    }
    container.append(wrap);
  }

  async function render() {
    if (window.__OPS_CONTROL_V2_READY__) return;
    const seq = ++renderSeq;
    const container = document.getElementById('ci-ready-view');
    if (!container) return;
    // Auth gate — Ready Tickets exposes the engineer roster (via the
    // token-vault decrypt) and lets admins assign issues. The nav button
    // is hidden from guests, but any forced panel activation still lands
    // here, so bail before we run the loadPlan/loadEngineers pipeline.
    const gate = window.__authGate;
    const allowed = !!(gate && typeof gate.canAccessTab === 'function'
      ? gate.canAccessTab('ci-ready')
      : (gate && gate.isAuthed && gate.isAuthed()));
    if (!allowed) {
      container.innerHTML = '';
      container.append(h('h2', { text: 'Ready Tickets', style: { marginBottom: '6px' } }));
      container.append(h('p', {
        text: 'Sign in to view the ready-tickets triage. This tab is not available to guests.',
        style: { color: C.m, marginTop: 0 },
      }));
      const unlock = h('button', {
        text: 'Sign in',
        style: { marginTop: '12px', padding: '7px 12px', borderRadius: '6px', border: `1px solid ${C.bd}`, background: C.bg, color: C.t, cursor: 'pointer', fontWeight: '600' },
      });
      unlock.addEventListener('click', () => {
        const auth = window.__authGate;
        if (auth && auth.promptSignIn) auth.promptSignIn();
      });
      container.append(unlock);
      return;
    }
    container.innerHTML = '';
    container.append(h('h2', { text: 'Ready Tickets', style: { marginBottom: '6px' } }));
    container.append(h('p', { text: 'AMD nightly failure tracking view for the upstream summary issue.', style: { color: C.m, marginTop: 0, marginBottom: '14px' } }));

    const plan = await loadPlan();
    if (seq !== renderSeq) return;
    if (!plan) {
      container.append(h('p', { text: 'No ready_tickets.json found yet — the collector will produce one on its next run.', style: { color: C.m, fontStyle: 'italic' } }));
      return;
    }
    const projectItems = await loadProjectItems();
    if (seq !== renderSeq) return;
    // Decrypt the roster blob if the vault is unlocked; empty array for
    // guests and locked sessions. The dropdown is already disabled for
    // non-admins at renderAssignControl, so an empty list is harmless.
    plan.engineers = await loadEngineers();
    if (seq !== renderSeq) return;

    const state = {
      render,
      login: gate && gate.getLogin ? gate.getLogin() : '',
      isAdmin: !!(gate && gate.isAdmin && gate.isAdmin()),
    };
    renderBanner(container, plan);
    renderSummaryCards(container, plan);
    if (plan.feature_paused || plan.mode === 'paused') {
      container.append(h('p', {
        text: 'This feature is frozen. The dashboard is not creating, updating, or proposing upstream project #39 issues from this tab.',
        style: { color: C.m, marginTop: 0 },
      }));
      return;
    }
    if (plan.issue_mode === 'single_master') {
      renderMasterIssueCard(container, plan);
      renderMetricsTable(container, plan, state, projectItems);
      return;
    }
    renderAdminStatus(container, state);
    renderMetricsTable(container, plan, state, projectItems);
  }

  window.OpsReadyActions = {
    loadPlan,
    loadProjectItems,
    loadEngineers,
    assignIssue,
    buildProjectIssueIndexes,
    pickProjectIssueForTicket,
    issueSearchUrl: _issueSearchUrl,
    issueCreateUrl: _issueCreateUrl,
  };

  // Lifecycle registration intentionally belongs only to the v2 renderer.
})();

(function renderReadyControlV2() {
  'use strict';
  window.__OPS_CONTROL_V2_READY__ = true;

  const ui = window.OpsControlV2;
  const h = window.el;
  const actions = window.OpsReadyActions;
  let renderSeq = 0;
  let operationsPromise = null;
  const viewState = {
    query: '',
    status: 'failing',
    build: '',
    page: 0,
    pageSize: 25,
  };

  function input(props) {
    return h('input', Object.assign({ cls: 'ocv2-input' }, props || {}));
  }

  function select(options, value, label) {
    const node = h('select', { cls: 'ocv2-select', 'aria-label': label });
    for (const option of options) {
      const item = h('option', { value: option.value, text: option.label });
      item.selected = option.value === value;
      node.append(item);
    }
    return node;
  }

  function loadOperations() {
    if (!operationsPromise) {
      operationsPromise = fetch('data/vllm/ci/operations_v2.json?_=' + Math.floor(Date.now() / 300000))
        .then(function(response) { return response.ok ? response.json() : null; })
        .catch(function() { return null; });
    }
    return operationsPromise;
  }

  function exposeReadyNavigation() {
    const button = document.querySelector('[data-tab="ci-ready"]');
    if (!button) return;
    button.classList.remove('__gate-locked');
    button.setAttribute('title', 'View Ready Tickets evidence');
    button.setAttribute('aria-disabled', 'false');
  }

  function buildRefs(summary) {
    const refs = summary && Array.isArray(summary.build_refs_latest) ? summary.build_refs_latest : [];
    if (refs.length) return refs;
    return (summary && Array.isArray(summary.builds_latest) ? summary.builds_latest : []).map(function(number) {
      return { build_number: number, pipeline: 'amd-ci', url: '' };
    });
  }

  function buildLinks(summary) {
    const root = h('div', { cls: 'ocv2-source-list' });
    const refs = buildRefs(summary);
    if (!refs.length) return h('span', { cls: 'ocv2-unavailable', text: 'No retained build reference' });
    for (const ref of refs.slice(0, 4)) {
      const label = (ref.pipeline || 'build') + ' #' + (ref.build_number || '?');
      root.append(ui.external(label, ref.url || ref.build_url, 'ocv2-mono', 'Open exact Buildkite build'));
    }
    return root;
  }

  function buildNumberText(summary) {
    return buildRefs(summary).map(function(ref) { return String(ref.build_number || ''); }).join(' ');
  }

  function normalizeGroupIdentity(group) {
    return String(group || '')
      .replace(/\s*%N(?=\s|$)/gi, '')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function groupIdentity(group) {
    const raw = String(group || '').trim();
    const normalized = normalizeGroupIdentity(raw);
    const match = normalized.match(/^((?:amd_)?mi\d+[a-z0-9]*_\d+):\s*(.+)$/i);
    const prefix = match ? match[1] : '';
    return {
      raw,
      normalized,
      name: match ? match[2] : normalized,
      queue: prefix ? (prefix.toLowerCase().startsWith('amd_') ? prefix : 'amd_' + prefix) : '',
      hasPlaceholder: /%N\b/i.test(raw),
    };
  }

  function analyticsGroupQuery(group) {
    return groupIdentity(group).normalized;
  }

  function analyticsHistoryUrl(group) {
    const url = new URL(window.location.href);
    url.searchParams.set('ops_analytics_search', analyticsGroupQuery(group));
    url.hash = 'ci-analytics';
    return url.toString();
  }

  function resolveOperationsGroups(summary, operations) {
    const catalog = operations && operations.reliability && Array.isArray(operations.reliability.group_catalog)
      ? operations.reliability.group_catalog
      : [];
    const identity = groupIdentity(summary && summary.group);
    if (!identity.normalized) return [];
    return catalog.filter(function(entry) {
      const queues = Array.isArray(entry.queues) ? entry.queues : [];
      if (identity.queue && !queues.includes(identity.queue)) return false;
      const rawNames = Array.isArray(entry.raw_names) ? entry.raw_names : [];
      const exactRawMatch = rawNames.some(function(rawName) {
        const candidate = String(rawName || '').replace(/\s+/g, ' ').trim();
        if (candidate === identity.normalized) return true;
        if (!identity.hasPlaceholder || !candidate.startsWith(identity.normalized + ' ')) return false;
        return /^\d+$/.test(candidate.slice(identity.normalized.length + 1));
      });
      if (exactRawMatch) return true;
      return !identity.hasPlaceholder && String(entry.name || '').trim() === identity.name;
    });
  }

  function exactGroupEvidence(summary, operationGroups) {
    const readyBuilds = new Set(buildRefs(summary).map(function(ref) { return Number(ref.build_number); }).filter(Boolean));
    return operationGroups.map(function(entry) {
      const observations = Array.isArray(entry.observations) ? entry.observations : [];
      const observation = observations.find(function(item) {
        return readyBuilds.has(Number(item.build_number)) && item.build_kind === 'nightly';
      }) || observations.find(function(item) {
        return readyBuilds.has(Number(item.build_number));
      }) || observations[0] || entry.last_incident || {};
      return {
        groupId: entry.id || observation.group_id || '',
        variant: entry.name || (entry.raw_names && entry.raw_names[0]) || summary.group,
        queue: observation.queue || (entry.queues && entry.queues[0]) || '',
        state: observation.state || entry.latest_state || 'unknown',
        observedAt: observation.observed_at || entry.latest_observed_at || '',
        buildNumber: observation.build_number || '',
        buildKind: observation.build_kind || '',
        buildUrl: observation.build_url || '',
        jobUrl: observation.job_url || entry.latest_url || '',
        stepUrl: observation.step_url || '',
        matchedReadyBuild: readyBuilds.has(Number(observation.build_number)),
      };
    }).filter(function(item) { return item.jobUrl || item.stepUrl || item.buildUrl; });
  }

  function projectIssue(ticket, indexes) {
    return ticket ? actions.pickProjectIssueForTicket(ticket, indexes) : null;
  }

  function groupRows(plan, operations) {
    const tickets = Array.isArray(plan.tickets) ? plan.tickets : [];
    const ticketByGroup = new Map();
    tickets.forEach(function(ticket) {
      const group = ticket && ticket.summary && ticket.summary.group;
      if (group) ticketByGroup.set(group, ticket);
    });
    const summaries = Array.isArray(plan.groups_all) && plan.groups_all.length
      ? plan.groups_all.slice()
      : tickets.map(function(ticket) { return ticket.summary || {}; });
    const summaryGroups = new Set(summaries.map(function(summary) { return summary.group; }));
    tickets.forEach(function(ticket) {
      const summary = ticket.summary || {};
      if (summary.group && !summaryGroups.has(summary.group)) summaries.push(summary);
    });
    return summaries.map(function(summary) {
      const ticket = ticketByGroup.get(summary.group) || null;
      const operationGroups = resolveOperationsGroups(summary, operations);
      return {
        summary,
        ticket,
        cohort: ticket ? 'current' : summary.currently_failing ? 'stale' : 'recovered',
        operationGroups,
        evidence: exactGroupEvidence(summary, operationGroups),
      };
    });
  }

  function groupEvidenceLinks(row) {
    if (!row.evidence.length) return buildLinks(row.summary);
    const root = h('div', { cls: 'ocv2-source-list' });
    row.evidence.slice(0, 2).forEach(function(item, index) {
      const suffix = String(item.variant || '').match(/\b(\d+)$/);
      const label = row.evidence.length > 1
        ? 'Job ' + (suffix ? suffix[1] : index + 1) + (item.buildNumber ? ' #' + item.buildNumber : '')
        : 'Exact job' + (item.buildNumber ? ' #' + item.buildNumber : '');
      root.append(ui.external(label, item.jobUrl || item.stepUrl || item.buildUrl, 'ocv2-mono', 'Open exact Buildkite group evidence'));
    });
    if (row.evidence.length > 2) {
      root.append(h('span', { cls: 'ocv2-muted', text: '+' + (row.evidence.length - 2) + ' exact logs in drawer' }));
    }
    return root;
  }

  function hardwareEvidence(summary) {
    const root = h('div', { cls: 'ocv2-source-list' });
    const rows = Object.entries(summary.hardware_latest || {});
    if (!rows.length) return h('span', { cls: 'ocv2-unavailable', text: 'No hardware state retained' });
    rows.forEach(function(entry) { root.append(ui.badge(entry[0] + ' ' + entry[1], ui.toneForState(entry[1]))); });
    return root;
  }

  function inspectGroup(row, plan, indexes) {
    const summary = row.summary || {};
    const ticket = row.ticket;
    const issue = projectIssue(ticket, indexes);
    const content = h('div', { cls: 'ocv2-stack' });
    content.append(ui.state(
      'is-info',
      (plan.window_days || 60) + '-day retained summary',
      'Use the all-main Test Groups history for the authoritative run timeline. The query preserves the full queue identity and this drawer retains exact Ready and operations evidence.',
      ui.internal(
        'Open all-main Test Groups history',
        analyticsHistoryUrl(summary.group),
        '',
        'Open CI Analytics Groups filtered to ' + analyticsGroupQuery(summary.group)
      )
    ));
    const stateLabel = row.cohort === 'current' ? 'Failing' : row.cohort === 'stale' ? 'Stale' : 'Recovered';
    const stateMeta = (row.cohort === 'stale' ? 'last-known failure; ' : '') + (summary.latest_date || 'no latest date');
    content.append(ui.kpis([
      { label: 'Latest-build status', value: stateLabel, meta: stateMeta, tone: row.cohort === 'current' ? 'is-danger' : row.cohort === 'stale' ? 'is-warning' : 'is-success', onClick: function() { groupEvidenceLinks(row).querySelector('a')?.click(); } },
      { label: 'State changes', value: summary.break_frequency || 0, meta: 'pass/fail flips in window', tone: Number(summary.break_frequency || 0) >= 2 ? 'is-warning' : '', onClick: function() { document.getElementById('ocv2-ready-timeline')?.scrollIntoView({ block: 'nearest' }); } },
      { label: 'Exact group logs', value: row.evidence.length, meta: row.operationGroups.length + ' strict identities matched', onClick: function() { document.getElementById('ocv2-ready-exact')?.scrollIntoView({ block: 'nearest' }); } },
      { label: 'Project issue', value: issue && issue.issue_number ? '#' + issue.issue_number : 'None', meta: issue && issue.status ? issue.status : 'No per-group issue', onClick: issue && issue.url ? function() { window.open(issue.url, '_blank', 'noopener'); } : function() { document.getElementById('ocv2-ready-issue')?.scrollIntoView({ block: 'nearest' }); } },
    ]));
    const history = ui.panel('Outcome milestones', 'Available ' + (plan.window_days || 60) + '-day summary evidence', []);
    history.root.id = 'ocv2-ready-timeline';
    history.body.append(ui.timeline([
      { date: summary.first_failure_in_window, label: 'First observed failure in the retained window', tone: 'is-danger' },
      { date: summary.last_successful, label: 'Most recent successful nightly observation', tone: 'is-success' },
      { date: summary.current_streak_started, label: summary.currently_failing ? 'Current failing streak began' : 'No active failing streak', tone: summary.currently_failing ? 'is-warning' : 'is-success' },
      { date: summary.latest_date, label: 'Latest retained group observation', tone: summary.currently_failing ? 'is-danger' : 'is-success' },
    ]));
    content.append(history.root);

    const exact = ui.panel('Exact Buildkite group evidence', row.evidence.length + ' operations records matched by queue and raw identity', []);
    exact.root.id = 'ocv2-ready-exact';
    if (row.evidence.length) {
      exact.body.classList.add('is-flush');
      exact.body.append(ui.table([
        { label: 'Strict variant', render: function(item) { return ui.external(item.variant, item.jobUrl || item.stepUrl || item.buildUrl); } },
        { label: 'Queue', nowrap: true, render: function(item) { return h('code', { cls: 'ocv2-mono', text: item.queue || 'Unavailable' }); } },
        { label: 'State', nowrap: true, render: function(item) { return ui.badge(item.state, ui.toneForState(item.state)); } },
        { label: 'Observed', nowrap: true, render: function(item) { return item.observedAt ? String(item.observedAt).replace('T', ' ').slice(0, 16) + ' UTC' : 'Unavailable'; } },
        { label: 'Build', nowrap: true, render: function(item) { return ui.external((item.buildKind || 'build') + ' #' + (item.buildNumber || '?'), item.buildUrl); } },
        { label: 'Job log', nowrap: true, render: function(item) { return ui.external(item.matchedReadyBuild ? 'Ready job' : 'Latest job', item.jobUrl); } },
        { label: 'Step output', nowrap: true, render: function(item) { return ui.external('Open step', item.stepUrl); } },
      ], row.evidence, {
        compact: true,
        scrollCue: true,
        caption: 'Strict queue and raw-name matches. Ready-build observations are preferred; otherwise the latest all-main observation is labeled explicitly.',
      }));
    } else {
      exact.body.append(ui.state('is-warning', 'Exact group evidence unavailable', 'The operations snapshot did not retain a strict queue and raw-name match for this Ready group.'));
    }
    content.append(exact.root);

    const evidence = ui.panel('Latest source evidence', 'Exact sources where the snapshot provides them', []);
    const builds = h('div', { id: 'ocv2-ready-builds', cls: 'ocv2-source-list' });
    builds.append(buildLinks(summary));
    evidence.body.append(builds);
    evidence.body.append(hardwareEvidence(summary));
    const issueRow = h('div', { id: 'ocv2-ready-issue', cls: 'ocv2-source-list' });
    if (issue && issue.url) issueRow.append(ui.external('Project issue #' + issue.issue_number, issue.url));
    if (plan.master_issue && plan.master_issue.url) issueRow.append(ui.external('Master issue #' + plan.master_issue.number, plan.master_issue.url));
    if (plan.master_issue_comment && plan.master_issue_comment.url) issueRow.append(ui.external('Latest automation update', plan.master_issue_comment.url));
    if (!issueRow.childNodes.length) issueRow.append(h('span', { cls: 'ocv2-unavailable', text: 'No issue source retained' }));
    evidence.body.append(issueRow);
    content.append(evidence.root);
    ui.dialog(summary.group || 'Group evidence', 'Ready Tickets outcome and source evidence', content);
  }

  function assignmentControl(row, plan, state) {
    const ticket = row.ticket;
    if (!ticket) return h('span', { cls: 'ocv2-unavailable', text: 'Not active' });
    if (plan.issue_mode === 'single_master') return h('span', { cls: 'ocv2-unavailable', text: 'Shared tracker' });
    if (!state.isAdmin) return h('span', { cls: 'ocv2-unavailable', text: 'Admin only' });
    if (!ticket.issue_number) return h('span', { cls: 'ocv2-unavailable', text: 'Issue required' });
    const control = select([{ value: '', label: 'Assign engineer' }].concat((plan.engineers || []).map(function(engineer) {
      return { value: engineer.github_login, label: engineer.display_name + ' (@' + engineer.github_login + ')' };
    })), ticket.assignee || '', 'Assign issue');
    control.addEventListener('change', async function() {
      if (!control.value) return;
      const pat = window.__authGate && window.__authGate.getGithubPat ? window.__authGate.getGithubPat() : '';
      if (!pat) { ui.dialog('Assignment unavailable', '', ui.state('is-warning', 'Session PAT unavailable', 'Sign in again before assigning an issue.')); return; }
      control.disabled = true;
      const result = await actions.assignIssue(pat, plan.issue_repo, ticket.issue_number, control.value);
      control.disabled = false;
      if (result.ok) {
        ticket.assignee = control.value;
        ui.dialog('Assignment updated', 'GitHub issue mutation', ui.state('is-success', 'Issue assigned', '@' + control.value + ' was assigned to issue #' + ticket.issue_number, ui.external('Open audit issue', ticket.issue_url)));
      } else {
        ui.dialog('Assignment failed', '', ui.state('is-danger', 'GitHub rejected the assignment', 'HTTP ' + result.status, ui.external('Open issue', ticket.issue_url)));
      }
    });
    return control;
  }

  function issueEvidence(row, plan, indexes, state) {
    const ticket = row.ticket;
    const linked = projectIssue(ticket, indexes);
    if (linked && linked.url) return ui.external('#' + linked.issue_number, linked.url, '', linked.status || 'Open project issue');
    if (ticket && ticket.issue_url) {
      const label = plan.issue_mode === 'single_master' ? 'Shared #' + ticket.issue_number : '#' + ticket.issue_number;
      return ui.external(label, ticket.issue_url);
    }
    if (!ticket) return h('span', { cls: 'ocv2-unavailable', text: 'No ticket' });
    const repo = plan.issue_repo || 'vllm-project/vllm';
    const search = ui.external('Search', actions.issueSearchUrl(repo, ticket.title || ''), '', 'Search for an existing issue');
    if (!state.isAdmin) return search;
    const root = h('div', { cls: 'ocv2-source-list' });
    root.append(search, ui.external('Review draft', actions.issueCreateUrl(repo, ticket.title || '', ticket.body || '', ticket.labels || []), '', 'Open prefilled GitHub issue draft'));
    return root;
  }

  function renderEvidence(container, plan, projectItems, operations, state) {
    const rows = groupRows(plan, operations);
    const masterNumber = Number(plan.master_issue && plan.master_issue.number) || 40554;
    const indexes = actions.buildProjectIssueIndexes(projectItems, masterNumber);
    const currentRows = rows.filter(function(row) { return row.cohort === 'current'; });
    const reportedCurrent = Number(plan.failing_groups_total);
    const failing = Number.isFinite(reportedCurrent) ? reportedCurrent : currentRows.length;
    const stale = rows.filter(function(row) { return row.cohort === 'stale'; }).length;
    const recovered = rows.filter(function(row) { return row.cohort === 'recovered'; }).length;
    const volatile = rows.filter(function(row) { return Number(row.summary.break_frequency || 0) >= 2; }).length;
    const linked = rows.filter(function(row) { return !!projectIssue(row.ticket, indexes); }).length;
    const tableHost = h('div');

    const toolbar = h('div', { cls: 'ocv2-toolbar' });
    const query = input({ type: 'search', value: viewState.query, placeholder: 'Filter group name or hardware', 'aria-label': 'Filter Ready Ticket groups' });
    const status = select([
      { value: 'failing', label: 'Currently failing' },
      { value: 'stale', label: 'Stale last-known failures' },
      { value: 'recovered', label: 'Recovered in window' },
      { value: 'volatile', label: 'Multiple state changes' },
      { value: 'linked', label: 'Has project issue' },
      { value: 'all', label: 'All tracked groups' },
    ], viewState.status, 'Filter by group status');
    const build = input({ type: 'search', value: viewState.build, placeholder: 'Build number', 'aria-label': 'Filter by Buildkite build number' });
    const pageSize = select([
      { value: '25', label: '25 rows' }, { value: '50', label: '50 rows' },
    ], String(viewState.pageSize), 'Rows per page');
    ui.append(toolbar, [query, status, build, pageSize]);

    function chooseStatus(next) {
      viewState.status = next;
      viewState.page = 0;
      status.value = next;
      renderRows();
      tableHost.scrollIntoView({ block: 'nearest' });
    }

    container.append(ui.kpis([
      { label: 'Currently failing', value: failing, meta: 'latest AMD nightly', tone: failing ? 'is-danger' : 'is-success', onClick: function() { chooseStatus('failing'); } },
      { label: 'Stale last-known', value: stale, meta: 'absent from latest AMD summary', tone: stale ? 'is-warning' : '', onClick: function() { chooseStatus('stale'); } },
      { label: 'Recovered', value: recovered, meta: 'tracked but green latest', tone: 'is-success', onClick: function() { chooseStatus('recovered'); } },
      { label: 'Mixed outcomes', value: volatile, meta: '2+ flips in ' + (plan.window_days || 60) + 'd', tone: volatile ? 'is-warning' : '', onClick: function() { chooseStatus('volatile'); } },
      { label: 'Dedicated issues', value: linked, meta: 'shared master #' + masterNumber + ' excluded', tone: linked ? 'is-info' : '', onClick: function() { chooseStatus('linked'); } },
    ]));

    const evidencePanel = ui.panel('Group evidence', failing + ' current, ' + stale + ' stale, ' + recovered + ' recovered', []);
    evidencePanel.body.append(toolbar, tableHost);
    container.append(evidencePanel.root);

    function renderRows() {
      viewState.query = query.value.trim().toLowerCase();
      viewState.status = status.value;
      viewState.build = build.value.trim().toLowerCase().replace(/^#/, '');
      viewState.pageSize = Number(pageSize.value) || 25;
      let filtered = rows.filter(function(row) {
        const summary = row.summary || {};
        if (viewState.status === 'failing' && row.cohort !== 'current') return false;
        if (viewState.status === 'stale' && row.cohort !== 'stale') return false;
        if (viewState.status === 'recovered' && row.cohort !== 'recovered') return false;
        if (viewState.status === 'volatile' && Number(summary.break_frequency || 0) < 2) return false;
        if (viewState.status === 'linked' && !projectIssue(row.ticket, indexes)) return false;
        if (viewState.query && ![
          summary.group,
          JSON.stringify(summary.hardware_latest || {}),
          row.evidence.map(function(item) { return item.queue + ' ' + item.variant; }).join(' '),
        ].some(function(value) { return String(value || '').toLowerCase().includes(viewState.query); })) return false;
        const evidenceBuilds = row.evidence.map(function(item) { return String(item.buildNumber || ''); }).join(' ');
        if (viewState.build && !(buildNumberText(summary) + ' ' + evidenceBuilds).includes(viewState.build)) return false;
        return true;
      });
      filtered.sort(function(a, b) {
        const order = { current: 0, stale: 1, recovered: 2 };
        return order[a.cohort] - order[b.cohort]
          || String(a.summary.group || '').localeCompare(String(b.summary.group || ''));
      });
      const pageCount = Math.max(1, Math.ceil(filtered.length / viewState.pageSize));
      viewState.page = Math.min(viewState.page, pageCount - 1);
      const start = viewState.page * viewState.pageSize;
      const shown = filtered.slice(start, start + viewState.pageSize);
      tableHost.innerHTML = '';
      tableHost.append(ui.table([
        { label: 'Group', render: function(row) { return ui.linkButton(row.summary.group || 'Unknown group', function() { inspectGroup(row, plan, indexes); }, 'Inspect retained group history'); } },
        { label: 'Latest state', render: function(row) {
          const label = row.cohort === 'current' ? 'Current failure' : row.cohort === 'stale' ? 'Stale last-known' : 'Recovered';
          return ui.linkButton(label, function() { inspectGroup(row, plan, indexes); }, 'Inspect state evidence');
        } },
        { label: 'Streak start', nowrap: true, render: function(row) { return ui.linkButton(row.summary.current_streak_started || 'No active streak', function() { inspectGroup(row, plan, indexes); }); } },
        { label: 'Last success', nowrap: true, render: function(row) { return ui.linkButton(row.summary.last_successful || 'Not observed', function() { inspectGroup(row, plan, indexes); }); } },
        { label: 'State changes', numeric: true, render: function(row) { return ui.linkButton(String(row.summary.break_frequency || 0), function() { inspectGroup(row, plan, indexes); }); } },
        { label: 'Group evidence', render: groupEvidenceLinks },
        { label: 'Issue', render: function(row) { return issueEvidence(row, plan, indexes, state); } },
        { label: 'Assignment', render: function(row) { return assignmentControl(row, plan, state); } },
      ], shown, {
        caption: shown.length + ' of ' + filtered.length + ' matching groups',
        empty: 'No groups match the selected status, name, and build filters.',
        scrollCue: true,
      }));

      const pager = h('div', { cls: 'ocv2-pager' });
      const text = h('span', { text: 'Page ' + (viewState.page + 1) + ' of ' + pageCount + ' - ' + filtered.length + ' matching groups' });
      const actionsRow = h('div', { cls: 'ocv2-actions' });
      const previous = ui.button('Previous', function() { viewState.page -= 1; renderRows(); }, '', 'Previous group page');
      const next = ui.button('Next', function() { viewState.page += 1; renderRows(); }, '', 'Next group page');
      previous.disabled = viewState.page <= 0;
      next.disabled = viewState.page >= pageCount - 1;
      actionsRow.append(previous, next);
      pager.append(text, actionsRow);
      tableHost.append(pager);
    }

    query.addEventListener('input', function() { viewState.page = 0; renderRows(); });
    status.addEventListener('change', function() { viewState.page = 0; renderRows(); });
    build.addEventListener('input', function() { viewState.page = 0; renderRows(); });
    pageSize.addEventListener('change', function() { viewState.page = 0; renderRows(); });
    renderRows();
  }

  function renderSourceBanner(container, plan, state) {
    const live = plan.mode === 'live';
    const paused = plan.feature_paused || plan.mode === 'paused';
    const banner = ui.state(
      paused ? 'is-danger' : live ? 'is-success' : 'is-warning',
      paused ? 'Automation paused' : live ? 'Live evidence snapshot' : 'Dry-run evidence snapshot',
      paused
        ? (plan.pause_reason || 'No upstream mutations are being performed.')
        : 'Read-only failure evidence is public. Current failures share one master tracker; the dedicated-issues count excludes that shared issue. Mutations remain admin-only.'
    );
    const sources = h('div', { cls: 'ocv2-actions' });
    if (plan.master_issue && plan.master_issue.url) sources.append(ui.external('Shared master tracker #' + plan.master_issue.number, plan.master_issue.url));
    if (plan.master_issue_comment && plan.master_issue_comment.url) sources.append(ui.external('Latest automation update', plan.master_issue_comment.url));
    if (state.isAdmin) sources.append(ui.badge('Admin controls enabled', 'is-success'));
    else sources.append(ui.badge('Read only', 'is-info'));
    banner.append(sources);
    container.append(banner);
  }

  async function render() {
    const seq = ++renderSeq;
    const container = document.getElementById('ci-ready-view');
    if (!container) return;
    exposeReadyNavigation();
    ui.page(container, {
      id: 'ready-tickets',
      title: 'Ready Tickets',
      description: 'Public AMD nightly failure evidence with exact Buildkite sources and admin-gated issue controls.',
    });
    const loading = ui.state('', 'Loading Ready Tickets evidence', 'Fetching the retained summary, strict operations catalog, and project links.');
    container.append(loading);
    const plan = await actions.loadPlan();
    if (seq !== renderSeq) return;
    if (!plan) {
      loading.replaceWith(ui.state('is-warning', 'Ready Tickets snapshot unavailable', 'The collector has not published ready_tickets.json yet.'));
      return;
    }
    const [projectItems, operations] = await Promise.all([
      actions.loadProjectItems(),
      loadOperations(),
    ]);
    if (seq !== renderSeq) return;
    const gate = window.__authGate;
    const state = {
      isAdmin: !!(gate && gate.isAdmin && gate.isAdmin()),
      login: gate && gate.getLogin ? gate.getLogin() : '',
    };
    plan.engineers = state.isAdmin ? await actions.loadEngineers() : [];
    if (seq !== renderSeq) return;
    loading.remove();
    renderSourceBanner(container, plan, state);
    renderEvidence(container, plan, projectItems || {}, operations || {}, state);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', render);
  else render();
  document.addEventListener('click', function(event) {
    const target = event.target.closest && event.target.closest('[data-tab="ci-ready"]');
    if (target) setTimeout(render, 0);
  });
  document.addEventListener('auth:changed', render);
})();
