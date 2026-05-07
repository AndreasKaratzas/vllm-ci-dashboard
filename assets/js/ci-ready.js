/**
 * Ready Tickets — dashboard view for the AMD nightly failure summary.
 *
 * Reads ``data/vllm/ci/ready_tickets.json`` written by
 * ``scripts/vllm/sync_ready_tickets.py`` and renders the one upstream master
 * issue plus the per-group failure table that feeds that issue body.
 */
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

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', render);
  } else {
    render();
  }

  document.addEventListener('click', (e) => {
    const btn = e.target.closest && e.target.closest('[data-tab="ci-ready"]');
    if (btn) setTimeout(render, 50);
  });
  document.addEventListener('auth:changed', render);
})();
