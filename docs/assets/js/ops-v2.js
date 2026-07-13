/**
 * Signal Desk v2.
 *
 * Operations renderer for Home, Health, Analytics, Perf Eval, Queue,
 * Trajectory, and Omni.
 */
(function () {
  'use strict';

  const OWNED_TABS = new Set([
    'projects', 'ci-health', 'ci-analytics', 'ci-perf-eval', 'ci-queue', 'ci-hotness', 'ci-omni',
  ]);
  const cache = new Map();
  const charts = new Map();
  const SOURCE_ASSETS = {
    operations: 'data/vllm/ci/operations_v2.json',
    queueHistory: 'data/vllm/ci/queue_timeseries.jsonl',
    perf: 'data/vllm/perf_eval/perf_eval.json',
  };
  const state = {
    healthView: 'overview',
    analyticsView: 'groups',
    analyticsPipeline: 'ci',
    homeWork: 'issues',
    healthSearch: '',
    healthPlan: 'all',
    healthResult: 'all',
    analyticsSearch: '',
    queueScope: 'amd',
    queueView: 'current',
    queueRange: '24h',
    queueHistoryQueue: 'fleet',
    queueIncludeIdle: false,
    trajectoryWindow: '24h',
    trajectoryWorkload: 'all',
    trajectoryHardware: 'all',
    trajectorySearch: '',
    perfView: 'performance',
    perfModel: 'all',
    perfDevice: 'all',
  };

  function n(tag, cls, text) {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text !== undefined && text !== null) el.textContent = String(text);
    return el;
  }

  function add(parent, children) {
    for (const child of Array.isArray(children) ? children : [children]) {
      if (child === null || child === undefined || child === false) continue;
      parent.append(child.nodeType ? child : document.createTextNode(String(child)));
    }
    return parent;
  }

  function clear(el) {
    while (el.firstChild) el.removeChild(el.firstChild);
  }

  function value(v, fallback) {
    return v === null || v === undefined || v === '' ? (fallback || '-') : v;
  }

  function integer(v) {
    if (v === null || v === undefined || v === '') return '-';
    return Number.isFinite(Number(v)) ? Number(v).toLocaleString() : '-';
  }

  function percent(num, den, digits) {
    if (!Number.isFinite(Number(num)) || !Number.isFinite(Number(den)) || Number(den) <= 0) return '-';
    return (Number(num) / Number(den) * 100).toFixed(digits === undefined ? 1 : digits) + '%';
  }

  function duration(minutes) {
    if (minutes === null || minutes === undefined || minutes === '') return '-';
    if (!Number.isFinite(Number(minutes))) return '-';
    const m = Number(minutes);
    if (m >= 1440) return (m / 1440).toFixed(m >= 2880 ? 0 : 1) + 'd';
    if (m >= 60) return Math.floor(m / 60) + 'h ' + Math.round(m % 60) + 'm';
    return m.toFixed(m < 10 ? 1 : 0) + 'm';
  }

  function shortDate(ts) {
    if (!ts) return '-';
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return String(ts).slice(0, 10);
    return d.toLocaleString(undefined, {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'});
  }

  function age(ts) {
    if (!ts) return 'timestamp unavailable';
    const delta = Date.now() - new Date(ts).getTime();
    if (!Number.isFinite(delta)) return 'timestamp unavailable';
    const mins = Math.max(0, Math.floor(delta / 60000));
    if (mins < 2) return 'just now';
    if (mins < 60) return mins + 'm ago';
    const hours = Math.floor(mins / 60);
    if (hours < 48) return hours + 'h ago';
    return Math.floor(hours / 24) + 'd ago';
  }

  function toneForState(s) {
    const stateName = String(s || '').toLowerCase();
    if (['passed', 'green', 'healthy', 'fixed', 'success'].includes(stateName)) return 'is-success';
    if (['failed', 'hard', 'red', 'critical', 'surge', 'broken'].includes(stateName)) return 'is-danger';
    if (['soft', 'soft_fail', 'soft_failed', 'warning', 'attention', 'elevated', 'waiting'].includes(stateName)) return 'is-warning';
    if (['new', 'info', 'recurring'].includes(stateName)) return 'is-info';
    return 'is-neutral';
  }

  function normalizeLabel(label) {
    return String(label || '').trim().replace(/\s+/g, ' ').toLowerCase();
  }

  function isRetiredQueue(queue) {
    return /^amd_mi355b(?:_|$)/i.test(String(queue || ''));
  }

  function isAmdQueue(queue) {
    const name = String(queue || '').toLowerCase();
    return name === 'amd-cpu' || name.startsWith('amd_');
  }

  function buildUrl(pipeline, number) {
    if (!pipeline || number === null || number === undefined || number === '') return '';
    return 'https://buildkite.com/vllm/' + encodeURIComponent(pipeline) + '/builds/' + encodeURIComponent(number);
  }

  function recordUrl(record) {
    if (!record) return '';
    return record.job_url || record.step_url || record.url || record.build_url || record.html_url || '';
  }

  function pipelineUrlMatches(url, pipeline, requireJob, expectedBuild) {
    if (!url || !pipeline) return false;
    try {
      const parsed = new URL(String(url));
      if (parsed.protocol !== 'https:' || parsed.host !== 'buildkite.com') return false;
      const parts = parsed.pathname.split('/').filter(Boolean);
      if (parts.length < 4 || parts[0] !== 'vllm' || parts[1] !== pipeline || parts[2] !== 'builds' || !/^\d+$/.test(parts[3])) return false;
      if (expectedBuild !== null && expectedBuild !== undefined && expectedBuild !== '' && String(expectedBuild) !== parts[3]) return false;
      const suffix = parts.slice(4);
      if (!requireJob) return suffix.length === 0;
      if (suffix.length < 2 || suffix[0] !== 'steps') return false;
      if (suffix[1] === 'canvas') return Boolean(parsed.searchParams.get('jid') || parsed.searchParams.get('sid'));
      return Boolean(suffix[1]);
    } catch (_) {
      return false;
    }
  }

  function exactPipelineEvidenceUrl(record, pipeline) {
    if (!record || !pipeline) return '';
    const urls = [record.job_url, record.step_url, record.url, record.latest_url, record.html_url];
    const buildNumber = record.build_number !== undefined ? record.build_number : record.number;
    return urls.find(function (url) { return pipelineUrlMatches(url, pipeline, true, buildNumber); }) || '';
  }

  function exactPipelineBuildUrl(record, pipeline) {
    if (!record || !pipeline) return '';
    const urls = [record.build_url, record.url, record.html_url];
    const buildNumber = record.build_number !== undefined ? record.build_number : record.number;
    return urls.find(function (url) { return pipelineUrlMatches(url, pipeline, false, buildNumber); }) || '';
  }

  function queryName(name) {
    return 'ops_' + name;
  }

  function queryValue(name) {
    try { return new URL(window.location.href).searchParams.get(queryName(name)); } catch (_) { return null; }
  }

  function setQueryValue(name, next) {
    try {
      const url = new URL(window.location.href);
      if (next === null || next === undefined || next === '') url.searchParams.delete(queryName(name));
      else url.searchParams.set(queryName(name), String(next));
      window.history.replaceState(null, '', url.pathname + url.search + url.hash);
    } catch (_) {}
  }

  function setRouteState(tabId, key, next, queryKey) {
    state[key] = next;
    setQueryValue(queryKey || key, next);
    render(tabId, true);
  }

  function syncRouteState(tabId) {
    const specs = {
      'ci-health': [['healthView', 'health_view', ['overview', 'gating', 'coverage', 'diagnostics']]],
      'ci-analytics': [
        ['analyticsView', 'analytics_view', ['groups', 'flakes', 'nightlies', 'retries', 'latency']],
        ['analyticsPipeline', 'analytics_pipeline', ['ci', 'amd-ci']],
        ['analyticsSearch', 'analytics_search', null],
      ],
      'ci-queue': [
        ['queueView', 'queue_view', ['current', 'history', 'jobs']],
        ['queueRange', 'queue_range', ['24h', '7d', '30d']],
        ['queueScope', 'queue_scope', ['amd', 'all']],
        ['queueHistoryQueue', 'queue_history_queue', null],
      ],
      'ci-hotness': [['trajectoryWindow', 'trajectory_window', ['24h', '72h', '7d', '30d']]],
      'ci-perf-eval': [['perfView', 'perf_view', ['performance', 'accuracy']]],
    };
    (specs[tabId] || []).forEach(function (spec) {
      const next = queryValue(spec[1]);
      if (next && (!spec[2] || spec[2].includes(next))) state[spec[0]] = next;
    });
    if (tabId === 'ci-analytics' && queryValue('analytics_search') !== null) state.analyticsView = 'groups';
  }

  function navigateTo(tabId, updates) {
    if (activeOverlay) closeOverlay();
    Object.entries(updates || {}).forEach(function (entry) {
      state[entry[0]] = entry[1];
      setQueryValue(entry[0].replace(/[A-Z]/g, function (letter) { return '_' + letter.toLowerCase(); }), entry[1]);
    });
    if (window.__dashboardNav && typeof window.__dashboardNav.switchTab === 'function') {
      window.__dashboardNav.switchTab(tabId);
    } else {
      window.location.hash = tabId;
    }
  }

  function openTestGroupHistory(name) {
    state.analyticsSearch = String(name || '');
    state.analyticsView = 'groups';
    setQueryValue('analytics_search', state.analyticsSearch);
    setQueryValue('analytics_view', 'groups');
    navigateTo('ci-analytics');
  }

  function badge(label, tone) {
    return n('span', 'ops-badge ' + (tone || toneForState(label)), label || 'unknown');
  }

  function externalLink(label, url, cls) {
    if (!url) return n('span', cls || 'ops-muted', label || '-');
    const renderedLabel = label === undefined || label === null ? 'Open' : label;
    const a = n('a', cls || '', renderedLabel);
    a.href = url;
    a.target = '_blank';
    a.rel = 'noopener';
    a.setAttribute('aria-label', (renderedLabel || 'Open source') + ' (opens source in a new tab)');
    return a;
  }

  function button(label, onClick, active) {
    const b = n('button', 'ops-button' + (active ? ' is-primary' : ''), label);
    b.type = 'button';
    b.addEventListener('click', onClick);
    return b;
  }

  function linkButton(label, onClick, title, ariaLabel) {
    const b = n('button', 'ops-link-button', label);
    b.type = 'button';
    if (title) b.title = title;
    if (ariaLabel || title) b.setAttribute('aria-label', ariaLabel || title);
    b.addEventListener('click', onClick);
    return b;
  }

  let activeOverlay = null;
  let overlayKeyHandler = null;

  function closeOverlay() {
    if (!activeOverlay) return;
    const trigger = activeOverlay.trigger;
    for (const [key, chart] of charts.entries()) {
      if (chart && chart.canvas && activeOverlay.root.contains(chart.canvas)) {
        chart.destroy();
        charts.delete(key);
      }
    }
    activeOverlay.root.remove();
    activeOverlay = null;
    document.body.classList.remove('ops-overlay-open');
    if (overlayKeyHandler) document.removeEventListener('keydown', overlayKeyHandler);
    overlayKeyHandler = null;
    setQueryValue('detail', null);
    if (trigger && trigger.focus) trigger.focus();
  }

  function openOverlay(title, subtitle, content, wide, detailKey) {
    closeOverlay();
    const trigger = document.activeElement;
    const root = n('div', 'ops-overlay ops-detail-overlay');
    const shell = n('section', 'ops-overlay-panel ops-detail-drawer' + (wide ? ' is-wide' : ''));
    shell.setAttribute('role', 'dialog');
    shell.setAttribute('aria-modal', 'true');
    const titleId = 'ops-dialog-title-' + Date.now();
    shell.setAttribute('aria-labelledby', titleId);

    const header = n('header', 'ops-overlay-header');
    const heading = n('div', 'ops-overlay-heading');
    const headingText = n('h2', 'ops-overlay-title', title);
    headingText.id = titleId;
    heading.append(headingText);
    if (subtitle) heading.append(n('p', 'ops-overlay-subtitle', subtitle));
    const close = n('button', 'ops-overlay-close', '\u00d7');
    close.type = 'button';
    close.setAttribute('aria-label', 'Close dialog');
    close.addEventListener('click', closeOverlay);
    add(header, [heading, close]);

    const body = n('div', 'ops-overlay-body ops-page');
    body.append(content);
    add(shell, [header, body]);
    root.append(shell);
    root.addEventListener('click', function (event) {
      if (event.target === root) closeOverlay();
    });
    overlayKeyHandler = function (event) {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeOverlay();
        return;
      }
      if (event.key !== 'Tab' || !activeOverlay) return;
      const focusable = Array.from(shell.querySelectorAll('a[href], button:not([disabled]), input, select, textarea, [tabindex]:not([tabindex="-1"])'));
      if (!focusable.length) return;
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', overlayKeyHandler);
    document.body.append(root);
    document.body.classList.add('ops-overlay-open');
    activeOverlay = {root: root, trigger: trigger};
    setQueryValue('detail', detailKey || title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, ''));
    close.focus();
  }

  function detailFields(fields) {
    const list = n('dl', 'ops-detail-fields');
    (fields || []).forEach(function (field) {
      if (field.value === null || field.value === undefined || field.value === '') return;
      const item = n('div', 'ops-detail-field');
      add(item, [n('dt', '', field.label), n('dd', '', field.value)]);
      list.append(item);
    });
    return list;
  }

  function sourceActions(sources) {
    const actions = n('div', 'ops-source-actions');
    (sources || []).filter(function (source) { return source && source.url; }).forEach(function (source) {
      actions.append(externalLink(source.label || 'Open source', source.url, 'ops-button'));
    });
    return actions;
  }

  function openDetailDrawer(config) {
    const content = n('div', 'ops-detail-content');
    if (config.description) content.append(n('p', 'ops-detail-description', config.description));
    if (config.fields && config.fields.length) content.append(detailFields(config.fields));
    if (config.sources && config.sources.some(function (source) { return source && source.url; })) content.append(sourceActions(config.sources));
    if (config.content) content.append(config.content);
    openOverlay(config.title || 'Evidence', config.subtitle || 'Source evidence and retained history', content, config.wide !== false, config.id);
  }

  function openMetricDetail(item) {
    openDetailDrawer({
      id: item.id || item.label,
      title: item.label,
      subtitle: item.scope || 'Operational aggregate',
      description: item.description || item.meta || 'This value is derived from the currently loaded dashboard snapshot.',
      fields: [
        {label: 'Value', value: value(item.value)},
        {label: 'Observed', value: item.observed ? shortDate(item.observed) : null},
        {label: 'Window', value: item.window},
        {label: 'Provenance', value: item.provenance},
      ],
      sources: item.sources || (item.url ? [{label: 'Open source', url: item.url}] : []),
      content: item.content || null,
    });
  }

  function segmented(items, current, onChange, ariaLabel) {
    const wrap = n('div', 'ops-segmented');
    wrap.setAttribute('role', 'group');
    wrap.setAttribute('aria-label', ariaLabel || 'View options');
    for (const item of items) {
      const b = n('button', 'ops-segment' + (item.id === current ? ' is-active' : ''), item.label);
      b.type = 'button';
      b.setAttribute('aria-pressed', item.id === current ? 'true' : 'false');
      b.addEventListener('click', function () { onChange(item.id); });
      wrap.append(b);
    }
    return wrap;
  }

  function pageHeader(title, description, ts, actions) {
    const header = n('header', 'ops-page-header');
    const heading = n('div', 'ops-page-heading');
    add(heading, [n('div', 'ops-eyebrow', 'AMD CI OPERATIONS'), n('h1', 'ops-page-title', title)]);
    if (description) heading.append(n('p', 'ops-page-description', description));
    if (ts) heading.append(n('div', 'ops-panel-meta', 'Observed ' + shortDate(ts) + ' - ' + age(ts)));
    header.append(heading);
    if (actions) add(header, n('div', 'ops-page-actions')).lastChild.append(actions);
    return header;
  }

  function statusStrip(items) {
    const strip = n('section', 'ops-status-strip ops-linked-metrics');
    strip.setAttribute('aria-label', 'Operational summary');
    for (const item of items) {
      let cell;
      if (item.url) {
        cell = externalLink('', item.url, 'ops-status-item ops-linked-metric ' + (item.tone || ''));
      } else {
        cell = n('button', 'ops-status-item ops-linked-metric ' + (item.tone || ''));
        cell.type = 'button';
        cell.addEventListener('click', item.onOpen || function () { openMetricDetail(item); });
      }
      cell.setAttribute('aria-label', item.label + ': ' + value(item.value) + '. ' + (item.meta || 'Inspect evidence'));
      add(cell, [
        n('div', 'ops-stat-label', item.label),
        n('div', 'ops-stat-value', value(item.value)),
        item.meta ? n('div', 'ops-stat-meta', item.meta) : null,
      ]);
      strip.append(cell);
    }
    return strip;
  }

  function panel(title, meta, children, cls) {
    const root = n('section', 'ops-panel ' + (cls || ''));
    const head = n('div', 'ops-panel-header');
    add(head, [n('h2', 'ops-panel-title', title), meta ? n('div', 'ops-panel-meta', meta) : null]);
    const body = n('div', 'ops-panel-body');
    add(body, children || []);
    add(root, [head, body]);
    return root;
  }

  function progress(label, current, total, tone) {
    const row = n('div', 'ops-progress-row');
    const top = n('div', 'ops-inline-actions');
    add(top, [n('span', '', label), n('strong', 'ops-progress-value', integer(current) + ' / ' + integer(total))]);
    const track = n('div', 'ops-progress');
    const bar = n('div', 'ops-progress-bar ' + (tone || ''));
    bar.style.width = total ? Math.min(100, Number(current || 0) / Number(total) * 100) + '%' : '0';
    track.append(bar);
    add(row, [top, track]);
    return row;
  }

  function cellContent(content) {
    if (content === null || content === undefined) return document.createTextNode('-');
    return content.nodeType ? content : document.createTextNode(String(content));
  }

  function dataTable(columns, rows, caption, options) {
    const geometry = options || {};
    const wrap = n('div', 'ops-table-wrap');
    if (!rows.length) {
      wrap.classList.add('is-empty');
      wrap.append(n('div', 'ops-empty', caption ? caption + '. No matching observations.' : 'No matching observations.'));
      return wrap;
    }
    const scroll = n('div', 'ops-table-scroll');
    const table = n('table', 'ops-table ' + (columns.length <= 4 ? 'is-compact' : columns.length >= 7 ? 'is-wide' : 'is-standard'));
    table.dataset.columnCount = String(columns.length);
    table.dataset.geometry = geometry.name || 'automatic';
    const columnWidths = columns.map(function (column) {
      return column.width || (column.sticky ? '280px' : column.numeric ? '110px' : '160px');
    });
    const automaticMinWidth = columnWidths.reduce(function (sum, width) {
      const match = String(width).match(/^(\d+(?:\.\d+)?)px$/);
      return sum + (match ? Number(match[1]) : 0);
    }, 0);
    table.style.setProperty('--ops-table-min-width', geometry.minWidth || Math.max(320, automaticMinWidth) + 'px');
    if (caption) table.append(n('caption', 'ops-table-caption', caption));
    table.classList.add('has-column-geometry');
    const colgroup = n('colgroup');
    columnWidths.forEach(function (width) {
      const col = n('col');
      col.style.width = width;
      colgroup.append(col);
    });
    table.append(colgroup);
    const thead = n('thead');
    const hr = n('tr');
    for (const col of columns) {
      const alignment = col.numeric ? 'numeric' : col.align || 'text';
      const th = n('th', (alignment === 'numeric' ? 'is-numeric ' : alignment === 'center' ? 'is-center ' : '') + (col.sticky ? 'is-sticky-left' : ''), col.label);
      th.scope = 'col';
      th.dataset.align = alignment;
      hr.append(th);
    }
    thead.append(hr);
    const tbody = n('tbody');
    for (const row of rows) {
      const tr = n('tr');
      if (row && row._rowTone) tr.classList.add(row._rowTone);
      for (const col of columns) {
        const alignment = col.numeric ? 'numeric' : col.align || 'text';
        const td = n('td', (alignment === 'numeric' ? 'is-numeric ' : alignment === 'center' ? 'is-center ' : '') + (col.sticky ? 'is-sticky-left ' : '') + (col.className || ''));
        td.dataset.align = alignment;
        const result = col.render ? col.render(row) : row[col.key];
        td.append(cellContent(result));
        tr.append(td);
      }
      tbody.append(tr);
    }
    add(table, [thead, tbody]);
    scroll.append(table);
    wrap.append(scroll);
    return wrap;
  }

  function linkedBadge(label, url, onOpen, tone) {
    let control;
    if (url) {
      control = externalLink('', url, 'ops-result-link');
    } else {
      control = n('button', 'ops-result-link');
      control.type = 'button';
      control.addEventListener('click', onOpen || function () {
        openMetricDetail({label: 'Result', value: label, meta: 'No exact external source is present in this snapshot.'});
      });
    }
    control.append(badge(label, tone));
    control.setAttribute('aria-label', 'Inspect result: ' + label);
    return control;
  }

  function historyPointLabel(point) {
    return point.label || point.name || (point.timestamp ? shortDate(point.timestamp) : 'Observation');
  }

  function historyPointSources(point, fallbackAsset) {
    const rows = [];
    if (point.url) rows.push({label: 'Open exact source', url: point.url});
    (point.sources || []).forEach(function (source) { if (source && source.url) rows.push(source); });
    if (!rows.length) rows.push({label: 'Open published source data', url: point.sourceAsset || fallbackAsset || SOURCE_ASSETS.operations});
    const seen = new Set();
    return rows.filter(function (source) {
      if (seen.has(source.url)) return false;
      seen.add(source.url);
      return true;
    });
  }

  function inspectHistoryPoint(point, fallbackAsset) {
    if (typeof point.onOpen === 'function') {
      point.onOpen();
      return;
    }
    openDetailDrawer({
      id: point.id || historyPointLabel(point),
      title: historyPointLabel(point),
      subtitle: point.scope || 'Historical observation',
      fields: Object.entries(point.details || {}).map(function (entry) { return {label: entry[0].replace(/_/g, ' '), value: entry[1]}; }),
      sources: historyPointSources(point, fallbackAsset),
    });
  }

  function openHistoryEvidence(title, points, subtitle, fallbackAsset) {
    const rows = (points || []).slice().reverse();
    const content = n('div', 'ops-evidence');
    const publishedSources = [];
    const publishedUrls = new Set();
    rows.forEach(function (point) {
      (point.sources || []).filter(function (source) { return source && source.url && /published|source data|history/i.test(source.label || ''); }).forEach(function (source) {
        if (!publishedUrls.has(source.url)) { publishedUrls.add(source.url); publishedSources.push(source); }
      });
    });
    if (rows.some(function (point) { return !point.url; })) {
      const asset = fallbackAsset || (rows.find(function (point) { return point.sourceAsset; }) || {}).sourceAsset || SOURCE_ASSETS.operations;
      if (asset && !publishedUrls.has(asset)) publishedSources.push({label: 'Open published source data', url: asset});
    }
    if (publishedSources.length) content.append(sourceActions(publishedSources));
    content.append(dataTable([
      {label: 'Observation', sticky: true, render: function (point) {
        if (point.url) return externalLink(historyPointLabel(point), point.url, 'ops-mono');
        return linkButton(historyPointLabel(point), function () { inspectHistoryPoint(point, fallbackAsset); }, 'Inspect ' + historyPointLabel(point) + ' history evidence');
      }},
      {label: 'Observed', render: function (point) { return shortDate(point.timestamp || point.ts || point.date); }},
      {label: 'Value', render: function (point) { return value(point.valueSummary || point.value); }},
      {label: 'Evidence', render: function (point) {
        if (point.url) return externalLink('Open source', point.url);
        const sources = historyPointSources(point, fallbackAsset);
        if (typeof point.onOpen !== 'function' && sources.length === 1) return externalLink('Open source data', sources[0].url);
        return linkButton('Inspect', function () { inspectHistoryPoint(point, fallbackAsset); }, 'Inspect source evidence for ' + historyPointLabel(point));
      }},
    ], rows, integer(rows.length) + ' retained observations'));
    openOverlay(title, subtitle || 'Select an observation to inspect its exact evidence', content, true, 'history-' + title);
  }

  function evidenceObservations(candidate) {
    const rows = candidate.observations || candidate.evidence || candidate.runs_evidence || [];
    return rows.slice().sort(function (a, b) {
      const buildDelta = Number(b.build_number || 0) - Number(a.build_number || 0);
      if (buildDelta) return buildDelta;
      return String(a.queue || '').localeCompare(String(b.queue || ''));
    });
  }

  function observationState(observation) {
    return String(observation.state || observation.result || observation.status || 'unknown').toLowerCase();
  }

  function isIncidentObservation(observation) {
    return ['hard', 'soft', 'failed', 'failing', 'soft_fail', 'soft_failed', 'timed_out', 'broken', 'canceled', 'expired']
      .includes(observationState(observation));
  }

  function isNightlyObservation(observation) {
    return String(observation.build_kind || '').toLowerCase() === 'nightly'
      || /\bnightly\b/i.test(String(observation.message || observation.build_message || ''));
  }

  function observationDurationMinutes(observation) {
    const raw = observation.duration_mins !== undefined ? observation.duration_mins
      : observation.wall_duration_mins !== undefined ? observation.wall_duration_mins
        : observation.duration_min !== undefined ? observation.duration_min : observation.dur;
    return Number.isFinite(Number(raw)) ? Number(raw) : null;
  }

  function observationWaitMinutes(observation) {
    const raw = observation.wait_mins !== undefined ? observation.wait_mins : observation.wait_min;
    return Number.isFinite(Number(raw)) ? Number(raw) : null;
  }

  function observationOutcomeValue(observation) {
    const stateName = observationState(observation);
    if (stateName === 'passed') return 2;
    if (['soft', 'soft_fail', 'soft_failed'].includes(stateName)) return 1;
    if (isIncidentObservation(observation)) return 0;
    return null;
  }

  function observationHistoryPoint(observation, sourcePipeline) {
    const stateName = observationState(observation);
    const completion = observationDurationMinutes(observation);
    const wait = observationWaitMinutes(observation);
    return {
      id: observation.job_id || sourcePipeline + '-' + value(observation.build_number),
      label: observation.build_number ? '#' + observation.build_number : shortDate(observationTimestamp(observation)),
      timestamp: observationTimestamp(observation),
      url: exactPipelineEvidenceUrl(observation, sourcePipeline),
      valueSummary: stateName + (completion !== null ? ' - ' + duration(completion) : ''),
      details: {
        result: stateName,
        build_kind: observation.build_kind || 'main',
        queue: observation.queue,
        completion: completion !== null ? duration(completion) : '-',
        queue_wait: wait !== null ? duration(wait) : '-',
      },
    };
  }

  function evidenceSummaryItem(label, metric, tone) {
    const item = n('div', 'ops-evidence-stat ' + (tone || ''));
    add(item, [n('div', 'ops-stat-label', label), n('div', 'ops-stat-value', metric)]);
    return item;
  }

  function openMixedOutcomeEvidence(candidate) {
    const sourcePipeline = candidate.source_pipeline || 'ci';
    const allObservations = evidenceObservations(candidate).filter(function (row) {
      return (!row.source_pipeline || row.source_pipeline === sourcePipeline)
        && Boolean(exactPipelineEvidenceUrl(row, sourcePipeline));
    }).sort(function (a, b) {
      return new Date(observationTimestamp(a) || 0) - new Date(observationTimestamp(b) || 0);
    });
    const scope = candidate.scope_label || candidate.scope || 'retained reliability window';
    const content = n('div', 'ops-evidence');
    const notice = n('div', 'ops-evidence-note is-info');
    const hasPassing = allObservations.some(function (row) { return observationState(row) === 'passed'; });
    const hasIncidents = allObservations.some(isIncidentObservation);
    add(notice, [
      n('strong', '', hasPassing && hasIncidents ? 'Classification: mixed-outcome candidate. ' : 'Historical group evidence. '),
      n('span', '', 'Outcome, completion, and queue-wait history below use exact ' + sourcePipeline + ' observations. Any incident rate shown is not a test-case flake probability.'),
    ]);
    content.append(notice);

    if (!allObservations.length) {
      content.append(n('div', 'ops-empty', 'The aggregate is available, but this snapshot predates per-run evidence. Regenerate operations_v2.json to populate exact links.'));
      openOverlay(candidate.name || 'Group evidence', scope + ' evidence', content, true, 'group-' + (candidate.id || candidate.name));
      return;
    }

    let historyMode = 'main';
    const scopeControlHost = n('div');
    const scopeToolbar = n('div', 'ops-toolbar ops-evidence-toolbar');
    scopeToolbar.append(scopeControlHost);
    scopeToolbar.append(n('span', 'ops-panel-meta', 'Choose the complete branch=main cohort or its nightly subset.'));
    content.append(scopeToolbar);
    const historyHost = n('div', 'ops-stack');
    content.append(historyHost);

    function selectHistoryMode(nextMode) {
      historyMode = nextMode;
      clear(scopeControlHost);
      scopeControlHost.append(segmented([
        {id: 'main', label: 'All main'},
        {id: 'nightly', label: 'Nightly only'},
      ], historyMode, selectHistoryMode, 'Test-group history cohort'));
      renderHistory();
    }

    function renderHistory() {
      const observations = allObservations.filter(function (row) {
        return historyMode === 'main' || isNightlyObservation(row);
      });
      clear(historyHost);
      if (!observations.length) {
        historyHost.append(n('div', 'ops-empty', 'No exact nightly observations are retained for this strict test-group variant.'));
        return;
      }

      const passed = observations.filter(function (row) { return observationState(row) === 'passed'; }).length;
      const soft = observations.filter(function (row) { return ['soft', 'soft_fail', 'soft_failed'].includes(observationState(row)); }).length;
      const incidents = observations.filter(isIncidentObservation);
      const hard = Math.max(0, incidents.length - soft);
      const summary = n('div', 'ops-evidence-summary');
      add(summary, [
        evidenceSummaryItem(historyMode === 'main' ? 'MAIN OBSERVATIONS' : 'NIGHTLY OBSERVATIONS', integer(observations.length)),
        evidenceSummaryItem('PASSED', integer(passed), 'is-success'),
        evidenceSummaryItem('HARD INCIDENTS', integer(hard), hard ? 'is-danger' : ''),
        evidenceSummaryItem('SOFT INCIDENTS', integer(soft), soft ? 'is-warning' : ''),
        evidenceSummaryItem('INCIDENT RATE', percent(incidents.length, observations.length), incidents.length ? 'is-warning' : 'is-success'),
      ]);
      historyHost.append(summary);

      const labels = observations.map(function (row) { return row.build_number ? '#' + row.build_number : shortDate(observationTimestamp(row)); });
      const evidence = observations.map(function (row) { return observationHistoryPoint(row, sourcePipeline); });
      const pointColors = observations.map(function (row) {
        const outcome = observationOutcomeValue(row);
        return outcome === 2 ? '#35bb78' : outcome === 1 ? '#e3a63a' : '#e06464';
      });
      const chartGrid = n('div', 'ops-grid ops-grid-2');
      const chartKey = 'group-' + String(candidate.id || candidate.name || 'history').replace(/[^a-z0-9]+/gi, '-').toLowerCase() + '-' + historyMode;
      const outcomeChart = chartPanel('Outcome history', integer(observations.length) + ' exact ' + (historyMode === 'main' ? 'main' : 'nightly') + ' observations', chartKey + '-outcome');
      chartGrid.append(outcomeChart.root);
      const hasDuration = observations.some(function (row) { return observationDurationMinutes(row) !== null || observationWaitMinutes(row) !== null; });
      let durationChart = null;
      if (hasDuration) {
        durationChart = chartPanel('Completion and queue wait', 'Minutes per exact Buildkite job observation', chartKey + '-duration');
        chartGrid.append(durationChart.root);
      }
      historyHost.append(chartGrid);

      const filterToolbar = n('div', 'ops-toolbar ops-evidence-toolbar');
      const search = n('input', 'ops-input');
      search.type = 'search';
      search.placeholder = 'Filter build, queue, or result';
      search.setAttribute('aria-label', 'Filter reliability observations');
      const resultFilter = n('select', 'ops-select');
      resultFilter.setAttribute('aria-label', 'Filter observations by result');
      [['all', 'All results'], ['passing', 'Passing only'], ['incident', 'Incidents only']].forEach(function (pair) {
        const option = n('option', '', pair[1]);
        option.value = pair[0];
        resultFilter.append(option);
      });
      add(filterToolbar, [search, resultFilter]);
      historyHost.append(filterToolbar);
      const tableHost = n('div', 'ops-evidence-table-host');
      historyHost.append(tableHost);

      function renderEvidenceRows() {
        const query = search.value.trim().toLowerCase();
        const mode = resultFilter.value;
        const filtered = observations.slice().reverse().filter(function (row) {
          const incident = isIncidentObservation(row);
          if (mode === 'passing' && observationState(row) !== 'passed') return false;
          if (mode === 'incident' && !incident) return false;
          if (!query) return true;
          return [row.build_number, row.queue, row.raw_name, row.name, observationState(row)]
            .some(function (part) { return String(part || '').toLowerCase().includes(query); });
        });
        clear(tableHost);
        tableHost.append(dataTable([
          {label: 'Build', sticky: true, width: '110px', render: function (row) {
            const label = row.build_number ? '#' + row.build_number : 'Build';
            return externalLink(label, exactPipelineEvidenceUrl(row, sourcePipeline), 'ops-mono');
          }},
          {label: 'Cohort', width: '100px', render: function (row) { return badge(isNightlyObservation(row) ? 'nightly' : 'main', isNightlyObservation(row) ? 'is-info' : 'is-neutral'); }},
          {label: 'Observed', width: '170px', render: function (row) { return shortDate(observationTimestamp(row)); }},
          {label: 'Result', width: '120px', render: function (row) {
            const stateName = observationState(row);
            return linkedBadge(stateName === 'soft' ? 'soft fail' : stateName === 'hard' ? 'hard fail' : stateName, exactPipelineEvidenceUrl(row, sourcePipeline));
          }},
          {label: 'Variant', width: '250px', render: function (row) {
            const parts = [row.variant_hardware, (row.variant_queues || []).join(', '), row.variant_id ? 'id ' + row.variant_id : null].filter(Boolean);
            return n('span', 'ops-mono', parts.join(' - ') || value(row.group_id));
          }},
          {label: 'Queue', width: '160px', render: function (row) { return n('span', 'ops-mono', value(row.queue)); }},
          {label: 'Completion', numeric: true, width: '120px', render: function (row) { return duration(observationDurationMinutes(row)); }},
          {label: 'Queue wait', numeric: true, width: '110px', render: function (row) { return duration(observationWaitMinutes(row)); }},
          {label: 'Retry evidence', width: '150px', render: function (row) {
            const retry = row.retry_evidence || row;
            const retries = Number(retry.retries_count || 0);
            if (retry.retried || retry.retried_in_job_id || retries) return linkedBadge(retries ? retries + ' retries' : 'retried', exactPipelineEvidenceUrl(row, sourcePipeline), null, 'is-info');
            return n('span', 'ops-cell-muted', '-');
          }},
          {label: 'Job evidence', width: '130px', render: function (row) { return externalLink('Open log', exactPipelineEvidenceUrl(row, sourcePipeline)); }},
        ], filtered, integer(filtered.length) + ' of ' + integer(observations.length) + ' retained ' + (historyMode === 'main' ? 'main' : 'nightly') + ' observations', {name: 'mixed-evidence', minWidth: '1420px'}));
      }

      search.addEventListener('input', renderEvidenceRows);
      resultFilter.addEventListener('change', renderEvidenceRows);
      renderEvidenceRows();
      historyHost.append(n('p', 'ops-evidence-method', 'Method: outcomes combine Buildkite job state with parsed test-result summaries. Completion is job wall time and queue wait is shown only when the collector retained it. Retry badges require explicit Buildkite retry metadata; mixed outcomes alone are not labeled as confirmed flakes.'));

      requestAnimationFrame(function () {
        drawChart(chartKey + '-outcome', outcomeChart.canvas, {
          type: 'line',
          data: {labels: labels, datasets: [{label: 'Outcome', data: observations.map(observationOutcomeValue), showLine: false, pointRadius: 4, pointHoverRadius: 6, pointBackgroundColor: pointColors, pointBorderColor: pointColors}]},
          options: {scales: {x: {grid: {display: false}, ticks: {maxTicksLimit: 8}}, y: {min: 0, max: 2, ticks: {stepSize: 1, callback: function (tick) { return tick === 2 ? 'Passed' : tick === 1 ? 'Soft' : tick === 0 ? 'Hard' : ''; }}}}},
          evidenceTitle: (candidate.name || 'Test group') + ' outcome history',
          evidence: evidence,
        });
        if (durationChart) {
          const datasets = [{label: 'Completion', data: observations.map(observationDurationMinutes), borderColor: '#22b8ad', backgroundColor: '#22b8ad', pointRadius: 2, borderWidth: 2, spanGaps: false}];
          if (observations.some(function (row) { return observationWaitMinutes(row) !== null; })) datasets.push({label: 'Queue wait', data: observations.map(observationWaitMinutes), borderColor: '#e3a63a', backgroundColor: '#e3a63a', pointRadius: 2, borderWidth: 1.5, spanGaps: false});
          drawChart(chartKey + '-duration', durationChart.canvas, {
            type: 'line',
            data: {labels: labels, datasets: datasets},
            options: {scales: {x: {grid: {display: false}, ticks: {maxTicksLimit: 8}}, y: {beginAtZero: true, title: {display: true, text: 'Minutes'}}}},
            evidenceTitle: (candidate.name || 'Test group') + ' completion and queue-wait history',
            evidence: evidence,
          });
        }
      });
    }

    openOverlay(candidate.name || 'Group evidence', 'Historical outcomes, latency, and exact Buildkite evidence', content, true, 'group-' + (candidate.id || candidate.name));
    selectHistoryMode('main');
  }

  function candidateNameCell(candidate) {
    return linkButton(candidate.name || 'Unnamed group', function () { openMixedOutcomeEvidence(candidate); }, 'Inspect all contributing Buildkite runs');
  }

  function candidateEvidenceCell(candidate) {
    const count = evidenceObservations(candidate).length || Number(candidate.runs || 0);
    return linkButton(integer(count) + ' runs', function () { openMixedOutcomeEvidence(candidate); }, 'Open per-run evidence');
  }

  function cssVar(name, fallback) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
  }

  function perfValue(raw, unit) {
    const number = Number(raw);
    if (!Number.isFinite(number)) return '-';
    if (unit === 'ratio') return (number * 100).toFixed(1) + '%';
    if (unit === 's') return number < 1 ? Math.round(number * 1000) + ' ms' : number.toFixed(2) + ' s';
    let rendered;
    if (Math.abs(number) >= 1000) rendered = Math.round(number).toLocaleString();
    else if (Math.abs(number) >= 1) rendered = number.toFixed(1);
    else rendered = number.toFixed(3);
    return unit ? rendered + ' ' + unit : rendered;
  }

  function perfDelta(block) {
    if (block.previous === null || block.previous === undefined) return n('span', 'ops-perf-delta is-neutral', 'First nightly');
    const delta = Number(block.delta_pct);
    if (!Number.isFinite(delta)) return n('span', 'ops-perf-delta is-neutral', 'No comparison');
    const label = (delta > 0 ? '+' : '') + delta.toFixed(1) + '%';
    const tone = block.status === 'good' ? 'is-success' : block.status === 'bad' ? 'is-danger' : 'is-neutral';
    const node = n('span', 'ops-perf-delta ' + tone, label);
    node.title = 'Change from the preceding nightly';
    return node;
  }

  function drawPerfSpark(key, canvas, series, status, unit) {
    const rows = (series || []).filter(function (point) { return Number.isFinite(Number(point.value)); });
    if (rows.length < 2) return;
    const color = status === 'good' ? cssVar('--ops-success', '#35bb78')
      : status === 'bad' ? cssVar('--ops-danger', '#e06464')
        : cssVar('--ops-neutral', '#8a969a');
    drawChart(key, canvas, {
      type: 'line',
      data: {
        labels: rows.map(function (point) { return String(point.vllm_commit || '').slice(0, 7) || shortDate(point.date); }),
        datasets: [{data: rows.map(function (point) { return Number(point.value); }), borderColor: color, backgroundColor: color, borderWidth: 2, pointRadius: 0, pointHoverRadius: 3, tension: 0.22, fill: false}],
      },
      options: {
        animation: false,
        interaction: {intersect: false, mode: 'index'},
        plugins: {
          legend: {display: false},
          tooltip: {callbacks: {label: function (item) { return perfValue(item.parsed.y, unit); }}},
        },
        scales: {x: {display: false}, y: {display: false}},
      },
      evidenceTitle: 'Performance metric history',
      evidenceAsset: SOURCE_ASSETS.perf,
      evidenceAction: false,
      evidence: rows.map(function (point) {
        return {
          id: 'perf-' + value(point.build_number) + '-' + value(point.date),
          label: point.build_number ? 'Build #' + point.build_number : shortDate(point.date),
          timestamp: point.date,
          valueSummary: perfValue(point.value, unit),
          url: point.build_url,
          details: {value: perfValue(point.value, unit), vllm_commit: point.vllm_commit, image: point.image},
        };
      }),
    });
  }

  function reliabilitySourcePipeline(reliability) {
    const cohort = reliability.cohort || {};
    const provenanceCohort = ((cohort.provenance || {}).cohort) || {};
    return reliability.source_pipeline || cohort.pipeline || provenanceCohort.pipeline || '';
  }

  function reliabilityPayload(entry) {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) return null;
    return entry.reliability && typeof entry.reliability === 'object' ? entry.reliability : entry;
  }

  function reliabilityForPipeline(ops, pipeline) {
    const root = (ops || {}).reliability || {};
    const maps = [
      (ops || {}).reliability_by_pipeline,
      root.by_pipeline,
      root.byPipeline,
      root,
    ];
    for (const map of maps) {
      const payload = reliabilityPayload(map && map[pipeline]);
      if (payload) return payload;
    }
    const lists = [(ops || {}).reliability_pipelines, root.pipelines];
    for (const list of lists) {
      if (!Array.isArray(list)) continue;
      const entry = list.find(function (item) {
        return item && (item.pipeline === pipeline || item.source_pipeline === pipeline);
      });
      const payload = reliabilityPayload(entry);
      if (payload) return payload;
    }
    const aliases = pipeline === 'ci'
      ? [(ops || {}).upstream_reliability, root.upstream, root.canonical]
      : [];
    for (const alias of aliases) {
      const payload = reliabilityPayload(alias);
      if (payload) return payload;
    }
    return reliabilitySourcePipeline(root) === pipeline ? root : {};
  }

  function canonicalReliability(ops) {
    return reliabilityForPipeline(ops, 'ci');
  }

  function exactReliabilityBuildUrl(row) {
    return exactPipelineBuildUrl(row, 'ci');
  }

  function reliabilityScopeInfo(reliability) {
    reliability = reliability && typeof reliability === 'object' ? reliability : {};
    const explicit = reliability.scope || reliability.observation_scope || reliability.denominator_scope || '';
    const text = JSON.stringify([explicit, reliability.cohort, reliability.denominator, reliability.source_pipeline, reliability.evidence_definitions]).toLowerCase();
    const available = reliability.available === true
      && reliability.source_pipeline === 'ci'
      && ((reliability.cohort || {}).available === true);
    const allMain = available && /all[-_ ]main|origin\/main|branch.?main/.test(text) && !/nightly job runs|night(?:ly|lies)[-_ ]only/.test(text);
    const pipeline = reliabilitySourcePipeline(reliability);
    const source = pipeline === 'ci' ? 'upstream CI' : pipeline === 'amd-ci' ? 'AMD CI' : 'selected CI';
    return {
      allMain: allMain,
      available: available,
      pipeline: pipeline,
      label: allMain ? 'All-main reliability - ' + source : available ? 'Retained nightly reliability - ' + source : 'Upstream reliability unavailable',
      detail: allMain ? 'All completed ' + source + ' branch=main builds in the retained window' : available ? 'Current payload contains nightly-only reliability; the all-main ledger has not landed yet' : 'The strict upstream main cohort is absent; nightly data was not substituted',
    };
  }

  function reliabilityCohortSummary(reliability) {
    const cohortBlock = reliability.cohort || {};
    const provenanceCohort = ((cohortBlock.provenance || {}).cohort) || {};
    const cohort = Object.assign({}, cohortBlock, provenanceCohort);
    const retries = ((reliability.retry_analysis || {}).summary) || {};
    const total = Number(cohort.build_count !== undefined ? cohort.build_count : (reliability.denominator || {}).builds !== undefined ? (reliability.denominator || {}).builds : retries.builds_evaluated);
    const nightlies = Number(cohort.canonical_nightly_build_count);
    const otherMain = Number(cohort.non_nightly_main_build_count);
    return {
      total: Number.isFinite(total) ? total : null,
      nightlies: Number.isFinite(nightlies) ? nightlies : null,
      otherMain: Number.isFinite(otherMain) ? otherMain : null,
      observedFrom: cohort.observed_from,
      observedTo: cohort.observed_to,
      selection: cohort.selection,
    };
  }

  function reliabilityCatalog(reliability) {
    if (Array.isArray(reliability.group_catalog)) {
      return reliability.group_catalog.map(function (row) { return Object.assign({}, row); });
    }
    const candidates = [];
    ['groups', 'test_groups', 'flaky_candidates'].forEach(function (key) {
      const value = reliability[key];
      if (Array.isArray(value)) candidates.push.apply(candidates, value);
      else if (value && typeof value === 'object') candidates.push.apply(candidates, Object.values(value));
    });
    const rankings = reliability.latency_rankings || {};
    ['by_p90_duration', 'by_median_duration', 'by_failure_rate'].forEach(function (key) {
      if (Array.isArray(rankings[key])) candidates.push.apply(candidates, rankings[key]);
    });
    const byIdentity = new Map();
    candidates.forEach(function (row) {
      const name = row.name || row.label || row.group || row.group_name;
      if (!name) return;
      const variant = [row.id || row.evidence_ref, name, row.hardware || row.hw, (row.queues || []).join('|'), row.queue, row.shard, row.step_key].filter(Boolean).join('::');
      const key = row.id || row.evidence_ref ? 'id:' + String(row.id || row.evidence_ref) : 'variant:' + normalizeLabel(variant);
      byIdentity.set(key, Object.assign({}, byIdentity.get(key) || {}, row, {name: name}));
    });
    return Array.from(byIdentity.values());
  }

  function groupReliability(reliability, name) {
    const key = normalizeLabel(name);
    const matches = reliabilityCatalog(reliability).filter(function (row) { return normalizeLabel(row.name) === key; });
    return matches.length === 1 ? matches[0] : null;
  }

  function groupReliabilityByRef(reliability, reference, name) {
    const rows = reliabilityCatalog(reliability);
    if (reference !== null && reference !== undefined && reference !== '') {
      const byId = rows.find(function (row) { return String(row.id) === String(reference); });
      return byId || null;
    }
    return groupReliability(reliability, name);
  }

  function groupReliabilityRowsByIds(reliability, references, fallbackName) {
    const ids = (references || []).filter(function (id) { return id !== null && id !== undefined && id !== ''; }).map(String);
    if (ids.length) {
      const wanted = new Set(ids);
      return reliabilityCatalog(reliability).filter(function (row) { return wanted.has(String(row.id)); });
    }
    const fallback = groupReliability(reliability, fallbackName);
    return fallback ? [fallback] : [];
  }

  function combinedGatingReliability(group, reliability) {
    const main = group.main_reliability || {};
    const ids = (main.group_ids || []).length ? main.group_ids : (main.variants || []).map(function (variant) { return variant.id; });
    if (!ids.length && main.id) ids.push(main.id);
    const variants = groupReliabilityRowsByIds(reliability, ids, group.label || group.name);
    const observations = [];
    variants.forEach(function (variant) {
      evidenceObservations(variant).forEach(function (observation) {
        observations.push(Object.assign({}, observation, {
          variant_id: variant.id,
          variant_hardware: variant.hardware || variant.hw,
          variant_queues: variant.queues || (variant.queue ? [variant.queue] : []),
        }));
      });
    });
    if (!variants.length && !observations.length) return null;
    return {
      id: 'gating-' + (group.id || group.label || group.name),
      name: group.label || group.name,
      hardware: variants.length === 1 ? variants[0].hardware : 'multiple variants',
      queues: variants.reduce(function (all, variant) { return all.concat(variant.queues || []); }, []).filter(function (queue, index, all) { return all.indexOf(queue) === index; }),
      runs: main.runs !== undefined ? main.runs : observations.length,
      passed: main.passed !== undefined ? main.passed : observations.filter(function (row) { return observationState(row) === 'passed'; }).length,
      failed: main.failed !== undefined ? main.failed : observations.filter(function (row) { return ['hard', 'failed'].includes(observationState(row)); }).length,
      soft_failed: main.soft_failed !== undefined ? main.soft_failed : observations.filter(function (row) { return ['soft', 'soft_fail', 'soft_failed'].includes(observationState(row)); }).length,
      fail_rate: main.incident_rate_pct,
      scope_label: 'all-main reliability across ' + integer(variants.length) + ' strict hardware variants',
      observations: observations,
      variants: variants,
      group_ids: ids,
    };
  }

  function groupVariantMeta(row) {
    const parts = [];
    if (row.hardware || row.hw) parts.push(row.hardware || row.hw);
    if (row.gpu_count) parts.push(row.gpu_count + ' GPUs');
    if (row.shard !== null && row.shard !== undefined && row.shard !== '') parts.push('shard ' + row.shard);
    const queues = row.queues || (row.queue ? [row.queue] : []);
    if (queues.length) parts.push(queues.join(', '));
    if (row.id) parts.push('id ' + row.id);
    return parts.join(' - ');
  }

  function groupIdentityCell(row, onOpen) {
    const cell = n('div', 'ops-entity-cell');
    cell.append(linkButton(row.name || row.label || row.group || 'Unnamed group', onOpen));
    const variant = groupVariantMeta(row);
    if (variant) cell.append(n('span', 'ops-entity-meta ops-mono', variant));
    return cell;
  }

  function compactChartLabel(row, maxLength) {
    const full = (row.name || row.label || row.group || 'Unnamed group') + ' [' + value(row.hardware || row.hw, 'unknown') + ']';
    const limit = maxLength || 46;
    return full.length > limit ? full.slice(0, Math.max(1, limit - 3)) + '...' : full;
  }

  function latestObservation(row) {
    return evidenceObservations(row || {}).slice().sort(function (a, b) {
      return new Date(b.observed_at || b.finished_at || b.created_at || b.date || 0) - new Date(a.observed_at || a.finished_at || a.created_at || a.date || 0);
    })[0] || null;
  }

  function greenStreak(row) {
    const observations = evidenceObservations(row || {}).slice().sort(function (a, b) {
      return new Date(b.observed_at || b.finished_at || b.created_at || b.date || 0) - new Date(a.observed_at || a.finished_at || a.created_at || a.date || 0);
    });
    let streak = 0;
    for (const observation of observations) {
      if (observationState(observation) !== 'passed') break;
      streak += 1;
    }
    return streak;
  }

  function lastIncident(row) {
    return evidenceObservations(row || {}).slice().sort(function (a, b) {
      return new Date(b.observed_at || b.finished_at || b.created_at || b.date || 0) - new Date(a.observed_at || a.finished_at || a.created_at || a.date || 0);
    }).find(isIncidentObservation) || null;
  }

  function openGroupDetail(group, ops, reliabilityRow, sourceReliability) {
    const reliability = sourceReliability || canonicalReliability(ops);
    const reference = (reliabilityRow || {}).evidence_ref || group.evidence_ref || ((group.main_reliability || {}).id);
    const row = groupReliabilityByRef(reliability, reference, group.name || group.label || group.group) || reliabilityRow;
    if (row && evidenceObservations(row).length) {
      const scope = reliabilityScopeInfo(reliability);
      row.scope_label = scope.label.toLowerCase();
      openMixedOutcomeEvidence(row);
      return;
    }
    const name = group.name || group.label || group.group || 'Test group';
    openDetailDrawer({
      id: 'group-' + name,
      title: name,
      subtitle: 'Test-group detail',
      description: 'The aggregate is available, but this snapshot does not include exact per-run observations for this group.',
      fields: [
        {label: 'Area', value: group.area},
        {label: 'Runs', value: group.runs !== undefined ? integer(group.runs) : group.count !== undefined ? integer(group.count) : null},
        {label: 'Median completion', value: group.median_dur !== undefined ? duration(group.median_dur) : group.p50_min !== undefined ? duration(group.p50_min) : null},
        {label: 'p90 completion', value: group.p90_dur !== undefined ? duration(group.p90_dur) : group.p90_min !== undefined ? duration(group.p90_min) : null},
        {label: 'Queues', value: (group.queues || []).join(', ') || group.queue},
      ],
      sources: recordUrl(group) ? [{label: 'Open source evidence', url: recordUrl(group)}] : [],
    });
  }

  function gatingEvidenceUrl(group) {
    const latest = group.latest_amd_result || {};
    const direct = exactPipelineEvidenceUrl(latest, 'amd-ci');
    if (direct) return direct;
    const exact = (latest.evidence || []).map(function (item) {
      return exactPipelineEvidenceUrl(item, 'amd-ci');
    }).find(Boolean);
    return exact || '';
  }

  function openGatingDetail(group, ops) {
    const reliability = canonicalReliability(ops);
    const combined = combinedGatingReliability(group, reliability);
    const variants = (combined || {}).variants || [];
    const latestEvidence = (((group.latest_amd_result || {}).evidence) || []).filter(function (row) {
      return Boolean(exactPipelineEvidenceUrl(row, 'amd-ci'));
    });
    const historyEvidence = (group.evidence || []).filter(function (row) {
      return Boolean(exactPipelineEvidenceUrl(row, 'ci'));
    });
    const content = n('div', 'ops-stack');
    function sourceEvidenceTable(rows, fallbackPipeline, caption) {
      return dataTable([
        {label: 'Evidence', sticky: true, render: function (row) { return externalLink(row.label || row.architecture || row.source || 'Open source', exactPipelineEvidenceUrl(row, fallbackPipeline)); }},
        {label: 'Result', render: function (row) { return linkedBadge(row.state || row.raw_state || 'unknown', exactPipelineEvidenceUrl(row, fallbackPipeline)); }},
        {label: 'Build', render: function (row) { return row.build_number ? externalLink('#' + row.build_number, exactPipelineEvidenceUrl(row, fallbackPipeline), 'ops-mono') : n('span', 'ops-cell-muted', '-'); }},
        {label: 'Source', render: function (row) { return value(row.source || row.architecture); }},
      ], rows, caption);
    }
    if (latestEvidence.length) {
      content.append(panel('Latest AMD execution', 'Current mirror signal only', sourceEvidenceTable(
        latestEvidence,
        'amd-ci',
        integer(latestEvidence.length) + ' exact AMD execution references'
      )));
    }
    if (historyEvidence.length) {
      content.append(panel('Upstream history', 'Historical reliability and parity evidence', sourceEvidenceTable(
        historyEvidence,
        'ci',
        integer(historyEvidence.length) + ' retained upstream references'
      )));
    }
    if (variants.length) {
      content.append(panel('Strict reliability variants', integer(variants.length) + ' catalog identities combined by the reviewed target', dataTable([
        {label: 'Hardware variant', sticky: true, render: function (variant) { return groupIdentityCell(variant, function () { openGroupDetail(variant, ops, variant, reliability); }); }},
        {label: 'Runs', numeric: true, render: function (variant) { return linkButton(integer(variant.runs), function () { openGroupDetail(variant, ops, variant, reliability); }, 'Inspect ' + value(variant.name) + ' on ' + value(variant.hardware) + ' run history'); }},
        {label: 'Incident rate', numeric: true, render: function (variant) { const rate = Number(variant.fail_rate); return linkButton(Number.isFinite(rate) ? rate.toFixed(1) + '%' : '-', function () { openGroupDetail(variant, ops, variant, reliability); }, 'Inspect ' + value(variant.name) + ' on ' + value(variant.hardware) + ' incidents'); }},
        {label: 'Latest', render: function (variant) { const latest = latestObservation(variant); return linkedBadge(latest ? observationState(latest) : 'unavailable', exactPipelineEvidenceUrl(latest, 'ci'), function () { openGroupDetail(variant, ops, variant, reliability); }); }},
        {label: 'Evidence', render: function (variant) { return linkButton(integer(evidenceObservations(variant).length) + ' observations', function () { openGroupDetail(variant, ops, variant, reliability); }, 'Inspect every retained observation for variant ' + value(variant.id)); }},
      ], variants, integer(evidenceObservations(combined).length) + ' exact observations across all target variants')));
    }
    if (combined && evidenceObservations(combined).length) {
      const openHistory = button('Inspect all variants and observations', function () { openMixedOutcomeEvidence(combined); }, true);
      content.append(openHistory);
    }
    const plan = group.reviewed_plan || {};
    const latest = group.latest_amd_result || {};
    const main = group.main_reliability || {};
    openDetailDrawer({
      id: 'gating-' + (group.id || group.label),
      title: group.label || group.name || 'Reviewed target',
      subtitle: 'Reviewed plan, current AMD signal, and upstream history',
      description: 'Plan membership is configuration intent. The latest result is AMD; reliability, streaks, and incidents are upstream.',
      fields: [
        {label: 'Reviewed plan', value: plan.label || plan.status},
        {label: 'Latest AMD result', value: latest.state},
        {label: 'Assessment', value: group.assessment},
        {label: 'Upstream main runs', value: main.runs !== undefined ? integer(main.runs) : null},
        {label: 'Upstream main incidents', value: main.incident_count !== undefined ? integer(main.incident_count) + ' (' + value(main.incident_rate_pct) + '%)' : null},
        {label: 'Upstream nightly streak', value: group.nightly_green_streak !== undefined ? integer(group.nightly_green_streak) : null},
        {label: 'Last upstream incident', value: group.last_incident ? shortDate(group.last_incident.observed_at) : null},
      ],
      sources: [
        plan.source_url ? {label: 'Open reviewed configuration', url: plan.source_url} : null,
        gatingEvidenceUrl(group) ? {label: 'Open latest AMD evidence', url: gatingEvidenceUrl(group)} : null,
        {label: 'Open published reliability catalog', url: SOURCE_ASSETS.operations},
      ],
      content: content,
    });
  }

  function openBuildDetail(build, title) {
    const sourcePipeline = build.source_pipeline || 'amd-ci';
    const transitions = build.transitions || {};
    const content = n('div', 'ops-stack');
    const rows = [];
    [['New', transitions.new || []], ['Recurring', transitions.recurring || []], ['Fixed', transitions.fixed || []]].forEach(function (bucket) {
      bucket[1].forEach(function (group) { rows.push(Object.assign({lifecycle: bucket[0]}, group)); });
    });
    if (rows.length) content.append(dataTable([
      {label: 'Group', sticky: true, render: function (row) { return externalLink(row.display_name || row.name, exactPipelineEvidenceUrl(row, sourcePipeline)); }},
      {label: 'Lifecycle', render: function (row) { return linkedBadge(row.lifecycle, exactPipelineEvidenceUrl(row, sourcePipeline), null, row.lifecycle === 'New' ? 'is-danger' : row.lifecycle === 'Fixed' ? 'is-success' : 'is-warning'); }},
      {label: 'Queue', render: function (row) { return n('span', 'ops-mono', value(row.queue)); }},
    ], rows, integer(rows.length) + ' changed group observations'));
    openDetailDrawer({
      id: 'build-' + value(build.number),
      title: title || (build.number ? 'AMD build #' + build.number : 'AMD build'),
      subtitle: 'Build result and group lifecycle evidence',
      fields: [
        {label: 'State', value: value(build.state)},
        {label: 'Started', value: shortDate(build.created_at)},
        {label: 'Observed groups', value: integer(build.total_groups)},
        {label: 'New / recurring / fixed', value: integer((transitions.new || []).length) + ' / ' + integer((transitions.recurring || []).length) + ' / ' + integer((transitions.fixed || []).length)},
      ],
      sources: exactPipelineBuildUrl(build, sourcePipeline) ? [{label: 'Open Buildkite build', url: exactPipelineBuildUrl(build, sourcePipeline)}] : [],
      content: content,
    });
  }

  function openQueueDetail(name, row, jobs) {
    const related = (jobs || []).filter(function (job) { return job.queue === name; });
    const content = related.length ? dataTable([
      {label: 'Job', sticky: true, render: function (job) { return externalLink(job.name || 'Unnamed job', job.url); }},
      {label: 'State', render: function (job) { return linkedBadge(job.state || 'unknown', job.url); }},
      {label: 'Age', numeric: true, render: function (job) { return duration(job.wait_min !== undefined ? job.wait_min : job.run_min); }},
      {label: 'Build', render: function (job) { return externalLink((job.pipeline || '?') + ' #' + value(job.build), job.build_url || buildUrl(job.pipeline, job.build), 'ops-mono'); }},
    ], related, integer(related.length) + ' active jobs on this queue') : n('div', 'ops-empty', 'No active jobs are retained for this queue.');
    openDetailDrawer({
      id: 'queue-' + name,
      title: name,
      subtitle: 'Current queue state and source-aware waits',
      fields: [
        {label: 'Running', value: integer(row.running)},
        {label: 'Waiting', value: integer(row.waiting)},
        {label: 'Connected agents', value: hasAgentMeasurement(row) ? integer(row.connected_agents !== undefined ? row.connected_agents : row.agents) : 'Unavailable'},
        {label: 'p50', value: duration(waitValue(row, 'p50'))},
        {label: 'p95', value: duration(waitValue(row, 'p95'))},
        {label: 'p99 sampled', value: duration(waitValue(row, 'p99'))},
        {label: 'Count source', value: row.count_source},
      ],
      sources: row.queue_url || row.url
        ? [{label: 'Open Buildkite queue', url: row.queue_url || row.url}, {label: 'Open published queue snapshot', url: SOURCE_ASSETS.operations}]
        : [{label: 'Open published queue snapshot', url: SOURCE_ASSETS.operations}],
      content: content,
    });
  }

  function openPerfHistory(model, config, metricName, block) {
    const series = (block.series || []).slice().sort(function (a, b) { return String(b.date || '').localeCompare(String(a.date || '')); });
    const content = n('div', 'ops-evidence ops-perf-history');
    const note = n('div', 'ops-evidence-note is-info');
    add(note, [n('strong', '', block.label || metricName), n('span', '', ' - ' + (block.direction === 'lower' ? 'lower is better' : 'higher is better') + '. Every point links to its source perf-eval build.')]);
    content.append(note);

    const summary = n('div', 'ops-evidence-summary is-four');
    add(summary, [
      evidenceSummaryItem('LATEST', perfValue(block.latest, block.unit)),
      evidenceSummaryItem('PREVIOUS', perfValue(block.previous, block.unit)),
      evidenceSummaryItem('CHANGE', perfDelta(block).textContent, block.status === 'good' ? 'is-success' : block.status === 'bad' ? 'is-danger' : ''),
      evidenceSummaryItem('POINTS', integer(series.length)),
    ]);
    content.append(summary);

    const chart = n('div', 'ops-perf-history-chart');
    const canvas = n('canvas', 'ops-chart-canvas');
    chart.append(canvas);
    content.append(chart);
    content.append(dataTable([
      {label: 'Nightly', sticky: true, render: function (row) { return externalLink(row.build_number ? '#' + row.build_number : 'Build', row.build_url, 'ops-mono'); }},
      {label: 'Observed', render: function (row) { return shortDate(row.date); }},
      {label: 'Value', numeric: true, render: function (row) { return perfValue(row.value, block.unit); }},
      {label: 'vLLM commit', render: function (row) {
        return row.vllm_commit ? externalLink(String(row.vllm_commit).slice(0, 7), 'https://github.com/vllm-project/vllm/commit/' + row.vllm_commit, 'ops-mono') : n('span', 'ops-cell-muted', '-');
      }},
      {label: 'Image', render: function (row) {
        return linkButton(value(row.image), function () {
          openDetailDrawer({id: 'image-' + value(row.image), title: 'Runtime image', fields: [{label: 'Image', value: value(row.image)}, {label: 'Build', value: row.build_number ? '#' + row.build_number : null}], sources: row.build_url ? [{label: 'Open producing build', url: row.build_url}] : []});
        });
      }},
    ], series, integer(series.length) + ' perf-eval nightly observations'));
    openOverlay(model.model + ' - ' + (config.label || 'Metric history'), block.label || metricName, content, true);
    requestAnimationFrame(function () {
      drawPerfSpark('perf-history-dialog', canvas, (block.series || []), block.status, block.unit);
    });
  }

  function perfMetricTile(model, config, metricName, block, chartQueue, chartKey) {
    const tone = block.status === 'good' ? 'is-success' : block.status === 'bad' ? 'is-danger' : 'is-neutral';
    const tile = n('article', 'ops-perf-metric ' + tone);
    const header = n('div', 'ops-perf-metric-header');
    add(header, [n('h4', 'ops-perf-metric-name', block.label || metricName), perfDelta(block)]);
    const valueRow = n('div', 'ops-perf-metric-value', perfValue(block.latest, block.unit));
    const direction = n('div', 'ops-perf-direction', block.direction === 'lower' ? 'Lower is better' : 'Higher is better');
    const spark = n('div', 'ops-perf-spark');
    const canvas = n('canvas');
    spark.append(canvas);
    const footer = n('div', 'ops-perf-metric-footer');
    add(footer, [direction, linkButton('Inspect history', function () { openPerfHistory(model, config, metricName, block); })]);
    add(tile, [header, valueRow, spark, footer]);
    chartQueue.push({key: chartKey, canvas: canvas, series: block.series || [], status: block.status, unit: block.unit});
    return tile;
  }

  function perfModelSection(model, modelIndex, chartQueue) {
    const section = n('section', 'ops-perf-model-section');
    const latest = model.latest || {};
    const header = n('header', 'ops-perf-model-header');
    const identity = n('div', 'ops-perf-model-identity');
    const titleRow = n('div', 'ops-inline-actions');
    const title = n('h2', 'ops-perf-model-title');
    const titleControl = linkButton(model.model, function () {
      openDetailDrawer({
        id: 'perf-model-' + model.model,
        title: model.model,
        subtitle: 'Performance and evaluation model history',
        fields: [
          {label: 'Nightlies', value: integer(model.nightly_count)},
          {label: 'Hardware', value: (model.devices || []).join(', ')},
          {label: 'Latest commit', value: latest.vllm_commit ? String(latest.vllm_commit).slice(0, 12) : null},
          {label: 'Latest image', value: latest.image},
        ],
        sources: [latest.build_url ? {label: 'Open latest perf build', url: latest.build_url} : null, latest.vllm_commit ? {label: 'Open vLLM commit', url: 'https://github.com/vllm-project/vllm/commit/' + latest.vllm_commit} : null],
      });
    });
    titleControl.classList.add('ops-perf-model-link');
    title.append(titleControl);
    titleRow.append(title);
    (model.devices || []).forEach(function (device) { titleRow.append(badge(String(device).toUpperCase(), 'is-info')); });
    identity.append(titleRow);
    const provenance = n('div', 'ops-perf-provenance');
    if (latest.vllm_commit) provenance.append(externalLink('commit ' + String(latest.vllm_commit).slice(0, 7), 'https://github.com/vllm-project/vllm/commit/' + latest.vllm_commit, 'ops-mono'));
    if (latest.build_number !== null && latest.build_number !== undefined) provenance.append(externalLink('build #' + latest.build_number, latest.build_url));
    if (latest.date) provenance.append(n('span', '', shortDate(latest.date)));
    if (latest.image) provenance.append(n('code', 'ops-perf-image', latest.image));
    identity.append(provenance);
    header.append(identity);
    header.append(n('div', 'ops-perf-nightly-count', integer(model.nightly_count) + ' nightlies'));
    section.append(header);

    const configs = (model.perf_configs || []).filter(function (config) {
      return state.perfDevice === 'all' || String(config.device || '').toLowerCase() === state.perfDevice;
    });
    if (!configs.length) {
      section.append(n('div', 'ops-empty', 'No performance configuration matches this hardware filter.'));
      return section;
    }
    configs.forEach(function (config, configIndex) {
      const group = n('section', 'ops-perf-config');
      const configHeader = n('header', 'ops-perf-config-header');
      add(configHeader, [n('h3', '', config.label || 'Performance configuration'), n('div', 'ops-panel-meta', ['TP ' + value(config.tp), value(config.precision)].join(' - '))]);
      group.append(configHeader);
      const grid = n('div', 'ops-perf-metric-grid');
      const preferred = ['tput_per_gpu', 'output_tput_per_gpu', 'input_tput_per_gpu', 'mean_ttft', 'p99_ttft', 'mean_tpot', 'mean_itl', 'mean_intvty'];
      Object.keys(config.metrics || {}).sort(function (a, b) {
        const ai = preferred.indexOf(a), bi = preferred.indexOf(b);
        return (ai < 0 ? 99 : ai) - (bi < 0 ? 99 : bi) || a.localeCompare(b);
      }).forEach(function (metricName, metricIndex) {
        grid.append(perfMetricTile(model, config, metricName, config.metrics[metricName], chartQueue, 'perf-' + modelIndex + '-' + configIndex + '-' + metricIndex));
      });
      group.append(grid);
      section.append(group);
    });
    return section;
  }

  function accuracyRows(models) {
    const rows = [];
    models.forEach(function (model) {
      (model.accuracy_tasks || []).forEach(function (task) { rows.push({model: model, task: task}); });
    });
    return rows;
  }

  function chartPanel(title, subtitle, key) {
    const canvas = n('canvas', 'ops-chart-canvas');
    canvas.dataset.chartKey = key;
    const frame = n('div', 'ops-chart-stage');
    const viewport = n('div', 'ops-chart-viewport');
    viewport.append(canvas);
    frame.append(viewport);
    return {root: panel(title, subtitle, frame, 'ops-chart-panel'), canvas, frame, viewport};
  }

  function drawChart(key, canvas, config) {
    if (!window.Chart || !canvas) return;
    if (charts.has(key)) charts.get(key).destroy();
    const evidence = config.evidence || [];
    const evidenceTitle = config.evidenceTitle || 'Chart evidence';
    const showEvidenceAction = config.evidenceAction !== false;
    const evidenceAsset = config.evidenceAsset || SOURCE_ASSETS.operations;
    delete config.evidence;
    delete config.evidenceTitle;
    delete config.evidenceAction;
    delete config.evidenceAsset;
    const text = getComputedStyle(document.documentElement).getPropertyValue('--ops-text-muted').trim() || '#93a0ad';
    const grid = getComputedStyle(document.documentElement).getPropertyValue('--ops-chart-grid').trim() || '#30383b';
    config.options = Object.assign({responsive: true, maintainAspectRatio: false}, config.options || {});
    config.options.plugins = Object.assign({
      legend: {position: 'top', align: 'end', labels: {color: text, boxWidth: 10, usePointStyle: true}},
    }, config.options.plugins || {});
    config.options.scales = config.options.scales || {
      x: {grid: {display: false}, ticks: {color: text, maxTicksLimit: 8}},
      y: {beginAtZero: true, grid: {color: grid}, ticks: {color: text}},
    };
    function inspectIndex(index) {
      const point = evidence[index];
      if (!point) return;
      if (typeof point.onOpen === 'function') point.onOpen();
      else if (point.url) {
        openDetailDrawer({
          id: point.id || historyPointLabel(point),
          title: historyPointLabel(point),
          subtitle: point.scope || 'Source-backed chart observation',
          fields: Object.entries(point.details || {}).map(function (entry) { return {label: entry[0].replace(/_/g, ' '), value: entry[1]}; }),
          sources: historyPointSources(point, evidenceAsset),
        });
      } else {
        openDetailDrawer({
          id: point.id || historyPointLabel(point),
          title: historyPointLabel(point),
          subtitle: point.scope || 'Retained chart observation',
          fields: Object.entries(point.details || {}).map(function (entry) { return {label: entry[0].replace(/_/g, ' '), value: entry[1]}; }),
          sources: historyPointSources(point, evidenceAsset),
        });
      }
    }
    if (evidence.length) {
      const existingOnClick = config.options.onClick;
      config.options.onClick = function (event, elements, chart) {
        if (elements && elements.length) inspectIndex(elements[0].index);
        if (typeof existingOnClick === 'function') existingOnClick(event, elements, chart);
      };
      canvas.tabIndex = 0;
      canvas.setAttribute('role', 'button');
      canvas.setAttribute('aria-label', evidenceTitle + '. Use left and right arrows to choose an observation and Enter to inspect it.');
      let activeEvidenceIndex = evidence.length - 1;
      canvas.addEventListener('keydown', function (event) {
        if (event.key === 'ArrowLeft') {
          event.preventDefault();
          activeEvidenceIndex = Math.max(0, activeEvidenceIndex - 1);
        } else if (event.key === 'ArrowRight') {
          event.preventDefault();
          activeEvidenceIndex = Math.min(evidence.length - 1, activeEvidenceIndex + 1);
        } else if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          inspectIndex(activeEvidenceIndex);
        }
        canvas.dataset.activeEvidenceIndex = String(activeEvidenceIndex);
        canvas.setAttribute('aria-description', 'Selected ' + historyPointLabel(evidence[activeEvidenceIndex]));
      });
      const stage = canvas.closest('.ops-chart-stage') || canvas.parentElement;
      if (showEvidenceAction && stage && !stage.querySelector('.ops-chart-evidence-action')) {
        const inspect = linkButton('Inspect ' + integer(evidence.length) + ' observations', function () {
          openHistoryEvidence(evidenceTitle, evidence, 'Chart values and their retained source evidence', evidenceAsset);
        }, 'Open chart evidence as an accessible table');
        inspect.classList.add('ops-chart-evidence-action');
        stage.append(inspect);
      }
    }
    charts.set(key, new window.Chart(canvas, config));
  }

  function pruneInactiveCharts() {
    for (const [key, chart] of charts.entries()) {
      const canvas = chart && chart.canvas;
      const panel = canvas && canvas.closest ? canvas.closest('.tab-panel') : null;
      if (!canvas || !document.documentElement.contains(canvas) || (panel && !panel.classList.contains('active'))) {
        chart.destroy();
        charts.delete(key);
      }
    }
  }

  async function fetchJSON(path) {
    if (!cache.has(path)) {
      cache.set(path, fetch(path + '?_=' + Math.floor(Date.now() / 300000)).then(function (r) {
        if (!r.ok) throw new Error(path + ' returned HTTP ' + r.status);
        return r.json();
      }));
    }
    return cache.get(path);
  }

  async function fetchJSONL(path) {
    const key = 'jsonl:' + path;
    if (!cache.has(key)) {
      cache.set(key, fetch(path + '?_=' + Math.floor(Date.now() / 300000)).then(function (r) {
        if (!r.ok) throw new Error(path + ' returned HTTP ' + r.status);
        return r.text();
      }).then(function (text) {
        return text.split(/\r?\n/).filter(Boolean).map(function (line) {
          try { return JSON.parse(line); } catch (_) { return null; }
        }).filter(Boolean);
      }));
    }
    return cache.get(key);
  }

  function ownedHost(tabId) {
    const panelEl = document.getElementById('tab-' + tabId);
    if (!panelEl) return null;
    panelEl.classList.add('ops-page');
    let host = panelEl.querySelector('.ops-v2-host');
    if (!host) {
      clear(panelEl);
      host = n('section', 'ops-v2-host');
      host.id = tabId + '-view';
      panelEl.append(host);
    }
    return host;
  }

  function nightlyForPipeline(ops, pipeline) {
    const nightly = (ops || {}).nightly || {};
    const pipelines = nightly.pipelines || [];
    const matched = pipelines.find(function (item) { return item.pipeline === pipeline; });
    if (matched) return matched;
    if (pipeline === 'ci') return nightly.upstream || nightly.upstream_parity || {pipeline: pipeline, builds: []};
    return nightly.amd || nightly.canonical_history || {pipeline: pipeline, builds: []};
  }

  function nightlyDisplayName(nightly, pipeline) {
    return nightly.display_name || (pipeline === 'ci' ? 'Upstream CI' : 'AMD CI');
  }

  function latestAmd(ops) {
    return nightlyForPipeline(ops, 'amd-ci');
  }

  function setFreshness(ops) {
    const ts = ops.generated_at || (((ops.sources || {}).analytics || {}).timestamp);
    const label = ts ? 'Updated ' + age(ts) : 'Update unknown';
    const sidebar = document.getElementById('last-updated');
    const mobile = document.getElementById('ops-mobile-freshness');
    if (sidebar) sidebar.textContent = label;
    if (mobile) mobile.textContent = ts ? age(ts) : 'Unknown';
  }

  function attentionLabel(item) {
    const labels = {
      nightly_new_failures: 'New failures since the preceding AMD nightly',
      nightly_soft_failures: 'Soft-failed groups in the latest AMD nightly',
      queue_zombies: 'Queue jobs older than the analysis threshold',
      queue_waiting: 'Jobs currently waiting across tracked queues',
      gating_red_targets: 'Canonical target groups not ready',
      target_groups_with_current_incidents: 'Reviewed target groups with current AMD incidents',
      mixed_state_flaky_candidates: 'Groups with both green and failing observations',
      omni_waiting: 'Omni jobs waiting across the fleet',
    };
    return labels[item.kind] || item.kind.replace(/_/g, ' ');
  }

  function inspectAttention(item, ops) {
    if (String(item.kind || '').startsWith('queue_')) navigateTo('ci-queue', {queueView: item.kind === 'queue_waiting' ? 'jobs' : 'current', queueScope: 'all'});
    else if (item.kind === 'gating_red_targets' || item.kind === 'target_groups_with_current_incidents') navigateTo('ci-health', {healthView: 'gating', healthResult: 'incident'});
    else if (item.kind === 'mixed_state_flaky_candidates') navigateTo('ci-analytics', {analyticsView: 'flakes'});
    else if (item.kind === 'omni_waiting') navigateTo('ci-omni');
    else {
      const build = ((latestAmd(ops).builds || [])[0]) || {};
      if (build.number) openBuildDetail(build, attentionLabel(item));
      else openMetricDetail({label: attentionLabel(item), value: item.count, meta: 'No linked build is available in this snapshot.'});
    }
  }

  async function renderHome(host, ops) {
    const amd = latestAmd(ops);
    const build = (amd.builds || [])[0] || {};
    const trans = build.transitions || {};
    const matrix = (ops.gating || {}).matrix_summary || {};
    const queue = (ops.queue || {}).snapshot || {};
    const allFleetQueues = Object.entries(queue.queues || {}).filter(function (entry) { return !isRetiredQueue(entry[0]); });
    const allFleetWaiting = allFleetQueues.length ? allFleetQueues.reduce(function (sum, entry) { return sum + Number((entry[1] || {}).waiting || 0); }, 0) : Number(queue.total_waiting || 0);
    const allFleetRunning = allFleetQueues.length ? allFleetQueues.reduce(function (sum, entry) { return sum + Number((entry[1] || {}).running || 0); }, 0) : Number(queue.total_running || 0);
    const unknownCells = Number(matrix.unknown_cells || 0);
    add(host, pageHeader('Command Center', 'Current AMD operations with retained nightly movement and direct paths to source evidence.', ops.generated_at));
    add(host, statusStrip([
      {id: 'home-amd-nightly', label: 'LATEST AMD NIGHTLY', value: build.number ? '#' + build.number : '-', meta: value(build.state, 'No completed build'), tone: toneForState(build.state), url: exactPipelineBuildUrl(build, 'amd-ci'), observed: build.created_at},
      {id: 'home-hardware-coverage', label: 'HARDWARE COVERAGE', value: integer(matrix.passing_cells) + ' / ' + integer(matrix.hardware_cells), meta: integer(matrix.passing_cells) + ' passing + ' + integer(matrix.failing_cells) + ' failing + ' + integer(unknownCells) + ' unknown = ' + integer(matrix.hardware_cells), tone: Number(matrix.failing_cells) ? 'is-danger' : unknownCells ? 'is-warning' : 'is-success', onOpen: function () { navigateTo('ci-health', {healthView: 'coverage'}); }},
      {id: 'home-failure-lifecycle', label: 'NIGHTLY MOVEMENT', value: integer((trans.new || []).length) + ' new', meta: integer((trans.recurring || []).length) + ' recurring - ' + integer((trans.fixed || []).length) + ' fixed', tone: (trans.new || []).length ? 'is-danger' : 'is-success', onOpen: function () { openBuildDetail(build); }},
      {id: 'home-queue-snapshot', label: 'ALL-FLEET QUEUE ACTIVITY', value: integer(allFleetWaiting) + ' waiting', meta: integer(allFleetRunning) + ' running across ' + integer(allFleetQueues.length) + ' queues', tone: allFleetWaiting ? 'is-warning' : 'is-success', observed: queue.ts, provenance: 'Same all-queue scope as destination', onOpen: function () { navigateTo('ci-queue', {queueView: 'current', queueScope: 'all'}); }},
    ]));

    const grid = n('div', 'ops-grid ops-grid-main-aside ops-home-grid');
    const attentionRows = ops.attention || [];
    grid.append(panel('Needs attention', attentionRows.length + ' active signals', dataTable([
      {label: 'Operational signal', sticky: true, render: function (item) { return linkButton(attentionLabel(item), function () { inspectAttention(item, ops); }); }},
      {label: 'Severity', render: function (item) { return linkedBadge(item.severity, null, function () { inspectAttention(item, ops); }); }},
      {label: 'Count', numeric: true, render: function (item) { return linkButton(integer(item.count), function () { inspectAttention(item, ops); }); }},
    ], attentionRows), 'ops-home-primary'));

    const recent = (amd.builds || []).slice(0, 7);
    grid.append(panel('AMD nightly movement', 'Latest seven completed observations', dataTable([
      {label: 'Build', render: function (r) { return externalLink('#' + r.number, exactPipelineBuildUrl(r, 'amd-ci'), 'ops-mono'); }},
      {label: 'New', numeric: true, render: function (r) { return linkButton(integer((r.transitions.new || []).length), function () { openBuildDetail(r); }); }},
      {label: 'Recurring', numeric: true, render: function (r) { return linkButton(integer((r.transitions.recurring || []).length), function () { openBuildDetail(r); }); }},
      {label: 'Fixed', numeric: true, render: function (r) { return linkButton(integer((r.transitions.fixed || []).length), function () { openBuildDetail(r); }); }},
      {label: 'Observed', render: function (r) { return shortDate(r.created_at); }},
    ], recent), 'ops-home-aside'));
    host.append(grid);

    let workData = {prs: [], issues: []};
    try {
      const loaded = await Promise.all([fetchJSON('data/vllm/prs.json'), fetchJSON('data/vllm/issues.json')]);
      workData = {prs: loaded[0].prs || [], issues: loaded[1].issues || []};
    } catch (_) {}
    const workPanel = n('section', 'ops-panel');
    const workHead = n('div', 'ops-panel-header');
    add(workHead, [n('h2', 'ops-panel-title', 'Engineering workbench'), segmented([
      {id: 'issues', label: 'Issues (' + workData.issues.length + ')'},
      {id: 'prs', label: 'PRs (' + workData.prs.length + ')'},
    ], state.homeWork, function (id) { state.homeWork = id; render('projects', true); }, 'Engineering workbench item type')]);
    const rows = state.homeWork === 'prs' ? workData.prs : workData.issues;
    const body = dataTable([
      {label: state.homeWork === 'prs' ? 'Pull request' : 'Issue', sticky: true, render: function (r) { return externalLink('#' + r.number + ' ' + r.title, r.html_url, 'ops-cell-primary'); }},
      {label: 'Assignee', render: function (r) {
        const who = r.author || (r.assignees || [])[0];
        return who ? externalLink(who, 'https://github.com/' + encodeURIComponent(who)) : n('span', 'ops-cell-muted', 'Unassigned');
      }},
      {label: 'State', render: function (r) { return linkedBadge(r.merged ? 'merged' : r.state, r.html_url); }},
      {label: 'Labels', render: function (r) { return (r.custom_tags || r.labels || []).slice(0, 4).join(', ') || '-'; }},
      {label: 'Updated', render: function (r) { return shortDate(r.updated_at); }},
    ], rows);
    add(workPanel, [workHead, n('div', 'ops-panel-body')]);
    workPanel.lastChild.append(body);
    host.append(workPanel);
  }

  function healthTabs(host) {
    host.append(segmented([
      {id: 'overview', label: 'Overview'}, {id: 'gating', label: 'Gating'},
      {id: 'coverage', label: 'Coverage'}, {id: 'diagnostics', label: 'Diagnostics'},
    ], state.healthView, function (id) { setRouteState('ci-health', 'healthView', id, 'health_view'); }, 'CI Health view'));
  }

  async function renderHealth(host, ops) {
    const amd = latestAmd(ops);
    const build = (amd.builds || [])[0] || {};
    const trans = build.transitions || {};
    const gating = ops.gating || {};
    const targetSummary = gating.active_target_summary || gating.target_summary || {};
    const matrix = gating.matrix_summary || {};
    add(host, pageHeader('CI Health', 'Reviewed AMD coverage, observed results, and nightly movement with source evidence kept distinct.', ops.generated_at));
    healthTabs(host);
    host.append(statusStrip([
      {id: 'health-build', label: 'LATEST AMD NIGHTLY', value: value(build.state), meta: build.number ? '#' + build.number + ' - ' + shortDate(build.created_at) : 'No completed build', tone: toneForState(build.state), url: exactPipelineBuildUrl(build, 'amd-ci')},
      {id: 'health-hardware', label: 'HARDWARE COVERAGE', value: integer(matrix.passing_cells) + ' / ' + integer(matrix.hardware_cells), meta: integer(matrix.failing_cells) + ' failing + ' + integer(matrix.unknown_cells || 0) + ' unknown', tone: Number(matrix.failing_cells) ? 'is-danger' : Number(matrix.unknown_cells) ? 'is-warning' : 'is-success', onOpen: function () { setRouteState('ci-health', 'healthView', 'coverage', 'health_view'); }},
      {id: 'health-reviewed-plan', label: 'REVIEWED PLAN', value: integer(targetSummary.canonical_group_count || targetSummary.target_group_count), meta: integer(targetSummary.active_outside_canonical_count || 0) + ' observed outside the reviewed list', onOpen: function () { setRouteState('ci-health', 'healthView', 'gating', 'health_view'); }},
      {id: 'health-nightly-transition', label: 'NIGHTLY MOVEMENT', value: integer((trans.new || []).length) + ' new', meta: integer((trans.recurring || []).length) + ' recurring - ' + integer((trans.fixed || []).length) + ' fixed', tone: (trans.new || []).length ? 'is-danger' : 'is-success', onOpen: function () { openBuildDetail(build); }},
    ]));

    if (state.healthView === 'overview') {
      const grid = n('div', 'ops-grid ops-grid-main-aside ops-health-grid');
      const trend = chartPanel('Nightly failure lifecycle', 'Group state versus the immediately preceding AMD nightly', 'health-nightly');
      trend.root.classList.add('ops-health-primary');
      grid.append(trend.root);
      const failures = (build.failed_groups || []).concat(build.soft_failed_groups || []).slice(0, 15);
      grid.append(panel('Latest blockers', (build.failed_groups || []).length + ' hard - ' + (build.soft_failed_groups || []).length + ' soft', dataTable([
        {label: 'Group', render: function (r) { return externalLink(r.display_name || r.name, exactPipelineEvidenceUrl(r, 'amd-ci')); }},
        {label: 'State', render: function (r) { return linkedBadge(r.state, exactPipelineEvidenceUrl(r, 'amd-ci')); }},
      ], failures), 'ops-health-aside'));
      host.append(grid);
      drawChart('health-nightly', trend.canvas, {
        type: 'bar',
        data: {
          labels: (amd.builds || []).slice(0, 14).reverse().map(function (b) { return '#' + b.number; }),
          datasets: [
            {label: 'New', data: (amd.builds || []).slice(0, 14).reverse().map(function (b) { return (b.transitions.new || []).length; }), backgroundColor: '#e06464'},
            {label: 'Recurring', data: (amd.builds || []).slice(0, 14).reverse().map(function (b) { return (b.transitions.recurring || []).length; }), backgroundColor: '#e3a63a'},
            {label: 'Fixed', data: (amd.builds || []).slice(0, 14).reverse().map(function (b) { return (b.transitions.fixed || []).length; }), backgroundColor: '#35bb78'},
          ],
        },
        options: {scales: {x: {stacked: true}, y: {stacked: true, beginAtZero: true}}},
        evidenceTitle: 'AMD nightly failure lifecycle',
        evidence: (amd.builds || []).slice(0, 14).reverse().map(function (nightly) {
          return {label: '#' + nightly.number, timestamp: nightly.created_at, url: exactPipelineBuildUrl(nightly, 'amd-ci'), valueSummary: integer((nightly.transitions.new || []).length) + ' new', details: {state: nightly.state, new: (nightly.transitions.new || []).length, recurring: (nightly.transitions.recurring || []).length, fixed: (nightly.transitions.fixed || []).length}};
        }),
      });
      return;
    }

    if (state.healthView === 'gating') {
      const activeGroups = gating.active_target_groups || gating.target_groups || [];
      const total = Number(targetSummary.target_group_count || activeGroups.length);
      const reliability = canonicalReliability(ops);
      const scope = reliabilityScopeInfo(reliability);
      const latestFailures = (build.failed_groups || []).concat(build.soft_failed_groups || []);
      const failureByName = new Map(latestFailures.map(function (row) { return [normalizeLabel(row.display_name || row.name), row]; }));
      const rows = activeGroups.map(function (group) {
        const explicitReliability = group.main_reliability || {};
        const rel = combinedGatingReliability(group, reliability);
        const failure = failureByName.get(normalizeLabel(group.label || group.name));
        const explicitLatestResult = group.latest_amd_result || {};
        const explicitLatestState = observationState(explicitLatestResult);
        const explicitLatest = ['passed', 'soft', 'hard'].includes(explicitLatestState) ? explicitLatestResult : null;
        const latest = explicitLatest || failure || null;
        const latestState = latest ? observationState(latest) : 'unavailable';
        const explicitPlan = group.reviewed_plan || {};
        const plan = explicitPlan.label || (group.target_origin === 'canonical' || !group.target_origin ? 'Reviewed target' : group.target_origin === 'active_outside_canonical' ? 'Observed outside review' : 'Unreviewed');
        const planStatus = explicitPlan.status || (plan === 'Reviewed target' ? 'included' : plan.toLowerCase().includes('outside') ? 'observed_outside_reviewed_plan' : 'unreviewed');
        const runs = rel && Number(rel.runs !== undefined ? rel.runs : rel.observation_count);
        const passes = rel && Number(rel.passed);
        const explicitRuns = Number(explicitReliability.runs);
        const explicitPassed = Number(explicitReliability.passed);
        const reliabilityValue = Number.isFinite(explicitRuns) && explicitRuns > 0 && Number.isFinite(explicitPassed)
          ? integer(explicitPassed) + ' / ' + integer(explicitRuns) + ' pass'
          : scope.allMain && Number.isFinite(runs) && runs > 0 && Number.isFinite(passes) ? integer(passes) + ' / ' + integer(runs) + ' pass' : null;
        return {
          group: group,
          reliability: rel,
          latest: latest,
          latestState: latestState,
          plan: plan,
          planStatus: planStatus,
          mainReliability: reliabilityValue,
          hasExplicitMainReliability: Object.prototype.hasOwnProperty.call(group, 'main_reliability'),
          streak: group.nightly_green_streak !== undefined ? Number(group.nightly_green_streak) : greenStreak(rel),
          incident: group.last_incident || lastIncident(rel),
        };
      });
      const linked = rows.filter(function (row) { return row.latest && gatingEvidenceUrl(row.group); }).length;
      const passing = rows.filter(function (row) { return row.latestState === 'passed'; }).length;
      const incidents = rows.filter(function (row) { return isIncidentObservation(row.latest || {}); }).length;
      host.append(statusStrip([
        {id: 'gating-reviewed', label: 'REVIEWED TARGETS', value: integer(rows.filter(function (row) { return row.planStatus === 'included'; }).length), meta: integer(total) + ' groups in this view', onOpen: function () { state.healthPlan = 'reviewed'; render('ci-health', true); }},
        {id: 'gating-linked', label: 'LINKED AMD RESULTS', value: integer(linked) + ' / ' + integer(total), meta: 'exact latest AMD execution links', tone: linked === total ? 'is-success' : 'is-warning', onOpen: function () { state.healthResult = 'linked'; render('ci-health', true); }},
        {id: 'gating-passing', label: 'LATEST AMD PASSING', value: integer(passing), meta: 'among groups with a current AMD signal', tone: incidents ? 'is-warning' : 'is-success', onOpen: function () { state.healthResult = 'passing'; render('ci-health', true); }},
        {id: 'gating-incidents', label: 'LATEST AMD INCIDENTS', value: integer(incidents), meta: 'current mirror evidence only', tone: incidents ? 'is-danger' : 'is-success', onOpen: function () { state.healthResult = 'incident'; render('ci-health', true); }},
      ]));
      const note = n('div', 'ops-evidence-note is-info');
      add(note, [n('strong', '', scope.label + '. '), n('span', '', scope.detail + '. Plan membership and observed execution are intentionally shown as separate facts.')]);
      host.append(note);
      const toolbar = n('div', 'ops-toolbar');
      const search = n('input', 'ops-input');
      search.type = 'search'; search.placeholder = 'Search 127 reviewed groups'; search.value = state.healthSearch;
      search.setAttribute('aria-label', 'Search reviewed test groups');
      search.addEventListener('change', function () { state.healthSearch = search.value; render('ci-health', true); });
      const planFilter = n('select', 'ops-select');
      planFilter.setAttribute('aria-label', 'Filter reviewed plan status');
      [['all', 'All plan states'], ['reviewed', 'Reviewed targets'], ['outside', 'Observed outside review']].forEach(function (pair) { const option = n('option', '', pair[1]); option.value = pair[0]; option.selected = state.healthPlan === pair[0]; planFilter.append(option); });
      planFilter.addEventListener('change', function () { state.healthPlan = planFilter.value; render('ci-health', true); });
      const resultFilter = n('select', 'ops-select');
      resultFilter.setAttribute('aria-label', 'Filter latest AMD result');
      [['all', 'All latest results'], ['passing', 'Passing'], ['incident', 'Incidents'], ['linked', 'Any linked result'], ['unavailable', 'Evidence pending']].forEach(function (pair) { const option = n('option', '', pair[1]); option.value = pair[0]; option.selected = state.healthResult === pair[0]; resultFilter.append(option); });
      resultFilter.addEventListener('change', function () { state.healthResult = resultFilter.value; render('ci-health', true); });
      add(toolbar, [search, planFilter, resultFilter]);
      host.append(toolbar);
      const q = state.healthSearch.trim().toLowerCase();
      const groups = rows.filter(function (row) {
        if (q && ![row.group.label, row.group.area, row.plan].some(function (part) { return String(part || '').toLowerCase().includes(q); })) return false;
        if (state.healthPlan === 'reviewed' && row.planStatus !== 'included') return false;
        if (state.healthPlan === 'outside' && row.planStatus !== 'observed_outside_reviewed_plan') return false;
        if (state.healthResult === 'passing' && row.latestState !== 'passed') return false;
        if (state.healthResult === 'incident' && !isIncidentObservation(row.latest || {})) return false;
        if (state.healthResult === 'linked' && !(row.latest && gatingEvidenceUrl(row.group))) return false;
        if (state.healthResult === 'unavailable' && row.latest) return false;
        return true;
      });
      host.append(dataTable([
        {label: 'Test group', sticky: true, width: '270px', render: function (row) { return linkButton(row.group.label || row.group.name, function () { openGatingDetail(row.group, ops); }); }},
        {label: 'Reviewed plan', width: '170px', render: function (row) { const plan = row.group.reviewed_plan || {}; return linkedBadge(row.plan, plan.source_url, function () { openGatingDetail(row.group, ops); }, row.planStatus === 'included' ? 'is-neutral' : 'is-info'); }},
        {label: 'Latest AMD result', width: '160px', render: function (row) { return linkedBadge(row.latest ? row.latestState : 'Evidence pending', gatingEvidenceUrl(row.group), function () { openGatingDetail(row.group, ops); }, row.latest ? toneForState(row.latestState) : 'is-neutral'); }},
        {label: 'Upstream pass history', numeric: true, width: '170px', render: function (row) { return linkButton(row.mainReliability || (row.hasExplicitMainReliability ? 'No upstream history' : 'Pending ledger'), function () { openGatingDetail(row.group, ops); }, 'Inspect upstream main variants contributing to ' + value(row.group.label) + ' reliability'); }},
        {label: 'Upstream nightly streak', numeric: true, width: '160px', render: function (row) { return linkButton(row.reliability || row.group.nightly_green_streak !== undefined ? integer(row.streak) + ' nightlies' : '-', function () { openGatingDetail(row.group, ops); }, 'Inspect upstream nightly streak evidence for ' + value(row.group.label)); }},
        {label: 'Last upstream incident', width: '180px', render: function (row) { const incidentUrl = exactPipelineEvidenceUrl(row.incident, 'ci'); return row.incident && incidentUrl ? externalLink(shortDate(row.incident.observed_at || row.incident.date), incidentUrl) : linkButton(row.incident ? shortDate(row.incident.observed_at || row.incident.date) : 'None retained', function () { openGatingDetail(row.group, ops); }); }},
        {label: 'History evidence', width: '160px', render: function (row) { const count = evidenceObservations(row.reliability || {}).length; const direct = (row.group.evidence || []).length; return linkButton(integer(count) + ' runs' + (direct ? ' + ' + integer(direct) + ' refs' : ''), function () { openGatingDetail(row.group, ops); }, 'Inspect every retained upstream source for ' + value(row.group.label)); }},
      ], groups, integer(groups.length) + ' of ' + integer(total) + ' reviewed groups; AMD current signal and upstream history are separate', {name: 'gating', minWidth: '1290px'}));
      return;
    }

    if (state.healthView === 'coverage') {
      let matrixData = {};
      try { matrixData = await fetchJSON('data/vllm/ci/amd_test_matrix.json'); } catch (_) {}
      const arch = matrixData.architectures || [];
      host.append(statusStrip(arch.map(function (a) {
        return {label: a.label + ' DEFINITIONS', value: integer(a.nightly_match_count) + ' / ' + integer(a.group_count), meta: 'nightly matched groups', tone: a.nightly_match_count === a.group_count ? 'is-success' : 'is-warning'};
      })));
      const cols = [{label: 'Group', sticky: true, render: function (r) { return linkButton(r.title, function () { openGroupDetail({name: r.title, area: r.area}, ops); }); }}, {label: 'Area', render: function (r) { return linkButton(value(r.area), function () { openGroupDetail({name: r.title, area: r.area}, ops); }); }}];
      for (const a of arch) {
        cols.push({label: a.label, render: function (r) {
          const c = (r.cells || {})[a.id] || {};
          if (!c.exists) return n('span', 'ops-cell-muted', '-');
          return linkedBadge(c.latest_state || 'unknown', exactPipelineEvidenceUrl({job_url: c.latest_url}, 'amd-ci'), function () { openGroupDetail({name: r.title, area: r.area}, ops); });
        }});
      }
      host.append(dataTable(cols, matrixData.rows || [], integer((matrixData.rows || []).length) + ' configured AMD definition rows'));
      return;
    }

    const reliability = canonicalReliability(ops);
    const retry = reliability.retry_analysis || {};
    const diagnosticGrid = n('div', 'ops-stack ops-reliability-stack');
    diagnosticGrid.append(panel('Upstream mixed-outcome candidates', 'Upstream groups with both passing and incident observations; inspect the run evidence before classifying a flake', dataTable([
      {label: 'Group', sticky: true, width: '320px', render: function (row) { const full = groupReliabilityByRef(reliability, row.evidence_ref, row.name) || row; return groupIdentityCell(full, function () { openGroupDetail(row, ops, full, reliability); }); }},
      {label: 'Runs', numeric: true, width: '90px', render: function (row) { const full = groupReliabilityByRef(reliability, row.evidence_ref, row.name) || row; return linkButton(integer(row.runs), function () { openGroupDetail(row, ops, full, reliability); }); }},
      {label: 'Passed', numeric: true, width: '90px', render: function (row) { const full = groupReliabilityByRef(reliability, row.evidence_ref, row.name) || row; return linkButton(integer(row.passed), function () { openGroupDetail(row, ops, full, reliability); }); }},
      {label: 'Failed / soft', numeric: true, width: '120px', render: function (row) { const full = groupReliabilityByRef(reliability, row.evidence_ref, row.name) || row; return linkButton(integer(Number(row.failed || 0) + Number(row.soft_failed || 0)), function () { openGroupDetail(row, ops, full, reliability); }); }},
      {label: 'Mixed-outcome incident rate', numeric: true, width: '210px', render: function (row) { const full = groupReliabilityByRef(reliability, row.evidence_ref, row.name) || row; return linkButton(value(row.incident_rate_pct !== undefined ? row.incident_rate_pct : row.fail_rate) + '%', function () { openGroupDetail(row, ops, full, reliability); }); }},
      {label: 'Evidence', width: '130px', render: function (row) { const full = groupReliabilityByRef(reliability, row.evidence_ref, row.name) || row; return linkButton(integer(evidenceObservations(full).length || full.retained_observation_count) + ' runs', function () { openGroupDetail(row, ops, full, reliability); }); }},
    ], reliability.flaky_candidates || [], null, {name: 'mixed-candidates', minWidth: '960px'})));
    diagnosticGrid.append(panel('Retry recoveries', 'Explicit Buildkite retry chains; log confirmation is a separate evidence level', dataTable([
      {label: 'Build', width: '110px', render: function (r) { return externalLink('#' + value(r.build_number), exactReliabilityBuildUrl(r), 'ops-mono'); }},
      {label: 'Retried job', width: '340px', render: function (r) { return externalLink(r.name, exactPipelineEvidenceUrl({job_url: r.failed_url || r.passed_url, build_number: r.build_number}, 'ci')); }},
      {label: 'Failed attempt', width: '180px', render: function (r) { return externalLink('Open', exactPipelineEvidenceUrl({job_url: r.failed_url, build_number: r.build_number}, 'ci')); }},
      {label: 'Passing retry', width: '180px', render: function (r) { return externalLink('Open', exactPipelineEvidenceUrl({job_url: r.passed_url, build_number: r.build_number}, 'ci')); }},
    ], retry.failed_then_passed_recoveries || [], null, {name: 'retry-recoveries', minWidth: '810px'})));
    host.append(diagnosticGrid);
  }

  async function renderAnalytics(host, ops) {
    const reliability = canonicalReliability(ops);
    const nightly = nightlyForPipeline(ops, state.analyticsPipeline);
    const builds = nightly.builds || [];
    const nightlyName = nightlyDisplayName(nightly, state.analyticsPipeline);
    const retry = reliability.retry_analysis || {};
    const scope = reliabilityScopeInfo(reliability);
    const cohort = reliabilityCohortSummary(reliability);
    const catalog = reliabilityCatalog(reliability);
    add(host, pageHeader('CI Analytics', 'Upstream CI is canonical for test groups, flakes, retries, and latency. Nightly comparisons can switch between upstream and AMD.', ops.generated_at));
    host.append(segmented([
      {id: 'groups', label: 'Test groups'}, {id: 'flakes', label: 'Flakes'},
      {id: 'retries', label: 'Retries'}, {id: 'latency', label: 'Latency'},
      {id: 'nightlies', label: 'Nightly comparisons'},
    ], state.analyticsView, function (id) { setRouteState('ci-analytics', 'analyticsView', id, 'analytics_view'); }, 'CI Analytics view'));
    const retrySummary = retry.summary || {};
    const retryAvailable = retry.available === true;
    const slowest = (((reliability.latency_rankings || {}).by_p90_duration || [])[0] || {});
    const cohortMeta = cohort.nightlies !== null && cohort.otherMain !== null
      ? integer(cohort.nightlies) + ' nightlies + ' + integer(cohort.otherMain) + ' other main'
      : scope.detail;
    host.append(statusStrip([
      {id: 'analytics-scope', label: 'UPSTREAM MAIN BUILDS', value: scope.available ? (cohort.total !== null ? integer(cohort.total) : 'All main') : 'Unavailable', meta: cohortMeta, tone: scope.allMain ? 'is-success' : 'is-warning', description: scope.detail, observed: cohort.observedTo, window: cohort.observedFrom && cohort.observedTo ? shortDate(cohort.observedFrom) + ' to ' + shortDate(cohort.observedTo) : null, provenance: cohort.selection, sources: [{label: 'Open published upstream reliability cohort', url: SOURCE_ASSETS.operations}], onOpen: function () { openMetricDetail({id: 'analytics-scope', label: 'Upstream all-main builds', value: scope.available ? cohort.total : 'Unavailable', meta: cohortMeta, observed: cohort.observedTo, window: cohort.observedFrom && cohort.observedTo ? shortDate(cohort.observedFrom) + ' to ' + shortDate(cohort.observedTo) : null, provenance: cohort.selection, sources: [{label: 'Open published upstream reliability cohort', url: SOURCE_ASSETS.operations}]}); }},
      {id: 'analytics-nightlies', label: 'NIGHTLIES COMPARED', value: integer(builds.length), meta: nightlyName + ' - regression lifecycle only', onOpen: function () { setRouteState('ci-analytics', 'analyticsView', 'nightlies', 'analytics_view'); }},
      {id: 'analytics-retries', label: 'UPSTREAM RETRY ATTEMPTS', value: retryAvailable ? integer(retrySummary.retry_attempt_count) : 'Unavailable', meta: retryAvailable ? integer(retrySummary.failed_then_passed_recovery_count) + ' recovered fail-to-pass chains' : 'complete explicit retry ledger not retained', tone: retryAvailable && Number(retrySummary.failed_then_passed_recovery_count) ? 'is-warning' : 'is-neutral', onOpen: function () { setRouteState('ci-analytics', 'analyticsView', 'retries', 'analytics_view'); }},
      {id: 'analytics-slowest', label: 'UPSTREAM SLOWEST P90', value: duration(slowest.p90_dur), meta: value(slowest.name, 'No duration data'), onOpen: function () { slowest.name ? openGroupDetail(slowest, ops, slowest, reliability) : setRouteState('ci-analytics', 'analyticsView', 'latency', 'analytics_view'); }},
    ]));

    if (!scope.available && state.analyticsView !== 'nightlies') {
      const unavailable = n('div', 'ops-evidence-note is-warning');
      add(unavailable, [n('strong', '', 'Upstream reliability unavailable. '), n('span', '', scope.detail + '. No AMD or nightly history has been substituted.')]);
      host.append(unavailable);
      return;
    }

    if (state.analyticsView === 'groups') {
      const note = n('div', 'ops-evidence-note ' + (scope.allMain ? 'is-success' : 'is-info'));
      add(note, [n('strong', '', 'Canonical upstream reliability. '), n('span', '', scope.detail + '. Mixed outcomes are candidates for investigation, not a test-case flake probability.')]);
      host.append(note);
      const toolbar = n('div', 'ops-toolbar');
      const search = n('input', 'ops-input');
      search.type = 'search';
      search.placeholder = 'Search test-group history';
      search.value = state.analyticsSearch;
      search.setAttribute('aria-label', 'Search reliability test groups');
      search.addEventListener('change', function () {
        state.analyticsSearch = search.value;
        setQueryValue('analytics_search', state.analyticsSearch);
        render('ci-analytics', true);
      });
      toolbar.append(search);
      host.append(toolbar);
      const query = normalizeLabel(state.analyticsSearch);
      const rows = catalog.filter(function (row) { return !query || normalizeLabel(row.name).includes(query); });
      host.append(dataTable([
        {label: 'Test group', sticky: true, width: '340px', render: function (row) { return groupIdentityCell(row, function () { openGroupDetail(row, ops, row, reliability); }); }},
        {label: 'Runs', numeric: true, width: '90px', render: function (row) { return linkButton(integer(row.runs !== undefined ? row.runs : row.observation_count), function () { openGroupDetail(row, ops, row, reliability); }); }},
        {label: 'Latest result', width: '150px', render: function (row) { const latest = latestObservation(row); return linkedBadge(latest ? observationState(latest) : 'Evidence pending', exactPipelineEvidenceUrl(latest, 'ci'), function () { openGroupDetail(row, ops, row, reliability); }); }},
        {label: 'Incident rate', numeric: true, width: '130px', render: function (row) { return linkButton(Number.isFinite(Number(row.fail_rate)) ? Number(row.fail_rate).toFixed(1) + '%' : '-', function () { openGroupDetail(row, ops, row, reliability); }); }},
        {label: 'Median', numeric: true, width: '100px', render: function (row) { return linkButton(duration(row.median_dur), function () { openGroupDetail(row, ops, row, reliability); }); }},
        {label: 'p90', numeric: true, width: '100px', render: function (row) { return linkButton(duration(row.p90_dur), function () { openGroupDetail(row, ops, row, reliability); }); }},
        {label: 'History', width: '170px', render: function (row) { return linkButton('Timeline - ' + integer(evidenceObservations(row).length || row.observation_count || row.runs) + ' runs', function () { openGroupDetail(row, ops, row, reliability); }, 'Open all-main and nightly history for ' + value(row.name)); }},
      ], rows, integer(rows.length) + ' upstream test groups in ' + scope.label.toLowerCase(), {name: 'test-groups', minWidth: '1060px'}));
      return;
    }

    if (state.analyticsView === 'flakes') {
      const candidates = reliability.flaky_candidates || [];
      const note = n('div', 'ops-evidence-note is-info');
      add(note, [n('strong', '', 'Upstream mixed-outcome history. '), n('span', '', 'These are investigation candidates from upstream branch=main observations, not test-case flake probabilities.')]);
      host.append(note);
      host.append(dataTable([
        {label: 'Group', sticky: true, width: '320px', render: function (row) { const full = groupReliabilityByRef(reliability, row.evidence_ref, row.name) || row; return groupIdentityCell(full, function () { openGroupDetail(row, ops, full, reliability); }); }},
        {label: 'Runs', numeric: true, width: '90px', render: function (row) { const full = groupReliabilityByRef(reliability, row.evidence_ref, row.name) || row; return linkButton(integer(row.runs), function () { openGroupDetail(row, ops, full, reliability); }); }},
        {label: 'Passed', numeric: true, width: '90px', render: function (row) { const full = groupReliabilityByRef(reliability, row.evidence_ref, row.name) || row; return linkButton(integer(row.passed), function () { openGroupDetail(row, ops, full, reliability); }); }},
        {label: 'Failed / soft', numeric: true, width: '120px', render: function (row) { const full = groupReliabilityByRef(reliability, row.evidence_ref, row.name) || row; return linkButton(integer(Number(row.failed || 0) + Number(row.soft_failed || 0)), function () { openGroupDetail(row, ops, full, reliability); }); }},
        {label: 'Mixed-outcome incident rate', numeric: true, width: '210px', render: function (row) { const full = groupReliabilityByRef(reliability, row.evidence_ref, row.name) || row; return linkButton(value(row.incident_rate_pct !== undefined ? row.incident_rate_pct : row.fail_rate) + '%', function () { openGroupDetail(row, ops, full, reliability); }); }},
        {label: 'Evidence', width: '130px', render: function (row) { const full = groupReliabilityByRef(reliability, row.evidence_ref, row.name) || row; return linkButton(integer(evidenceObservations(full).length || full.retained_observation_count) + ' runs', function () { openGroupDetail(row, ops, full, reliability); }); }},
      ], candidates, integer(candidates.length) + ' upstream mixed-outcome candidates', {name: 'mixed-candidates', minWidth: '960px'}));
      return;
    }

    if (state.analyticsView === 'nightlies') {
      const controls = n('div', 'ops-toolbar ops-analytics-nightly-toolbar');
      controls.append(segmented([
        {id: 'ci', label: 'Upstream'},
        {id: 'amd-ci', label: 'AMD'},
      ], state.analyticsPipeline, function (pipeline) { setRouteState('ci-analytics', 'analyticsPipeline', pipeline, 'analytics_pipeline'); }, 'Nightly pipeline'));
      host.append(controls);
      const cp = chartPanel(nightlyName + ' nightly regressions', 'New, recurring, and fixed group observations per completed nightly', 'analytics-trend');
      host.append(cp.root);
      drawChart('analytics-trend', cp.canvas, {type: 'bar', data: {
        labels: builds.slice().reverse().map(function (b) { return '#' + b.number; }),
        datasets: [
          {label: 'New', data: builds.slice().reverse().map(function (b) { return (b.transitions.new || []).length; }), backgroundColor: '#e06464'},
          {label: 'Recurring', data: builds.slice().reverse().map(function (b) { return (b.transitions.recurring || []).length; }), backgroundColor: '#e3a63a'},
          {label: 'Fixed', data: builds.slice().reverse().map(function (b) { return (b.transitions.fixed || []).length; }), backgroundColor: '#35bb78'},
        ],
      }, options: {scales: {x: {stacked: true}, y: {stacked: true, beginAtZero: true}}},
      evidenceTitle: nightlyName + ' nightly regression comparison history',
      evidence: builds.slice().reverse().map(function (buildRow) { return {label: '#' + buildRow.number, timestamp: buildRow.created_at, url: exactPipelineBuildUrl(buildRow, state.analyticsPipeline), valueSummary: integer((buildRow.transitions.new || []).length) + ' new', details: {state: buildRow.state, new: (buildRow.transitions.new || []).length, recurring: (buildRow.transitions.recurring || []).length, fixed: (buildRow.transitions.fixed || []).length}}; })});
      host.append(dataTable([
        {label: nightlyName + ' nightly', sticky: true, width: '130px', render: function (r) { return externalLink('#' + r.number, exactPipelineBuildUrl(r, state.analyticsPipeline), 'ops-mono'); }},
        {label: 'State', width: '120px', render: function (r) { return linkedBadge(r.state, exactPipelineBuildUrl(r, state.analyticsPipeline)); }},
        {label: 'Observed groups', numeric: true, width: '140px', render: function (r) { return linkButton(integer(r.total_groups), function () { openBuildDetail(r, nightlyName + ' build #' + value(r.number)); }); }},
        {label: 'Hard fail', numeric: true, width: '100px', render: function (r) { return linkButton(integer((r.failed_groups || []).length), function () { openBuildDetail(r, nightlyName + ' build #' + value(r.number)); }); }},
        {label: 'Soft fail', numeric: true, width: '100px', render: function (r) { return linkButton(integer((r.soft_failed_groups || []).length), function () { openBuildDetail(r, nightlyName + ' build #' + value(r.number)); }); }},
        {label: 'New', numeric: true, width: '80px', render: function (r) { return linkButton(integer((r.transitions.new || []).length), function () { openBuildDetail(r, nightlyName + ' build #' + value(r.number)); }); }},
        {label: 'Recurring', numeric: true, width: '100px', render: function (r) { return linkButton(integer((r.transitions.recurring || []).length), function () { openBuildDetail(r, nightlyName + ' build #' + value(r.number)); }); }},
        {label: 'Fixed', numeric: true, width: '80px', render: function (r) { return linkButton(integer((r.transitions.fixed || []).length), function () { openBuildDetail(r, nightlyName + ' build #' + value(r.number)); }); }},
        {label: 'Started', width: '180px', render: function (r) { return shortDate(r.created_at); }},
      ], builds, nightlyName + ' nightly comparisons; this selector does not change canonical upstream reliability', {name: 'nightly', minWidth: '1030px'}));
      return;
    }

    if (state.analyticsView === 'retries') {
      if (retry.available !== true) {
        const unavailable = n('div', 'ops-evidence-note is-warning');
        add(unavailable, [n('strong', '', 'Upstream retry ledger unavailable. '), n('span', '', ((retry.provenance || {}).reason || 'Complete explicit Buildkite retry metadata was not retained; compacted group history was not substituted.'))]);
        host.append(unavailable);
        return;
      }
      const attemptsTable = dataTable([
        {label: 'Build', width: '110px', render: function (r) { return externalLink('#' + value(r.build_number), exactReliabilityBuildUrl(r), 'ops-mono'); }},
        {label: 'Retried job', sticky: true, width: '360px', render: function (r) { return externalLink(r.name || 'Unnamed retry', exactPipelineEvidenceUrl(r, 'ci')); }},
        {label: 'Result', width: '120px', render: function (r) { return linkedBadge(r.state || 'unknown', exactPipelineEvidenceUrl(r, 'ci')); }},
        {label: 'Retry type', width: '130px', render: function (r) { return linkedBadge(r.retry_type || 'explicit', exactPipelineEvidenceUrl(r, 'ci'), null, 'is-info'); }},
        {label: 'Job ID', width: '300px', render: function (r) { return externalLink(value(r.job_id), exactPipelineEvidenceUrl(r, 'ci'), 'ops-mono'); }},
        {label: 'Evidence', width: '170px', render: function (r) { return externalLink('Open exact attempt', exactPipelineEvidenceUrl(r, 'ci')); }},
      ], retry.retry_attempts || [], integer((retry.retry_attempts || []).length) + ' upstream explicit attempts; every row links to its exact Buildkite job', {name: 'retry-attempts', minWidth: '1190px'});
      host.append(panel('Upstream explicit retry attempts', integer((retry.retry_attempts || []).length) + ' Buildkite attempts across ' + integer(retrySummary.builds_with_retries) + ' builds', attemptsTable));
      const recoveryTable = dataTable([
        {label: 'Build', width: '110px', render: function (r) { return externalLink('#' + value(r.build_number), exactReliabilityBuildUrl(r), 'ops-mono'); }},
        {label: 'Retried job', sticky: true, width: '390px', render: function (r) { return externalLink(r.name || 'Unnamed retry chain', exactPipelineEvidenceUrl({job_url: r.failed_url || r.passed_url, build_number: r.build_number}, 'ci')); }},
        {label: 'Failed attempt', width: '190px', render: function (r) { return externalLink('Open failed log', exactPipelineEvidenceUrl({job_url: r.failed_url, build_number: r.build_number}, 'ci')); }},
        {label: 'Passing retry', width: '190px', render: function (r) { return externalLink('Open passing log', exactPipelineEvidenceUrl({job_url: r.passed_url, build_number: r.build_number}, 'ci')); }},
      ], retry.failed_then_passed_recoveries || [], integer((retry.failed_then_passed_recoveries || []).length) + ' upstream recovered chains, separate from the full attempt ledger', {name: 'retry-recoveries', minWidth: '880px'});
      host.append(panel('Upstream recovered fail-to-pass chains', integer((retry.failed_then_passed_recoveries || []).length) + ' chains confirmed by explicit retry metadata', recoveryTable));
      return;
    }

    const latencyRows = (reliability.latency_rankings || {}).by_p90_duration || [];
    host.append(dataTable([
      {label: 'Test group', sticky: true, width: '340px', render: function (r) { const full = groupReliabilityByRef(reliability, r.evidence_ref); return groupIdentityCell(full || r, function () { if (full) openGroupDetail(r, ops, full, reliability); }); }},
      {label: 'Runs', numeric: true, width: '90px', render: function (r) { const full = groupReliabilityByRef(reliability, r.evidence_ref); return linkButton(integer(r.runs), function () { if (full) openGroupDetail(r, ops, full, reliability); }, 'Inspect run history for evidence ID ' + value(r.evidence_ref)); }},
      {label: 'Median completion', numeric: true, width: '150px', render: function (r) { const full = groupReliabilityByRef(reliability, r.evidence_ref); return linkButton(duration(r.median_dur), function () { if (full) openGroupDetail(r, ops, full, reliability); }, 'Inspect median completion evidence for ID ' + value(r.evidence_ref)); }},
      {label: 'p90 completion', numeric: true, width: '140px', render: function (r) { const full = groupReliabilityByRef(reliability, r.evidence_ref); return linkButton(duration(r.p90_dur), function () { if (full) openGroupDetail(r, ops, full, reliability); }, 'Inspect p90 completion evidence for ID ' + value(r.evidence_ref)); }},
      {label: 'Maximum', numeric: true, width: '120px', render: function (r) { const full = groupReliabilityByRef(reliability, r.evidence_ref); return linkButton(duration(r.max_dur), function () { if (full) openGroupDetail(r, ops, full, reliability); }, 'Inspect maximum completion evidence for ID ' + value(r.evidence_ref)); }},
      {label: 'Incident rate', numeric: true, width: '130px', render: function (r) { const full = groupReliabilityByRef(reliability, r.evidence_ref); return linkButton(Number.isFinite(Number(r.fail_rate)) ? Number(r.fail_rate).toFixed(1) + '%' : '-', function () { if (full) openGroupDetail(r, ops, full, reliability); }, 'Inspect incident evidence for ID ' + value(r.evidence_ref)); }},
      {label: 'Queues', width: '220px', render: function (r) { const names = r.queues || []; const wrap = n('div', 'ops-inline-links'); names.forEach(function (name) { wrap.append(linkButton(name, function () { navigateTo('ci-queue', {queueView: 'current'}); })); }); return names.length ? wrap : n('span', 'ops-cell-muted', '-'); }},
      {label: 'Evidence', width: '150px', render: function (r) { const rel = groupReliabilityByRef(reliability, r.evidence_ref); return rel ? linkButton(integer(evidenceObservations(rel).length) + ' runs', function () { openGroupDetail(r, ops, rel, reliability); }, 'Inspect exact observations for evidence ID ' + value(r.evidence_ref)) : externalLink('Published ranking', SOURCE_ASSETS.operations); }},
    ], latencyRows, 'Upstream completion time per test group in ' + scope.label.toLowerCase() + '; queue wait is separate when reported', {name: 'latency', minWidth: '1340px'}));
  }

  async function renderPerf(host, ops) {
    const perf = await fetchJSON('data/vllm/perf_eval/perf_eval.json');
    const models = Array.isArray(perf.models) ? perf.models : [];
    const summary = perf.summary || {};
    const pipeline = externalLink('Open perf-eval pipeline', (perf.pipeline || {}).url || 'https://buildkite.com/vllm/perf-eval', 'ops-button');
    add(host, pageHeader('Performance & Evaluation', 'Artifact-backed AMD nightly throughput, latency, and accuracy with build and commit provenance.', perf.generated_at || ops.generated_at, pipeline));
    host.append(statusStrip([
      {id: 'perf-models', label: 'AMD MODELS', value: integer(summary.models !== undefined ? summary.models : models.length), meta: 'nightly model families', onOpen: function () { openHistoryEvidence('AMD model families', models.map(function (model) { return {label: model.model, valueSummary: integer(model.nightly_count) + ' nightlies', url: (model.latest || {}).build_url}; })); }},
      {id: 'perf-nightlies', label: 'NIGHTLIES TRACKED', value: integer(summary.nightlies), meta: 'across retained series', url: (perf.pipeline || {}).url || 'https://buildkite.com/vllm/perf-eval'},
      {id: 'perf-points', label: 'METRIC POINTS', value: integer(Number(summary.perf_points || 0) + Number(summary.accuracy_points || 0)), meta: integer(summary.perf_points) + ' performance - ' + integer(summary.accuracy_points) + ' accuracy', onOpen: function () { openMetricDetail({label: 'Retained metric points', value: Number(summary.perf_points || 0) + Number(summary.accuracy_points || 0), meta: 'Performance and accuracy points are inspectable from their model histories.', provenance: 'perf_eval.json'}); }},
      {id: 'perf-hardware', label: 'AMD HARDWARE', value: (summary.amd_devices || []).map(function (device) { return String(device).toUpperCase(); }).join(' / ') || '-', meta: 'ROCm only', onOpen: function () { openMetricDetail({label: 'AMD performance hardware', value: (summary.amd_devices || []).join(', ') || '-', meta: 'Hardware declared by retained AMD workload records.'}); }},
    ]));

    const toolbar = n('div', 'ops-toolbar ops-perf-toolbar');
    toolbar.append(segmented([{id: 'performance', label: 'Performance'}, {id: 'accuracy', label: 'Accuracy'}], state.perfView, function (id) {
      setRouteState('ci-perf-eval', 'perfView', id, 'perf_view');
    }, 'Performance and accuracy view'));
    toolbar.append(n('div', 'ops-toolbar-spacer'));

    const modelField = n('label', 'ops-field ops-perf-filter');
    modelField.append(n('span', 'ops-field-label', 'Model'));
    const modelSelect = n('select', 'ops-select');
    const allModels = n('option', '', 'All models');
    allModels.value = 'all';
    modelSelect.append(allModels);
    models.forEach(function (model) {
      const option = n('option', '', model.model);
      option.value = model.model;
      modelSelect.append(option);
    });
    modelSelect.value = state.perfModel;
    modelSelect.addEventListener('change', function () { state.perfModel = modelSelect.value; render('ci-perf-eval', true); });
    modelField.append(modelSelect);
    toolbar.append(modelField);

    const devices = summary.amd_devices || Array.from(new Set(models.flatMap(function (model) { return model.devices || []; })));
    if (devices.length) {
      const deviceField = n('div', 'ops-field ops-perf-filter');
      deviceField.append(n('span', 'ops-field-label', 'Hardware'));
      deviceField.append(segmented([{id: 'all', label: 'All'}].concat(devices.map(function (device) {
        return {id: String(device).toLowerCase(), label: String(device).toUpperCase()};
      })), state.perfDevice, function (id) { state.perfDevice = id; render('ci-perf-eval', true); }, 'Performance hardware filter'));
      toolbar.append(deviceField);
    }
    host.append(toolbar);

    const filteredModels = models.filter(function (model) {
      if (state.perfModel !== 'all' && model.model !== state.perfModel) return false;
      if (state.perfDevice !== 'all' && !(model.devices || []).some(function (device) { return String(device).toLowerCase() === state.perfDevice; })) return false;
      return true;
    });
    if (!filteredModels.length) {
      host.append(n('div', 'ops-empty', 'No model matches the selected filters.'));
      return;
    }

    for (const [key, chart] of charts.entries()) {
      if (key.startsWith('perf-')) {
        chart.destroy();
        charts.delete(key);
      }
    }

    if (state.perfView === 'accuracy') {
      const rows = accuracyRows(filteredModels);
      host.append(panel('Accuracy observations', 'lm-eval tasks; higher is better unless the source payload says otherwise', dataTable([
        {label: 'Model', sticky: true, render: function (row) { return linkButton(row.model.model, function () { const block = Object.assign({}, row.task, {label: row.task.task + ' - ' + row.task.metric, unit: 'ratio', direction: row.task.direction || 'higher'}); openPerfHistory(row.model, {label: 'Accuracy'}, row.task.metric, block); }); }},
        {label: 'Task', render: function (row) { return linkButton(row.task.task, function () { const block = Object.assign({}, row.task, {label: row.task.task + ' - ' + row.task.metric, unit: 'ratio', direction: row.task.direction || 'higher'}); openPerfHistory(row.model, {label: 'Accuracy'}, row.task.metric, block); }); }},
        {label: 'Metric', render: function (row) { return linkButton(row.task.metric, function () { const block = Object.assign({}, row.task, {label: row.task.task + ' - ' + row.task.metric, unit: 'ratio', direction: row.task.direction || 'higher'}); openPerfHistory(row.model, {label: 'Accuracy'}, row.task.metric, block); }); }},
        {label: 'Latest', numeric: true, render: function (row) { return linkButton(perfValue(row.task.latest, 'ratio'), function () { const block = Object.assign({}, row.task, {label: row.task.task + ' - ' + row.task.metric, unit: 'ratio', direction: row.task.direction || 'higher'}); openPerfHistory(row.model, {label: 'Accuracy'}, row.task.metric, block); }); }},
        {label: 'vs previous', numeric: true, render: function (row) { const control = linkButton(perfDelta(row.task).textContent, function () { const block = Object.assign({}, row.task, {label: row.task.task + ' - ' + row.task.metric, unit: 'ratio', direction: row.task.direction || 'higher'}); openPerfHistory(row.model, {label: 'Accuracy'}, row.task.metric, block); }); return control; }},
        {label: 'Status', render: function (row) { return linkedBadge(row.task.status === 'good' ? 'improved' : row.task.status === 'bad' ? 'regressed' : 'within band', (row.model.latest || {}).build_url, null, row.task.status === 'good' ? 'is-success' : row.task.status === 'bad' ? 'is-danger' : 'is-neutral'); }},
        {label: 'Latest build', render: function (row) { return externalLink(row.model.latest && row.model.latest.build_number ? '#' + row.model.latest.build_number : 'Build', row.model.latest && row.model.latest.build_url, 'ops-mono'); }},
        {label: 'History', render: function (row) {
          const block = Object.assign({}, row.task, {label: row.task.task + ' - ' + row.task.metric, unit: 'ratio', direction: row.task.direction || 'higher'});
          return linkButton(integer((row.task.series || []).length) + ' points', function () { openPerfHistory(row.model, {label: 'Accuracy'}, row.task.metric, block); });
        }},
      ], rows, integer(rows.length) + ' AMD accuracy task metrics'), 'ops-perf-accuracy'));
      return;
    }

    const chartQueue = [];
    const stack = n('div', 'ops-stack ops-perf-models');
    filteredModels.forEach(function (model, index) { stack.append(perfModelSection(model, index, chartQueue)); });
    host.append(stack);
    requestAnimationFrame(function () {
      chartQueue.forEach(function (item) { drawPerfSpark(item.key, item.canvas, item.series, item.status, item.unit); });
    });
  }

  function queueIsActiveProblem(row) {
    return Number(row.waiting || 0) > 0 || Number(row.running || 0) > 0 || Number(row.zombie_waiting || 0) > 0 || Number(row.zombie_running || 0) > 0
      || ['warning', 'critical', 'degraded'].includes(String(row.status || '').toLowerCase());
  }

  function selectedQueues(snapshot, includeIdle) {
    return Object.entries(snapshot.queues || {}).filter(function (entry) {
      if (isRetiredQueue(entry[0])) return false;
      if (state.queueScope !== 'all' && !isAmdQueue(entry[0])) return false;
      return includeIdle || queueIsActiveProblem(entry[1] || {});
    });
  }

  function waitValue(row, metric) {
    const current = row.current_wait || {};
    if (current[metric] && current[metric].value !== undefined) return current[metric].value;
    if (metric === 'p99' && row.p99_wait_source !== 'sample_wait') return null;
    return row[metric + '_wait'];
  }

  function waitSource(row, metric) {
    const current = row.current_wait || {};
    const source = (current[metric] && current[metric].source) || row[metric + '_wait_source'] || row.wait_source || null;
    return source && !['none', 'unavailable', 'unknown'].includes(String(source).toLowerCase()) ? source : null;
  }

  function hasAgentMeasurement(row) {
    if (row.connected_agents === null || row.connected_agents === undefined || row.connected_agents === '' || !Number.isFinite(Number(row.connected_agents))) return false;
    if (row.connected_agents_available !== undefined) return row.connected_agents_available === true;
    const source = String(row.connected_agents_source || row.agent_count_source || row.metrics_source || row.count_source || '').toLowerCase();
    return !!source && !['active_jobs', 'webhook', 'job_scan', 'none', 'unknown'].includes(source);
  }

  function openQueueSnapshotDetail(snapshot, totals) {
    const source = snapshot.sources || snapshot.provenance || {};
    const rows = selectedQueues(snapshot, false).map(function (entry) { return {name: entry[0], row: entry[1]}; });
    const content = rows.length ? dataTable([
      {label: 'Queue', sticky: true, render: function (item) { return linkButton(item.name, function () { openQueueDetail(item.name, item.row, []); }); }},
      {label: 'Running', numeric: true, render: function (item) { return integer(item.row.running); }},
      {label: 'Waiting', numeric: true, render: function (item) { return integer(item.row.waiting); }},
      {label: 'Count source', render: function (item) { return value(item.row.count_source || source.counts || source.mode); }},
    ], rows, integer(rows.length) + ' active or problem queues in this snapshot') : n('div', 'ops-empty', 'No active or problem queues in this snapshot.');
    openDetailDrawer({
      id: 'queue-snapshot-' + value(snapshot.ts),
      title: 'Queue snapshot',
      subtitle: shortDate(snapshot.ts),
      description: 'Point-in-time fleet counts. Wait percentiles appear only when the source supplied them.',
      fields: [
        {label: 'Running', value: integer(totals.running)},
        {label: 'Waiting', value: integer(totals.waiting)},
        {label: 'Queues in scope', value: integer(totals.queues)},
        {label: 'Provenance', value: source.mode || source.counts || source.waits || 'retained snapshot'},
      ],
      sources: [
        {label: 'Open published queue history', url: SOURCE_ASSETS.queueHistory},
        {label: 'Open current operations snapshot', url: SOURCE_ASSETS.operations},
      ],
      content: content,
    });
  }

  function queueWaitHistoryPoint(snapshot, queueName) {
    const entries = queueName === 'fleet'
      ? selectedQueues(snapshot, true)
      : Object.prototype.hasOwnProperty.call(snapshot.queues || {}, queueName)
        ? [[queueName, (snapshot.queues || {})[queueName]]]
        : [];
    function highest(metric) {
      return entries.map(function (entry) {
        return {queue: entry[0], value: waitValue(entry[1] || {}, metric), source: waitSource(entry[1] || {}, metric)};
      }).filter(function (row) {
        return row.value !== null && row.value !== undefined && Number.isFinite(Number(row.value));
      }).sort(function (a, b) { return Number(b.value) - Number(a.value); })[0] || {};
    }
    const p50 = highest('p50'), p95 = highest('p95'), p99 = highest('p99');
    return {
      ts: snapshot.ts,
      snapshot: snapshot,
      p50: p50.value !== undefined ? Number(p50.value) : null,
      p95: p95.value !== undefined ? Number(p95.value) : null,
      p99: p99.value !== undefined ? Number(p99.value) : null,
      p50Queue: p50.queue,
      p95Queue: p95.queue,
      p99Queue: p99.queue,
      p50Source: p50.source,
      p95Source: p95.source,
      p99Source: p99.source,
    };
  }

  function queuePressureRows(snapshot, history) {
    return selectedQueues(snapshot, true).map(function (entry) {
      const name = entry[0], currentRow = entry[1] || {};
      const loads = (history || []).filter(function (point) { return point && point.ts !== snapshot.ts; }).map(function (point) {
        const row = ((point.queues || {})[name]);
        return row ? Number(row.running || 0) + Number(row.waiting || 0) : null;
      }).filter(function (load) { return Number.isFinite(load); });
      const current = Number(currentRow.running || 0) + Number(currentRow.waiting || 0);
      const baselineMedian = percentileValue(loads, 0.5);
      const baselineP95 = percentileValue(loads, 0.95);
      const pressureRatio = Number(baselineP95) > 0 ? current / Number(baselineP95) : current > 0 ? null : 0;
      return {
        name: name,
        row: currentRow,
        current: current,
        running: Number(currentRow.running || 0),
        waiting: Number(currentRow.waiting || 0),
        baselineMedian: baselineMedian,
        baselineP95: baselineP95,
        pressureRatio: pressureRatio,
        historyPoints: loads.length,
        elevated: baselineP95 !== null && current > Number(baselineP95),
      };
    }).filter(function (row) {
      return row.current > 0 || Number(row.baselineP95 || 0) > 0;
    }).sort(function (a, b) {
      if (a.elevated !== b.elevated) return a.elevated ? -1 : 1;
      return Number(b.current || 0) - Number(a.current || 0);
    });
  }

  async function renderQueue(host, ops) {
    const queueBlock = ops.queue || {};
    const snapshot = queueBlock.snapshot || {};
    const allScopeEntries = selectedQueues(snapshot, true);
    const entries = selectedQueues(snapshot, state.queueIncludeIdle);
    const sums = allScopeEntries.reduce(function (a, item) {
      const queueRow = item[1] || {};
      a.waiting += Number(queueRow.waiting || 0);
      a.running += Number(queueRow.running || 0);
      if (hasAgentMeasurement(queueRow)) {
        a.agents += Number(queueRow.connected_agents);
        a.agentMeasurements += 1;
      }
      if (queueRow.count_source) a.countSources.add(queueRow.count_source);
      return a;
    }, {waiting: 0, running: 0, agents: 0, agentMeasurements: 0, countSources: new Set()});
    const snapshotSources = snapshot.sources || snapshot.provenance || (((queueBlock.provenance || {}).snapshot || {}).sources) || {};
    const countProvenance = Array.from(sums.countSources).join(', ') || snapshotSources.counts || snapshotSources.count_source || snapshotSources.mode || 'source unavailable';
    function highest(metric) {
      const vals = allScopeEntries.map(function (e) { return {queue: e[0], value: waitValue(e[1], metric), source: waitSource(e[1], metric), row: e[1]}; }).filter(function (r) { return r.value !== null && r.value !== undefined && r.value !== '' && Number.isFinite(Number(r.value)); });
      return vals.sort(function (a, b) { return Number(b.value) - Number(a.value); })[0] || {};
    }
    const p50 = highest('p50'), p95 = highest('p95'), p99 = highest('p99');
    const waitLeader = p95.queue ? Object.assign({metric: 'p95'}, p95) : p50.queue ? Object.assign({metric: 'p50'}, p50) : p99.queue ? Object.assign({metric: 'p99 sampled'}, p99) : {};
    add(host, pageHeader('Queue Monitor', 'Current fleet state, retained snapshots, and exact active jobs with source-aware wait metrics.', snapshot.ts));
    const controls = n('div', 'ops-toolbar ops-queue-toolbar');
    controls.append(segmented([{id: 'current', label: 'Current'}, {id: 'history', label: 'History'}, {id: 'jobs', label: 'Jobs'}], state.queueView, function (id) { setRouteState('ci-queue', 'queueView', id, 'queue_view'); }, 'Queue monitor mode'));
    controls.append(segmented([{id: 'amd', label: 'AMD queues'}, {id: 'all', label: 'All queues'}], state.queueScope, function (id) { setRouteState('ci-queue', 'queueScope', id, 'queue_scope'); }, 'Queue hardware scope'));
    if (state.queueView === 'history') controls.append(segmented([{id: '24h', label: '24h'}, {id: '7d', label: '7d'}, {id: '30d', label: '30d'}], state.queueRange, function (id) { setRouteState('ci-queue', 'queueRange', id, 'queue_range'); }, 'Queue history range'));
    if (state.queueView === 'current') {
      const idleLabel = n('label', 'ops-toggle');
      const idle = n('input'); idle.type = 'checkbox'; idle.checked = state.queueIncludeIdle;
      idle.addEventListener('change', function () { state.queueIncludeIdle = idle.checked; render('ci-queue', true); });
      add(idleLabel, [idle, n('span', '', 'Include idle')]);
      controls.append(idleLabel);
    }
    host.append(controls);
    host.append(statusStrip([
      {id: 'queue-running', label: 'RUNNING NOW', value: integer(sums.running), meta: allScopeEntries.length + ' queues in scope', onOpen: function () { setRouteState('ci-queue', 'queueView', 'jobs', 'queue_view'); }},
      {id: 'queue-waiting', label: 'WAITING NOW', value: integer(sums.waiting), meta: 'count source: ' + countProvenance, tone: sums.waiting ? 'is-warning' : 'is-success', provenance: countProvenance, onOpen: function () { setRouteState('ci-queue', 'queueView', 'jobs', 'queue_view'); }},
      {id: 'queue-agents', label: 'CONNECTED AGENTS', value: sums.agentMeasurements ? integer(sums.agents) : '-', meta: sums.agentMeasurements ? sums.agentMeasurements + ' queues reported agent counts' : 'Unavailable in current source', observed: snapshot.ts, tone: sums.agentMeasurements ? 'is-neutral' : 'is-warning', onOpen: function () { openMetricDetail({label: 'Connected agents', value: sums.agentMeasurements ? sums.agents : '-', meta: sums.agentMeasurements ? sums.agentMeasurements + ' measured queues' : 'No queue has an actual agent measurement; missing values are not zero.', provenance: countProvenance}); }},
      {id: 'queue-wait-leader', label: 'HIGHEST REPORTED WAIT', value: waitLeader.queue ? duration(waitLeader.value) : '-', meta: waitLeader.queue ? waitLeader.metric + ' - ' + waitLeader.queue + ' - ' + value(waitLeader.source) : 'Unavailable in current source', tone: waitLeader.queue ? 'is-warning' : 'is-neutral', onOpen: function () { waitLeader.queue ? openQueueDetail(waitLeader.queue, waitLeader.row, []) : openMetricDetail({label: 'Highest reported wait', value: '-', meta: 'No source reported a current wait percentile. Missing values are not zero.'}); }},
    ]));

    const jobs = queueBlock.queue_jobs || {};
    const activeJobs = (jobs.pending || []).concat(jobs.running || []).filter(function (job) {
      if (isRetiredQueue(job.queue)) return false;
      return state.queueScope === 'all' || isAmdQueue(job.queue);
    });

    if (state.queueView === 'current') {
      const pressureRows = queuePressureRows(snapshot, Array.isArray(queueBlock.history) ? queueBlock.history : []);
      if (pressureRows.length) {
        const pressureTop = pressureRows.slice(0, 14);
        const pressureChart = chartPanel('Queue pressure against retained baseline', 'Current running + waiting versus each queue\'s historical p95 load', 'queue-pressure');
        host.append(pressureChart.root);
        requestAnimationFrame(function () {
          drawChart('queue-pressure', pressureChart.canvas, {
            type: 'bar',
            data: {
              labels: pressureTop.map(function (row) { return row.name; }),
              datasets: [
                {label: 'Current load', data: pressureTop.map(function (row) { return row.current; }), backgroundColor: '#22b8ad', borderColor: pressureTop.map(function (row) { return row.elevated ? '#e06464' : '#22b8ad'; }), borderWidth: pressureTop.map(function (row) { return row.elevated ? 2 : 0; })},
                {label: 'Historical p95', data: pressureTop.map(function (row) { return row.baselineP95; }), backgroundColor: '#66717d'},
              ],
            },
            options: {indexAxis: 'y', scales: {x: {beginAtZero: true, title: {display: true, text: 'Running + waiting jobs'}}, y: {grid: {display: false}}}},
            evidenceTitle: 'Queue pressure versus historical p95',
            evidenceAsset: SOURCE_ASSETS.queueHistory,
            evidence: pressureTop.map(function (row) { return {label: row.name, timestamp: snapshot.ts, valueSummary: integer(row.current) + ' current - ' + integer(row.baselineP95) + ' historical p95', details: {running: row.running, waiting: row.waiting, historical_median: row.baselineMedian, historical_p95: row.baselineP95, retained_snapshots: row.historyPoints}, sources: [{label: 'Open published queue history', url: SOURCE_ASSETS.queueHistory}], onOpen: function () { openQueueDetail(row.name, row.row, activeJobs); }}; }),
          });
        });
        const elevatedRows = pressureRows.filter(function (row) { return row.elevated; });
        if (elevatedRows.length) host.append(n('div', 'ops-evidence-note is-warning', integer(elevatedRows.length) + ' queues are above their retained historical p95 concurrent load. Select a queue in History to inspect its wait-time trend.'));
      }
      host.append(dataTable([
        {label: 'Queue', sticky: true, render: function (item) { const name = item[0], row = item[1]; return row.queue_url ? externalLink(name, row.queue_url, 'ops-mono') : linkButton(name, function () { openQueueDetail(name, row, activeJobs); }); }},
        {label: 'Running', numeric: true, render: function (item) { return linkButton(integer(item[1].running), function () { openQueueDetail(item[0], item[1], activeJobs); }); }},
        {label: 'Waiting', numeric: true, render: function (item) { return linkButton(integer(item[1].waiting), function () { openQueueDetail(item[0], item[1], activeJobs); }); }},
        {label: 'Agents', numeric: true, render: function (item) { return linkButton(hasAgentMeasurement(item[1]) ? integer(item[1].connected_agents) : '-', function () { openQueueDetail(item[0], item[1], activeJobs); }); }},
        {label: 'p50', numeric: true, render: function (item) { return linkButton(duration(waitValue(item[1], 'p50')), function () { openQueueDetail(item[0], item[1], activeJobs); }); }},
        {label: 'p95', numeric: true, render: function (item) { const source = waitSource(item[1], 'p95'); return linkButton(duration(waitValue(item[1], 'p95')) + (source ? ' - ' + source : ''), function () { openQueueDetail(item[0], item[1], activeJobs); }); }},
        {label: 'p99 sampled', numeric: true, render: function (item) { return linkButton(duration(waitValue(item[1], 'p99')), function () { openQueueDetail(item[0], item[1], activeJobs); }); }},
        {label: 'Provenance', render: function (item) { const row = item[1]; return linkButton([waitSource(row, 'p50'), waitSource(row, 'p95'), waitSource(row, 'p99')].filter(Boolean).filter(function (x, i, a) { return a.indexOf(x) === i; }).join(', ') || 'No wait sample', function () { openQueueDetail(item[0], row, activeJobs); }); }},
      ], entries, integer(entries.length) + (state.queueIncludeIdle ? ' queues including idle' : ' active or problem queues')));
      return;
    }

    if (state.queueView === 'jobs') {
      const workloadCounts = activeJobs.reduce(function (out, job) { const key = job.workload || 'unknown'; out[key] = (out[key] || 0) + 1; return out; }, {});
      host.append(panel('Active jobs', Object.entries(workloadCounts).map(function (entry) { return entry[0] + ': ' + entry[1]; }).join(' - '), dataTable([
        {label: 'Job', sticky: true, render: function (job) { return externalLink(job.name || 'Unnamed job', job.url); }},
        {label: 'Queue', render: function (job) { const row = (snapshot.queues || {})[job.queue] || {}; return linkButton(value(job.queue), function () { openQueueDetail(job.queue, row, activeJobs); }); }},
        {label: 'Workload', render: function (job) { return linkedBadge(job.workload || 'unknown', job.url, null, job.workload === 'omni' ? 'is-info' : 'is-neutral'); }},
        {label: 'State', render: function (job) { return linkedBadge(job.state || 'unknown', job.url); }},
        {label: 'Age', numeric: true, render: function (job) { return externalLink(duration(job.wait_min !== undefined ? job.wait_min : job.run_min), job.url); }},
        {label: 'Build', render: function (job) { return externalLink((job.pipeline || '?') + ' #' + value(job.build), job.build_url || buildUrl(job.pipeline, job.build), 'ops-mono'); }},
      ], activeJobs, integer(activeJobs.length) + ' active jobs in scope')));
      return;
    }

    let history = Array.isArray(queueBlock.history) ? queueBlock.history : [];
    if (!history.length) {
      try { history = await fetchJSONL('data/vllm/ci/queue_timeseries.jsonl'); } catch (_) {}
    }
    history = history.filter(function (snap) { return snap && snap.ts && snap.queues; }).sort(function (a, b) { return String(a.ts).localeCompare(String(b.ts)); });
    const latestHistoryMs = history.length ? new Date(history[history.length - 1].ts).getTime() : Date.now();
    const rangeHours = state.queueRange === '30d' ? 720 : state.queueRange === '7d' ? 168 : 24;
    history = history.filter(function (snap) { const time = new Date(snap.ts).getTime(); return !Number.isFinite(time) || time >= latestHistoryMs - rangeHours * 3600000; });
    const queueNames = Array.from(new Set(history.flatMap(function (snap) {
      return Object.keys(snap.queues || {}).filter(function (name) {
        return !isRetiredQueue(name) && (state.queueScope === 'all' || isAmdQueue(name));
      });
    }))).sort();
    if (state.queueHistoryQueue !== 'fleet' && !queueNames.includes(state.queueHistoryQueue)) {
      state.queueHistoryQueue = 'fleet';
      setQueryValue('queue_history_queue', 'fleet');
    }
    const queueField = n('label', 'ops-field');
    queueField.append(n('span', 'ops-field-label', 'History queue'));
    const queueSelect = n('select', 'ops-select');
    queueSelect.setAttribute('aria-label', 'Select queue for historical activity and wait time');
    [['fleet', 'Fleet aggregate']].concat(queueNames.map(function (name) { return [name, name]; })).forEach(function (pair) {
      const option = n('option', '', pair[1]);
      option.value = pair[0];
      option.selected = pair[0] === state.queueHistoryQueue;
      queueSelect.append(option);
    });
    queueSelect.addEventListener('change', function () { setRouteState('ci-queue', 'queueHistoryQueue', queueSelect.value, 'queue_history_queue'); });
    queueField.append(queueSelect);
    const historyToolbar = n('div', 'ops-toolbar');
    historyToolbar.append(queueField);
    host.append(historyToolbar);
    const selectedHistory = state.queueHistoryQueue === 'fleet' ? history : history.filter(function (snap) {
      return Object.prototype.hasOwnProperty.call(snap.queues || {}, state.queueHistoryQueue);
    });
    const points = selectedHistory.map(function (snap) {
      let waiting = 0, running = 0, queues = 0;
      for (const [name, row] of Object.entries(snap.queues || {})) {
        if (isRetiredQueue(name) || (state.queueScope !== 'all' && !isAmdQueue(name))) continue;
        if (state.queueHistoryQueue !== 'fleet' && name !== state.queueHistoryQueue) continue;
        waiting += Number(row.waiting || 0);
        running += Number(row.running || 0);
        queues += 1;
      }
      return {ts: snap.ts, waiting: waiting, running: running, snapshot: snap, queues: queues};
    });
    const summary = queueBlock.history_summary || {};
    const selectedHistoryStart = selectedHistory.length ? selectedHistory[0].ts : summary.first_observed_at;
    const historyLabel = state.queueHistoryQueue === 'fleet' ? 'Fleet' : state.queueHistoryQueue;
    const cp = chartPanel(historyLabel + ' activity', integer(points.length) + ' snapshots in ' + state.queueRange + (selectedHistoryStart ? ' - history begins ' + shortDate(selectedHistoryStart) : ''), 'queue-history');
    host.append(cp.root);
    drawChart('queue-history', cp.canvas, {type: 'line', data: {
      labels: points.map(function (p) { return shortDate(p.ts); }),
      datasets: [
        {label: 'Running', data: points.map(function (p) { return p.running; }), borderColor: '#22b8ad', backgroundColor: '#22b8ad', pointRadius: 0, borderWidth: 2},
        {label: 'Waiting', data: points.map(function (p) { return p.waiting; }), borderColor: '#e3a63a', backgroundColor: '#e3a63a', pointRadius: 0, borderWidth: 2},
      ],
    }, evidenceTitle: historyLabel + ' queue activity history', evidenceAsset: SOURCE_ASSETS.queueHistory, evidence: points.map(function (point) { return {label: shortDate(point.ts), timestamp: point.ts, valueSummary: integer(point.running) + ' running - ' + integer(point.waiting) + ' waiting', details: {running: point.running, waiting: point.waiting, queues: point.queues, selected_queue: state.queueHistoryQueue}, sources: [{label: 'Open published queue history', url: SOURCE_ASSETS.queueHistory}], onOpen: function () { openQueueSnapshotDetail(point.snapshot, point); }}; })});

    const waitPoints = selectedHistory.map(function (snap) { return queueWaitHistoryPoint(snap, state.queueHistoryQueue); });
    const waitEvidenceCount = waitPoints.filter(function (point) { return point.p50 !== null || point.p95 !== null || point.p99 !== null; }).length;
    if (waitEvidenceCount) {
      const waitTitle = state.queueHistoryQueue === 'fleet' ? 'Highest reported wait across queues' : state.queueHistoryQueue + ' wait history';
      const waitSubtitle = state.queueHistoryQueue === 'fleet'
        ? 'Maximum queue-level percentile in scope at each snapshot; percentiles are not combined into a fleet percentile'
        : 'Source-reported queue wait percentiles; p99 appears only for sampled wait evidence';
      const waitChart = chartPanel(waitTitle, integer(waitEvidenceCount) + ' snapshots with wait measurements', 'queue-wait-history');
      host.append(waitChart.root);
      drawChart('queue-wait-history', waitChart.canvas, {
        type: 'line',
        data: {
          labels: waitPoints.map(function (point) { return shortDate(point.ts); }),
          datasets: [
            {label: 'p50', data: waitPoints.map(function (point) { return point.p50; }), borderColor: '#22b8ad', backgroundColor: '#22b8ad', pointRadius: 2, borderWidth: 2},
            {label: 'p95', data: waitPoints.map(function (point) { return point.p95; }), borderColor: '#e3a63a', backgroundColor: '#e3a63a', pointRadius: 2, borderWidth: 2},
            {label: 'p99 sampled', data: waitPoints.map(function (point) { return point.p99; }), borderColor: '#cf8dd9', backgroundColor: '#cf8dd9', pointRadius: 2, borderWidth: 1.5},
          ],
        },
        options: {scales: {x: {grid: {display: false}, ticks: {maxTicksLimit: 8}}, y: {beginAtZero: true, title: {display: true, text: 'Wait minutes'}}}},
        evidenceTitle: waitTitle,
        evidenceAsset: SOURCE_ASSETS.queueHistory,
        evidence: waitPoints.map(function (point) { return {label: shortDate(point.ts), timestamp: point.ts, valueSummary: 'p50 ' + duration(point.p50) + ' - p95 ' + duration(point.p95) + ' - p99 ' + duration(point.p99), details: {p50: duration(point.p50), p50_queue: point.p50Queue, p50_source: point.p50Source, p95: duration(point.p95), p95_queue: point.p95Queue, p95_source: point.p95Source, p99_sampled: duration(point.p99), p99_queue: point.p99Queue, p99_source: point.p99Source}, sources: [{label: 'Open published queue history', url: SOURCE_ASSETS.queueHistory}], onOpen: function () { openQueueSnapshotDetail(point.snapshot, {running: '-', waiting: '-', queues: state.queueHistoryQueue === 'fleet' ? selectedQueues(point.snapshot, true).length : 1}); }}; }),
      });
    } else {
      host.append(n('div', 'ops-evidence-note is-info', 'No source-reported queue wait percentiles exist for ' + historyLabel + ' in this range. Counts remain historical evidence; missing waits are not rendered as zero.'));
    }
    if (points.length < 2) host.append(n('div', 'ops-evidence-note is-info', 'Historical collection has only one snapshot in this range. The dashboard will not infer a trend until another source-backed point exists.'));
    host.append(dataTable([
      {label: 'Snapshot', sticky: true, render: function (point) { return linkButton(shortDate(point.ts), function () { openQueueSnapshotDetail(point.snapshot, point); }); }},
      {label: 'Running', numeric: true, render: function (point) { return linkButton(integer(point.running), function () { openQueueSnapshotDetail(point.snapshot, point); }); }},
      {label: 'Waiting', numeric: true, render: function (point) { return linkButton(integer(point.waiting), function () { openQueueSnapshotDetail(point.snapshot, point); }); }},
      {label: 'Queues', numeric: true, render: function (point) { return linkButton(integer(point.queues), function () { openQueueSnapshotDetail(point.snapshot, point); }); }},
    ], points.slice().reverse().slice(0, 100), integer(points.length) + ' snapshots in selected range'));
  }

  function hotnessRatePercent(row) {
    const explicit = row.fail_rate_percent !== undefined ? row.fail_rate_percent : row.incident_rate_pct;
    if (explicit !== undefined && explicit !== null && Number.isFinite(Number(explicit))) return Number(explicit);
    const raw = Number(row.fail_rate);
    if (!Number.isFinite(raw)) return NaN;
    const unit = String(row.fail_rate_unit || row.rate_unit || '').toLowerCase();
    if (unit === 'percent' || unit === 'pct') return raw;
    if (unit === 'fraction' || unit === 'ratio') return raw * 100;
    return raw >= 0 && raw <= 1 ? raw * 100 : raw;
  }

  function percentileValue(values, percentile) {
    const sorted = values.filter(function (item) { return Number.isFinite(Number(item)); }).map(Number).sort(function (a, b) { return a - b; });
    if (!sorted.length) return null;
    return sorted[Math.ceil((sorted.length - 1) * percentile)];
  }

  function executionCadencePerDay(observations) {
    const timestamps = observations.map(function (observation) {
      return new Date(observationTimestamp(observation) || 0).getTime();
    }).filter(Number.isFinite).sort(function (a, b) { return a - b; });
    const gapsMinutes = [];
    for (let index = 1; index < timestamps.length; index += 1) {
      const gap = (timestamps[index] - timestamps[index - 1]) / 60000;
      if (gap > 0) gapsMinutes.push(gap);
    }
    const medianGap = percentileValue(gapsMinutes, 0.5);
    return Number(medianGap) > 0 ? 1440 / Number(medianGap) : null;
  }

  function observationTimestamp(observation) {
    return observation.observed_at || observation.finished_at || observation.created_at || observation.date || null;
  }

  function trajectoryRowsFromReliability(reliability, windowId, generatedAt) {
    const windowHours = {"24h": 24, "72h": 72, "7d": 168, "30d": 720};
    const cohort = reliabilityCohortSummary(reliability);
    const endCandidate = cohort.observedTo || generatedAt;
    const endMs = new Date(endCandidate || Date.now()).getTime();
    const safeEndMs = Number.isFinite(endMs) ? endMs : Date.now();
    const cutoffMs = safeEndMs - (windowHours[windowId] || 24) * 3600000;
    const rows = reliabilityCatalog(reliability).filter(function (catalogRow) {
      return catalogRow && catalogRow.source_pipeline === 'ci';
    }).map(function (catalogRow) {
      const observations = evidenceObservations(catalogRow).filter(function (observation) {
        const observedMs = new Date(observationTimestamp(observation) || 0).getTime();
        return observation.source_pipeline === 'ci'
          && Boolean(exactPipelineEvidenceUrl(observation, 'ci'))
          && Number.isFinite(observedMs)
          && observedMs >= cutoffMs
          && observedMs <= safeEndMs;
      }).map(function (observation) {
        return Object.assign({}, observation, {
          variant_id: catalogRow.id,
          variant_hardware: catalogRow.hardware || catalogRow.hw,
          variant_queues: catalogRow.queues || (catalogRow.queue ? [catalogRow.queue] : []),
        });
      });
      if (!observations.length) return null;
      const durations = observations.map(function (observation) { return observation.duration_mins; }).filter(function (minutes) { return Number.isFinite(Number(minutes)); });
      const incidents = observations.filter(isIncidentObservation);
      const passed = observations.filter(function (observation) { return observationState(observation) === 'passed'; }).length;
      const queues = Array.from(new Set(observations.map(function (observation) { return observation.queue; }).filter(Boolean)));
      const builds = new Set(observations.map(function (observation) { return observation.build_number; }).filter(function (build) { return build !== null && build !== undefined; }));
      const latest = observations.slice().sort(function (a, b) { return new Date(observationTimestamp(b) || 0) - new Date(observationTimestamp(a) || 0); })[0];
      return {
        id: catalogRow.id,
        evidence_ref: catalogRow.id,
        name: catalogRow.name,
        hardware: catalogRow.hardware || catalogRow.hw || 'unknown',
        queues: queues.length ? queues : (catalogRow.queues || []),
        workload: catalogRow.workload || 'vllm',
        count: observations.length,
        build_count: builds.size,
        passed: passed,
        failed: incidents.filter(function (observation) { return ['hard', 'failed'].includes(observationState(observation)); }).length,
        soft_failed: incidents.filter(function (observation) { return ['soft', 'soft_fail', 'soft_failed'].includes(observationState(observation)); }).length,
        incident_count: incidents.length,
        incident_rate_pct: observations.length ? incidents.length / observations.length * 100 : 0,
        p50_min: percentileValue(durations, 0.5),
        p90_min: percentileValue(durations, 0.9),
        max_min: durations.length ? Math.max.apply(null, durations) : null,
        last_seen: observationTimestamp(latest),
        observations: observations,
        catalogRow: catalogRow,
        window: windowId,
      };
    }).filter(Boolean);
    return {rows: rows, observedTo: new Date(safeEndMs).toISOString(), observedFrom: new Date(cutoffMs).toISOString(), cohort: cohort};
  }

  function trajectoryAnomaliesFromReliability(reliability, windowId, generatedAt) {
    const windowHours = {"24h": 24, "72h": 72, "7d": 168, "30d": 720};
    const cohort = reliabilityCohortSummary(reliability);
    const endMsRaw = new Date(cohort.observedTo || generatedAt || Date.now()).getTime();
    const endMs = Number.isFinite(endMsRaw) ? endMsRaw : Date.now();
    const recentHours = Math.min(windowHours[windowId] || 24, 72);
    const recentStartMs = endMs - recentHours * 3600000;
    const retainedStartRaw = new Date(cohort.observedFrom || 0).getTime();
    const desiredBaselineHours = Math.max(168, recentHours * 4);
    const baselineStartMs = Math.max(Number.isFinite(retainedStartRaw) ? retainedStartRaw : 0, recentStartMs - desiredBaselineHours * 3600000);
    const baselineDays = Math.max((recentStartMs - baselineStartMs) / 86400000, 0);
    const rows = reliabilityCatalog(reliability).filter(function (catalogRow) {
      return catalogRow && catalogRow.source_pipeline === 'ci';
    }).map(function (catalogRow) {
      const retained = evidenceObservations(catalogRow).filter(function (observation) {
        const observedMs = new Date(observationTimestamp(observation) || 0).getTime();
        return observation.source_pipeline === 'ci'
          && Boolean(exactPipelineEvidenceUrl(observation, 'ci'))
          && Number.isFinite(observedMs)
          && observedMs <= endMs;
      }).sort(function (a, b) { return new Date(observationTimestamp(a) || 0) - new Date(observationTimestamp(b) || 0); });
      const recent = retained.filter(function (observation) { return new Date(observationTimestamp(observation)).getTime() >= recentStartMs; });
      const baseline = retained.filter(function (observation) { const observedMs = new Date(observationTimestamp(observation)).getTime(); return observedMs >= baselineStartMs && observedMs < recentStartMs; });
      if (!recent.length) return null;
      const byBuild = new Map();
      retained.forEach(function (observation) {
        const key = observation.build_number !== null && observation.build_number !== undefined
          ? 'build-' + observation.build_number
          : 'job-' + value(observation.job_id || exactPipelineEvidenceUrl(observation, 'ci'));
        if (!byBuild.has(key)) byBuild.set(key, observation);
      });
      const distinctBuilds = Array.from(byBuild.values());
      const cadenceRecent = distinctBuilds.slice(-8);
      const cadenceBaseline = distinctBuilds.slice(-24, -8);
      const recentDurations = recent.map(observationDurationMinutes).filter(function (minutes) { return minutes !== null; });
      const baselineDurations = baseline.map(observationDurationMinutes).filter(function (minutes) { return minutes !== null; });
      const recentRate = cadenceRecent.length >= 4 ? executionCadencePerDay(cadenceRecent) : null;
      const baselineRate = cadenceBaseline.length >= 4 ? executionCadencePerDay(cadenceBaseline) : null;
      const frequencyChangePct = Number(recentRate) > 0 && Number(baselineRate) > 0 ? (Number(recentRate) - Number(baselineRate)) / Number(baselineRate) * 100 : null;
      const recentMedian = percentileValue(recentDurations, 0.5);
      const baselineMedian = percentileValue(baselineDurations, 0.5);
      const durationChangePct = Number(baselineMedian) > 0 && recentMedian !== null ? (Number(recentMedian) - Number(baselineMedian)) / Number(baselineMedian) * 100 : null;
      const incidents = recent.filter(isIncidentObservation);
      const latest = recent.slice().sort(function (a, b) { return new Date(observationTimestamp(b) || 0) - new Date(observationTimestamp(a) || 0); })[0];
      return {
        id: catalogRow.id,
        name: catalogRow.name,
        hardware: catalogRow.hardware || catalogRow.hw || 'unknown',
        queues: catalogRow.queues || (catalogRow.queue ? [catalogRow.queue] : []),
        workload: catalogRow.workload || 'vllm',
        recent: recent,
        baseline: baseline,
        recentCount: recent.length,
        baselineCount: baseline.length,
        cadenceRecentCount: cadenceRecent.length,
        cadenceBaselineCount: cadenceBaseline.length,
        cadenceRecent: cadenceRecent,
        cadenceBaseline: cadenceBaseline,
        recentRate: recentRate,
        baselineRate: baselineRate,
        frequencyChangePct: frequencyChangePct,
        recentMedian: recentMedian,
        baselineMedian: baselineMedian,
        durationChangePct: durationChangePct,
        incidentRatePct: incidents.length / recent.length * 100,
        latest: latest,
        catalogRow: catalogRow,
      };
    }).filter(Boolean);
    return {
      rows: rows,
      recentHours: recentHours,
      recentStart: new Date(recentStartMs).toISOString(),
      baselineStart: new Date(baselineStartMs).toISOString(),
      baselineEnd: new Date(recentStartMs).toISOString(),
      baselineDays: baselineDays,
    };
  }

  function trajectoryAnomalyObservations(row) {
    const unique = new Map();
    [row.baseline, row.recent, row.cadenceBaseline, row.cadenceRecent].flat().filter(Boolean).forEach(function (observation) {
      const key = observation.job_id || exactPipelineEvidenceUrl(observation, 'ci')
        || value(observation.build_number) + '-' + observationTimestamp(observation);
      if (!unique.has(key)) unique.set(key, observation);
    });
    return Array.from(unique.values());
  }

  function openTrajectoryAnomalyHistory(row, anomalyData) {
    const observations = trajectoryAnomalyObservations(row);
    const passed = observations.filter(function (observation) { return observationState(observation) === 'passed'; }).length;
    const incidents = observations.filter(isIncidentObservation);
    const soft = observations.filter(function (observation) { return ['soft', 'soft_fail', 'soft_failed'].includes(observationState(observation)); }).length;
    openMixedOutcomeEvidence(Object.assign({}, row.catalogRow || {}, {
      id: row.id,
      name: row.name,
      hardware: row.hardware,
      queues: row.queues,
      runs: observations.length,
      passed: passed,
      failed: Math.max(0, incidents.length - soft),
      soft_failed: soft,
      fail_rate: observations.length ? incidents.length / observations.length * 100 : 0,
      observations: observations,
      scope_label: duration(anomalyData.recentHours * 60) + ' recent window versus retained baseline beginning ' + shortDate(anomalyData.baselineStart),
    }));
  }

  function openTrajectoryGroupHistory(row) {
    const candidate = Object.assign({}, row.catalogRow || {}, {
      id: row.id,
      name: row.name,
      hardware: row.hardware,
      queues: row.queues,
      runs: row.count,
      passed: row.passed,
      failed: row.failed,
      soft_failed: row.soft_failed,
      fail_rate: row.incident_rate_pct,
      observations: row.observations,
      scope_label: row.window + ' all-main window for strict catalog ID ' + row.id,
    });
    openMixedOutcomeEvidence(candidate);
  }

  async function renderTrajectory(host, ops) {
    const reliability = canonicalReliability(ops);
    const scope = reliabilityScopeInfo(reliability);
    const windowData = trajectoryRowsFromReliability(reliability, state.trajectoryWindow, ops.generated_at);
    const anomalyData = trajectoryAnomaliesFromReliability(reliability, state.trajectoryWindow, ops.generated_at);
    const allRows = windowData.rows;
    let rows = allRows.slice();
    const hardware = Array.from(new Set(allRows.map(function (row) { return row.hardware || 'unknown'; }))).sort();
    const workloads = Array.from(new Set(allRows.map(function (row) { return row.workload || 'vllm'; }))).sort();
    if (state.trajectoryWorkload !== 'all') rows = rows.filter(function (row) { return (row.workload || 'vllm') === state.trajectoryWorkload; });
    if (state.trajectoryHardware !== 'all') rows = rows.filter(function (row) { return (row.hardware || 'unknown') === state.trajectoryHardware; });
    const query = state.trajectorySearch.trim().toLowerCase();
    if (query) rows = rows.filter(function (row) { return [row.name, row.id, row.hardware, (row.queues || []).join(' ')].some(function (part) { return String(part || '').toLowerCase().includes(query); }); });
    const sourceAction = externalLink('Open upstream main source', SOURCE_ASSETS.operations, 'ops-button');
    add(host, pageHeader('CI Workload Trajectory', 'Upstream main execution volume, completion time, and incident pressure from strict vllm/ci job observations.', windowData.observedTo, sourceAction));
    if (!scope.available) {
      const unavailable = n('div', 'ops-evidence-note is-warning');
      add(unavailable, [n('strong', '', 'Upstream trajectory unavailable. '), n('span', '', scope.detail + '. No AMD or nightly observations have been substituted.')]);
      host.append(unavailable);
      return;
    }
    const toolbar = n('div', 'ops-toolbar');
    toolbar.append(segmented(['24h', '72h', '7d', '30d'].map(function (id) { return {id: id, label: id}; }), state.trajectoryWindow, function (id) { setRouteState('ci-hotness', 'trajectoryWindow', id, 'trajectory_window'); }, 'All-main observation window'));
    const workloadSelect = n('select', 'ops-select');
    workloadSelect.setAttribute('aria-label', 'Filter workload trajectory by workload');
    for (const id of ['all'].concat(workloads)) { const o = n('option', '', id === 'all' ? 'All workloads' : id); o.value = id; o.selected = id === state.trajectoryWorkload; workloadSelect.append(o); }
    workloadSelect.addEventListener('change', function () { state.trajectoryWorkload = workloadSelect.value; render('ci-hotness', true); });
    const hwSelect = n('select', 'ops-select');
    hwSelect.setAttribute('aria-label', 'Filter workload trajectory by hardware');
    for (const id of ['all'].concat(hardware)) { const o = n('option', '', id === 'all' ? 'All hardware' : id); o.value = id; o.selected = id === state.trajectoryHardware; hwSelect.append(o); }
    hwSelect.addEventListener('change', function () { state.trajectoryHardware = hwSelect.value; render('ci-hotness', true); });
    const search = n('input', 'ops-input'); search.type = 'search'; search.placeholder = 'Filter test groups'; search.value = state.trajectorySearch;
    search.setAttribute('aria-label', 'Search workload trajectory test groups');
    search.addEventListener('change', function () { state.trajectorySearch = search.value; render('ci-hotness', true); });
    add(toolbar, [workloadSelect, hwSelect, search]); host.append(toolbar);
    const sourceNote = n('div', 'ops-evidence-note is-info');
    add(sourceNote, [n('strong', '', 'Upstream main terminal history. '), n('span', '', 'Windowed in the browser from ' + shortDate(windowData.observedFrom) + ' through ' + shortDate(windowData.observedTo) + '. Identities remain split by catalog ID, hardware, and queue. The source retains up to 60 observations per group, so longer windows may be truncated.')]);
    host.append(sourceNote);
    const totalRuns = rows.reduce(function (sum, row) { return sum + Number(row.count || 0); }, 0);
    const uniqueBuilds = new Set();
    rows.forEach(function (row) { row.observations.forEach(function (observation) { if (observation.build_number !== undefined) uniqueBuilds.add(observation.build_number); }); });
    const slowest = rows.filter(function (row) { return Number.isFinite(Number(row.p90_min)); }).sort(function (a, b) { return Number(b.p90_min) - Number(a.p90_min); })[0] || {};
    const failing = rows.filter(function (row) { return hotnessRatePercent(row) > 0; }).length;
    host.append(statusStrip([
      {id: 'trajectory-jobs', label: 'TERMINAL OBSERVATIONS', value: integer(totalRuns), meta: integer(uniqueBuilds.size) + ' builds in ' + state.trajectoryWindow, window: state.trajectoryWindow, observed: windowData.observedTo, provenance: 'reliability.group_catalog observations', sources: [{label: 'Open published all-main history', url: SOURCE_ASSETS.operations}]},
      {id: 'trajectory-groups', label: 'STRICT GROUP VARIANTS', value: integer(rows.length), meta: 'after active filters', onOpen: function () { openMetricDetail({label: 'Filtered strict group variants', value: rows.length, meta: state.trajectoryWindow + ' selected window', provenance: 'reliability.group_catalog IDs', sources: [{label: 'Open published all-main history', url: SOURCE_ASSETS.operations}]}); }},
      {id: 'trajectory-incidents', label: 'VARIANTS WITH INCIDENTS', value: integer(failing), meta: 'non-zero incident rate', tone: failing ? 'is-warning' : 'is-success', onOpen: function () { openHistoryEvidence('Variants with incidents', rows.filter(function (row) { return hotnessRatePercent(row) > 0; }).map(function (row) { return {id: row.id, label: row.name + ' - ' + row.hardware, timestamp: row.last_seen, valueSummary: hotnessRatePercent(row).toFixed(1) + '%', sources: [{label: 'Open published all-main history', url: SOURCE_ASSETS.operations}], onOpen: function () { openTrajectoryGroupHistory(row); }}; }), 'Strict all-main group identities in the selected window', SOURCE_ASSETS.operations); }},
      {id: 'trajectory-slowest', label: 'SLOWEST P90', value: duration(slowest.p90_min), meta: value(slowest.name, 'No duration data'), onOpen: function () { slowest.id ? openTrajectoryGroupHistory(slowest) : openMetricDetail({label: 'Slowest p90', value: '-', meta: 'No completion data in this window', sources: [{label: 'Open published all-main history', url: SOURCE_ASSETS.operations}]}); }},
    ]));

    let anomalyRows = anomalyData.rows.slice();
    if (state.trajectoryWorkload !== 'all') anomalyRows = anomalyRows.filter(function (row) { return row.workload === state.trajectoryWorkload; });
    if (state.trajectoryHardware !== 'all') anomalyRows = anomalyRows.filter(function (row) { return row.hardware === state.trajectoryHardware; });
    if (query) anomalyRows = anomalyRows.filter(function (row) { return [row.name, row.id, row.hardware, row.queues.join(' ')].some(function (part) { return String(part || '').toLowerCase().includes(query); }); });
    const frequencyRows = anomalyRows.filter(function (row) {
      return row.cadenceRecentCount >= 4 && row.cadenceBaselineCount >= 4 && Number(row.frequencyChangePct) >= 25;
    }).sort(function (a, b) {
      return Number(b.frequencyChangePct || 0) - Number(a.frequencyChangePct || 0);
    }).slice(0, 15);
    const durationRows = anomalyRows.filter(function (row) {
      return row.recentCount >= 2 && row.baselineCount >= 2 && Number(row.durationChangePct) >= 15;
    }).sort(function (a, b) { return Number(b.durationChangePct) - Number(a.durationChangePct); }).slice(0, 15);
    const anomalyGrid = n('div', 'ops-grid ops-grid-2');
    if (frequencyRows.length) {
      const frequencyChart = chartPanel('Execution-frequency changes', 'Median cadence across the latest 8 distinct builds versus the preceding 16', 'trajectory-frequency-anomalies');
      anomalyGrid.append(frequencyChart.root);
      requestAnimationFrame(function () {
        drawChart('trajectory-frequency-anomalies', frequencyChart.canvas, {
          type: 'bar',
          data: {labels: frequencyRows.map(function (row) { return compactChartLabel(row, 42); }), datasets: [
            {label: 'Latest', data: frequencyRows.map(function (row) { return Number(row.recentRate.toFixed(2)); }), backgroundColor: '#e3a63a'},
            {label: 'Prior', data: frequencyRows.map(function (row) { return row.baselineRate === null ? null : Number(row.baselineRate.toFixed(2)); }), backgroundColor: '#66717d'},
          ]},
          options: {indexAxis: 'y', scales: {x: {beginAtZero: true, title: {display: true, text: 'Distinct builds per day'}}, y: {grid: {display: false}}}},
          evidenceTitle: 'Test-group execution-frequency changes',
          evidence: frequencyRows.map(function (row) { return {id: row.id, label: row.name + ' - ' + row.hardware, timestamp: observationTimestamp(row.latest), valueSummary: (row.frequencyChangePct >= 0 ? '+' : '') + row.frequencyChangePct.toFixed(0) + '% execution cadence', details: {latest_distinct_builds: row.cadenceRecentCount, latest_cadence_per_day: row.recentRate.toFixed(2), prior_distinct_builds: row.cadenceBaselineCount, prior_cadence_per_day: row.baselineRate === null ? '-' : row.baselineRate.toFixed(2), queues: row.queues.join(', '), incident_rate: row.incidentRatePct.toFixed(1) + '%'}, sources: [{label: 'Open published all-main history', url: SOURCE_ASSETS.operations}], onOpen: function () { openTrajectoryAnomalyHistory(row, anomalyData); }}; }),
        });
      });
    } else {
      anomalyGrid.append(panel('Execution-frequency changes', 'No group crossed the evidence threshold', n('div', 'ops-empty', 'At least four latest and four prior distinct builds plus a 25% cadence increase are required.')));
    }
    if (durationRows.length) {
      const durationRegressionChart = chartPanel('Completion-time regressions', 'Recent median completion versus the preceding retained baseline', 'trajectory-duration-anomalies');
      anomalyGrid.append(durationRegressionChart.root);
      requestAnimationFrame(function () {
        drawChart('trajectory-duration-anomalies', durationRegressionChart.canvas, {
          type: 'bar',
          data: {labels: durationRows.map(function (row) { return compactChartLabel(row, 42); }), datasets: [
            {label: 'Recent', data: durationRows.map(function (row) { return row.recentMedian; }), backgroundColor: '#e06464'},
            {label: 'Baseline', data: durationRows.map(function (row) { return row.baselineMedian; }), backgroundColor: '#66717d'},
          ]},
          options: {indexAxis: 'y', scales: {x: {beginAtZero: true, title: {display: true, text: 'Completion minutes'}}, y: {grid: {display: false}}}},
          evidenceTitle: 'Test-group completion-time regressions',
          evidence: durationRows.map(function (row) { return {id: row.id, label: row.name + ' - ' + row.hardware, timestamp: observationTimestamp(row.latest), valueSummary: '+' + row.durationChangePct.toFixed(0) + '% median completion time', details: {recent_median: duration(row.recentMedian), baseline_median: duration(row.baselineMedian), recent_observations: row.recentCount, baseline_observations: row.baselineCount, queues: row.queues.join(', '), incident_rate: row.incidentRatePct.toFixed(1) + '%'}, sources: [{label: 'Open published all-main history', url: SOURCE_ASSETS.operations}], onOpen: function () { openTrajectoryAnomalyHistory(row, anomalyData); }}; }),
        });
      });
    } else {
      anomalyGrid.append(panel('Completion-time regressions', 'No group crossed the evidence threshold', n('div', 'ops-empty', 'At least two recent and two baseline durations plus a 15% median increase are required.')));
    }
    host.append(anomalyGrid);
    const anomalyById = new Map();
    frequencyRows.concat(durationRows).forEach(function (row) { anomalyById.set(row.id, row); });
    const anomalyDetails = Array.from(anomalyById.values()).sort(function (a, b) {
      const aScore = Math.max(Number(a.frequencyChangePct || 0), Number(a.durationChangePct || 0));
      const bScore = Math.max(Number(b.frequencyChangePct || 0), Number(b.durationChangePct || 0));
      return bScore - aScore;
    });
    if (anomalyDetails.length) {
      host.append(panel('Abnormal test-group activity', integer(anomalyDetails.length) + ' strict variants crossing frequency or duration thresholds', dataTable([
        {label: 'Test group variant', sticky: true, width: '340px', render: function (row) { return groupIdentityCell(row, function () { openTrajectoryAnomalyHistory(row, anomalyData); }); }},
        {label: 'Frequency signal', width: '150px', render: function (row) { const hasChange = Number.isFinite(Number(row.frequencyChangePct)); const text = hasChange ? (row.frequencyChangePct >= 0 ? '+' : '') + row.frequencyChangePct.toFixed(0) + '%' : 'baseline limited'; return linkedBadge(text, exactPipelineEvidenceUrl(row.latest, 'ci'), function () { openTrajectoryAnomalyHistory(row, anomalyData); }, Number(row.frequencyChangePct) >= 100 ? 'is-warning' : 'is-info'); }},
        {label: 'Latest builds / day', numeric: true, width: '150px', render: function (row) { return linkButton(row.recentRate === null ? '-' : row.recentRate.toFixed(1), function () { openTrajectoryAnomalyHistory(row, anomalyData); }); }},
        {label: 'Prior builds / day', numeric: true, width: '150px', render: function (row) { return linkButton(row.baselineRate === null ? '-' : row.baselineRate.toFixed(1), function () { openTrajectoryAnomalyHistory(row, anomalyData); }); }},
        {label: 'Median change', numeric: true, width: '130px', render: function (row) { return linkButton(row.durationChangePct === null ? '-' : (row.durationChangePct >= 0 ? '+' : '') + row.durationChangePct.toFixed(0) + '%', function () { openTrajectoryAnomalyHistory(row, anomalyData); }); }},
        {label: 'Incident rate', numeric: true, width: '120px', render: function (row) { return linkButton(row.incidentRatePct.toFixed(1) + '%', function () { openTrajectoryAnomalyHistory(row, anomalyData); }); }},
        {label: 'Queues', width: '220px', render: function (row) { const links = n('div', 'ops-inline-links'); row.queues.forEach(function (queueName) { links.append(linkButton(queueName, function () { navigateTo('ci-queue', {queueView: 'history', queueHistoryQueue: queueName, queueScope: isAmdQueue(queueName) ? 'amd' : 'all'}); }, 'Open historical queue activity for ' + queueName)); }); return row.queues.length ? links : n('span', 'ops-cell-muted', '-'); }},
        {label: 'History', width: '140px', render: function (row) { return linkButton(integer(trajectoryAnomalyObservations(row).length) + ' runs', function () { openTrajectoryAnomalyHistory(row, anomalyData); }, 'Open exact cadence, baseline, and recent Buildkite history'); }},
      ], anomalyDetails, integer(anomalyDetails.length) + ' evidence-backed anomalies with strict hardware and queue identity', {name: 'trajectory-anomalies', minWidth: '1390px'})));
    }
    const anomalyNote = n('div', 'ops-evidence-note is-info');
    add(anomalyNote, [n('strong', '', 'Abnormal activity method. '), n('span', '', 'Execution frequency compares median inter-build cadence across the latest 8 distinct builds with the preceding 16, so retries in one build are counted once. Completion compares the last ' + duration(anomalyData.recentHours * 60) + ' with ' + shortDate(anomalyData.baselineStart) + ' through ' + shortDate(anomalyData.baselineEnd) + '. Signals remain split by strict group ID, hardware, and queue.')]);
    host.append(anomalyNote);
    const top = rows.slice().sort(function (a, b) { return Number(b.count || 0) - Number(a.count || 0); }).slice(0, 15);
    const cp = chartPanel('Most active strict variants', 'Terminal observations in the selected all-main window', 'trajectory-groups');
    host.append(cp.root);
    drawChart('trajectory-groups', cp.canvas, {type: 'bar', data: {labels: top.map(function (row) { return compactChartLabel(row, 54); }), datasets: [{label: 'Observations', data: top.map(function (row) { return row.count; }), backgroundColor: '#22b8ad'}]}, options: {indexAxis: 'y'}, evidenceTitle: 'Strict test-group execution volume', evidence: top.map(function (row) { return {id: row.id, label: row.name + ' - ' + row.hardware, timestamp: row.last_seen, valueSummary: integer(row.count) + ' observations', details: {catalog_id: row.id, hardware: row.hardware, queues: row.queues.join(', '), median: duration(row.p50_min), p90: duration(row.p90_min), incident_rate: hotnessRatePercent(row).toFixed(1) + '%'}, sources: [{label: 'Open published all-main history', url: SOURCE_ASSETS.operations}], onOpen: function () { openTrajectoryGroupHistory(row); }}; })});
    host.append(dataTable([
      {label: 'Test group variant', sticky: true, render: function (row) { return groupIdentityCell(row, function () { openTrajectoryGroupHistory(row); }); }},
      {label: 'Workload', render: function (row) { return linkedBadge(row.workload || 'vllm', null, function () { state.trajectoryWorkload = row.workload || 'vllm'; render('ci-hotness', true); }, row.workload === 'omni' ? 'is-info' : 'is-neutral'); }},
      {label: 'Observations', numeric: true, render: function (row) { return linkButton(integer(row.count), function () { openTrajectoryGroupHistory(row); }, 'Inspect ' + integer(row.count) + ' observations for ' + row.name + ' on ' + row.hardware); }},
      {label: 'Builds', numeric: true, render: function (row) { return linkButton(integer(row.build_count), function () { openTrajectoryGroupHistory(row); }, 'Inspect builds for ' + row.name + ' on ' + row.hardware); }},
      {label: 'Median', numeric: true, render: function (row) { return linkButton(duration(row.p50_min), function () { openTrajectoryGroupHistory(row); }, 'Inspect median completion for ' + row.name + ' on ' + row.hardware); }},
      {label: 'p90', numeric: true, render: function (row) { return linkButton(duration(row.p90_min), function () { openTrajectoryGroupHistory(row); }, 'Inspect p90 completion for ' + row.name + ' on ' + row.hardware); }},
      {label: 'Incident rate', numeric: true, render: function (row) { return linkButton(hotnessRatePercent(row).toFixed(1) + '%', function () { openTrajectoryGroupHistory(row); }, 'Inspect incidents for ' + row.name + ' on ' + row.hardware); }},
      {label: 'Last observed', render: function (row) { const latest = row.observations.slice().sort(function (a, b) { return new Date(observationTimestamp(b) || 0) - new Date(observationTimestamp(a) || 0); })[0]; return externalLink(shortDate(row.last_seen), exactPipelineEvidenceUrl(latest, 'ci')); }},
      {label: 'Evidence', render: function (row) { return linkButton(integer(row.observations.length) + ' exact links', function () { openTrajectoryGroupHistory(row); }, 'Open exact Buildkite evidence for catalog ID ' + row.id); }},
    ], rows.slice(0, 150), rows.length + ' strict all-main test-group variants'));

  }

  async function renderOmni(host, ops) {
    const omni = ops.omni || {};
    const current = omni.current || {};
    const heuristic = omni.heuristic_thresholds || {};
    const jobs = omni.current_jobs || {};
    add(host, pageHeader('Omni', 'All current vLLM-Omni demand across the fleet, split into AMD and non-AMD execution scopes.', (omni.provenance || {}).queue_snapshot_ts, externalLink('Open Omni source', SOURCE_ASSETS.operations, 'ops-button')));
    const waitingByQueue = current.waiting_by_queue || {};
    const runningByQueue = current.running_by_queue || {};
    const pending = (jobs.pending || []).filter(function (job) { return !isRetiredQueue(job.queue); });
    const running = (jobs.running || []).filter(function (job) { return !isRetiredQueue(job.queue); });
    const activeJobs = pending.concat(running);
    const affected = new Set(Object.keys(waitingByQueue).concat(Object.keys(runningByQueue)).concat(activeJobs.map(function (job) { return job.queue || 'unknown'; })).filter(function (name) { return !isRetiredQueue(name); }));
    const amdPending = pending.filter(function (job) { return isAmdQueue(job.queue); });
    const amdRunning = running.filter(function (job) { return isAmdQueue(job.queue); });
    const nonAmdPending = pending.filter(function (job) { return !isAmdQueue(job.queue); });
    const nonAmdRunning = running.filter(function (job) { return !isAmdQueue(job.queue); });
    function openJobsEvidence(title, rows) {
      if (!rows.length) {
        openMetricDetail({label: title, value: 0, meta: 'No exact active jobs in this scope.', sources: [{label: 'Open published Omni snapshot', url: SOURCE_ASSETS.operations}]});
        return;
      }
      openHistoryEvidence(title, rows.map(function (job) { return {id: job.job_id, label: job.name || 'Unnamed Omni job', timestamp: job.created_at || job.scheduled_at || job.started_at, valueSummary: value(job.state) + ' on ' + value(job.queue), url: job.url, details: {queue: job.queue, state: job.state, pipeline: job.pipeline, build: job.build}}; }), 'Every active job links to its exact Buildkite source', SOURCE_ASSETS.operations);
    }
    host.append(statusStrip([
      {id: 'omni-all-jobs', label: 'ALL-FLEET ACTIVE JOBS', value: integer(activeJobs.length), meta: integer(pending.length) + ' waiting - ' + integer(running.length) + ' running', tone: pending.length ? 'is-warning' : 'is-neutral', onOpen: function () { openJobsEvidence('All-fleet active Omni jobs', activeJobs); }},
      {id: 'omni-amd-jobs', label: 'AMD ACTIVE JOBS', value: integer(amdPending.length + amdRunning.length), meta: integer(amdPending.length) + ' waiting - ' + integer(amdRunning.length) + ' running', onOpen: function () { openJobsEvidence('AMD active Omni jobs', amdPending.concat(amdRunning)); }},
      {id: 'omni-non-amd-jobs', label: 'NON-AMD ACTIVE JOBS', value: integer(nonAmdPending.length + nonAmdRunning.length), meta: integer(nonAmdPending.length) + ' waiting - ' + integer(nonAmdRunning.length) + ' running', tone: nonAmdPending.length ? 'is-warning' : 'is-info', onOpen: function () { openJobsEvidence('Non-AMD active Omni jobs', nonAmdPending.concat(nonAmdRunning)); }},
      {id: 'omni-queues', label: 'AFFECTED QUEUES', value: integer(affected.size), meta: integer(Array.from(affected).filter(isAmdQueue).length) + ' AMD - ' + integer(Array.from(affected).filter(function (name) { return !isAmdQueue(name); }).length) + ' non-AMD', onOpen: function () { openHistoryEvidence('Omni queue distribution', Array.from(affected).map(function (name) { return {label: name, valueSummary: integer(waitingByQueue[name] || 0) + ' waiting - ' + integer(runningByQueue[name] || 0) + ' running', sources: [{label: 'Open published Omni snapshot', url: SOURCE_ASSETS.operations}], onOpen: function () { openQueueDetail(name, (((ops.queue || {}).snapshot || {}).queues || {})[name] || {}, activeJobs); }}; }), 'Current all-fleet queue distribution', SOURCE_ASSETS.operations); }},
    ]));
    const scopeNote = n('div', 'ops-evidence-note is-info');
    add(scopeNote, [n('strong', '', 'Collector status: ' + value(omni.status) + '. '), n('span', '', 'The current job ledger is fleet-wide. The configured waiting heuristic (' + integer(heuristic.trigger) + ' trigger) is shown only as collector provenance and is not used to hide non-AMD work.')]);
    host.append(scopeNote);
    const queueRows = Array.from(affected).sort().map(function (name) {
      const relatedPending = pending.filter(function (job) { return (job.queue || 'unknown') === name; }).length;
      const relatedRunning = running.filter(function (job) { return (job.queue || 'unknown') === name; }).length;
      return {
        name: name,
        scope: isAmdQueue(name) ? 'AMD' : 'Non-AMD',
        waiting: Object.prototype.hasOwnProperty.call(waitingByQueue, name) ? Number(waitingByQueue[name] || 0) : relatedPending,
        running: Object.prototype.hasOwnProperty.call(runningByQueue, name) ? Number(runningByQueue[name] || 0) : relatedRunning,
        jobs: relatedPending + relatedRunning,
      };
    });
    const grid = n('div', 'ops-grid ops-grid-main-aside ops-omni-grid');
    let history = Array.isArray((ops.queue || {}).history) ? ops.queue.history : [];
    if (!history.length) {
      try { history = await fetchJSONL('data/vllm/ci/queue_timeseries.jsonl'); } catch (_) {}
    }
    const points = history.slice(-336).map(function (snap) {
      let allWaiting = 0, allRunning = 0, amdWaiting = 0, amdRunningHistory = 0, supported = false;
      for (const [name, q] of Object.entries(snap.queues || {})) {
        if (isRetiredQueue(name)) continue;
        const waitingByWorkload = q.waiting_by_workload || {};
        const runningByWorkload = q.running_by_workload || {};
        const hasWaiting = Object.prototype.hasOwnProperty.call(waitingByWorkload, 'omni');
        const hasRunning = Object.prototype.hasOwnProperty.call(runningByWorkload, 'omni');
        if (!hasWaiting && !hasRunning) continue;
        supported = true;
        const waitingCount = hasWaiting ? Number(waitingByWorkload.omni || 0) : 0;
        const runningCount = hasRunning ? Number(runningByWorkload.omni || 0) : 0;
        allWaiting += waitingCount;
        allRunning += runningCount;
        if (isAmdQueue(name)) { amdWaiting += waitingCount; amdRunningHistory += runningCount; }
      }
      return {ts: snap.ts, allWaiting: allWaiting, allRunning: allRunning, amdWaiting: amdWaiting, amdRunning: amdRunningHistory, supported: supported, snapshot: snap};
    }).filter(function (point) { return point.supported; });
    if (points.length) {
      const cp = chartPanel('Omni workload history: all fleet vs AMD', points.length + ' snapshots with explicit workload attribution', 'omni-history');
      cp.root.classList.add('ops-omni-detail');
      grid.append(cp.root);
      requestAnimationFrame(function () {
        drawChart('omni-history', cp.canvas, {type: 'line', data: {labels: points.map(function (point) { return shortDate(point.ts); }), datasets: [
          {label: 'All-fleet running', data: points.map(function (point) { return point.allRunning; }), borderColor: '#cf8dd9', backgroundColor: '#cf8dd9', pointRadius: 0, borderWidth: 2},
          {label: 'All-fleet waiting', data: points.map(function (point) { return point.allWaiting; }), borderColor: '#e3a63a', backgroundColor: '#e3a63a', pointRadius: 0, borderWidth: 2},
          {label: 'AMD running', data: points.map(function (point) { return point.amdRunning; }), borderColor: '#22b8ad', backgroundColor: '#22b8ad', pointRadius: 0, borderWidth: 1, borderDash: [4, 3]},
          {label: 'AMD waiting', data: points.map(function (point) { return point.amdWaiting; }), borderColor: '#5ca8ff', backgroundColor: '#5ca8ff', pointRadius: 0, borderWidth: 1, borderDash: [4, 3]},
        ]}, evidenceTitle: 'Omni all-fleet and AMD demand history', evidenceAsset: SOURCE_ASSETS.queueHistory, evidence: points.map(function (point) { return {label: shortDate(point.ts), timestamp: point.ts, valueSummary: integer(point.allRunning) + ' all-fleet running - ' + integer(point.amdRunning) + ' AMD running', details: {all_fleet_running: point.allRunning, all_fleet_waiting: point.allWaiting, amd_running: point.amdRunning, amd_waiting: point.amdWaiting, non_amd_running: point.allRunning - point.amdRunning, non_amd_waiting: point.allWaiting - point.amdWaiting}, sources: [{label: 'Open published queue history', url: SOURCE_ASSETS.queueHistory}]}; })});
      });
    } else {
      const unavailable = n('div', 'ops-stack');
      unavailable.append(n('div', 'ops-evidence-note is-warning', 'Retained queue snapshots do not contain workload-attributed counts, so no Omni history is inferred from aggregate queue totals.'));
      unavailable.append(sourceActions([{label: 'Inspect published queue history', url: SOURCE_ASSETS.queueHistory}]));
      const historyPanel = panel('Omni workload history unavailable', '0 snapshots with explicit Omni workload attribution', unavailable, 'ops-omni-detail');
      grid.append(historyPanel);
    }
    grid.append(panel('Current all-fleet queue distribution', queueRows.length + ' affected queues', dataTable([
      {label: 'Queue', render: function (row) { const raw = ((((ops.queue || {}).snapshot || {}).queues || {})[row.name]) || {}; return raw.queue_url ? externalLink(row.name, raw.queue_url, 'ops-mono') : linkButton(row.name, function () { openQueueDetail(row.name, raw, activeJobs); }, 'Inspect active Omni jobs on ' + row.name); }},
      {label: 'Scope', render: function (row) { return badge(row.scope, row.scope === 'AMD' ? 'is-success' : 'is-info'); }},
      {label: 'Waiting', numeric: true, render: function (row) { return linkButton(integer(row.waiting), function () { openQueueDetail(row.name, ((((ops.queue || {}).snapshot || {}).queues || {})[row.name]) || {}, activeJobs); }, 'Inspect waiting Omni jobs on ' + row.name); }},
      {label: 'Running', numeric: true, render: function (row) { return linkButton(integer(row.running), function () { openQueueDetail(row.name, ((((ops.queue || {}).snapshot || {}).queues || {})[row.name]) || {}, activeJobs); }, 'Inspect running Omni jobs on ' + row.name); }},
      {label: 'Exact jobs', numeric: true, render: function (row) { return linkButton(integer(row.jobs), function () { openQueueDetail(row.name, ((((ops.queue || {}).snapshot || {}).queues || {})[row.name]) || {}, activeJobs); }, 'Inspect exact active Omni jobs on ' + row.name); }},
    ], queueRows), 'ops-omni-summary'));
    host.append(grid);
    host.append(panel('Current all-fleet Omni jobs', integer(pending.length) + ' waiting - ' + integer(running.length) + ' running; every row is exact evidence', dataTable([
      {label: 'Job', sticky: true, render: function (r) { return externalLink(r.name || 'Unnamed job', r.url); }},
      {label: 'Queue', render: function (r) { return linkButton(value(r.queue), function () { openQueueDetail(r.queue, ((((ops.queue || {}).snapshot || {}).queues || {})[r.queue]) || {}, activeJobs); }, 'Inspect queue and exact jobs for ' + value(r.queue)); }},
      {label: 'Scope', render: function (r) { return badge(isAmdQueue(r.queue) ? 'AMD' : 'Non-AMD', isAmdQueue(r.queue) ? 'is-success' : 'is-info'); }},
      {label: 'State', render: function (r) { return linkedBadge(r.state, r.url); }},
      {label: 'Age', numeric: true, render: function (r) { return externalLink(duration(r.wait_min !== undefined ? r.wait_min : r.run_min), r.url); }},
      {label: 'Build', render: function (r) { return externalLink((r.pipeline || '?') + ' #' + value(r.build), r.build_url || buildUrl(r.pipeline, r.build), 'ops-mono'); }},
      {label: 'Source', render: function (r) { return linkedBadge(r.source || r.workload || 'omni', r.url, null, 'is-info'); }},
    ], activeJobs, integer(activeJobs.length) + ' exact active Omni jobs across the fleet')));
  }

  async function render(tabId, force) {
    if (!OWNED_TABS.has(tabId)) return;
    syncRouteState(tabId);
    const host = ownedHost(tabId);
    if (!host) return;
    const token = String(Date.now()) + Math.random();
    host.dataset.renderToken = token;
    clear(host);
    pruneInactiveCharts();
    host.append(n('div', 'ops-loading', 'Loading operational data...'));
    try {
      const ops = await fetchJSON('data/vllm/ci/operations_v2.json');
      if (host.dataset.renderToken !== token) return;
      clear(host);
      setFreshness(ops);
      if (tabId === 'projects') await renderHome(host, ops);
      else if (tabId === 'ci-health') await renderHealth(host, ops);
      else if (tabId === 'ci-analytics') await renderAnalytics(host, ops);
      else if (tabId === 'ci-perf-eval') await renderPerf(host, ops);
      else if (tabId === 'ci-queue') await renderQueue(host, ops);
      else if (tabId === 'ci-hotness') await renderTrajectory(host, ops);
      else if (tabId === 'ci-omni') await renderOmni(host, ops);
    } catch (error) {
      clear(host);
      const retry = button('Retry', function () {
        cache.clear();
        render(tabId, true);
      }, true);
      add(host, [pageHeader('Signal Desk', 'The requested operational data could not be loaded.', null, retry), n('div', 'ops-error', error.message || String(error))]);
      console.error('Ops v2 render failed:', error);
    }
  }

  window.OpsV2 = {render: render, state: state, openTestGroupHistory: openTestGroupHistory};

  function activeTab() {
    const panelEl = document.querySelector('.tab-panel.active');
    return panelEl && panelEl.id ? panelEl.id.replace(/^tab-/, '') : 'projects';
  }

  document.addEventListener('DOMContentLoaded', function () {
    render(activeTab());
  });
})();
