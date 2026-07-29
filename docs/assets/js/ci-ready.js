/**
 * Ready Tickets — dashboard view for the AMD nightly failure summary.
 *
 * Reads ``data/vllm/ci/ready_tickets.json`` written by
 * ``scripts/vllm/sync_ready_tickets.py`` and renders the dashboard-owned
 * tracker plus read-only Project #39 evidence for each failing group.
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

  let readyTableSort = { key: null, dir: 'asc' };
  const CI_FAILURE_PREFIX_RE = /^\[CI Failure\]:\s*/i;
  const HW_PREFIX_RE = /^mi\d+_\d+:\s*/i;
  const PROJECT_ISSUE_CUTOVER_NUMBER = 40554;

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

  function renderBanner(container, plan) {
    const paused = !!(plan && (plan.feature_paused || plan.mode === 'paused'));
    const dryRun = plan && plan.mode !== 'live' && !paused;
    const msg = paused
      ? (plan.pause_reason || 'Ready Tickets automation is paused. Project #39 remains read-only and the dashboard tracker will not be updated.')
      : plan && plan.mode === 'live' && plan.issue_mode === 'single_master'
        ? 'Live mode updates one managed comment on dashboard issue #255. Upstream vLLM issues and Project #39 remain read-only.'
        : dryRun
          ? 'Dry-run mode — Project #39 evidence is read-only and the dashboard tracker will not be modified.'
          : 'Live mode — the dashboard tracker sync is active; upstream evidence is read-only.';
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
    card.append(h('div', { text: 'Dashboard tracker', style: { fontSize: '10px', color: C.m, textTransform: 'uppercase', letterSpacing: '0.05em' } }));
    const row = h('div', { style: { display: 'flex', gap: '10px', alignItems: 'baseline', flexWrap: 'wrap', marginTop: '6px' } });
    row.append(h('a', {
      href: master.url,
      target: '_blank',
      rel: 'noopener',
      text: `#${master.number || '—'}`,
      style: { color: C.b, fontWeight: '700', fontSize: '18px' },
    }));
    row.append(h('span', { text: master.title || 'Current AMD nightly failures', style: { color: C.t, fontWeight: '600' } }));
    card.append(row);
    card.append(h('p', {
      text: 'Every currently failing AMD nightly group is summarized in this dashboard-owned issue. The table below is published to its single managed automation comment; Project #39 is read-only evidence.',
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

  function fmtDate(d) { return d || '—'; }
  function daysSince(d) {
    if (!d) return '—';
    const t = new Date(d + 'T12:00:00Z').getTime();
    if (!t) return '—';
    const diff = Math.floor((Date.now() - t) / 86400000);
    return diff + 'd';
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

  function buildProjectIssueIndexes(projectItems, projectIssueCutoverNumber) {
    const byTitle = {};
    const byNorm = {};
    const items = projectItems && projectItems.items_by_number ? projectItems.items_by_number : {};
    Object.keys(items).forEach(function(key) {
      const item = items[key] || {};
      const num = Number(item.issue_number || key);
      if (!(num > projectIssueCutoverNumber)) return;
      const issueState = String(item.issue_state || '').trim().toLowerCase();
      if (issueState && issueState !== 'open') return;
      const title = String(item.title || '').trim();
      if (!title) return;
      byTitle[title] = item;
      const norm = normalizeIssueTitle(title);
      if (!norm) return;
      if (!byNorm[norm]) byNorm[norm] = [];
      byNorm[norm].push(item);
    });
    return { byTitle, byNorm, projectIssueCutoverNumber };
  }

  function pickProjectIssueForTicket(ticket, indexes) {
    if (!ticket) return null;
    const ticketNum = Number(ticket.issue_number);
    if (ticketNum && ticket.project_status && ticketNum > indexes.projectIssueCutoverNumber) {
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
        return Number.isFinite(n) && ticket && !String(ticket.project_status || '').startsWith('Tracked in ') ? n : null;
      }
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

  function renderMetricsTable(container, plan, projectItems) {
    const card = h('div', { style: { background: C.bg, border: `1px solid ${C.bd}`, borderRadius: '8px', padding: '14px 18px', marginBottom: '12px' } });
    card.append(h('h3', { text: `Failing test groups (${(plan.tickets || []).length})`, style: { marginTop: 0, fontSize: '15px' } }));
    const trackerIssueNumber = Number(plan && plan.master_issue && plan.master_issue.number) || 255;
    const projectIssueIndexes = buildProjectIssueIndexes(projectItems, PROJECT_ISSUE_CUTOVER_NUMBER);

    if (!plan.tickets || !plan.tickets.length) {
      card.append(h('p', { text: 'No AMD nightly test groups currently failing. Nothing to triage.', style: { color: C.m, fontSize: '13px' } }));
      container.append(card);
      return;
    }

    const columns = [
      { key: 'group', label: 'Group', defaultDir: 'asc' },
      { key: 'streak_start', label: 'Streak start', defaultDir: 'desc' },
      { key: 'first_fail', label: 'First fail', defaultDir: 'desc' },
      { key: 'last_success', label: 'Last success', defaultDir: 'desc' },
      { key: 'break_freq', label: 'Break freq', defaultDir: 'desc' },
      { key: 'latest_builds', label: 'Latest build(s)', defaultDir: 'desc' },
      { key: 'project_issue', label: 'Issue evidence', defaultDir: 'desc' },
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
          issueCell.append(h('a', {
            href: (plan.master_issue && plan.master_issue.url) || t.issue_url,
            target: '_blank',
            rel: 'noopener',
            text: `Tracker #${trackerIssueNumber}`,
            style: { color: C.b, fontWeight: '600' },
          }));
        }
        tr.append(issueCell);

        tbody.append(tr);
      }
      table.append(tbody);
      tableMount.append(table);
    }

    renderTable();
    container.append(card);
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
    // The nav button is hidden from guests, but a forced panel activation
    // still lands here, so preserve the tab's access policy.
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
    container.append(h('p', { text: 'AMD nightly failure tracking for dashboard issue #255, with read-only evidence from upstream Project #39.', style: { color: C.m, marginTop: 0, marginBottom: '14px' } }));

    const plan = await loadPlan();
    if (seq !== renderSeq) return;
    if (!plan) {
      container.append(h('p', { text: 'No ready_tickets.json found yet — the collector will produce one on its next run.', style: { color: C.m, fontStyle: 'italic' } }));
      return;
    }
    const projectItems = await loadProjectItems();
    if (seq !== renderSeq) return;
    renderBanner(container, plan);
    renderSummaryCards(container, plan);
    if (plan.feature_paused || plan.mode === 'paused') {
      container.append(h('p', {
        text: 'This feature is paused. Project #39 remains read-only and dashboard issue #255 is not being updated.',
        style: { color: C.m, marginTop: 0 },
      }));
      return;
    }
    renderMasterIssueCard(container, plan);
    renderMetricsTable(container, plan, projectItems);
  }

  window.OpsReadyActions = {
    loadPlan,
    loadProjectItems,
    buildProjectIssueIndexes,
    pickProjectIssueForTicket,
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
  const operationsPromises = new Map();
  const READY_VIEWS = new Set(['tickets', 'ownership']);
  const OPERATION_SECTION_ASSETS = {
    reliability: 'data/vllm/ci/operations_v2/reliability.json',
    ownership: 'data/vllm/ci/operations_v2/ownership.json',
  };
  const viewState = {
    view: 'tickets',
    query: '',
    status: 'failing',
    build: '',
    page: 0,
    pageSize: 15,
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

  function loadOperations(sectionNames) {
    const names = Array.from(sectionNames || []).sort();
    const key = names.join(',');
    if (!operationsPromises.has(key)) {
      let request;
      if (window.OpsV2 && typeof window.OpsV2.loadSections === 'function') {
        request = window.OpsV2.loadSections(names);
      } else {
        request = Promise.all(names.map(function(name) {
          const path = OPERATION_SECTION_ASSETS[name];
          if (!path) return Promise.resolve(null);
          return fetch(path + '?_=' + Math.floor(Date.now() / 300000))
            .then(function(response) { return response.ok ? response.json() : null; });
        })).then(function(sections) {
          return sections.reduce(function(combined, section) {
            return Object.assign(combined, section || {});
          }, {});
        });
      }
      operationsPromises.set(key, request.catch(function() {
        operationsPromises.delete(key);
        return null;
      }));
    }
    return operationsPromises.get(key);
  }

  function replaceReadyUrl(mutator) {
    try {
      const url = new URL(window.location.href);
      mutator(url);
      window.history.replaceState(null, '', url.pathname + url.search + url.hash);
    } catch (_) {}
  }

  function syncReadyRoute() {
    replaceReadyUrl(function(url) {
      const requested = url.searchParams.get('ops_ready_view');
      viewState.view = READY_VIEWS.has(requested) ? requested : 'tickets';
      Array.from(url.searchParams.keys()).forEach(function(key) {
        if (key.startsWith('ops_') && key !== 'ops_ready_view') url.searchParams.delete(key);
      });
      if (viewState.view === 'tickets') url.searchParams.delete('ops_ready_view');
    });
  }

  function setReadyView(next) {
    if (!READY_VIEWS.has(next)) return;
    viewState.view = next;
    replaceReadyUrl(function(url) {
      Array.from(url.searchParams.keys()).forEach(function(key) {
        if (key.startsWith('ops_') && key !== 'ops_ready_view') url.searchParams.delete(key);
      });
      if (next === 'tickets') url.searchParams.delete('ops_ready_view');
      else url.searchParams.set('ops_ready_view', next);
      url.hash = 'ci-ready';
    });
    renderIfActive();
  }

  function migrateLegacyOwnershipRoute() {
    let migrated = false;
    replaceReadyUrl(function(url) {
      if (url.searchParams.get('ops_health_view') !== 'ownership') return;
      url.searchParams.delete('ops_health_view');
      url.searchParams.set('ops_ready_view', 'ownership');
      url.hash = 'ci-ready';
      migrated = true;
    });
    if (migrated && window.__dashboardNav && typeof window.__dashboardNav.switchTab === 'function') {
      window.__dashboardNav.switchTab('ci-ready', { updateHash: false });
    }
    return migrated;
  }

  function readyTabs(container) {
    const tabs = h('div', {
      cls: 'ops-segmented ocv2-ready-subnav',
      role: 'group',
      'aria-label': 'Ready Tickets view',
    });
    [
      { id: 'tickets', label: 'Tickets' },
      { id: 'ownership', label: 'CI ownership' },
    ].forEach(function(item) {
      const active = item.id === viewState.view;
      const button = h('button', {
        cls: 'ops-segment' + (active ? ' is-active' : ''),
        type: 'button',
        text: item.label,
        'aria-pressed': active ? 'true' : 'false',
        'data-ready-view': item.id,
      });
      button.addEventListener('click', function() { setReadyView(item.id); });
      tabs.append(button);
    });
    container.append(tabs);
  }

  function signInRequired(container) {
    const signIn = h('button', {
      cls: 'ocv2-button is-primary',
      type: 'button',
      text: 'Sign in',
    });
    signIn.addEventListener('click', function() {
      const auth = window.__authGate;
      if (auth && auth.promptSignIn) auth.promptSignIn();
    });
    container.append(ui.state(
      'is-warning',
      'Sign in required',
      'Ready Tickets and CI ownership are available only to signed-in dashboard members.',
      signIn
    ));
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

  function displayGroupIdentity(group) {
    const identity = groupIdentity(group);
    return identity.normalized + (identity.hasPlaceholder ? ' (sharded)' : '');
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
    if (issue && issue.url) issueRow.append(ui.external('Read-only Project #39 issue #' + issue.issue_number, issue.url));
    if (plan.master_issue && plan.master_issue.url) issueRow.append(ui.external('Dashboard tracker #' + plan.master_issue.number, plan.master_issue.url));
    if (plan.master_issue_comment && plan.master_issue_comment.url) issueRow.append(ui.external('Latest tracker update', plan.master_issue_comment.url));
    if (!issueRow.childNodes.length) issueRow.append(h('span', { cls: 'ocv2-unavailable', text: 'No issue source retained' }));
    evidence.body.append(issueRow);
    content.append(evidence.root);
    ui.dialog(displayGroupIdentity(summary.group) || 'Group evidence', 'Ready Tickets outcome and source evidence', content);
  }

  function issueEvidence(row, plan, indexes) {
    const ticket = row.ticket;
    const linked = projectIssue(ticket, indexes);
    if (linked && linked.url) return ui.external('#' + linked.issue_number, linked.url, '', linked.status || 'Open read-only Project #39 issue');
    if (ticket && ticket.issue_url) {
      const masterNumber = plan.master_issue && plan.master_issue.number;
      const label = ticket.issue_number === masterNumber ? 'Tracker #' + ticket.issue_number : '#' + ticket.issue_number;
      return ui.external(label, ticket.issue_url);
    }
    if (plan.master_issue && plan.master_issue.url) {
      return ui.external('Tracker #' + plan.master_issue.number, plan.master_issue.url);
    }
    return h('span', { cls: 'ocv2-unavailable', text: 'No issue evidence' });
  }

  function renderEvidence(container, plan, projectItems, operations) {
    const rows = groupRows(plan, operations);
    const trackerNumber = Number(plan.master_issue && plan.master_issue.number) || 255;
    const indexes = actions.buildProjectIssueIndexes(projectItems, PROJECT_ISSUE_CUTOVER_NUMBER);
    const currentRows = rows.filter(function(row) { return row.cohort === 'current'; });
    const reportedCurrent = Number(plan.failing_groups_total);
    const failing = Number.isFinite(reportedCurrent) ? reportedCurrent : currentRows.length;
    const stale = rows.filter(function(row) { return row.cohort === 'stale'; }).length;
    const recovered = rows.filter(function(row) { return row.cohort === 'recovered'; }).length;
    const volatile = rows.filter(function(row) { return Number(row.summary.break_frequency || 0) >= 2; }).length;
    const linked = rows.filter(function(row) { return !!projectIssue(row.ticket, indexes); }).length;
    const amdLatestStates = operations && operations.amd_test_health && operations.amd_test_health.summary
      ? operations.amd_test_health.summary.latest_state_counts || {}
      : {};
    const exactAmdIncidents = Number(amdLatestStates.soft || 0) + Number(amdLatestStates.hard || 0);
    const failingMeta = 'normalized latest AMD groups' + (exactAmdIncidents ? '; ' + exactAmdIncidents + ' exact job variants in Analytics' : '');
    const tableHost = h('div');

    const toolbar = h('div', { cls: 'ocv2-toolbar' });
    const query = input({ type: 'search', value: viewState.query, placeholder: 'Filter group name or hardware', 'aria-label': 'Filter Ready Ticket groups' });
    const status = select([
      { value: 'failing', label: 'Currently failing' },
      { value: 'stale', label: 'Stale last-known failures' },
      { value: 'recovered', label: 'Recovered in window' },
      { value: 'volatile', label: 'Multiple state changes' },
      { value: 'linked', label: 'Has Project #39 issue' },
      { value: 'all', label: 'All tracked groups' },
    ], viewState.status, 'Filter by group status');
    const build = input({ type: 'search', value: viewState.build, placeholder: 'Build number', 'aria-label': 'Filter by Buildkite build number' });
    const pageSize = select([
      { value: '15', label: '15 rows' }, { value: '25', label: '25 rows' }, { value: '50', label: '50 rows' },
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
      { label: 'Failing Ready groups', value: failing, meta: failingMeta, tone: failing ? 'is-danger' : 'is-success', onClick: function() { chooseStatus('failing'); } },
      { label: 'Stale last-known', value: stale, meta: 'absent from latest AMD summary', tone: stale ? 'is-warning' : '', onClick: function() { chooseStatus('stale'); } },
      { label: 'Recovered', value: recovered, meta: 'tracked but green latest', tone: 'is-success', onClick: function() { chooseStatus('recovered'); } },
      { label: 'Mixed outcomes', value: volatile, meta: '2+ flips in ' + (plan.window_days || 60) + 'd', tone: volatile ? 'is-warning' : '', onClick: function() { chooseStatus('volatile'); } },
      { label: 'Dedicated issues', value: linked, meta: 'dashboard tracker #' + trackerNumber + ' excluded', tone: linked ? 'is-info' : '', onClick: function() { chooseStatus('linked'); } },
    ]));

    const evidencePanel = ui.panel('Group evidence', failing + ' failing normalized groups, ' + stale + ' stale, ' + recovered + ' recovered', []);
    evidencePanel.body.append(toolbar, tableHost);
    container.append(evidencePanel.root);

    function renderRows() {
      viewState.query = query.value.trim().toLowerCase();
      viewState.status = status.value;
      viewState.build = build.value.trim().toLowerCase().replace(/^#/, '');
      viewState.pageSize = Number(pageSize.value) || 15;
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
        { label: 'Group', render: function(row) { return ui.linkButton(displayGroupIdentity(row.summary.group) || 'Unknown group', function() { inspectGroup(row, plan, indexes); }, 'Inspect retained group history'); } },
        { label: 'Latest state', render: function(row) {
          const label = row.cohort === 'current' ? 'Current failure' : row.cohort === 'stale' ? 'Stale last-known' : 'Recovered';
          return ui.linkButton(label, function() { inspectGroup(row, plan, indexes); }, 'Inspect state evidence');
        } },
        { label: 'Streak start', nowrap: true, render: function(row) { return ui.linkButton(row.summary.current_streak_started || 'No active streak', function() { inspectGroup(row, plan, indexes); }); } },
        { label: 'Last success', nowrap: true, render: function(row) { return ui.linkButton(row.summary.last_successful || 'Not observed', function() { inspectGroup(row, plan, indexes); }); } },
        { label: 'State changes', numeric: true, render: function(row) { return ui.linkButton(String(row.summary.break_frequency || 0), function() { inspectGroup(row, plan, indexes); }); } },
        { label: 'Group evidence', render: groupEvidenceLinks },
        { label: 'Issue evidence', render: function(row) { return issueEvidence(row, plan, indexes); } },
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

  function renderSourceBanner(container, plan) {
    const live = plan.mode === 'live';
    const paused = plan.feature_paused || plan.mode === 'paused';
    const banner = ui.state(
      paused ? 'is-danger' : live ? 'is-success' : 'is-warning',
      paused ? 'Automation paused' : live ? 'Live dashboard tracker' : 'Dry-run evidence snapshot',
      paused
        ? (plan.pause_reason || 'The dashboard tracker is not being updated; upstream evidence remains read-only.')
        : 'Signed-in, read-only failure evidence from Project #39 and upstream vLLM issues. Current failures share dashboard issue #255, whose managed automation comment is updated with the repository token.'
    );
    const sources = h('div', { cls: 'ocv2-actions' });
    if (plan.master_issue && plan.master_issue.url) sources.append(ui.external('Dashboard tracker #' + plan.master_issue.number, plan.master_issue.url));
    if (plan.master_issue_comment && plan.master_issue_comment.url) sources.append(ui.external('Latest tracker update', plan.master_issue_comment.url));
    sources.append(ui.badge('Read only: Project #39', 'is-info'));
    banner.append(sources);
    container.append(banner);
  }

  async function render() {
    const seq = ++renderSeq;
    const container = document.getElementById('ci-ready-view');
    if (!container) return;
    syncReadyRoute();
    ui.page(container, {
      id: 'ready-tickets',
      title: 'Ready Tickets',
      description: 'Signed-in AMD nightly ticket evidence and CI ownership status.',
    });
    const gate = window.__authGate;
    const allowed = !!(gate && typeof gate.canAccessTab === 'function'
      ? gate.canAccessTab('ci-ready')
      : (gate && gate.isAuthed && gate.isAuthed()));
    if (!allowed) {
      operationsPromises.clear();
      signInRequired(container);
      return;
    }
    readyTabs(container);
    if (viewState.view === 'ownership') {
      const ownershipLoading = ui.state('', 'Loading CI ownership', 'Fetching the signed-in ownership and escalation snapshot.');
      container.append(ownershipLoading);
      const ownershipOperations = await loadOperations(['ownership']);
      if (seq !== renderSeq) return;
      ownershipLoading.remove();
      if (!ownershipOperations) {
        container.append(ui.state(
          'is-warning',
          'CI ownership snapshot unavailable',
          'The ownership section could not be loaded. Retry now or reload after the collector publishes it.',
          ui.button('Retry', function() {
            operationsPromises.delete('ownership');
            renderIfActive();
          }, '', 'Retry loading CI ownership')
        ));
        return;
      }
      if (!window.OpsV2 || typeof window.OpsV2.renderOwnership !== 'function') {
        container.append(ui.state('is-warning', 'CI ownership renderer unavailable', 'Reload after the latest dashboard assets have been deployed.'));
        return;
      }
      const ownershipHost = h('section', { cls: 'ops-v2-host ocv2-ownership-host' });
      container.append(ownershipHost);
      window.OpsV2.renderOwnership(ownershipHost, ownershipOperations);
      return;
    }
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
      loadOperations(['reliability']),
    ]);
    if (seq !== renderSeq) return;
    loading.remove();
    renderSourceBanner(container, plan);
    renderEvidence(container, plan, projectItems || {}, operations || {});
  }

  function renderIfActive() {
    const panel = document.getElementById('tab-ci-ready');
    if (panel && panel.classList.contains('active')) render();
  }

  function initializeReadyRoute() {
    migrateLegacyOwnershipRoute();
    renderIfActive();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initializeReadyRoute);
  else initializeReadyRoute();
  document.addEventListener('click', function(event) {
    const target = event.target.closest && event.target.closest('[data-tab="ci-ready"]');
    if (target) setTimeout(renderIfActive, 0);
  });
  window.addEventListener('hashchange', function() { setTimeout(renderIfActive, 0); });
  document.addEventListener('auth:changed', initializeReadyRoute);
})();
