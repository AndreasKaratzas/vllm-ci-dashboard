/**
 * vLLM AMD CI Operations v2.
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
  let operationsManifestPromise = null;
  let chartLibraryPromise = null;
  const SOURCE_ASSETS = {
    operations: 'data/vllm/ci/operations_v2_manifest.json',
    operationsManifest: 'data/vllm/ci/operations_v2_manifest.json',
    queueHistory: 'data/vllm/ci/queue_timeseries.jsonl',
    perf: 'data/vllm/perf_eval/perf_eval.json',
    amdPipeline: 'https://buildkite.com/vllm/amd-ci',
  };
  const CHART_LIBRARY_URL = 'https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js';
  const state = {
    healthView: 'overview',
    healthCoverageSort: 'platform',
    healthReduceDuplicates: true,
    healthIgnoreMi355Only: true,
    analyticsView: 'groups',
    analyticsPipeline: 'amd-ci',
    homeWork: 'issues',
    healthSearch: '',
    healthPlan: 'all',
    healthResult: 'all',
    analyticsSearch: '',
    analyticsGroupId: '',
    analyticsGroupCohort: 'main',
    analyticsAmdFilter: 'attention',
    analyticsWindow: '24h',
    agentWindow: '7d',
    agentGpu: 'all',
    agentNode: '',
    agentCofail: '180',
    agentExclCancel: '1',
    agentNightly: '0',
    agentSignal: 'infra',
    queueScope: 'amd',
    queueView: 'current',
    queueRange: '24h',
    queueHistoryQueue: 'fleet',
    queueIncludeIdle: false,
    trajectoryWindow: '24h',
    trajectoryWorkload: 'all',
    trajectoryHardware: 'all',
    trajectorySearch: '',
    omniRange: '24h',
    omniAge: 'all',
    perfView: 'performance',
    perfModel: 'all',
    perfDevice: 'all',
  };

  const ROUTE_QUERY_KEYS = {
    'ci-health': new Set(['ops_health_view', 'ops_health_sort', 'ops_health_result']),
    'ci-analytics': new Set([
      'ops_analytics_view', 'ops_analytics_pipeline', 'ops_analytics_search',
      'ops_analytics_group', 'ops_analytics_cohort', 'ops_analytics_amd_filter',
      'ops_analytics_window', 'ops_agent_window', 'ops_agent_gpu', 'ops_agent_node',
      'ops_agent_cofail', 'ops_agent_excl_cancel', 'ops_agent_nightly',
      'ops_agent_signal', 'ops_detail',
    ]),
    'ci-queue': new Set(['ops_queue_view', 'ops_queue_range', 'ops_queue_scope', 'ops_queue_history_queue', 'ops_detail']),
    'ci-hotness': new Set(['ops_trajectory_window', 'ops_detail']),
    'ci-omni': new Set(['ops_omni_range', 'ops_omni_age', 'ops_detail']),
    'ci-perf-eval': new Set(['ops_perf_view', 'ops_perf_model', 'ops_perf_device', 'ops_detail']),
  };
  const ROUTE_DEFAULTS = {
    health_view: 'overview',
    health_sort: 'platform',
    health_result: 'all',
    analytics_view: 'groups',
    analytics_pipeline: 'amd-ci',
    analytics_search: '',
    analytics_group: '',
    analytics_cohort: 'main',
    analytics_amd_filter: 'attention',
    analytics_window: '24h',
    agent_window: '7d',
    agent_gpu: 'all',
    agent_node: '',
    agent_cofail: '180',
    agent_excl_cancel: '1',
    agent_nightly: '0',
    agent_signal: 'infra',
    queue_view: 'current',
    queue_range: '24h',
    queue_scope: 'amd',
    queue_history_queue: 'fleet',
    trajectory_window: '24h',
    omni_range: '24h',
    omni_age: 'all',
    perf_view: 'performance',
    perf_model: 'all',
    perf_device: 'all',
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
    if (['failed', 'hard', 'incident', 'error', 'red', 'critical', 'surge', 'broken'].includes(stateName)) return 'is-danger';
    if (['soft', 'soft_fail', 'soft_failed', 'warning', 'attention', 'elevated', 'waiting'].includes(stateName)) return 'is-warning';
    if (['new', 'info', 'recurring'].includes(stateName)) return 'is-info';
    return 'is-neutral';
  }

  function normalizeLabel(label) {
    return String(label || '').trim().replace(/\s+/g, ' ').toLowerCase();
  }

  function compareText(left, right) {
    return String(left || '').localeCompare(String(right || ''), undefined, {sensitivity: 'base'});
  }

  function isRetiredQueue(queue) {
    const name = String(queue || '').trim().toLowerCase();
    return /^amd_mi355b(?:_|$)/i.test(name);
  }

  function isAmdQueue(queue) {
    const name = String(queue || '').toLowerCase();
    return (name === 'amd-cpu' || name.startsWith('amd_')) && !isRetiredQueue(name);
  }

  function hardwareDisplayLabel(hardware) {
    const id = String(hardware || 'unknown').toLowerCase();
    if (id === 'unknown') return 'Unknown';
    if (/^(?:mi\d|[abh]\d|l4$|t4$|cpu$|gpu$|npu$|tpu$)/.test(id)) return id.toUpperCase();
    return id;
  }

  function appendHardwareOptions(select, hardware, current) {
    const all = n('option', '', 'All hardware');
    all.value = 'all';
    all.selected = current === 'all';
    select.append(all);
    const remaining = new Set(hardware);
    [
      {label: 'AMD', matches: function (id) { return /^mi\d/.test(id); }},
      {label: 'NVIDIA', matches: function (id) { return /^(?:a|b|h)\d/.test(id) || ['l4', 't4'].includes(id); }},
      {label: 'General', matches: function (id) { return ['cpu', 'gpu', 'npu', 'tpu'].includes(id); }},
      {label: 'Other', matches: function () { return true; }},
    ].forEach(function (family) {
      const matches = Array.from(remaining).filter(family.matches);
      if (!matches.length) return;
      const group = n('optgroup');
      group.label = family.label;
      matches.forEach(function (id) {
        const option = n('option', '', hardwareDisplayLabel(id));
        option.value = id;
        option.selected = id === current;
        group.append(option);
        remaining.delete(id);
      });
      select.append(group);
    });
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
      const isDefault = Object.prototype.hasOwnProperty.call(ROUTE_DEFAULTS, name)
        && String(next) === String(ROUTE_DEFAULTS[name]);
      if (next === null || next === undefined || next === '' || isDefault) url.searchParams.delete(queryName(name));
      else url.searchParams.set(queryName(name), String(next));
      window.history.replaceState(null, '', url.pathname + url.search + url.hash);
    } catch (_) {}
  }

  function pruneRouteQuery(tabId) {
    try {
      const url = new URL(window.location.href);
      const allowed = ROUTE_QUERY_KEYS[tabId] || new Set();
      let changed = false;
      Array.from(url.searchParams.keys()).forEach(function (key) {
        if (key.startsWith('ops_') && !allowed.has(key)) {
          url.searchParams.delete(key);
          changed = true;
        }
      });
      if (changed) window.history.replaceState(null, '', url.pathname + url.search + url.hash);
    } catch (_) {}
  }

  function setRouteState(tabId, key, next, queryKey) {
    state[key] = next;
    setQueryValue(queryKey || key, next);
    render(tabId, true);
  }

  function syncRouteState(tabId) {
    const specs = {
      'ci-health': [
        ['healthView', 'health_view', ['overview', 'targets', 'gating', 'coverage', 'diagnostics']],
        ['healthCoverageSort', 'health_sort', ['platform', 'name', 'area']],
        ['healthResult', 'health_result', ['all', 'incident', 'unobserved', 'passed']],
      ],
      'ci-analytics': [
        ['analyticsView', 'analytics_view', ['groups', 'flakes', 'nightlies', 'retries', 'latency', 'agent-health']],
        ['analyticsPipeline', 'analytics_pipeline', ['ci', 'amd-ci']],
        ['analyticsSearch', 'analytics_search', null],
        ['analyticsGroupId', 'analytics_group', null],
        ['analyticsGroupCohort', 'analytics_cohort', ['main', 'nightly']],
        ['analyticsAmdFilter', 'analytics_amd_filter', ['attention', 'all', 'passing', 'incident', 'missing', 'mixed']],
        ['analyticsWindow', 'analytics_window', ['1h', '3h', '6h', '24h', '7d', '30d']],
        ['agentWindow', 'agent_window', ['1d', '3d', '7d', '14d', '30d', '60d']],
        ['agentGpu', 'agent_gpu', null],
        ['agentNode', 'agent_node', null],
        ['agentCofail', 'agent_cofail', ['30', '60', '120', '180', '360', '720', '1440']],
        ['agentExclCancel', 'agent_excl_cancel', ['0', '1']],
        ['agentNightly', 'agent_nightly', ['0', '1']],
        ['agentSignal', 'agent_signal', ['infra', 'hard', 'all']],
      ],
      'ci-queue': [
        ['queueView', 'queue_view', ['current', 'history', 'jobs']],
        ['queueRange', 'queue_range', ['24h', '7d', '30d']],
        ['queueScope', 'queue_scope', ['amd', 'all']],
        ['queueHistoryQueue', 'queue_history_queue', null],
      ],
      'ci-hotness': [['trajectoryWindow', 'trajectory_window', ['24h', '72h', '7d', '30d']]],
      'ci-omni': [
        ['omniRange', 'omni_range', ['1h', '3h', '6h', '12h', '24h', '72h']],
        ['omniAge', 'omni_age', ['all', 'lt1h', '1to3h', '3to6h', '6to12h', '12to24h', '1to3d', 'gte3d']],
      ],
      'ci-perf-eval': [
        ['perfView', 'perf_view', ['performance', 'accuracy']],
        ['perfModel', 'perf_model', null],
        ['perfDevice', 'perf_device', null],
      ],
    };
    pruneRouteQuery(tabId);
    (specs[tabId] || []).forEach(function (spec) {
      const next = queryValue(spec[1]);
      const fallback = Object.prototype.hasOwnProperty.call(ROUTE_DEFAULTS, spec[1]) ? ROUTE_DEFAULTS[spec[1]] : '';
      state[spec[0]] = next && (!spec[2] || spec[2].includes(next)) ? next : fallback;
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
    state.analyticsGroupId = '';
    state.analyticsView = 'groups';
    setQueryValue('analytics_search', state.analyticsSearch);
    setQueryValue('analytics_group', null);
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
  let overlayStack = [];
  let overlayKeyHandler = null;

  function destroyOverlay(frame) {
    if (!frame) return;
    for (const [key, chart] of charts.entries()) {
      if (chart && chart.canvas && frame.root.contains(chart.canvas)) {
        chart.destroy();
        charts.delete(key);
      }
    }
    frame.root.remove();
  }

  function removeOverlayKeyHandler() {
    if (overlayKeyHandler) document.removeEventListener('keydown', overlayKeyHandler);
    overlayKeyHandler = null;
  }

  function installOverlayKeyHandler() {
    if (overlayKeyHandler) return;
    overlayKeyHandler = function (event) {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeOverlay();
        return;
      }
      const shell = activeOverlay && activeOverlay.shell;
      if (event.key !== 'Tab' || !shell) return;
      const focusable = Array.from(shell.querySelectorAll('a[href], button:not([disabled]), input, select, textarea, [tabindex]:not([tabindex="-1"])'));
      if (!focusable.length) return;
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', overlayKeyHandler);
  }

  function restoreOverlayCharts(frame) {
    requestAnimationFrame(function () {
      for (const chart of charts.values()) {
        if (chart && chart.canvas && frame.root.contains(chart.canvas) && typeof chart.resize === 'function') chart.resize();
      }
    });
  }

  function backOverlay() {
    if (!activeOverlay) return;
    const current = activeOverlay;
    const trigger = current.trigger;
    destroyOverlay(current);
    if (overlayStack.length) {
      activeOverlay = overlayStack.pop();
      activeOverlay.root.hidden = false;
      activeOverlay.root.removeAttribute('aria-hidden');
      setQueryValue('detail', activeOverlay.detailKey);
      restoreOverlayCharts(activeOverlay);
      if (trigger && activeOverlay.root.contains(trigger) && trigger.focus) trigger.focus();
      else activeOverlay.back.focus();
      return;
    }
    activeOverlay = null;
    document.body.classList.remove('ops-overlay-open');
    removeOverlayKeyHandler();
    setQueryValue('detail', null);
    if (trigger && trigger.focus) trigger.focus();
  }

  function closeOverlay() {
    const frames = overlayStack.concat(activeOverlay ? [activeOverlay] : []);
    if (!frames.length) return;
    const trigger = frames[0].trigger;
    frames.slice().reverse().forEach(destroyOverlay);
    activeOverlay = null;
    overlayStack = [];
    document.body.classList.remove('ops-overlay-open');
    removeOverlayKeyHandler();
    setQueryValue('detail', null);
    if (trigger && trigger.focus) trigger.focus();
  }

  function openOverlay(title, subtitle, content, wide, detailKey) {
    const trigger = document.activeElement;
    const hasParent = Boolean(activeOverlay);
    if (activeOverlay) {
      activeOverlay.root.hidden = true;
      activeOverlay.root.setAttribute('aria-hidden', 'true');
      overlayStack.push(activeOverlay);
    }
    const root = n('div', 'ops-overlay ops-detail-overlay');
    const shell = n('section', 'ops-overlay-panel ops-detail-drawer' + (wide ? ' is-wide' : ''));
    shell.setAttribute('role', 'dialog');
    shell.setAttribute('aria-modal', 'true');
    const titleId = 'ops-dialog-title-' + Date.now();
    shell.setAttribute('aria-labelledby', titleId);

    const header = n('header', 'ops-overlay-header');
    const back = n('button', 'ops-overlay-back', '\u2190');
    back.type = 'button';
    back.setAttribute('aria-label', hasParent ? 'Back to previous dialog' : 'Back to dashboard');
    back.title = hasParent ? 'Back to previous dialog' : 'Back to dashboard';
    back.addEventListener('click', backOverlay);
    const heading = n('div', 'ops-overlay-heading');
    const headingText = n('h2', 'ops-overlay-title', title);
    headingText.id = titleId;
    heading.append(headingText);
    if (subtitle) heading.append(n('p', 'ops-overlay-subtitle', subtitle));
    const close = n('button', 'ops-overlay-close', '\u00d7');
    close.type = 'button';
    close.setAttribute('aria-label', 'Close dialog');
    close.addEventListener('click', closeOverlay);
    add(header, [back, heading, close]);

    const body = n('div', 'ops-overlay-body ops-page');
    body.append(content);
    add(shell, [header, body]);
    root.append(shell);
    root.addEventListener('click', function (event) {
      if (event.target === root) closeOverlay();
    });
    document.body.append(root);
    document.body.classList.add('ops-overlay-open');
    const resolvedDetailKey = detailKey || title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '');
    activeOverlay = {root: root, shell: shell, trigger: trigger, back: back, detailKey: resolvedDetailKey};
    installOverlayKeyHandler();
    setQueryValue('detail', resolvedDetailKey);
    back.focus();
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
    // Optional, backward-compatible column sorting: active only when the caller
    // passes options.onSort. Columns opt in via col.sortKey.
    const sortState = geometry.sort || {};
    const onSort = typeof geometry.onSort === 'function' ? geometry.onSort : null;
    for (const col of columns) {
      const alignment = col.numeric ? 'numeric' : col.align || 'text';
      const th = n('th', (alignment === 'numeric' ? 'is-numeric ' : alignment === 'center' ? 'is-center ' : '') + (col.sticky ? 'is-sticky-left' : ''));
      th.scope = 'col';
      th.dataset.align = alignment;
      if (onSort && col.sortKey) {
        const active = sortState.key === col.sortKey;
        const arrow = active ? (sortState.dir === 'asc' ? ' ▲' : ' ▼') : '';
        const sortBtn = n('button', 'ops-sort-header' + (active ? ' is-active' : ''), col.label + arrow);
        sortBtn.type = 'button';
        sortBtn.setAttribute('aria-label', 'Sort by ' + col.label);
        th.setAttribute('aria-sort', active ? (sortState.dir === 'asc' ? 'ascending' : 'descending') : 'none');
        sortBtn.addEventListener('click', function () { onSort(col.sortKey); });
        th.append(sortBtn);
      } else {
        th.append(document.createTextNode(col.label));
      }
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

  function defaultTableSearchText(row) {
    if (!row || typeof row !== 'object') return String(row || '');
    return Object.values(row).filter(function (part) {
      return ['string', 'number', 'boolean'].includes(typeof part);
    }).join(' ');
  }

  function openTableBrowser(config) {
    const rows = Array.isArray(config.rows) ? config.rows : [];
    const pageSize = Number(config.pageSize || 50);
    const content = n('div', 'ops-table-browser');
    const toolbar = n('div', 'ops-toolbar ops-browser-toolbar');
    const search = n('input', 'ops-input');
    search.type = 'search';
    search.placeholder = config.searchPlaceholder || 'Filter evidence';
    search.setAttribute('aria-label', config.searchLabel || search.placeholder);
    search.value = config.initialQuery || '';
    const count = n('span', 'ops-browser-count');
    add(toolbar, [search, n('div', 'ops-toolbar-spacer'), count]);
    const tableHost = n('div', 'ops-evidence-table-host');
    const pager = n('div', 'ops-browser-pagination');
    const previous = button('Previous', function () { page -= 1; renderRows(); });
    const position = n('span', 'ops-browser-position');
    const next = button('Next', function () { page += 1; renderRows(); });
    add(pager, [previous, position, next]);
    add(content, [toolbar, tableHost, pager]);
    let page = 0;

    function renderRows() {
      const query = normalizeLabel(search.value);
      const searchable = config.searchText || defaultTableSearchText;
      const filtered = query ? rows.filter(function (row) {
        return normalizeLabel(searchable(row)).includes(query);
      }) : rows;
      const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
      page = Math.max(0, Math.min(page, pageCount - 1));
      const start = page * pageSize;
      const visible = filtered.slice(start, start + pageSize);
      clear(tableHost);
      tableHost.append(dataTable(
        config.columns,
        visible,
        filtered.length ? integer(start + 1) + '-' + integer(start + visible.length) + ' of ' + integer(filtered.length) + ' matching rows' : 'No matching evidence',
        config.geometry || {}
      ));
      count.textContent = integer(filtered.length) + ' of ' + integer(rows.length) + ' rows';
      position.textContent = 'Page ' + integer(page + 1) + ' of ' + integer(pageCount);
      previous.disabled = page === 0;
      next.disabled = page >= pageCount - 1;
      pager.hidden = filtered.length <= pageSize;
    }

    search.addEventListener('input', function () { page = 0; renderRows(); });
    renderRows();
    openOverlay(config.title, config.subtitle || integer(rows.length) + ' evidence rows', content, true, config.id || 'table-browser');
    requestAnimationFrame(function () { search.focus(); });
  }

  function compactTablePanel(title, meta, columns, rows, options) {
    const config = options || {};
    const limit = Number(config.limit || 12);
    const preview = rows.slice(0, limit);
    const previewLabel = config.previewLabel || 'priority rows';
    const root = panel(
      title,
      meta,
      dataTable(columns, preview, integer(preview.length) + ' ' + previewLabel + ' of ' + integer(rows.length), config.geometry || {}),
      config.className || ''
    );
    if (config.headerActions) {
      const header = root.firstElementChild;
      const metaNode = header.querySelector('.ops-panel-meta');
      const trailing = n('div', 'ops-panel-header-trailing');
      header.classList.add('has-actions');
      add(trailing, [config.headerActions, metaNode]);
      header.append(trailing);
    }
    if (rows.length > limit || config.alwaysBrowse) {
      const footer = n('footer', 'ops-panel-footer ops-browser-footer');
      add(footer, [
        n('span', '', 'Showing ' + integer(preview.length) + ' of ' + integer(rows.length)),
        button(config.buttonLabel || 'Browse all ' + integer(rows.length), function () {
          openTableBrowser({
            id: config.id,
            title: config.browserTitle || title,
            subtitle: config.browserSubtitle || meta,
            rows: rows,
            columns: columns,
            geometry: config.geometry,
            searchText: config.searchText,
            searchPlaceholder: config.searchPlaceholder,
            searchLabel: config.searchLabel,
            initialQuery: config.initialQuery,
            pageSize: config.pageSize,
          });
        }),
      ]);
      root.append(footer);
    }
    return root;
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
    const historyColumns = [
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
    ];
    content.append(compactTablePanel('Retained evidence', integer(rows.length) + ' source-backed observations', historyColumns, rows, {
      id: 'history-evidence-browser',
      limit: 30,
      browserTitle: title + ' evidence',
      browserSubtitle: subtitle || 'Select an observation to inspect its exact evidence',
      searchPlaceholder: 'Filter observation, value, date, or source',
      searchText: function (point) { return [historyPointLabel(point), point.timestamp, point.ts, point.date, point.valueSummary, point.value].join(' '); },
      geometry: {name: 'history-evidence', minWidth: '780px'},
    }));
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
    return ['hard', 'soft', 'incident', 'error', 'failed', 'failing', 'soft_fail', 'soft_failed', 'timed_out', 'broken', 'canceled', 'expired']
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

  function trailingPassStats(observations, windowSize) {
    const size = Math.max(1, Number(windowSize || 10));
    return observations.map(function (_, index) {
      const windowRows = observations.slice(Math.max(0, index - size + 1), index + 1);
      const passed = windowRows.filter(function (row) { return observationState(row) === 'passed'; }).length;
      return {
        passed: passed,
        total: windowRows.length,
        rate: windowRows.length ? passed / windowRows.length * 100 : null,
      };
    });
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
      const rollingPass = trailingPassStats(observations, 10);
      const rollingRates = rollingPass.map(function (row) { return row.rate; });
      const currentRolling = rollingPass[rollingPass.length - 1] || {};
      const outcomeSeries = [
        {label: 'Passed run', state: 'passed', color: '#35bb78'},
        {label: 'Soft incident', state: 'soft', color: '#e3a63a'},
        {label: 'Hard incident', state: 'hard', color: '#e06464'},
      ];
      const chartGrid = n('div', 'ops-grid ops-grid-2');
      const chartKey = 'group-' + String(candidate.id || candidate.name || 'history').replace(/[^a-z0-9]+/gi, '-').toLowerCase() + '-' + historyMode;
      const outcomeChart = chartPanel(
        'Outcome trend',
        'Current trailing 10: ' + Number(currentRolling.rate || 0).toFixed(1) + '% (' + integer(currentRolling.passed) + '/' + integer(currentRolling.total) + '); bar color is the exact result',
        chartKey + '-outcome'
      );
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
          type: 'bar',
          data: {
            labels: labels,
            datasets: outcomeSeries.map(function (series) {
              return {
                label: series.label,
                data: observations.map(function (observation, index) {
                  const stateName = observationState(observation);
                  const matches = series.state === 'passed'
                    ? stateName === 'passed'
                    : series.state === 'soft'
                      ? ['soft', 'soft_fail', 'soft_failed'].includes(stateName)
                      : isIncidentObservation(observation) && !['soft', 'soft_fail', 'soft_failed'].includes(stateName);
                  return matches ? rollingRates[index] : null;
                }),
                backgroundColor: series.color,
                borderColor: series.color,
                borderWidth: 0,
                borderRadius: 2,
                categoryPercentage: 0.92,
                barPercentage: 0.92,
                minBarLength: 4,
                order: 2,
              };
            }).concat([{
              type: 'line',
              label: 'Trailing 10-run pass rate',
              data: rollingRates,
              borderColor: '#64a8e8',
              backgroundColor: '#64a8e8',
              borderWidth: 2,
              pointRadius: 0,
              pointHoverRadius: 4,
              stepped: 'after',
              tension: 0,
              order: 1,
            }]),
          },
          options: {
            interaction: {mode: 'index', intersect: false},
            plugins: {tooltip: {callbacks: {label: function (item) {
              const index = item.dataIndex;
              if (item.dataset.type === 'line') {
                const stat = rollingPass[index] || {};
                return 'Trailing pass rate: ' + Number(stat.rate || 0).toFixed(1) + '% (' + integer(stat.passed) + ' / ' + integer(stat.total) + ')';
              }
              return 'Result: ' + historyOutcomeLabel(observations[index]);
            }}}},
            scales: {
              x: {grid: {display: false}, ticks: {maxTicksLimit: 8}},
              y: {min: 0, max: 100, title: {display: true, text: 'Trailing pass rate'}, ticks: {stepSize: 20, callback: function (tick) { return tick + '%'; }}},
            },
          },
          evidenceTitle: (candidate.name || 'Test group') + ' exact outcomes and trailing pass rate',
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
    let full = row.name || row.label || row.group || 'Unnamed group';
    const hardware = row.hardware || row.hw;
    if (hardware) full += ' [' + hardware + ']';
    const desktopLimit = maxLength || 46;
    const limit = window.innerWidth <= 767 ? Math.min(desktopLimit, 20) : desktopLimit;
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

  const TARGET_RESOLUTION_LABELS = {
    matched: 'Matched AMD definition',
    no_amd_definition: 'No one-to-one AMD definition',
    stale_target_alias: 'Target mapping needs review',
    ambiguous: 'Ambiguous AMD mapping',
    not_observed: 'Not observed in latest AMD build',
  };

  const TARGET_RESOLUTION_METHOD_LABELS = {
    exact_matrix_label: 'Exact matrix label',
    shard_template: 'Authorized shard template',
    definition_parity: 'Definition parity identity',
  };

  function humanizeIdentifier(identifier) {
    return String(identifier || '').replaceAll('_', ' ').trim();
  }

  function targetResolutionPresentation(group) {
    const resolution = (group || {}).runtime_resolution || {};
    const status = String(resolution.status || '').toLowerCase();
    const latestState = observationState((group || {}).latest_amd_result || {});
    const fallbackStatus = latestState === 'passed' || isIncidentObservation({state: latestState})
      ? 'matched'
      : 'not_observed';
    const normalizedStatus = TARGET_RESOLUTION_LABELS[status] ? status : fallbackStatus;
    const commits = resolution.source_commits || {};
    const labels = Array.isArray(resolution.amd_definition_labels)
      ? resolution.amd_definition_labels.filter(Boolean)
      : [];
    const alignment = String(resolution.source_alignment || 'unavailable');
    const alignmentLabels = {
      same_commit: 'AMD matrix and definition parity use the same commit',
      different_commits: 'AMD matrix and definition parity use different commits',
      unavailable: 'Source-commit alignment unavailable',
    };
    return {
      status: normalizedStatus,
      label: TARGET_RESOLUTION_LABELS[normalizedStatus],
      reason: String(resolution.reason || '').trim(),
      method: String(resolution.method || '').trim(),
      methodLabel: TARGET_RESOLUTION_METHOD_LABELS[resolution.method]
        || humanizeIdentifier(resolution.method)
        || 'Unavailable',
      targetIdentityKey: String(resolution.target_identity_key || '').trim(),
      amdDefinitionLabels: labels,
      candidateCount: resolution.candidate_count !== null
        && resolution.candidate_count !== undefined
        && resolution.candidate_count !== ''
        && Number.isFinite(Number(resolution.candidate_count))
        ? Number(resolution.candidate_count)
        : null,
      mappingQuality: humanizeIdentifier(resolution.mapping_quality),
      commandSimilarityPct: resolution.command_similarity_pct !== null
        && resolution.command_similarity_pct !== undefined
        && Number.isFinite(Number(resolution.command_similarity_pct))
        ? Number(resolution.command_similarity_pct)
        : null,
      sourceCommits: {
        amdMatrix: String(commits.amd_matrix || '').trim(),
        definitionParity: String(commits.definition_parity || '').trim(),
      },
      sourceAlignment: alignment,
      sourceAlignmentLabel: alignmentLabels[alignment] || humanizeIdentifier(alignment),
      sourceUrls: resolution.source_urls || {},
    };
  }

  function targetAssessmentText(group) {
    const stateName = observationState((group || {}).latest_amd_result || {});
    const unresolved = stateName !== 'passed' && !isIncidentObservation({state: stateName});
    const resolution = targetResolutionPresentation(group);
    if (unresolved) {
      return resolution.reason
        ? resolution.label + ' - ' + resolution.reason
        : resolution.label;
    }
    return humanizeIdentifier((group || {}).assessment) || resolution.label;
  }

  function targetNoSignalBreakdown(groups) {
    const result = {noDefinition: 0, needsReview: 0, notObserved: 0};
    for (const group of groups || []) {
      const status = targetResolutionPresentation(group).status;
      if (status === 'no_amd_definition') result.noDefinition += 1;
      else if (status === 'stale_target_alias' || status === 'ambiguous') result.needsReview += 1;
      else result.notObserved += 1;
    }
    return result;
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
    const resolution = targetResolutionPresentation(group);
    const shortCommit = function (commit) {
      return commit ? String(commit).slice(0, 12) : null;
    };
    openDetailDrawer({
      id: 'gating-' + (group.id || group.label),
      title: group.label || group.name || 'Reviewed target',
      subtitle: 'Reviewed plan, current AMD signal, and upstream history',
      description: resolution.reason || 'Plan membership is configuration intent. The latest result is AMD; reliability, streaks, and incidents are upstream.',
      fields: [
        {label: 'Reviewed plan', value: plan.label || plan.status},
        {label: 'Plan note', value: plan.note},
        {label: 'Latest AMD result', value: latest.state},
        {label: 'Assessment', value: targetAssessmentText(group)},
        {label: 'Runtime resolution', value: resolution.label},
        {label: 'Resolution method', value: resolution.methodLabel},
        {label: 'Target identity', value: resolution.targetIdentityKey},
        {label: 'AMD definitions', value: resolution.amdDefinitionLabels.join(', ')},
        {label: 'Candidate definitions', value: resolution.candidateCount !== null ? integer(resolution.candidateCount) : null},
        {label: 'Mapping quality', value: resolution.mappingQuality},
        {label: 'Command similarity', value: resolution.commandSimilarityPct !== null ? resolution.commandSimilarityPct.toFixed(1) + '%' : null},
        {label: 'Source alignment', value: resolution.sourceAlignmentLabel},
        {label: 'AMD matrix commit', value: shortCommit(resolution.sourceCommits.amdMatrix)},
        {label: 'Definition parity commit', value: shortCommit(resolution.sourceCommits.definitionParity)},
        {label: 'Upstream main runs', value: main.runs !== undefined ? integer(main.runs) : null},
        {label: 'Upstream main incidents', value: main.incident_count !== undefined ? integer(main.incident_count) + ' (' + value(main.incident_rate_pct) + '%)' : null},
        {label: 'Upstream nightly streak', value: group.nightly_green_streak !== undefined ? integer(group.nightly_green_streak) : null},
        {label: 'Last upstream incident', value: group.last_incident ? shortDate(group.last_incident.observed_at) : null},
      ],
      sources: [
        plan.source_url ? {label: 'Open reviewed configuration', url: plan.source_url} : null,
        gatingEvidenceUrl(group) ? {label: 'Open latest AMD evidence', url: gatingEvidenceUrl(group)} : null,
        resolution.sourceUrls.amd_matrix ? {label: 'Open AMD matrix definition source', url: resolution.sourceUrls.amd_matrix} : null,
        resolution.sourceUrls.definition_parity ? {label: 'Open definition-parity source', url: resolution.sourceUrls.definition_parity} : null,
        {label: 'Open published reliability catalog', url: SOURCE_ASSETS.operations},
      ],
      content: content,
    });
  }

  function openGatingDetailWithEvidence(group, ops) {
    if (reliabilityCatalog(canonicalReliability(ops)).length) {
      openGatingDetail(group, ops);
      return;
    }
    loadOperationSections(ops, ['reliability']).then(function (expanded) {
      openGatingDetail(group, expanded);
    }).catch(function (error) {
      console.error('Reviewed target reliability load failed:', error);
      openGatingDetail(group, ops);
    });
  }

  function openGroupDetailWithEvidence(group, ops) {
    if (reliabilityCatalog(canonicalReliability(ops)).length) {
      openGroupDetail(group, ops);
      return;
    }
    loadOperationSections(ops, ['reliability']).then(function (expanded) {
      openGroupDetail(group, expanded);
    }).catch(function (error) {
      console.error('Reliability evidence load failed:', error);
      openGroupDetail(group, ops);
    });
  }

  function openDefinitionDetail(row, definitionParity) {
    const content = n('div', 'ops-stack');
    function commandPanel(title, commands) {
      const pre = n('pre', 'ops-code-block');
      pre.textContent = (commands || []).length ? commands.join('\n') : 'No command list is present in this definition.';
      return panel(title, integer((commands || []).length) + ' normalized command lines', pre);
    }
    if (row.amd_commands || row.category === 'amd_only') {
      content.append(commandPanel('AMD command definition', row.amd_commands || (row.category === 'amd_only' ? row.commands : [])));
    }
    if (row.nvidia_commands || row.category === 'upstream_only') {
      content.append(commandPanel('Upstream command definition', row.nvidia_commands || (row.category === 'upstream_only' ? row.commands : [])));
    }
    const source = (definitionParity || {}).source || {};
    openDetailDrawer({
      id: 'definition-' + (row.identity_key || row.amd_label || row.nvidia_label || row.label),
      title: row.amd_label || row.label || row.nvidia_label || 'CI definition',
      subtitle: 'Commit-pinned vLLM CI source comparison',
      description: row.category === 'matched'
        ? (row.match_method === 'command_twin'
          ? 'The titles differ, but this unique candidate has an exact normalized command match and passed the platform-neutral title threshold.'
          : 'The AMD and upstream YAML definitions share the same normalized identity. Command similarity is reported separately.')
        : row.category === 'amd_only'
          ? 'No unique upstream identity or exact-command twin was found for this AMD definition.'
          : 'No test-amd.yaml identity or exact-command twin was found for this upstream definition.',
      fields: [
        {label: 'AMD definition', value: row.amd_label || (row.category === 'amd_only' ? row.label : null)},
        {label: 'Upstream definition', value: row.nvidia_label || (row.category === 'upstream_only' ? row.label : null)},
        {label: 'Match rule', value: row.match_method === 'command_twin' ? 'Exact-command twin' : row.match_method === 'identity' ? 'Normalized identity' : 'Unmatched'},
        {label: 'Command similarity', value: row.command_similarity !== undefined ? (Number(row.command_similarity) * 100).toFixed(1) + '%' : null},
        {label: 'Title similarity', value: row.title_similarity !== undefined ? (Number(row.title_similarity) * 100).toFixed(1) + '%' : null},
        {label: 'Identity', value: row.identity_key},
        {label: 'vLLM commit', value: source.commit_sha ? source.commit_sha.slice(0, 12) : null},
      ],
      sources: [
        row.amd_source_url || (row.category === 'amd_only' ? row.source_url : null) ? {label: 'Open AMD YAML', url: row.amd_source_url || row.source_url} : null,
        row.nvidia_source_url || (row.category === 'upstream_only' ? row.source_url : null) ? {label: 'Open upstream YAML', url: row.nvidia_source_url || row.source_url} : null,
        source.commit_url ? {label: 'Open pinned vLLM commit', url: source.commit_url} : null,
      ],
      content: content,
    });
  }

  function openBuildDetail(build, title) {
    const sourcePipeline = build.source_pipeline || 'amd-ci';
    const transitions = build.transitions || {};
    const content = n('div', 'ops-stack');
    if (build.has_test_results === false) {
      const blocked = Number(build.test_jobs_blocked || 0);
      content.append(n(
        'div',
        'ops-evidence-note ' + (blocked ? 'is-danger' : 'is-warning'),
        blocked
          ? 'This nightly failed before test execution. ' + integer(blocked) + ' test groups were dependency-blocked, so no pass/fail movement is inferred.'
          : 'This build has no parsed test-group signal. No pass/fail movement is inferred.',
      ));
    }
    const rows = [];
    [['New', transitions.new || []], ['Recurring', transitions.recurring || []], ['Fixed', transitions.fixed || []]].forEach(function (bucket) {
      bucket[1].forEach(function (group) { rows.push(Object.assign({lifecycle: bucket[0]}, group)); });
    });
    if (rows.length) {
      const transitionColumns = [
      {label: 'Group', sticky: true, render: function (row) { return externalLink(row.display_name || row.name, exactPipelineEvidenceUrl(row, sourcePipeline)); }},
      {label: 'Lifecycle', render: function (row) { return linkedBadge(row.lifecycle, exactPipelineEvidenceUrl(row, sourcePipeline), null, row.lifecycle === 'New' ? 'is-danger' : row.lifecycle === 'Fixed' ? 'is-success' : 'is-warning'); }},
      {label: 'Queue', render: function (row) { return n('span', 'ops-mono', value(row.queue)); }},
      ];
      content.append(compactTablePanel('Changed test groups', integer(rows.length) + ' lifecycle observations', transitionColumns, rows, {
        id: 'build-transition-browser',
        limit: 30,
        browserSubtitle: 'New, recurring, and fixed groups in this exact Buildkite nightly comparison',
        searchPlaceholder: 'Filter group, lifecycle, or queue',
        searchText: function (row) { return [row.display_name, row.name, row.lifecycle, row.queue].join(' '); },
      }));
    }
    openDetailDrawer({
      id: 'build-' + value(build.number),
      title: title || (build.number ? 'AMD build #' + build.number : 'AMD build'),
      subtitle: 'Build result and group lifecycle evidence',
      fields: [
        {label: 'State', value: value(build.state)},
        {label: 'Started', value: shortDate(build.created_at)},
        {label: 'Observed groups', value: integer(build.total_groups)},
        {label: 'Test signal', value: build.has_test_results === false ? 'Unavailable' : 'Observed'},
        {label: 'Dependency-blocked test groups', value: build.test_jobs_blocked ? integer(build.test_jobs_blocked) : null},
        {label: 'New / recurring / fixed', value: integer((transitions.new || []).length) + ' / ' + integer((transitions.recurring || []).length) + ' / ' + integer((transitions.fixed || []).length)},
      ],
      sources: exactPipelineBuildUrl(build, sourcePipeline) ? [{label: 'Open Buildkite build', url: exactPipelineBuildUrl(build, sourcePipeline)}] : [],
      content: content,
    });
  }

  function openQueueDetail(name, row, jobs) {
    const related = (jobs || []).filter(function (job) { return job.queue === name; });
    const p50Source = waitSourceDetail(row, 'p50');
    const p95Source = waitSourceDetail(row, 'p95');
    const p99Value = waitValue(row, 'p99');
    const sampleCount = waitSampleCount(row);
    const content = related.length ? dataTable([
      {label: 'Job', sticky: true, render: function (job) { return externalLink(job.name || 'Unnamed job', job.url); }},
      {label: 'State', render: function (job) { return linkedBadge(job.state || 'unknown', job.url); }},
      {label: 'Age', numeric: true, render: function (job) { return duration(job.wait_min !== undefined ? job.wait_min : job.run_min); }},
      {label: 'Build', render: function (job) { return externalLink((job.pipeline || '?') + ' #' + value(job.build), job.build_url || buildUrl(job.pipeline, job.build), 'ops-mono'); }},
    ], related, integer(related.length) + ' active jobs on this queue') : n('div', 'ops-empty', 'No active jobs are retained for this queue.');
    openDetailDrawer({
      id: 'queue-' + name,
      title: name,
      subtitle: 'Current queue state; official and scheduled-sample waits remain separate',
      fields: [
        {label: 'Running', value: integer(row.running)},
        {label: 'Waiting', value: integer(row.waiting)},
        {label: 'Connected agents', value: hasAgentMeasurement(row) ? integer(row.connected_agents !== undefined ? row.connected_agents : row.agents) : 'Unavailable'},
        {label: 'p50 official/fallback', value: duration(waitValue(row, 'p50')) + (p50Source ? ' - ' + p50Source : '')},
        {label: 'p95 official/fallback', value: duration(waitValue(row, 'p95')) + (p95Source ? ' - ' + p95Source : '')},
        {label: 'p99 scheduled sample', value: p99Value === null || p99Value === undefined ? 'Not measured' : duration(p99Value) + (sampleCount !== null ? ' - n=' + integer(sampleCount) : '')},
        {label: 'p99 source', value: value(waitSourceDetail(row, 'p99'))},
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

  function loadChartLibrary() {
    if (window.Chart) return Promise.resolve(window.Chart);
    if (!chartLibraryPromise) {
      chartLibraryPromise = new Promise(function (resolve, reject) {
        const script = document.createElement('script');
        script.src = CHART_LIBRARY_URL;
        script.async = true;
        script.addEventListener('load', function () {
          if (window.Chart) resolve(window.Chart);
          else reject(new Error('Chart.js loaded without exposing window.Chart'));
        });
        script.addEventListener('error', function () {
          reject(new Error('Chart.js could not be loaded'));
        });
        document.head.append(script);
      });
    }
    return chartLibraryPromise;
  }

  function drawChart(key, canvas, config) {
    if (!canvas) return;
    if (!window.Chart) {
      loadChartLibrary().then(function () {
        if (canvas.isConnected) drawChart(key, canvas, config);
      }).catch(function (error) {
        console.error('Chart library load failed:', error);
      });
      return;
    }
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

  function isPlainObject(value) {
    return value && typeof value === 'object' && !Array.isArray(value);
  }

  function mergeOperationPayload(target, source) {
    Object.entries(source || {}).forEach(function (entry) {
      const key = entry[0], value = entry[1];
      if (isPlainObject(value)) {
        target[key] = mergeOperationPayload(isPlainObject(target[key]) ? target[key] : {}, value);
      } else {
        target[key] = value;
      }
    });
    return target;
  }

  function operationSectionNames(tabId) {
    if (tabId === 'ci-health') {
      if (state.healthView === 'overview') return ['nightly', 'amd_test_health'];
      if (state.healthView === 'targets') return ['gating'];
      if (state.healthView === 'gating') return ['definition_parity'];
      if (state.healthView === 'diagnostics') return ['diagnostics'];
      return [];
    }
    if (tabId === 'ci-analytics') {
      if (state.analyticsView === 'groups') return ['amd_test_health'];
      if (state.analyticsView === 'agent-health') return ['amd_agent_health'];
      if (state.analyticsView === 'nightlies') return ['nightly'];
      return ['reliability'];
    }
    if (tabId === 'ci-queue') return ['queue'];
    if (tabId === 'ci-hotness') return ['reliability'];
    if (tabId === 'ci-omni') return ['omni', 'queue'];
    return [];
  }

  function resolveOperationSectionPath(relativePath) {
    const base = SOURCE_ASSETS.operationsManifest.slice(0, SOURCE_ASSETS.operationsManifest.lastIndexOf('/') + 1);
    return base + String(relativePath || '').replace(/^\/+/, '');
  }

  async function operationsManifest() {
    if (!operationsManifestPromise) {
      operationsManifestPromise = fetchJSON(SOURCE_ASSETS.operationsManifest);
    }
    return operationsManifestPromise;
  }

  async function loadOperationSections(ops, sectionNames) {
    const manifest = await operationsManifest();
    if (!manifest || !manifest.shell || !manifest.sections) {
      throw new Error('Operations manifest is incomplete');
    }
    const descriptors = sectionNames.map(function (name) {
      const descriptor = manifest.sections[name];
      if (!descriptor || !descriptor.path) throw new Error('Operations section "' + name + '" is missing from the manifest');
      return descriptor;
    });
    const sections = await Promise.all(descriptors.map(function (descriptor) {
      return fetchJSON(resolveOperationSectionPath(descriptor.path));
    }));
    const combined = mergeOperationPayload({}, ops || manifest.shell);
    sections.forEach(function (section) { mergeOperationPayload(combined, section); });
    return combined;
  }

  async function loadOperations(tabId) {
    const manifest = await operationsManifest();
    if (!manifest || !manifest.shell || !manifest.sections) {
      throw new Error('Operations manifest is incomplete');
    }
    return loadOperationSections(manifest.shell, operationSectionNames(tabId));
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

  function amdNightlyMovement(build) {
    const transitions = (build || {}).transitions || {};
    const hasComparison = Number(transitions.preceding_build_number || 0) > 0
      && ['new', 'recurring', 'fixed'].every(function (key) { return Array.isArray(transitions[key]); });
    const newlyIncident = Array.isArray(transitions.new) ? transitions.new.length : 0;
    const recurring = Array.isArray(transitions.recurring) ? transitions.recurring.length : 0;
    const fixed = Array.isArray(transitions.fixed) ? transitions.fixed.length : 0;
    return {
      hasComparison: hasComparison,
      newCount: newlyIncident,
      recurringCount: recurring,
      fixedCount: fixed,
      currentIncidents: newlyIncident + recurring,
      previousIncidents: recurring + fixed,
      delta: newlyIncident - fixed,
    };
  }

  function amdNightlyPresentation(build, healthSummary) {
    const record = build || {};
    const summary = healthSummary || {};
    const latestStates = summary.latest_state_counts || {};
    const signalBuild = Number(summary.latest_build_number || 0);
    const pipelineBuild = Number(record.number || 0);
    const pipelineState = String(record.state || 'unknown').toLowerCase();
    const inProgress = ['running', 'scheduled', 'creating', 'assigned', 'starting'].includes(pipelineState);
    const blocked = Number(record.test_jobs_blocked || 0);
    const explicitlyNoSignal = Object.prototype.hasOwnProperty.call(record, 'has_test_results') && !record.has_test_results;
    const summaryMatchesBuild = Number(summary.latest_group_count || 0) > 0
      && (!signalBuild || !pipelineBuild || signalBuild === pipelineBuild);

    function stateCount(keys, fallback) {
      for (const key of keys) {
        if (Object.prototype.hasOwnProperty.call(latestStates, key) && Number.isFinite(Number(latestStates[key]))) {
          return Number(latestStates[key]);
        }
      }
      return Number(fallback || 0);
    }

    const fallbackHard = Array.isArray(record.failed_groups) ? record.failed_groups.length : 0;
    const fallbackSoft = Array.isArray(record.soft_failed_groups) ? record.soft_failed_groups.length : 0;
    const fallbackTotal = Number(record.total_groups || 0);
    const hard = summaryMatchesBuild ? stateCount(['hard', 'failed'], fallbackHard) : fallbackHard;
    const soft = summaryMatchesBuild ? stateCount(['soft', 'soft_fail'], fallbackSoft) : fallbackSoft;
    const passed = summaryMatchesBuild
      ? stateCount(['passed'], Math.max(0, fallbackTotal - hard - soft))
      : Math.max(0, fallbackTotal - hard - soft);
    const observedGroups = summaryMatchesBuild ? Number(summary.latest_group_count || 0) : fallbackTotal;
    const hasSignal = !explicitlyNoSignal && Math.max(observedGroups, passed + soft + hard) > 0;
    const movement = amdNightlyMovement(record);
    const incidentCount = hard + soft;
    const comparisonReliable = hasSignal && movement.hasComparison && movement.currentIncidents === incidentCount;

    if (!hasSignal) {
      return {
        label: blocked ? 'Infra blocked' : inProgress ? 'Awaiting results' : 'No test signal',
        tone: blocked ? 'is-danger' : inProgress ? 'is-info' : 'is-warning',
        meta: (pipelineBuild ? '#' + pipelineBuild + ' - ' : '')
          + (blocked ? integer(blocked) + ' test groups never started' : inProgress ? 'Buildkite is running; no parsed test groups yet' : 'no parsed test groups')
          + (signalBuild && signalBuild !== pipelineBuild ? '; latest test signal #' + signalBuild : ''),
        hasSignal: false,
        movementLabel: 'Not classified',
        movementMeta: 'No test execution in the latest nightly',
        movementTone: 'is-warning',
        hasComparison: false,
        incidentCount: 0,
        incidentDelta: null,
      };
    }

    let movementLabel = movement.hasComparison ? 'Movement unavailable' : 'No prior comparison';
    let movementMeta = movement.hasComparison
      ? 'Transition totals do not match the latest observed signal'
      : 'No comparable preceding nightly';
    let movementTone = incidentCount ? 'is-warning' : 'is-neutral';
    if (comparisonReliable) {
      movementLabel = movement.delta > 0
        ? '+' + integer(movement.delta) + ' job variants'
        : movement.delta < 0
          ? integer(Math.abs(movement.delta)) + ' fewer job variants'
          : 'No net change';
      movementMeta = integer(movement.newCount) + ' new variants - ' + integer(movement.recurringCount) + ' recurring - ' + integer(movement.fixedCount) + ' fixed';
      movementTone = movement.delta > 0 ? 'is-danger' : movement.delta < 0 ? 'is-success' : incidentCount ? 'is-warning' : 'is-success';
    }

    let label;
    let tone;
    if (inProgress) {
      label = incidentCount ? 'Running with incidents' : 'Running clean';
      tone = hard ? 'is-danger' : incidentCount ? 'is-warning' : 'is-info';
    } else if (['canceled', 'cancelled'].includes(pipelineState)) {
      label = 'Canceled';
      tone = 'is-warning';
    } else if (hard) {
      label = 'Hard failures';
      tone = 'is-danger';
    } else if (['failed', 'failing', 'blocked'].includes(pipelineState)) {
      label = 'Pipeline failed';
      tone = 'is-danger';
    } else if (!incidentCount) {
      label = comparisonReliable && movement.fixedCount ? 'Recovered' : 'Healthy';
      tone = 'is-success';
    } else if (!comparisonReliable) {
      label = 'Incidents present';
      tone = 'is-warning';
    } else if (movement.delta > 0) {
      label = 'Regressed';
      tone = 'is-danger';
    } else if (movement.delta < 0) {
      label = 'Improved';
      tone = 'is-success';
    } else if (movement.newCount || movement.fixedCount) {
      label = 'Changed, net even';
      tone = 'is-warning';
    } else {
      label = 'Stable incidents';
      tone = 'is-warning';
    }

    return {
      label: label,
      tone: tone,
      meta: (pipelineBuild ? '#' + pipelineBuild + ' - ' : '')
        + integer(passed) + ' pass - ' + integer(soft) + ' soft - ' + integer(hard) + ' hard; '
        + movementLabel + (inProgress ? '; provisional while Buildkite is running' : '; Buildkite ' + value(record.state, 'unknown')),
      hasSignal: true,
      movementLabel: movementLabel,
      movementMeta: movementMeta,
      movementTone: movementTone,
      hasComparison: comparisonReliable,
      incidentCount: incidentCount,
      incidentDelta: comparisonReliable ? movement.delta : null,
    };
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
      nightly_hard_failures: 'Hard-failed groups in the latest AMD nightly',
      nightly_infrastructure_blocked: 'AMD nightly blocked before test execution',
      nightly_soft_failures: 'Soft-failed groups in the latest AMD nightly',
      queue_zombies: 'Queue jobs older than the analysis threshold',
      queue_waiting: 'Jobs currently waiting across tracked queues',
      gating_red_targets: 'Canonical target groups not ready',
      target_groups_with_current_incidents: 'Reviewed target groups with current AMD incidents',
      mixed_state_flaky_candidates: 'Upstream groups with mixed pass and incident history',
      omni_waiting: 'Omni jobs waiting across the fleet',
    };
    return labels[item.kind] || item.kind.replace(/_/g, ' ');
  }

  function inspectAttention(item, ops) {
    if (String(item.kind || '').startsWith('queue_')) navigateTo('ci-queue', {queueView: item.kind === 'queue_waiting' ? 'jobs' : 'current', queueScope: 'all'});
    else if (item.kind === 'gating_red_targets' || item.kind === 'target_groups_with_current_incidents') navigateTo('ci-health', {healthView: 'targets', healthResult: 'incident'});
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
    const amdHealthSummary = ((ops.amd_test_health || {}).summary) || {};
    const nightlyState = amdNightlyPresentation(build, amdHealthSummary);
    const matrix = (ops.gating || {}).matrix_summary || {};
    const uniqueHealth = matrixHealthPolicy(matrix);
    const queue = (ops.queue || {}).snapshot || {};
    const allFleetQueues = Object.entries(queue.queues || {}).filter(function (entry) { return !isRetiredQueue(entry[0]); });
    const allFleetWaiting = allFleetQueues.length ? allFleetQueues.reduce(function (sum, entry) { return sum + Number((entry[1] || {}).waiting || 0); }, 0) : Number(queue.total_waiting || 0);
    const allFleetRunning = allFleetQueues.length ? allFleetQueues.reduce(function (sum, entry) { return sum + Number((entry[1] || {}).running || 0); }, 0) : Number(queue.total_running || 0);
    add(host, pageHeader('Command Center', 'Current AMD operations with retained nightly movement and direct paths to source evidence.', ops.generated_at));
    add(host, statusStrip([
      {id: 'home-amd-nightly', label: 'LATEST AMD NIGHTLY', value: nightlyState.label, meta: nightlyState.meta, tone: nightlyState.tone, url: exactPipelineBuildUrl(build, 'amd-ci'), observed: build.created_at},
      {id: 'home-hardware-coverage', label: 'UNIQUE AMD TEST GROUPS', value: uniqueHealth.pass_percentage === null || uniqueHealth.pass_percentage === undefined ? 'No resolved signal' : Number(uniqueHealth.pass_percentage).toFixed(1) + '% resolved pass', meta: integer(uniqueHealth.passing_groups) + ' passing - ' + integer(uniqueHealth.failing_groups) + ' failing', tone: Number(uniqueHealth.failing_groups) ? 'is-warning' : 'is-success', onOpen: function () { navigateTo('ci-health', {healthView: 'overview'}); }},
      {id: 'home-failure-lifecycle', label: 'NIGHTLY VARIANT MOVEMENT', value: nightlyState.movementLabel, meta: nightlyState.movementMeta, tone: nightlyState.movementTone, onOpen: function () { openBuildDetail(build); }},
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
    grid.append(panel('AMD nightly job-variant movement', 'Latest seven completed observations', dataTable([
      {label: 'Build', render: function (r) { return externalLink('#' + r.number, exactPipelineBuildUrl(r, 'amd-ci'), 'ops-mono'); }},
      {label: 'Test signal', render: function (r) { return linkedBadge(r.has_test_results === false ? (Number(r.test_jobs_blocked || 0) ? 'Infra blocked' : 'Unavailable') : 'Observed', exactPipelineBuildUrl(r, 'amd-ci'), function () { openBuildDetail(r); }, r.has_test_results === false ? 'is-danger' : 'is-success'); }},
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

  const MATRIX_CORE_ARCHITECTURES = new Set(['mi250', 'mi300', 'mi325']);
  const MATRIX_INCIDENT_STATES = new Set(['failed', 'timed_out', 'broken', 'soft_fail', 'soft_failed']);
  const MATRIX_WAITING_STATES = new Set(['running', 'scheduled', 'assigned']);

  function matrixHealthPolicyKey() {
    const prefix = state.healthReduceDuplicates ? 'reduced' : 'definitions';
    return prefix + (state.healthIgnoreMi355Only ? '_ignore_mi355' : '_include_mi355');
  }

  function matrixHealthPolicy(matrixSummary) {
    const summary = matrixSummary || {};
    const policy = ((summary.health_policies || {})[matrixHealthPolicyKey()]);
    if (policy) return policy;

    const passing = Number(summary.passing_cells || 0);
    const failed = Number(summary.failing_cells || 0);
    const resolved = passing + failed;
    return {
      passing_groups: passing,
      failed_only_groups: failed,
      mixed_groups: 0,
      failing_groups: failed,
      waiting_groups: Number(summary.waiting_cells || 0),
      unknown_groups: Number(summary.unknown_cells || 0),
      ignored_mi355_only_groups: 0,
      inherited_mi355_groups: 0,
      resolved_groups: resolved,
      included_groups: resolved + Number(summary.waiting_cells || 0) + Number(summary.unknown_cells || 0),
      pass_percentage: resolved ? passing / resolved * 100 : null,
      reduce_duplicates: state.healthReduceDuplicates,
      ignore_mi355_only: state.healthIgnoreMi355Only,
      legacy_cell_fallback: true,
    };
  }

  function matrixCells(rows, architectureFilter) {
    const cells = [];
    (rows || []).forEach(function (row) {
      Object.entries(row.cells || {}).forEach(function (entry) {
        if (!entry[1] || !entry[1].exists) return;
        if (architectureFilter && !architectureFilter.has(entry[0])) return;
        cells.push({architecture: entry[0], row: row, cell: entry[1]});
      });
    });
    return cells;
  }

  function matrixHealthStatus(cells) {
    const states = (cells || []).map(function (entry) {
      return String(entry.cell.latest_state || '').toLowerCase();
    });
    const hasPass = states.includes('passed');
    const hasIncident = states.some(function (result) { return MATRIX_INCIDENT_STATES.has(result); });
    if (hasPass && hasIncident) return 'mixed';
    if (hasPass) return 'passing';
    if (hasIncident) return 'failed';
    if (states.some(function (result) { return MATRIX_WAITING_STATES.has(result); })) return 'waiting';
    return 'unknown';
  }

  function matrixHealthStatusLabel(status) {
    return {
      passing: 'Passing',
      failed: 'Failing',
      mixed: 'Mixed',
      waiting: 'In progress',
      unknown: 'No signal',
      ignored: 'Ignored',
    }[status] || value(status);
  }

  function matrixHealthTone(status) {
    return {
      passing: 'is-success',
      failed: 'is-danger',
      mixed: 'is-warning',
      waiting: 'is-info',
      unknown: 'is-neutral',
      ignored: 'is-neutral',
    }[status] || 'is-neutral';
  }

  function matrixHealthCollection(matrixData) {
    const rows = Array.from((matrixData || {}).rows || []);
    const components = new Map();
    rows.forEach(function (row) {
      const componentId = row.duplicate_group_id || row.id || ('row-' + row.yaml_order + '-' + row.title);
      if (!components.has(componentId)) components.set(componentId, []);
      components.get(componentId).push(row);
    });
    const duplicateMeta = new Map(((matrixData || {}).duplicate_groups || []).map(function (group) {
      return [group.id, group];
    }));
    const candidates = [];
    if (state.healthReduceDuplicates) {
      components.forEach(function (members, componentId) {
        candidates.push({id: componentId, rows: members, componentRows: members});
      });
    } else {
      rows.forEach(function (row) {
        const componentId = row.duplicate_group_id || row.id || ('row-' + row.yaml_order + '-' + row.title);
        candidates.push({
          id: row.id || componentId,
          rows: [row],
          componentRows: components.get(componentId) || [row],
          componentId: componentId,
        });
      });
    }

    const groups = candidates.map(function (candidate) {
      const candidateCore = matrixCells(candidate.rows, MATRIX_CORE_ARCHITECTURES);
      const candidateMi355 = matrixCells(candidate.rows, new Set(['mi355']));
      const componentCore = matrixCells(candidate.componentRows, MATRIX_CORE_ARCHITECTURES);
      let signalCells = [];
      let inherited = false;
      let status;
      if (candidateCore.length) {
        signalCells = candidateCore;
        inherited = candidateMi355.length > 0;
      } else if (componentCore.length) {
        signalCells = componentCore;
        inherited = true;
      } else if (state.healthIgnoreMi355Only) {
        signalCells = candidateMi355;
        status = 'ignored';
      } else {
        signalCells = candidateMi355;
      }
      if (!status) status = matrixHealthStatus(signalCells);

      const componentId = candidate.componentId
        || ((candidate.rows[0] || {}).duplicate_group_id)
        || candidate.id;
      const meta = duplicateMeta.get(componentId) || duplicateMeta.get(candidate.id) || {};
      const allCells = matrixCells(candidate.rows);
      const states = signalCells.map(function (entry) {
        return String(entry.cell.latest_state || '').toLowerCase();
      });
      const architectures = Array.from(new Set(allCells.map(function (entry) {
        return entry.architecture;
      }))).sort();
      return {
        id: candidate.id,
        title: state.healthReduceDuplicates ? (meta.title || (candidate.rows[0] || {}).title) : (candidate.rows[0] || {}).title,
        status: status,
        inherited: inherited,
        rows: candidate.rows,
        componentRows: candidate.componentRows,
        duplicateSize: candidate.componentRows.length,
        definitionCount: allCells.reduce(function (total, entry) {
          return total + Number(entry.cell.raw_variant_count || entry.cell.variant_count || 1);
        }, 0),
        architectures: architectures,
        passedCells: states.filter(function (result) { return result === 'passed'; }).length,
        incidentCells: states.filter(function (result) { return MATRIX_INCIDENT_STATES.has(result); }).length,
        waitingCells: states.filter(function (result) { return MATRIX_WAITING_STATES.has(result); }).length,
        unknownCells: states.filter(function (result) {
          return result !== 'passed' && !MATRIX_INCIDENT_STATES.has(result) && !MATRIX_WAITING_STATES.has(result);
        }).length,
        pairMatches: meta.pair_matches || [],
      };
    });
    return groups.sort(function (left, right) {
      const priority = {mixed: 0, failed: 1, unknown: 2, waiting: 3, passing: 4, ignored: 5};
      return priority[left.status] - priority[right.status] || String(left.title).localeCompare(String(right.title));
    });
  }

  function matrixGroupEvidence(group) {
    const rows = [];
    const seen = new Set();
    const evidenceRows = group.inherited ? group.componentRows : group.rows;
    matrixCells(evidenceRows).forEach(function (entry) {
      const variants = entry.cell.variants && entry.cell.variants.length
        ? entry.cell.variants : [{
          label: entry.cell.primary_label || entry.row.title,
          agent_pool: '',
          latest_state: entry.cell.latest_state,
          latest_url: entry.cell.latest_url,
          entries: [],
        }];
      variants.forEach(function (variant) {
        const definitions = variant.entries && variant.entries.length ? variant.entries : [variant];
        definitions.forEach(function (definition) {
          const result = definition.latest_state || variant.latest_state || entry.cell.latest_state;
          const url = exactPipelineEvidenceUrl({
            latest_url: definition.latest_url || variant.latest_url || entry.cell.latest_url,
            build_number: entry.cell.latest_build_number,
          }, 'amd-ci');
          const key = [entry.row.id, entry.architecture, definition.label, definition.agent_pool, url].join('|');
          if (seen.has(key)) return;
          seen.add(key);
          rows.push({
            definition: definition.label || variant.label || entry.row.title,
            architecture: entry.architecture,
            queue: definition.agent_pool || variant.agent_pool || '',
            result: result || 'unknown',
            url: url,
            buildNumber: entry.cell.latest_build_number,
          });
        });
      });
    });
    return rows;
  }

  function openMatrixGroupEvidence(group, matrixData) {
    const evidenceRows = matrixGroupEvidence(group);
    const content = n('div', 'ops-evidence');
    const note = n('div', 'ops-evidence-note ' + matrixHealthTone(group.status));
    add(note, [
      n('strong', '', matrixHealthStatusLabel(group.status) + '. '),
      n('span', '', group.inherited
        ? 'MI355 evidence is shown, while the status comes from MI250, MI300, or MI325.'
        : 'The status uses the latest exact AMD job evidence shown below.'),
    ]);
    content.append(note);
    content.append(dataTable([
      {label: 'Definition', sticky: true, width: '390px', render: function (row) { return row.url ? externalLink(row.definition, row.url, 'ops-cell-primary') : row.definition; }},
      {label: 'Hardware', width: '110px', render: function (row) { return badge(row.architecture.toUpperCase(), 'is-neutral'); }},
      {label: 'Agent pool', width: '150px', render: function (row) { return value(row.queue); }},
      {label: 'Latest result', width: '130px', render: function (row) { return linkedBadge(row.result, row.url, null, toneForState(row.result)); }},
      {label: 'Evidence', width: '130px', render: function (row) { return row.url ? externalLink('Open job', row.url) : n('span', 'ops-cell-muted', '-'); }},
    ], evidenceRows, integer(evidenceRows.length) + ' exact AMD definitions', {name: 'matrix-unique-evidence', minWidth: '980px'}));
    openDetailDrawer({
      id: 'matrix-health-' + group.id,
      title: group.title,
      subtitle: 'Unique AMD test-group signal and exact Buildkite evidence',
      fields: [
        {label: 'Status', value: matrixHealthStatusLabel(group.status)},
        {label: 'Source definitions', value: integer(group.definitionCount)},
        {label: 'Hardware', value: group.architectures.map(function (arch) { return arch.toUpperCase(); }).join(', ')},
        {label: 'Signal', value: integer(group.passedCells) + ' passing - ' + integer(group.incidentCells) + ' incident'},
        {label: 'Duplicate rows', value: group.duplicateSize > 1 ? integer(group.duplicateSize) : null},
        {label: 'Shared title substring', value: Array.from(new Set(group.pairMatches.map(function (match) { return match.shared_substring; }))).join(', ') || null},
      ],
      sources: [
        {label: 'Open AMD test definitions', url: ((matrixData || {}).source || {}).yaml_url},
        {label: 'Open latest AMD build', url: ((matrixData || {}).source || {}).latest_build_url},
      ],
      content: content,
    });
  }

  function matrixHealthFilter(groups, mode) {
    if (mode === 'failing') return groups.filter(function (group) { return ['failed', 'mixed'].includes(group.status); });
    if (mode === 'no-signal') return groups.filter(function (group) { return ['waiting', 'unknown'].includes(group.status); });
    if (mode === 'all') return groups.filter(function (group) { return group.status !== 'ignored'; });
    return groups.filter(function (group) { return group.status === mode; });
  }

  async function openMatrixHealthBrowser(mode) {
    let matrixData;
    try {
      matrixData = await fetchJSON('data/vllm/ci/amd_test_matrix.json');
    } catch (error) {
      openMetricDetail({
        label: 'Unique AMD test groups',
        value: 'Unavailable',
        description: 'The AMD matrix payload could not be loaded.',
      });
      return;
    }
    const groups = matrixHealthFilter(matrixHealthCollection(matrixData), mode || 'all');
    const titles = {
      all: 'Unique AMD test-group health',
      passing: 'Passing AMD test groups',
      failing: 'Failing AMD test groups',
      failed: 'Fully failing AMD test groups',
      mixed: 'Mixed AMD test groups',
      'no-signal': 'AMD groups without a terminal signal',
      ignored: 'Ignored MI355-only test groups',
    };
    openTableBrowser({
      id: 'unique-amd-health-' + (mode || 'all'),
      title: titles[mode || 'all'],
      subtitle: integer(groups.length) + ' groups under the active duplicate and MI355 policy',
      rows: groups,
      columns: [
        {label: 'Unique test group', sticky: true, width: '390px', render: function (group) { return linkButton(group.title, function () { openMatrixGroupEvidence(group, matrixData); }); }},
        {label: 'Status', width: '130px', render: function (group) { return linkedBadge(matrixHealthStatusLabel(group.status), null, function () { openMatrixGroupEvidence(group, matrixData); }, matrixHealthTone(group.status)); }},
        {label: 'Definitions', numeric: true, width: '110px', render: function (group) { return linkButton(integer(group.definitionCount), function () { openMatrixGroupEvidence(group, matrixData); }); }},
        {label: 'Hardware', width: '180px', render: function (group) { return group.architectures.map(function (arch) { return arch.toUpperCase(); }).join(', ') || '-'; }},
        {label: 'Core signal', width: '190px', render: function (group) { return integer(group.passedCells) + ' pass - ' + integer(group.incidentCells) + ' incident'; }},
        {label: 'Evidence', width: '140px', render: function (group) { return linkButton(integer(matrixGroupEvidence(group).filter(function (row) { return row.url; }).length) + ' jobs', function () { openMatrixGroupEvidence(group, matrixData); }); }},
      ],
      searchText: function (group) { return [group.title, group.status, group.architectures.join(' ')].join(' '); },
      searchPlaceholder: 'Filter test group, status, or hardware',
      geometry: {name: 'unique-amd-health', minWidth: '1160px'},
    });
  }

  function matrixHealthToggle(label, checked, title, onChange) {
    const control = n('label', 'ops-health-policy-toggle');
    control.title = title;
    const input = n('input');
    input.type = 'checkbox';
    input.checked = checked;
    input.addEventListener('change', function () { onChange(input.checked); });
    add(control, [input, n('span', '', label)]);
    return control;
  }

  function matrixHealthOverview(matrixSummary, policy, latestBuildNumber) {
    const root = n('section', 'ops-unique-health');
    const head = n('header', 'ops-panel-header ops-unique-health-header');
    const heading = n('div', 'ops-unique-health-heading');
    add(heading, [
      n('h2', 'ops-panel-title', 'Unique AMD test-group health'),
      n('div', 'ops-panel-meta', latestBuildNumber ? 'Latest observed matrix build #' + latestBuildNumber : 'Latest observed AMD matrix signal'),
    ]);
    const controls = n('div', 'ops-unique-health-controls');
    add(controls, [
      matrixHealthToggle(
        'Reduce duplicates',
        state.healthReduceDuplicates,
        'Equal command lists plus a shared title substring of at least two characters',
        function (checked) { state.healthReduceDuplicates = checked; render('ci-health', true); }
      ),
      matrixHealthToggle(
        'Ignore MI355-only',
        state.healthIgnoreMi355Only,
        'Exclude groups that have no MI250, MI300, or MI325 definition',
        function (checked) { state.healthIgnoreMi355Only = checked; render('ci-health', true); }
      ),
    ]);
    add(head, [heading, controls]);
    root.append(head);

    const body = n('div', 'ops-unique-health-body');
    const rate = n('button', 'ops-unique-health-rate');
    rate.type = 'button';
    rate.addEventListener('click', function () { openMatrixHealthBrowser('all'); });
    add(rate, [
      n('strong', '', policy.pass_percentage === null || policy.pass_percentage === undefined ? '-' : Number(policy.pass_percentage).toFixed(1) + '%'),
      n('span', '', 'of resolved groups passing'),
    ]);
    const stats = n('div', 'ops-unique-health-stats');
    [
      {mode: 'passing', label: 'Passing', count: policy.passing_groups, tone: 'is-passing'},
      {mode: 'failing', label: 'Failing', count: policy.failing_groups, tone: 'is-failing'},
      {mode: 'mixed', label: 'Mixed subset', count: policy.mixed_groups, tone: 'is-mixed'},
      {mode: 'no-signal', label: 'No signal', count: Number(policy.waiting_groups || 0) + Number(policy.unknown_groups || 0), tone: 'is-unknown'},
    ].forEach(function (item) {
      const stat = n('button', 'ops-unique-health-stat ' + item.tone);
      stat.type = 'button';
      stat.addEventListener('click', function () { openMatrixHealthBrowser(item.mode); });
      add(stat, [n('span', '', item.label), n('strong', '', integer(item.count))]);
      stats.append(stat);
    });
    add(body, [rate, stats]);

    const total = Math.max(1, Number(policy.included_groups || 0));
    const bar = n('div', 'ops-unique-health-bar');
    [
      {mode: 'passing', label: 'Passing', count: Number(policy.passing_groups || 0), tone: 'is-passing'},
      {mode: 'failed', label: 'Failing', count: Number(policy.failed_only_groups || 0), tone: 'is-failing'},
      {mode: 'mixed', label: 'Mixed', count: Number(policy.mixed_groups || 0), tone: 'is-mixed'},
      {mode: 'no-signal', label: 'No signal', count: Number(policy.waiting_groups || 0) + Number(policy.unknown_groups || 0), tone: 'is-unknown'},
    ].forEach(function (item) {
      if (!item.count) return;
      const segment = n('button', 'ops-unique-health-segment ' + item.tone);
      segment.type = 'button';
      segment.style.width = item.count / total * 100 + '%';
      segment.title = item.label + ': ' + integer(item.count);
      segment.setAttribute('aria-label', 'Inspect ' + item.label.toLowerCase() + ' groups: ' + integer(item.count));
      segment.addEventListener('click', function () { openMatrixHealthBrowser(item.mode); });
      bar.append(segment);
    });
    body.append(bar);

    const footer = n('footer', 'ops-unique-health-footer');
    const definitionRows = Number(matrixSummary.definition_rows || matrixSummary.unique_groups || 0);
    const reducedRows = Math.max(0, definitionRows - Number(matrixSummary.reduced_unique_groups || definitionRows));
    const facts = [
      integer(policy.resolved_groups) + ' resolved of ' + integer(policy.included_groups) + ' included',
      state.healthReduceDuplicates ? integer(reducedRows) + ' duplicate definitions reduced' : 'Definition rows shown separately',
      integer(policy.inherited_mi355_groups || 0) + ' MI355 signals inherited',
    ];
    add(footer, [
      n('span', '', facts.join(' - ')),
      Number(policy.ignored_mi355_only_groups || 0)
        ? linkButton(integer(policy.ignored_mi355_only_groups) + ' MI355-only ignored', function () { openMatrixHealthBrowser('ignored'); })
        : null,
      button('Browse groups', function () { openMatrixHealthBrowser('all'); }),
    ]);
    body.append(footer);
    root.append(body);
    return root;
  }

  const AMD_MATRIX_PLATFORM_ORDER = ['mi250', 'mi300', 'mi325', 'mi355'];
  const AMD_ARCHITECTURE_HARD_SIGNAL_STATES = new Set([
    'hard', 'failed', 'failing', 'incident', 'error', 'timed_out', 'broken',
    'canceled', 'cancelled', 'expired',
  ]);
  const AMD_ARCHITECTURE_SOFT_SIGNAL_STATES = new Set([
    'soft', 'soft_fail', 'soft_failed',
  ]);

  function amdMatrixPlatformRank(row) {
    const cells = (row || {}).cells || {};
    const rank = AMD_MATRIX_PLATFORM_ORDER.findIndex(function (platform) {
      return Boolean((cells[platform] || {}).exists);
    });
    return rank < 0 ? AMD_MATRIX_PLATFORM_ORDER.length : rank;
  }

  function sortAmdMatrixRows(rows, mode) {
    return Array.from(rows || []).sort(function (left, right) {
      if (mode === 'name') {
        return compareText(left.title, right.title) || compareText(left.area, right.area);
      }
      if (mode === 'area') {
        return compareText(left.area, right.area) || compareText(left.title, right.title);
      }
      return amdMatrixPlatformRank(left) - amdMatrixPlatformRank(right)
        || compareText(left.title, right.title)
        || compareText(left.area, right.area);
    });
  }

  function architectureSignalStateRank(stateName) {
    const normalized = String(stateName || 'unobserved').trim().toLowerCase();
    if (AMD_ARCHITECTURE_HARD_SIGNAL_STATES.has(normalized)) return 0;
    if (AMD_ARCHITECTURE_SOFT_SIGNAL_STATES.has(normalized)) return 1;
    if (normalized === 'passed') return 3;
    return 2;
  }

  function sortArchitectureSignalRows(rows, architectureId) {
    function latestState(row) {
      return (((row || {}).cells || {})[architectureId] || {}).latest_state || 'unobserved';
    }
    return Array.from(rows || []).sort(function (left, right) {
      return architectureSignalStateRank(latestState(left)) - architectureSignalStateRank(latestState(right))
        || compareText(left.title, right.title)
        || compareText(left.area, right.area)
        || compareText(left.id, right.id);
    });
  }

  function runtimeTargetState(row) {
    return observationState((row || {}).latest_amd_result || {});
  }

  function sortRuntimeTargetRows(rows) {
    return Array.from(rows || []).sort(function (left, right) {
      return architectureSignalStateRank(runtimeTargetState(left)) - architectureSignalStateRank(runtimeTargetState(right))
        || compareText(left.label, right.label)
        || compareText(left.area, right.area)
        || compareText(left.id, right.id);
    });
  }

  function amdMatrixSortDescription(mode) {
    if (mode === 'name') return 'Test groups sorted alphabetically by name';
    if (mode === 'area') return 'Test areas sorted alphabetically, then by test-group name';
    return 'Grouped by first configured platform: MI250, MI300, MI325, then MI355';
  }

  function healthTabs(host) {
    host.append(segmented([
      {id: 'overview', label: 'Overview'}, {id: 'targets', label: 'Runtime targets'},
      {id: 'gating', label: 'Definition parity'},
      {id: 'coverage', label: 'Coverage'}, {id: 'diagnostics', label: 'Diagnostics'},
    ], state.healthView, function (id) { setRouteState('ci-health', 'healthView', id, 'health_view'); }, 'CI Health view'));
  }

  function ownershipAreaState(row) {
    const counts = (row || {}).counts || {};
    if (Number(counts.hard || 0)) return 'hard';
    if (Number(counts.soft || 0)) return 'soft';
    if (Number(counts.unobserved || 0)) return 'unknown';
    return 'passed';
  }

  function ownershipSelectedName(row) {
    const selected = (row || {}).selected_owner || {};
    const actual = (row || {}).actual_assignee || {};
    if (actual.display_name && actual.github_login !== selected.github_login) {
      return actual.display_name + ' (CI fallback)';
    }
    return selected.display_name || 'Unassigned';
  }

  function ownershipChainText(row) {
    return ((row || {}).owners || []).map(function (owner) {
      return integer(owner.rank) + ' ' + value(owner.display_name);
    }).join(' · ');
  }

  function openOwnershipAreaDetail(row) {
    const counts = row.counts || {};
    const content = n('div', 'ops-detail-stack');
    content.append(statusStrip([
      {id: 'owner-area-incidents', label: 'CURRENT REGRESSIONS', value: integer(counts.incidents), meta: integer(counts.hard) + ' hard - ' + integer(counts.soft) + ' soft', tone: Number(counts.incidents) ? 'is-warning' : 'is-success'},
      {id: 'owner-area-targets', label: 'RUNTIME TARGETS', value: integer(counts.targets), meta: integer(counts.passed) + ' passed - ' + integer(counts.unobserved) + ' unresolved'},
      {id: 'owner-area-parity', label: 'UPSTREAM PARITY GAPS', value: integer(counts.upstream_parity_gaps), meta: row.source_file || row.area, tone: Number(counts.upstream_parity_gaps) ? 'is-warning' : 'is-success'},
      {id: 'owner-area-assignee', label: 'GITHUB ASSIGNEE', value: ownershipSelectedName(row), meta: row.assignment_reason || row.selection_reason || 'not reconciled'},
    ]));
    const chainColumns = [
      {label: 'Rank', width: '80px', render: function (owner) { return n('span', 'ops-mono', integer(owner.rank)); }},
      {label: 'Engineer', width: '260px', render: function (owner) { return value(owner.display_name); }},
    ];
    content.append(panel(
      'Escalation chain',
      'Assignment evaluates Serbia and Chicago working hours in rank order; per-owner routing state is not published',
      dataTable(chainColumns, row.owners || [], integer((row.owners || []).length) + ' ranked owners', {name: 'ownership-chain', minWidth: '540px'})
    ));
    const regressions = row.regressions || [];
    const regressionColumns = [
      {label: 'Target group', sticky: true, width: '400px', render: function (item) { return item.url ? externalLink(item.label, item.url) : value(item.label); }},
      {label: 'Latest result', width: '130px', render: function (item) { return badge(value(item.result), toneForState(item.result)); }},
      {label: 'Build', width: '100px', render: function (item) { return item.build_number ? n('span', 'ops-mono', '#' + integer(item.build_number)) : n('span', 'ops-cell-muted', '-'); }},
      {label: 'Observed', width: '180px', render: function (item) { return shortDate(item.observed_at); }},
    ];
    content.append(panel(
      'Current AMD regressions',
      regressions.length ? 'Exact latest AMD job evidence' : 'No current regression',
      dataTable(regressionColumns, regressions, integer(regressions.length) + ' current regressions', {name: 'ownership-regressions', minWidth: '820px'})
    ));
    const gaps = row.upstream_parity_gaps || [];
    const gapColumns = [
      {label: 'Upstream-only definition', sticky: true, width: '500px', render: function (item) { return item.url ? externalLink(item.label, item.url) : value(item.label); }},
    ];
    content.append(panel(
      'Upstream parity work',
      'Definitions present upstream without a one-to-one AMD definition',
      dataTable(gapColumns, gaps, integer(gaps.length) + ' parity gaps', {name: 'ownership-parity', minWidth: '520px'})
    ));
    openOverlay(
      row.source_file || row.area || 'CI ownership',
      'Regression response, escalation, runtime signal, and parity obligations',
      content,
      true,
      'ci-ownership-area'
    );
  }

  function renderOwnership(host, ops) {
    const ownership = (ops || {}).ownership || {};
    const summary = ownership.summary || {};
    const availability = ownership.availability || {};
    const project = ownership.project || {};
    if (ownership.available !== true) {
      const unavailable = n('div', 'ops-evidence-note is-warning');
      add(unavailable, [
        n('strong', '', 'CI ownership status is unavailable. '),
        n('span', '', value(ownership.unavailable_reason || 'The managed ownership snapshot has not been generated yet.')),
      ]);
      host.append(unavailable);
      return;
    }
    host.append(statusStrip([
      {id: 'ownership-areas', label: 'OWNED TEST AREAS', value: integer(summary.areas), meta: 'complete primary / secondary / tertiary chains'},
      {id: 'ownership-regressing', label: 'AREAS REGRESSING', value: integer(summary.areas_with_incidents), meta: integer(summary.incidents) + ' target regressions', tone: Number(summary.areas_with_incidents) ? 'is-warning' : 'is-success'},
      {id: 'ownership-parity', label: 'UPSTREAM PARITY GAPS', value: integer(summary.upstream_parity_gaps), meta: 'commit-pinned upstream-only definitions', tone: Number(summary.upstream_parity_gaps) ? 'is-warning' : 'is-success'},
      {id: 'ownership-unmapped', label: 'UNMAPPED TARGETS', value: integer(summary.unmapped_targets), meta: 'never assigned by a lossy fallback', tone: Number(summary.unmapped_targets) ? 'is-warning' : 'is-success'},
    ]));
    const workingHoursConfigured = availability.configured === true
      && availability.fresh === true
      && availability.reason === 'working_hours_profiles';
    const policyNote = n('div', 'ops-evidence-note ' + (workingHoursConfigured ? 'is-info' : 'is-warning'));
    add(policyNote, [
      n('strong', '', 'Regional working-hours routing. '),
      n('span', '', workingHoursConfigured
        ? 'EU follows 09:00–17:00 Serbia time (Europe/Belgrade) and NA follows 09:00–17:00 Chicago time (America/Chicago), Monday through Friday. The first in-hours owner is selected in rank order. Missing or invalid working-hour schedules fail closed to the CI lead, even when the regional profile source is healthy.'
        : 'Regional working-hour profiles are unavailable or invalid. Missing or invalid working-hour schedules fail closed to the CI lead.'),
      n('span', '', ' GitHub assignability is checked before mutation, and automation issue text contains no user mentions.'),
      project.url ? n('span', '', ' ') : null,
      project.url ? externalLink('Open AMD CI Operations project', project.url) : null,
      project.url ? n('span', '', '.') : null,
    ]);
    host.append(policyNote);
    const areas = Array.from(ownership.areas || []).sort(function (left, right) {
      return architectureSignalStateRank(ownershipAreaState(left)) - architectureSignalStateRank(ownershipAreaState(right))
        || compareText(left.source_file, right.source_file);
    });
    const areaColumns = [
      {label: 'Test area', sticky: true, width: '230px', render: function (row) { return linkButton(row.source_file || row.area, function () { openOwnershipAreaDetail(row); }); }},
      {label: 'Latest status', width: '150px', render: function (row) { const result = ownershipAreaState(row); return linkedBadge(result, (row.issue || {}).url, function () { openOwnershipAreaDetail(row); }, toneForState(result)); }},
      {label: 'Selected engineer', width: '240px', render: function (row) { return linkButton(ownershipSelectedName(row), function () { openOwnershipAreaDetail(row); }); }},
      {label: 'Runtime targets', width: '190px', render: function (row) { const counts = row.counts || {}; return integer(counts.incidents) + ' incident - ' + integer(counts.passed) + ' pass - ' + integer(counts.unobserved) + ' unresolved'; }},
      {label: 'Parity gaps', width: '120px', render: function (row) { return integer((row.counts || {}).upstream_parity_gaps); }},
      {label: 'Managed issue', width: '130px', render: function (row) { const issue = row.issue || {}; if (issue.url) return externalLink('#' + integer(issue.number), issue.url, 'ops-mono'); if (issue.suppressed) return badge('suppressed', 'is-neutral'); return n('span', 'ops-cell-muted', '-'); }},
    ];
    host.append(compactTablePanel(
      'CI test-area ownership',
      'Incident areas first, then source filename; owner chains are rank ordered',
      areaColumns,
      areas,
      {
        id: 'ci-ownership-browser',
        limit: 18,
        alwaysBrowse: areas.length > 0,
        browserTitle: 'CI test-area ownership and escalation',
        browserSubtitle: 'Runtime regression, parity, working-hours routing, and managed issue status',
        searchPlaceholder: 'Filter area, engineer, status, or assignment reason',
        searchText: function (row) { return [row.source_file, ownershipAreaState(row), ownershipSelectedName(row), ownershipChainText(row), row.assignment_reason, row.selection_reason, (row.regressions || []).map(function (item) { return item.label; }).join(' ')].join(' '); },
        geometry: {name: 'ci-ownership', minWidth: '1090px'},
      }
    ));
  }

  async function renderHealth(host, ops) {
    const amd = latestAmd(ops);
    const build = (amd.builds || [])[0] || {};
    const gating = ops.gating || {};
    const definitionSummary = ((ops.definition_parity || {}).summary) || {};
    const matrix = gating.matrix_summary || {};
    const uniqueHealth = matrixHealthPolicy(matrix);
    const amdHealthSummary = ((ops.amd_test_health || {}).summary) || {};
    const amdLatestStates = amdHealthSummary.latest_state_counts || {};
    const amdSoft = Number(amdLatestStates.soft || 0);
    const amdHard = Number(amdLatestStates.hard || 0);
    const nightlyState = amdNightlyPresentation(build, amdHealthSummary);
    add(host, pageHeader('CI Health', 'AMD nightly outcomes, hardware coverage, and source-definition parity with each evidence type kept distinct.', ops.generated_at));
    healthTabs(host);
    host.append(statusStrip([
      {id: 'health-build', label: 'LATEST AMD NIGHTLY', value: nightlyState.label, meta: build.number ? nightlyState.meta : 'No completed build', tone: nightlyState.tone, url: exactPipelineBuildUrl(build, 'amd-ci')},
      {id: 'health-hardware', label: 'UNIQUE AMD TEST GROUPS', value: uniqueHealth.pass_percentage === null || uniqueHealth.pass_percentage === undefined ? 'No resolved signal' : Number(uniqueHealth.pass_percentage).toFixed(1) + '% resolved pass', meta: integer(uniqueHealth.passing_groups) + ' passing - ' + integer(uniqueHealth.failing_groups) + ' failing - ' + integer(Number(uniqueHealth.waiting_groups || 0) + Number(uniqueHealth.unknown_groups || 0)) + ' no signal', tone: Number(uniqueHealth.failing_groups) ? 'is-warning' : Number(uniqueHealth.unknown_groups || 0) ? 'is-warning' : 'is-success', onOpen: function () { openMatrixHealthBrowser('all'); }},
      {id: 'health-definitions', label: 'AMD DEFINITIONS', value: integer(definitionSummary.total_amd_steps), meta: integer(definitionSummary.matched) + ' matched to current upstream definitions', onOpen: function () { setRouteState('ci-health', 'healthView', 'gating', 'health_view'); }},
      {id: 'health-nightly-transition', label: 'NIGHTLY VARIANT MOVEMENT', value: nightlyState.movementLabel, meta: nightlyState.movementMeta, tone: nightlyState.movementTone, onOpen: function () { openBuildDetail(build); }},
    ]));

    if (state.healthView === 'overview') {
      const movementBuilds = (amd.builds || []).filter(function (row) { return row.has_test_results !== false; }).slice(0, 14).reverse();
      if (!nightlyState.hasSignal) {
        const signalNote = n('div', 'ops-evidence-note is-warning');
        add(signalNote, [
          n('strong', '', 'Latest nightly has no test signal. '),
          n('span', '', 'The incident list, matrix, and movement chart below use the latest observed AMD test build'),
          amdHealthSummary.latest_build_url ? externalLink(' #' + amdHealthSummary.latest_build_number, amdHealthSummary.latest_build_url) : n('span', '', ' #' + value(amdHealthSummary.latest_build_number)),
          n('span', '', '.'),
        ]);
        host.append(signalNote);
      }
      host.append(matrixHealthOverview(
        matrix,
        uniqueHealth,
        matrix.latest_build_number || amdHealthSummary.latest_build_number
      ));
      const grid = n('div', 'ops-grid ops-grid-main-aside ops-health-grid');
      const trend = chartPanel('Observed test-result movement', 'Nightlies with test execution only; latest signal #' + value(amdHealthSummary.latest_build_number), 'health-nightly');
      trend.root.classList.add('ops-health-primary');
      grid.append(trend.root);
      const amdHealth = ops.amd_test_health || {};
      const latestAmdBuild = ((amdHealth.summary || {}).latest_build_number);
      const failures = amdHealthGroups(amdHealth).filter(function (row) { return ['soft', 'hard'].includes(amdLatestState(row, latestAmdBuild)); }).sort(function (a, b) {
        return (amdLatestState(a, latestAmdBuild) === 'hard' ? 0 : 1) - (amdLatestState(b, latestAmdBuild) === 'hard' ? 0 : 1) || Number(amdGroupPassRate(a) || 0) - Number(amdGroupPassRate(b) || 0);
      });
      const overviewColumns = [
        {label: 'AMD test group', sticky: true, width: '330px', render: function (row) { return amdGroupIdentity(row, function () { openAmdGroupDetail(row, amdHealth); }); }},
        {label: 'Latest', width: '120px', render: function (row) { const result = amdLatestState(row, latestAmdBuild); return linkedBadge(amdStateLabel(result), row.latest_url, function () { openAmdGroupDetail(row, amdHealth); }, toneForState(result)); }},
      ];
      const failurePanel = compactTablePanel('Latest available AMD incidents', '#' + value(latestAmdBuild) + ' - ' + integer(amdHard) + ' hard - ' + integer(amdSoft) + ' soft', overviewColumns, failures, {
        id: 'health-current-incidents',
        limit: 10,
        browserSubtitle: 'Every row opens its exact AMD nightly job and retained history',
        searchPlaceholder: 'Filter AMD group, hardware, or queue',
        searchText: function (row) { return [row.display_name, row.name, row.hardware_variant, row.queue].join(' '); },
        geometry: {name: 'health-incidents', minWidth: '520px'},
        className: 'ops-health-aside',
      });
      grid.append(failurePanel);
      host.append(grid);
      drawChart('health-nightly', trend.canvas, {
        type: 'bar',
        data: {
          labels: movementBuilds.map(function (b) { return '#' + b.number; }),
          datasets: [
            {label: 'New', data: movementBuilds.map(function (b) { return (b.transitions.new || []).length; }), backgroundColor: '#e06464'},
            {label: 'Recurring', data: movementBuilds.map(function (b) { return (b.transitions.recurring || []).length; }), backgroundColor: '#e3a63a'},
            {label: 'Fixed', data: movementBuilds.map(function (b) { return (b.transitions.fixed || []).length; }), backgroundColor: '#35bb78'},
          ],
        },
        options: {scales: {x: {stacked: true}, y: {stacked: true, beginAtZero: true}}},
        evidenceTitle: 'AMD nightly non-passing group movement',
        evidence: movementBuilds.map(function (nightly) {
          return {label: '#' + nightly.number, timestamp: nightly.created_at, url: exactPipelineBuildUrl(nightly, 'amd-ci'), valueSummary: integer((nightly.transitions.new || []).length) + ' new', details: {state: nightly.state, new: (nightly.transitions.new || []).length, recurring: (nightly.transitions.recurring || []).length, fixed: (nightly.transitions.fixed || []).length}};
        }),
      });
      return;
    }

    if (state.healthView === 'targets') {
      const targetSummary = gating.active_target_summary || {};
      const allTargets = Array.isArray(gating.active_target_groups) ? gating.active_target_groups : [];
      function targetState(row) {
        return runtimeTargetState(row);
      }
      function isTargetIncident(row) {
        return isIncidentObservation({state: targetState(row)});
      }
      const incidentTargets = allTargets.filter(isTargetIncident);
      const passedTargets = allTargets.filter(function (row) { return targetState(row) === 'passed'; });
      const unobservedTargets = allTargets.filter(function (row) {
        return targetState(row) !== 'passed' && !isTargetIncident(row);
      });
      const noSignalBreakdown = targetNoSignalBreakdown(unobservedTargets);
      const filters = {
        all: allTargets,
        incident: incidentTargets,
        unobserved: unobservedTargets,
        passed: passedTargets,
      };
      const targetRows = sortRuntimeTargetRows(filters[state.healthResult] || allTargets);
      host.append(statusStrip([
        {id: 'runtime-target-all', label: 'ACTIVE TARGET GROUPS', value: integer(targetSummary.target_group_count), meta: integer(targetSummary.canonical_group_count) + ' reviewed - ' + integer(targetSummary.active_outside_canonical_count) + ' active additions', onOpen: function () { setRouteState('ci-health', 'healthResult', 'all', 'health_result'); }},
        {id: 'runtime-target-incidents', label: 'INCIDENTS NOW', value: integer(incidentTargets.length), meta: 'latest exact AMD matrix result', tone: incidentTargets.length ? 'is-warning' : 'is-success', onOpen: function () { setRouteState('ci-health', 'healthResult', 'incident', 'health_result'); }},
        {id: 'runtime-target-unobserved', label: 'UNRESOLVED TARGETS', value: integer(unobservedTargets.length), meta: integer(noSignalBreakdown.noDefinition) + ' no one-to-one definition - ' + integer(noSignalBreakdown.needsReview) + ' mapping review - ' + integer(noSignalBreakdown.notObserved) + ' not observed', tone: unobservedTargets.length ? 'is-warning' : 'is-success', onOpen: function () { setRouteState('ci-health', 'healthResult', 'unobserved', 'health_result'); }},
        {id: 'runtime-target-passed', label: 'PASSING NOW', value: integer(passedTargets.length), meta: percent(passedTargets.length, allTargets.length) + ' of active target groups', tone: passedTargets.length ? 'is-success' : 'is-neutral', onOpen: function () { setRouteState('ci-health', 'healthResult', 'passed', 'health_result'); }},
      ]));
      const runtimeNote = n('div', 'ops-evidence-note is-info');
      add(runtimeNote, [
        n('strong', '', 'Runtime AMD target signal, not definition parity. '),
        n('span', '', 'Latest soft and hard outcomes are shown first, followed by unresolved signal and passes; names are alphabetical within each result tier.'),
      ]);
      host.append(runtimeNote);
      const targetToolbar = n('div', 'ops-toolbar');
      targetToolbar.append(segmented([
        {id: 'all', label: 'All targets'},
        {id: 'incident', label: 'Incidents'},
        {id: 'unobserved', label: 'No signal'},
        {id: 'passed', label: 'Passed'},
      ], state.healthResult, function (result) {
        setRouteState('ci-health', 'healthResult', result, 'health_result');
      }, 'Filter runtime target groups by latest AMD result'));
      const targetColumns = [
        {label: 'Target group', sticky: true, width: '400px', render: function (row) { return linkButton(row.label, function () { openGatingDetailWithEvidence(row, ops); }); }},
        {label: 'Area', width: '170px', render: function (row) { return value(row.area); }},
        {label: 'Latest AMD result', width: '160px', render: function (row) { const latest = row.latest_amd_result || {}; return linkedBadge(targetState(row), gatingEvidenceUrl(row), function () { openGatingDetailWithEvidence(row, ops); }, toneForState(targetState(row))); }},
        {label: 'Assessment', width: '330px', render: function (row) { return linkButton(targetAssessmentText(row), function () { openGatingDetailWithEvidence(row, ops); }); }},
        {label: 'Build', width: '110px', render: function (row) { const latest = row.latest_amd_result || {}; const url = gatingEvidenceUrl(row); return url ? externalLink('#' + value(latest.build_number), url, 'ops-mono') : n('span', 'ops-cell-muted', '-'); }},
      ];
      host.append(compactTablePanel(
        'Runtime target-group signal',
        integer(targetRows.length) + ' groups match the ' + state.healthResult + ' filter',
        targetColumns,
        targetRows,
        {
          id: 'runtime-target-browser',
          limit: 18,
          headerActions: targetToolbar,
          browserTitle: 'Runtime AMD target-group signal',
          browserSubtitle: 'Exact AMD outcomes first by result severity, then alphabetically',
          searchPlaceholder: 'Filter target group, area, result, or assessment',
          searchText: function (row) { const resolution = targetResolutionPresentation(row); return [row.label, row.area, targetState(row), row.assessment, resolution.status, resolution.reason, resolution.method, resolution.mappingQuality, resolution.commandSimilarityPct, resolution.amdDefinitionLabels.join(' ')].join(' '); },
          geometry: {name: 'runtime-targets', minWidth: '1070px'},
        }
      ));
      return;
    }

    if (state.healthView === 'gating') {
      const parity = ops.definition_parity || {};
      const summary = parity.summary || {};
      const source = parity.source || {};
      const rows = [];
      (parity.matches || []).forEach(function (row) { rows.push(Object.assign({category: 'matched'}, row)); });
      (parity.amd_only || []).forEach(function (row) { rows.push(Object.assign({category: 'amd_only'}, row)); });
      (parity.nvidia_only || []).forEach(function (row) { rows.push(Object.assign({category: 'upstream_only'}, row)); });
      host.append(statusStrip([
        {id: 'definition-upstream', label: 'UPSTREAM DEFINITIONS', value: integer(summary.total_nvidia_steps), meta: '.buildkite/test_areas at one vLLM commit', onOpen: function () { state.healthPlan = 'upstream'; render('ci-health', true); }},
        {id: 'definition-amd', label: 'AMD DEFINITIONS', value: integer(summary.total_amd_steps), meta: '.buildkite/test-amd.yaml exact identities', onOpen: function () { state.healthPlan = 'amd'; render('ci-health', true); }},
        {id: 'definition-matched', label: 'AMD DEFINITIONS MATCHED', value: integer(summary.matched) + ' / ' + integer(summary.total_amd_steps), meta: integer(summary.identity_matches) + ' identity - ' + integer(summary.command_twins) + ' exact-command twins', tone: Number(summary.amd_only) ? 'is-warning' : 'is-success', onOpen: function () { state.healthPlan = 'matched'; render('ci-health', true); }},
        {id: 'definition-unmatched', label: 'UNMATCHED DEFINITIONS', value: integer(summary.amd_only) + ' AMD', meta: integer(summary.nvidia_only) + ' upstream-only; inspect before classifying', tone: Number(summary.amd_only) ? 'is-warning' : 'is-success', onOpen: function () { state.healthPlan = 'unmatched'; render('ci-health', true); }},
      ]));
      const note = n('div', 'ops-evidence-note is-info');
      add(note, [
        n('strong', '', 'Definition coverage, not passing test groups. '),
        n('span', '', 'Every count comes from one commit-pinned vLLM main snapshot. Runtime AMD health remains in Overview and Coverage.'),
        source.commit_url ? externalLink(' Open vLLM ' + String(source.commit_sha || '').slice(0, 12), source.commit_url) : null,
      ]);
      host.append(note);
      const toolbar = n('div', 'ops-toolbar');
      const search = n('input', 'ops-input');
      search.type = 'search'; search.placeholder = 'Search AMD or upstream definitions'; search.value = state.healthSearch;
      search.setAttribute('aria-label', 'Search CI source definitions');
      search.addEventListener('change', function () { state.healthSearch = search.value; render('ci-health', true); });
      const planFilter = n('select', 'ops-select');
      planFilter.setAttribute('aria-label', 'Filter definition match status');
      [['all', 'All comparisons'], ['amd', 'All AMD definitions'], ['upstream', 'All upstream definitions'], ['matched', 'Matched AMD'], ['twins', 'Command twins'], ['changed', 'Command differences'], ['unmatched', 'All unmatched'], ['amd_only', 'AMD-only'], ['upstream_only', 'Upstream-only']].forEach(function (pair) { const option = n('option', '', pair[1]); option.value = pair[0]; option.selected = state.healthPlan === pair[0]; planFilter.append(option); });
      planFilter.addEventListener('change', function () { state.healthPlan = planFilter.value; render('ci-health', true); });
      add(toolbar, [search, planFilter]);
      host.append(toolbar);
      const q = state.healthSearch.trim().toLowerCase();
      const definitions = rows.filter(function (row) {
        if (q && ![row.amd_label, row.nvidia_label, row.label, row.identity_key, row.source, row.amd_source, row.nvidia_source].some(function (part) { return String(part || '').toLowerCase().includes(q); })) return false;
        if (state.healthPlan === 'amd' && !['matched', 'amd_only'].includes(row.category)) return false;
        if (state.healthPlan === 'upstream' && !['matched', 'upstream_only'].includes(row.category)) return false;
        if (state.healthPlan === 'matched' && row.category !== 'matched') return false;
        if (state.healthPlan === 'twins' && row.match_method !== 'command_twin') return false;
        if (state.healthPlan === 'changed' && !(row.category === 'matched' && Number(row.command_similarity) < 0.999999)) return false;
        if (state.healthPlan === 'unmatched' && !['amd_only', 'upstream_only'].includes(row.category)) return false;
        if (state.healthPlan === 'amd_only' && row.category !== 'amd_only') return false;
        if (state.healthPlan === 'upstream_only' && row.category !== 'upstream_only') return false;
        return true;
      }).sort(function (a, b) {
        function priority(row) {
          if (row.category === 'amd_only') return 0;
          if (row.match_method === 'command_twin') return 1;
          if (row.category === 'matched' && Number(row.command_similarity) < 0.999999) return 2;
          if (row.category === 'upstream_only') return 3;
          return 4;
        }
        return priority(a) - priority(b) || String(a.amd_label || a.label || a.nvidia_label).localeCompare(String(b.amd_label || b.label || b.nvidia_label));
      });
      const definitionColumns = [
        {label: 'AMD definition', sticky: true, width: '300px', render: function (row) { return row.amd_label || row.category === 'amd_only' ? linkButton(row.amd_label || row.label, function () { openDefinitionDetail(row, parity); }) : n('span', 'ops-cell-muted', '-'); }},
        {label: 'Upstream definition', width: '300px', render: function (row) { return row.nvidia_label || row.category === 'upstream_only' ? linkButton(row.nvidia_label || row.label, function () { openDefinitionDetail(row, parity); }) : n('span', 'ops-cell-muted', '-'); }},
        {label: 'Match rule', width: '160px', render: function (row) { const label = row.match_method === 'command_twin' ? 'Command twin' : row.match_method === 'identity' ? 'Identity' : row.category === 'amd_only' ? 'AMD-only' : 'Upstream-only'; return linkedBadge(label, null, function () { openDefinitionDetail(row, parity); }, row.match_method === 'command_twin' ? 'is-info' : row.category === 'matched' ? 'is-success' : 'is-warning'); }},
        {label: 'Command match', numeric: true, width: '140px', render: function (row) { return row.command_similarity !== undefined ? linkButton((Number(row.command_similarity) * 100).toFixed(1) + '%', function () { openDefinitionDetail(row, parity); }) : n('span', 'ops-cell-muted', '-'); }},
        {label: 'Source', width: '190px', render: function (row) { const wrap = n('div', 'ops-inline-actions'); if (row.amd_source_url || (row.category === 'amd_only' && row.source_url)) wrap.append(externalLink('AMD YAML', row.amd_source_url || row.source_url)); if (row.nvidia_source_url || (row.category === 'upstream_only' && row.source_url)) wrap.append(externalLink('Upstream YAML', row.nvidia_source_url || row.source_url)); return wrap.childNodes.length ? wrap : n('span', 'ops-cell-muted', '-'); }},
      ];
      host.append(compactTablePanel(
        'Source-definition comparison',
        integer(definitions.length) + ' comparison rows match the active filters; AMD gaps and command-derived twins are shown first',
        definitionColumns,
        definitions,
        {
          id: 'definition-parity-browser',
          limit: 14,
          browserTitle: 'vLLM CI definition parity',
          browserSubtitle: 'Exact source links and command evidence from commit ' + String(source.commit_sha || '').slice(0, 12),
          searchPlaceholder: 'Filter AMD label, upstream label, source, or identity',
          searchText: function (row) { return [row.amd_label, row.nvidia_label, row.label, row.identity_key, row.source, row.amd_source, row.nvidia_source].join(' '); },
          initialQuery: state.healthSearch,
          geometry: {name: 'definition-parity', minWidth: '1090px'},
        }
      ));
      return;
    }

    if (state.healthView === 'coverage') {
      let matrixData = {};
      try { matrixData = await fetchJSON('data/vllm/ci/amd_test_matrix.json'); } catch (_) {}
      const arch = matrixData.architectures || [];
      const coverageRows = sortAmdMatrixRows(matrixData.rows || [], state.healthCoverageSort);
      host.append(statusStrip(arch.map(function (a) {
        return {label: a.label + ' DEFINITIONS', value: integer(a.nightly_match_count) + ' / ' + integer(a.group_count), meta: 'nightly matched groups', tone: a.nightly_match_count === a.group_count ? 'is-success' : 'is-warning'};
      })));
      const architectureHealth = arch.map(function (architecture) {
        const result = {architecture: architecture, passed: 0, incident: 0, unknown: 0, missing: 0};
        coverageRows.forEach(function (row) {
          const cell = (row.cells || {})[architecture.id] || {};
          if (!cell.exists) { result.missing += 1; return; }
          const stateName = observationState({state: cell.latest_state});
          if (stateName === 'passed') result.passed += 1;
          else if (isIncidentObservation({state: stateName})) result.incident += 1;
          else result.unknown += 1;
        });
        return result;
      });
      function architectureRows(architecture) {
        return coverageRows.filter(function (row) { return ((row.cells || {})[architecture.id] || {}).exists; });
      }
      function openArchitectureHealth(health) {
        const architecture = health.architecture;
        const selectedRows = sortArchitectureSignalRows(architectureRows(architecture), architecture.id);
        openTableBrowser({
          id: 'amd-architecture-' + architecture.id,
          title: architecture.label + ' test-group signal',
          subtitle: integer(selectedRows.length) + ' configured groups; non-passing latest results first, then test group A-Z; every result links to its exact AMD Buildkite job',
          rows: selectedRows,
          columns: [
            {label: 'Test group', sticky: true, width: '430px', render: function (row) { return linkButton(row.title, function () { openGroupDetailWithEvidence({name: row.title, area: row.area}, ops); }); }},
            {label: 'Area', width: '180px', render: function (row) { return value(row.area); }},
            {label: 'Latest result', width: '150px', render: function (row) { const cell = (row.cells || {})[architecture.id] || {}; const url = exactPipelineEvidenceUrl({latest_url: cell.latest_url, build_number: cell.latest_build_number}, 'amd-ci'); return linkedBadge(cell.latest_state || 'unobserved', url, null, toneForState(cell.latest_state)); }},
            {label: 'Build', width: '110px', render: function (row) { const cell = (row.cells || {})[architecture.id] || {}; const url = exactPipelineEvidenceUrl({latest_url: cell.latest_url, build_number: cell.latest_build_number}, 'amd-ci'); return url ? externalLink('#' + value(cell.latest_build_number), url, 'ops-mono') : n('span', 'ops-cell-muted', '-'); }},
          ],
          searchText: function (row) { return [row.title, row.area, (((row.cells || {})[architecture.id] || {}).latest_state)].join(' '); },
          geometry: {name: 'amd-architecture', minWidth: '900px'},
        });
      }
      const scorecard = n('section', 'ops-architecture-scorecard');
      const scorecardHeader = n('header', 'ops-panel-header');
      add(scorecardHeader, [n('div', 'ops-panel-title', 'AMD architecture health'), n('div', 'ops-panel-meta', 'Exact latest counts and rates; select an architecture for every group and Buildkite job')]);
      scorecard.append(scorecardHeader);
      const scorecardRows = n('div', 'ops-architecture-rows');
      architectureHealth.forEach(function (health) {
        const architecture = health.architecture;
        const configured = health.passed + health.incident + health.unknown;
        const passRate = configured ? health.passed / configured * 100 : null;
        const control = n('button', 'ops-architecture-row');
        control.type = 'button';
        control.setAttribute('aria-label', 'Inspect ' + architecture.label + ': ' + integer(health.passed) + ' passing, ' + integer(health.incident) + ' incident, ' + integer(health.unknown) + ' unobserved');
        control.addEventListener('click', function () { openArchitectureHealth(health); });
        const identity = n('div', 'ops-architecture-identity');
        add(identity, [n('strong', '', architecture.label), n('span', '', integer(configured) + ' configured groups')]);
        const bar = n('div', 'ops-architecture-bar');
        [['is-passed', health.passed], ['is-incident', health.incident], ['is-unknown', health.unknown]].forEach(function (entry) {
          if (!entry[1]) return;
          const segment = n('span', 'ops-architecture-segment ' + entry[0]);
          segment.style.width = entry[1] / Math.max(1, configured) * 100 + '%';
          bar.append(segment);
        });
        const metrics = n('div', 'ops-architecture-metrics');
        add(metrics, [
          n('span', 'is-passed', integer(health.passed) + ' passing'),
          n('span', 'is-incident', integer(health.incident) + ' incident'),
          n('span', 'is-unknown', integer(health.unknown) + ' unobserved'),
        ]);
        const rate = n('div', 'ops-architecture-rate ' + (Number(passRate) >= 90 ? 'is-success' : Number(passRate) >= 50 ? 'is-warning' : 'is-danger'));
        add(rate, [n('strong', '', passRate === null ? '-' : passRate.toFixed(1) + '%'), n('span', '', 'passing')]);
        add(control, [identity, bar, metrics, rate]);
        scorecardRows.append(control);
      });
      scorecard.append(scorecardRows);
      host.append(scorecard);
      const cols = [{label: 'Group', sticky: true, render: function (r) { return linkButton(r.title, function () { openGroupDetailWithEvidence({name: r.title, area: r.area}, ops); }); }}, {label: 'Area', render: function (r) { return linkButton(value(r.area), function () { openGroupDetailWithEvidence({name: r.title, area: r.area}, ops); }); }}];
      for (const a of arch) {
        cols.push({label: a.label, render: function (r) {
          const c = (r.cells || {})[a.id] || {};
          if (!c.exists) return n('span', 'ops-cell-muted', '-');
          return linkedBadge(c.latest_state || 'unknown', exactPipelineEvidenceUrl({job_url: c.latest_url}, 'amd-ci'), function () { openGroupDetailWithEvidence({name: r.title, area: r.area}, ops); });
        }});
      }
      const coverageSortGroup = n('div', 'ops-panel-header-actions');
      const coverageSort = segmented([
        {id: 'platform', label: 'Platform'},
        {id: 'name', label: 'Test group'},
        {id: 'area', label: 'Test area'},
      ], state.healthCoverageSort, function (sortMode) {
        setRouteState('ci-health', 'healthCoverageSort', sortMode, 'health_sort');
      }, 'Sort AMD test matrix');
      add(coverageSortGroup, [n('span', 'ops-toolbar-label', 'Sort matrix'), coverageSort]);
      host.append(compactTablePanel(
        'AMD test matrix details',
        amdMatrixSortDescription(state.healthCoverageSort),
        cols,
        coverageRows,
        {
          id: 'coverage-browser',
          limit: 16,
          headerActions: coverageSortGroup,
          previewLabel: 'sorted rows',
          browserTitle: 'Complete AMD test matrix',
          browserSubtitle: integer(coverageRows.length) + ' group definitions across ' + integer(arch.length) + ' architectures - ' + amdMatrixSortDescription(state.healthCoverageSort),
          searchPlaceholder: 'Filter test group or area',
          searchText: function (row) { return [row.title, row.area].join(' '); },
          geometry: {name: 'coverage', minWidth: Math.max(760, 360 + arch.length * 150) + 'px'},
        }
      ));
      return;
    }

    const reliability = canonicalReliability(ops);
    const retry = reliability.retry_analysis || {};
    const retrySummary = retry.summary || {};
    const amdProvenance = ((ops.amd_test_health || {}).provenance || {});
    const amdJoin = amdProvenance.nightly_metadata || {};
    const totalAmdObservations = Number(amdJoin.joined_group_observations || 0) + Number(amdJoin.unjoined_group_observations || 0);
    const queueHistorySummary = ((ops.queue || {}).history_summary || {});
    host.append(statusStrip([
      {id: 'diagnostic-amd-join', label: 'AMD JOB OUTCOME JOINS', value: integer(amdJoin.joined_group_observations) + ' / ' + integer(totalAmdObservations), meta: integer(amdJoin.unjoined_group_observations) + ' unjoined observations', tone: Number(amdJoin.unjoined_group_observations) ? 'is-danger' : 'is-success', onOpen: function () { navigateTo('ci-analytics', {analyticsView: 'groups'}); }},
      {id: 'diagnostic-upstream-groups', label: 'UPSTREAM GROUP LEDGER', value: integer(reliabilityCatalog(reliability).length), meta: integer((reliability.flaky_candidates || []).length) + ' mixed-outcome candidates', onOpen: function () { navigateTo('ci-analytics', {analyticsView: 'flakes'}); }},
      {id: 'diagnostic-retries', label: 'EXPLICIT RETRY LEDGER', value: integer(retrySummary.retry_attempt_count), meta: integer(retrySummary.failed_then_passed_recovery_count) + ' fail-to-pass chains', tone: Number(retrySummary.failed_then_passed_recovery_count) ? 'is-warning' : 'is-neutral', onOpen: function () { navigateTo('ci-analytics', {analyticsView: 'retries'}); }},
      {id: 'diagnostic-queue-history', label: 'QUEUE HISTORY', value: integer(queueHistorySummary.snapshot_count), meta: integer(queueHistorySummary.counts_only_snapshot_count) + ' counts-only snapshots', tone: Number(queueHistorySummary.counts_only_snapshot_count) ? 'is-warning' : 'is-success', onOpen: function () { navigateTo('ci-queue', {queueView: 'history'}); }},
    ]));
    const diagnosticNote = n('div', 'ops-evidence-note is-info');
    add(diagnosticNote, [n('strong', '', 'Collector integrity, not another failure list. '), n('span', '', 'Use this pane to check freshness and join coverage. AMD health, upstream flakes, retries, and queue history each have their own investigation view.')]);
    host.append(diagnosticNote);
      const sourceDescriptions = {
        analytics: 'Buildkite build and job outcomes',
        agent_health: 'AMD CI agent observations',
        amd_test_signal: 'Latest observed AMD nightly test signal',
      ci_health: 'Published CI health snapshot',
      gating_targets: 'Reviewed gating target plan',
      gating_target_candidates: 'Proposed target candidates',
      amd_test_matrix: 'AMD architecture definition matrix',
      capacity_monitor: 'Queue capacity and connected-agent snapshot',
      queue_timeseries: 'Retained queue counts and wait measurements',
      queue_jobs: 'Current active Buildkite jobs',
      group_changes: 'Test-group definition changes',
      omni_heuristic: 'Omni surge thresholds',
      omni_issue_state: 'Open Omni operational issues',
    };
      const sourceRows = Object.entries(ops.sources || {}).map(function (entry) {
        return {id: entry[0], record: entry[1] || {}, description: sourceDescriptions[entry[0]] || 'Published collector input'};
      }).sort(function (a, b) { return String(a.id).localeCompare(String(b.id)); });
      function publishedSourceUrl(row) {
        return row.record.published === false ? '' : 'data/vllm/ci/' + row.record.path;
      }
      host.append(panel('Collector input freshness', integer(sourceRows.length) + ' published source contracts', dataTable([
      {label: 'Input', sticky: true, width: '210px', render: function (row) { const url = publishedSourceUrl(row); return url ? externalLink(row.id.replaceAll('_', ' '), url, 'ops-cell-primary') : n('span', 'ops-cell-primary', row.id.replaceAll('_', ' ')); }},
      {label: 'Purpose', width: '360px', render: function (row) { return row.description; }},
      {label: 'Observed', width: '190px', render: function (row) { const url = publishedSourceUrl(row); return url ? externalLink(shortDate(row.record.timestamp), url) : n('span', '', shortDate(row.record.timestamp)); }},
      {label: 'Freshness', width: '130px', render: function (row) { const tone = Date.now() - new Date(row.record.timestamp).getTime() > 48 * 3600000 ? 'is-warning' : 'is-success'; const url = publishedSourceUrl(row); return url ? linkedBadge(age(row.record.timestamp), url, null, tone) : badge(age(row.record.timestamp), tone); }},
      {label: 'Timestamp source', width: '160px', render: function (row) { return badge(value(row.record.timestamp_source), 'is-neutral'); }},
    ], sourceRows, 'Every row opens the exact published collector input', {name: 'diagnostic-sources', minWidth: '1050px'})));
  }

  function reliabilityIncidentRate(row) {
    const raw = row.incident_rate_pct !== undefined ? row.incident_rate_pct : row.fail_rate;
    return Number.isFinite(Number(raw)) ? Number(raw) : 0;
  }

  function groupHistoryObservations(row, cohort) {
    return evidenceObservations(row || {}).filter(function (observation) {
      return (!observation.source_pipeline || observation.source_pipeline === 'ci')
        && Boolean(exactPipelineEvidenceUrl(observation, 'ci'))
        && (cohort !== 'nightly' || isNightlyObservation(observation));
    }).sort(function (a, b) {
      return new Date(observationTimestamp(a) || 0) - new Date(observationTimestamp(b) || 0);
    });
  }

  function reliabilityBandDefinitions() {
    return [
      {id: 'stable', label: 'Stable', description: 'No retained incidents', tone: 'is-success', matches: function (rate) { return rate === 0; }},
      {id: 'watch', label: 'Watch', description: 'Above 0% and below 10%', tone: 'is-info', matches: function (rate) { return rate > 0 && rate < 10; }},
      {id: 'elevated', label: 'Elevated', description: '10% to below 25%', tone: 'is-warning', matches: function (rate) { return rate >= 10 && rate < 25; }},
      {id: 'high', label: 'High', description: '25% to below 50%', tone: 'is-warning', matches: function (rate) { return rate >= 25 && rate < 50; }},
      {id: 'critical', label: 'Critical', description: '50% or greater', tone: 'is-danger', matches: function (rate) { return rate >= 50; }},
    ];
  }

  function reliabilityRiskClusters(rows) {
    return reliabilityBandDefinitions().map(function (definition) {
      const members = rows.filter(function (row) { return definition.matches(reliabilityIncidentRate(row)); });
      const rates = members.map(reliabilityIncidentRate);
      const latestIncidents = members.filter(function (row) { return isIncidentObservation(latestObservation(row) || {}); }).length;
      return Object.assign({}, definition, {
        rows: members,
        count: members.length,
        medianRate: percentileValue(rates, 0.5),
        latestIncidents: latestIncidents,
      });
    });
  }

  function reliabilityHardwareClusters(rows) {
    const clusters = new Map();
    rows.forEach(function (row) {
      const hardware = value(row.hardware || row.hw, 'unknown');
      if (!clusters.has(hardware)) clusters.set(hardware, []);
      clusters.get(hardware).push(row);
    });
    return Array.from(clusters.entries()).map(function (entry) {
      const members = entry[1];
      return {
        id: entry[0],
        label: entry[0].toUpperCase(),
        rows: members,
        count: members.length,
        incidentObserved: members.filter(function (row) { return reliabilityIncidentRate(row) > 0; }).length,
      };
    }).sort(function (a, b) { return b.count - a.count || a.label.localeCompare(b.label); });
  }

  function openReliabilityList(title, subtitle, rows, ops, reliability, initialQuery) {
    const content = n('div', 'ops-evidence ops-reliability-browser');
    const toolbar = n('div', 'ops-toolbar ops-evidence-toolbar');
    const search = n('input', 'ops-input');
    search.type = 'search';
    search.placeholder = 'Filter group, hardware, or queue';
    search.value = initialQuery || '';
    search.setAttribute('aria-label', 'Filter test-group list');
    const resultFilter = n('select', 'ops-select');
    resultFilter.setAttribute('aria-label', 'Filter test groups by latest result');
    [['all', 'All latest results'], ['passing', 'Currently passing'], ['incident', 'Current incidents'], ['mixed', 'Mixed outcomes']].forEach(function (pair) {
      const option = n('option', '', pair[1]);
      option.value = pair[0];
      resultFilter.append(option);
    });
    add(toolbar, [search, resultFilter]);
    content.append(toolbar);
    const tableHost = n('div', 'ops-evidence-table-host');
    content.append(tableHost);
    let page = 0;
    const pageSize = 50;
    const pager = n('div', 'ops-browser-pagination');
    const previous = button('Previous', function () { page -= 1; renderRows(); });
    const position = n('span', 'ops-browser-position');
    const next = button('Next', function () { page += 1; renderRows(); });
    add(pager, [previous, position, next]);
    content.append(pager);

    function renderRows() {
      const query = normalizeLabel(search.value);
      const mode = resultFilter.value;
      const filtered = rows.filter(function (row) {
        const latest = latestObservation(row);
        if (mode === 'passing' && observationState(latest || {}) !== 'passed') return false;
        if (mode === 'incident' && !isIncidentObservation(latest || {})) return false;
        if (mode === 'mixed' && !(row.mixed_outcomes || (reliabilityIncidentRate(row) > 0 && Number(row.passed || 0) > 0))) return false;
        if (!query) return true;
        return [row.name, row.hardware, (row.queues || []).join(' '), row.id]
          .some(function (part) { return normalizeLabel(part).includes(query); });
      });
      const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
      page = Math.max(0, Math.min(page, pageCount - 1));
      const start = page * pageSize;
      const visible = filtered.slice(start, start + pageSize);
      clear(tableHost);
      tableHost.append(dataTable([
        {label: 'Test group', sticky: true, width: '360px', render: function (row) { return groupIdentityCell(row, function () { openGroupDetail(row, ops, row, reliability); }); }},
        {label: 'Runs', numeric: true, width: '90px', render: function (row) { return linkButton(integer(row.runs !== undefined ? row.runs : row.observation_count), function () { openGroupDetail(row, ops, row, reliability); }); }},
        {label: 'Latest', width: '130px', render: function (row) { const latest = latestObservation(row); return linkedBadge(latest ? observationState(latest) : 'pending', exactPipelineEvidenceUrl(latest, 'ci'), function () { openGroupDetail(row, ops, row, reliability); }); }},
        {label: 'Incident rate', numeric: true, width: '130px', render: function (row) { return linkButton(reliabilityIncidentRate(row).toFixed(1) + '%', function () { openGroupDetail(row, ops, row, reliability); }); }},
        {label: 'p90 completion', numeric: true, width: '140px', render: function (row) { return linkButton(duration(row.p90_dur), function () { openGroupDetail(row, ops, row, reliability); }); }},
        {label: 'Hardware', width: '110px', render: function (row) { return badge(value(row.hardware, 'unknown'), 'is-neutral'); }},
        {label: 'Queues', width: '220px', render: function (row) { const links = n('div', 'ops-inline-links'); (row.queues || []).forEach(function (queueName) { links.append(linkButton(queueName, function () { navigateTo('ci-queue', {queueView: 'history', queueHistoryQueue: queueName, queueScope: isAmdQueue(queueName) ? 'amd' : 'all'}); }, 'Open queue history for ' + queueName)); }); return links.childNodes.length ? links : n('span', 'ops-cell-muted', '-'); }},
        {label: 'History', width: '140px', render: function (row) { return linkButton(integer(groupHistoryObservations(row, 'main').length) + ' runs', function () { openGroupDetail(row, ops, row, reliability); }, 'Open exact pass, incident, and latency history'); }},
      ], visible, integer(start + 1) + '-' + integer(start + visible.length) + ' of ' + integer(filtered.length) + ' matching test groups', {name: 'reliability-browser', minWidth: '1320px'}));
      position.textContent = 'Page ' + integer(page + 1) + ' of ' + integer(pageCount);
      previous.disabled = page === 0;
      next.disabled = page >= pageCount - 1;
      pager.hidden = filtered.length <= pageSize;
    }

    search.addEventListener('input', function () { page = 0; renderRows(); });
    resultFilter.addEventListener('change', function () { page = 0; renderRows(); });
    renderRows();
    openOverlay(title, subtitle, content, true, 'reliability-browser-' + normalizeLabel(title));
    requestAnimationFrame(function () { search.focus(); });
  }

  function clusterTile(cluster, onOpen) {
    const tile = n('button', 'ops-cluster-tile ' + (cluster.tone || ''));
    tile.type = 'button';
    tile.setAttribute('aria-label', 'Open ' + cluster.label + ' cluster with ' + integer(cluster.count) + ' test groups');
    const head = n('div', 'ops-cluster-tile-head');
    add(head, [n('span', 'ops-cluster-label', cluster.label), n('span', 'ops-cluster-count', integer(cluster.count))]);
    const median = cluster.medianRate === null || cluster.medianRate === undefined ? '-' : Number(cluster.medianRate).toFixed(1) + '% median incident';
    add(tile, [head, n('div', 'ops-cluster-description', cluster.description), n('div', 'ops-cluster-meta', median + ' - ' + integer(cluster.latestIncidents || 0) + ' current incidents')]);
    tile.addEventListener('click', onOpen);
    return tile;
  }

  function renderReliabilityClusters(host, title, subtitle, rows, ops, reliability, initialQuery) {
    const section = n('section', 'ops-cluster-section');
    const header = n('header', 'ops-section-header');
    const heading = n('div', 'ops-section-heading');
    add(heading, [n('h2', 'ops-section-title', title), n('p', 'ops-section-description', subtitle)]);
    const browse = button('Browse all ' + integer(rows.length), function () {
      openReliabilityList(title, 'Search and inspect every exact upstream test-group variant', rows, ops, reliability, initialQuery);
    });
    add(header, [heading, browse]);
    section.append(header);
    const grid = n('div', 'ops-cluster-grid');
    reliabilityRiskClusters(rows).filter(function (cluster) { return cluster.count > 0; }).forEach(function (cluster) {
      grid.append(clusterTile(cluster, function () {
        openReliabilityList(cluster.label + ' reliability', cluster.description, cluster.rows, ops, reliability);
      }));
    });
    section.append(grid);
    host.append(section);
  }

  function chooseAnalyticsGroup(rows) {
    const byId = rows.find(function (row) { return row.id === state.analyticsGroupId; });
    if (byId) return byId;
    const bySearch = state.analyticsSearch && rows.find(function (row) { return normalizeLabel(row.name) === normalizeLabel(state.analyticsSearch); });
    if (bySearch) return bySearch;
    return rows.filter(function (row) { return row.mixed_outcomes && groupHistoryObservations(row, 'main').length; }).sort(function (a, b) {
      return new Date(b.latest_observed_at || 0) - new Date(a.latest_observed_at || 0);
    })[0] || rows.find(function (row) { return groupHistoryObservations(row, 'main').length; }) || rows[0];
  }

  function historyOutcomeTone(observation) {
    const result = observationState(observation);
    if (result === 'passed') return 'is-passed';
    if (['soft', 'soft_fail', 'soft_failed'].includes(result)) return 'is-soft';
    if (isIncidentObservation(observation)) return 'is-hard';
    return 'is-unknown';
  }

  function historyOutcomeLabel(observation) {
    const result = observationState(observation);
    if (result === 'passed') return 'Passed';
    if (['soft', 'soft_fail', 'soft_failed'].includes(result)) return 'Soft incident';
    if (['incident', 'error'].includes(result)) return 'Incident';
    if (isIncidentObservation(observation)) return 'Hard incident';
    return value(result, 'Unknown');
  }

  function cohortPassStreak(observations) {
    let streak = 0;
    for (let index = observations.length - 1; index >= 0; index -= 1) {
      if (observationState(observations[index]) !== 'passed') break;
      streak += 1;
    }
    return streak;
  }

  function observationPassRate(observations) {
    if (!observations.length) return null;
    const passed = observations.filter(function (row) { return observationState(row) === 'passed'; }).length;
    return passed / observations.length * 100;
  }

  function historyRunCell(observation, pipeline) {
    pipeline = pipeline || 'ci';
    const build = observation.build_number ? '#' + observation.build_number : shortDate(observationTimestamp(observation));
    const outcome = historyOutcomeLabel(observation);
    const observed = shortDate(observationTimestamp(observation));
    const url = exactPipelineEvidenceUrl(observation, pipeline);
    const cell = n(url ? 'a' : 'span', 'ops-run-cell ' + historyOutcomeTone(observation));
    if (url) {
      cell.href = url;
      cell.target = '_blank';
      cell.rel = 'noopener';
      cell.setAttribute('aria-label', 'Open ' + build + ', ' + outcome + ', observed ' + observed + ' in Buildkite');
    } else {
      cell.setAttribute('aria-label', build + ', ' + outcome + ', observed ' + observed + '; exact job link unavailable');
    }
    cell.title = build + ' - ' + outcome + ' - ' + observed;
    return cell;
  }

  function historyBatch(observations, startIndex, pipeline) {
    const passed = observations.filter(function (row) { return observationState(row) === 'passed'; }).length;
    const incidents = observations.filter(isIncidentObservation).length;
    const first = observations[0] || {};
    const last = observations[observations.length - 1] || {};
    const card = n('article', 'ops-run-batch');
    const header = n('header', 'ops-run-batch-header');
    add(header, [
      n('strong', '', 'Runs ' + integer(startIndex + 1) + '-' + integer(startIndex + observations.length)),
      n('span', incidents ? 'is-warning' : 'is-success', percent(passed, observations.length, 0) + ' pass'),
    ]);
    const cells = n('div', 'ops-run-cells');
    observations.forEach(function (observation) { cells.append(historyRunCell(observation, pipeline)); });
    const firstBuild = first.build_number ? '#' + first.build_number : shortDate(observationTimestamp(first));
    const lastBuild = last.build_number ? '#' + last.build_number : shortDate(observationTimestamp(last));
    add(card, [header, cells, n('div', 'ops-run-batch-range ops-mono', firstBuild + ' to ' + lastBuild)]);
    return card;
  }

  function historyIncidentRow(observation, pipeline) {
    pipeline = pipeline || 'ci';
    const row = n('article', 'ops-incident-row');
    const top = n('div', 'ops-incident-row-head');
    const build = observation.build_number ? '#' + observation.build_number : shortDate(observationTimestamp(observation));
    add(top, [
      externalLink(build, exactPipelineEvidenceUrl(observation, pipeline), 'ops-history-build ops-mono'),
      badge(historyOutcomeLabel(observation), historyOutcomeTone(observation) === 'is-soft' ? 'is-warning' : 'is-danger'),
      n('time', 'ops-incident-time', shortDate(observationTimestamp(observation))),
    ]);
    const completion = observationDurationMinutes(observation);
    const wait = observationWaitMinutes(observation);
    const facts = [
      completion !== null ? 'ran ' + duration(completion) : null,
      wait !== null ? 'waited ' + duration(wait) : null,
      observation.queue || null,
    ].filter(Boolean).join(' - ');
    const message = String(observation.message || observation.build_message || '').split('\n')[0];
    add(row, [top, facts ? n('div', 'ops-incident-facts', facts) : null, message ? n('div', 'ops-incident-message', message) : null]);
    return row;
  }

  function openAllGroupHistoryMap(rows, ops, reliability) {
    const sourceRows = rows.filter(function (row) { return groupHistoryObservations(row, 'main').length; });
    const hardware = Array.from(new Set(sourceRows.map(function (row) { return row.hardware || 'unknown'; }))).sort();
    const content = n('div', 'ops-history-map-content');
    const controls = n('div', 'ops-toolbar ops-history-map-controls');
    const search = n('input', 'ops-input');
    search.type = 'search';
    search.placeholder = 'Filter test groups, hardware, or queue';
    search.setAttribute('aria-label', 'Filter complete test-group history');
    const hardwareSelect = n('select', 'ops-select');
    appendHardwareOptions(hardwareSelect, hardware, 'all');
    controls.append(search);
    controls.append(hardwareSelect);
    const cohortHost = n('div');
    const filterHost = n('div');
    controls.append(cohortHost);
    controls.append(filterHost);
    content.append(controls);
    const summaryHost = n('div');
    const mapHost = n('div');
    content.append(summaryHost);
    content.append(mapHost);
    const local = {cohort: state.analyticsGroupCohort, filter: 'all', hardware: 'all', query: ''};

    function chooseGroup(row) {
      closeOverlay();
      state.analyticsGroupId = row.id;
      state.analyticsSearch = '';
      state.analyticsGroupCohort = local.cohort;
      setQueryValue('analytics_group', row.id);
      setQueryValue('analytics_search', null);
      setQueryValue('analytics_cohort', local.cohort);
      render('ci-analytics', true);
    }

    function renderMap() {
      clear(cohortHost);
      cohortHost.append(segmented([{id: 'main', label: 'All main'}, {id: 'nightly', label: 'Nightly only'}], local.cohort, function (cohort) { local.cohort = cohort; renderMap(); }, 'Complete history cohort'));
      clear(filterHost);
      filterHost.append(segmented([
        {id: 'all', label: 'All'}, {id: 'incident', label: 'Incident latest'},
        {id: 'mixed', label: 'Mixed history'}, {id: 'stable', label: 'Stable passing'},
      ], local.filter, function (filter) { local.filter = filter; renderMap(); }, 'Complete history state filter'));
      let prepared = sourceRows.map(function (row) {
        const observations = groupHistoryObservations(row, local.cohort);
        const passed = observations.filter(function (observation) { return observationState(observation) === 'passed'; }).length;
        const incidents = observations.filter(isIncidentObservation).length;
        const latest = observations[observations.length - 1] || {};
        return {row: row, observations: observations, passed: passed, incidents: incidents, latest: latest, passRate: observations.length ? passed / observations.length * 100 : null};
      }).filter(function (item) {
        if (!item.observations.length) return false;
        if (local.hardware !== 'all' && (item.row.hardware || 'unknown') !== local.hardware) return false;
        if (local.query && ![item.row.name, item.row.hardware, (item.row.queues || []).join(' ')].some(function (part) { return String(part || '').toLowerCase().includes(local.query); })) return false;
        if (local.filter === 'incident' && !isIncidentObservation(item.latest)) return false;
        if (local.filter === 'mixed' && !(item.passed && item.incidents)) return false;
        if (local.filter === 'stable' && item.incidents) return false;
        return true;
      }).sort(function (a, b) {
        const latestDelta = Number(isIncidentObservation(b.latest)) - Number(isIncidentObservation(a.latest));
        return latestDelta || Number(a.passRate || 0) - Number(b.passRate || 0) || a.row.name.localeCompare(b.row.name);
      });
      const totalRuns = prepared.reduce(function (sum, item) { return sum + item.observations.length; }, 0);
      const latestIncidents = prepared.filter(function (item) { return isIncidentObservation(item.latest); }).length;
      const mixed = prepared.filter(function (item) { return item.passed && item.incidents; }).length;
      const rates = prepared.map(function (item) { return item.passRate; }).filter(function (rate) { return rate !== null; });
      const medianRate = percentileValue(rates, 0.5);
      clear(summaryHost);
      summaryHost.append(statusStrip([
        {label: 'GROUPS SHOWN', value: integer(prepared.length), meta: integer(sourceRows.length) + ' with retained main history'},
        {label: 'EXACT RUNS VISIBLE', value: integer(totalRuns), meta: local.cohort === 'nightly' ? 'nightly observations' : 'all-main observations'},
        {label: 'INCIDENT LATEST', value: integer(latestIncidents), meta: 'groups ending in an incident', tone: latestIncidents ? 'is-danger' : 'is-success'},
        {label: 'MEDIAN PASS RATE', value: medianRate === null ? '-' : medianRate.toFixed(1) + '%', meta: integer(mixed) + ' groups have mixed outcomes', tone: medianRate >= 95 ? 'is-success' : medianRate >= 80 ? 'is-warning' : 'is-danger'},
      ]));
      clear(mapHost);
      if (!prepared.length) {
        mapHost.append(n('div', 'ops-empty', 'No retained groups match these filters.'));
        return;
      }
      const viewport = n('div', 'ops-history-map-viewport');
      const map = n('div', 'ops-history-map');
      const mapHeader = n('div', 'ops-history-map-row ops-history-map-header');
      add(mapHeader, [n('span', '', 'Test group'), n('span', '', 'Pass rate'), n('span', '', 'Latest'), n('span', '', 'Latest 30 exact runs - oldest to newest')]);
      map.append(mapHeader);
      prepared.forEach(function (item) {
        const row = n('div', 'ops-history-map-row');
        const identity = n('div', 'ops-history-map-identity');
        identity.append(linkButton(item.row.name, function () { chooseGroup(item.row); }, 'Open selected-group history for ' + item.row.name));
        identity.append(n('span', 'ops-entity-meta', hardwareDisplayLabel(item.row.hardware) + ' - ' + (item.row.queues || []).join(', ')));
        const rate = linkButton(item.passRate === null ? '-' : item.passRate.toFixed(1) + '%', function () { chooseGroup(item.row); }, 'Open all retained outcomes for ' + item.row.name);
        rate.classList.add('ops-history-map-rate');
        const latestUrl = exactPipelineEvidenceUrl(item.latest, 'ci');
        const latest = linkedBadge(historyOutcomeLabel(item.latest), latestUrl, function () { chooseGroup(item.row); }, historyOutcomeTone(item.latest) === 'is-passed' ? 'is-success' : historyOutcomeTone(item.latest) === 'is-soft' ? 'is-warning' : 'is-danger');
        const track = n('div', 'ops-history-map-track');
        const visible = item.observations.slice(-30);
        for (let index = visible.length; index < 30; index += 1) track.append(n('span', 'ops-run-cell is-empty'));
        visible.forEach(function (observation) { track.append(historyRunCell(observation, 'ci')); });
        add(row, [identity, rate, latest, track]);
        map.append(row);
      });
      viewport.append(map);
      mapHost.append(viewport);
    }

    search.addEventListener('input', function () { local.query = search.value.trim().toLowerCase(); renderMap(); });
    hardwareSelect.addEventListener('change', function () { local.hardware = hardwareSelect.value; renderMap(); });
    renderMap();
    openOverlay('Complete test-group history', 'All retained strict upstream groups with exact Buildkite outcomes', content, true, 'all-group-history');
  }

  function renderGroupHistoryExplorer(host, rows, ops, reliability) {
    const choices = rows.filter(function (row) { return groupHistoryObservations(row, 'main').length; }).slice().sort(function (a, b) { return String(a.name).localeCompare(String(b.name)); });
    const selected = chooseAnalyticsGroup(choices);
    if (!selected) return;
    state.analyticsGroupId = selected.id;
    const observations = groupHistoryObservations(selected, state.analyticsGroupCohort);
    const section = n('section', 'ops-history-explorer');
    const header = n('header', 'ops-section-header');
    const heading = n('div', 'ops-section-heading');
    add(heading, [n('h2', 'ops-section-title', 'Test-group reliability'), n('p', 'ops-section-description', 'Choose one strict upstream group. Every outcome below is an exact Buildkite job, ordered from oldest to newest.')]);
    header.append(heading);
    section.append(header);

    const controls = n('div', 'ops-toolbar ops-history-controls');
    const groupField = n('label', 'ops-field ops-history-group-field');
    groupField.append(n('span', 'ops-field-label', 'Test group'));
    const groupSelect = n('select', 'ops-select');
    groupSelect.setAttribute('aria-label', 'Select test group for historical analysis');
    choices.forEach(function (row) {
      const option = n('option', '', row.name + ' - ' + value(row.hardware, 'unknown'));
      option.value = row.id;
      option.selected = row.id === selected.id;
      groupSelect.append(option);
    });
    groupSelect.addEventListener('change', function () {
      state.analyticsGroupId = groupSelect.value;
      state.analyticsSearch = '';
      setQueryValue('analytics_group', state.analyticsGroupId);
      setQueryValue('analytics_search', null);
      render('ci-analytics', true);
    });
    groupField.append(groupSelect);
    controls.append(groupField);
    controls.append(segmented([{id: 'main', label: 'All main'}, {id: 'nightly', label: 'Nightly only'}], state.analyticsGroupCohort, function (cohort) {
      setRouteState('ci-analytics', 'analyticsGroupCohort', cohort, 'analytics_cohort');
    }, 'Test-group history cohort'));
    const actions = n('div', 'ops-history-actions');
    actions.append(button('Explore all groups', function () { openAllGroupHistoryMap(choices, ops, reliability); }));
    actions.append(button('Open full run evidence', function () { openGroupDetail(selected, ops, selected, reliability); }));
    const latest = observations[observations.length - 1] || latestObservation(selected);
    const latestUrl = exactPipelineEvidenceUrl(latest, 'ci');
    if (latestUrl) actions.append(externalLink('Latest Buildkite job', latestUrl, 'ops-button'));
    controls.append(actions);
    section.append(controls);

    if (!observations.length) {
      section.append(n('div', 'ops-empty', 'No exact nightly observations are retained for this strict group. Switch to All main to inspect its complete retained history.'));
      host.append(section);
      return;
    }

    const passed = observations.filter(function (row) { return observationState(row) === 'passed'; }).length;
    const soft = observations.filter(function (row) { return ['soft', 'soft_fail', 'soft_failed'].includes(observationState(row)); }).length;
    const incidents = observations.filter(isIncidentObservation);
    const hard = Math.max(0, incidents.length - soft);
    const latestResult = observations[observations.length - 1];
    const streak = cohortPassStreak(observations);
    const lastIncidentIndex = observations.map(function (row) { return isIncidentObservation(row); }).lastIndexOf(true);
    const incidentDistance = lastIncidentIndex >= 0 ? observations.length - lastIncidentIndex - 1 : null;
    const lastIncidentObservation = lastIncidentIndex >= 0 ? observations[lastIncidentIndex] : null;
    const recentWindow = observations.slice(-Math.min(10, observations.length));
    const priorWindow = observations.slice(Math.max(0, observations.length - recentWindow.length * 2), observations.length - recentWindow.length);
    const recentRate = observationPassRate(recentWindow);
    const priorRate = observationPassRate(priorWindow);
    const rateDelta = recentRate !== null && priorRate !== null ? recentRate - priorRate : null;
    const completionValues = observations.map(observationDurationMinutes).filter(function (minutes) { return minutes !== null; });
    const medianCompletion = percentileValue(completionValues, 0.5);
    const p90Completion = percentileValue(completionValues, 0.9);
    const overallTone = passed === observations.length ? 'is-success' : passed / observations.length >= 0.9 ? 'is-warning' : 'is-danger';

    const snapshot = n('div', 'ops-history-snapshot');
    const score = n('div', 'ops-history-score ' + overallTone);
    const scoreTrack = n('div', 'ops-history-score-track');
    const scoreFill = n('div', 'ops-history-score-fill');
    scoreFill.style.width = passed / observations.length * 100 + '%';
    scoreTrack.append(scoreFill);
    add(score, [
      n('div', 'ops-history-fact-label', 'RETAINED PASS RATE'),
      n('strong', 'ops-history-score-value', percent(passed, observations.length)),
      n('div', 'ops-history-score-meta', integer(passed) + ' passed - ' + integer(hard) + ' hard - ' + integer(soft) + ' soft'),
      scoreTrack,
    ]);
    snapshot.append(score);

    const currentSignal = n('div', 'ops-history-fact');
    add(currentSignal, [n('div', 'ops-history-fact-label', 'CURRENT SIGNAL'), badge(historyOutcomeLabel(latestResult), historyOutcomeTone(latestResult) === 'is-passed' ? 'is-success' : historyOutcomeTone(latestResult) === 'is-soft' ? 'is-warning' : 'is-danger'), n('div', 'ops-history-fact-meta', integer(streak) + '-run passing streak')]);
    snapshot.append(currentSignal);

    const incidentFact = n('div', 'ops-history-fact');
    const incidentValue = lastIncidentObservation
      ? externalLink('#' + value(lastIncidentObservation.build_number), exactPipelineEvidenceUrl(lastIncidentObservation, 'ci'), 'ops-history-fact-value ops-mono')
      : n('strong', 'ops-history-fact-value is-success', 'None retained');
    add(incidentFact, [n('div', 'ops-history-fact-label', 'LAST INCIDENT'), incidentValue, n('div', 'ops-history-fact-meta', lastIncidentObservation ? integer(incidentDistance) + ' runs ago - ' + shortDate(observationTimestamp(lastIncidentObservation)) : 'No incidents in this cohort')]);
    snapshot.append(incidentFact);

    const recentFact = n('div', 'ops-history-fact');
    const deltaText = rateDelta === null ? 'No prior comparison window' : (rateDelta > 0 ? '+' : '') + rateDelta.toFixed(1) + ' pp vs prior ' + integer(priorWindow.length);
    add(recentFact, [n('div', 'ops-history-fact-label', 'RECENT ' + integer(recentWindow.length)), n('strong', 'ops-history-fact-value ' + (rateDelta !== null && rateDelta < 0 ? 'is-warning' : 'is-success'), recentRate === null ? '-' : recentRate.toFixed(1) + '%'), n('div', 'ops-history-fact-meta', deltaText)]);
    snapshot.append(recentFact);

    const durationFact = n('div', 'ops-history-fact');
    add(durationFact, [n('div', 'ops-history-fact-label', 'TYPICAL COMPLETION'), n('strong', 'ops-history-fact-value', duration(medianCompletion)), n('div', 'ops-history-fact-meta', 'p90 ' + duration(p90Completion) + ' across ' + integer(completionValues.length) + ' timed runs')]);
    snapshot.append(durationFact);
    section.append(snapshot);

    const detailGrid = n('div', 'ops-history-detail-grid');
    const timeline = n('section', 'ops-history-panel ops-history-timeline');
    const timelineHeader = n('header', 'ops-history-panel-header');
    const timelineHeading = n('div');
    add(timelineHeading, [n('h3', '', 'Outcome timeline'), n('p', '', integer(observations.length) + ' exact runs - oldest to newest')]);
    const legend = n('div', 'ops-history-legend');
    [['is-passed', 'Passed'], ['is-soft', 'Soft'], ['is-hard', 'Hard']].forEach(function (entry) {
      const item = n('span', 'ops-history-legend-item');
      add(item, [n('i', 'ops-run-cell ' + entry[0]), entry[1]]);
      legend.append(item);
    });
    add(timelineHeader, [timelineHeading, legend]);
    const overview = n('div', 'ops-history-overview');
    overview.style.setProperty('--ops-history-track-width', Math.max(680, observations.length * 13) + 'px');
    const track = n('div', 'ops-history-track');
    track.style.gridTemplateColumns = 'repeat(' + observations.length + ', minmax(10px, 1fr))';
    observations.forEach(function (observation) { track.append(historyRunCell(observation, 'ci')); });
    const axis = n('div', 'ops-history-track-axis');
    const axisIndexes = Array.from(new Set([0, Math.floor((observations.length - 1) / 2), observations.length - 1]));
    axisIndexes.forEach(function (index) {
      const observation = observations[index] || {};
      axis.append(n('span', 'ops-mono', observation.build_number ? '#' + observation.build_number : shortDate(observationTimestamp(observation))));
    });
    const overviewSummary = n('div', 'ops-history-track-summary');
    add(overviewSummary, [
      n('span', 'is-passed', integer(passed) + ' passed'),
      n('span', 'is-soft', integer(soft) + ' soft'),
      n('span', 'is-hard', integer(hard) + ' hard'),
      n('span', '', integer(streak) + '-run current passing streak'),
    ]);
    add(overview, [track, axis, overviewSummary]);
    add(timeline, [timelineHeader, overview]);

    const incidentPanel = n('section', 'ops-history-panel ops-history-incidents');
    const incidentHeader = n('header', 'ops-history-panel-header');
    const incidentHeading = n('div');
    add(incidentHeading, [n('h3', '', 'Incidents to inspect'), n('p', '', integer(incidents.length) + ' retained in this cohort')]);
    incidentHeader.append(incidentHeading);
    incidentPanel.append(incidentHeader);
    const incidentList = n('div', 'ops-incident-list');
    if (incidents.length) {
      incidents.slice().reverse().slice(0, 6).forEach(function (observation) { incidentList.append(historyIncidentRow(observation)); });
    } else {
      incidentList.append(n('div', 'ops-history-all-clear', 'No incident observations are retained for this cohort.'));
    }
    incidentPanel.append(incidentList);
    if (incidents.length) {
      const inspectAll = button('Inspect all ' + integer(incidents.length) + ' incidents', function () {
        openHistoryEvidence(
          selected.name + ' incidents',
          incidents.slice().reverse().map(function (observation) { return observationHistoryPoint(observation, 'ci'); }),
          integer(incidents.length) + ' exact incident observations in the selected cohort',
          SOURCE_ASSETS.operations
        );
      });
      const footer = n('footer', 'ops-history-panel-footer');
      footer.append(inspectAll);
      incidentPanel.append(footer);
    }
    add(detailGrid, [timeline, incidentPanel]);
    section.append(detailGrid);
    section.append(n('p', 'ops-evidence-method', 'The source retains up to 60 exact observations for each strict group. Empty positions in the complete map mean fewer retained runs, not inferred missing executions.'));
    host.append(section);
  }

  function amdHealthGroups(amdHealth) {
    return Array.isArray((amdHealth || {}).group_catalog) ? amdHealth.group_catalog : [];
  }

  function amdGroupObservations(row) {
    return Array.isArray((row || {}).observations) ? row.observations.slice().sort(function (a, b) {
      return new Date(observationTimestamp(a) || 0) - new Date(observationTimestamp(b) || 0);
    }) : [];
  }

  function amdGroupPassRate(row) {
    if (Number.isFinite(Number(row && row.pass_rate_pct))) return Number(row.pass_rate_pct);
    const known = Number((row || {}).passed || 0) + Number((row || {}).soft_failed || 0) + Number((row || {}).hard_failed || 0);
    return known ? Number((row || {}).passed || 0) / known * 100 : null;
  }

  function amdLatestState(row, latestBuild) {
    if (latestBuild && Number(row.latest_build_number) !== Number(latestBuild)) return 'missing';
    const result = String(row.latest_state || 'unknown').toLowerCase();
    if (['soft', 'soft_fail', 'soft_failed'].includes(result)) return 'soft';
    if (['hard', 'incident', 'error', 'failed', 'timed_out', 'broken', 'canceled'].includes(result)) return 'hard';
    return result === 'passed' ? 'passed' : 'unknown';
  }

  function amdStateLabel(result) {
    if (result === 'passed') return 'Passing';
    if (result === 'soft') return 'Soft fail';
    if (result === 'hard') return 'Hard fail';
    if (result === 'missing') return 'Not in latest';
    return 'No result';
  }

  function amdGroupIdentity(row, onOpen) {
    const wrap = n('div', 'ops-group-identity');
    const name = linkButton(row.display_name || row.name || row.job_name, onOpen, 'Inspect AMD nightly history for ' + value(row.job_name || row.name));
    name.classList.add('ops-cell-primary');
    add(wrap, [name, n('div', 'ops-group-identity-meta ops-mono', value(row.hardware_variant || row.hardware, 'unknown') + ' - ' + value(row.queue, 'queue unavailable'))]);
    return wrap;
  }

  function openAmdGroupDetail(row, amdHealth) {
    const observations = amdGroupObservations(row);
    const latestBuild = ((amdHealth || {}).summary || {}).latest_build_number;
    const latestState = amdLatestState(row, latestBuild);
    const incidents = observations.filter(isIncidentObservation);
    const content = n('div', 'ops-amd-group-detail');
    content.append(statusStrip([
      {label: 'LATEST AMD RESULT', value: amdStateLabel(latestState), meta: row.latest_build_number ? '#' + row.latest_build_number + ' - ' + shortDate(row.latest_observed_at) : 'No retained latest result', tone: toneForState(latestState)},
      {label: 'RETAINED PASS RATE', value: amdGroupPassRate(row) === null ? '-' : amdGroupPassRate(row).toFixed(1) + '%', meta: integer(row.passed) + ' passed - ' + integer(row.soft_failed) + ' soft - ' + integer(row.hard_failed) + ' hard'},
      {label: 'CURRENT PASS STREAK', value: integer(row.current_pass_streak), meta: integer(row.runs) + ' retained AMD nightlies'},
      {label: 'EXACT INCIDENTS', value: integer(incidents.length), meta: incidents.length ? 'Select any amber or red outcome for its Buildkite job' : 'None in retained history', tone: incidents.length ? 'is-warning' : 'is-success'},
    ]));

    const timeline = n('section', 'ops-history-panel ops-amd-history-timeline');
    const timelineHeader = n('header', 'ops-history-panel-header');
    const timelineHeading = n('div');
    add(timelineHeading, [n('h3', '', 'AMD nightly outcomes'), n('p', '', integer(observations.length) + ' exact job groups - oldest to newest')]);
    const legend = n('div', 'ops-history-legend');
    [['is-passed', 'Passed'], ['is-soft', 'Soft fail'], ['is-hard', 'Hard fail'], ['is-unknown', 'Unknown']].forEach(function (entry) {
      const item = n('span', 'ops-history-legend-item');
      add(item, [n('i', 'ops-run-cell ' + entry[0]), entry[1]]);
      legend.append(item);
    });
    add(timelineHeader, [timelineHeading, legend]);
    const batches = n('div', 'ops-history-batches');
    for (let index = 0; index < observations.length; index += 10) batches.append(historyBatch(observations.slice(index, index + 10), index, 'amd-ci'));
    add(timeline, [timelineHeader, batches]);
    content.append(timeline);

    if (incidents.length) {
      const incidentList = n('div', 'ops-incident-list ops-amd-incident-list');
      incidents.slice().reverse().forEach(function (observation) { incidentList.append(historyIncidentRow(observation, 'amd-ci')); });
      content.append(panel('Incident evidence', integer(incidents.length) + ' exact AMD jobs', incidentList));
    }
    content.append(sourceActions([
      {label: 'Open latest AMD job', url: exactPipelineEvidenceUrl(observations[observations.length - 1], 'amd-ci')},
      {label: 'Open AMD pipeline', url: SOURCE_ASSETS.amdPipeline},
      {label: 'Open published dashboard data', url: SOURCE_ASSETS.operations},
    ]));
    openOverlay(row.display_name || row.name || row.job_name, value(row.hardware_variant || row.hardware) + ' - ' + value(row.queue) + ' - exact AMD nightly evidence', content, true, 'amd-group-' + row.id);
  }

  function openAmdCatalog(title, subtitle, rows, amdHealth, initialFilter) {
    const latestBuild = ((amdHealth || {}).summary || {}).latest_build_number;
    const content = n('div', 'ops-reliability-browser ops-amd-browser');
    const toolbar = n('div', 'ops-toolbar');
    const search = n('input', 'ops-input');
    search.type = 'search';
    search.placeholder = 'Search AMD group, hardware, or queue';
    search.setAttribute('aria-label', 'Search AMD test groups');
    const resultFilter = n('select', 'ops-select');
    resultFilter.setAttribute('aria-label', 'Filter AMD test groups by health');
    [['all', 'All retained variants'], ['attention', 'Needs attention'], ['passing', 'Passing now'], ['incident', 'Incidents now'], ['missing', 'Historical only'], ['mixed', 'Mixed history']].forEach(function (pair) {
      const option = n('option', '', pair[1]);
      option.value = pair[0];
      option.selected = pair[0] === (initialFilter || 'all');
      resultFilter.append(option);
    });
    add(toolbar, [search, resultFilter]);
    content.append(toolbar);
    const tableHost = n('div', 'ops-evidence-table-host');
    content.append(tableHost);
    let page = 0;
    const pageSize = 50;
    const pager = n('div', 'ops-browser-pagination');
    const previous = button('Previous', function () { page -= 1; renderRows(); });
    const position = n('span', 'ops-browser-position');
    const next = button('Next', function () { page += 1; renderRows(); });
    add(pager, [previous, position, next]);
    content.append(pager);

    function renderRows() {
      const query = normalizeLabel(search.value);
      const mode = resultFilter.value;
      const filtered = rows.filter(function (row) {
        const latest = amdLatestState(row, latestBuild);
        const mixed = Number(row.passed || 0) > 0 && Number(row.soft_failed || 0) + Number(row.hard_failed || 0) > 0;
        if (mode === 'attention' && !['soft', 'hard', 'unknown'].includes(latest)) return false;
        if (mode === 'passing' && latest !== 'passed') return false;
        if (mode === 'incident' && !['soft', 'hard'].includes(latest)) return false;
        if (mode === 'missing' && latest !== 'missing') return false;
        if (mode === 'mixed' && !mixed) return false;
        if (!query) return true;
        return [row.display_name, row.name, row.job_name, row.hardware_variant, row.hardware, row.queue]
          .some(function (part) { return normalizeLabel(part).includes(query); });
      });
      const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
      page = Math.max(0, Math.min(page, pageCount - 1));
      const start = page * pageSize;
      const visible = filtered.slice(start, start + pageSize);
      clear(tableHost);
      tableHost.append(dataTable([
        {label: 'AMD job variant', sticky: true, width: '370px', render: function (row) { return amdGroupIdentity(row, function () { openAmdGroupDetail(row, amdHealth); }); }},
        {label: 'Latest', width: '130px', render: function (row) { const latest = amdLatestState(row, latestBuild); const url = latest === 'missing' ? '' : row.latest_url; return linkedBadge(amdStateLabel(latest), url, function () { openAmdGroupDetail(row, amdHealth); }, toneForState(latest)); }},
        {label: 'Pass rate', numeric: true, width: '120px', render: function (row) { const rate = amdGroupPassRate(row); return linkButton(rate === null ? '-' : rate.toFixed(1) + '%', function () { openAmdGroupDetail(row, amdHealth); }); }},
        {label: 'Runs', numeric: true, width: '90px', render: function (row) { return linkButton(integer(row.runs), function () { openAmdGroupDetail(row, amdHealth); }); }},
        {label: 'Pass streak', numeric: true, width: '120px', render: function (row) { return linkButton(integer(row.current_pass_streak), function () { openAmdGroupDetail(row, amdHealth); }); }},
        {label: 'Hardware', width: '120px', render: function (row) { return badge(value(row.hardware_variant || row.hardware), 'is-neutral'); }},
        {label: 'Queue', width: '170px', render: function (row) { return linkButton(value(row.queue), function () { navigateTo('ci-queue', {queueView: 'history', queueHistoryQueue: row.queue, queueScope: 'amd'}); }, 'Open queue history for ' + value(row.queue)); }},
        {label: 'Evidence', width: '150px', render: function (row) { return row.latest_url ? externalLink('Latest AMD job', row.latest_url) : linkButton('Inspect history', function () { openAmdGroupDetail(row, amdHealth); }); }},
      ], visible, integer(start + 1) + '-' + integer(start + visible.length) + ' of ' + integer(filtered.length) + ' matching AMD job variants', {name: 'amd-health-browser', minWidth: '1270px'}));
      position.textContent = 'Page ' + integer(page + 1) + ' of ' + integer(pageCount);
      previous.disabled = page === 0;
      next.disabled = page >= pageCount - 1;
      pager.hidden = filtered.length <= pageSize;
    }

    search.addEventListener('input', function () { page = 0; renderRows(); });
    resultFilter.addEventListener('change', function () { page = 0; renderRows(); });
    renderRows();
    openOverlay(title, subtitle, content, true, 'amd-health-browser-' + normalizeLabel(title));
    requestAnimationFrame(function () { search.focus(); });
  }

  function amdHardwareRows(groups, latestBuild) {
    const byHardware = new Map();
    groups.forEach(function (group) {
      const id = value(group.hardware_variant || group.hardware, 'unknown');
      if (!byHardware.has(id)) byHardware.set(id, {id: id, label: hardwareDisplayLabel(id), rows: [], passed: 0, soft: 0, hard: 0, missing: 0});
      const cluster = byHardware.get(id);
      const latest = amdLatestState(group, latestBuild);
      cluster.rows.push(group);
      if (latest === 'passed') cluster.passed += 1;
      else if (latest === 'soft') cluster.soft += 1;
      else if (latest === 'hard') cluster.hard += 1;
      else cluster.missing += 1;
    });
    return Array.from(byHardware.values()).sort(function (a, b) {
      return (b.passed + b.soft + b.hard) - (a.passed + a.soft + a.hard) || a.label.localeCompare(b.label);
    });
  }

  function amdHealthCluster(label, count, description, meta, tone, onOpen) {
    const tile = n('button', 'ops-cluster-tile ' + (tone || ''));
    tile.type = 'button';
    tile.setAttribute('aria-label', 'Open ' + label + ': ' + integer(count) + ' AMD job variants');
    const head = n('div', 'ops-cluster-tile-head');
    add(head, [n('span', 'ops-cluster-label', label), n('span', 'ops-cluster-count', integer(count))]);
    add(tile, [head, n('div', 'ops-cluster-description', description), n('div', 'ops-cluster-meta', meta)]);
    tile.addEventListener('click', onOpen);
    return tile;
  }

  function renderAmdHealth(host, amdHealth) {
    const summary = amdHealth.summary || {};
    const groups = amdHealthGroups(amdHealth);
    const builds = Array.isArray(amdHealth.builds) ? amdHealth.builds : [];
    if (amdHealth.available !== true || !groups.length) {
      host.append(n('div', 'ops-evidence-note is-warning', 'AMD test-group history is unavailable. No upstream result has been substituted for AMD health.'));
      return;
    }
    const latestBuild = summary.latest_build_number;
    const latestCounts = summary.latest_state_counts || {};
    const passing = Number(latestCounts.passed || 0);
    const soft = Number(latestCounts.soft || latestCounts.soft_failed || 0);
    const hard = Number(latestCounts.hard || latestCounts.hard_failed || 0);
    const incidents = soft + hard;
    const unknown = Number(latestCounts.unknown || 0);
    const retainedCount = Number(summary.retained_group_count || summary.union_group_count || summary.group_count || groups.length);
    const notLatest = Math.max(0, retainedCount - Number(summary.latest_group_count || 0));
    const mixed = groups.filter(function (row) { return Number(row.passed || 0) > 0 && Number(row.soft_failed || 0) + Number(row.hard_failed || 0) > 0; });
    const currentIncidents = groups.filter(function (row) { return ['soft', 'hard'].includes(amdLatestState(row, latestBuild)); });
    const currentPassing = groups.filter(function (row) { return amdLatestState(row, latestBuild) === 'passed'; });
    const currentUnknown = groups.filter(function (row) { return amdLatestState(row, latestBuild) === 'unknown'; });
    const currentVariants = currentPassing.concat(currentIncidents, currentUnknown);
    const missing = groups.filter(function (row) { return amdLatestState(row, latestBuild) === 'missing'; });

    host.append(statusStrip([
      {id: 'amd-health-build', label: 'LATEST AMD NIGHTLY', value: latestBuild ? '#' + latestBuild : '-', meta: shortDate(summary.latest_observed_at), tone: hard ? 'is-danger' : soft ? 'is-warning' : 'is-success', url: summary.latest_build_url},
      {id: 'amd-health-observed', label: 'LATEST JOB VARIANTS', value: integer(summary.latest_group_count), meta: 'exact jobs in the latest nightly' + (notLatest ? '; ' + integer(notLatest) + ' older variants retained only for history' : ''), onOpen: function () { openAmdCatalog('Latest AMD job variants', 'Exact Buildkite job variants observed in the latest AMD nightly', currentVariants, amdHealth, 'all'); }},
      {id: 'amd-health-passing', label: 'PASSING NOW', value: integer(passing), meta: percent(passing, passing + incidents + unknown) + ' of observed groups', tone: passing ? 'is-success' : 'is-neutral', onOpen: function () { openAmdCatalog('AMD groups passing now', 'Latest exact AMD nightly outcomes', currentPassing, amdHealth, 'passing'); }},
      {id: 'amd-health-incidents', label: 'INCIDENTS NOW', value: integer(incidents), meta: integer(soft) + ' soft - ' + integer(hard) + ' hard' + (unknown ? ' - ' + integer(unknown) + ' unknown' : ''), tone: hard ? 'is-danger' : soft ? 'is-warning' : 'is-success', onOpen: function () { openAmdCatalog('Current AMD incidents', 'Exact groups with a soft or hard result in the latest AMD nightly', currentIncidents, amdHealth, 'incident'); }},
    ]));
    const note = n('div', 'ops-evidence-note is-info');
    add(note, [n('strong', '', 'AMD nightly test health. '), n('span', '', 'Each row is one exact AMD Buildkite job variant. The latest count is current-only; older names remain available as history and are not treated as missing incidents. Upstream results are not used as AMD passes.')]);
    host.append(note);

    const hardware = amdHardwareRows(groups, latestBuild);
    const chartsGrid = n('div', 'ops-grid ops-grid-2 ops-amd-health-charts');
    const buildChart = chartPanel('AMD health by nightly', 'Passing, soft-failing, hard-failing, and unknown groups in each retained AMD build', 'analytics-amd-build-health');
    const hardwareChart = chartPanel('Latest health by hardware variant', 'Current outcomes for each MI250, MI300, MI325, and MI355 execution variant', 'analytics-amd-hardware-health');
    add(chartsGrid, [buildChart.root, hardwareChart.root]);
    host.append(chartsGrid);
    requestAnimationFrame(function () {
      drawChart('analytics-amd-build-health', buildChart.canvas, {
        type: 'bar',
        data: {labels: builds.map(function (build) { return '#' + build.number; }), datasets: [
          {label: 'Passing', data: builds.map(function (build) { return build.passed; }), backgroundColor: '#35bb78'},
          {label: 'Soft fail', data: builds.map(function (build) { return build.soft_failed; }), backgroundColor: '#e3a63a'},
          {label: 'Hard fail', data: builds.map(function (build) { return build.hard_failed; }), backgroundColor: '#e06464'},
          {label: 'Unknown', data: builds.map(function (build) { return build.unknown; }), backgroundColor: '#66717d'},
        ]},
        options: {scales: {x: {stacked: true, grid: {display: false}, ticks: {maxTicksLimit: 10}}, y: {stacked: true, beginAtZero: true, title: {display: true, text: 'AMD job groups'}}}},
        evidenceTitle: 'AMD nightly test-group health',
        evidence: builds.map(function (build) { return {label: '#' + build.number, timestamp: build.observed_at, url: build.url, valueSummary: integer(build.passed) + ' passing - ' + integer(build.soft_failed) + ' soft - ' + integer(build.hard_failed) + ' hard', details: {observed_groups: build.observed, passing: build.passed, soft_failed: build.soft_failed, hard_failed: build.hard_failed, unknown: build.unknown, pass_rate: Number(build.pass_rate_pct || 0).toFixed(1) + '%'}}; }),
      });
      drawChart('analytics-amd-hardware-health', hardwareChart.canvas, {
        type: 'bar',
        data: {labels: hardware.map(function (row) { return row.label; }), datasets: [
          {label: 'Passing', data: hardware.map(function (row) { return row.passed; }), backgroundColor: '#35bb78'},
          {label: 'Soft fail', data: hardware.map(function (row) { return row.soft; }), backgroundColor: '#e3a63a'},
          {label: 'Hard fail', data: hardware.map(function (row) { return row.hard; }), backgroundColor: '#e06464'},
          {label: 'Not in latest', data: hardware.map(function (row) { return row.missing; }), backgroundColor: '#66717d'},
        ]},
        options: {indexAxis: 'y', scales: {x: {stacked: true, beginAtZero: true, title: {display: true, text: 'AMD job groups'}}, y: {stacked: true, grid: {display: false}}}},
        evidenceTitle: 'Latest AMD health by hardware variant',
        evidence: hardware.map(function (row) { return {label: row.label, valueSummary: integer(row.passed) + ' passing - ' + integer(row.soft) + ' soft - ' + integer(row.hard) + ' hard - ' + integer(row.missing) + ' not in latest', sources: [{label: 'Open published AMD health data', url: SOURCE_ASSETS.operations}], onOpen: function () { openAmdCatalog(row.label + ' AMD test groups', 'Exact groups assigned to ' + row.label, row.rows, amdHealth, 'all'); }}; }),
      });
    });

    const clusterSection = n('section', 'ops-cluster-section ops-amd-summary');
    const clusterHeader = n('header', 'ops-section-header');
    const clusterHeading = n('div', 'ops-section-heading');
    add(clusterHeading, [n('h2', 'ops-section-title', 'Retained AMD job-variant catalog'), n('p', 'ops-section-description', 'Start with the latest signal, then inspect exact nightly evidence for current or historical variants.')]);
    add(clusterHeader, [clusterHeading, button('Browse all ' + integer(groups.length) + ' retained variants', function () { openAmdCatalog('Retained AMD job variants', 'Search every exact AMD job variant retained across the nightly history window', groups, amdHealth, 'all'); })]);
    clusterSection.append(clusterHeader);
    const clusterGrid = n('div', 'ops-cluster-grid ops-amd-cluster-grid');
    clusterGrid.append(amdHealthCluster('Needs attention', currentIncidents.length + currentUnknown.length, 'Soft, hard, or unresolved result in the latest nightly', integer(soft) + ' soft - ' + integer(hard) + ' hard - ' + integer(currentUnknown.length) + ' unresolved', hard ? 'is-danger' : 'is-warning', function () { openAmdCatalog('AMD variants needing attention', 'Current soft, hard, and unresolved outcomes only; historical-only names are excluded', currentVariants, amdHealth, 'attention'); }));
    clusterGrid.append(amdHealthCluster('Passing now', currentPassing.length, 'Latest exact AMD job result passed', percent(currentPassing.length, summary.latest_group_count) + ' of latest build', 'is-success', function () { openAmdCatalog('AMD groups passing now', 'Latest exact AMD nightly outcomes', currentPassing, amdHealth, 'passing'); }));
    clusterGrid.append(amdHealthCluster('Mixed history', mixed.length, 'Both passing and incident nightlies retained', integer(summary.build_count) + ' nightlies retained', 'is-warning', function () { openAmdCatalog('AMD groups with mixed history', 'Groups that passed on some AMD nightlies and had incidents on others', mixed, amdHealth, 'mixed'); }));
    clusterGrid.append(amdHealthCluster('Historical only', missing.length, 'Older job names not present in the latest nightly', 'Not classified as current incidents', 'is-neutral', function () { openAmdCatalog('Historical AMD job variants', 'Retained older names that are not part of the latest nightly observation set', missing, amdHealth, 'missing'); }));
    clusterSection.append(clusterGrid);
    host.append(clusterSection);

    const priority = currentIncidents.slice().sort(function (a, b) {
      const stateDelta = (amdLatestState(a, latestBuild) === 'hard' ? 0 : 1) - (amdLatestState(b, latestBuild) === 'hard' ? 0 : 1);
      return stateDelta || Number(amdGroupPassRate(a) || 0) - Number(amdGroupPassRate(b) || 0) || String(a.display_name || a.name).localeCompare(String(b.display_name || b.name));
    }).slice(0, 10);
    host.append(panel('Current AMD incidents to inspect', integer(priority.length) + ' highest-priority groups shown; every row opens exact nightly evidence', dataTable([
      {label: 'AMD test group', sticky: true, width: '380px', render: function (row) { return amdGroupIdentity(row, function () { openAmdGroupDetail(row, amdHealth); }); }},
      {label: 'Queue', width: '170px', render: function (row) { return linkButton(value(row.queue), function () { navigateTo('ci-queue', {queueView: 'history', queueHistoryQueue: row.queue, queueScope: 'amd'}); }); }},
      {label: 'Retained pass rate', numeric: true, width: '150px', render: function (row) { const rate = amdGroupPassRate(row); return linkButton(rate === null ? '-' : rate.toFixed(1) + '%', function () { openAmdGroupDetail(row, amdHealth); }); }},
      {label: 'Latest', width: '110px', render: function (row) { const latest = amdLatestState(row, latestBuild); return linkedBadge(amdStateLabel(latest), row.latest_url, function () { openAmdGroupDetail(row, amdHealth); }, toneForState(latest)); }},
      {label: 'Pass / soft / hard', numeric: true, width: '170px', render: function (row) { return linkButton(integer(row.passed) + ' / ' + integer(row.soft_failed) + ' / ' + integer(row.hard_failed), function () { openAmdGroupDetail(row, amdHealth); }); }},
      {label: 'Latest evidence', width: '160px', render: function (row) { return externalLink('#' + value(row.latest_build_number), row.latest_url, 'ops-mono'); }},
    ], priority, integer(currentIncidents.length) + ' current AMD incident groups; use Browse all for the complete catalog', {name: 'amd-current-incidents', minWidth: '1020px'}), 'ops-amd-priority'));
  }

  const AGENT_WINDOW_DAYS = {'1d': 1, '3d': 3, '7d': 7, '14d': 14, '30d': 30, '60d': 60};

  function agentStateColor(stateName) {
    const s = String(stateName || '').toLowerCase();
    if (s === 'pass' || s === 'passed') return '#35bb78';
    if (s === 'soft') return '#e3a63a';
    if (s === 'hard') return '#e06464';
    if (s === 'canceled') return '#8a93a0';
    return '#66717d';
  }

  function agentTruncate(text, max) {
    const s = String(text || '');
    return s.length > max ? s.slice(0, max - 1) + '…' : s;
  }

  // Mirror of collect_agent_health node labelling: append the GPU type to every
  // node name (gpu9124 -> "gpu9124 (MI300)").
  function agentNodeLabel(raw, hardware) {
    if (!raw || !hardware) return raw;
    return raw + ' (' + hardware + ')';
  }

  function agentCofailLabel(mins) {
    const m = Number(mins) || 0;
    return m % 60 === 0 ? (m / 60) + 'h' : m + 'm';
  }

  function buildkiteJobUrl(pipeline, build, jobId) {
    if (!pipeline || build === null || build === undefined) return '';
    let url = 'https://buildkite.com/vllm/' + pipeline + '/builds/' + build;
    if (jobId) url += '/steps/canvas?jid=' + jobId + '&tab=output';
    return url;
  }

  // Port of build_operations_snapshot's co-failure clustering, moved client-side
  // so the window is a live toggle. `runs` are failing runs (active signal) on ONE
  // node, each carrying {group,pipeline,state,build_number,url,started_at,_start,_end}.
  // A cluster is a run of consecutive failures whose gaps stay within `windowMins`.
  // Retries of the same test group within the same build collapse to one logical
  // failure (same node is implied, since clustering is per node); a cluster becomes
  // an event with >=2 such distinct failures — they need NOT be different groups.
  function clusterNodeCofailures(node, nodeRaw, hardware, runs, windowMins) {
    const failing = runs.filter(function (r) { return r._start !== null; })
      .slice().sort(function (a, b) { return a._start - b._start; });
    const windowMs = windowMins * 60000;
    const events = [];
    let cluster = [];
    let clusterEnd = null;
    function flush() {
      // Dedupe retries of the same (pipeline, build, group) — a job retried within
      // one build is a single logical failure, not a co-failure — keeping the last
      // attempt. An event needs >=2 of these distinct failures.
      const byKey = new Map();
      cluster.forEach(function (r) {
        const key = r.pipeline + '\u001f' + r.build_number + '\u001f' + r.group;
        const prev = byKey.get(key);
        if (!prev || r._start > prev._start) byKey.set(key, r);
      });
      const distinct = Array.from(byKey.values());
      if (distinct.length >= 2) events.push(makeCofailEvent(node, nodeRaw, hardware, distinct));
      cluster = [];
    }
    failing.forEach(function (r) {
      const start = r._start;
      const end = r._end !== null ? r._end : start;
      if (clusterEnd !== null && (start - clusterEnd) > windowMs) { flush(); clusterEnd = null; }
      cluster.push(r);
      clusterEnd = clusterEnd === null ? end : Math.max(clusterEnd, end);
    });
    flush();
    return events;
  }

  function makeCofailEvent(node, nodeRaw, hardware, cluster) {
    const starts = cluster.map(function (r) { return r._start; });
    const ends = cluster.map(function (r) { return r._end !== null ? r._end : r._start; });
    const startMs = Math.min.apply(null, starts);
    const endMs = Math.max.apply(null, ends);
    // concurrent := any two run intervals overlap (contention / ephemeral fault);
    // otherwise back-to-back failures suggesting an unclean node state.
    const intervals = cluster.map(function (r) { return [r._start, r._end !== null ? r._end : r._start]; })
      .sort(function (a, b) { return a[0] - b[0]; });
    let concurrent = false;
    for (let i = 1; i < intervals.length; i++) { if (intervals[i][0] < intervals[i - 1][1]) { concurrent = true; break; } }
    const pipelines = Array.from(new Set(cluster.map(function (r) { return r.pipeline; }))).sort();
    return {
      node: node,
      node_raw: nodeRaw,
      hardware: hardware,
      pattern: concurrent ? 'concurrent' : 'sequential',
      concurrent: concurrent,
      started_at: new Date(startMs).toISOString(),
      _startMs: startMs,
      _endMs: endMs,
      span_mins: Math.round((endMs - startMs) / 60000 * 100) / 100,
      run_count: cluster.length,
      group_count: new Set(cluster.map(function (r) { return r.group; })).size,
      cross_pipeline: pipelines.length > 1,
      pipelines: pipelines,
      hard_failed: cluster.filter(function (r) { return r.state === 'hard'; }).length,
      soft_failed: cluster.filter(function (r) { return r.state === 'soft'; }).length,
      runs: cluster.slice().sort(function (a, b) { return a._start - b._start; }),
    };
  }

  // Draw the per-node timeline. `range` (optional {min,max} in ms) zooms the time
  // axis: only runs overlapping the window are shown, at a finer x scale. Returns
  // the full (unzoomed) data bounds {min,max} so the caller can drive zoom/reset.
  function drawAgentTimeline(nodeRuns, label, chart, range) {
    const allRows = (nodeRuns || []).slice()
      .filter(function (run) { return run._start !== null; })
      .map(function (run) {
        const start = run._start;
        const end = run._end !== null && run._end > start ? run._end : start + 60000;
        return {run: run, start: start, end: end, mins: (end - start) / 60000};
      })
      .sort(function (a, b) { return a.start - b.start; });
    if (!allRows.length) {
      chart.frame.style.setProperty('--ops-chart-height', '180px');
      chart.viewport.style.minWidth = '';
      drawChart('analytics-agent-timeline', chart.canvas, {type: 'bar', data: {labels: [], datasets: [{data: []}]}, options: {plugins: {legend: {display: false}}}});
      return null;
    }
    const dataMin = Math.min.apply(null, allRows.map(function (row) { return row.start; }));
    const dataMax = Math.max.apply(null, allRows.map(function (row) { return row.end; }));
    // Rows to show + x window: a zoom range clips to overlapping runs; otherwise
    // show everything padded.
    const rows = range
      ? allRows.filter(function (row) { return row.end >= range.min && row.start <= range.max; })
      : allRows;
    const pad = Math.max(60000, (dataMax - dataMin) * 0.02);
    const xMin = range ? range.min : dataMin - pad;
    const xMax = range ? range.max : dataMax + pad;
    // ~26px of vertical room per shown run; the stage caps visible height and scrolls.
    chart.frame.style.setProperty('--ops-chart-height', Math.max(180, rows.length * 26) + 'px');
    const spanDays = (xMax - xMin) / 86400000;
    chart.viewport.style.minWidth = Math.min(2600, Math.max(720, Math.round(spanDays * 90))) + 'px';
    drawChart('analytics-agent-timeline', chart.canvas, {
      type: 'bar',
      data: {
        labels: rows.map(function (row, index) { return (index + 1) + '. ' + agentTruncate(row.run.group, 34); }),
        datasets: [{
          label: 'Failing run window',
          data: rows.map(function (row) { return [row.start, row.end]; }),
          backgroundColor: rows.map(function (row) { return agentStateColor(row.run.state); }),
          borderWidth: 0,
          borderSkipped: false,
          barPercentage: 0.82,
          categoryPercentage: 0.92,
        }],
      },
      options: {
        indexAxis: 'y',
        scales: {
          x: {type: 'linear', min: xMin, max: xMax, title: {display: true, text: 'Time'}, ticks: {maxTicksLimit: 8, callback: function (v) { return shortDate(new Date(Number(v)).toISOString()); }}},
          y: {grid: {display: false}, ticks: {autoSkip: false, font: {size: 10}}},
        },
        plugins: {
          legend: {display: false},
          tooltip: {callbacks: {
            title: function (items) { return rows[items[0].dataIndex].run.group; },
            label: function (item) {
              const run = rows[item.dataIndex].run;
              return ['State: ' + run.state, 'Pipeline: ' + run.pipeline, 'Queue: ' + (run.queue || '-'), 'Start: ' + shortDate(run.started_at), 'Duration: ' + duration(rows[item.dataIndex].mins), 'Click to open the BuildKite log'];
            },
          }},
        },
      },
      evidenceTitle: 'Infra-suspect failing runs on ' + label,
      evidenceAction: false,
      evidence: rows.map(function (row) {
        const run = row.run;
        const openJob = run.url ? function () { window.open(run.url, '_blank', 'noopener'); } : null;
        return {label: run.group, url: run.url, onOpen: openJob, timestamp: run.started_at, valueSummary: run.state + ' - ' + duration(row.mins), details: {pipeline: run.pipeline, queue: run.queue, state: run.state, build: '#' + value(run.build_number)}};
      }),
    });
    return {min: dataMin, max: dataMax};
  }

  // Runs shown on a compact (unselected) co-failure card before the overflow hint.
  const COFAIL_COMPACT_RUNS = 3;

  function agentCofailureCard(event, onSelectEvent, selected) {
    const card = n('div', 'ops-cofailure-card' + (event.cross_pipeline ? ' is-cross' : '') + (selected ? ' is-selected' : ''));
    // Clicking anywhere on the card (but not a link/button inside it) selects the
    // event, which inflates + highlights it and zooms the timeline to it.
    card.addEventListener('click', function (e) {
      if (e.target && e.target.closest && e.target.closest('a, button')) return;
      onSelectEvent(event);
    });
    const head = n('div', 'ops-cofailure-head');
    const nodeButton = linkButton(value(event.node), function () { onSelectEvent(event); }, 'Zoom the timeline to this co-failure event and expand it');
    nodeButton.classList.add('ops-mono', 'ops-cofailure-node');
    add(head, [
      nodeButton,
      n('span', 'ops-cofailure-meta', shortDate(event.started_at) + ' · ' + duration(event.span_mins) + ' span · ' + integer(event.group_count) + ' groups'),
    ]);
    const concurrent = event.pattern === 'concurrent' || event.concurrent;
    head.append(n('span', 'ops-badge ' + (concurrent ? 'is-warning' : 'is-info'), concurrent ? 'concurrent' : 'sequential'));
    if (event.cross_pipeline) head.append(n('span', 'ops-badge is-danger', 'cross-pipeline'));
    card.append(head);
    if (selected) {
      // Finer-grained event summary, shown only on the inflated (selected) card.
      card.append(n('p', 'ops-cofailure-detail',
        integer(event.run_count) + ' runs · ' + integer(event.hard_failed) + ' hard · '
        + integer(event.soft_failed) + ' soft · pipelines: ' + (event.pipelines || []).join(', ')));
    }
    const allRuns = event.runs || [];
    // Compact cards show only the first few runs so a busy cluster can't sprawl;
    // the selected card inflates to the full list with per-run timing.
    const shown = selected ? allRuns : allRuns.slice(0, COFAIL_COMPACT_RUNS);
    const list = n('ul', 'ops-cofailure-runs');
    shown.forEach(function (run) {
      const li = n('li', 'ops-cofailure-run');
      const chip = n('span', 'ops-state-chip');
      chip.style.background = agentStateColor(run.state);
      chip.title = run.state;
      const cells = [
        chip,
        n('span', 'ops-cofailure-group', run.group),
        n('span', 'ops-cofailure-run-meta', [run.pipeline, run.queue].filter(Boolean).join(' · ')),
      ];
      if (selected) {
        const mins = run._end !== null && run._start !== null ? (run._end - run._start) / 60000 : null;
        cells.push(n('span', 'ops-cofailure-run-time', shortDate(run.started_at) + ' · ' + run.state + ' · ' + duration(mins)));
      }
      cells.push(externalLink('#' + value(run.build_number) + ' log ↗', run.url, 'ops-mono'));
      add(li, cells);
      list.append(li);
    });
    card.append(list);
    if (!selected && allRuns.length > shown.length) {
      const more = n('button', 'ops-cofailure-more', '+' + integer(allRuns.length - shown.length) + ' more · select to expand');
      more.type = 'button';
      // Expand in place: no timeline scroll (unlike selecting the card body).
      more.addEventListener('click', function (e) { e.stopPropagation(); onSelectEvent(event, false); });
      card.append(more);
    }
    return card;
  }

  function renderAmdAgentHealth(host, agentHealth) {
    const nodeDays = Array.isArray(agentHealth.node_days) ? agentHealth.node_days : [];
    const failingRaw = Array.isArray(agentHealth.failing_runs) ? agentHealth.failing_runs : [];
    const hardwareTypes = Array.isArray(agentHealth.hardware_types) ? agentHealth.hardware_types : [];
    const windowOptions = Array.isArray(agentHealth.window_options) ? agentHealth.window_options : [1, 3, 7, 14, 30, 60];
    const cofailOptions = Array.isArray(agentHealth.cofailure_window_options) ? agentHealth.cofailure_window_options : [30, 60, 120, 180, 360, 720, 1440];
    const endMs = new Date(agentHealth.generated_at || Date.now()).getTime();

    // Pre-parse failing runs once (timestamps + reconstructed url). Each row's
    // infra_suspect flag lets the signal toggle select the infra-only subset.
    const failing = failingRaw.map(function (r) {
      const start = Date.parse(r.t);
      const end = Date.parse(r.e);
      return {
        node_raw: r.nd,
        hardware: r.h,
        pipeline: r.p,
        queue: r.q,
        group: r.g,
        state: r.s,
        nightly: !!r.ng,
        infra_suspect: !!r.i,
        build_canceled: !!r.bc,
        build_number: r.b,
        url: buildkiteJobUrl(r.p, r.b, r.j),
        started_at: r.t,
        _start: Number.isFinite(start) ? start : null,
        _end: Number.isFinite(end) ? end : null,
      };
    });

    // Local, in-place view state (avoids full-tab re-render so the search box
    // keeps focus and the timeline chart does not flicker on every keystroke).
    let windowId = AGENT_WINDOW_DAYS[state.agentWindow] ? state.agentWindow : ((agentHealth.default_window_days || 7) + 'd');
    let gpu = (function (g) { return (g === 'all' || hardwareTypes.includes(g)) ? g : 'all'; })(state.agentGpu || 'all');
    let search = '';
    let selectedNode = state.agentNode || '';
    let cofailMins = (function (m) { return cofailOptions.includes(m) ? m : (agentHealth.default_cofailure_window_mins || 180); })(parseInt(state.agentCofail, 10));
    let excludeCancelled = state.agentExclCancel !== '0';
    let nightlyOnly = state.agentNightly === '1';
    // Failure signal: which failing runs drive the timeline + co-failure views.
    //   'infra' = anomalous, node-attributable subset (default)
    //   'hard'  = every hard failure   'all' = hard + soft
    let signal = ['infra', 'hard', 'all'].includes(state.agentSignal) ? state.agentSignal : 'infra';
    // Until the user clicks a column, the sort follows the active signal
    // (see defaultSortKey); after an explicit click we respect their choice.
    let sort = {key: 'infra_suspect', dir: 'desc'};
    let sortExplicit = false;
    let searchTimer = null;
    let current = null;
    // Timeline zoom: timelineRange overrides the x window ({min,max} ms), null =
    // full span. timelineBounds is the last drawn full data span (for reset/zoom).
    let timelineRange = null;
    let timelineBounds = null;
    // Co-failure events filter: when set, the events list is scoped to one node.
    let eventNodeFilter = '';
    // The co-failure card the user selected directly: inflate + highlight it and
    // keep every other card compact so the events list never dominates the page.
    // The key is stable across re-renders (node + cluster start time).
    let selectedEventKey = '';
    function eventKey(e) { return e.node_raw + '@' + e._startMs; }

    add(host, pageHeaderNote());
    // The infra-suspect criterion banner only applies to the default signal, so
    // it's hidden (in apply()) whenever the Failure signal is Hard/All failures.
    const criteriaNoteEl = criteriaNote();
    add(host, criteriaNoteEl);
    const modeHost = n('div', 'ops-agent-mode');
    host.append(modeHost);
    const controlsHost = n('div', 'ops-agent-controls');
    host.append(controlsHost);
    const kpiHost = n('div');
    host.append(kpiHost);
    const emptyHost = n('div');
    host.append(emptyHost);
    const tableHost = n('div');
    host.append(tableHost);

    // Timeline (persistent chart; only its data is redrawn).
    const timelineToolbar = n('div', 'ops-toolbar ops-agent-toolbar');
    const nodeField = n('label', 'ops-field-label', 'Timeline node ');
    const nodeSelect = n('select', 'ops-select');
    nodeSelect.setAttribute('aria-label', 'Physical node for run timeline');
    nodeField.append(nodeSelect);
    timelineToolbar.append(nodeField);
    // Horizontal zoom controls for the time axis.
    const zoomGroup = n('div', 'ops-agent-zoom');
    function zoomButton(label, title, handler) {
      const b = n('button', 'ops-zoom-btn', label);
      b.type = 'button';
      b.title = title;
      b.setAttribute('aria-label', title);
      b.addEventListener('click', handler);
      return b;
    }
    add(zoomGroup, [
      n('span', 'ops-field-label', 'Zoom '),
      zoomButton('−', 'Zoom out (widen the time window)', function () { zoomBy(1.6); }),
      zoomButton('+', 'Zoom in (narrow the time window)', function () { zoomBy(0.625); }),
      zoomButton('Reset', 'Reset the timeline to the full window', function () { resetZoom(); }),
      n('span', 'ops-zoom-hint', 'drag to select a range'),
    ]);
    timelineToolbar.append(zoomGroup);
    const timelineChart = chartPanel('Per-node failure timeline', 'Each bar is one failing run on the selected node (which failures are shown depends on the Failure signal toggle above); bars that overlap or cluster point to shared-node contention, an ephemeral host/network fault, or a node left in an unclean state. Click a bar to open its BuildKite log.', 'analytics-agent-timeline');
    const legend = n('div', 'ops-agent-legend');
    [['Soft fail', '#e3a63a'], ['Hard fail', '#e06464']].forEach(function (entry) {
      const item = n('span', 'ops-agent-legend-item');
      const swatch = n('span', 'ops-agent-legend-swatch');
      swatch.style.background = entry[1];
      add(item, [swatch, n('span', '', entry[0])]);
      legend.append(item);
    });
    const signalCaptionEl = n('p', 'ops-agent-signal-caption');
    const timelineSection = n('section', 'ops-agent-timeline-section');
    add(timelineSection, [timelineToolbar, signalCaptionEl, timelineChart.root, legend]);
    host.append(timelineSection);

    const eventsHost = n('div');
    host.append(eventsHost);

    nodeSelect.addEventListener('change', function () { selectNode(nodeSelect.value, false); });

    function pageHeaderNote() {
      const note = n('div', 'ops-evidence-note is-info');
      add(note, [n('strong', '', 'AMD physical CI agent health. '), n('span', '', 'Every build on AMD GPU hardware — all branches and PRs across the AMD nightly and upstream CI pipelines — is attributed to the physical node from its Buildkite agent tag. The timeline and co-failure clustering run on the Failure signal you pick below: "infra-suspect" (the default — anomalous, node-attributable failures, since most PR failures are code bugs), all hard failures, or all failures. Toggle the signal, build scope, cancelled-job handling, and co-failure window; click any node or event to load its timeline.')]);
      return note;
    }

    // Precise, self-contained definition of the anomaly criterion, so a reader
    // knows exactly what "infra-suspect" / anomalous means without guessing.
    function criteriaNote() {
      const rate = Number(agentHealth.infra_suspect_min_pass_rate);
      const pct = Number.isFinite(rate) ? Math.round(rate * 100) + '%' : '50%';
      const samples = Number(agentHealth.infra_suspect_min_samples) || 3;
      const note = n('div', 'ops-evidence-note is-neutral ops-agent-criteria');
      add(note, [
        n('strong', '', 'What counts as an anomalous (infra-suspect) failure? '),
        n('span', '', 'When the Failure signal is set to Infra-suspect (the default), a soft or hard failure is treated as node-attributable when its exact test group, on that same day, both: '),
      ]);
      const list = n('ol', 'ops-criteria-list');
      add(list, [
        n('li', '', 'passed at least ' + pct + ' of its gradable runs (needs ≥ ' + samples + ' graded samples), so the group demonstrably works; and'),
        n('li', '', 'passed on at least one other physical node that day, so the failure is isolated to this box rather than a broad code break.'),
      ]);
      note.append(list);
      note.append(n('span', 'ops-criteria-foot', 'Groups that fail broadly (real code bugs) and failures inside auto-cancelled / superseded builds are filtered out by default, isolating the signal that points at a specific machine.'));
      return note;
    }

    function agentField(labelText, control) {
      const field = n('div', 'ops-agent-field');
      add(field, [n('span', 'ops-field-label', labelText), control]);
      return field;
    }

    // Prominent, global signal switch. Sits above the finer filter row because it
    // changes what "failure" means across the timeline, co-failures and KPIs.
    function buildModeToggle() {
      clear(modeHost);
      const seg = segmented([
        {id: 'infra', label: 'Infra-suspect failures'},
        {id: 'hard', label: 'Hard failures'},
        {id: 'all', label: 'All failures (hard + soft)'},
      ], signal, function (id) {
        signal = id; state.agentSignal = id; setQueryValue('agent_signal', id);
        buildModeToggle(); apply();
      }, 'Failure signal');
      seg.classList.add('ops-agent-mode-seg');
      add(modeHost, [n('span', 'ops-agent-mode-label', 'Failure signal'), seg]);
    }

    function buildControls() {
      clear(controlsHost);
      const windowSeg = segmented(windowOptions.map(function (d) { return {id: d + 'd', label: d + 'd'}; }), windowId, function (id) {
        windowId = id; state.agentWindow = id; setQueryValue('agent_window', id); buildControls(); apply();
      }, 'Date range');
      const gpuItems = [{id: 'all', label: 'All GPUs'}].concat(hardwareTypes.map(function (h) { return {id: h, label: h}; }));
      const gpuSeg = segmented(gpuItems, gpu, function (id) {
        gpu = id; state.agentGpu = id; setQueryValue('agent_gpu', id); buildControls(); apply();
      }, 'GPU type');
      const nightlySeg = segmented([{id: '0', label: 'All builds'}, {id: '1', label: 'Nightly/main'}], nightlyOnly ? '1' : '0', function (id) {
        nightlyOnly = id === '1'; state.agentNightly = id; setQueryValue('agent_nightly', id); buildControls(); apply();
      }, 'Build scope');
      const cancelSeg = segmented([{id: '1', label: 'Exclude'}, {id: '0', label: 'Include'}], excludeCancelled ? '1' : '0', function (id) {
        excludeCancelled = id === '1'; state.agentExclCancel = id; setQueryValue('agent_excl_cancel', id); buildControls(); apply();
      }, 'Cancelled jobs');
      const cofailSeg = segmented(cofailOptions.map(function (m) { return {id: String(m), label: agentCofailLabel(m)}; }), String(cofailMins), function (id) {
        cofailMins = parseInt(id, 10); state.agentCofail = id; setQueryValue('agent_cofail', id); buildControls(); apply();
      }, 'Co-failure window');
      const searchInput = n('input', 'ops-input ops-agent-search');
      searchInput.type = 'search';
      searchInput.placeholder = 'Filter nodes by name or GPU type';
      searchInput.setAttribute('aria-label', 'Filter nodes by name or GPU type');
      searchInput.value = search;
      searchInput.addEventListener('input', function () {
        search = searchInput.value.trim();
        if (searchTimer) clearTimeout(searchTimer);
        searchTimer = setTimeout(apply, 200);
      });
      add(controlsHost, [
        agentField('Window', windowSeg),
        agentField('GPU', gpuSeg),
        agentField('Scope', nightlySeg),
        agentField('Cancelled', cancelSeg),
        agentField('Co-failure window', cofailSeg),
        agentField('Node', searchInput),
      ]);
    }

    function matchesFilter(hardware, nodeRaw) {
      const term = search.toLowerCase();
      if (gpu !== 'all' && hardware !== gpu) return false;
      if (term && String(nodeRaw || '').toLowerCase().indexOf(term) === -1 && String(hardware || '').toLowerCase().indexOf(term) === -1) return false;
      return true;
    }

    // Does a failing run belong to the active signal subset?
    function signalMatch(r) {
      if (signal === 'infra') return r.infra_suspect;
      if (signal === 'hard') return r.state === 'hard';
      return true; // 'all' — every shipped record is a hard/soft failure
    }
    // Short noun for the active signal, woven into headings/columns/copy.
    function signalTerm() {
      return signal === 'infra' ? 'infra-suspect' : signal === 'hard' ? 'hard' : 'all';
    }
    function signalColumnLabel() {
      return signal === 'infra' ? 'Infra-suspect failures' : 'Hard failures';
    }
    // Column the table sorts by when the user hasn't chosen one: the signal
    // column for Infra/Hard, and Test group failures for All (no signal column).
    function defaultSortKey() {
      return signal === 'all' ? 'failures' : 'infra_suspect';
    }
    function signalCaption() {
      if (signal === 'infra') return 'Showing infra-suspect failures — anomalous, node-attributable (a group that otherwise passes that day, including on another node).';
      if (signal === 'hard') return 'Showing every hard failure on the node — not just the infra-suspect subset.';
      return 'Showing all failures (hard + soft) on the node.';
    }

    function computeView() {
      const days = AGENT_WINDOW_DAYS[windowId] || 7;
      const startMs = endMs - days * 86400000;
      const startDay = new Date(startMs).toISOString().slice(0, 10);

      // Reliability rollups -> per-node aggregate over the window.
      const byNode = new Map();
      let totalRuns = 0;
      let identifiedRuns = 0;
      nodeDays.forEach(function (nd) {
        if (String(nd.d) < startDay) return;
        if (!matchesFilter(nd.h, nd.nd)) return;
        const bucket = nightlyOnly ? nd.n : nd.a;
        if (!bucket || !bucket[0]) return;
        // bucket = [runs, soft, hard, canceled]; tolerate the legacy 3-tuple
        // [runs, fail, canceled] (fail attributed to hard, soft unknown).
        const split = bucket.length >= 4;
        const soft = split ? (bucket[1] || 0) : 0;
        const hard = split ? (bucket[2] || 0) : (bucket[1] || 0);
        const canceled = split ? (bucket[3] || 0) : (bucket[2] || 0);
        totalRuns += bucket[0];
        if (nd.nd !== '(unidentified)') identifiedRuns += bucket[0];
        let agg = byNode.get(nd.nd);
        if (!agg) { agg = {node_raw: nd.nd, hardware: nd.h, runs: 0, soft: 0, hard: 0, canceled: 0}; byNode.set(nd.nd, agg); }
        agg.runs += bucket[0];
        agg.soft += soft;
        agg.hard += hard;
        agg.canceled += canceled;
        if (nd.h) agg.hardware = nd.h;
      });

      // Failing runs in the active signal subset -> co-failure clustering +
      // per-node counts. Exclude-cancelled drops failures whose parent build was
      // auto-canceled (superseded by a newer push) so they don't inflate the signal.
      const fRuns = failing.filter(function (r) {
        if (!signalMatch(r)) return false;
        if (r._start === null || r._start < startMs || r._start > endMs) return false;
        if (nightlyOnly && !r.nightly) return false;
        if (excludeCancelled && r.build_canceled) return false;
        return matchesFilter(r.hardware, r.node_raw);
      });
      const runsByNode = new Map();
      fRuns.forEach(function (r) { if (!runsByNode.has(r.node_raw)) runsByNode.set(r.node_raw, []); runsByNode.get(r.node_raw).push(r); });
      // Every hard/soft failing run for the node in-window regardless of signal,
      // with the SAME cancelled-build handling as fRuns. This backs the Failures
      // column so its hard/soft counts stay consistent with the signal column
      // (which is just the signal-matching subset of these same records): the
      // node_days rollup can't drop failures inside auto-cancelled builds, so
      // deriving Failures from the per-run records is what makes them agree.
      const failByNode = new Map();
      failing.forEach(function (r) {
        if (r._start === null || r._start < startMs || r._start > endMs) return;
        if (nightlyOnly && !r.nightly) return;
        if (excludeCancelled && r.build_canceled) return;
        if (!matchesFilter(r.hardware, r.node_raw)) return;
        let f = failByNode.get(r.node_raw);
        if (!f) { f = {hard: 0, soft: 0}; failByNode.set(r.node_raw, f); }
        if (r.state === 'hard') f.hard += 1; else if (r.state === 'soft') f.soft += 1;
      });
      const events = [];
      const signalByNode = {};
      const groupsByNode = {};
      runsByNode.forEach(function (runs, nodeRaw) {
        const hardware = (byNode.get(nodeRaw) || {}).hardware || (runs[0] && runs[0].hardware) || '';
        signalByNode[nodeRaw] = runs.length;
        groupsByNode[nodeRaw] = new Set(runs.map(function (x) { return x.group; })).size;
        clusterNodeCofailures(agentNodeLabel(nodeRaw, hardware), nodeRaw, hardware, runs, cofailMins).forEach(function (e) { events.push(e); });
      });
      const eventsByNode = {};
      events.forEach(function (e) { eventsByNode[e.node_raw] = (eventsByNode[e.node_raw] || 0) + 1; });

      const agents = [];
      byNode.forEach(function (agg, nodeRaw) {
        const identified = nodeRaw !== '(unidentified)';
        // Failure counts come from the per-run failing records (so the cancelled-
        // build toggle applies and they match the signal column); the run total /
        // denominator still comes from the node_days rollup (its only source).
        const f = failByNode.get(nodeRaw) || {hard: 0, soft: 0};
        const failures = f.hard + f.soft;
        const denom = excludeCancelled ? Math.max(0, agg.runs - agg.canceled) : agg.runs;
        agents.push({
          node: identified ? agentNodeLabel(nodeRaw, agg.hardware) : nodeRaw,
          node_raw: nodeRaw,
          hardware: agg.hardware,
          identified: identified,
          runs: denom,
          failures: failures,
          soft: f.soft,
          hard: f.hard,
          canceled: agg.canceled,
          incident_rate: denom ? failures / denom : 0,
          infra_suspect: signalByNode[nodeRaw] || 0,
          distinct_groups: groupsByNode[nodeRaw] || 0,
          cofailure_event_count: eventsByNode[nodeRaw] || 0,
          _runs: runsByNode.get(nodeRaw) || [],
        });
      });

      events.sort(function (a, b) {
        return (b.hard_failed - a.hard_failed)
          || (Number(b.cross_pipeline) - Number(a.cross_pipeline))
          || (b.group_count - a.group_count)
          || String(b.started_at).localeCompare(String(a.started_at));
      });
      return {agents: agents, events: events, fRuns: fRuns, totalRuns: totalRuns, identifiedRuns: identifiedRuns};
    }

    function renderKpis(view) {
      clear(kpiHost);
      const identifiedNodes = view.agents.filter(function (a) { return a.identified; }).length;
      // Count nodes carrying at least one failure under the active signal
      // (a.infra_suspect holds the signal-matched count) so this KPI tracks the
      // Failure signal toggle rather than the raw all-builds soft/hard total.
      const unreliable = view.agents.filter(function (a) { return a.identified && a.infra_suspect > 0; }).length;
      const unreliableNoun = signal === 'infra' ? 'an infra-suspect' : signal === 'hard' ? 'a hard' : 'a hard/soft';
      const coveragePct = view.totalRuns ? (100 * view.identifiedRuns / view.totalRuns) : 0;
      const concurrent = view.events.filter(function (e) { return e.concurrent; }).length;
      const cross = view.events.filter(function (e) { return e.cross_pipeline; }).length;
      kpiHost.append(statusStrip([
        {id: 'agent-nodes', label: 'IDENTIFIED AMD NODES', value: integer(identifiedNodes), meta: integer(view.totalRuns) + ' runs in ' + windowId, tone: 'is-info'},
        {id: 'agent-unreliable', label: 'NODES WITH FAILURES', value: integer(unreliable), meta: 'nodes with ' + unreliableNoun + ' failure', tone: unreliable ? 'is-warning' : 'is-success'},
        {id: 'agent-coverage', label: 'NODE COVERAGE', value: coveragePct.toFixed(1) + '%', meta: integer(view.identifiedRuns) + ' / ' + integer(view.totalRuns) + ' runs identified', tone: coveragePct >= 50 ? 'is-success' : coveragePct > 0 ? 'is-warning' : 'is-danger'},
        {id: 'agent-cofail', label: 'CO-FAILURE EVENTS', value: integer(view.events.length), meta: integer(concurrent) + ' concurrent · ' + integer(cross) + ' cross-pipeline', tone: view.events.length ? 'is-danger' : 'is-success', onOpen: function () { eventsHost.scrollIntoView({behavior: 'smooth', block: 'start'}); }},
      ]));
    }

    function sortedAgents(view) {
      const dir = sort.dir === 'asc' ? 1 : -1;
      return view.agents.slice().sort(function (a, b) {
        let x = a[sort.key];
        let y = b[sort.key];
        if (typeof x === 'number' || typeof y === 'number') return ((Number(x) || 0) - (Number(y) || 0)) * dir;
        x = String(x || '').toLowerCase();
        y = String(y || '').toLowerCase();
        return x < y ? -dir : x > y ? dir : 0;
      });
    }

    function onSort(key) {
      sortExplicit = true;
      if (sort.key === key) sort.dir = sort.dir === 'asc' ? 'desc' : 'asc';
      else sort = {key: key, dir: (key === 'node' || key === 'hardware') ? 'asc' : 'desc'};
      renderTable(current);
    }

    function openNodeEvidence(agent) {
      selectNode(agent.node_raw, false);
      const points = (agent._runs || []).slice()
        .sort(function (a, b) { return String(a.started_at).localeCompare(String(b.started_at)); })
        .map(function (run) {
          return {label: run.group, url: run.url, timestamp: run.started_at, valueSummary: run.state + ' · ' + run.pipeline, details: {pipeline: run.pipeline, queue: run.queue, state: run.state, build: '#' + value(run.build_number)}};
        });
      openHistoryEvidence(signalTerm() + ' failures on ' + agent.node, points, signalTerm() + ' failing runs on this node over ' + windowId + ', each linking to its BuildKite log', SOURCE_ASSETS.operations);
    }

    function renderTable(view) {
      clear(tableHost);
      // The dedicated signal column is redundant when "All failures" is selected
      // (it would equal the Test group failures total), so we drop it and let the
      // Failures column carry the sort.
      const showSignalColumn = signal !== 'all';
      // Default sort follows the active signal until the user picks a column.
      if (!sortExplicit) sort = {key: defaultSortKey(), dir: 'desc'};
      // Never sort by a column that isn't rendered.
      if (!showSignalColumn && sort.key === 'infra_suspect') sort.key = 'failures';
      const rows = sortedAgents(view);
      const columns = [
        {label: 'Physical node', sticky: true, width: '190px', sortKey: 'node', render: function (row) { return linkButton(value(row.node), function () { focusNode(row.node_raw); }, 'Show this node in the timeline and co-failure events', 'Show ' + value(row.node) + ' in the timeline and co-failure events'); }},
        {label: 'GPU', width: '62px', sortKey: 'hardware', render: function (row) { return value(row.hardware); }},
        {label: 'Runs', numeric: true, width: '66px', sortKey: 'runs', render: function (row) { return integer(row.runs); }},
        {label: 'Fail %', numeric: true, width: '74px', sortKey: 'incident_rate', render: function (row) { return (Number(row.incident_rate) * 100).toFixed(1) + '%'; }},
        {label: 'Test group failures', numeric: true, width: '150px', sortKey: 'failures', render: function (row) {
          const cell = n('span', 'ops-failures-cell');
          cell.append(n('span', 'ops-failures-total', integer(row.failures)));
          if (row.hard || row.soft) {
            cell.append(n('span', 'ops-failures-split', integer(row.hard) + ' hard · ' + integer(row.soft) + ' soft'));
          }
          return cell;
        }},
      ];
      if (showSignalColumn) {
        columns.push({label: signalColumnLabel(), numeric: true, width: '132px', sortKey: 'infra_suspect', render: function (row) { return integer(row.infra_suspect); }});
      }
      columns.push(
        {label: 'Groups', numeric: true, width: '68px', sortKey: 'distinct_groups', render: function (row) { return integer(row.distinct_groups); }},
        {label: 'Co-fail', numeric: true, width: '68px', sortKey: 'cofailure_event_count', render: function (row) { return integer(row.cofailure_event_count); }},
        {label: 'Evidence', width: '86px', render: function (row) { return linkButton('runs ↗', function () { openNodeEvidence(row); }, 'Open ' + signalTerm() + ' failing runs and BuildKite logs for ' + value(row.node)); }}
      );
      const table = dataTable(columns, rows, 'Per-node AMD reliability in ' + windowId, {name: 'agent-nodes', minWidth: '930px', sort: sort, onSort: onSort});
      table.classList.add('ops-agent-table');
      tableHost.append(panel('AMD nodes by reliability', tableDescription(view), [table]));
    }

    // Panel copy, written for the currently-selected failure signal so the reader
    // knows exactly what each column counts without cross-referencing the toggle.
    function tableDescription(view) {
      const count = integer(view.agents.length) + ' node(s) in ' + windowId;
      const base = 'Runs and Fail % come from the node_days rollup of every build. Test group failures counts each hard or soft failing run (with its hard/soft split) and honors the cancelled-build toggle. Groups and Co-fail are limited to that same set.';
      if (signal === 'infra') {
        return count + ', sorted by infra-suspect failures. The Infra-suspect failures column is the node-attributable subset of Test group failures — a group that otherwise passed that day, including on another node. ' + base + ' Click a node (or Evidence) to load its timeline; sort by any column.';
      }
      if (signal === 'hard') {
        return count + ', sorted by hard failures. The Hard failures column is the hard-only subset of Test group failures (soft failures excluded). ' + base + ' Click a node (or Evidence) to load its timeline; sort by any column.';
      }
      return count + ', sorted by Test group failures. With every failure counted, the total already carries the signal, so no separate signal column is shown. ' + base + ' Click a node (or Evidence) to load its timeline; sort by any column.';
    }

    function timelineAgents(view) {
      return view.agents.filter(function (agent) { return agent.identified && agent.infra_suspect; })
        .sort(function (a, b) { return b.cofailure_event_count - a.cofailure_event_count || b.infra_suspect - a.infra_suspect; });
    }

    function renderNodeSelect(view) {
      const nodes = timelineAgents(view);
      clear(nodeSelect);
      nodes.forEach(function (agent) {
        const option = n('option', '', agent.node + ' (' + agent.infra_suspect + ' ' + signalTerm() + ', ' + agent.cofailure_event_count + ' co-failures)');
        option.value = agent.node_raw;
        nodeSelect.append(option);
      });
      if (!nodes.some(function (agent) { return agent.node_raw === selectedNode; })) {
        selectedNode = nodes.length ? nodes[0].node_raw : '';
      }
      nodeSelect.value = selectedNode;
      nodeSelect.disabled = !nodes.length;
    }

    function drawSelectedTimeline(view) {
      const agent = view.agents.find(function (a) { return a.node_raw === selectedNode; });
      requestAnimationFrame(function () {
        timelineBounds = drawAgentTimeline(agent ? agent._runs : [], agent ? agent.node : '-', timelineChart, timelineRange);
      });
    }

    function selectNode(nodeRaw, scroll, keepZoom) {
      selectedNode = nodeRaw;
      state.agentNode = nodeRaw;
      setQueryValue('agent_node', nodeRaw);
      if (!keepZoom) timelineRange = null;
      if (!current) return;
      renderNodeSelect(current);
      drawSelectedTimeline(current);
      if (scroll) timelineChart.root.scrollIntoView({behavior: 'smooth', block: 'center'});
    }

    // Table node click: load the node's timeline AND scope the co-failure events
    // list to it (expanded), so the two views focus together.
    function focusNode(nodeRaw) {
      eventNodeFilter = nodeRaw;
      selectNode(nodeRaw, true);
      renderEvents(current);
    }

    function currentTimelineRange() {
      if (timelineRange) return timelineRange;
      return timelineBounds ? {min: timelineBounds.min, max: timelineBounds.max} : null;
    }

    // Constrain a proposed [min,max] window: keep at least a ~1-minute span and
    // never widen past the full padded data bounds.
    function clampRange(min, max) {
      if (max - min < 60000) { const c = (min + max) / 2; min = c - 30000; max = c + 30000; }
      if (timelineBounds) {
        const fpad = Math.max(60000, (timelineBounds.max - timelineBounds.min) * 0.02);
        min = Math.max(min, timelineBounds.min - fpad);
        max = Math.min(max, timelineBounds.max + fpad);
      }
      return {min: min, max: max};
    }

    function setTimelineRange(min, max) {
      timelineRange = clampRange(min, max);
      drawSelectedTimeline(current);
    }

    function zoomBy(factor) {
      const r = currentTimelineRange();
      if (!r) return;
      const center = (r.min + r.max) / 2;
      const half = ((r.max - r.min) / 2) * factor;
      setTimelineRange(center - half, center + half);
    }

    function resetZoom() {
      timelineRange = null;
      drawSelectedTimeline(current);
    }

    // Live Chart.js instance for the timeline, for pixel<->time mapping.
    function timelineXScale() {
      const chart = charts.get('analytics-agent-timeline');
      return chart && chart.scales ? chart.scales.x : null;
    }

    // Click-drag range selection on the timeline canvas. Wired once to the
    // persistent canvas; handlers read the live chart scale.
    function enableTimelineInteractions() {
      const canvas = timelineChart.canvas;
      const viewport = timelineChart.viewport;
      viewport.style.position = 'relative';
      const overlay = n('div', 'ops-timeline-select');
      overlay.style.display = 'none';
      viewport.append(overlay);

      let dragStartPx = null;
      let dragMoved = false;
      let suppressClick = false;

      function pxIn(clientX) {
        const rect = canvas.getBoundingClientRect();
        return Math.max(0, Math.min(canvas.clientWidth, clientX - rect.left));
      }
      function paintOverlay(a, b) {
        overlay.style.display = 'block';
        overlay.style.left = Math.min(a, b) + 'px';
        overlay.style.width = Math.abs(b - a) + 'px';
        overlay.style.height = canvas.clientHeight + 'px';
      }
      function endDrag() {
        overlay.style.display = 'none';
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
      }
      function onMove(e) {
        if (dragStartPx === null) return;
        const px = pxIn(e.clientX);
        if (Math.abs(px - dragStartPx) > 4) dragMoved = true;
        if (dragMoved) paintOverlay(dragStartPx, px);
      }
      function onUp(e) {
        if (dragStartPx === null) return;
        const startPx = dragStartPx;
        dragStartPx = null;
        endDrag();
        if (!dragMoved) return;  // treat as a click -> let the bar open its log
        suppressClick = true;
        const scale = timelineXScale();
        if (!scale) return;
        const endPx = pxIn(e.clientX);
        const a = scale.getValueForPixel(Math.min(startPx, endPx));
        const b = scale.getValueForPixel(Math.max(startPx, endPx));
        if (Number.isFinite(a) && Number.isFinite(b) && b > a) setTimelineRange(a, b);
      }
      canvas.addEventListener('mousedown', function (e) {
        if (e.button !== 0 || !timelineXScale()) return;
        dragStartPx = pxIn(e.clientX);
        dragMoved = false;
        window.addEventListener('mousemove', onMove);
        window.addEventListener('mouseup', onUp);
      });
      // Capture-phase guard: swallow the click Chart.js would fire after a drag
      // (which would otherwise open a BuildKite log). Plain clicks pass through.
      canvas.addEventListener('click', function (e) {
        if (suppressClick) { suppressClick = false; e.stopImmediatePropagation(); e.preventDefault(); }
      }, true);
    }

    // Clicking a co-failure event loads its node and zooms the timeline to it.
    // scroll defaults on; the "select to expand" hint passes false so expanding
    // in place doesn't yank the page up to the timeline.
    function zoomToEvent(event, scroll) {
      selectedNode = event.node_raw;
      state.agentNode = event.node_raw;
      setQueryValue('agent_node', event.node_raw);
      const pad = Math.max(60000, (event._endMs - event._startMs) * 0.15);
      timelineRange = {min: event._startMs - pad, max: event._endMs + pad};
      if (!current) return;
      renderNodeSelect(current);
      drawSelectedTimeline(current);
      if (scroll !== false) timelineChart.root.scrollIntoView({behavior: 'smooth', block: 'center'});
    }

    // Select a co-failure event: inflate/highlight its card, then zoom the timeline.
    // scroll defaults on; pass false to expand in place without scrolling up.
    function selectEvent(event, scroll) {
      selectedEventKey = eventKey(event);
      renderEvents(current);
      zoomToEvent(event, scroll);
    }

    function renderEvents(view) {
      clear(eventsHost);
      const section = n('section', 'ops-cluster-section');
      const header = n('header', 'ops-section-header');
      const heading = n('div', 'ops-section-heading');
      const filtered = eventNodeFilter
        ? view.events.filter(function (e) { return e.node_raw === eventNodeFilter; })
        : view.events;
      const scoped = !!eventNodeFilter;
      const desc = scoped
        ? integer(filtered.length) + ' event(s) on this node in ' + windowId + '. Click an event to expand it (full runs + per-run timing) and zoom the timeline to it.'
        : integer(view.events.length) + ' event(s) in ' + windowId + '. Two or more ' + signalTerm() + ' failures on one node within ' + agentCofailLabel(cofailMins) + ' (retries of the same test group within a build count once): "concurrent" (overlapping) points to contention or an ephemeral fault; "sequential" (back-to-back) suggests the node was left unclean. Click an event to expand it and zoom the timeline to it.';
      add(heading, [
        n('h2', 'ops-section-title', scoped ? 'Co-failure events · one node' : 'Co-failure events'),
        n('p', 'ops-section-description', desc),
      ]);
      header.append(heading);
      if (scoped) {
        header.append(linkButton('Show all nodes ✕', function () { eventNodeFilter = ''; renderEvents(current); }, 'Clear the node filter on co-failure events'));
      }
      section.append(header);
      if (!filtered.length) {
        section.append(n('div', 'ops-evidence-note is-success', scoped ? 'No co-failure events on this node in the current window and filter.' : 'No co-failure events in this window and filter.'));
      } else {
        const grid = n('div', 'ops-cofailure-grid');
        filtered.slice(0, scoped ? 200 : 80).forEach(function (event) { grid.append(agentCofailureCard(event, selectEvent, eventKey(event) === selectedEventKey)); });
        section.append(grid);
      }
      eventsHost.append(section);
    }

    function apply() {
      current = computeView();
      signalCaptionEl.textContent = signalCaption();
      // The infra-suspect criterion only defines the default signal.
      criteriaNoteEl.hidden = signal !== 'infra';
      clear(emptyHost);
      renderKpis(current);
      if (!nodeDays.length) {
        emptyHost.append(n('div', 'ops-evidence-note is-warning', 'No AMD node data has been collected yet. It populates as collect_agent_health.py captures the Buildkite agent k8s:node tag.'));
      } else if (!current.agents.length) {
        emptyHost.append(n('div', 'ops-evidence-note is-warning', 'No AMD runs match the current window and filter.'));
      }
      renderTable(current);
      renderNodeSelect(current);
      drawSelectedTimeline(current);
      renderEvents(current);
    }

    buildModeToggle();
    buildControls();
    enableTimelineInteractions();
    apply();
  }

  function renderGroupOverviewCharts(host, rows, ops, reliability) {
    const risks = reliabilityRiskClusters(rows);
    const hardware = reliabilityHardwareClusters(rows);
    const grid = n('div', 'ops-grid ops-grid-2');
    const riskChart = chartPanel('Reliability distribution', 'Strict upstream groups clustered by retained incident rate', 'analytics-group-risk');
    const hardwareChart = chartPanel('Hardware composition', 'Stable and incident-observed groups by strict hardware family', 'analytics-group-hardware');
    add(grid, [riskChart.root, hardwareChart.root]);
    host.append(grid);
    requestAnimationFrame(function () {
      drawChart('analytics-group-risk', riskChart.canvas, {
        type: 'bar',
        data: {labels: risks.map(function (cluster) { return cluster.label; }), datasets: [{label: 'Groups', data: risks.map(function (cluster) { return cluster.count; }), backgroundColor: ['#35bb78', '#4e9ed4', '#e3a63a', '#d9823b', '#e06464']}]},
        options: {scales: {x: {grid: {display: false}}, y: {beginAtZero: true, title: {display: true, text: 'Strict groups'}}}},
        evidenceTitle: 'Reliability distribution clusters',
        evidence: risks.map(function (cluster) { return {id: cluster.id, label: cluster.label, valueSummary: integer(cluster.count) + ' groups', details: {definition: cluster.description, median_incident_rate: cluster.medianRate === null ? '-' : Number(cluster.medianRate).toFixed(1) + '%', current_incidents: cluster.latestIncidents}, sources: [{label: 'Open published upstream reliability', url: SOURCE_ASSETS.operations}], onOpen: function () { openReliabilityList(cluster.label + ' reliability', cluster.description, cluster.rows, ops, reliability); }}; }),
      });
      drawChart('analytics-group-hardware', hardwareChart.canvas, {
        type: 'bar',
        data: {labels: hardware.map(function (cluster) { return cluster.label; }), datasets: [
          {label: 'Stable', data: hardware.map(function (cluster) { return cluster.count - cluster.incidentObserved; }), backgroundColor: '#35bb78'},
          {label: 'Incident observed', data: hardware.map(function (cluster) { return cluster.incidentObserved; }), backgroundColor: '#e3a63a'},
        ]},
        options: {scales: {x: {stacked: true, grid: {display: false}}, y: {stacked: true, beginAtZero: true, title: {display: true, text: 'Strict groups'}}}},
        evidenceTitle: 'Hardware reliability clusters',
        evidence: hardware.map(function (cluster) { return {id: cluster.id, label: cluster.label, valueSummary: integer(cluster.count) + ' groups - ' + integer(cluster.incidentObserved) + ' incident observed', sources: [{label: 'Open published upstream reliability', url: SOURCE_ASSETS.operations}], onOpen: function () { openReliabilityList(cluster.label + ' test groups', 'Strict groups assigned to the ' + cluster.label + ' hardware family', cluster.rows, ops, reliability); }}; }),
      });
    });
  }

  function renderFlakeOverviewCharts(host, rows, ops, reliability) {
    const risks = reliabilityRiskClusters(rows).filter(function (cluster) { return cluster.count > 0; });
    const dispositions = [
      {id: 'passing', label: 'Passing latest', tone: '#35bb78', rows: rows.filter(function (row) { return observationState(latestObservation(row) || {}) === 'passed'; })},
      {id: 'soft', label: 'Soft incident latest', tone: '#e3a63a', rows: rows.filter(function (row) { return ['soft', 'soft_fail', 'soft_failed'].includes(observationState(latestObservation(row) || {})); })},
      {id: 'hard', label: 'Hard incident latest', tone: '#e06464', rows: rows.filter(function (row) { const latest = latestObservation(row) || {}; return isIncidentObservation(latest) && !['soft', 'soft_fail', 'soft_failed'].includes(observationState(latest)); })},
      {id: 'unknown', label: 'Unknown latest', tone: '#66717d', rows: rows.filter(function (row) { const latest = latestObservation(row) || {}; return observationState(latest) !== 'passed' && !isIncidentObservation(latest); })},
    ].filter(function (group) { return group.rows.length > 0; });
    const grid = n('div', 'ops-grid ops-grid-2');
    const riskChart = chartPanel('Candidate risk distribution', 'How often each mixed-history group has an incident in retained upstream main runs', 'analytics-flake-risk');
    const dispositionChart = chartPanel('What candidates are doing now', 'Latest exact upstream result for every mixed-outcome candidate', 'analytics-flake-disposition');
    add(grid, [riskChart.root, dispositionChart.root]);
    host.append(grid);
    requestAnimationFrame(function () {
      drawChart('analytics-flake-risk', riskChart.canvas, {
        type: 'bar',
        data: {labels: risks.map(function (cluster) { return cluster.label; }), datasets: [
          {label: 'Candidates', data: risks.map(function (cluster) { return cluster.count; }), backgroundColor: risks.map(function (cluster) { return cluster.id === 'critical' ? '#e06464' : cluster.id === 'watch' ? '#5ca8ff' : '#e3a63a'; })},
          {label: 'Incident on latest run', data: risks.map(function (cluster) { return cluster.latestIncidents; }), backgroundColor: '#7f3441'},
        ]},
        options: {scales: {x: {grid: {display: false}}, y: {beginAtZero: true, title: {display: true, text: 'Mixed-outcome candidates'}}}},
        evidenceTitle: 'Flake-candidate incident-rate distribution',
        evidence: risks.map(function (cluster) { return {id: cluster.id, label: cluster.label, valueSummary: integer(cluster.count) + ' candidates - ' + integer(cluster.latestIncidents) + ' incident latest', details: {definition: cluster.description, median_incident_rate: cluster.medianRate === null ? '-' : Number(cluster.medianRate).toFixed(1) + '%'}, sources: [{label: 'Open published upstream reliability', url: SOURCE_ASSETS.operations}], onOpen: function () { openReliabilityList(cluster.label + ' flake candidates', cluster.description, cluster.rows, ops, reliability); }}; }),
      });
      drawChart('analytics-flake-disposition', dispositionChart.canvas, {
        type: 'bar',
        data: {labels: dispositions.map(function (group) { return group.label; }), datasets: [{label: 'Candidates', data: dispositions.map(function (group) { return group.rows.length; }), backgroundColor: dispositions.map(function (group) { return group.tone; })}]},
        options: {indexAxis: 'y', scales: {x: {beginAtZero: true, title: {display: true, text: 'Mixed-outcome candidates'}}, y: {grid: {display: false}}}},
        evidenceTitle: 'Latest exact result for flake candidates',
        evidence: dispositions.map(function (group) { return {id: group.id, label: group.label, valueSummary: integer(group.rows.length) + ' candidates', sources: [{label: 'Open published upstream reliability', url: SOURCE_ASSETS.operations}], onOpen: function () { openReliabilityList(group.label, 'Mixed-history groups with this latest exact upstream result', group.rows, ops, reliability); }}; }),
      });
    });
  }

  function platformComparison(reliability) {
    const comparison = (reliability || {}).platform_comparison || {};
    return comparison.available === true && Array.isArray(comparison.rows) ? comparison : {available: false, summary: {}, matching: {}, rows: []};
  }

  const ANALYTICS_WINDOW_HOURS = {'1h': 1, '3h': 3, '6h': 6, '24h': 24, '7d': 168, '30d': 720};

  function analyticsWindowBounds(ops, windowId) {
    const end = new Date((ops || {}).generated_at || Date.now()).getTime();
    const hours = ANALYTICS_WINDOW_HOURS[windowId] || 24;
    const span = hours * 3600000;
    return {
      id: windowId,
      hours: hours,
      start: end - span,
      end: end,
      priorStart: end - span * 2,
      priorEnd: end - span,
    };
  }

  function observationInRange(observation, start, end) {
    const timestamp = new Date(observationTimestamp(observation) || 0).getTime();
    return Number.isFinite(timestamp) && timestamp >= start && timestamp <= end;
  }

  function comparisonVariantWindow(variant, reliability, start, end) {
    const group = comparisonGroupById(reliability, variant.group_id);
    const all = group ? groupHistoryObservations(group, 'main') : [];
    const observations = all.filter(function (observation) { return observationInRange(observation, start, end); });
    const passed = observations.filter(function (observation) { return observationState(observation) === 'passed'; }).length;
    const soft = observations.filter(function (observation) { return ['soft', 'soft_fail', 'soft_failed'].includes(observationState(observation)); }).length;
    const incidents = observations.filter(isIncidentObservation).length;
    const hard = Math.max(0, incidents - soft);
    const wallDurations = observations.map(function (observation) {
      const wall = observation.wall_duration_mins;
      if (wall !== null && wall !== undefined && Number.isFinite(Number(wall))) return Number(wall);
      return observation.duration_basis === 'job_wall' && Number.isFinite(Number(observation.duration_mins))
        ? Number(observation.duration_mins) : null;
    }).filter(function (minutes) { return minutes !== null; });
    const latest = observations[observations.length - 1] || {};
    const oldestRetained = all.length ? new Date(observationTimestamp(all[0]) || 0).getTime() : null;
    const historyIncomplete = Boolean(group && group.history_truncated && Number.isFinite(oldestRetained) && oldestRetained > start);
    return Object.assign({}, variant, {
      runs: observations.length,
      build_count: new Set(observations.map(function (observation) { return observation.build_number; }).filter(Boolean)).size,
      passed: passed,
      hard_failed: hard,
      soft_failed: soft,
      incidents: incidents,
      incident_rate_pct: observations.length ? incidents / observations.length * 100 : null,
      mixed_outcomes: Boolean(passed && incidents),
      latest_state: observations.length ? observationState(latest) : 'not_observed',
      latest_observed_at: observationTimestamp(latest),
      latest_url: exactPipelineEvidenceUrl(latest, 'ci'),
      median_duration_mins: percentileValue(wallDurations, 0.5),
      p90_duration_mins: percentileValue(wallDurations, 0.9),
      max_duration_mins: wallDurations.length ? Math.max.apply(null, wallDurations) : null,
      duration_basis: wallDurations.length ? 'job_wall' : 'unavailable',
      _historyIncomplete: historyIncomplete,
      _observations: observations,
    });
  }

  function comparisonSideWindow(source, variants, buildCount) {
    const runs = variants.reduce(function (sum, variant) { return sum + Number(variant.runs || 0); }, 0);
    const passed = variants.reduce(function (sum, variant) { return sum + Number(variant.passed || 0); }, 0);
    const hard = variants.reduce(function (sum, variant) { return sum + Number(variant.hard_failed || 0); }, 0);
    const soft = variants.reduce(function (sum, variant) { return sum + Number(variant.soft_failed || 0); }, 0);
    const incidents = hard + soft;
    const timed = variants.filter(function (variant) { return variant.duration_basis === 'job_wall' && Number.isFinite(Number(variant.p90_duration_mins)); });
    const slowest = timed.slice().sort(function (a, b) { return Number(b.p90_duration_mins) - Number(a.p90_duration_mins); })[0] || {};
    return Object.assign({}, source, {
      variants: variants,
      runs: runs,
      passed: passed,
      hard_failed: hard,
      soft_failed: soft,
      incidents: incidents,
      incident_rate_pct: runs ? incidents / runs * 100 : null,
      attempts_per_100_builds: buildCount ? runs / buildCount * 100 : null,
      mixed_outcome_variant_count: variants.filter(function (variant) { return variant.mixed_outcomes; }).length,
      worst_p90_duration_mins: Number.isFinite(Number(slowest.p90_duration_mins)) ? Number(slowest.p90_duration_mins) : null,
      slowest_group_id: slowest.group_id || null,
      duration_basis: timed.length ? 'job_wall' : 'unavailable',
      history_incomplete_variant_count: variants.filter(function (variant) { return variant._historyIncomplete; }).length,
      retry_attempts: 0,
      child_retry_attempts: 0,
      retry_involved_attempts: 0,
      retry_frequency_pct: runs ? 0 : null,
      recovered_chains: 0,
      retry_recovery_rate_pct: null,
    });
  }

  function comparisonWindowBuildCount(reliability, start, end) {
    const builds = new Set();
    reliabilityCatalog(reliability).forEach(function (group) {
      groupHistoryObservations(group, 'main').forEach(function (observation) {
        if (observationInRange(observation, start, end) && observation.build_number) builds.add(observation.build_number);
      });
    });
    return builds.size;
  }

  function comparisonRetryTimestamp(item) {
    const timestamp = new Date((item || {}).observed_at || 0).getTime();
    return Number.isFinite(timestamp) ? timestamp : null;
  }

  function applyComparisonRetryWindow(row, retryRows) {
    ['AMD', 'CUDA'].forEach(function (platform) {
      const side = platform === 'AMD' ? row.amd : row.cuda;
      const attempts = retryRows.attempts.filter(function (attempt) { return attempt._platform === platform; });
      const children = attempts.filter(function (attempt) { return Boolean(attempt.retry_source); });
      const recoveries = retryRows.recoveries.filter(function (recovery) { return recovery._platform === platform; });
      side.retry_attempts = attempts.length;
      side.retry_involved_attempts = attempts.length;
      side.child_retry_attempts = children.length;
      side.retry_frequency_pct = side.runs ? children.length / side.runs * 100 : null;
      side.recovered_chains = recoveries.length;
      side.retry_recovery_rate_pct = children.length ? recoveries.length / children.length * 100 : null;
    });
  }

  function combineComparisonSides(rows, sideName) {
    const sides = rows.map(function (row) { return row[sideName] || {}; });
    const runs = sides.reduce(function (sum, side) { return sum + Number(side.runs || 0); }, 0);
    const incidents = sides.reduce(function (sum, side) { return sum + Number(side.incidents || 0); }, 0);
    const children = sides.reduce(function (sum, side) { return sum + Number(side.child_retry_attempts || 0); }, 0);
    const involved = sides.reduce(function (sum, side) { return sum + Number(side.retry_involved_attempts || 0); }, 0);
    const recovered = sides.reduce(function (sum, side) { return sum + Number(side.recovered_chains || 0); }, 0);
    return {
      runs: runs,
      incidents: incidents,
      incident_rate_pct: runs ? incidents / runs * 100 : null,
      child_retry_attempts: children,
      retry_involved_attempts: involved,
      retry_frequency_pct: runs ? children / runs * 100 : null,
      recovered_chains: recovered,
      retry_recovery_rate_pct: children ? recovered / children * 100 : null,
    };
  }

  function platformComparisonForWindow(comparison, reliability, retry, ops, windowId) {
    if (windowId === '30d') {
      return Object.assign({}, comparison, {
        rows: comparison.rows.map(function (row) { return Object.assign({}, row, {_window: analyticsWindowBounds(ops, windowId), _priorAvailable: false}); }),
        window: Object.assign(analyticsWindowBounds(ops, windowId), {completeAggregate: true, buildCount: comparison.cohort_build_count}),
      });
    }
    const bounds = analyticsWindowBounds(ops, windowId);
    const buildCount = comparisonWindowBuildCount(reliability, bounds.start, bounds.end);
    const priorBuildCount = comparisonWindowBuildCount(reliability, bounds.priorStart, bounds.priorEnd);
    const rows = comparison.rows.map(function (sourceRow) {
      function sideFor(source, start, end, count) {
        const variants = (source.variants || []).map(function (variant) { return comparisonVariantWindow(variant, reliability, start, end); });
        return comparisonSideWindow(source, variants, count);
      }
      const row = Object.assign({}, sourceRow, {
        amd: sideFor(sourceRow.amd || {}, bounds.start, bounds.end, buildCount),
        cuda: sideFor(sourceRow.cuda || {}, bounds.start, bounds.end, buildCount),
        amd_prior: sideFor(sourceRow.amd || {}, bounds.priorStart, bounds.priorEnd, priorBuildCount),
        cuda_prior: sideFor(sourceRow.cuda || {}, bounds.priorStart, bounds.priorEnd, priorBuildCount),
        _window: bounds,
        _priorAvailable: priorBuildCount > 0,
      });
      const retryRows = comparisonRetryRows(row, retry, bounds);
      const priorRetryRows = comparisonRetryRows(row, retry, {start: bounds.priorStart, end: bounds.priorEnd});
      applyComparisonRetryWindow(row, retryRows);
      const priorRow = {amd: row.amd_prior, cuda: row.cuda_prior};
      applyComparisonRetryWindow(priorRow, priorRetryRows);
      row.incident_rate_delta_pp = row.comparison_eligible && row.amd.runs && row.cuda.runs ? row.amd.incident_rate_pct - row.cuda.incident_rate_pct : null;
      row.retry_frequency_delta_pp = row.comparison_eligible && row.amd.runs && row.cuda.runs ? row.amd.retry_frequency_pct - row.cuda.retry_frequency_pct : null;
      row.worst_p90_delta_mins = row.comparison_eligible && row.amd.worst_p90_duration_mins !== null && row.cuda.worst_p90_duration_mins !== null ? row.amd.worst_p90_duration_mins - row.cuda.worst_p90_duration_mins : null;
      row.amd_incident_change_pp = row._priorAvailable && row.amd.runs && row.amd_prior.runs ? row.amd.incident_rate_pct - row.amd_prior.incident_rate_pct : null;
      row.amd_retry_change_pp = row._priorAvailable && row.amd.runs && row.amd_prior.runs ? row.amd.retry_frequency_pct - row.amd_prior.retry_frequency_pct : null;
      return row;
    });
    const exact = rows.filter(function (row) { return row.comparison_eligible; });
    const active = rows.filter(function (row) { return row.amd.runs > 0; });
    const exactActive = exact.filter(function (row) { return row.amd.runs > 0 || row.cuda.runs > 0; });
    const summary = Object.assign({}, comparison.summary, {
      amd: combineComparisonSides(active, 'amd'),
      comparable_amd: combineComparisonSides(exactActive, 'amd'),
      matched_cuda: combineComparisonSides(exactActive, 'cuda'),
      active_amd_group_count: active.length,
      regressed_incident_group_count: active.filter(function (row) { return Number(row.amd_incident_change_pp) > 0; }).length,
      new_incident_group_count: active.filter(function (row) { return row.amd.incidents > 0 && row.amd_prior.incidents === 0; }).length,
      regressed_retry_group_count: active.filter(function (row) { return Number(row.amd_retry_change_pp) > 0; }).length,
      history_incomplete_variant_count: active.reduce(function (sum, row) { return sum + Number(row.amd.history_incomplete_variant_count || 0); }, 0),
    });
    return Object.assign({}, comparison, {
      rows: rows,
      summary: summary,
      window: Object.assign(bounds, {completeAggregate: false, buildCount: buildCount, priorBuildCount: priorBuildCount}),
    });
  }

  function comparisonPercent(side, key) {
    const raw = (side || {})[key];
    if (raw === null || raw === undefined || raw === '') return '-';
    const numeric = Number(raw);
    return Number.isFinite(numeric) ? numeric.toFixed(1) + '%' : '-';
  }

  function comparisonDelta(valueNumber, unit) {
    if (valueNumber === null || valueNumber === undefined || valueNumber === '') return '-';
    const numeric = Number(valueNumber);
    if (!Number.isFinite(numeric)) return '-';
    return (numeric > 0 ? '+' : '') + numeric.toFixed(1) + (unit || '');
  }

  function comparisonTone(valueNumber, threshold) {
    if (valueNumber === null || valueNumber === undefined || valueNumber === '') return 'is-neutral';
    const numeric = Number(valueNumber);
    if (!Number.isFinite(numeric) || Math.abs(numeric) <= Number(threshold || 0)) return 'is-neutral';
    return numeric > 0 ? 'is-danger' : 'is-success';
  }

  function comparisonMatchLabel(row) {
    const labels = {
      exact_cuda_pair: 'Exact CUDA pair',
      shared_amd_base_label: 'Shared AMD variants',
      ambiguous_cuda_variants: 'Multiple CUDA variants',
      generic_or_unsupported_gpu_reference: 'Generic GPU reference',
      hardware_specific_label: 'Hardware-specific label',
      no_cuda_equivalent: 'No CUDA equivalent',
    };
    return labels[row.match_status] || 'Review required';
  }

  function comparisonNameKey(name) {
    return String(name || '')
      .replace(/^AMD:\s*/i, '')
      .replace(/^mi\d{3,4}b?_\d+:\s*/i, '')
      .replace(/\s*\(mi\d{3,4}b?_\d+\)\s*$/i, '')
      .trim().replace(/\s+/g, ' ').toLowerCase();
  }

  function comparisonGroupById(reliability, groupId) {
    return groupReliabilityByRef(reliability, groupId);
  }

  function comparisonVariantCell(variant, ops, reliability, platform) {
    const group = comparisonGroupById(reliability, variant.group_id);
    const cell = n('div', 'ops-entity-cell');
    cell.append(linkButton(variant.name || 'Unnamed variant', function () {
      if (group) openGroupDetail(variant, ops, group, reliability);
    }, 'Inspect exact ' + platform + ' group history'));
    const meta = [platform, hardwareDisplayLabel(variant.hardware), (variant.queues || []).join(', ')].filter(Boolean).join(' - ');
    if (meta) cell.append(n('span', 'ops-entity-meta', meta));
    return cell;
  }

  function comparisonRetryRows(row, retry, bounds) {
    const key = row.comparison_key;
    const groupIds = new Set([].concat((row.amd || {}).group_ids || [], (row.cuda || {}).group_ids || []));
    function selected(source) {
      return (source || []).filter(function (item) {
        const identityMatches = item.group_id ? groupIds.has(item.group_id) : comparisonNameKey(item.name) === key;
        if (!identityMatches) return false;
        if (!bounds) return true;
        const timestamp = comparisonRetryTimestamp(item);
        return timestamp !== null && timestamp >= bounds.start && timestamp <= bounds.end;
      }).map(function (item) {
        return Object.assign({}, item, {
          _platform: /^AMD:\s*/i.test(String(item.name || '')) ? 'AMD' : 'CUDA',
          _role: item.retry_source ? 'Child retry' : 'Original attempt',
        });
      });
    }
    return {
      attempts: selected((retry || {}).retry_attempts),
      recoveries: selected((retry || {}).failed_then_passed_recoveries),
    };
  }

  function openPlatformComparisonDetail(row, ops, reliability, retry, focus) {
    const amd = row.amd || {};
    const cuda = row.cuda || {};
    const content = n('div', 'ops-comparison-detail');
    content.append(statusStrip([
      {label: 'AMD INCIDENT FREQUENCY', value: comparisonPercent(amd, 'incident_rate_pct'), meta: integer(amd.incidents) + ' of ' + integer(amd.runs) + ' terminal attempts', tone: Number(amd.incident_rate_pct) ? 'is-warning' : 'is-success'},
      {label: 'CUDA INCIDENT FREQUENCY', value: comparisonPercent(cuda, 'incident_rate_pct'), meta: integer(cuda.incidents) + ' of ' + integer(cuda.runs) + ' matched attempts'},
      {label: 'AMD CHILD RETRY SHARE', value: comparisonPercent(amd, 'retry_frequency_pct'), meta: integer(amd.child_retry_attempts) + ' child retries - ' + integer(amd.recovered_chains) + ' recovered', tone: Number(amd.child_retry_attempts) ? 'is-warning' : 'is-success'},
      {label: 'WORST P90 DELTA', value: comparisonDelta(row.worst_p90_delta_mins, 'm'), meta: duration(amd.worst_p90_duration_mins) + ' AMD - ' + duration(cuda.worst_p90_duration_mins) + ' CUDA', tone: comparisonTone(row.worst_p90_delta_mins, 5)},
    ]));
    const note = n('div', 'ops-evidence-note is-info');
    add(note, [n('strong', '', comparisonMatchLabel(row) + '. '), n('span', '', row.comparison_eligible ? 'This one-to-one explicit NVIDIA pair shares a hardware-neutral base label in the same completed branch=main cohort.' : 'The AMD evidence is valid, but the reference is excluded from comparative deltas until its variant or hardware ambiguity is reviewed.')]);
    content.append(note);
    const variants = (amd.variants || []).map(function (variant) { return Object.assign({}, variant, {_platform: 'AMD'}); })
      .concat((cuda.variants || []).map(function (variant) { return Object.assign({}, variant, {_platform: 'CUDA'}); }));
    content.append(panel('Hardware variants', integer(amd.variant_count) + ' AMD - ' + integer(cuda.variant_count) + ' CUDA', dataTable([
      {label: 'Platform and exact group', sticky: true, width: '430px', render: function (variant) { return comparisonVariantCell(variant, ops, reliability, variant._platform); }},
      {label: 'Runs', numeric: true, width: '90px', render: function (variant) { const group = comparisonGroupById(reliability, variant.group_id); return linkButton(integer(variant.runs), function () { if (group) openGroupDetail(variant, ops, group, reliability); }); }},
      {label: 'Incidents', numeric: true, width: '110px', render: function (variant) { const group = comparisonGroupById(reliability, variant.group_id); return linkButton(integer(variant.incidents), function () { if (group) openGroupDetail(variant, ops, group, reliability); }); }},
      {label: 'Incident frequency', numeric: true, width: '150px', render: function (variant) { const group = comparisonGroupById(reliability, variant.group_id); return linkButton(comparisonPercent(variant, 'incident_rate_pct'), function () { if (group) openGroupDetail(variant, ops, group, reliability); }); }},
      {label: 'p90 completion', numeric: true, width: '140px', render: function (variant) { const group = comparisonGroupById(reliability, variant.group_id); return linkButton(duration(variant.p90_duration_mins), function () { if (group) openGroupDetail(variant, ops, group, reliability); }); }},
      {label: 'Latest evidence', width: '150px', render: function (variant) { return externalLink('Open job', exactPipelineEvidenceUrl({latest_url: variant.latest_url}, 'ci')); }},
    ], variants, integer(variants.length) + ' exact upstream hardware variants', {name: 'amd-cuda-variants', minWidth: '1070px'}), 'ops-comparison-variants'));

    const retryRows = comparisonRetryRows(row, retry, row._window && row._window.id !== '30d' ? row._window : null);
    if (focus === 'retries' || retryRows.attempts.length || retryRows.recoveries.length) {
      const attemptColumns = [
        {label: 'Platform', width: '90px', render: function (attempt) { return badge(attempt._platform, attempt._platform === 'AMD' ? 'is-info' : 'is-neutral'); }},
        {label: 'Build', width: '100px', render: function (attempt) { return externalLink('#' + value(attempt.build_number), exactReliabilityBuildUrl(attempt), 'ops-mono'); }},
        {label: 'Exact retry attempt', sticky: true, width: '420px', render: function (attempt) { return externalLink(attempt.name || 'Unnamed retry', exactPipelineEvidenceUrl(attempt, 'ci')); }},
        {label: 'Result', width: '120px', render: function (attempt) { return linkedBadge(attempt.state || attempt.result || 'unknown', exactPipelineEvidenceUrl(attempt, 'ci')); }},
        {label: 'Role', width: '140px', render: function (attempt) { return linkedBadge(attempt._role, exactPipelineEvidenceUrl(attempt, 'ci'), null, attempt.retry_source ? 'is-info' : 'is-neutral'); }},
        {label: 'Retry type', width: '130px', render: function (attempt) { return linkedBadge(attempt.retry_type || 'explicit', exactPipelineEvidenceUrl(attempt, 'ci'), null, 'is-info'); }},
      ];
      content.append(compactTablePanel('Retry-involved attempts', integer(retryRows.attempts.filter(function (attempt) { return attempt.retry_source; }).length) + ' child retries inside ' + integer(retryRows.attempts.length) + ' linked attempts', attemptColumns, retryRows.attempts, {
        id: 'comparison-retries-' + row.id,
        limit: 10,
        alwaysBrowse: retryRows.attempts.length > 0,
        browserSubtitle: 'Every row opens the exact upstream Buildkite attempt',
        searchText: function (attempt) { return [attempt._platform, attempt.name, attempt.build_number, attempt.state, attempt.retry_type].join(' '); },
        geometry: {name: 'comparison-retries', minWidth: '1040px'},
      }));
      const recoveryColumns = [
        {label: 'Platform', width: '90px', render: function (recovery) { return badge(recovery._platform, recovery._platform === 'AMD' ? 'is-info' : 'is-neutral'); }},
        {label: 'Build', width: '100px', render: function (recovery) { return externalLink('#' + value(recovery.build_number), exactReliabilityBuildUrl(recovery), 'ops-mono'); }},
        {label: 'Recovered group', sticky: true, width: '420px', render: function (recovery) { return externalLink(recovery.name || 'Unnamed recovery', exactPipelineEvidenceUrl({job_url: recovery.failed_url || recovery.passed_url, build_number: recovery.build_number}, 'ci')); }},
        {label: 'Failed attempt', width: '170px', render: function (recovery) { return externalLink('Open failed log', exactPipelineEvidenceUrl({job_url: recovery.failed_url, build_number: recovery.build_number}, 'ci')); }},
        {label: 'Passing retry', width: '170px', render: function (recovery) { return externalLink('Open passing log', exactPipelineEvidenceUrl({job_url: recovery.passed_url, build_number: recovery.build_number}, 'ci')); }},
      ];
      content.append(compactTablePanel('Confirmed retry recoveries', integer(retryRows.recoveries.length) + ' explicit fail-to-pass chains', recoveryColumns, retryRows.recoveries, {
        id: 'comparison-recoveries-' + row.id,
        limit: 8,
        alwaysBrowse: retryRows.recoveries.length > 0,
        browserSubtitle: 'Failed and passing jobs remain separate exact evidence',
        searchText: function (recovery) { return [recovery._platform, recovery.name, recovery.build_number].join(' '); },
        geometry: {name: 'comparison-recoveries', minWidth: '950px'},
      }));
    }
    content.append(sourceActions([{label: 'Open published comparison data', url: SOURCE_ASSETS.operations}, {label: 'Open upstream CI pipeline', url: 'https://buildkite.com/vllm/ci'}]));
    openOverlay(row.label + ': AMD vs CUDA', 'Exact matched group variants, ' + value((row._window || {}).id, '30d') + ' rates, latency, and Buildkite evidence', content, true, 'amd-cuda-' + row.id);
  }

  function comparisonGroupCell(row, ops, reliability, retry, focus) {
    const cell = n('div', 'ops-entity-cell');
    cell.append(linkButton(row.label, function () { openPlatformComparisonDetail(row, ops, reliability, retry, focus); }));
    const amdHardware = ((row.amd || {}).hardware || []).map(hardwareDisplayLabel).join(', ');
    const cudaHardware = ((row.cuda || {}).hardware || []).map(hardwareDisplayLabel).join(', ');
    cell.append(n('span', 'ops-entity-meta', amdHardware + ' AMD - ' + (cudaHardware || 'no CUDA') + ' - ' + comparisonMatchLabel(row)));
    return cell;
  }

  function comparisonCountRate(side, countKey, rateKey) {
    const source = side || {};
    if (!Number(source.runs || 0)) return 'Not observed';
    return integer(source[countKey]) + ' / ' + integer(source.runs) + ' - ' + comparisonPercent(source, rateKey);
  }

  function comparisonRecoveryShare(side) {
    const source = side || {};
    return Number(source.runs) > 0 ? Number(source.recovered_chains || 0) / Number(source.runs) * 100 : null;
  }

  function analyticsWindowControl(host, comparison) {
    const windowInfo = comparison.window || {};
    const toolbar = n('div', 'ops-toolbar ops-analytics-window-toolbar');
    const label = n('span', 'ops-toolbar-label', 'Observation window');
    toolbar.append(label);
    toolbar.append(segmented(['1h', '3h', '6h', '24h', '7d', '30d'].map(function (id) {
      return {id: id, label: id};
    }), state.analyticsWindow, function (id) {
      setRouteState('ci-analytics', 'analyticsWindow', id, 'analytics_window');
    }, 'Flake and retry observation window'));
    const context = n('span', 'ops-window-context');
    if (windowInfo.completeAggregate) {
      context.textContent = integer(windowInfo.buildCount) + ' completed main builds - complete 30-day aggregate';
    } else {
      context.textContent = integer(windowInfo.buildCount) + ' builds with retained evidence - ending ' + shortDate(new Date(windowInfo.end).toISOString());
    }
    toolbar.append(context);
    host.append(toolbar);
  }

  function renderComparisonChart(host, config) {
    if (!config.rows.length) {
      const empty = n('div', 'ops-empty ops-comparison-empty');
      add(empty, [
        n('strong', '', config.emptyTitle || 'No matching AMD observations in this window.'),
        n('span', '', config.emptyMessage || 'Choose a longer window or inspect the complete 30-day aggregate.'),
      ]);
      host.append(panel(config.title, config.subtitle, empty, 'ops-chart-panel'));
      return;
    }
    const chart = chartPanel(config.title, config.subtitle, config.key);
    chart.root.classList.add('ops-comparison-chart');
    host.append(chart.root);
    requestAnimationFrame(function () {
      drawChart(config.key, chart.canvas, {
        type: 'bar',
        data: {
          labels: config.rows.map(function (row) { return compactChartLabel({name: row.label}, 42); }),
          datasets: (config.datasets || [
            {label: 'AMD', value: config.amdValue, backgroundColor: '#e3a63a'},
            {label: 'Matched CUDA', value: config.cudaValue, backgroundColor: '#5ca8ff'},
          ]).map(function (dataset) {
            const chartDataset = Object.assign({}, dataset, {data: config.rows.map(dataset.value)});
            delete chartDataset.value;
            return chartDataset;
          }),
        },
        options: {
          animation: false,
          indexAxis: 'y',
          scales: {x: {beginAtZero: true, title: {display: true, text: config.axis}}, y: {grid: {display: false}}},
          plugins: config.tooltipLabel ? {tooltip: {callbacks: {label: function (item) { return config.tooltipLabel(config.rows[item.dataIndex], item.datasetIndex, item); }}}} : {},
        },
        evidenceTitle: config.title + ' evidence',
        evidence: config.rows.map(function (row) {
          return {label: row.label, valueSummary: config.evidenceSummary(row), sources: [{label: 'Open published upstream comparison', url: SOURCE_ASSETS.operations}], onOpen: function () { openPlatformComparisonDetail(row, config.ops, config.reliability, config.retry, config.focus); }};
        }),
      });
    });
  }

  function renderPlatformFlakes(host, comparison, ops, reliability, retry) {
    const rows = comparison.rows.slice();
    const summary = comparison.summary || {};
    const amd = summary.amd || {};
    const pairedAmd = summary.comparable_amd || {};
    const cuda = summary.matched_cuda || {};
    const sorted = rows.slice().sort(function (a, b) {
      const regressionDelta = Number(b.amd_incident_change_pp || 0) - Number(a.amd_incident_change_pp || 0);
      return regressionDelta || Number(b.amd.incident_rate_pct || 0) - Number(a.amd.incident_rate_pct || 0) || a.label.localeCompare(b.label);
    });
    const active = sorted.filter(function (row) { return Number(row.amd.runs || 0) > 0; });
    const comparable = active.filter(function (row) { return row.comparison_eligible; });
    const chartRows = comparable.filter(function (row) {
      return Number(row.amd.incidents || 0) > 0 || Number(row.cuda.incidents || 0) > 0;
    });
    const windowInfo = comparison.window || {};
    analyticsWindowControl(host, comparison);
    host.append(statusStrip([
      {label: 'ACTIVE AMD GROUPS', value: integer(active.length) + ' / ' + integer(summary.amd_base_group_count), meta: integer(amd.runs) + ' exact attempts in ' + state.analyticsWindow, onOpen: function () { openTableBrowser({id: 'flake-comparison-all', title: 'AMD and CUDA incident comparison', subtitle: state.analyticsWindow + ' upstream branch=main window', rows: sorted, columns: comparisonFlakeColumns(ops, reliability, retry), searchText: comparisonSearchText, geometry: {name: 'flake-comparison', minWidth: '1260px'}}); }},
      {label: 'AMD INCIDENTS', value: integer(amd.incidents), meta: comparisonPercent(amd, 'incident_rate_pct') + ' of ' + integer(amd.runs) + ' attempts', tone: Number(amd.incidents) ? 'is-warning' : 'is-success'},
      {label: 'REGRESSED VS PRIOR', value: windowInfo.completeAggregate ? '-' : integer(summary.regressed_incident_group_count), meta: windowInfo.completeAggregate ? 'choose 1h-7d for movement' : integer(summary.new_incident_group_count) + ' newly incident groups', tone: Number(summary.regressed_incident_group_count) ? 'is-danger' : 'is-success'},
      {label: 'PAIRED AMD / CUDA', value: comparisonPercent(pairedAmd, 'incident_rate_pct') + ' / ' + comparisonPercent(cuda, 'incident_rate_pct'), meta: integer(comparable.length) + ' active exact pairs'},
    ]));
    const note = n('div', 'ops-evidence-note is-info');
    const retentionNote = Number(summary.history_incomplete_variant_count || 0)
      ? ' ' + integer(summary.history_incomplete_variant_count) + ' high-frequency variants reached the retained-history cap; their window values are lower bounds.' : '';
    add(note, [n('strong', '', 'AMD-first, upstream-only incident evidence. '), n('span', '', (windowInfo.completeAggregate ? 'The 30-day selection uses complete aggregate counters.' : 'Movement compares this window with the immediately preceding equal-length window.') + ' Exact CUDA deltas exclude generic or ambiguous references. Incident frequency is not a test-case flake probability.' + retentionNote)]);
    host.append(note);
    renderComparisonChart(host, {
      title: 'AMD incident frequency - ' + state.analyticsWindow,
      subtitle: integer(chartRows.length) + ' exact pairs with current incidents; ' + (windowInfo.completeAggregate ? 'complete 30-day AMD burden beside CUDA equivalents' : 'largest recent AMD regressions first') + '; zero-incident groups remain in the table',
      key: 'analytics-platform-flakes',
      rows: chartRows.slice(0, 12),
      emptyTitle: 'No incidents in exact AMD/CUDA pairs for this window.',
      emptyMessage: 'Active zero-incident groups remain in the table; choose a longer window for historical burden.',
      amdValue: function (row) { return row.amd.incident_rate_pct; },
      cudaValue: function (row) { return row.cuda.incident_rate_pct; },
      axis: 'Incident frequency (%)',
      tooltipLabel: function (row, datasetIndex) {
        const side = datasetIndex === 0 ? row.amd : row.cuda;
        return (datasetIndex === 0 ? 'AMD: ' : 'Matched CUDA: ') + comparisonCountRate(side, 'incidents', 'incident_rate_pct');
      },
      evidenceSummary: function (row) { return comparisonPercent(row.amd, 'incident_rate_pct') + ' AMD - ' + comparisonPercent(row.cuda, 'incident_rate_pct') + ' CUDA'; },
      ops: ops, reliability: reliability, retry: retry, focus: 'flakes',
    });
    host.append(compactTablePanel('AMD incident comparison', integer(active.length) + ' active AMD groups - ' + integer(comparable.length) + ' active exact CUDA pairs', comparisonFlakeColumns(ops, reliability, retry), sorted, {
      id: 'flake-comparison-browser',
      limit: 12,
      alwaysBrowse: true,
      browserSubtitle: 'Exact counts, percentages, prior-window movement, and matched CUDA context',
      searchPlaceholder: 'Filter AMD group, CUDA equivalent, hardware, or queue',
      searchText: comparisonSearchText,
      geometry: {name: 'flake-comparison', minWidth: '1260px'},
    }));
    renderGroupHistoryExplorer(host, reliabilityCatalog(reliability), ops, reliability);
  }

  function comparisonSearchText(row) {
    return [row.label, (row.amd.hardware || []).join(' '), (row.cuda.hardware || []).join(' '), (row.amd.queues || []).join(' '), (row.cuda.queues || []).join(' ')].join(' ');
  }

  function comparisonFlakeColumns(ops, reliability, retry) {
    return [
      {label: 'AMD test group and CUDA equivalent', sticky: true, width: '320px', render: function (row) { return comparisonGroupCell(row, ops, reliability, retry, 'flakes'); }},
      {label: 'Match', width: '140px', render: function (row) { return linkButton(comparisonMatchLabel(row), function () { openPlatformComparisonDetail(row, ops, reliability, retry, 'flakes'); }); }},
      {label: 'AMD incidents / attempts', numeric: true, width: '175px', render: function (row) { return linkButton(comparisonCountRate(row.amd, 'incidents', 'incident_rate_pct'), function () { openPlatformComparisonDetail(row, ops, reliability, retry, 'flakes'); }); }},
      {label: 'vs prior window', numeric: true, width: '125px', render: function (row) { const control = linkButton(comparisonDelta(row.amd_incident_change_pp, ' pp'), function () { openPlatformComparisonDetail(row, ops, reliability, retry, 'flakes'); }); control.classList.add('ops-comparison-delta', comparisonTone(row.amd_incident_change_pp, 0)); return control; }},
      {label: 'CUDA incidents / attempts', numeric: true, width: '175px', render: function (row) { return linkButton(comparisonCountRate(row.cuda, 'incidents', 'incident_rate_pct'), function () { openPlatformComparisonDetail(row, ops, reliability, retry, 'flakes'); }); }},
      {label: 'AMD / CUDA gap', numeric: true, width: '120px', render: function (row) { const valueText = comparisonDelta(row.incident_rate_delta_pp, ' pp'); const control = linkButton(valueText, function () { openPlatformComparisonDetail(row, ops, reliability, retry, 'flakes'); }); control.classList.add('ops-comparison-delta', comparisonTone(row.incident_rate_delta_pp, 5)); return control; }},
      {label: 'AMD attempts / 100 builds', numeric: true, width: '150px', render: function (row) { return linkButton(comparisonPercent(row.amd, 'attempts_per_100_builds'), function () { openPlatformComparisonDetail(row, ops, reliability, retry, 'flakes'); }); }},
      {label: 'Evidence', width: '90px', render: function (row) { return linkButton('Inspect', function () { openPlatformComparisonDetail(row, ops, reliability, retry, 'flakes'); }, 'Inspect exact AMD and CUDA variants'); }},
    ];
  }

  function renderPlatformRetries(host, comparison, ops, reliability, retry) {
    const rows = comparison.rows.slice();
    const summary = comparison.summary || {};
    const amd = summary.amd || {};
    const pairedAmd = summary.comparable_amd || {};
    const cuda = summary.matched_cuda || {};
    const sorted = rows.slice().sort(function (a, b) {
      const regressionDelta = Number(b.amd_retry_change_pp || 0) - Number(a.amd_retry_change_pp || 0);
      return regressionDelta || Number(b.amd.retry_frequency_pct || 0) - Number(a.amd.retry_frequency_pct || 0) || Number(b.amd.child_retry_attempts || 0) - Number(a.amd.child_retry_attempts || 0) || a.label.localeCompare(b.label);
    });
    const active = sorted.filter(function (row) { return Number(row.amd.runs || 0) > 0; });
    const comparable = active.filter(function (row) { return row.comparison_eligible; });
    const chartRows = comparable.filter(function (row) {
      return Number(row.amd.child_retry_attempts || 0) > 0
        || Number(row.cuda.child_retry_attempts || 0) > 0
        || Number(row.amd.recovered_chains || 0) > 0;
    });
    const windowInfo = comparison.window || {};
    analyticsWindowControl(host, comparison);
    host.append(statusStrip([
      {label: 'AMD CHILD RETRIES', value: integer(amd.child_retry_attempts), meta: integer(amd.retry_involved_attempts) + ' total retry-involved attempts', tone: Number(amd.child_retry_attempts) ? 'is-warning' : 'is-success'},
      {label: 'AMD CHILD RETRY SHARE', value: comparisonPercent(amd, 'retry_frequency_pct'), meta: integer(amd.child_retry_attempts) + ' / ' + integer(amd.runs) + ' terminal attempts'},
      {label: 'REGRESSED VS PRIOR', value: windowInfo.completeAggregate ? '-' : integer(summary.regressed_retry_group_count), meta: windowInfo.completeAggregate ? 'choose 1h-7d for movement' : 'groups with a higher retry share', tone: Number(summary.regressed_retry_group_count) ? 'is-danger' : 'is-success'},
      {label: 'RECOVERED / PAIRED CUDA', value: integer(amd.recovered_chains) + ' / ' + comparisonPercent(cuda, 'retry_frequency_pct'), meta: comparisonPercent(amd, 'retry_recovery_rate_pct') + ' AMD recovery rate - ' + integer(comparable.length) + ' active pairs'},
    ]));
    const note = n('div', 'ops-evidence-note is-info');
    const retryRetention = Number(summary.history_incomplete_variant_count || 0)
      ? ' ' + integer(summary.history_incomplete_variant_count) + ' high-frequency variants reached the retained-history cap.' : '';
    add(note, [n('strong', '', 'Explicit Buildkite retry metadata only. '), n('span', '', (windowInfo.completeAggregate ? 'The 30-day selection uses complete aggregate retry counters.' : 'Movement compares timestamped child retries with the immediately preceding equal-length window.') + ' Recovery means an exact failed attempt linked to a passing retry; mixed outcomes alone are not counted.' + retryRetention)]);
    host.append(note);
    renderComparisonChart(host, {
      title: 'AMD retry burden - ' + state.analyticsWindow,
      subtitle: integer(chartRows.length) + ' exact pairs with retry activity; child retry and AMD recovery shares are shown; zero-retry groups remain in the table',
      key: 'analytics-platform-retries',
      rows: chartRows.slice(0, 12),
      emptyTitle: 'No explicit child retries in exact AMD/CUDA pairs for this window.',
      emptyMessage: 'Active zero-retry groups remain in the table; choose a longer window for historical burden.',
      datasets: [
        {label: 'AMD child retry share', value: function (row) { return row.amd.retry_frequency_pct; }, backgroundColor: '#e3a63a'},
        {label: 'Matched CUDA share', value: function (row) { return row.cuda.retry_frequency_pct; }, backgroundColor: '#5ca8ff'},
        {label: 'AMD recovered share', value: function (row) { return comparisonRecoveryShare(row.amd); }, backgroundColor: '#35bb78'},
      ],
      axis: 'Retry frequency (%)',
      tooltipLabel: function (row, datasetIndex) {
        if (datasetIndex === 2) return 'AMD recovered: ' + integer(row.amd.recovered_chains) + ' / ' + integer(row.amd.runs) + ' - ' + value(comparisonRecoveryShare(row.amd) === null ? null : comparisonRecoveryShare(row.amd).toFixed(1) + '%');
        const side = datasetIndex === 0 ? row.amd : row.cuda;
        return (datasetIndex === 0 ? 'AMD: ' : 'Matched CUDA: ') + comparisonCountRate(side, 'child_retry_attempts', 'retry_frequency_pct');
      },
      evidenceSummary: function (row) { return comparisonPercent(row.amd, 'retry_frequency_pct') + ' AMD - ' + comparisonPercent(row.cuda, 'retry_frequency_pct') + ' CUDA'; },
      ops: ops, reliability: reliability, retry: retry, focus: 'retries',
    });
    const columns = [
      {label: 'AMD test group and CUDA equivalent', sticky: true, width: '320px', render: function (row) { return comparisonGroupCell(row, ops, reliability, retry, 'retries'); }},
      {label: 'Match', width: '140px', render: function (row) { return linkButton(comparisonMatchLabel(row), function () { openPlatformComparisonDetail(row, ops, reliability, retry, 'retries'); }); }},
      {label: 'AMD child retries / attempts', numeric: true, width: '180px', render: function (row) { return linkButton(comparisonCountRate(row.amd, 'child_retry_attempts', 'retry_frequency_pct'), function () { openPlatformComparisonDetail(row, ops, reliability, retry, 'retries'); }); }},
      {label: 'vs prior window', numeric: true, width: '125px', render: function (row) { const control = linkButton(comparisonDelta(row.amd_retry_change_pp, ' pp'), function () { openPlatformComparisonDetail(row, ops, reliability, retry, 'retries'); }); control.classList.add('ops-comparison-delta', comparisonTone(row.amd_retry_change_pp, 0)); return control; }},
      {label: 'AMD recovered', numeric: true, width: '115px', render: function (row) { return linkButton(integer(row.amd.recovered_chains), function () { openPlatformComparisonDetail(row, ops, reliability, retry, 'retries'); }); }},
      {label: 'CUDA child retries / attempts', numeric: true, width: '180px', render: function (row) { return linkButton(comparisonCountRate(row.cuda, 'child_retry_attempts', 'retry_frequency_pct'), function () { openPlatformComparisonDetail(row, ops, reliability, retry, 'retries'); }); }},
      {label: 'AMD / CUDA gap', numeric: true, width: '120px', render: function (row) { const control = linkButton(comparisonDelta(row.retry_frequency_delta_pp, ' pp'), function () { openPlatformComparisonDetail(row, ops, reliability, retry, 'retries'); }); control.classList.add('ops-comparison-delta', comparisonTone(row.retry_frequency_delta_pp, 2)); return control; }},
      {label: 'Evidence', width: '90px', render: function (row) { return linkButton('Inspect', function () { openPlatformComparisonDetail(row, ops, reliability, retry, 'retries'); }, 'Inspect exact retry attempts and recoveries'); }},
    ];
    host.append(compactTablePanel('AMD retry comparison', integer(active.length) + ' active AMD groups - ' + integer(comparable.length) + ' active exact CUDA pairs', columns, sorted, {
      id: 'retry-comparison-browser',
      limit: 12,
      alwaysBrowse: true,
      browserSubtitle: 'Exact child-retry counts, prior-window movement, recoveries, and matched CUDA evidence',
      searchPlaceholder: 'Filter AMD group, CUDA equivalent, hardware, or queue',
      searchText: comparisonSearchText,
      geometry: {name: 'retry-comparison', minWidth: '1320px'},
    }));
  }

  function renderPlatformLatency(host, comparison, ops, reliability, retry) {
    const rows = comparison.rows.filter(function (row) { return Number.isFinite(Number(row.amd.worst_p90_duration_mins)); });
    const sorted = rows.slice().sort(function (a, b) { return Number(b.amd.worst_p90_duration_mins || 0) - Number(a.amd.worst_p90_duration_mins || 0) || a.label.localeCompare(b.label); });
    const comparable = sorted.filter(function (row) {
      return row.comparison_eligible
        && row.amd.duration_basis === 'job_wall'
        && row.cuda.duration_basis === 'job_wall'
        && Number.isFinite(Number(row.cuda.worst_p90_duration_mins));
    });
    const p90Values = comparable.map(function (row) { return Number(row.amd.worst_p90_duration_mins); }).filter(Number.isFinite).sort(function (a, b) { return a - b; });
    const typical = percentileValue(p90Values, 0.5);
    const slower = comparable.filter(function (row) { return Number(row.worst_p90_delta_mins) > 5; });
    const slowest = comparable[0] || {amd: {}, cuda: {}};
    host.append(statusStrip([
      {label: 'AMD GROUPS TIMED', value: integer(rows.length), meta: integer(comparable.length) + ' exact CUDA pairs'},
      {label: 'TYPICAL PAIRED AMD P90', value: duration(typical), meta: 'median of exact-pair AMD group p90s'},
      {label: 'SLOWEST AMD P90', value: duration(slowest.amd.worst_p90_duration_mins), meta: value(slowest.label, 'No duration evidence'), tone: 'is-warning', onOpen: function () { if (slowest.label) openPlatformComparisonDetail(slowest, ops, reliability, retry, 'latency'); }},
      {label: 'AMD SLOWER THAN CUDA', value: integer(slower.length), meta: 'groups with a p90 gap greater than 5 minutes', tone: slower.length ? 'is-warning' : 'is-success'},
    ]));
    const note = n('div', 'ops-evidence-note is-info');
    add(note, [n('strong', '', 'AMD completion time first. '), n('span', '', 'Comparative deltas use one-to-one explicit NVIDIA pairs with job-wall timing. Review-required references remain in the table without a delta. Queue wait stays separate in exact group evidence.')]);
    host.append(note);
    renderComparisonChart(host, {
      title: 'Slowest AMD test groups',
      subtitle: 'Worst AMD hardware-variant p90 beside the exact CUDA-name equivalent',
      key: 'analytics-platform-latency',
      rows: comparable.slice(0, 12),
      amdValue: function (row) { return row.amd.worst_p90_duration_mins; },
      cudaValue: function (row) { return row.cuda.worst_p90_duration_mins; },
      axis: 'Wall completion p90 (minutes)',
      evidenceSummary: function (row) { return duration(row.amd.worst_p90_duration_mins) + ' AMD - ' + duration(row.cuda.worst_p90_duration_mins) + ' CUDA'; },
      ops: ops, reliability: reliability, retry: retry, focus: 'latency',
    });
    const columns = [
      {label: 'AMD test group and CUDA equivalent', sticky: true, width: '340px', render: function (row) { return comparisonGroupCell(row, ops, reliability, retry, 'latency'); }},
      {label: 'Match', width: '150px', render: function (row) { return linkButton(comparisonMatchLabel(row), function () { openPlatformComparisonDetail(row, ops, reliability, retry, 'latency'); }); }},
      {label: 'AMD hardware', width: '120px', render: function (row) { return linkButton((row.amd.hardware || []).map(hardwareDisplayLabel).join(', '), function () { openPlatformComparisonDetail(row, ops, reliability, retry, 'latency'); }); }},
      {label: 'AMD worst p90', numeric: true, width: '120px', render: function (row) { return linkButton(duration(row.amd.worst_p90_duration_mins), function () { openPlatformComparisonDetail(row, ops, reliability, retry, 'latency'); }); }},
      {label: 'CUDA worst p90', numeric: true, width: '120px', render: function (row) { return linkButton(duration(row.cuda.worst_p90_duration_mins), function () { openPlatformComparisonDetail(row, ops, reliability, retry, 'latency'); }); }},
      {label: 'AMD delta', numeric: true, width: '100px', render: function (row) { const control = linkButton(comparisonDelta(row.worst_p90_delta_mins, 'm'), function () { openPlatformComparisonDetail(row, ops, reliability, retry, 'latency'); }); control.classList.add('ops-comparison-delta', comparisonTone(row.worst_p90_delta_mins, 5)); return control; }},
      {label: 'AMD attempts / 100 builds', numeric: true, width: '150px', render: function (row) { return linkButton(comparisonPercent(row.amd, 'attempts_per_100_builds'), function () { openPlatformComparisonDetail(row, ops, reliability, retry, 'latency'); }); }},
      {label: 'Evidence', width: '90px', render: function (row) { return linkButton('Inspect', function () { openPlatformComparisonDetail(row, ops, reliability, retry, 'latency'); }, 'Inspect exact AMD and CUDA timing evidence'); }},
    ];
    host.append(compactTablePanel('AMD completion-time comparison', integer(rows.length) + ' AMD base groups - ' + integer(comparable.length) + ' exact CUDA pairs', columns, sorted, {
      id: 'latency-comparison-browser',
      limit: 12,
      alwaysBrowse: true,
      browserSubtitle: 'AMD-first wall completion with exact CUDA counterparts and Buildkite evidence',
      searchPlaceholder: 'Filter AMD group, CUDA equivalent, hardware, or queue',
      searchText: comparisonSearchText,
      geometry: {name: 'latency-comparison', minWidth: '1270px'},
    }));
  }

  async function renderAnalytics(host, ops) {
    const reliability = canonicalReliability(ops);
    const amdHealth = ops.amd_test_health || {};
    const retry = reliability.retry_analysis || {};
    const baseComparison = platformComparison(reliability);
    const comparison = ['flakes', 'retries'].includes(state.analyticsView)
      ? platformComparisonForWindow(baseComparison, reliability, retry, ops, state.analyticsWindow)
      : baseComparison;
    const nightly = nightlyForPipeline(ops, state.analyticsPipeline);
    const builds = nightly.builds || [];
    const nightlyName = nightlyDisplayName(nightly, state.analyticsPipeline);
    const scope = reliabilityScopeInfo(reliability);
    const analyticsObservedAt = state.analyticsView === 'agent-health'
      ? (ops.amd_agent_health || {}).generated_at
      : state.analyticsView === 'groups'
        ? ((amdHealth.summary || {}).latest_observed_at || ops.generated_at)
        : ops.generated_at;
    add(host, pageHeader('CI Analytics', 'AMD health is primary. Flakes, retries, and latency compare upstream AMD mirror jobs only with their exact CUDA-name equivalents.', analyticsObservedAt));
    host.append(segmented([
      {id: 'groups', label: 'AMD test health'}, {id: 'flakes', label: 'Flake comparison'},
      {id: 'retries', label: 'Retry comparison'}, {id: 'latency', label: 'Latency comparison'},
      {id: 'nightlies', label: 'AMD nightlies'}, {id: 'agent-health', label: 'CI agent health'},
    ], state.analyticsView, function (id) { setRouteState('ci-analytics', 'analyticsView', id, 'analytics_view'); }, 'CI Analytics view'));
    if (state.analyticsView === 'groups') {
      renderAmdHealth(host, amdHealth);
      return;
    }

    if (state.analyticsView === 'agent-health') {
      renderAmdAgentHealth(host, ops.amd_agent_health || {});
      return;
    }

    if ((!scope.available || !comparison.available) && state.analyticsView !== 'nightlies') {
      const unavailable = n('div', 'ops-evidence-note is-warning');
      add(unavailable, [n('strong', '', 'AMD/CUDA comparison unavailable. '), n('span', '', scope.detail + '. The dashboard will not substitute unmatched hardware or a different pipeline.')]);
      host.append(unavailable);
      return;
    }

    if (state.analyticsView === 'flakes') {
      renderPlatformFlakes(host, comparison, ops, reliability, retry);
      return;
    }

    if (state.analyticsView === 'nightlies') {
      const controls = n('div', 'ops-toolbar ops-analytics-nightly-toolbar');
      controls.append(segmented([
        {id: 'amd-ci', label: 'AMD'},
        {id: 'ci', label: 'Upstream parity'},
      ], state.analyticsPipeline, function (pipeline) { setRouteState('ci-analytics', 'analyticsPipeline', pipeline, 'analytics_pipeline'); }, 'Nightly pipeline'));
      host.append(controls);
      const latestNightly = builds[0] || {transitions: {new: [], recurring: [], fixed: []}};
      host.append(statusStrip([
        {label: 'LATEST ' + nightlyName.toUpperCase() + ' NIGHTLY', value: latestNightly.number ? '#' + latestNightly.number : '-', meta: latestNightly.created_at ? shortDate(latestNightly.created_at) : 'No completed nightly', tone: toneForState(latestNightly.state), url: latestNightly.number ? exactPipelineBuildUrl(latestNightly, state.analyticsPipeline) : null},
        {label: 'JOB VARIANTS OBSERVED', value: integer(latestNightly.total_groups), meta: 'exact jobs in the latest completed nightly', onOpen: function () { if (latestNightly.number) openBuildDetail(latestNightly, nightlyName + ' build #' + value(latestNightly.number)); }},
        {label: 'NEW INCIDENT VARIANTS', value: integer((latestNightly.transitions.new || []).length), meta: integer((latestNightly.transitions.recurring || []).length) + ' recurring variants', tone: (latestNightly.transitions.new || []).length ? 'is-danger' : 'is-success', onOpen: function () { if (latestNightly.number) openBuildDetail(latestNightly, nightlyName + ' build #' + value(latestNightly.number)); }},
        {label: 'FIXED SINCE PRIOR', value: integer((latestNightly.transitions.fixed || []).length), meta: integer(builds.length) + ' nightlies retained', tone: (latestNightly.transitions.fixed || []).length ? 'is-success' : 'is-neutral', onOpen: function () { if (latestNightly.number) openBuildDetail(latestNightly, nightlyName + ' build #' + value(latestNightly.number)); }},
      ]));
      const nightlyNote = n('div', 'ops-evidence-note is-info');
      add(nightlyNote, [n('strong', '', state.analyticsPipeline === 'amd-ci' ? 'AMD nightly history. ' : 'Upstream parity history. '), n('span', '', state.analyticsPipeline === 'amd-ci' ? 'AMD is the default operational signal; each build and transition links to its exact evidence.' : 'This alternate view is retained for parity checks and does not replace AMD health.')]);
      host.append(nightlyNote);
      const cp = chartPanel(nightlyName + ' nightly regressions', 'New, recurring, and fixed exact job variants per completed nightly', 'analytics-trend');
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
        add(unavailable, [n('strong', '', 'Explicit retry comparison unavailable. '), n('span', '', ((retry.provenance || {}).reason || 'Complete Buildkite retry metadata was not retained for the upstream cohort.'))]);
        host.append(unavailable);
        return;
      }
      renderPlatformRetries(host, comparison, ops, reliability, retry);
      return;
    }

    if (state.analyticsView === 'latency') {
      renderPlatformLatency(host, comparison, ops, reliability, retry);
      return;
    }

    if (false) {
      if (retry.available !== true) {
        const unavailable = n('div', 'ops-evidence-note is-warning');
        add(unavailable, [n('strong', '', 'Upstream retry ledger unavailable. '), n('span', '', ((retry.provenance || {}).reason || 'Complete explicit Buildkite retry metadata was not retained; compacted group history was not substituted.'))]);
        host.append(unavailable);
        return;
      }
      const retryAttempts = retry.retry_attempts || [];
      const recoveries = retry.failed_then_passed_recoveries || [];
      const outcomeCounts = new Map();
      const retriesByGroup = new Map();
      retryAttempts.forEach(function (row) {
        const outcome = historyOutcomeLabel(row);
        outcomeCounts.set(outcome, (outcomeCounts.get(outcome) || 0) + 1);
        const group = row.name || 'Unnamed retry';
        if (!retriesByGroup.has(group)) retriesByGroup.set(group, {name: group, attempts: 0, recoveries: 0, rows: []});
        const item = retriesByGroup.get(group);
        item.attempts += 1;
        item.rows.push(row);
      });
      recoveries.forEach(function (row) {
        const item = retriesByGroup.get(row.name || 'Unnamed retry chain');
        if (item) item.recoveries += 1;
      });
      const topRetried = Array.from(retriesByGroup.values()).sort(function (a, b) { return b.attempts - a.attempts || a.name.localeCompare(b.name); }).slice(0, 12);
      const retryCharts = n('div', 'ops-grid ops-grid-2');
      const outcomeChart = chartPanel('Retry attempt outcomes', 'Terminal state of every explicit upstream retry attempt', 'analytics-retry-outcomes');
      const groupChart = chartPanel('Most frequently retried groups', 'Attempt volume with confirmed fail-to-pass recovery count', 'analytics-retry-groups');
      add(retryCharts, [outcomeChart.root, groupChart.root]);
      host.append(retryCharts);
      requestAnimationFrame(function () {
        const outcomes = Array.from(outcomeCounts.entries()).sort(function (a, b) { return b[1] - a[1]; });
        drawChart('analytics-retry-outcomes', outcomeChart.canvas, {
          type: 'bar',
          data: {labels: outcomes.map(function (row) { return row[0]; }), datasets: [{label: 'Attempts', data: outcomes.map(function (row) { return row[1]; }), backgroundColor: outcomes.map(function (row) { return row[0] === 'Passed' ? '#35bb78' : row[0].includes('Soft') ? '#e3a63a' : '#e06464'; })}]},
          options: {scales: {y: {beginAtZero: true, title: {display: true, text: 'Explicit attempts'}}, x: {grid: {display: false}}}},
          evidenceTitle: 'Explicit retry attempts by terminal outcome',
          evidence: outcomes.map(function (row) { return {label: row[0], valueSummary: integer(row[1]) + ' attempts', sources: [{label: 'Open published retry ledger', url: SOURCE_ASSETS.operations}]}; }),
        });
        drawChart('analytics-retry-groups', groupChart.canvas, {
          type: 'bar',
          data: {labels: topRetried.map(function (row) { return compactChartLabel(row, 36); }), datasets: [
            {label: 'Attempts', data: topRetried.map(function (row) { return row.attempts; }), backgroundColor: '#5ca8ff'},
            {label: 'Recovered chains', data: topRetried.map(function (row) { return row.recoveries; }), backgroundColor: '#35bb78'},
          ]},
          options: {indexAxis: 'y', scales: {x: {beginAtZero: true, title: {display: true, text: 'Count'}}, y: {grid: {display: false}}}},
          evidenceTitle: 'Groups with the most explicit retries',
          evidence: topRetried.map(function (row) { return {label: row.name, valueSummary: integer(row.attempts) + ' attempts - ' + integer(row.recoveries) + ' recovered chains', url: exactPipelineEvidenceUrl(row.rows[row.rows.length - 1], 'ci')}; }),
        });
      });
      const attemptsColumns = [
        {label: 'Build', width: '110px', render: function (r) { return externalLink('#' + value(r.build_number), exactReliabilityBuildUrl(r), 'ops-mono'); }},
        {label: 'Retried job', sticky: true, width: '360px', render: function (r) { return externalLink(r.name || 'Unnamed retry', exactPipelineEvidenceUrl(r, 'ci')); }},
        {label: 'Result', width: '120px', render: function (r) { return linkedBadge(r.state || 'unknown', exactPipelineEvidenceUrl(r, 'ci')); }},
        {label: 'Retry type', width: '130px', render: function (r) { return linkedBadge(r.retry_type || 'explicit', exactPipelineEvidenceUrl(r, 'ci'), null, 'is-info'); }},
        {label: 'Job ID', width: '300px', render: function (r) { return externalLink(value(r.job_id), exactPipelineEvidenceUrl(r, 'ci'), 'ops-mono'); }},
        {label: 'Evidence', width: '170px', render: function (r) { return externalLink('Open exact attempt', exactPipelineEvidenceUrl(r, 'ci')); }},
      ];
      host.append(compactTablePanel('Upstream explicit retry attempts', integer(retryAttempts.length) + ' Buildkite attempts across ' + integer(retrySummary.builds_with_retries) + ' builds', attemptsColumns, retryAttempts, {
        id: 'retry-attempt-browser',
        limit: 12,
        browserSubtitle: 'Every row opens its exact upstream Buildkite job',
        searchPlaceholder: 'Filter job, build, result, retry type, or ID',
        searchText: function (row) { return [row.name, row.build_number, row.state, row.retry_type, row.job_id].join(' '); },
        geometry: {name: 'retry-attempts', minWidth: '1190px'},
      }));
      const recoveryColumns = [
        {label: 'Build', width: '110px', render: function (r) { return externalLink('#' + value(r.build_number), exactReliabilityBuildUrl(r), 'ops-mono'); }},
        {label: 'Retried job', sticky: true, width: '390px', render: function (r) { return externalLink(r.name || 'Unnamed retry chain', exactPipelineEvidenceUrl({job_url: r.failed_url || r.passed_url, build_number: r.build_number}, 'ci')); }},
        {label: 'Failed attempt', width: '190px', render: function (r) { return externalLink('Open failed log', exactPipelineEvidenceUrl({job_url: r.failed_url, build_number: r.build_number}, 'ci')); }},
        {label: 'Passing retry', width: '190px', render: function (r) { return externalLink('Open passing log', exactPipelineEvidenceUrl({job_url: r.passed_url, build_number: r.build_number}, 'ci')); }},
      ];
      host.append(compactTablePanel('Upstream recovered fail-to-pass chains', integer(recoveries.length) + ' chains confirmed by explicit retry metadata', recoveryColumns, recoveries, {
        id: 'retry-recovery-browser',
        limit: 10,
        browserSubtitle: 'Failed and passing attempts remain separate exact Buildkite evidence',
        searchPlaceholder: 'Filter recovered job or build',
        searchText: function (row) { return [row.name, row.build_number].join(' '); },
        geometry: {name: 'retry-recoveries', minWidth: '880px'},
      }));
      return;
    }

    const latencyRows = (reliability.latency_rankings || {}).by_p90_duration || [];
    const slowestRows = latencyRows.slice(0, 15);
    const latencyChart = chartPanel('Slowest upstream test groups', 'p90 completion time; queue wait is shown separately when the source reports it', 'analytics-latency-ranking');
    host.append(latencyChart.root);
    requestAnimationFrame(function () {
      drawChart('analytics-latency-ranking', latencyChart.canvas, {
        type: 'bar',
        data: {labels: slowestRows.map(function (row) { return row.name; }), datasets: [
          {label: 'Median completion', data: slowestRows.map(function (row) { return row.median_dur; }), backgroundColor: '#5ca8ff'},
          {label: 'p90 completion', data: slowestRows.map(function (row) { return row.p90_dur; }), backgroundColor: '#e3a63a'},
        ]},
        options: {indexAxis: 'y', scales: {x: {beginAtZero: true, title: {display: true, text: 'Minutes'}}, y: {grid: {display: false}}}},
        evidenceTitle: 'Upstream groups ranked by p90 completion',
        evidence: slowestRows.map(function (row) { const full = groupReliabilityByRef(reliability, row.evidence_ref); return {label: row.name, valueSummary: 'p90 ' + duration(row.p90_dur) + ' - median ' + duration(row.median_dur), sources: [{label: 'Open published all-main history', url: SOURCE_ASSETS.operations}], onOpen: function () { if (full) openGroupDetail(row, ops, full, reliability); }}; }),
      });
    });
    const latencyColumns = [
      {label: 'Test group', sticky: true, width: '340px', render: function (r) { const full = groupReliabilityByRef(reliability, r.evidence_ref); return groupIdentityCell(full || r, function () { if (full) openGroupDetail(r, ops, full, reliability); }); }},
      {label: 'Runs', numeric: true, width: '90px', render: function (r) { const full = groupReliabilityByRef(reliability, r.evidence_ref); return linkButton(integer(r.runs), function () { if (full) openGroupDetail(r, ops, full, reliability); }, 'Inspect run history for evidence ID ' + value(r.evidence_ref)); }},
      {label: 'Median completion', numeric: true, width: '150px', render: function (r) { const full = groupReliabilityByRef(reliability, r.evidence_ref); return linkButton(duration(r.median_dur), function () { if (full) openGroupDetail(r, ops, full, reliability); }, 'Inspect median completion evidence for ID ' + value(r.evidence_ref)); }},
      {label: 'p90 completion', numeric: true, width: '140px', render: function (r) { const full = groupReliabilityByRef(reliability, r.evidence_ref); return linkButton(duration(r.p90_dur), function () { if (full) openGroupDetail(r, ops, full, reliability); }, 'Inspect p90 completion evidence for ID ' + value(r.evidence_ref)); }},
      {label: 'Maximum', numeric: true, width: '120px', render: function (r) { const full = groupReliabilityByRef(reliability, r.evidence_ref); return linkButton(duration(r.max_dur), function () { if (full) openGroupDetail(r, ops, full, reliability); }, 'Inspect maximum completion evidence for ID ' + value(r.evidence_ref)); }},
      {label: 'Incident rate', numeric: true, width: '130px', render: function (r) { const full = groupReliabilityByRef(reliability, r.evidence_ref); return linkButton(Number.isFinite(Number(r.fail_rate)) ? Number(r.fail_rate).toFixed(1) + '%' : '-', function () { if (full) openGroupDetail(r, ops, full, reliability); }, 'Inspect incident evidence for ID ' + value(r.evidence_ref)); }},
      {label: 'Queues', width: '220px', render: function (r) { const names = r.queues || []; const wrap = n('div', 'ops-inline-links'); names.forEach(function (name) { wrap.append(linkButton(name, function () { navigateTo('ci-queue', {queueView: 'history', queueHistoryQueue: name, queueScope: isAmdQueue(name) ? 'amd' : 'all'}); })); }); return names.length ? wrap : n('span', 'ops-cell-muted', '-'); }},
      {label: 'Evidence', width: '150px', render: function (r) { const rel = groupReliabilityByRef(reliability, r.evidence_ref); return rel ? linkButton(integer(evidenceObservations(rel).length) + ' runs', function () { openGroupDetail(r, ops, rel, reliability); }, 'Inspect exact observations for evidence ID ' + value(r.evidence_ref)) : externalLink('Published ranking', SOURCE_ASSETS.operations); }},
    ];
    host.append(compactTablePanel('Completion-time evidence', integer(latencyRows.length) + ' strict upstream groups ranked by p90', latencyColumns, latencyRows, {
      id: 'latency-browser',
      limit: 15,
      browserSubtitle: 'Completion time per test group in ' + scope.label.toLowerCase() + '; queue wait remains separate',
      searchPlaceholder: 'Filter test group, hardware, queue, or evidence ID',
      searchText: function (row) { return [row.name, row.hardware, (row.queues || []).join(' '), row.evidence_ref].join(' '); },
      geometry: {name: 'latency', minWidth: '1340px'},
    }));
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
    function selectPerfModel(modelName, viewName) {
      state.perfModel = modelName;
      if (viewName) state.perfView = viewName;
      setQueryValue('perf_model', state.perfModel);
      setQueryValue('perf_view', state.perfView);
      render('ci-perf-eval', true);
    }
    if (state.perfModel !== 'all') {
      const back = button('\u2190 All models', function () { selectPerfModel('all'); });
      back.classList.add('ops-perf-back');
      back.setAttribute('aria-label', 'Back to all performance models');
      toolbar.append(back);
    }
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
    modelSelect.addEventListener('change', function () { selectPerfModel(modelSelect.value); });
    modelField.append(modelSelect);
    toolbar.append(modelField);

    const devices = summary.amd_devices || Array.from(new Set(models.flatMap(function (model) { return model.devices || []; })));
    if (devices.length) {
      const deviceField = n('div', 'ops-field ops-perf-filter');
      deviceField.append(n('span', 'ops-field-label', 'Hardware'));
      deviceField.append(segmented([{id: 'all', label: 'All'}].concat(devices.map(function (device) {
        return {id: String(device).toLowerCase(), label: String(device).toUpperCase()};
      })), state.perfDevice, function (id) { setRouteState('ci-perf-eval', 'perfDevice', id, 'perf_device'); }, 'Performance hardware filter'));
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

    if (state.perfModel === 'all') {
      const modelRows = filteredModels.map(function (model) {
        const performanceMetrics = (model.perf_configs || []).flatMap(function (config) { return Object.values(config.metrics || {}); });
        const accuracyMetrics = model.accuracy_tasks || [];
        const regressions = performanceMetrics.concat(accuracyMetrics).filter(function (metric) { return metric.status === 'bad'; }).length;
        const improvements = performanceMetrics.concat(accuracyMetrics).filter(function (metric) { return metric.status === 'good'; }).length;
        return {model: model, performanceMetrics: performanceMetrics.length, accuracyMetrics: accuracyMetrics.length, regressions: regressions, improvements: improvements};
      });
      const note = n('div', 'ops-evidence-note is-info');
      add(note, [n('strong', '', 'Model summary. '), n('span', '', 'Choose one model to render its detailed metric histories; the dashboard does not create every chart at once.')]);
      host.append(note);
      host.append(panel('AMD model overview', integer(modelRows.length) + ' retained model families', dataTable([
        {label: 'Model', sticky: true, width: '320px', render: function (row) { return linkButton(row.model.model, function () { selectPerfModel(row.model.model); }, 'Open detailed metrics for ' + row.model.model); }},
        {label: 'Hardware', width: '160px', render: function (row) { return (row.model.devices || []).map(function (device) { return String(device).toUpperCase(); }).join(' / ') || '-'; }},
        {label: 'Nightlies', numeric: true, width: '100px', render: function (row) { return linkButton(integer(row.model.nightly_count), function () { selectPerfModel(row.model.model); }); }},
        {label: 'Performance metrics', numeric: true, width: '150px', render: function (row) { return linkButton(integer(row.performanceMetrics), function () { selectPerfModel(row.model.model, 'performance'); }); }},
        {label: 'Accuracy metrics', numeric: true, width: '140px', render: function (row) { return linkButton(integer(row.accuracyMetrics), function () { selectPerfModel(row.model.model, 'accuracy'); }); }},
        {label: 'Regressions', numeric: true, width: '110px', render: function (row) { return linkedBadge(integer(row.regressions), (row.model.latest || {}).build_url, null, row.regressions ? 'is-danger' : 'is-success'); }},
        {label: 'Improvements', numeric: true, width: '120px', render: function (row) { return linkedBadge(integer(row.improvements), (row.model.latest || {}).build_url, null, row.improvements ? 'is-success' : 'is-neutral'); }},
        {label: 'Latest build', width: '130px', render: function (row) { return externalLink((row.model.latest || {}).build_number ? '#' + row.model.latest.build_number : 'Build', (row.model.latest || {}).build_url, 'ops-mono'); }},
      ], modelRows, 'Select a model row to open its detailed performance or accuracy histories', {name: 'perf-model-summary', minWidth: '1120px'})));
      return;
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

  function waitSourceDetail(row, metric) {
    const family = waitSource(row || {}, metric);
    if (!family) return null;
    const key = String(family).toLowerCase();
    const provider = key === 'official_wait' ? row.official_wait_source : key === 'sample_wait' ? row.sample_wait_source : null;
    return provider && provider !== family ? family + ' - ' + provider : family;
  }

  function waitSampleCount(row) {
    const nested = (row || {}).sample_wait || {};
    const count = row && row.wait_sample_count !== undefined ? row.wait_sample_count : nested.count;
    return Number.isFinite(Number(count)) ? Number(count) : null;
  }

  function queueHasWaitMeasurement(row) {
    return ['p50', 'p95', 'p99'].some(function (metric) {
      const measured = waitValue(row || {}, metric);
      return measured !== null && measured !== undefined && Number.isFinite(Number(measured));
    });
  }

  function hasAgentMeasurement(row) {
    if (row.connected_agents === null || row.connected_agents === undefined || row.connected_agents === '' || !Number.isFinite(Number(row.connected_agents))) return false;
    if (row.connected_agents_available !== undefined) return row.connected_agents_available === true;
    const source = String(row.connected_agents_source || row.agent_count_source || row.metrics_source || row.count_source || '').toLowerCase();
    return !!source && !['active_jobs', 'webhook', 'job_scan', 'none', 'unknown'].includes(source);
  }

  function openQueueSnapshotDetail(snapshot, totals) {
    const source = snapshot.sources || snapshot.provenance || {};
    const resolved = totals || {};
    const selectedQueue = resolved.selectedQueue && resolved.selectedQueue !== 'fleet' ? resolved.selectedQueue : null;
    const scopeEntries = selectedQueue && Object.prototype.hasOwnProperty.call(snapshot.queues || {}, selectedQueue)
      ? [[selectedQueue, (snapshot.queues || {})[selectedQueue]]]
      : selectedQueues(snapshot, true);
    const rows = scopeEntries.filter(function (entry) {
      return queueIsActiveProblem(entry[1] || {}) || queueHasWaitMeasurement(entry[1] || {});
    }).map(function (entry) { return {name: entry[0], row: entry[1]}; });
    const running = Number.isFinite(Number(resolved.running)) ? Number(resolved.running) : scopeEntries.reduce(function (sum, entry) { return sum + Number((entry[1] || {}).running || 0); }, 0);
    const waiting = Number.isFinite(Number(resolved.waiting)) ? Number(resolved.waiting) : scopeEntries.reduce(function (sum, entry) { return sum + Number((entry[1] || {}).waiting || 0); }, 0);
    const queueCount = Number.isFinite(Number(resolved.queues)) ? Number(resolved.queues) : scopeEntries.length;
    const scopeLabel = selectedQueue || (state.queueScope === 'amd' ? 'All AMD queues combined' : 'All queues combined');
    const content = rows.length ? dataTable([
      {label: 'Queue', sticky: true, render: function (item) { return linkButton(item.name, function () { openQueueDetail(item.name, item.row, []); }); }},
      {label: 'Running', numeric: true, render: function (item) { return integer(item.row.running); }},
      {label: 'Waiting', numeric: true, render: function (item) { return integer(item.row.waiting); }},
      {label: 'p50', numeric: true, render: function (item) { return duration(waitValue(item.row, 'p50')); }},
      {label: 'p95 official/fallback', numeric: true, render: function (item) { return duration(waitValue(item.row, 'p95')); }},
      {label: 'p99 sampled', numeric: true, render: function (item) { return duration(waitValue(item.row, 'p99')); }},
      {label: 'Wait source', render: function (item) { return value([waitSourceDetail(item.row, 'p95'), waitSourceDetail(item.row, 'p99')].filter(Boolean).join(', ')); }},
    ], rows, integer(rows.length) + ' queues with activity or a wait measurement in this snapshot', {name: 'queue-snapshot', minWidth: '980px'}) : n('div', 'ops-empty', 'No queue activity or source-reported wait measurements exist in this snapshot.');
    openDetailDrawer({
      id: 'queue-snapshot-' + value(snapshot.ts),
      title: selectedQueue ? selectedQueue + ' snapshot' : 'Combined queue snapshot',
      subtitle: scopeLabel + ' - ' + shortDate(snapshot.ts),
      description: selectedQueue ? 'Point-in-time evidence for one named queue.' : 'Point-in-time evidence. Running and waiting are summed across the selected queues; every wait percentile below belongs to its named queue.',
      fields: [
        {label: 'Scope', value: scopeLabel},
        {label: 'Running across scope', value: integer(running)},
        {label: 'Waiting across scope', value: integer(waiting)},
        {label: 'Queues in scope', value: integer(queueCount)},
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
        return {queue: entry[0], value: waitValue(entry[1] || {}, metric), source: waitSource(entry[1] || {}, metric), sourceDetail: waitSourceDetail(entry[1] || {}, metric), sampleCount: waitSampleCount(entry[1] || {})};
      }).filter(function (row) {
        return row.value !== null && row.value !== undefined && Number.isFinite(Number(row.value));
      }).sort(function (a, b) { return Number(b.value) - Number(a.value) || a.queue.localeCompare(b.queue); });
    }
    function leaders(metric) {
      const ranked = highest(metric);
      if (!ranked.length) return {leader: {}, rows: []};
      const max = Number(ranked[0].value);
      return {leader: ranked[0], rows: ranked.filter(function (row) { return Number(row.value) === max; })};
    }
    const p50Rank = leaders('p50'), p95Rank = leaders('p95'), p99Rank = leaders('p99');
    const p50 = p50Rank.leader, p95 = p95Rank.leader, p99 = p99Rank.leader;
    return {
      ts: snapshot.ts,
      snapshot: snapshot,
      p50: p50.value !== undefined ? Number(p50.value) : null,
      p95: p95.value !== undefined ? Number(p95.value) : null,
      p99: p99.value !== undefined ? Number(p99.value) : null,
      p50Queue: p50.queue,
      p95Queue: p95.queue,
      p99Queue: p99.queue,
      p50Queues: p50Rank.rows.map(function (row) { return row.queue; }),
      p95Queues: p95Rank.rows.map(function (row) { return row.queue; }),
      p99Queues: p99Rank.rows.map(function (row) { return row.queue; }),
      p50Source: p50.source,
      p95Source: p95.source,
      p99Source: p99.source,
      p50SourceDetail: p50.sourceDetail,
      p95SourceDetail: p95.sourceDetail,
      p99SourceDetail: p99.sourceDetail,
      p50SampleCount: p50.sampleCount,
      p95SampleCount: p95.sampleCount,
      p99SampleCount: p99.sampleCount,
    };
  }

  function queueLeaderSummary(queues) {
    const names = (queues || []).filter(Boolean);
    if (!names.length) return 'not measured';
    if (names.length === 1) return names[0];
    if (names.length === 2) return names.join(', ');
    return integer(names.length) + ' queues tied';
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
    const p95 = highest('p95'), p99 = highest('p99');
    const p95Coverage = allScopeEntries.filter(function (entry) { return waitValue(entry[1] || {}, 'p95') !== null && waitValue(entry[1] || {}, 'p95') !== undefined; }).length;
    const p99Coverage = allScopeEntries.filter(function (entry) { return waitValue(entry[1] || {}, 'p99') !== null && waitValue(entry[1] || {}, 'p99') !== undefined; }).length;
    add(host, pageHeader('Queue Monitor', 'Current queue counts, named queue-level wait measurements, retained history, and exact active jobs.', snapshot.ts));
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
      {id: 'queue-p95-leader', label: 'P95 QUEUE LEADER', value: p95.queue ? duration(p95.value) : '-', meta: p95.queue ? p95.queue + ' - ' + value(waitSourceDetail(p95.row, 'p95')) : 'No p95 source - ' + integer(p95Coverage) + ' measured queues', tone: p95.queue ? 'is-warning' : 'is-neutral', onOpen: function () { p95.queue ? openQueueDetail(p95.queue, p95.row, []) : openMetricDetail({label: 'Current p95 queue leader', value: '-', meta: 'No queue in scope reported a current p95. Missing values are not zero.'}); }},
      {id: 'queue-p99-leader', label: 'SAMPLED P99 LEADER', value: p99.queue ? duration(p99.value) : '-', meta: p99.queue ? p99.queue + ' - ' + (waitSampleCount(p99.row) !== null ? 'n=' + integer(waitSampleCount(p99.row)) + ' scheduled jobs' : 'sample count unavailable') : 'No sampled p99 - ' + integer(p99Coverage) + ' measured queues', tone: p99.queue ? 'is-danger' : 'is-neutral', onOpen: function () { p99.queue ? openQueueDetail(p99.queue, p99.row, []) : openMetricDetail({label: 'Current sampled p99 queue leader', value: '-', meta: 'p99 is rendered only from the current scheduled-job sample. It is unavailable when no sample exists.'}); }},
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
        {label: 'p95 official/fallback', numeric: true, render: function (item) { const source = waitSourceDetail(item[1], 'p95'); return linkButton(duration(waitValue(item[1], 'p95')) + (source ? ' - ' + source : ''), function () { openQueueDetail(item[0], item[1], activeJobs); }); }},
        {label: 'p99 scheduled sample', numeric: true, render: function (item) { const measured = waitValue(item[1], 'p99'); const count = waitSampleCount(item[1]); return linkButton(measured === null || measured === undefined ? '-' : duration(measured) + (count !== null ? ' - n=' + integer(count) : ''), function () { openQueueDetail(item[0], item[1], activeJobs); }); }},
        {label: 'Measurement source', render: function (item) { const row = item[1]; return linkButton([waitSourceDetail(row, 'p50'), waitSourceDetail(row, 'p95'), waitSourceDetail(row, 'p99')].filter(Boolean).filter(function (x, i, a) { return a.indexOf(x) === i; }).join(', ') || 'No wait measurement', function () { openQueueDetail(item[0], row, activeJobs); }); }},
      ], entries, integer(entries.length) + (state.queueIncludeIdle ? ' queues including idle' : ' active or problem queues')));
      return;
    }

    if (state.queueView === 'jobs') {
      const workloadCounts = activeJobs.reduce(function (out, job) { const key = job.workload || 'unknown'; out[key] = (out[key] || 0) + 1; return out; }, {});
      const jobColumns = [
        {label: 'Job', sticky: true, render: function (job) { return externalLink(job.name || 'Unnamed job', job.url); }},
        {label: 'Queue', render: function (job) { const row = (snapshot.queues || {})[job.queue] || {}; return linkButton(value(job.queue), function () { openQueueDetail(job.queue, row, activeJobs); }); }},
        {label: 'Workload', render: function (job) { return linkedBadge(job.workload || 'unknown', job.url, null, job.workload === 'omni' ? 'is-info' : 'is-neutral'); }},
        {label: 'State', render: function (job) { return linkedBadge(job.state || 'unknown', job.url); }},
        {label: 'Age', numeric: true, render: function (job) { return externalLink(duration(job.wait_min !== undefined ? job.wait_min : job.run_min), job.url); }},
        {label: 'Build', render: function (job) { return externalLink((job.pipeline || '?') + ' #' + value(job.build), job.build_url || buildUrl(job.pipeline, job.build), 'ops-mono'); }},
      ];
      host.append(compactTablePanel('Active jobs', Object.entries(workloadCounts).map(function (entry) { return entry[0] + ': ' + entry[1]; }).join(' - '), jobColumns, activeJobs, {
        id: 'queue-jobs-browser',
        limit: 15,
        browserSubtitle: integer(activeJobs.length) + ' exact active Buildkite jobs in the selected queue scope',
        searchPlaceholder: 'Filter job, queue, workload, state, or build',
        searchText: function (job) { return [job.name, job.queue, job.workload, job.state, job.pipeline, job.build].join(' '); },
        geometry: {name: 'queue-jobs', minWidth: '980px'},
      }));
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
    queueField.append(n('span', 'ops-field-label', 'History scope'));
    const queueSelect = n('select', 'ops-select');
    queueSelect.setAttribute('aria-label', 'Select queue for historical activity and wait time');
    [['fleet', state.queueScope === 'amd' ? 'All AMD queues combined' : 'All queues combined']].concat(queueNames.map(function (name) { return [name, name]; })).forEach(function (pair) {
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
    const historyLabel = state.queueHistoryQueue === 'fleet' ? (state.queueScope === 'amd' ? 'All AMD queues' : 'All queues') : state.queueHistoryQueue;
    if (state.queueHistoryQueue === 'fleet') {
      const aggregationNote = n('div', 'ops-evidence-note is-info');
      add(aggregationNote, [n('strong', '', 'Combined scope has two different reducers. '), n('span', '', 'Running and waiting below are summed across queues. Wait charts show the worst named queue at each snapshot; they are not fleet percentiles and the leading queue can change.')]);
      host.append(aggregationNote);
    }
    const activityTitle = state.queueHistoryQueue === 'fleet' ? historyLabel + ': total active jobs' : historyLabel + ': active jobs';
    const activityMeta = integer(points.length) + ' snapshots in ' + state.queueRange + ' - running and waiting ' + (state.queueHistoryQueue === 'fleet' ? 'summed across observed queues' : 'for this queue') + (selectedHistoryStart ? ' - begins ' + shortDate(selectedHistoryStart) : '');
    const cp = chartPanel(activityTitle, activityMeta, 'queue-history');
    host.append(cp.root);
    drawChart('queue-history', cp.canvas, {type: 'line', data: {
      labels: points.map(function (p) { return shortDate(p.ts); }),
      datasets: [
        {label: 'Running', data: points.map(function (p) { return p.running; }), borderColor: '#22b8ad', backgroundColor: '#22b8ad', pointRadius: 0, borderWidth: 2},
        {label: 'Waiting', data: points.map(function (p) { return p.waiting; }), borderColor: '#e3a63a', backgroundColor: '#e3a63a', pointRadius: 0, borderWidth: 2},
      ],
    }, evidenceTitle: historyLabel + ' queue activity history', evidenceAsset: SOURCE_ASSETS.queueHistory, evidence: points.map(function (point) { return {label: shortDate(point.ts), timestamp: point.ts, valueSummary: integer(point.running) + ' running - ' + integer(point.waiting) + ' waiting', details: {running: point.running, waiting: point.waiting, queues: point.queues, selected_queue: state.queueHistoryQueue}, sources: [{label: 'Open published queue history', url: SOURCE_ASSETS.queueHistory}], onOpen: function () { openQueueSnapshotDetail(point.snapshot, Object.assign({}, point, {selectedQueue: state.queueHistoryQueue})); }}; })});

    const waitPoints = selectedHistory.map(function (snap) { return queueWaitHistoryPoint(snap, state.queueHistoryQueue); });
    const waitEvidenceCount = waitPoints.filter(function (point) { return point.p50 !== null || point.p95 !== null || point.p99 !== null; }).length;
    if (waitEvidenceCount) {
      const waitTitle = state.queueHistoryQueue === 'fleet' ? 'Worst individual queue wait at each snapshot' : state.queueHistoryQueue + ': reported wait history';
      const waitSubtitle = state.queueHistoryQueue === 'fleet'
        ? 'Each point names the queue with the largest reported value; ties are preserved and no fleet percentile is calculated'
        : 'p50/p95 prefer official queue metrics; p99 is only the current scheduled-job sample';
      const waitChart = chartPanel(waitTitle, waitSubtitle + ' - ' + integer(waitEvidenceCount) + ' measured snapshots', 'queue-wait-history');
      host.append(waitChart.root);
      drawChart('queue-wait-history', waitChart.canvas, {
        type: 'line',
        data: {
          labels: waitPoints.map(function (point) { return shortDate(point.ts); }),
          datasets: [
            {label: 'p50 official/fallback', metric: 'p50', data: waitPoints.map(function (point) { return point.p50; }), borderColor: '#22b8ad', backgroundColor: '#22b8ad', pointRadius: 3, borderWidth: 2},
            {label: 'p95 official/fallback', metric: 'p95', data: waitPoints.map(function (point) { return point.p95; }), borderColor: '#e3a63a', backgroundColor: '#e3a63a', pointRadius: 3, borderWidth: 2},
            {label: 'p99 scheduled sample', metric: 'p99', data: waitPoints.map(function (point) { return point.p99; }), borderColor: '#cf8dd9', backgroundColor: '#cf8dd9', pointRadius: 3, borderWidth: 1.5},
          ],
        },
        options: {
          scales: {x: {grid: {display: false}, ticks: {maxTicksLimit: 8}}, y: {beginAtZero: true, title: {display: true, text: 'Wait minutes'}}},
          plugins: {tooltip: {callbacks: {
            label: function (context) {
              const metric = context.dataset.metric;
              const point = waitPoints[context.dataIndex] || {};
              const queues = point[metric + 'Queues'] || [point[metric + 'Queue']].filter(Boolean);
              return context.dataset.label + ': ' + duration(context.parsed.y) + (queues.length ? ' - ' + queueLeaderSummary(queues) : '');
            },
            afterLabel: function (context) {
              const metric = context.dataset.metric;
              const point = waitPoints[context.dataIndex] || {};
              const source = point[metric + 'SourceDetail'] || point[metric + 'Source'];
              const count = point[metric + 'SampleCount'];
              return [source ? 'Source: ' + source : null, metric === 'p99' && count !== null && count !== undefined ? 'Scheduled sample: n=' + integer(count) : null].filter(Boolean);
            },
          }}},
        },
        evidenceTitle: waitTitle,
        evidenceAsset: SOURCE_ASSETS.queueHistory,
        evidence: waitPoints.map(function (point) { return {label: shortDate(point.ts), timestamp: point.ts, valueSummary: 'p50 ' + duration(point.p50) + ' (' + queueLeaderSummary(point.p50Queues) + ') - p95 ' + duration(point.p95) + ' (' + queueLeaderSummary(point.p95Queues) + ') - p99 ' + duration(point.p99) + ' (' + queueLeaderSummary(point.p99Queues) + ')', details: {p50: duration(point.p50), p50_queues: (point.p50Queues || []).join(', '), p50_source: point.p50SourceDetail || point.p50Source, p95: duration(point.p95), p95_queues: (point.p95Queues || []).join(', '), p95_source: point.p95SourceDetail || point.p95Source, p99_sampled: duration(point.p99), p99_queues: (point.p99Queues || []).join(', '), p99_source: point.p99SourceDetail || point.p99Source, p99_sample_count: point.p99SampleCount}, sources: [{label: 'Open published queue history', url: SOURCE_ASSETS.queueHistory}], onOpen: function () { const activityPoint = points.find(function (row) { return row.ts === point.ts; }) || {}; openQueueSnapshotDetail(point.snapshot, {running: activityPoint.running, waiting: activityPoint.waiting, queues: activityPoint.queues, selectedQueue: state.queueHistoryQueue}); }}; }),
      });

      function peak(metric) {
        return waitPoints.filter(function (point) { return point[metric] !== null && point[metric] !== undefined; }).sort(function (a, b) { return Number(b[metric]) - Number(a[metric]); })[0] || null;
      }
      const leaderGrid = n('div', 'ops-wait-leader-grid');
      [['p50', 'PEAK P50', ''], ['p95', 'PEAK P95', 'is-warning'], ['p99', 'PEAK SAMPLED P99', 'is-danger']].forEach(function (spec) {
        const metric = spec[0];
        const point = peak(metric);
        const queueNamesForPoint = point ? point[metric + 'Queues'] || [point[metric + 'Queue']].filter(Boolean) : [];
        const card = n('button', 'ops-wait-leader ' + spec[2]);
        card.type = 'button';
        add(card, [
          n('span', 'ops-stat-label', spec[1]),
          n('strong', 'ops-wait-leader-value', point ? duration(point[metric]) : '-'),
          n('span', 'ops-wait-leader-queue', point ? queueLeaderSummary(queueNamesForPoint) : 'No measurement'),
          n('span', 'ops-wait-leader-meta', point ? shortDate(point.ts) + ' - ' + value(point[metric + 'SourceDetail'] || point[metric + 'Source']) + (metric === 'p99' && point.p99SampleCount !== null && point.p99SampleCount !== undefined ? ' - n=' + integer(point.p99SampleCount) : '') : 'Missing values are not zero'),
        ]);
        card.addEventListener('click', function () {
          if (!point) return;
          if (state.queueHistoryQueue === 'fleet' && queueNamesForPoint.length === 1) setRouteState('ci-queue', 'queueHistoryQueue', queueNamesForPoint[0], 'queue_history_queue');
          else { const activityPoint = points.find(function (row) { return row.ts === point.ts; }) || {}; openQueueSnapshotDetail(point.snapshot, {running: activityPoint.running, waiting: activityPoint.waiting, queues: activityPoint.queues, selectedQueue: state.queueHistoryQueue}); }
        });
        leaderGrid.append(card);
      });
      host.append(leaderGrid);
      if (waitPoints.some(function (point) { return point.p99 !== null; })) {
        host.append(n('p', 'ops-evidence-method', 'Sampled p99 uses only scheduled jobs present in that snapshot. The displayed n is the sample size; this is not a percentile over completed job history.'));
      }
    } else {
      host.append(n('div', 'ops-evidence-note is-info', 'No source-reported queue wait percentiles exist for ' + historyLabel + ' in this range. Counts remain historical evidence; missing waits are not rendered as zero.'));
    }
    if (points.length < 2) host.append(n('div', 'ops-evidence-note is-info', 'Historical collection has only one snapshot in this range. The dashboard will not infer a trend until another source-backed point exists.'));
    const waitsByTimestamp = new Map(waitPoints.map(function (point) { return [point.ts, point]; }));
    const historyColumns = [
      {label: 'Snapshot', sticky: true, render: function (point) { return linkButton(shortDate(point.ts), function () { openQueueSnapshotDetail(point.snapshot, Object.assign({}, point, {selectedQueue: state.queueHistoryQueue})); }); }},
      {label: 'Running', numeric: true, render: function (point) { return linkButton(integer(point.running), function () { openQueueSnapshotDetail(point.snapshot, Object.assign({}, point, {selectedQueue: state.queueHistoryQueue})); }); }},
      {label: 'Waiting', numeric: true, render: function (point) { return linkButton(integer(point.waiting), function () { openQueueSnapshotDetail(point.snapshot, Object.assign({}, point, {selectedQueue: state.queueHistoryQueue})); }); }},
      {label: 'Queues', numeric: true, render: function (point) { return linkButton(integer(point.queues), function () { openQueueSnapshotDetail(point.snapshot, Object.assign({}, point, {selectedQueue: state.queueHistoryQueue})); }); }},
      {label: 'Worst p95 queue', render: function (point) { const wait = waitsByTimestamp.get(point.ts) || {}; return linkButton(wait.p95Queue ? queueLeaderSummary(wait.p95Queues) + ' - ' + duration(wait.p95) : '-', function () { openQueueSnapshotDetail(point.snapshot, Object.assign({}, point, {selectedQueue: state.queueHistoryQueue})); }); }},
      {label: 'Worst sampled p99 queue', render: function (point) { const wait = waitsByTimestamp.get(point.ts) || {}; return linkButton(wait.p99Queue ? queueLeaderSummary(wait.p99Queues) + ' - ' + duration(wait.p99) : '-', function () { openQueueSnapshotDetail(point.snapshot, Object.assign({}, point, {selectedQueue: state.queueHistoryQueue})); }); }},
    ];
    host.append(compactTablePanel('Queue history snapshots', integer(points.length) + ' snapshots; worst-wait columns always name the queue', historyColumns, points.slice().reverse(), {
      id: 'queue-history-browser',
      limit: 14,
      browserSubtitle: historyLabel + ' in the selected ' + state.queueRange + ' range',
      searchPlaceholder: 'Filter by timestamp or leading queue',
      searchText: function (point) { const wait = waitsByTimestamp.get(point.ts) || {}; return [point.ts, wait.p95Queue, wait.p99Queue].join(' '); },
      geometry: {name: 'queue-history-snapshots', minWidth: '980px'},
    }));
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
    appendHardwareOptions(hwSelect, hardware, state.trajectoryHardware);
    hwSelect.addEventListener('change', function () { state.trajectoryHardware = hwSelect.value; render('ci-hotness', true); });
    const search = n('input', 'ops-input'); search.type = 'search'; search.placeholder = 'Filter test groups'; search.value = state.trajectorySearch;
    search.setAttribute('aria-label', 'Search workload trajectory test groups');
    search.addEventListener('change', function () { state.trajectorySearch = search.value; render('ci-hotness', true); });
    add(toolbar, [workloadSelect, hwSelect, search]); host.append(toolbar);
    const sourceNote = n('div', 'ops-evidence-note is-info');
    add(sourceNote, [n('strong', '', 'Upstream main terminal history. '), n('span', '', 'Windowed in the browser from ' + shortDate(windowData.observedFrom) + ' through ' + shortDate(windowData.observedTo) + '. Hardware comes from explicit job labels and queue assignment, including AMD MI mirror queues. Identities remain split by catalog ID, hardware, and queue. The source retains up to 60 observations per group, so longer windows may be truncated.')]);
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
      const anomalyColumns = [
        {label: 'Test group variant', sticky: true, width: '340px', render: function (row) { return groupIdentityCell(row, function () { openTrajectoryAnomalyHistory(row, anomalyData); }); }},
        {label: 'Frequency signal', width: '150px', render: function (row) { const hasChange = Number.isFinite(Number(row.frequencyChangePct)); const text = hasChange ? (row.frequencyChangePct >= 0 ? '+' : '') + row.frequencyChangePct.toFixed(0) + '%' : 'baseline limited'; return linkedBadge(text, exactPipelineEvidenceUrl(row.latest, 'ci'), function () { openTrajectoryAnomalyHistory(row, anomalyData); }, Number(row.frequencyChangePct) >= 100 ? 'is-warning' : 'is-info'); }},
        {label: 'Latest builds / day', numeric: true, width: '150px', render: function (row) { return linkButton(row.recentRate === null ? '-' : row.recentRate.toFixed(1), function () { openTrajectoryAnomalyHistory(row, anomalyData); }); }},
        {label: 'Prior builds / day', numeric: true, width: '150px', render: function (row) { return linkButton(row.baselineRate === null ? '-' : row.baselineRate.toFixed(1), function () { openTrajectoryAnomalyHistory(row, anomalyData); }); }},
        {label: 'Median change', numeric: true, width: '130px', render: function (row) { return linkButton(row.durationChangePct === null ? '-' : (row.durationChangePct >= 0 ? '+' : '') + row.durationChangePct.toFixed(0) + '%', function () { openTrajectoryAnomalyHistory(row, anomalyData); }); }},
        {label: 'Incident rate', numeric: true, width: '120px', render: function (row) { return linkButton(row.incidentRatePct.toFixed(1) + '%', function () { openTrajectoryAnomalyHistory(row, anomalyData); }); }},
        {label: 'Queues', width: '220px', render: function (row) { const links = n('div', 'ops-inline-links'); row.queues.forEach(function (queueName) { links.append(linkButton(queueName, function () { navigateTo('ci-queue', {queueView: 'history', queueHistoryQueue: queueName, queueScope: isAmdQueue(queueName) ? 'amd' : 'all'}); }, 'Open historical queue activity for ' + queueName)); }); return row.queues.length ? links : n('span', 'ops-cell-muted', '-'); }},
        {label: 'History', width: '140px', render: function (row) { return linkButton(integer(trajectoryAnomalyObservations(row).length) + ' runs', function () { openTrajectoryAnomalyHistory(row, anomalyData); }, 'Open exact cadence, baseline, and recent Buildkite history'); }},
      ];
      host.append(compactTablePanel(
        'Abnormal test-group activity',
        integer(anomalyDetails.length) + ' strict variants crossing frequency or duration thresholds',
        anomalyColumns,
        anomalyDetails,
        {
          id: 'trajectory-anomaly-browser',
          limit: 12,
          browserSubtitle: 'Strict hardware and queue identity are retained for every signal',
          searchPlaceholder: 'Filter test group, hardware, queue, or catalog ID',
          searchText: function (row) { return [row.name, row.id, row.hardware, (row.queues || []).join(' ')].join(' '); },
          geometry: {name: 'trajectory-anomalies', minWidth: '1390px'},
        }
      ));
    }
    const anomalyNote = n('div', 'ops-evidence-note is-info');
    add(anomalyNote, [n('strong', '', 'Abnormal activity method. '), n('span', '', 'Execution frequency compares median inter-build cadence across the latest 8 distinct builds with the preceding 16, so retries in one build are counted once. Completion compares the last ' + duration(anomalyData.recentHours * 60) + ' with ' + shortDate(anomalyData.baselineStart) + ' through ' + shortDate(anomalyData.baselineEnd) + '. Signals remain split by strict group ID, hardware, and queue.')]);
    host.append(anomalyNote);
    const top = rows.slice().sort(function (a, b) { return Number(b.count || 0) - Number(a.count || 0); }).slice(0, 15);
    const cp = chartPanel('Most active strict variants', 'Terminal observations in the selected all-main window', 'trajectory-groups');
    host.append(cp.root);
    drawChart('trajectory-groups', cp.canvas, {type: 'bar', data: {labels: top.map(function (row) { return compactChartLabel(row, 54); }), datasets: [{label: 'Observations', data: top.map(function (row) { return row.count; }), backgroundColor: '#22b8ad'}]}, options: {indexAxis: 'y'}, evidenceTitle: 'Strict test-group execution volume', evidence: top.map(function (row) { return {id: row.id, label: row.name + ' - ' + row.hardware, timestamp: row.last_seen, valueSummary: integer(row.count) + ' observations', details: {catalog_id: row.id, hardware: row.hardware, queues: row.queues.join(', '), median: duration(row.p50_min), p90: duration(row.p90_min), incident_rate: hotnessRatePercent(row).toFixed(1) + '%'}, sources: [{label: 'Open published all-main history', url: SOURCE_ASSETS.operations}], onOpen: function () { openTrajectoryGroupHistory(row); }}; })});
    const trajectoryColumns = [
      {label: 'Test group variant', sticky: true, render: function (row) { return groupIdentityCell(row, function () { openTrajectoryGroupHistory(row); }); }},
      {label: 'Workload', render: function (row) { return linkedBadge(row.workload || 'vllm', null, function () { state.trajectoryWorkload = row.workload || 'vllm'; render('ci-hotness', true); }, row.workload === 'omni' ? 'is-info' : 'is-neutral'); }},
      {label: 'Observations', numeric: true, render: function (row) { return linkButton(integer(row.count), function () { openTrajectoryGroupHistory(row); }, 'Inspect ' + integer(row.count) + ' observations for ' + row.name + ' on ' + row.hardware); }},
      {label: 'Builds', numeric: true, render: function (row) { return linkButton(integer(row.build_count), function () { openTrajectoryGroupHistory(row); }, 'Inspect builds for ' + row.name + ' on ' + row.hardware); }},
      {label: 'Median', numeric: true, render: function (row) { return linkButton(duration(row.p50_min), function () { openTrajectoryGroupHistory(row); }, 'Inspect median completion for ' + row.name + ' on ' + row.hardware); }},
      {label: 'p90', numeric: true, render: function (row) { return linkButton(duration(row.p90_min), function () { openTrajectoryGroupHistory(row); }, 'Inspect p90 completion for ' + row.name + ' on ' + row.hardware); }},
      {label: 'Incident rate', numeric: true, render: function (row) { return linkButton(hotnessRatePercent(row).toFixed(1) + '%', function () { openTrajectoryGroupHistory(row); }, 'Inspect incidents for ' + row.name + ' on ' + row.hardware); }},
      {label: 'Last observed', render: function (row) { const latest = row.observations.slice().sort(function (a, b) { return new Date(observationTimestamp(b) || 0) - new Date(observationTimestamp(a) || 0); })[0]; return externalLink(shortDate(row.last_seen), exactPipelineEvidenceUrl(latest, 'ci')); }},
      {label: 'Evidence', render: function (row) { return linkButton(integer(row.observations.length) + ' exact links', function () { openTrajectoryGroupHistory(row); }, 'Open exact Buildkite evidence for catalog ID ' + row.id); }},
    ];
    host.append(compactTablePanel('All strict variants in this window', integer(rows.length) + ' variants after the active filters', trajectoryColumns, rows, {
      id: 'trajectory-browser',
      limit: 15,
      browserTitle: 'Workload trajectory evidence',
      browserSubtitle: state.trajectoryWindow + ' upstream main window; every row opens exact Buildkite observations',
      searchPlaceholder: 'Filter test group, hardware, workload, queue, or ID',
      searchText: function (row) { return [row.name, row.id, row.hardware, row.workload, (row.queues || []).join(' ')].join(' '); },
      geometry: {name: 'trajectory-groups', minWidth: '1320px'},
    }));

  }

  const OMNI_RANGE_WINDOWS = [
    {id: '1h', label: '1 hour', hours: 1},
    {id: '3h', label: '3 hours', hours: 3},
    {id: '6h', label: '6 hours', hours: 6},
    {id: '12h', label: '12 hours', hours: 12},
    {id: '24h', label: '1 day', hours: 24},
    {id: '72h', label: '3 days', hours: 72},
  ];
  const OMNI_AGE_BANDS = [
    {id: 'all', label: 'All active', min: null, max: null},
    {id: 'lt1h', label: '<1h', min: 0, max: 60},
    {id: '1to3h', label: '1-3h', min: 60, max: 180},
    {id: '3to6h', label: '3-6h', min: 180, max: 360},
    {id: '6to12h', label: '6-12h', min: 360, max: 720},
    {id: '12to24h', label: '12-24h', min: 720, max: 1440},
    {id: '1to3d', label: '1-3d', min: 1440, max: 4320},
    {id: 'gte3d', label: '3d+', min: 4320, max: null},
  ];

  function omniHistoryPoints(omni) {
    const rows = ((((omni || {}).history || {}).points) || []);
    return rows.map(function (point) {
      const allFleet = point.all_fleet || {};
      const amd = point.amd || {};
      const waitingSupported = allFleet.waiting_supported === true
        || (allFleet.waiting_supported === undefined && ['complete', 'partial'].includes(allFleet.waiting_attribution));
      const runningSupported = allFleet.running_supported === true
        || (allFleet.running_supported === undefined && ['complete', 'partial'].includes(allFleet.running_attribution));
      const amdWaitingSupported = amd.waiting_supported === true
        || (amd.waiting_supported === undefined && ['complete', 'partial'].includes(amd.waiting_attribution));
      const amdRunningSupported = amd.running_supported === true
        || (amd.running_supported === undefined && ['complete', 'partial'].includes(amd.running_attribution));
      return {
        ts: point.ts,
        time: new Date(point.ts || '').getTime(),
        allWaiting: waitingSupported ? Number(allFleet.waiting_observed || 0) : null,
        allRunning: runningSupported ? Number(allFleet.running_observed || 0) : null,
        amdWaiting: amdWaitingSupported ? Number(amd.waiting_observed || 0) : null,
        amdRunning: amdRunningSupported ? Number(amd.running_observed || 0) : null,
        waitingSupported: waitingSupported,
        runningSupported: runningSupported,
        amdWaitingSupported: amdWaitingSupported,
        amdRunningSupported: amdRunningSupported,
        waitingCoverage: allFleet.waiting_attribution || 'unavailable',
        runningCoverage: allFleet.running_attribution || 'unavailable',
        source: point,
      };
    }).filter(function (point) {
      return Number.isFinite(point.time);
    }).sort(function (left, right) {
      return left.time - right.time;
    });
  }

  function omniWindowPoints(points, rangeId) {
    if (!points.length) return [];
    const selected = OMNI_RANGE_WINDOWS.find(function (item) { return item.id === rangeId; }) || OMNI_RANGE_WINDOWS[4];
    const latestTime = points[points.length - 1].time;
    const cutoff = latestTime - selected.hours * 60 * 60 * 1000;
    return points.filter(function (point) {
      return point.time >= cutoff && point.time <= latestTime;
    });
  }

  function omniAgeBand(job) {
    if (!job || job.wait_min === null || job.wait_min === undefined || job.wait_min === '') return '';
    const minutes = Number(job && job.wait_min);
    if (!Number.isFinite(minutes) || minutes < 0) return '';
    const band = OMNI_AGE_BANDS.slice(1).find(function (item) {
      return minutes >= item.min && (item.max === null || minutes < item.max);
    });
    return band ? band.id : '';
  }

  function omniDailyRows(points) {
    const byDay = new Map();
    points.filter(function (point) {
      return point.allWaiting !== null
        && point.allWaiting !== undefined
        && Number.isFinite(Number(point.allWaiting));
    }).forEach(function (point) {
      const day = new Date(point.time).toISOString().slice(0, 10);
      if (!byDay.has(day)) byDay.set(day, []);
      byDay.get(day).push(point);
    });
    const rows = Array.from(byDay.entries()).sort(function (left, right) {
      return compareText(left[0], right[0]);
    }).map(function (entry) {
      const samples = entry[1].slice().sort(function (left, right) { return left.time - right.time; });
      const last = samples[samples.length - 1];
      return {
        day: entry[0],
        last: last,
        waiting: last.allWaiting,
        amdWaiting: last.amdWaiting,
        peak: Math.max.apply(null, samples.map(function (point) { return point.allWaiting; })),
        samples: samples.length,
        complete: samples.every(function (point) { return point.waitingCoverage === 'complete'; }),
        delta: null,
      };
    });
    rows.forEach(function (row, index) {
      if (!index) return;
      const previous = rows[index - 1];
      const dayGap = (
        new Date(row.day + 'T00:00:00Z').getTime()
        - new Date(previous.day + 'T00:00:00Z').getTime()
      ) / (24 * 60 * 60 * 1000);
      if (dayGap === 1) row.delta = row.waiting - previous.waiting;
    });
    return rows.slice(-7).reverse();
  }

  function signedInteger(number) {
    if (!Number.isFinite(Number(number))) return '-';
    const value = Number(number);
    return (value > 0 ? '+' : '') + integer(value);
  }

  async function renderOmni(host, ops) {
    const omni = ops.omni || {};
    const current = omni.current || {};
    const currentAttribution = current.attribution || {};
    const currentLedger = current.ledger || {};
    const currentBasis = current.count_basis || {};
    const heuristic = omni.heuristic_thresholds || {};
    const jobs = omni.current_jobs || {};
    add(host, pageHeader('Omni', 'All current vLLM-Omni demand across the fleet, split into AMD and non-AMD execution scopes.', (omni.provenance || {}).queue_snapshot_ts, externalLink('Open Omni source', SOURCE_ASSETS.operations, 'ops-button')));
    const waitingByQueue = current.waiting_by_queue || {};
    const runningByQueue = current.running_by_queue || {};
    const pendingLedger = (jobs.pending || []).filter(function (job) { return !isRetiredQueue(job.queue); });
    const runningLedger = (jobs.running || []).filter(function (job) { return !isRetiredQueue(job.queue); });
    const pending = pendingLedger.filter(function (job) { return !job.analysis_excluded; });
    const running = runningLedger.filter(function (job) { return !job.analysis_excluded; });
    const excludedPending = pendingLedger.filter(function (job) { return job.analysis_excluded; });
    const excludedRunning = runningLedger.filter(function (job) { return job.analysis_excluded; });
    const excludedJobs = excludedPending.concat(excludedRunning);
    const activeJobs = pending.concat(running);
    const affected = new Set(Object.keys(waitingByQueue).concat(Object.keys(runningByQueue)).concat(activeJobs.map(function (job) { return job.queue || 'unknown'; })).filter(function (name) { return !isRetiredQueue(name); }));
    const amdPending = pending.filter(function (job) { return isAmdQueue(job.queue); });
    const amdRunning = running.filter(function (job) { return isAmdQueue(job.queue); });
    const nonAmdPending = pending.filter(function (job) { return !isAmdQueue(job.queue); });
    const nonAmdRunning = running.filter(function (job) { return !isAmdQueue(job.queue); });
    const ledgerWaiting = Number.isFinite(Number(currentLedger.waiting)) ? Number(currentLedger.waiting) : pending.length;
    const ledgerRunning = Number.isFinite(Number(currentLedger.running)) ? Number(currentLedger.running) : running.length;
    const attributedWaiting = currentAttribution.waiting_supported ? integer(currentAttribution.waiting_observed) : 'unavailable';
    const attributedRunning = currentAttribution.running_supported ? integer(currentAttribution.running_observed) : 'unavailable';
    const attributionSummary = (
      'Exact active-job ledger: ' + integer(ledgerWaiting) + ' waiting and ' + integer(ledgerRunning)
      + ' running. Separately observed queue workload split: ' + attributedWaiting + ' waiting ('
      + value(currentAttribution.waiting_attribution) + ') and ' + attributedRunning + ' running ('
      + value(currentAttribution.running_attribution) + '). Current aggregate basis: '
      + value(currentBasis.waiting) + ' waiting and ' + value(currentBasis.running) + ' running.'
    );
    function openJobsEvidence(title, rows, evidenceNote) {
      if (!rows.length) {
        openMetricDetail({label: title, value: 0, meta: 'No source-backed jobs in this scope.', sources: [{label: 'Open published Omni snapshot', url: SOURCE_ASSETS.operations}]});
        return;
      }
      openHistoryEvidence(title, rows.map(function (job) { return {id: job.job_id, label: job.name || 'Unnamed Omni job', timestamp: job.created_at || job.scheduled_at || job.started_at, valueSummary: value(job.state) + ' on ' + value(job.queue), url: job.url, details: {queue: job.queue, state: job.state, pipeline: job.pipeline, build: job.build, exclusion_reason: job.exclusion_reason || null}}; }), evidenceNote || 'Every active job links to its exact Buildkite source', SOURCE_ASSETS.operations);
    }
    host.append(statusStrip([
      {id: 'omni-all-jobs', label: 'ALL-FLEET ACTIVE JOBS', value: integer(activeJobs.length), meta: integer(pending.length) + ' waiting - ' + integer(running.length) + ' running; ' + integer(excludedJobs.length) + ' stale excluded', tone: pending.length ? 'is-warning' : 'is-neutral', onOpen: function () { openJobsEvidence('All-fleet active Omni jobs', activeJobs); }},
      {id: 'omni-amd-jobs', label: 'AMD ACTIVE JOBS', value: integer(amdPending.length + amdRunning.length), meta: integer(amdPending.length) + ' waiting - ' + integer(amdRunning.length) + ' running', onOpen: function () { openJobsEvidence('AMD active Omni jobs', amdPending.concat(amdRunning)); }},
      {id: 'omni-non-amd-jobs', label: 'NON-AMD ACTIVE JOBS', value: integer(nonAmdPending.length + nonAmdRunning.length), meta: integer(nonAmdPending.length) + ' waiting - ' + integer(nonAmdRunning.length) + ' running', tone: nonAmdPending.length ? 'is-warning' : 'is-info', onOpen: function () { openJobsEvidence('Non-AMD active Omni jobs', nonAmdPending.concat(nonAmdRunning)); }},
      {id: 'omni-queues', label: 'AFFECTED QUEUES', value: integer(affected.size), meta: integer(Array.from(affected).filter(isAmdQueue).length) + ' AMD - ' + integer(Array.from(affected).filter(function (name) { return !isAmdQueue(name); }).length) + ' non-AMD', onOpen: function () { openHistoryEvidence('Omni queue distribution', Array.from(affected).map(function (name) { return {label: name, valueSummary: integer(waitingByQueue[name] || 0) + ' waiting - ' + integer(runningByQueue[name] || 0) + ' running', sources: [{label: 'Open published Omni snapshot', url: SOURCE_ASSETS.operations}], onOpen: function () { openQueueDetail(name, (((ops.queue || {}).snapshot || {}).queues || {})[name] || {}, activeJobs); }}; }), 'Current all-fleet queue distribution', SOURCE_ASSETS.operations); }},
    ]));
    const scopeNote = n('div', 'ops-evidence-note is-info');
    add(scopeNote, [n('strong', '', 'Collector status: ' + value(omni.status) + '. '), n('span', '', 'The current job ledger is fleet-wide. ' + attributionSummary + ' These evidence types are never forced to agree. ' + integer(excludedJobs.length) + ' stale jobs are retained as evidence but excluded from active counts and queue analytics. The configured waiting heuristic (' + integer(heuristic.trigger) + ' trigger) is collector provenance only. '), linkButton('Inspect excluded stale jobs', function () { openJobsEvidence('Excluded stale Omni jobs', excludedJobs, 'Jobs beyond the collector age threshold; retained for exact Buildkite review but excluded from active analytics'); }, 'Inspect stale Omni jobs excluded from active analytics')]);
    host.append(scopeNote);
    const allHistoryPoints = omniHistoryPoints(omni);
    const waitingHistoryPoints = allHistoryPoints.filter(function (point) { return point.waitingSupported; });
    const points = omniWindowPoints(waitingHistoryPoints, state.omniRange);
    const selectedRange = OMNI_RANGE_WINDOWS.find(function (item) { return item.id === state.omniRange; }) || OMNI_RANGE_WINDOWS[4];
    const firstPoint = points.length > 1 ? points[0] : null;
    const latestPoint = points.length ? points[points.length - 1] : null;
    const waitingDelta = firstPoint && latestPoint ? latestPoint.allWaiting - firstPoint.allWaiting : null;
    const waitingPeak = points.length ? Math.max.apply(null, points.map(function (point) { return point.allWaiting; })) : null;
    const windowCoverage = points.length && points.every(function (point) { return point.waitingCoverage === 'complete'; }) ? 'complete' : 'partial';
    function historyEvidence(rows) {
      return rows.map(function (point) {
        return {
          label: shortDate(point.ts),
          timestamp: point.ts,
          valueSummary: (point.waitingSupported ? integer(point.allWaiting) : 'unavailable') + ' observed waiting - ' + (point.runningSupported ? integer(point.allRunning) : 'unavailable') + ' observed running',
          details: {
            all_fleet_waiting_observed: point.allWaiting,
            all_fleet_running_observed: point.allRunning,
            amd_waiting_observed: point.amdWaiting,
            amd_running_observed: point.amdRunning,
            waiting_supported: point.waitingSupported,
            running_supported: point.runningSupported,
            amd_waiting_supported: point.amdWaitingSupported,
            amd_running_supported: point.amdRunningSupported,
            waiting_attribution: point.waitingCoverage,
            running_attribution: point.runningCoverage,
          },
          sources: [{label: 'Open published queue history', url: SOURCE_ASSETS.queueHistory}],
        };
      });
    }
    const rangeToolbar = n('div', 'ops-toolbar ops-analytics-window-toolbar ops-omni-window-toolbar');
    add(rangeToolbar, [
      n('span', 'ops-toolbar-label', 'Queue history window'),
      segmented(OMNI_RANGE_WINDOWS, state.omniRange, function (range) {
        setRouteState('ci-omni', 'omniRange', range, 'omni_range');
      }, 'Filter Omni queue history by time window'),
      n('span', 'ops-window-context', 'Trailing ' + selectedRange.label + ' relative to the latest observed snapshot; sparse windows remain explicitly unavailable.'),
    ]);
    host.append(rangeToolbar);
    host.append(statusStrip([
      {
        id: 'omni-window-latest',
        label: 'OBSERVED QUEUED NOW',
        value: latestPoint ? integer(latestPoint.allWaiting) : '-',
        meta: latestPoint ? (latestPoint.amdWaitingSupported ? integer(latestPoint.amdWaiting) : 'unavailable') + ' AMD - ' + shortDate(latestPoint.ts) : 'No workload-attributed snapshot',
        tone: latestPoint && latestPoint.allWaiting ? 'is-warning' : 'is-neutral',
        onOpen: function () { openHistoryEvidence('Latest observed Omni queue', historyEvidence(latestPoint ? [latestPoint] : []), 'Explicit workload-attributed counts only', SOURCE_ASSETS.queueHistory); },
      },
      {
        id: 'omni-window-change',
        label: 'NET CHANGE IN ' + selectedRange.label.toUpperCase(),
        value: waitingDelta === null ? '-' : signedInteger(waitingDelta),
        meta: firstPoint ? 'from ' + shortDate(firstPoint.ts) + '; ' + windowCoverage + ' attribution across window' : 'Needs at least two observations in this window',
        tone: waitingDelta > 0 ? 'is-warning' : waitingDelta < 0 ? 'is-success' : 'is-neutral',
        onOpen: function () { openHistoryEvidence('Omni queue change in ' + selectedRange.label, historyEvidence(points), 'First-to-latest observed waiting count in the selected window', SOURCE_ASSETS.queueHistory); },
      },
      {
        id: 'omni-window-peak',
        label: 'WINDOW PEAK QUEUED',
        value: waitingPeak === null ? '-' : integer(waitingPeak),
        meta: integer(points.length) + ' attributed snapshots',
        tone: waitingPeak ? 'is-warning' : 'is-neutral',
        onOpen: function () { openHistoryEvidence('Omni queue observations in ' + selectedRange.label, historyEvidence(points), 'Every retained observation in the selected window', SOURCE_ASSETS.queueHistory); },
      },
      {
        id: 'omni-window-coverage',
        label: 'ATTRIBUTION COVERAGE',
        value: points.length ? windowCoverage : '-',
        meta: windowCoverage === 'complete' ? 'Every queued task in each snapshot was workload-classified' : 'Observed Omni counts may be lower bounds',
        tone: windowCoverage === 'complete' ? 'is-success' : 'is-warning',
        onOpen: function () { openHistoryEvidence('Omni workload attribution coverage', historyEvidence(points), 'Partial attribution is never converted into inferred Omni counts', SOURCE_ASSETS.queueHistory); },
      },
    ]));
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
    if (points.length) {
      const cp = chartPanel('Omni queue history: ' + selectedRange.label, points.length + ' workload-attributed snapshots; observed counts only', 'omni-history');
      cp.root.classList.add('ops-omni-detail');
      grid.append(cp.root);
      requestAnimationFrame(function () {
        drawChart('omni-history', cp.canvas, {type: 'line', data: {labels: points.map(function (point) { return shortDate(point.ts); }), datasets: [
          {label: 'All-fleet waiting observed', data: points.map(function (point) { return point.allWaiting; }), borderColor: '#e3a63a', backgroundColor: '#e3a63a', pointRadius: 0, borderWidth: 2},
          {label: 'All-fleet running observed', data: points.map(function (point) { return point.allRunning; }), borderColor: '#cf8dd9', backgroundColor: '#cf8dd9', pointRadius: 0, borderWidth: 2},
          {label: 'AMD waiting observed', data: points.map(function (point) { return point.amdWaiting; }), borderColor: '#5ca8ff', backgroundColor: '#5ca8ff', pointRadius: 0, borderWidth: 1, borderDash: [4, 3]},
          {label: 'AMD running observed', data: points.map(function (point) { return point.amdRunning; }), borderColor: '#22b8ad', backgroundColor: '#22b8ad', pointRadius: 0, borderWidth: 1, borderDash: [4, 3]},
        ]}, evidenceTitle: 'Observed Omni all-fleet and AMD queue history', evidenceAsset: SOURCE_ASSETS.queueHistory, evidence: historyEvidence(points)});
      });
    } else {
      const unavailable = n('div', 'ops-stack');
      unavailable.append(n('div', 'ops-evidence-note is-warning', 'No workload-attributed Omni snapshots fall inside this window. Aggregate queue totals are never reclassified as Omni.'));
      unavailable.append(sourceActions([{label: 'Inspect published queue history', url: SOURCE_ASSETS.queueHistory}]));
      const historyPanel = panel('Omni queue history unavailable for ' + selectedRange.label, '0 snapshots with explicit workload attribution', unavailable, 'ops-omni-detail');
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

    const dailyRows = omniDailyRows(waitingHistoryPoints);
    host.append(panel('Day-over-day observed queued workload (UTC)', 'Last observation per UTC day; change is against the preceding day at its last observation', dataTable([
      {label: 'UTC day', sticky: true, render: function (row) { return linkButton(row.day, function () { openHistoryEvidence('Omni queue observations on ' + row.day, historyEvidence(allHistoryPoints.filter(function (point) { return new Date(point.time).toISOString().slice(0, 10) === row.day; })), 'Every workload-attributed snapshot retained for this UTC day', SOURCE_ASSETS.queueHistory); }); }},
      {label: 'Last observed queued', numeric: true, render: function (row) { return linkButton(integer(row.waiting), function () { openHistoryEvidence('Closing Omni queue observation on ' + row.day, historyEvidence([row.last]), 'Last retained observation for the UTC day', SOURCE_ASSETS.queueHistory); }); }},
      {label: 'Day-over-day change', numeric: true, render: function (row) { return badge(row.delta === null ? 'unavailable' : signedInteger(row.delta), row.delta > 0 ? 'is-warning' : row.delta < 0 ? 'is-success' : 'is-neutral'); }},
      {label: 'Daily peak', numeric: true, render: function (row) { return integer(row.peak); }},
      {label: 'AMD queued', numeric: true, render: function (row) { return integer(row.amdWaiting); }},
      {label: 'Last snapshot', render: function (row) { return shortDate(row.last.ts); }},
      {label: 'Samples', numeric: true, render: function (row) { return integer(row.samples); }},
      {label: 'Attribution', render: function (row) { return badge(row.complete ? 'complete' : 'partial lower bound', row.complete ? 'is-success' : 'is-warning'); }},
    ], dailyRows), 'ops-domain-full ops-omni-daily'));

    const ageOptions = OMNI_AGE_BANDS.map(function (band) {
      const count = band.id === 'all'
        ? activeJobs.length
        : pendingLedger.filter(function (job) { return omniAgeBand(job) === band.id; }).length;
      return {id: band.id, label: band.label + ' (' + integer(count) + ')'};
    });
    const selectedAge = OMNI_AGE_BANDS.find(function (band) { return band.id === state.omniAge; }) || OMNI_AGE_BANDS[0];
    const visibleJobs = (state.omniAge === 'all'
      ? activeJobs
      : pendingLedger.filter(function (job) { return omniAgeBand(job) === state.omniAge; })
    ).slice().sort(function (left, right) {
      return Number(right.wait_min || 0) - Number(left.wait_min || 0)
        || compareText(left.name, right.name);
    });
    const ageToolbar = n('div', 'ops-toolbar ops-analytics-window-toolbar ops-omni-age-toolbar');
    add(ageToolbar, [
      n('span', 'ops-toolbar-label', 'Queued task age'),
      segmented(ageOptions, state.omniAge, function (ageBand) {
        setRouteState('ci-omni', 'omniAge', ageBand, 'omni_age');
      }, 'Filter exact Omni tasks by queued age'),
      n('span', 'ops-window-context', state.omniAge === 'all' ? 'Active waiting and running tasks' : 'Queued tasks only; retained stale records remain visibly marked'),
    ]);
    host.append(ageToolbar);
    const jobColumns = [
      {label: 'Job', sticky: true, render: function (r) { return externalLink(r.name || 'Unnamed job', r.url); }},
      {label: 'Queue', render: function (r) { return linkButton(value(r.queue), function () { openQueueDetail(r.queue, ((((ops.queue || {}).snapshot || {}).queues || {})[r.queue]) || {}, activeJobs); }, 'Inspect queue and exact jobs for ' + value(r.queue)); }},
      {label: 'Scope', render: function (r) { return badge(isAmdQueue(r.queue) ? 'AMD' : 'Non-AMD', isAmdQueue(r.queue) ? 'is-success' : 'is-info'); }},
      {label: 'State', render: function (r) { return linkedBadge(r.state, r.url); }},
      {label: 'Age', numeric: true, render: function (r) { return externalLink(duration(r.wait_min !== undefined ? r.wait_min : r.run_min), r.url); }},
      {label: 'Analysis', render: function (r) { return badge(r.analysis_excluded ? 'stale excluded' : 'active', r.analysis_excluded ? 'is-warning' : 'is-success'); }},
      {label: 'Build', render: function (r) { return externalLink((r.pipeline || '?') + ' #' + value(r.build), r.build_url || buildUrl(r.pipeline, r.build), 'ops-mono'); }},
      {label: 'Source', render: function (r) { return linkedBadge(r.source || r.workload || 'omni', r.url, null, 'is-info'); }},
    ];
    host.append(compactTablePanel(
      state.omniAge === 'all' ? 'Current all-fleet Omni jobs' : 'Queued Omni tasks aged ' + selectedAge.label,
      integer(visibleJobs.length) + ' exact source-backed tasks; no aggregate queue total is expanded into synthetic jobs',
      jobColumns,
      visibleJobs,
      {
        id: 'omni-job-browser',
        limit: 20,
        browserTitle: state.omniAge === 'all' ? 'Current all-fleet Omni jobs' : 'Queued Omni tasks aged ' + selectedAge.label,
        browserSubtitle: 'Every row links to the exact Buildkite job; stale retained records are marked and excluded from active analytics',
        searchPlaceholder: 'Filter job, queue, pipeline, branch, or state',
        searchText: function (row) { return [row.name, row.queue, row.pipeline, row.branch, row.state].join(' '); },
        geometry: {name: 'omni-jobs', minWidth: '1180px'},
      }
    ));
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
      const ops = await loadOperations(tabId);
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
        operationsManifestPromise = null;
        render(tabId, true);
      }, true);
      add(host, [pageHeader('AMD CI Operations', 'The requested operational data could not be loaded.', null, retry), n('div', 'ops-error', error.message || String(error))]);
      console.error('Ops v2 render failed:', error);
    }
  }

  window.OpsV2 = {
    render: render,
    renderOwnership: renderOwnership,
    state: state,
    openTestGroupHistory: openTestGroupHistory,
    loadSections: function (names) { return loadOperationSections(null, names || []); },
  };
  if (window.__OPS_V2_TEST__) {
    window.OpsV2Test = {
      sortRuntimeTargetRows: sortRuntimeTargetRows,
      targetResolutionPresentation: targetResolutionPresentation,
      targetAssessmentText: targetAssessmentText,
      targetNoSignalBreakdown: targetNoSignalBreakdown,
      omniHistoryPoints: omniHistoryPoints,
      omniWindowPoints: omniWindowPoints,
      omniAgeBand: omniAgeBand,
      omniDailyRows: omniDailyRows,
    };
  }

  function activeTab() {
    const panelEl = document.querySelector('.tab-panel.active');
    return panelEl && panelEl.id ? panelEl.id.replace(/^tab-/, '') : 'projects';
  }

  document.addEventListener('DOMContentLoaded', function () {
    render(activeTab());
  });
})();
