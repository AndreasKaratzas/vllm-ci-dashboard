/**
 * vLLM AMD CI Operations v2.
 *
 * Operations renderer for Home, Health, Analytics, Perf Eval, and Omni.
 */
(function () {
  'use strict';

  const OWNED_TABS = new Set([
    'projects', 'ci-health', 'ci-analytics', 'ci-perf-eval', 'ci-omni',
  ]);
  const cache = new Map();
  const charts = new Map();
  const DNS_AUTO_REFRESH_MS = 5 * 60 * 1000;
  const OPS_SNAPSHOT_MAX_AGE_MS = 3 * 60 * 60 * 1000;
  const QUEUE_LIVE_BASE = 'https://raw.githubusercontent.com/AndreasKaratzas/vllm-ci-dashboard/queue-data/data/vllm/ci/';
  const QUEUE_DNS_LIVE_BASE = 'https://raw.githubusercontent.com/AndreasKaratzas/vllm-ci-dashboard/dns-health-data/data/vllm/ci/';
  let operationsManifestPromise = null;
  let lastDnsRefreshAt = 0;
  let firstRenderSettled = false;
  const SOURCE_ASSETS = {
    operations: 'data/vllm/ci/operations_v2_manifest.json',
    operationsManifest: 'data/vllm/ci/operations_v2_manifest.json',
    nightly: 'data/vllm/ci/operations_v2/nightly.json',
    amdTestHealth: 'data/vllm/ci/operations_v2/amd_test_health.json',
    amdAgentHealth: 'data/vllm/ci/operations_v2/amd_agent_health.json',
    reliability: 'data/vllm/ci/operations_v2/reliability.json',
    comparison: 'data/vllm/ci/operations_v2/comparison.json',
    testGroupParity: 'data/vllm/ci/operations_v2/test_group_parity.json',
    omni: 'data/vllm/ci/operations_v2/omni.json',
    queueSection: QUEUE_LIVE_BASE + 'operations_v2/queue.json',
    queueHistory: QUEUE_LIVE_BASE + 'queue_timeseries.jsonl',
    queueDns: QUEUE_DNS_LIVE_BASE + 'dns_failures.json',
    queueDnsFallback: 'data/vllm/ci/dns_failures.json',
    workloadMapping: 'data/vllm/ci/workload_mapping.json',
    perf: 'data/vllm/perf_eval/perf_eval.json',
  };
  const CHART_LIBRARY_URL = 'https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js';
  const AMD_MIRROR_INVENTORY_MODULE_URL = 'assets/js/amd-mirror-inventory.js?v=3';
  const state = {
    healthView: 'overview',
    healthCoverageSort: 'platform',
    healthParityState: 'action',
    healthParityArea: 'all',
    analyticsView: 'groups',
    homeWork: 'issues',
    healthSearch: '',
    healthPlan: 'upstream_only',
    analyticsSearch: '',
    analyticsAmdFilter: 'attention',
    analyticsDnsScope: 'amd',
    analyticsDnsWindow: '24h',
    agentWindow: '7d',
    agentGpu: 'all',
    agentNode: '',
    agentCofail: '180',
    agentExclCancel: '1',
    agentNightly: '0',
    agentSignal: 'infra',


    omniRange: '24h',
    omniMappingRange: '7d',
    omniAge: 'all',
    perfView: 'performance',
    perfModel: 'all',
    perfDevice: 'all',
  };
  let pendingSegmentFocus = null;
  let pendingTabFocus = '';

  const ROUTE_QUERY_KEYS = {
    'ci-health': new Set([
      'ops_health_view', 'ops_health_sort',
      'ops_health_parity_state', 'ops_health_parity_area',
    ]),
    'ci-analytics': new Set([
      'ops_analytics_view', 'ops_analytics_search',
      'ops_analytics_amd_filter',
      'ops_analytics_dns_scope', 'ops_analytics_dns_window',
      'ops_agent_window', 'ops_agent_gpu', 'ops_agent_node',
      'ops_agent_cofail', 'ops_agent_excl_cancel', 'ops_agent_nightly',
      'ops_agent_signal', 'ops_detail',
    ]),
    'ci-omni': new Set(['ops_omni_mapping_range', 'ops_omni_range', 'ops_omni_age', 'ops_detail']),
    'ci-perf-eval': new Set(['ops_perf_view', 'ops_perf_model', 'ops_perf_device', 'ops_detail']),
  };
  const ROUTE_DEFAULTS = {
    health_view: 'overview',
    health_sort: 'platform',
    health_parity_state: 'action',
    health_parity_area: 'all',
    health_definition_filter: 'upstream_only',
    health_definition_search: '',
    analytics_view: 'groups',
    analytics_search: '',
    analytics_amd_filter: 'attention',
    analytics_dns_scope: 'amd',
    analytics_dns_window: '24h',
    agent_window: '7d',
    agent_gpu: 'all',
    agent_node: '',
    agent_cofail: '180',
    agent_excl_cancel: '1',
    agent_nightly: '0',
    agent_signal: 'infra',


    omni_mapping_range: '7d',
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

  function populationSemantics(payload) {
    const p = payload || {};
    const c = p.source_coverage || {};
    if (p.count_semantics === 'lower_bound' || c.population_semantics === 'lower_bound' || c.authoritative_complete === false || (p.publication_retention || {}).complete_relative_to_source === false) return 'lower_bound';
    return p.count_semantics === 'complete' || (c.population_semantics === 'complete' && c.authoritative_complete === true) ? 'complete' : 'lower_bound';
  }

  function observedCountLabel(count, semantics) {
    const rendered = integer(count);
    if (rendered === '-') return rendered;
    return semantics === 'lower_bound' ? '≥' + rendered : rendered;
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
    if (['failed', 'failing', 'hard', 'incident', 'error', 'red', 'critical', 'surge', 'broken'].includes(stateName)) return 'is-danger';
    if (['soft', 'soft_fail', 'soft_failed', 'soft_failing', 'warning', 'attention', 'elevated', 'waiting'].includes(stateName)) return 'is-warning';
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

  function isCanonicalAmdQueue(queue) {
    const name = String(queue || '').trim().toLowerCase();
    return /^amd_mi\d{3,4}b?(?:_|$)/.test(name) && !name.startsWith('amd_mi355b');
  }

  function isAmdMiHardware(hardware) {
    return /^mi\d{3,4}$/.test(String(hardware || '').toLowerCase());
  }

  function isAmdRuntimeJob(job) {
    if (!job || job.no_gpu === true || job.source_no_gpu === true
      || job.device === 'cpu' || job.gpu_count === 0 || job.num_gpus === 0) return false;
    const raw = String(job.raw_name || job.name || job.job_name || '');
    if (/:(?:computer|amd):\s*\(\s*cpu(?:\s|\))/i.test(raw)) return false;
    const queue = job.queue || job.q;
    if (queue) return isCanonicalAmdQueue(queue);
    const prefix = raw.match(/^((?:amd_)?mi\d{3,4}b?(?:_[a-z0-9_-]+)?):/i);
    return Boolean(prefix && isCanonicalAmdQueue(prefix[1].startsWith('amd_') ? prefix[1] : 'amd_' + prefix[1]));
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
    const matches = Array.from(new Set(hardware)).filter(isAmdMiHardware).sort(compareText);
    matches.forEach(function (id) {
      const option = n('option', '', hardwareDisplayLabel(id));
      option.value = id;
      option.selected = current === id;
      select.append(option);
    });
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

  function setQueryValue(name, next, options) {
    try {
      const url = new URL(window.location.href);
      const isDefault = Object.prototype.hasOwnProperty.call(ROUTE_DEFAULTS, name)
        && String(next) === String(ROUTE_DEFAULTS[name]);
      if (next === null || next === undefined || next === '' || isDefault) url.searchParams.delete(queryName(name));
      else url.searchParams.set(queryName(name), String(next));
      const method = options && options.history === 'push' ? 'pushState' : 'replaceState';
      window.history[method](null, '', url.pathname + url.search + url.hash);
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
    setQueryValue(queryKey || key, next, {history: 'push'});
    render(tabId, true);
  }

  function syncRouteState(tabId) {
    const specs = {
      'ci-health': [
        ['healthView', 'health_view', ['overview', 'parity', 'coverage', 'mirrors']],
        ['healthCoverageSort', 'health_sort', ['platform', 'name', 'area']],
        ['healthParityState', 'health_parity_state', ['all', 'existing', 'unsupported', 'action']],
        ['healthParityArea', 'health_parity_area', null],
      ],
      'ci-analytics': [
        ['analyticsView', 'analytics_view', ['groups', 'nightlies', 'latency', 'dns', 'agent-health']],
        ['analyticsSearch', 'analytics_search', null],
        ['analyticsAmdFilter', 'analytics_amd_filter', ['attention', 'all', 'passing', 'incident', 'missing', 'mixed']],
        ['analyticsDnsScope', 'analytics_dns_scope', ['canonical', 'amd']],
        ['analyticsDnsWindow', 'analytics_dns_window', ['1h', '3h', '12h', '24h', '72h', '168h', '720h']],
        ['agentWindow', 'agent_window', ['1d', '3d', '7d', '14d', '30d', '60d']],
        ['agentGpu', 'agent_gpu', null],
        ['agentNode', 'agent_node', null],
        ['agentCofail', 'agent_cofail', ['30', '60', '120', '180', '360', '720', '1440']],
        ['agentExclCancel', 'agent_excl_cancel', ['0', '1']],
        ['agentNightly', 'agent_nightly', ['0', '1']],
        ['agentSignal', 'agent_signal', ['infra', 'hard', 'all']],
      ],
      'ci-omni': [
        ['omniMappingRange', 'omni_mapping_range', ['6h', '1d', '3d', '7d', '1m', '3m']],
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
    let nextUrl = null;
    try { nextUrl = new URL(window.location.href); } catch (_) {}
    Object.entries(updates || {}).forEach(function (entry) {
      state[entry[0]] = entry[1];
      if (!nextUrl) return;
      const queryKey = entry[0].replace(/[A-Z]/g, function (letter) { return '_' + letter.toLowerCase(); });
      const parameter = queryName(queryKey);
      const isDefault = Object.prototype.hasOwnProperty.call(ROUTE_DEFAULTS, queryKey)
        && String(entry[1]) === String(ROUTE_DEFAULTS[queryKey]);
      if (entry[1] === null || entry[1] === undefined || entry[1] === '' || isDefault) nextUrl.searchParams.delete(parameter);
      else nextUrl.searchParams.set(parameter, String(entry[1]));
    });
    if (nextUrl) {
      nextUrl.hash = tabId;
      window.history.pushState(null, '', nextUrl.pathname + nextUrl.search + nextUrl.hash);
    }
    if (window.__dashboardNav && typeof window.__dashboardNav.switchTab === 'function') {
      window.__dashboardNav.switchTab(tabId, {updateHash: false});
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
    const groupLabel = ariaLabel || 'View options';
    wrap.setAttribute('role', 'group');
    wrap.setAttribute('aria-label', groupLabel);
    const controls = [];
    for (const item of items) {
      const b = n('button', 'ops-segment' + (item.id === current ? ' is-active' : ''), item.label);
      b.type = 'button';
      b.setAttribute('aria-pressed', item.id === current ? 'true' : 'false');
      b.addEventListener('click', function () {
        if (item.id === current) return;
        pendingSegmentFocus = {group: groupLabel, id: item.id};
        onChange(item.id);
        requestAnimationFrame(function () {
          if (!b.isConnected || !pendingSegmentFocus
            || pendingSegmentFocus.group !== groupLabel || pendingSegmentFocus.id !== item.id) return;
          b.focus({preventScroll: true});
          pendingSegmentFocus = null;
        });
      });
      controls.push({item: item, control: b});
      wrap.append(b);
    }
    const active = controls.find(function (entry) { return entry.item.id === current; });
    if (active) requestAnimationFrame(function () {
      if (!pendingSegmentFocus || pendingSegmentFocus.group !== groupLabel
        || pendingSegmentFocus.id !== current) return;
      active.control.focus({preventScroll: true});
      pendingSegmentFocus = null;
    });
    return wrap;
  }

  function tabList(items, current, onChange, ariaLabel) {
    const wrap = n('div', 'ops-tabs ops-health-tabs');
    wrap.setAttribute('role', 'group');
    wrap.setAttribute('aria-label', ariaLabel || 'Views');
    const controls = [];
    items.forEach(function (item, index) {
      const control = n('button', 'ops-tab' + (item.id === current ? ' is-active' : ''), item.label);
      control.type = 'button';
      control.setAttribute('aria-pressed', item.id === current ? 'true' : 'false');
      control.tabIndex = item.id === current ? 0 : -1;
      control.addEventListener('click', function () {
        if (item.id === current) return;
        pendingTabFocus = item.id;
        onChange(item.id);
      });
      control.addEventListener('keydown', function (event) {
        let targetIndex = null;
        if (event.key === 'ArrowRight') targetIndex = (index + 1) % items.length;
        else if (event.key === 'ArrowLeft') targetIndex = (index - 1 + items.length) % items.length;
        else if (event.key === 'Home') targetIndex = 0;
        else if (event.key === 'End') targetIndex = items.length - 1;
        if (targetIndex === null) return;
        event.preventDefault();
        controls[targetIndex].focus();
        if (items[targetIndex].id === current) return;
        pendingTabFocus = items[targetIndex].id;
        onChange(items[targetIndex].id);
      });
      controls.push(control);
      wrap.append(control);
    });
    const active = controls.find(function (control) { return control.getAttribute('aria-pressed') === 'true'; });
    if (active) requestAnimationFrame(function () {
      const centered = active.offsetLeft - Math.max(0, (wrap.clientWidth - active.offsetWidth) / 2);
      wrap.scrollLeft = Math.max(0, centered);
      if (pendingTabFocus === current) {
        active.focus({preventScroll: true});
        pendingTabFocus = '';
      }
    });
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

  function statusStrip(items, ariaLabel) {
    const strip = n('section', 'ops-status-strip');
    strip.setAttribute('aria-label', ariaLabel || 'Operational summary');
    strip.style.setProperty('--ops-status-columns', String(Math.min(5, Math.max(1, items.length))));
    for (const item of items) {
      let cell;
      if (item.url) {
        cell = externalLink('', item.url, 'ops-status-item ops-linked-metric ' + (item.tone || ''));
      } else if (item.static) {
        cell = n('div', 'ops-status-item ' + (item.tone || ''));
      } else {
        cell = n('button', 'ops-status-item ops-linked-metric ' + (item.tone || ''));
        cell.type = 'button';
        cell.addEventListener('click', item.onOpen || function () { openMetricDetail(item); });
      }
      const behavior = item.url ? ' Opens source in a new tab.' : item.static ? '' : ' Activate to inspect.';
      cell.setAttribute('aria-label', item.label + ': ' + value(item.value) + '. ' + (item.meta || '') + behavior);
      add(cell, [
        n('div', 'ops-stat-label', item.label),
        n('div', 'ops-stat-value', value(item.value)),
        item.meta ? n('div', 'ops-stat-meta', item.meta) : null,
        item.actionLabel ? n('div', 'ops-stat-action', item.actionLabel) : null,
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
    const filters = (config.filters || []).map(function (filter) {
      const select = n('select', 'ops-select');
      select.setAttribute('aria-label', filter.label || 'Filter evidence');
      (filter.options || []).forEach(function (option) {
        const control = n('option', '', option.label);
        control.value = option.value;
        control.selected = option.value === (filter.initialValue || 'all');
        select.append(control);
      });
      return {config: filter, control: select};
    });
    add(toolbar, [search].concat(filters.map(function (filter) { return filter.control; }), [n('div', 'ops-toolbar-spacer'), count]));
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
      let filtered = query ? rows.filter(function (row) {
        return normalizeLabel(searchable(row)).includes(query);
      }) : rows;
      filters.forEach(function (filter) {
        const selected = filter.control.value;
        if (selected === 'all') return;
        filtered = filtered.filter(function (row) {
          return filter.config.predicate(row, selected);
        });
      });
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
    filters.forEach(function (filter) {
      filter.control.addEventListener('change', function () { page = 0; renderRows(); });
    });
    renderRows();
    openOverlay(config.title, config.subtitle || integer(rows.length) + ' evidence rows', content, true, config.id || 'table-browser');
    requestAnimationFrame(function () { search.focus(); });
  }

  function compactTablePanel(title, meta, columns, rows, options) {
    const config = options || {};
    const limit = Number(config.limit || 12);
    const preview = rows.slice(0, limit);
    const previewLabel = config.previewLabel || 'priority rows';
    const previewCaption = config.previewCaption === undefined
      ? integer(preview.length) + ' ' + previewLabel + ' of ' + integer(rows.length)
      : config.previewCaption;
    const root = panel(
      title,
      meta,
      dataTable(columns, preview, previewCaption, config.geometry || {}),
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
      const footerItems = [];
      if (!config.conciseCounts) footerItems.push(n('span', '', 'Showing ' + integer(preview.length) + ' of ' + integer(rows.length)));
      footerItems.push(
        button(config.buttonLabel || (config.conciseCounts ? 'Browse complete list' : 'Browse all ' + integer(rows.length)), function () {
          openTableBrowser({
            id: config.id,
            title: config.browserTitle || title,
            subtitle: config.browserSubtitle || meta,
            rows: rows,
            columns: config.browserColumns || columns,
            geometry: config.browserGeometry || config.geometry,
            searchText: config.searchText,
            searchPlaceholder: config.searchPlaceholder,
            searchLabel: config.searchLabel,
            initialQuery: config.initialQuery,
            pageSize: config.pageSize,
          });
        })
      );
      add(footer, footerItems);
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

  function openMixedOutcomeEvidence(candidate, options) {
    const publicationHistoryComplete = groupPublicationHistoryComplete(candidate);
    const sourcePipeline = candidate.source_pipeline || 'ci';
    const allObservations = evidenceObservations(candidate).filter(function (row) {
      return (!row.source_pipeline || row.source_pipeline === sourcePipeline)
        && Boolean(exactPipelineEvidenceUrl(row, sourcePipeline));
    }).sort(function (a, b) {
      return new Date(observationTimestamp(a) || 0) - new Date(observationTimestamp(b) || 0);
    });
    const scope = candidate.scope_label || candidate.scope || 'retained reliability window';
    const content = n('div', 'ops-evidence');
    const notice = n('div', 'ops-evidence-note ' + (publicationHistoryComplete ? 'is-info' : 'is-warning'));
    const hasPassing = allObservations.some(function (row) { return observationState(row) === 'passed'; });
    const hasIncidents = allObservations.some(isIncidentObservation);
    add(notice, [
      n('strong', '', hasPassing && hasIncidents ? 'Classification: mixed-outcome candidate. ' : 'Historical group evidence. '),
      n('span', '', 'Outcome, completion, and queue-wait history below use exact ' + sourcePipeline + ' observations. ' + (publicationHistoryComplete ? 'Any incident rate shown is not a test-case flake probability.' : 'This group history was byte-bounded; exact rows remain inspectable, but derived rates, percentiles, and deltas are unavailable.')),
    ]);
    content.append(notice);

    if (!allObservations.length) {
      content.append(n('div', 'ops-empty', 'The aggregate is available, but this snapshot predates per-run evidence. Regenerate operations_v2.json.gz to populate exact links.'));
      openOverlay(candidate.name || 'Group evidence', scope + ' evidence', content, true, 'group-' + (candidate.id || candidate.name));
      return;
    }

    let historyMode = 'main';
    const scopeControlHost = n('div');
    const scopeToolbar = n('div', 'ops-toolbar ops-evidence-toolbar');
    scopeToolbar.append(scopeControlHost);
    scopeToolbar.append(n('span', 'ops-panel-meta', publicationHistoryComplete
      ? 'Choose the complete branch=main cohort or its nightly subset.'
      : 'Choose the published branch=main evidence or its nightly subset; omitted rows are not inferred.'));
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
      const countPrefix = publicationHistoryComplete ? '' : '≥';
      const summary = n('div', 'ops-evidence-summary');
      add(summary, [
        evidenceSummaryItem(historyMode === 'main' ? 'MAIN OBSERVATIONS' : 'NIGHTLY OBSERVATIONS', countPrefix + integer(observations.length)),
        evidenceSummaryItem('PASSED', countPrefix + integer(passed), 'is-success'),
        evidenceSummaryItem('HARD INCIDENTS', countPrefix + integer(hard), hard ? 'is-danger' : ''),
        evidenceSummaryItem('SOFT INCIDENTS', countPrefix + integer(soft), soft ? 'is-warning' : ''),
        evidenceSummaryItem('INCIDENT RATE', publicationHistoryComplete ? percent(incidents.length, observations.length) : 'Unavailable', incidents.length ? 'is-warning' : 'is-success'),
      ]);
      historyHost.append(summary);

      const labels = observations.map(function (row) { return row.build_number ? '#' + row.build_number : shortDate(observationTimestamp(row)); });
      const evidence = observations.map(function (row) { return observationHistoryPoint(row, sourcePipeline); });
      const rollingPass = trailingPassStats(observations, 10);
      const rollingRates = rollingPass.map(function (row) { return publicationHistoryComplete ? row.rate : null; });
      const currentRolling = rollingPass[rollingPass.length - 1] || {};
      const outcomeSeries = [
        {label: 'Passed run', state: 'passed', color: '#35bb78'},
        {label: 'Soft failure', state: 'soft', color: '#e3a63a'},
        {label: 'Hard failure', state: 'hard', color: '#e06464'},
      ];
      const chartGrid = n('div', 'ops-grid ops-grid-2');
      const chartKey = 'group-' + String(candidate.id || candidate.name || 'history').replace(/[^a-z0-9]+/gi, '-').toLowerCase() + '-' + historyMode;
      const outcomeChart = chartPanel(
        'Outcome trend',
        publicationHistoryComplete
          ? 'Current trailing 10: ' + Number(currentRolling.rate || 0).toFixed(1) + '% (' + integer(currentRolling.passed) + '/' + integer(currentRolling.total) + '); bar color is the exact result'
          : 'Rate trend unavailable because the published group history is incomplete; use the exact rows below.',
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
      [['all', 'All results'], ['passing', 'Passing only'], ['incident', 'Failures only']].forEach(function (pair) {
        const option = n('option', '', pair[1]);
        option.value = pair[0];
        resultFilter.append(option);
      });
      resultFilter.value = ['passing', 'incident'].includes((options || {}).resultFilter) ? options.resultFilter : 'all';
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
                return 'Trailing 10-run pass rate: ' + Number(stat.rate || 0).toFixed(1) + '% (' + integer(stat.passed) + ' / ' + integer(stat.total) + ')';
              }
              return 'Result: ' + historyOutcomeLabel(observations[index]);
            }}}},
            scales: {
              x: {grid: {display: false}, ticks: {maxTicksLimit: 8}},
              y: {min: 0, max: 100, title: {display: true, text: 'Trailing 10-run pass rate'}, ticks: {stepSize: 20, callback: function (tick) { return tick + '%'; }}},
            },
          },
          evidenceTitle: (candidate.name || 'Test group') + ' exact outcomes and trailing 10-run pass rate',
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

  function reliabilityForPipeline(ops, pipeline) {
    const reliability = (ops || {}).reliability || {};
    return pipeline === 'ci' && reliability.source_pipeline === 'ci'
      && reliability.job_scope === 'amd_gpu' && reliability.hardware_scope === 'amd_mi_gpu'
      && (reliability.group_catalog || []).every(function (row) { return isAmdMiHardware(row.hardware) && isAmdRuntimeJob(row); })
      ? reliability : {};
  }

  function canonicalReliability(ops) {
    return reliabilityForPipeline(ops, 'ci');
  }

  function reliabilityScopeInfo(reliability) {
    reliability = reliability && typeof reliability === 'object' ? reliability : {};
    const explicit = reliability.scope || reliability.observation_scope || reliability.denominator_scope || '';
    const text = JSON.stringify([explicit, reliability.cohort, reliability.denominator, reliability.source_pipeline, reliability.evidence_definitions]).toLowerCase();
    const available = reliability.available === true
      && reliability.source_pipeline === 'ci'
      && reliability.job_scope === 'amd_gpu' && reliability.hardware_scope === 'amd_mi_gpu'
      && ((reliability.cohort || {}).available === true);
    const allMain = available && /all[-_ ]main|origin\/main|branch.?main/.test(text) && !/nightly job runs|night(?:ly|lies)[-_ ]only/.test(text);
    const pipeline = reliabilitySourcePipeline(reliability);
    const source = 'AMD MI main CI';
    return {
      allMain: allMain,
      available: available,
      pipeline: pipeline,
      label: allMain ? 'All-main reliability - ' + source : available ? 'Retained nightly reliability - ' + source : 'AMD MI reliability unavailable',
      detail: allMain ? 'All completed ' + source + ' branch=main builds in the retained window' : available ? 'Current payload contains nightly-only reliability; the all-main ledger has not landed yet' : 'The AMD MI main cohort is absent; nightly data was not substituted',
    };
  }

  const reliabilityCatalogCache = new WeakMap();
  const reliabilityCatalogIndexCache = new WeakMap();

  function publicationCount(block, key) {
    const raw = (block || {})[key];
    const parsed = Number(raw);
    return Number.isFinite(parsed) && parsed >= 0 ? parsed : 0;
  }

  function groupPublicationHistoryComplete(group) {
    if (!group || typeof group !== 'object') return true;
    const retained = Number(group.retained_observation_count);
    const sourceRetained = Number(group.source_retained_observation_count);
    const aggregate = Number(group.observation_count);
    const sourceCountMatches = !Number.isFinite(retained)
      || !Number.isFinite(sourceRetained)
      || retained === sourceRetained;
    const aggregateCountMatches = !Number.isFinite(aggregate)
      || !Number.isFinite(sourceRetained)
      || aggregate === sourceRetained;
    return group.publication_history_complete !== false
      && group.history_truncated !== true
      && publicationCount(group, 'excluded_observation_count') === 0
      && publicationCount(group, 'publication_observation_fields_truncated_count') === 0
      && sourceCountMatches
      && aggregateCountMatches;
  }

  function agentSourceCoverage(agentHealth) {
    if (!agentHealth || !Array.isArray(agentHealth.pipelines)
      || agentHealth.pipelines.length !== 1 || agentHealth.pipelines[0] !== 'ci'
      || agentHealth.max_window_days !== 60) return null;
    const retention = agentHealth.retention || {};
    const scope = retention.pipeline_scope || {};
    const states = ['creating', 'scheduled', 'running', 'failing', 'blocked', 'canceling'];
    const isVersion2 = scope.version === 2;
    const legs = isVersion2 ? ['created'] : ['created', 'older_finished', 'older_active'];
    const dayCounts = [retention.original_day_count, retention.retained_day_count, retention.dropped_oldest_day_count];
    if (retention.configured_days !== 60 || typeof retention.byte_limited !== 'boolean'
      || dayCounts.some(function (count) { return !Number.isSafeInteger(count) || count < 0; })
      || retention.original_day_count !== retention.retained_day_count + retention.dropped_oldest_day_count) return null;
    if ((scope.version !== 1 && !isVersion2) || !Number.isInteger(scope.requested_days)
      || scope.requested_days < 1 || scope.requested_days > 60
      || scope.basis !== (isVersion2 ? 'terminal_jobs_by_build_created_at' : 'terminal_jobs_by_started_at')
      || scope.exhaustive !== true
      || scope.attempt_policy !== 'latest_attempt_per_step'
      || scope.terminal_time_policy !== 'finished_at_or_terminal_build_bound_for_canceled'
      || typeof scope.complete_window !== 'boolean'
      || !scope.discovery_legs || typeof scope.discovery_legs !== 'object' || Array.isArray(scope.discovery_legs)
      || Object.keys(scope.discovery_legs).length !== legs.length
      || legs.some(function (leg) { return scope.discovery_legs[leg] !== true; })) return null;
    if (isVersion2) {
      if (scope.eligible_completion !== 'current_ci_build_creation_cohort_with_provable_completion'
        || scope.day_basis !== 'build_created_at_utc'
        || Object.prototype.hasOwnProperty.call(scope, 'finished_job_source')
        || Object.prototype.hasOwnProperty.call(scope, 'active_build_states')) return null;
    } else if (!Array.isArray(scope.active_build_states) || scope.active_build_states.length !== states.length
      || new Set(scope.active_build_states).size !== states.length
      || states.some(function (status) { return !scope.active_build_states.includes(status); })
      || Object.prototype.hasOwnProperty.call(scope, 'eligible_completion')
      || Object.prototype.hasOwnProperty.call(scope, 'day_basis')
      || Object.prototype.hasOwnProperty.call(scope, 'finished_job_source')) return null;
    function utcClock(raw) {
      if (typeof raw !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|\+00:00)$/.test(raw)) return NaN;
      const instant = Date.parse(raw);
      return Number.isFinite(instant) && new Date(instant).toISOString().replace('.000Z', 'Z') === raw.replace('+00:00', 'Z') ? instant : NaN;
    }
    const start = utcClock(scope.collected_from);
    const end = utcClock(scope.collected_to);
    const clock = utcClock(agentHealth.generated_at);
    if (!Number.isFinite(start) || !Number.isFinite(end) || end !== clock || start >= end) return null;
    const expectedStart = new Date(end - scope.requested_days * 86400000);
    expectedStart.setUTCHours(0, 0, 0, 0);
    const expectedComplete = scope.requested_days === 60 && retention.dropped_oldest_day_count === 0;
    if (start !== expectedStart.getTime() || scope.complete_window !== expectedComplete) return null;
    return {start: start, end: end};
  }

  function agentSourceHistoryComplete(agentHealth, startDay) {
    const retention = ((agentHealth || {}).retention) || {};
    const scope = retention.pipeline_scope || {};
    const coverage = agentSourceCoverage(agentHealth);
    const start = typeof startDay === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(startDay)
      ? Date.parse(startDay + 'T00:00:00Z') : NaN;
    const scopeComplete = coverage && (startDay === undefined ? scope.complete_window
      : Number.isFinite(start) && new Date(start).toISOString().slice(0, 10) === startDay
        && start >= coverage.start && start < coverage.end);
    return Boolean(scopeComplete)
      && retention.byte_limited === false
      && retention.dropped_oldest_day_count === 0
      && retention.original_day_count === retention.retained_day_count;
  }

  function agentPipelineScopeLabel(agentHealth) {
    const pipelines = Array.from(new Set((Array.isArray((agentHealth || {}).pipelines) ? agentHealth.pipelines : [])
      .filter(function (pipeline) { return typeof pipeline === 'string' && pipeline.trim(); })
      .map(function (pipeline) { return pipeline.trim(); })));
    return pipelines.length
      ? 'the ' + pipelines.join(' and ') + (pipelines.length === 1 ? ' pipeline' : ' pipelines')
      : 'the declared pipeline scope';
  }

  function reliabilityPublicationState(reliability) {
    const retention = ((reliability || {}).publication_retention) || {};
    const groups = retention.groups || {};
    const observations = retention.observations || {};
    const catalog = Array.isArray((reliability || {}).group_catalog) ? reliability.group_catalog : [];
    const incompleteGroupHistories = catalog.filter(function (group) {
      return !groupPublicationHistoryComplete(group);
    }).length;
    const catalogIncomplete = publicationCount(groups, 'omitted') > 0
      || publicationCount(observations, 'omitted') > 0
      || publicationCount(retention, 'sanitized_group_count') > 0
      || publicationCount(retention, 'sanitized_observation_count') > 0
      || publicationCount(retention, 'unpublishable_group_count') > 0
      || publicationCount(retention, 'unpublishable_observation_count') > 0
      || incompleteGroupHistories > 0;
    const state = {
      complete: !catalogIncomplete,
      groups: {
        source: publicationCount(groups, 'source'),
        published: publicationCount(groups, 'published'),
      },
      observations: {
        source: publicationCount(observations, 'source'),
        published: publicationCount(observations, 'published'),
      },
      incompleteGroupHistories: incompleteGroupHistories,
    };
    if (!catalogIncomplete) {
      state.message = '';
      return state;
    }
    state.message = 'Published reliability evidence is bounded: '
      + integer(state.groups.published) + ' of ' + integer(state.groups.source) + ' groups and '
      + integer(state.observations.published) + ' of ' + integer(state.observations.source) + ' exact observations are retained. '
      + 'Catalog-wide counts are lower bounds; rates, percentiles, and anomaly deltas derived from an incomplete group history are unavailable. Source-precomputed group aggregates remain complete.';
    return state;
  }

  function reliabilityCatalog(reliability) {
    if (!reliability || typeof reliability !== 'object') return [];
    if (reliabilityCatalogCache.has(reliability)) return reliabilityCatalogCache.get(reliability);
    let result;
    if (Array.isArray(reliability.group_catalog)) {
      result = reliability.group_catalog.map(function (row) { return Object.assign({}, row); });
    } else {
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
      result = Array.from(byIdentity.values());
    }
    reliabilityCatalogCache.set(reliability, result);
    return result;
  }

  function reliabilityCatalogIndex(reliability) {
    if (!reliability || typeof reliability !== 'object') return {byId: new Map(), byName: new Map()};
    if (reliabilityCatalogIndexCache.has(reliability)) return reliabilityCatalogIndexCache.get(reliability);
    const index = {byId: new Map(), byName: new Map()};
    reliabilityCatalog(reliability).forEach(function (row) {
      if (row.id !== null && row.id !== undefined && row.id !== '') index.byId.set(String(row.id), row);
      const key = normalizeLabel(row.name);
      if (!index.byName.has(key)) index.byName.set(key, []);
      index.byName.get(key).push(row);
    });
    reliabilityCatalogIndexCache.set(reliability, index);
    return index;
  }

  function groupReliability(reliability, name) {
    const key = normalizeLabel(name);
    const matches = reliabilityCatalogIndex(reliability).byName.get(key) || [];
    return matches.length === 1 ? matches[0] : null;
  }

  function groupReliabilityByRef(reliability, reference, name) {
    if (reference !== null && reference !== undefined && reference !== '') {
      const byId = reliabilityCatalogIndex(reliability).byId.get(String(reference));
      return byId || null;
    }
    return groupReliability(reliability, name);
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

  function openGroupDetail(group, ops, reliabilityRow, sourceReliability, options) {
    const reliability = sourceReliability || canonicalReliability(ops);
    const reference = (reliabilityRow || {}).evidence_ref || group.evidence_ref || group.group_id || ((group.main_reliability || {}).id);
    const row = groupReliabilityByRef(reliability, reference, group.name || group.label || group.group) || reliabilityRow;
    if (row && evidenceObservations(row).length) {
      const scope = reliabilityScopeInfo(reliability);
      row.scope_label = scope.label.toLowerCase();
      openMixedOutcomeEvidence(row, options);
      return;
    }
    const name = group.name || group.label || group.group || 'Test group';
    const content = n('div', 'ops-stack');
    if ((reference || name) && !reliabilityCatalog(reliability).length) {
      const loadHistory = button('Load 30-day run history', function () {
        loadHistory.disabled = true;
        loadHistory.textContent = 'Loading run history…';
        loadOperationSections(ops, ['reliability']).then(function (expanded) {
          backOverlay();
          openGroupDetail(group, expanded, reliabilityRow, undefined, options);
        }).catch(function (error) {
          loadHistory.disabled = false;
          loadHistory.textContent = 'Retry loading run history';
          console.error('Reliability evidence load failed:', error);
        });
      }, true);
      content.append(loadHistory);
    }
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
      content: content,
    });
  }

  function openGroupDetailWithEvidence(group, ops) {
    openGroupDetail(group, ops);
  }

  function nightlyBuildEvidence(build, scope) {
    const movement = nightlyFailureMovement(build);
    const labels = {hard: 'Current hard failures', soft: 'Current soft failures', new: 'New failures', recurring: 'Recurring failures', fixed: 'Fixed job variants'};
    const selectedScope = Object.prototype.hasOwnProperty.call(labels, scope) ? scope : 'movement';
    const movementAvailable = Boolean(movement) && movement.available !== false;
    function currentState(row) {
      const stateName = Object.prototype.hasOwnProperty.call(row, 'current_state') ? row.current_state : row.state || row.result;
      return String(stateName || '').trim().toLowerCase();
    }
    function severity(row) {
      const stateName = currentState(row);
      if (['soft', 'soft_fail', 'soft_failed'].includes(stateName)) return 'soft';
      if (['hard', 'failed', 'failing', 'incident', 'error', 'timed_out', 'broken', 'canceled', 'cancelled', 'expired'].includes(stateName)) return 'hard';
      return null;
    }
    function currentObservation(row) {
      return row.observed_in_current_build !== false
        && (!row.build_number || !build.number || Number(row.build_number) === Number(build.number));
    }
    let available = movementAvailable;
    let rows = [];
    if (selectedScope === 'hard' || selectedScope === 'soft') {
      const currentRows = build[selectedScope === 'hard' ? 'failed_groups' : 'soft_failed_groups'];
      available = build.has_test_results !== false && (Array.isArray(currentRows) || movementAvailable);
      const candidates = Array.isArray(currentRows) ? currentRows : movementAvailable ? (movement.new || []).concat(movement.recurring || []) : [];
      rows = available ? candidates.filter(function (row) { return currentObservation(row) && severity(row) === selectedScope; }) : [];
    } else if (movementAvailable) {
      [['new', 'New failure'], ['recurring', 'Recurring failure'], ['fixed', 'Fixed']].forEach(function (bucket) {
        if (selectedScope !== 'movement' && selectedScope !== bucket[0]) return;
        (movement[bucket[0]] || []).forEach(function (row) {
          if (selectedScope === 'fixed' && currentState(row) !== 'passed') return;
          if (['new', 'recurring'].includes(selectedScope) && (!currentObservation(row) || !severity(row))) return;
          const currentEvidence = selectedScope === 'fixed' && row.current_url
            ? {url: row.current_url, job_url: row.current_url, build_number: build.number, state: currentState(row)}
            : {};
          rows.push(Object.assign({}, row, currentEvidence, {lifecycle: bucket[1]}));
        });
      });
    }
    return {scope: selectedScope, label: labels[selectedScope] || 'Failure movement', available: available, rows: rows};
  }

  function openBuildDetail(build, title, scope) {
    const sourcePipeline = build.source_pipeline || 'ci';
    const failureMovement = nightlyFailureMovement(build);
    const evidence = nightlyBuildEvidence(build, scope);
    const severityScope = evidence.scope === 'hard' || evidence.scope === 'soft';
    const scoped = evidence.scope !== 'movement';
    const content = n('div', 'ops-stack');
    if (build.has_test_results === false) {
      const blocked = Number(build.test_jobs_blocked || 0);
      content.append(n(
        'div',
        'ops-evidence-note ' + (blocked ? 'is-danger' : 'is-warning'),
        blocked
          ? 'This nightly failed before test execution. ' + integer(blocked) + ' test jobs were dependency-blocked, so no pass/fail movement is inferred.'
          : 'This build has no parsed test-group signal. No pass/fail movement is inferred.',
      ));
    }
    const rows = evidence.rows;
    if (!evidence.available) {
      content.append(n('div', 'ops-evidence-note is-warning', scoped ? evidence.label + ' evidence is unavailable for this build.' : 'Failure movement is unavailable for this build. Raw build outcomes remain visible below.'));
    }
    if (rows.length) {
      const transitionColumns = [
      {label: 'Job variant', sticky: true, render: function (row) { return externalLink(row.display_name || row.name, exactPipelineEvidenceUrl(row, sourcePipeline)); }},
      severityScope
        ? {label: 'Current result', render: function (row) { return linkedBadge(row.current_state || row.state || row.result, exactPipelineEvidenceUrl(row, sourcePipeline)); }}
        : {label: 'Change', render: function (row) { return linkedBadge(row.lifecycle, exactPipelineEvidenceUrl(row, sourcePipeline), null, row.lifecycle === 'New failure' ? 'is-danger' : row.lifecycle === 'Fixed' ? 'is-success' : 'is-warning'); }},
      {label: 'Queue', render: function (row) { return n('span', 'ops-mono', value(row.queue)); }},
      ];
      content.append(compactTablePanel(evidence.label, integer(rows.length) + (scoped ? ' matching job variants' : ' observed changes'), transitionColumns, rows, {
        id: 'build-transition-browser-' + evidence.scope,
        limit: 30,
        browserSubtitle: scoped ? evidence.label + ' in this exact Buildkite nightly' : 'New failures, recurring failures, and fixes observed in this exact Buildkite nightly comparison',
        searchPlaceholder: 'Filter job variant, change, or queue',
        searchText: function (row) { return [row.display_name, row.name, row.lifecycle, row.queue].join(' '); },
      }));
    } else if (evidence.available && scoped) {
      content.append(n('div', 'ops-empty', 'No ' + evidence.label.toLowerCase() + ' are observed in this build.'));
    }
    openDetailDrawer({
      id: 'build-' + value(build.number) + '-' + evidence.scope,
      title: title || (build.number ? 'AMD build #' + build.number : 'AMD build'),
      subtitle: scoped ? evidence.label + ' evidence' : 'Build result and failure movement evidence',
      fields: [
        {label: 'State', value: value(build.state)},
        {label: 'Started', value: shortDate(build.created_at)},
        {label: 'Job variants observed', value: integer(build.total_groups)},
        {label: 'Test signal', value: build.has_test_results === false ? 'Unavailable' : 'Observed'},
        {label: 'Dependency-blocked test jobs', value: build.test_jobs_blocked ? integer(build.test_jobs_blocked) : null},
        scoped
          ? {label: evidence.label, value: evidence.available ? integer(rows.length) : 'Unavailable'}
          : {label: 'New failure / recurring failure / fixed', value: failureMovement && failureMovement.available !== false ? integer((failureMovement.new || []).length) + ' / ' + integer((failureMovement.recurring || []).length) + ' / ' + integer((failureMovement.fixed || []).length) : 'Unavailable'},
      ],
      sources: exactPipelineBuildUrl(build, sourcePipeline) ? [{label: 'Open Buildkite build', url: exactPipelineBuildUrl(build, sourcePipeline)}] : [],
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

  function loadGlobalScript(url, globalName, label, validate) {
    const exposed = window[globalName];
    if (exposed && (!validate || validate(exposed))) return Promise.resolve(exposed);
    const cacheKey = 'script:' + url;
    if (!cache.has(cacheKey)) {
      const request = new Promise(function (resolve, reject) {
        const script = document.createElement('script');
        function fail(message) {
          script.remove();
          reject(new Error(message));
        }
        script.src = url;
        script.async = true;
        script.onload = function () {
          const loaded = window[globalName];
          if (loaded && (!validate || validate(loaded))) resolve(loaded);
          else fail(label + ' loaded without exposing its browser API');
        };
        script.onerror = function () { fail(label + ' could not be loaded'); };
        document.head.append(script);
      });
      cache.set(cacheKey, request);
      request.catch(function () { if (cache.get(cacheKey) === request) cache.delete(cacheKey); });
    }
    return cache.get(cacheKey);
  }

  function loadChartLibrary() {
    return loadGlobalScript(CHART_LIBRARY_URL, 'Chart', 'Chart.js');
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
      canvas.setAttribute('aria-label', evidenceTitle + '. Use arrow keys to choose an observation and Enter to inspect it.');
      let activeEvidenceIndex = evidence.length - 1;
      canvas.addEventListener('keydown', function (event) {
        if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
          event.preventDefault();
          activeEvidenceIndex = Math.max(0, activeEvidenceIndex - 1);
        } else if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
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

  function retryDelay(milliseconds) {
    return new Promise(function (resolve) { window.setTimeout(resolve, milliseconds); });
  }

  async function fetchDecoded(path, decode) {
    let lastError = null;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        const separator = path.includes('?') ? '&' : '?';
        const requestPath = path + separator + '_health=' + Date.now() + '-' + attempt;
        const response = await fetch(requestPath, {cache: 'no-store'});
        if (!response.ok) throw new Error(path + ' returned HTTP ' + response.status);
        return await decode(response);
      } catch (error) {
        lastError = error;
        if (attempt < 2) await retryDelay(attempt === 0 ? 250 : 1000);
      }
    }
    throw lastError || new Error(path + ' could not be fetched');
  }

  function memoizedFetch(key, request) {
    cache.set(key, request);
    // A transient CDN or deployment race must not poison the page-wide cache.
    // The current render still receives the rejection, while a later render or
    // tab revisit gets a fresh bounded retry sequence.
    request.catch(function () {
      if (cache.get(key) === request) cache.delete(key);
    });
    return request;
  }

  async function fetchJSON(path) {
    if (!cache.has(path)) {
      memoizedFetch(path, fetchDecoded(path, function (response) {
        return response.json();
      }));
    }
    return cache.get(path);
  }

  function queueTimestamp(value) {
    const parsed = new Date(value || '').getTime();
    return Number.isFinite(parsed) ? parsed : -Infinity;
  }

  function queueSectionTimestamp(section) {
    return queueTimestamp((((section || {}).queue || {}).snapshot || {}).ts);
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
      if (state.healthView === 'parity') return ['test_group_parity'];
      if (state.healthView === 'mirrors') return ['test_group_parity'];
      if (state.healthView === 'coverage') return ['amd_test_health'];
      return [];
    }
    if (tabId === 'ci-analytics') {
      if (state.analyticsView === 'groups') return ['amd_test_health'];
      if (state.analyticsView === 'agent-health') return ['amd_agent_health'];
      if (state.analyticsView === 'nightlies') return ['nightly'];
      if (state.analyticsView === 'dns') return [];
      if (state.analyticsView === 'latency') return ['comparison'];
      return ['reliability'];
    }
    if (tabId === 'ci-omni') return ['omni', 'queue'];
    return [];
  }

  function resolveOperationSectionPath(relativePath) {
    const base = SOURCE_ASSETS.operationsManifest.slice(0, SOURCE_ASSETS.operationsManifest.lastIndexOf('/') + 1);
    return base + String(relativePath || '').replace(/^\/+/, '');
  }

  async function operationsManifest() {
    if (!operationsManifestPromise) {
      const request = fetchJSON(SOURCE_ASSETS.operationsManifest);
      operationsManifestPromise = request;
      request.catch(function () {
        if (operationsManifestPromise === request) operationsManifestPromise = null;
      });
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
      return {name: name, descriptor: descriptor};
    });
    const sections = await Promise.all(descriptors.map(function (entry) {
      const fallback = resolveOperationSectionPath(entry.descriptor.path);
      if (entry.name === 'queue') {
        return Promise.allSettled([
          fetchJSON(SOURCE_ASSETS.queueSection),
          fetchJSON(fallback),
        ]).then(function (results) {
          const candidates = results.filter(function (result) { return result.status === 'fulfilled'; }).map(function (result) { return result.value; });
          if (!candidates.length) throw new Error('No queue section is available');
          return candidates.sort(function (a, b) { return queueSectionTimestamp(b) - queueSectionTimestamp(a); })[0];
        });
      }
      return fetchJSON(fallback);
    }));
    const combined = mergeOperationPayload({}, ops || manifest.shell);
    sections.forEach(function (section) { mergeOperationPayload(combined, section); });
    return combined;
  }

  function loadAmdMirrorInventoryModule() {
    return loadGlobalScript(
      AMD_MIRROR_INVENTORY_MODULE_URL,
      'AmdMirrorInventory',
      'AMD mirror inventory renderer',
      function (module) {
        return typeof module.render === 'function' && typeof module.summaryCard === 'function';
      }
    );
  }

  function amdMirrorUiHelpers() {
    return {
      badge,
      button,
      compareText,
      externalLink,
      hardwareDisplayLabel,
      integer,
      linkButton,
      methodDisclosure,
      n,
      openDetailDrawer,
      openTableBrowser,
      panel,
      value,
    };
  }

  async function loadOperations(tabId) {
    if (tabId === 'ci-analytics' && state.analyticsView === 'dns') return {};
    const manifest = await operationsManifest();
    if (!manifest || !manifest.shell || !manifest.sections) {
      throw new Error('Operations manifest is incomplete');
    }
    if (tabId === 'ci-health' && state.healthView === 'overview') {
      const overviewDependencies = await Promise.all([
        loadOperationSections(manifest.shell, operationSectionNames(tabId)),
        loadOperationSections({}, ['test_group_parity']).catch(function () { return {}; }),
        loadAmdMirrorInventoryModule().catch(function () { return null; }),
      ]);
      return mergeOperationPayload(overviewDependencies[0], overviewDependencies[1]);
    }
    if (tabId === 'ci-health' && state.healthView === 'mirrors') {
      const mirrorDependencies = await Promise.all([
        loadOperationSections(manifest.shell, operationSectionNames(tabId)),
        loadAmdMirrorInventoryModule(),
      ]);
      return mirrorDependencies[0];
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

  function nightlyForCohort(ops, cohort) {
    const nightly = (ops || {}).nightly || {};
    const empty = {pipeline: 'ci', source_pipeline: 'ci', job_scope: 'amd_gpu', hardware_scope: 'amd_mi_gpu', cohort_id: 'ci-amd', builds: []};
    if (cohort !== 'ci-amd') return empty;
    const valid = function (candidate) {
      return candidate && candidate.source_pipeline === 'ci'
        && candidate.cohort_id === 'ci-amd' && candidate.job_scope === 'amd_gpu'
        && candidate.hardware_scope === 'amd_mi_gpu';
    };
    const cohorts = (Array.isArray(nightly.pipelines) ? nightly.pipelines : []).filter(valid);
    return cohorts.length === 1 ? cohorts[0]
      : cohorts.length === 0 && valid(nightly.canonical_history) ? nightly.canonical_history : empty;
  }

  function ciHealthPublicationRetentionMessage(nightly) {
    const retention = (nightly || {}).ci_health_publication_retention || {};
    const builds = retention.builds || {};
    if (retention.complete_relative_to_source !== false) return '';
    return 'The CI-health reporter retained ' + integer(builds.published) + ' of '
      + integer(builds.source) + ' newest whole build summaries. Latest-build and aggregate scalars remain source-complete; omitted historical enrichments are not inferred, and no rate or delta is derived from them.';
  }

  const CONFIRMED_INCIDENT_POLICY_ID = 'confirmed-incidents-v1';
  const OBSERVED_FAILURE_MOVEMENT_ID = 'observed-failure-movement-v1';

  function confirmedNightlyTransitions(build) {
    const transitions = (build || {}).transitions || {};
    return transitions.policy_id === CONFIRMED_INCIDENT_POLICY_ID ? transitions : null;
  }

  function nightlyFailureMovement(build) {
    const published = (build || {}).failure_movement || {};
    if (published.policy_id === OBSERVED_FAILURE_MOVEMENT_ID
      && ['new', 'recurring', 'fixed'].every(function (key) { return Array.isArray(published[key]); })) {
      return published;
    }

    const transitions = confirmedNightlyTransitions(build);
    if (!transitions || !['new', 'recurring', 'fixed'].every(function (key) { return Array.isArray(transitions[key]); })) return null;
    const currentPending = (transitions.pending_soft || []).filter(function (row) {
      return ['pending_started', 'pending_advanced'].includes(String((row || {}).transition_change || ''));
    });
    const newlyConfirmedRecurring = transitions.new.filter(function (row) {
      return String((row || {}).current_severity || '') === 'soft'
        && Number((row || {}).soft_streak || 0) > 1
        && String((row || {}).transition_change || '') === 'confirmed';
    });
    const newlyObserved = transitions.new.filter(function (row) {
      return !newlyConfirmedRecurring.includes(row);
    });
    const available = (build || {}).has_test_results !== false
      && (build || {}).transition_eligible !== false
      && Number(transitions.preceding_build_number || 0) > 0;
    return {
      policy_id: 'frontend-compatible-failure-movement',
      available: available,
      preceding_build_number: transitions.preceding_build_number,
      new: newlyObserved.concat(currentPending),
      recurring: (transitions.recurring || []).concat(newlyConfirmedRecurring),
      fixed: transitions.fixed || [],
    };
  }

  function nightlyFailureCount(build, key) {
    const movement = nightlyFailureMovement(build);
    return movement && movement.available !== false && Array.isArray(movement[key])
      ? movement[key].length
      : null;
  }

  function nightlyDisplayName(nightly) {
    return nightly.display_name || 'AMD MI main CI';
  }

  function latestAmd(ops) {
    return nightlyForCohort(ops, 'ci-amd');
  }

  function amdNightlyMovement(build) {
    const movement = nightlyFailureMovement(build);
    const policyAvailable = Boolean(movement) && movement.available !== false;
    const hasComparison = policyAvailable
      && Number(movement.preceding_build_number || 0) > 0;
    const newlyFailing = movement && Array.isArray(movement.new) ? movement.new.length : 0;
    const recurring = movement && Array.isArray(movement.recurring) ? movement.recurring.length : 0;
    const fixed = movement && Array.isArray(movement.fixed) ? movement.fixed.length : 0;
    return {
      policyAvailable: policyAvailable,
      hasComparison: hasComparison,
      newCount: newlyFailing,
      recurringCount: recurring,
      fixedCount: fixed,
      currentFailures: newlyFailing + recurring,
      previousFailures: recurring + fixed,
      delta: newlyFailing - fixed,
    };
  }

  function amdNightlyPresentation(build, healthSummary, snapshotGeneratedAt, nowMs) {
    const record = build || {};
    const summary = healthSummary || {};
    const latestStates = summary.latest_job_variant_state_counts || summary.latest_state_counts || {};
    const latestVariantCount = Number(summary.latest_job_variant_count !== undefined ? summary.latest_job_variant_count : summary.latest_group_count || 0);
    const signalBuild = Number(summary.latest_build_number || 0);
    const pipelineBuild = Number(record.number || 0);
    const pipelineState = String(record.state || 'unknown').toLowerCase();
    const inProgress = ['running', 'scheduled', 'creating', 'assigned', 'starting'].includes(pipelineState);
    const snapshotTimestamp = Date.parse(String(snapshotGeneratedAt || ''));
    const currentTimestamp = Number.isFinite(Number(nowMs)) ? Number(nowMs) : Date.now();
    const snapshotStale = Number.isFinite(snapshotTimestamp)
      && currentTimestamp - snapshotTimestamp > OPS_SNAPSHOT_MAX_AGE_MS;
    const blocked = Number(record.test_jobs_blocked || 0);
    const explicitlyNoSignal = Object.prototype.hasOwnProperty.call(record, 'has_test_results') && !record.has_test_results;
    const summaryMatchesBuild = latestVariantCount > 0
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
    const observedGroups = summaryMatchesBuild ? latestVariantCount : fallbackTotal;
    const hasSignal = !explicitlyNoSignal && Math.max(observedGroups, passed + soft + hard) > 0;
    const movement = amdNightlyMovement(record);
    const incidentCount = hard + soft;
    const comparisonReliable = hasSignal && movement.hasComparison
      && movement.currentFailures === incidentCount;

    if (!hasSignal) {
      return {
        label: blocked ? 'Infra blocked' : inProgress ? (snapshotStale ? 'Snapshot stale' : 'Awaiting results') : 'No test signal',
        tone: blocked ? 'is-danger' : inProgress && !snapshotStale ? 'is-info' : 'is-warning',
        meta: (pipelineBuild ? '#' + pipelineBuild + ' - ' : '')
          + (blocked
            ? integer(blocked) + ' test groups never started'
            : inProgress
              ? (snapshotStale ? 'Last published while Buildkite was running; no parsed test groups in this snapshot' : 'Buildkite is running; no parsed test groups yet')
              : 'no parsed test groups')
          + (signalBuild && signalBuild !== pipelineBuild ? '; latest test signal #' + signalBuild : ''),
        hasSignal: false,
        movementLabel: 'Movement unavailable',
        movementMeta: 'No test execution in the latest nightly; no change is inferred',
        movementTone: 'is-neutral',
        hasComparison: false,
        incidentCount: 0,
        incidentDelta: null,
      };
    }

    let movementLabel = !movement.policyAvailable
      ? 'Movement unavailable'
      : movement.hasComparison ? 'Movement unavailable' : 'No prior comparison';
    let movementMeta = !movement.policyAvailable
      ? 'Snapshot has no comparable failure movement'
      : movement.hasComparison
        ? 'Failure movement does not match the latest observed signal'
        : 'No comparable preceding eligible nightly';
    let movementTone = incidentCount ? 'is-warning' : 'is-neutral';
    if (comparisonReliable) {
      movementLabel = movement.delta > 0
        ? '+' + integer(movement.delta) + ' failures'
        : movement.delta < 0
          ? integer(Math.abs(movement.delta)) + ' fewer failures'
          : 'No net change';
      movementMeta = integer(movement.newCount) + ' new - ' + integer(movement.recurringCount) + ' recurring - ' + integer(movement.fixedCount) + ' fixed';
      movementTone = movement.delta > 0 ? 'is-danger' : movement.delta < 0 ? 'is-success' : movement.currentFailures ? 'is-warning' : 'is-success';
    }

    let label;
    let tone;
    if (inProgress) {
      label = incidentCount ? 'Running with failures' : 'Running clean';
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
      label = soft && !hard ? 'Soft observations' : 'Failures observed';
      tone = 'is-warning';
    } else if (movement.delta > 0) {
      label = 'More failures';
      tone = 'is-danger';
    } else if (movement.delta < 0) {
      label = 'Improved';
      tone = 'is-success';
    } else if (movement.newCount || movement.fixedCount) {
      label = 'Changed, net even';
      tone = 'is-warning';
    } else {
      label = 'Stable failure count';
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
      amd_logical_groups_not_fully_passing: 'AMD logical test groups not passing every route',
      omni_waiting: 'Omni jobs waiting across the fleet',
    };
    return labels[item.kind] || item.kind.replace(/_/g, ' ');
  }

  function inspectAttention(item, ops) {
    if (item.kind === 'amd_logical_groups_not_fully_passing') navigateTo('ci-health', {healthView: 'overview'});
    else if (item.kind === 'omni_waiting') navigateTo('ci-omni');
    else {
      const build = ((latestAmd(ops).builds || [])[0]) || {};
      const scope = item.kind === 'nightly_hard_failures' ? 'hard' : item.kind === 'nightly_soft_failures' ? 'soft' : undefined;
      if (build.number) openBuildDetail(build, attentionLabel(item), scope);
      else openMetricDetail({label: attentionLabel(item), value: item.count, meta: 'No linked build is available in this snapshot.'});
    }
  }

  async function renderHome(host, ops) {
    const amd = latestAmd(ops);
    const build = (amd.builds || [])[0] || {};
    const amdHealthSummary = currentAmdHealth(ops.amd_test_health).summary || {};
    const nightlyState = amdNightlyPresentation(build, amdHealthSummary, ops.generated_at);
    const paritySummary = currentTestGroupParity(ops.test_group_parity).summary || {};
    add(host, pageHeader('Command Center', 'Current AMD operations with observed nightly failure movement and direct paths to source evidence.', ops.generated_at));
    const ciHealthRetentionMessage = ciHealthPublicationRetentionMessage(amd);
    if (ciHealthRetentionMessage) {
      host.append(n('div', 'ops-evidence-note is-warning', ciHealthRetentionMessage));
    }
    add(host, statusStrip([
      {id: 'home-amd-nightly', label: 'LATEST AMD NIGHTLY', value: nightlyState.label, meta: nightlyState.meta, tone: nightlyState.tone, url: exactPipelineBuildUrl(build, 'ci'), observed: build.created_at, actionLabel: 'Open Buildkite ↗'},
      {id: 'home-upstream-parity', label: 'UPSTREAM TEST-GROUP PARITY', value: paritySummary.main_complete_groups === undefined ? 'Unavailable' : integer(paritySummary.main_complete_groups) + ' / ' + integer(paritySummary.applicable_groups) + ' on main', meta: paritySummary.upstream_logical_groups === undefined ? 'Current main CI parity inventory unavailable' : percent(paritySummary.main_complete_groups, paritySummary.applicable_groups) + ' · ' + integer(paritySummary.main_missing_groups) + ' missing', tone: Number(paritySummary.action_groups) ? 'is-warning' : 'is-success', onOpen: function () { navigateTo('ci-health', {healthView: 'parity'}); }, actionLabel: 'Open Upstream parity →'},
    ], 'Command Center summary'));

    const grid = n('div', 'ops-grid ops-grid-main-aside ops-home-grid');
    const attentionRows = (ops.attention || []).filter(function (item) { return !['queue_waiting', 'queue_zombies', 'gating_red_targets', 'target_groups_with_current_incidents', 'mixed_state_flaky_candidates'].includes(item.kind); });
    grid.append(panel('Needs attention', attentionRows.length + ' active signals', dataTable([
      {label: 'Operational signal', sticky: true, render: function (item) { return linkButton(attentionLabel(item), function () { inspectAttention(item, ops); }); }},
      {label: 'Severity', render: function (item) { return linkedBadge(item.severity, null, function () { inspectAttention(item, ops); }); }},
      {label: 'Count', numeric: true, render: function (item) { return linkButton(integer(item.count), function () { inspectAttention(item, ops); }); }},
    ], attentionRows), 'ops-home-primary'));

    const recent = (amd.builds || []).slice(0, 7);
    grid.append(panel('AMD nightly failure movement', 'Latest seven completed observations', dataTable([
      {label: 'Build', render: function (r) { return externalLink('#' + r.number, exactPipelineBuildUrl(r, 'ci'), 'ops-mono'); }},
      {label: 'Test signal', render: function (r) { return linkedBadge(r.has_test_results === false ? (Number(r.test_jobs_blocked || 0) ? 'Infra blocked' : 'Unavailable') : 'Observed', exactPipelineBuildUrl(r, 'ci'), function () { openBuildDetail(r); }, r.has_test_results === false ? 'is-danger' : 'is-success'); }},
      {label: 'New failure', numeric: true, render: function (r) { const count = nightlyFailureCount(r, 'new'); return linkButton(count === null ? '-' : integer(count), function () { openBuildDetail(r, undefined, 'new'); }); }},
      {label: 'Recurring failure', numeric: true, render: function (r) { const count = nightlyFailureCount(r, 'recurring'); return linkButton(count === null ? '-' : integer(count), function () { openBuildDetail(r, undefined, 'recurring'); }); }},
      {label: 'Fixed', numeric: true, render: function (r) { const count = nightlyFailureCount(r, 'fixed'); return linkButton(count === null ? '-' : integer(count), function () { openBuildDetail(r, undefined, 'fixed'); }); }},
      {label: 'Observed', render: function (r) { return shortDate(r.created_at); }},
    ], recent), 'ops-home-aside'));
    host.append(grid);

    let workData = {
      prs: [],
      issues: [],
      prsCountSemantics: 'lower_bound',
      issuesCountSemantics: 'lower_bound',
    };
    try {
      const loaded = await Promise.all([fetchJSON('data/vllm/prs.json'), fetchJSON('data/vllm/issues.json')]);
      workData = {
        prs: loaded[0].prs || [],
        issues: loaded[1].issues || [],
        prsCountSemantics: populationSemantics(loaded[0]),
        issuesCountSemantics: populationSemantics(loaded[1]),
      };
    } catch (_) {}
    const workPanel = n('section', 'ops-panel');
    const workHead = n('div', 'ops-panel-header');
    add(workHead, [n('h2', 'ops-panel-title', 'Engineering workbench'), segmented([
      {id: 'issues', label: 'Issues (' + observedCountLabel(workData.issues.length, workData.issuesCountSemantics) + ')'},
      {id: 'prs', label: 'PRs (' + observedCountLabel(workData.prs.length, workData.prsCountSemantics) + ')'},
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

  const MATRIX_INCIDENT_STATES = new Set(['failed', 'timed_out', 'broken', 'soft_fail', 'soft_failed']);
  const MATRIX_WAITING_STATES = new Set(['running', 'scheduled', 'assigned']);

  function matrixHealthPolicy(matrixSummary) {
    const summary = matrixSummary || {};
    const policies = summary.health_policies || {};
    const policy = policies.best_hardware;
    if (policy) {
      const included = Number(policy.included_groups || 0);
      const publishedCount = Number(summary.health_group_count || 0);
      if (!included || !publishedCount || publishedCount !== included) {
        return {
          passing_groups: 0,
          failing_groups: 0,
          waiting_groups: 0,
          unknown_groups: 0,
          included_groups: 0,
          pass_percentage: null,
          best_hardware_unavailable: true,
        };
      }
      return Object.assign({}, policy, {
        pass_percentage: included ? Number(policy.passing_groups || 0) / included * 100 : null,
      });
    }
    return {
      passing_groups: 0,
      failed_only_groups: 0,
      mixed_groups: 0,
      failing_groups: 0,
      waiting_groups: 0,
      unknown_groups: 0,
      ignored_mi355_only_groups: 0,
      inherited_mi355_groups: 0,
      resolved_groups: 0,
      included_groups: 0,
      pass_percentage: null,
      generic_groups: 0,
      mi355_sensitive_groups: 0,
      best_hardware_unavailable: true,
    };
  }

  function bestHardwareMatrixContract(matrixData) {
    const matrix = matrixData || {};
    const policy = matrixHealthPolicy(matrix.summary || {});
    const groups = Array.from(matrix.health_groups || []);
    const valid = !policy.best_hardware_unavailable
      && Number(policy.included_groups || 0) > 0
      && groups.length === Number(policy.included_groups || 0);
    return {policy: policy, groups: groups, valid: valid};
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

  function matrixGateKindLabel(group) {
    return group.classification === 'mi355-sensitive' ? 'MI355-sensitive' : 'Generic family';
  }

  function matrixHealthCollection(matrixData) {
    const rows = Array.from((matrixData || {}).rows || []);
    const publishedGroups = Array.from((matrixData || {}).health_groups || []);
    const rowsById = new Map(rows.map(function (row) { return [row.id, row]; }));
    return publishedGroups.map(function (published) {
        const members = Array.from(published.members || []);
        const memberRows = Array.from(new Set((published.member_row_ids || []).concat(
          members.map(function (member) { return member.row_id; })
        ))).map(function (id) { return rowsById.get(id); }).filter(Boolean);
        const states = members.map(function (member) {
          return String(member.state || '').toLowerCase();
        });
        const rawStatus = String(published.status || '').toLowerCase();
        const status = published.is_passing === true || ['pass', 'passed', 'passing'].includes(rawStatus)
          ? 'passing'
          : ['fail', 'failed', 'failing', 'incident', 'soft', 'soft_fail', 'soft_failed', 'hard'].includes(rawStatus)
            ? 'failed'
            : MATRIX_WAITING_STATES.has(rawStatus) ? 'waiting' : 'unknown';
        const gateKind = String(published.gate_kind || 'generic').toLowerCase();
        return {
          id: published.id,
          title: published.title,
          status: status,
          gateKind: gateKind,
          classification: gateKind.includes('mi355') ? 'mi355-sensitive' : 'generic',
          classificationReason: published.classification_reason || '',
          members: members,
          rows: memberRows,
          componentRows: memberRows,
          duplicateSize: memberRows.length,
          definitionCount: members.reduce(function (count, member) {
            const variants = member.definitions && member.definitions.length
              ? member.definitions : member.variants || [];
            return count + Math.max(1, variants.length);
          }, 0),
          architectures: Array.from(new Set((published.architectures || []).concat(
            members.map(function (member) { return member.architecture; })
          ).filter(Boolean))).sort(),
          passedCells: states.filter(function (result) { return result === 'passed'; }).length,
          incidentCells: states.filter(function (result) { return MATRIX_INCIDENT_STATES.has(result); }).length,
          waitingCells: states.filter(function (result) { return MATRIX_WAITING_STATES.has(result); }).length,
          unknownCells: states.filter(function (result) {
            return result !== 'passed' && !MATRIX_INCIDENT_STATES.has(result) && !MATRIX_WAITING_STATES.has(result);
          }).length,
          pairMatches: [],
        };
      }).sort(function (left, right) {
      const priority = {failed: 0, unknown: 1, waiting: 2, passing: 3};
      return priority[left.status] - priority[right.status] || String(left.title).localeCompare(String(right.title));
    });
  }

  function matrixGroupEvidence(group) {
    if ((group.members || []).length) {
      const memberRows = new Map((group.rows || []).map(function (row) { return [row.id, row]; }));
      const publishedEvidence = [];
      group.members.forEach(function (member) {
        const sourceRow = memberRows.get(member.row_id) || {};
        const nestedDefinitions = member.definitions && member.definitions.length
          ? member.definitions : member.variants || [];
        const definitions = nestedDefinitions.length ? nestedDefinitions : [member];
        definitions.forEach(function (definition) {
          publishedEvidence.push({
            definition: definition.label || member.label || member.title || group.title,
            architecture: definition.architecture || member.architecture || '',
            queue: definition.agent_pool || definition.queue || member.agent_pool || member.queue || (member.agent_pools || []).join(', ') || '',
            result: definition.state || member.state || 'unknown',
            url: exactPipelineEvidenceUrl({latest_url: definition.url || definition.latest_url || member.url || member.latest_url}, 'ci'),
            buildNumber: definition.build_number || member.build_number,
            commands: definition.commands || member.commands || [],
            commandIdentity: definition.command_fingerprint || member.command_fingerprint || sourceRow.command_fingerprint || '',
            sourceUrl: definition.source_url || member.source_url || '',
          });
        });
      });
      return publishedEvidence;
    }
    return [];
  }

  function openMatrixGroupEvidence(group, matrixData) {
    const evidenceRows = matrixGroupEvidence(group);
    const content = n('div', 'ops-evidence');
    const note = n('div', 'ops-evidence-note ' + matrixHealthTone(group.status));
    add(note, [
      n('strong', '', matrixHealthStatusLabel(group.status) + '. '),
      n('span', '', group.classification === 'mi355-sensitive'
        ? 'This hardware-sensitive obligation uses its exact MI355 route; another architecture cannot satisfy it.'
        : 'This generic family passes when at least one configured AMD route passes. Architecture disagreements remain visible below.'),
    ]);
    content.append(note);
    if (group.classificationReason) {
      content.append(n('p', 'ops-detail-description', group.classificationReason));
    }
    content.append(dataTable([
      {label: 'Definition', sticky: true, width: '390px', render: function (row) { return row.url ? externalLink(row.definition, row.url, 'ops-cell-primary') : row.definition; }},
      {label: 'Hardware', width: '110px', render: function (row) { return badge(row.architecture.toUpperCase(), 'is-neutral'); }},
      {label: 'Agent pool', width: '150px', render: function (row) { return value(row.queue); }},
      {label: 'Latest result', width: '130px', render: function (row) { return linkedBadge(row.result, row.url, null, toneForState(row.result)); }},
      {label: 'Command identity', width: '190px', render: function (row) { return row.commandIdentity ? n('span', 'ops-mono', row.commandIdentity) : n('span', 'ops-cell-muted', '-'); }},
      {label: 'Evidence', width: '170px', render: function (row) { const actions = n('div', 'ops-inline-actions'); if (row.url) actions.append(externalLink('Job', row.url)); if (row.sourceUrl) actions.append(externalLink('Source', row.sourceUrl)); return actions.childNodes.length ? actions : n('span', 'ops-cell-muted', '-'); }},
    ], evidenceRows, integer(evidenceRows.length) + ' exact AMD definitions', {name: 'matrix-unique-evidence', minWidth: '1190px'}));
    const commands = evidenceRows.filter(function (row) { return (row.commands || []).length; });
    if (commands.length) {
      const pre = n('pre', 'ops-code-block');
      pre.textContent = commands.map(function (row) {
        return '# ' + row.architecture.toUpperCase() + ' - ' + row.definition + '\n' + row.commands.join('\n');
      }).join('\n\n');
      content.append(panel('Executed commands', integer(commands.length) + ' architecture definitions with published commands', pre));
    }
    openDetailDrawer({
      id: 'matrix-health-' + group.id,
      title: group.title,
      subtitle: 'Test-group status and exact AMD route evidence',
      fields: [
        {label: 'Status', value: matrixHealthStatusLabel(group.status)},
        {label: 'Test-group classification', value: matrixGateKindLabel(group)},
        {label: 'Classification reason', value: group.classificationReason},
        {label: 'Source definitions', value: integer(group.definitionCount)},
        {label: 'Hardware', value: group.architectures.map(function (arch) { return arch.toUpperCase(); }).join(', ')},
        {label: 'Signal', value: integer(group.passedCells) + ' passing - ' + integer(group.incidentCells) + ' non-passing'},
        {label: 'Matrix rows', value: group.duplicateSize > 1 ? integer(group.duplicateSize) : null},
        {label: 'Shared title substring', value: Array.from(new Set(group.pairMatches.map(function (match) { return match.shared_substring; }))).join(', ') || null},
      ],
      sources: [
        {label: 'Open AMD test definitions', url: ((matrixData || {}).source || {}).yaml_url},
        {label: 'Open latest AMD build', url: ((matrixData || {}).source || {}).latest_build_url},
      ],
      content: content,
    });
  }

  async function openMatrixHealthBrowser(mode) {
    let matrixData;
    try {
      matrixData = await fetchJSON('data/vllm/ci/amd_test_matrix.json');
    } catch (error) {
      openMetricDetail({
        label: 'Configured AMD test groups',
        value: 'Unavailable',
        description: 'The AMD matrix payload could not be loaded.',
      });
      return;
    }
    if (((matrixData || {}).source || {}).pipeline !== 'ci') {
      openMetricDetail({label: 'Current main CI AMD hardware evidence', value: 'Unavailable', description: 'The matrix does not declare the current main CI source.'});
      return;
    }
    const contract = bestHardwareMatrixContract(matrixData);
    if (!contract.valid) {
      openMetricDetail({
        label: 'Configured AMD test groups',
        value: 'Unavailable',
        description: 'This snapshot does not contain a complete best-hardware policy and matching test-group inventory. Refresh the dashboard after the matrix collector publishes both together.',
        sources: [{label: 'Open published matrix', url: 'data/vllm/ci/amd_test_matrix.json'}],
      });
      return;
    }
    const allGroups = matrixHealthCollection(matrixData).filter(function (group) { return group.status !== 'ignored'; });
    const titles = {
      all: 'Configured AMD test groups',
      passing: 'Passing AMD test groups',
      failing: 'Failing AMD test groups',
      'no-signal': 'AMD groups without a terminal signal',
      'mi355-sensitive': 'MI355-sensitive AMD test groups',
      generic: 'Generic AMD test families',
      ignored: 'Legacy ignored MI355-only test groups',
    };
    openTableBrowser({
      id: 'unique-amd-health-' + (mode || 'all'),
      title: titles[mode || 'all'],
      subtitle: integer(allGroups.length) + ' configured AMD test groups; the best-hardware policy lets generic families use any passing AMD route while hardware-sensitive obligations use their exact route',
      rows: allGroups,
      columns: [
        {label: 'Test group', sticky: true, width: '390px', render: function (group) { return linkButton(group.title, function () { openMatrixGroupEvidence(group, matrixData); }); }},
        {label: 'Status', width: '130px', render: function (group) { return linkedBadge(matrixHealthStatusLabel(group.status), null, function () { openMatrixGroupEvidence(group, matrixData); }, matrixHealthTone(group.status)); }},
        {label: 'Classification', width: '180px', render: function (group) { return linkedBadge(matrixGateKindLabel(group), null, function () { openMatrixGroupEvidence(group, matrixData); }, group.classification === 'mi355-sensitive' ? 'is-info' : 'is-neutral'); }},
        {label: 'Hardware', width: '180px', render: function (group) { return group.architectures.map(function (arch) { return arch.toUpperCase(); }).join(', ') || '-'; }},
        {label: 'Classification reason', width: '420px', render: function (group) { return linkButton(group.classificationReason || '-', function () { openMatrixGroupEvidence(group, matrixData); }); }},
        {label: 'Route signal', width: '190px', render: function (group) { return integer(group.passedCells) + ' pass - ' + integer(group.incidentCells) + ' non-passing'; }},
        {label: 'Evidence', width: '140px', render: function (group) { return linkButton(integer(matrixGroupEvidence(group).filter(function (row) { return row.url; }).length) + ' jobs', function () { openMatrixGroupEvidence(group, matrixData); }); }},
      ],
      searchText: function (group) { return [group.title, group.status, group.classification, group.classificationReason, group.architectures.join(' '), matrixGroupEvidence(group).map(function (row) { return [row.definition, row.queue, row.commandIdentity, (row.commands || []).join(' ')].join(' '); }).join(' ')].join(' '); },
      searchPlaceholder: 'Search group, reason, command, hardware, or queue',
      filters: [
        {
          label: 'Filter configured test-group status',
          initialValue: mode === 'passing' || mode === 'failing' || mode === 'no-signal' ? mode : 'all',
          options: [{value: 'all', label: 'All statuses'}, {value: 'passing', label: 'Passing'}, {value: 'failing', label: 'Failing'}, {value: 'no-signal', label: 'No signal'}],
          predicate: function (group, selected) { return selected === 'no-signal' ? ['waiting', 'unknown'].includes(group.status) : selected === 'failing' ? group.status === 'failed' : group.status === selected; },
        },
        {
          label: 'Filter configured test-group classification',
          initialValue: mode === 'mi355-sensitive' || mode === 'generic' ? mode : 'all',
          options: [{value: 'all', label: 'All classifications'}, {value: 'generic', label: 'Generic families'}, {value: 'mi355-sensitive', label: 'MI355-sensitive'}],
          predicate: function (group, selected) { return group.classification === selected; },
        },
      ],
      geometry: {name: 'unique-amd-health', minWidth: '1620px'},
    });
  }

  function matrixHealthOverview(matrixSummary, policy, latestBuildNumber) {
    const root = n('section', 'ops-unique-health');
    const head = n('header', 'ops-panel-header ops-unique-health-header');
    const heading = n('div', 'ops-unique-health-heading');
    add(heading, [
      n('h2', 'ops-panel-title', 'Configured AMD test groups'),
      n('div', 'ops-panel-meta', (latestBuildNumber ? 'Latest observed matrix build #' + latestBuildNumber + ' - ' : '') + 'one fixed best-hardware policy'),
    ]);
    add(head, heading);
    root.append(head);

    const body = n('div', 'ops-unique-health-body');
    if (policy.best_hardware_unavailable) {
      const unavailable = n('div', 'ops-evidence-note is-warning');
      add(unavailable, [
        n('strong', '', 'Best-hardware metric unavailable. '),
        n('span', '', 'Complete best-hardware detail is unavailable or storage-bounded; rates and drill-downs stay hidden.'),
      ]);
      body.append(unavailable);
      root.append(body);
      return root;
    }
    const rate = n('button', 'ops-unique-health-rate');
    rate.type = 'button';
    rate.addEventListener('click', function () { openMatrixHealthBrowser('all'); });
    add(rate, [
      n('strong', '', policy.pass_percentage === null || policy.pass_percentage === undefined ? '-' : Number(policy.pass_percentage).toFixed(1) + '%'),
      n('span', '', 'Passing'),
    ]);
    const stats = n('div', 'ops-unique-health-stats');
    [
      {mode: 'all', label: 'Test groups', count: policy.included_groups, tone: 'is-neutral'},
      {mode: 'passing', label: 'Passing', count: policy.passing_groups, tone: 'is-passing'},
      {mode: 'failing', label: 'Failing', count: policy.failing_groups, tone: 'is-failing'},
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
      {mode: 'failing', label: 'Failing', count: Number(policy.failing_groups || 0), tone: 'is-failing'},
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
    const facts = [
      integer(policy.generic_group_count !== undefined ? policy.generic_group_count : policy.generic_groups || 0) + ' generic best-of-hardware families',
      integer(policy.mi355_sensitive_group_count !== undefined ? policy.mi355_sensitive_group_count : policy.mi355_sensitive_groups || 0) + ' explicit MI355-sensitive obligations',
      'no-signal groups remain in the denominator',
    ];
    add(footer, [
      n('span', '', facts.join(' - ')),
      button('Browse all ' + integer(policy.included_groups) + ' test groups', function () { openMatrixHealthBrowser('all'); }),
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

  function amdMatrixSortDescription(mode) {
    if (mode === 'name') return 'Test groups sorted alphabetically by name';
    if (mode === 'area') return 'Test areas sorted alphabetically, then by test-group name';
    return 'Grouped by first configured platform: MI250, MI300, MI325, then MI355';
  }

  function healthTabs(host) {
    host.append(tabList([
      {id: 'overview', label: 'Overview'}, {id: 'parity', label: 'Upstream parity'},
      {id: 'coverage', label: 'AMD hardware'},
      {id: 'mirrors', label: 'AMD mirrors'},
    ], state.healthView, function (id) { setRouteState('ci-health', 'healthView', id, 'health_view'); }, 'CI Health view'));
  }

  function logicalTestGroupPresentation(groups) {
    const summary = groups || {};
    if (summary.available === false || summary.total === undefined) {
      return {available: false, value: 'Unavailable', meta: 'No aligned runtime test-group signal', tone: 'is-warning'};
    }
    const total = Number(summary.total || 0);
    const partial = Number(summary.partial || 0);
    const passingAny = Number(summary.passing || 0);
    const passingAll = summary.passing_all === undefined ? Math.max(0, passingAny - partial) : Number(summary.passing_all || 0);
    const nonPassing = summary.non_passing === undefined ? Math.max(0, total - passingAny) : Number(summary.non_passing || 0);
    return {
      available: true,
      value: integer(passingAny) + ' / ' + integer(total) + ' passing',
      meta: integer(passingAll) + ' pass on every route · ' + integer(partial) + ' pass on some hardware only · ' + integer(nonPassing) + ' non-passing everywhere',
      tone: partial || nonPassing ? 'is-warning' : 'is-success',
    };
  }

  function methodDisclosure(title, paragraphs) {
    const details = n('details', 'ops-method-details');
    details.append(n('summary', '', title));
    const body = n('div', 'ops-method-details-body');
    (paragraphs || []).forEach(function (paragraph) {
      if (!paragraph) return;
      const row = n('p', '');
      add(row, Array.isArray(paragraph) ? paragraph : [paragraph]);
      body.append(row);
    });
    details.append(body);
    return details;
  }

  function currentTestGroupParity(payload) {
    const source = (payload || {}).source || {};
    return source.pipeline === 'ci' && /^[0-9a-f]{40}$/.test(source.current_definition_commit_sha || '')
      ? payload : {available: false, source: {}, summary: {}, groups: []};
  }

  function testGroupParityState(stateName) {
    const presentations = {
      existing: {label: '● Covered on main', tone: 'is-success'},
      unsupported: {label: '■ Not targeted / unsupported', tone: 'is-not-targeted'},
      action: {label: '● Potential open gap', tone: 'is-danger'},
    };
    return presentations[stateName] || {label: value(stateName), tone: 'is-neutral'};
  }

  function testGroupParityRows(payload, stateName, areaName) {
    return (Array.isArray((payload || {}).groups) ? payload.groups : []).filter(function (row) {
      return (stateName === 'all' || row.state === stateName)
        && (areaName === 'all' || row.area === areaName);
    }).sort(function (left, right) {
      const rank = {action: 0, unsupported: 1, existing: 2};
      return Number(rank[left.state] || 0) - Number(rank[right.state] || 0)
        || String(left.area || '').localeCompare(String(right.area || ''))
        || Number(left.id || 0) - Number(right.id || 0);
    });
  }

  function testGroupDefinitionState(row) {
    const status = (row.definition_resolution || {}).status;
    return {
      resolved: 'Current',
      unresolved: 'Removed or changed',
      ambiguous: 'Mapping needs review',
      unavailable: 'Snapshot unavailable',
      unconfigured: 'Unlinked',
    }[status] || 'Current source snapshot';
  }

  function openTestGroupParityDetail(row, payload) {
    const source = (payload || {}).source || {};
    const definition = row.definition_resolution || {};
    const commit = source.main_commit || source.commit_sha || '';
    const commitUrl = source.main_commit_url || (commit ? 'https://github.com/vllm-project/vllm/commit/' + commit : '');
    const presentation = testGroupParityState(row.state);
    openDetailDrawer({
      id: 'parity-group-' + row.id,
      title: row.title || 'Upstream logical test group',
      subtitle: 'Configured AMD routes against upstream source definitions',
      description: row.assessment || 'No source-derived assessment is available.',
      fields: [
        {label: 'Inventory number', value: row.id},
        {label: 'Test area', value: row.area},
        {label: 'Configured coverage', value: presentation.label.replace(/^[●■]\s*/, '')},
        {label: 'Current CI definition', value: testGroupDefinitionState(row)},
        {label: 'Source name', value: row.title},
        {label: 'AMD route mode', value: {required: 'Required', optional: 'Optional', soft_fail: 'Soft fail', missing: 'Missing', not_applicable: 'Not applicable'}[row.gate_kind] || 'Unavailable'},
        {label: 'Current replacement names', value: (definition.successor_labels || []).join(' · ') || '—'},
        {label: 'Definition changes', value: definition.note || '—'},
        {label: 'Upstream CUDA variants', value: row.cuda_variants},
        {label: 'ROCm assessment', value: row.assessment},
      ],
      sources: [
        commitUrl ? {label: 'Open current main CI source commit', url: commitUrl} : null,
        /^[0-9a-f]{40}$/.test(definition.commit_sha || '') ? {label: 'Open current CI definitions', url: 'https://github.com/vllm-project/vllm/tree/' + definition.commit_sha + '/.buildkite/test_areas'} : null,
      ],
    });
  }

  function testGroupParityColumns(payload) {
    return [
      {label: '#', numeric: true, width: '70px', render: function (row) { return n('span', 'ops-mono', integer(row.id)); }},
      {label: 'Upstream logical test group', sticky: true, width: '330px', render: function (row) { return linkButton(row.title, function () { openTestGroupParityDetail(row, payload); }); }},
      {label: 'Area', width: '180px', render: function (row) { return value(row.area); }},
      {label: 'CUDA variants', width: '180px', render: function (row) { return value(row.cuda_variants); }},
      {label: 'Configured coverage', width: '225px', render: function (row) { const presentation = testGroupParityState(row.state); return linkedBadge(presentation.label, null, function () { openTestGroupParityDetail(row, payload); }, presentation.tone); }},
      {label: 'Current CI definition', width: '190px', render: function (row) { return value(testGroupDefinitionState(row)); }},
      {label: 'ROCm counterpart or assessment', width: '480px', render: function (row) { return linkButton(row.assessment, function () { openTestGroupParityDetail(row, payload); }); }},
    ];
  }

  function openParityRows(title, rows, payload, subtitle) {
    openTableBrowser({
      id: 'parity-' + String(title || 'groups').toLowerCase().replace(/[^a-z0-9]+/g, '-'),
      title: title,
      subtitle: subtitle || integer(rows.length) + ' current upstream logical test groups',
      rows: rows,
      columns: testGroupParityColumns(payload),
      searchPlaceholder: 'Filter test group, area, CUDA variant, status, or assessment',
      searchText: function (row) { return [row.id, row.title, row.reviewed_title, row.area, row.cuda_variants, row.state, row.assessment, testGroupDefinitionState(row)].join(' '); },
      geometry: {name: 'upstream-test-group-parity', minWidth: '1655px'},
    });
  }

  function healthRingCard(options) {
    const root = n(options.onOpen ? 'button' : 'section', 'ops-health-ring-card ' + (options.tone || ''));
    if (options.onOpen) {
      root.type = 'button';
      root.addEventListener('click', options.onOpen);
    }
    const denominator = Math.max(0, Number(options.total || 0));
    const numerator = Math.max(0, Number(options.current || 0));
    const available = options.available !== false && denominator > 0;
    const rate = available ? Math.min(100, numerator / denominator * 100) : 0;
    const ring = n('span', 'ops-health-ring');
    ring.style.setProperty('--ops-ring-progress', rate.toFixed(1));
    add(ring, [
      n('strong', '', available ? rate.toFixed(1) + '%' : '—'),
      n('span', '', available ? integer(numerator) + ' / ' + integer(denominator) : 'Unavailable'),
    ]);
    const copy = n('span', 'ops-health-ring-copy');
    add(copy, [
      n('span', 'ops-eyebrow', options.eyebrow || ''),
      n('span', 'ops-health-ring-title', options.title || ''),
      n('span', 'ops-health-ring-meta', options.meta || ''),
      options.onOpen ? n('span', 'ops-stat-action', options.actionLabel || 'Inspect groups →') : null,
    ]);
    add(root, [ring, copy]);
    return root;
  }

  function healthDistributionCard(title, subtitle, segments) {
    const total = (segments || []).reduce(function (sum, segment) { return sum + Number(segment.count || 0); }, 0);
    const root = n('section', 'ops-health-distribution');
    add(root, [n('div', 'ops-eyebrow', title), n('p', 'ops-health-distribution-copy', subtitle || '')]);
    const track = n('div', 'ops-health-stack');
    (segments || []).forEach(function (segment) {
      if (!Number(segment.count || 0)) return;
      const slice = n('span', 'ops-health-stack-segment ' + (segment.tone || ''));
      slice.style.width = Number(segment.count || 0) / Math.max(1, total) * 100 + '%';
      slice.title = segment.label + ': ' + integer(segment.count);
      track.append(slice);
    });
    root.append(track);
    const legend = n('div', 'ops-health-legend');
    (segments || []).forEach(function (segment) {
      const item = n(segment.onOpen ? 'button' : 'div', 'ops-health-legend-item ' + (segment.tone || ''));
      if (segment.onOpen) {
        item.type = 'button';
        item.addEventListener('click', segment.onOpen);
      }
      add(item, [n('span', 'ops-health-legend-dot'), n('strong', '', integer(segment.count)), n('span', '', segment.label)]);
      legend.append(item);
    });
    root.append(legend);
    return root;
  }

  function healthAreaBoard(title, subtitle, areas, onOpen) {
    const grid = n('div', 'ops-health-area-grid');
    (areas || []).forEach(function (area) {
      const control = n('button', 'ops-health-area-card');
      control.type = 'button';
      control.addEventListener('click', function () { onOpen(area); });
      control.setAttribute('aria-label', area.label + ': ' + (area.segments || []).map(function (segment) {
        return integer(segment.count) + ' ' + value(segment.label, 'groups');
      }).join(', ') + '. Open group table.');
      const header = n('div', 'ops-health-area-heading');
      add(header, [
        n('strong', '', area.label),
        n('span', '', area.countLabel || integer(area.attention) + ' open items'),
      ]);
      const stack = n('div', 'ops-health-mini-stack');
      (area.segments || []).forEach(function (segment) {
        if (!segment.count) return;
        const slice = n('span', 'ops-health-stack-segment ' + (segment.tone || ''));
        slice.style.width = Number(segment.count) / Math.max(1, Number(area.total || 0)) * 100 + '%';
        slice.title = value(segment.label, 'Groups') + ': ' + integer(segment.count);
        slice.setAttribute('aria-hidden', 'true');
        stack.append(slice);
      });
      const preview = n('div', 'ops-health-area-preview');
      (area.preview || []).slice(0, 3).forEach(function (label) { preview.append(n('span', '', label)); });
      add(control, [header, stack, preview, n('span', 'ops-stat-action', 'Open group table →')]);
      grid.append(control);
    });
    return panel(title, subtitle, grid, 'ops-health-area-panel');
  }

  function openHealthDataFreshness(ops) {
    const retiredSources = new Set(['ready_tickets']);
    const cadenceHours = {amd_test_signal: 36, project_items: 36};
    const rows = Object.entries((ops || {}).sources || {}).filter(function (entry) {
      return !retiredSources.has(entry[0]);
    }).map(function (entry) {
      const record = entry[1] || {};
      const observed = new Date(record.timestamp || record.generated_at || 0).getTime();
      const expectedWithinHours = cadenceHours[entry[0]] || 6;
      return {
        name: entry[0].replaceAll('_', ' '),
        timestamp: record.timestamp || record.generated_at,
        path: record.path,
        timestampSource: record.timestamp_source,
        expectedWithinHours: expectedWithinHours,
        published: record.published !== false,
        stale: !Number.isFinite(observed) || Date.now() - observed > expectedWithinHours * 3600000,
      };
    }).sort(function (left, right) {
      return Number(right.stale) - Number(left.stale) || String(left.name).localeCompare(String(right.name));
    });
    openTableBrowser({
      id: 'ci-health-data-freshness',
      title: 'CI Health data freshness',
      subtitle: 'Collector inputs used by this snapshot; this view does not redirect to raw JSON',
      rows: rows,
      columns: [
        {label: 'Input', sticky: true, width: '240px', render: function (row) { return value(row.name); }},
        {label: 'Observed', width: '190px', render: function (row) { return shortDate(row.timestamp); }},
        {label: 'Freshness', width: '180px', render: function (row) { return badge(row.stale ? 'outside ' + integer(row.expectedWithinHours) + 'h cadence' : age(row.timestamp), row.stale ? 'is-warning' : 'is-success'); }},
        {label: 'Collector artifact', width: '300px', render: function (row) { return value(row.path); }},
        {label: 'Dashboard role', width: '150px', render: function (row) { return badge(row.published ? 'published input' : 'private input', row.published ? 'is-info' : 'is-neutral'); }},
        {label: 'Timestamp basis', width: '180px', render: function (row) { return value(row.timestampSource); }},
      ],
      searchPlaceholder: 'Filter collector input or artifact',
      searchText: function (row) { return [row.name, row.path, row.timestampSource].join(' '); },
      geometry: {name: 'ci-health-data-freshness', minWidth: '1220px'},
    });
  }

  function ownershipAreaState(row) {
    const counts = (row || {}).counts || {};
    const confirmedHard = counts.confirmed_hard === undefined ? counts.hard : counts.confirmed_hard;
    const confirmedSoft = counts.confirmed_soft === undefined ? counts.soft : counts.confirmed_soft;
    if (Number(confirmedHard || 0)) return 'hard';
    if (Number(confirmedSoft || 0)) return 'soft';
    if (Number(counts.pending_soft || 0)) return 'pending_soft';
    if (Number(counts.unobserved || 0)) return 'unknown';
    return 'passed';
  }

  function ownershipAreaStatusLabel(row) {
    const stateName = ownershipAreaState(row);
    if (stateName === 'hard') return 'confirmed hard';
    if (stateName === 'soft') return 'confirmed soft';
    if (stateName === 'pending_soft') return 'pending soft';
    if (stateName === 'unknown') return 'unresolved';
    return 'passing';
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

  function ownershipFailureEvidence(item, key) {
    const evidence = (item || {}).last_failure_evidence || {};
    const raw = String((item || {}).raw_result || (item || {}).result || '').toLowerCase();
    if ((item || {}).incident_observation_eligible === false || ['unobserved', 'unknown', 'indeterminate'].includes(raw)) {
      return evidence[key] || item[key];
    }
    return item[key] || evidence[key];
  }

  function ownershipObservationLabel(item) {
    const raw = value((item || {}).raw_result || (item || {}).result);
    return (item || {}).incident_observation_eligible === false ? raw + ' (ignored older build)' : raw;
  }

  function boundedRowsNote(host, retention, label) {
    if ((retention || {}).complete_relative_to_source === false) {
      host.append(n('div', 'ops-evidence-note is-warning', label + ' is bounded; totals exact, rows partial.'));
    }
  }

  function openOwnershipAreaDetail(row) {
    const counts = row.counts || {};
    const confirmedHard = counts.confirmed_hard === undefined ? counts.hard : counts.confirmed_hard;
    const confirmedSoft = counts.confirmed_soft === undefined ? counts.soft : counts.confirmed_soft;
    const content = n('div', 'ops-detail-stack');
    const publicationDetailIncomplete = row.publication_detail_complete === false;
    boundedRowsNote(content, {complete_relative_to_source: !publicationDetailIncomplete}, 'Ownership detail');
    content.append(statusStrip([
      {id: 'owner-area-incidents', label: 'CONFIRMED INCIDENTS', value: integer(counts.incidents), meta: integer(confirmedHard) + ' hard - ' + integer(confirmedSoft) + ' soft', tone: Number(counts.incidents) ? 'is-warning' : 'is-success'},
      {id: 'owner-area-pending-soft', label: 'PENDING SOFT OBSERVATIONS', value: integer(counts.pending_soft), meta: 'requires 2 distinct completed builds', tone: Number(counts.pending_soft) ? 'is-warning' : 'is-success'},
      {id: 'owner-area-targets', label: 'RUNTIME TARGETS', value: integer(counts.targets), meta: 'latest raw: ' + integer(counts.passed) + ' passed - ' + integer(counts.unobserved) + ' unresolved'},
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
    const incidents = row.regressions || [];
    const incidentColumns = [
      {label: 'Target group', sticky: true, width: '400px', render: function (item) { const url = ownershipFailureEvidence(item, 'url'); return url ? externalLink(item.label, url) : value(item.label); }},
      {label: 'Confirmed severity', width: '150px', render: function (item) { return badge(value(item.incident_severity), toneForState(item.incident_severity)); }},
      {label: 'Latest observation', width: '190px', render: function (item) { return badge(ownershipObservationLabel(item), toneForState(item.raw_result || item.result)); }},
      {label: 'Failure build', width: '120px', render: function (item) { const build = ownershipFailureEvidence(item, 'build_number'); return build ? n('span', 'ops-mono', '#' + integer(build)) : n('span', 'ops-cell-muted', '-'); }},
      {label: 'Failure observed', width: '180px', render: function (item) { return shortDate(ownershipFailureEvidence(item, 'observed_at')); }},
    ];
    content.append(panel(
      'Confirmed AMD incidents',
      incidents.length ? 'Confirmed incident state with exact latest AMD observation evidence' : 'No confirmed incidents',
      dataTable(incidentColumns, incidents, (publicationDetailIncomplete ? '≥' : '') + integer(incidents.length) + ' published confirmed incidents', {name: 'ownership-incidents', minWidth: '970px'})
    ));
    const pendingSoft = row.pending_soft_observations || [];
    const pendingColumns = [
      {label: 'Target group', sticky: true, width: '440px', render: function (item) { const url = ownershipFailureEvidence(item, 'url'); return url ? externalLink(item.label, url) : value(item.label); }},
      {label: 'Soft streak', width: '140px', render: function (item) { return integer(item.soft_streak) + ' / ' + integer(item.soft_threshold || 2); }},
      {label: 'Latest observation', width: '190px', render: function (item) { return badge(ownershipObservationLabel(item), toneForState(item.raw_result || item.result)); }},
      {label: 'Failure build', width: '120px', render: function (item) { const build = ownershipFailureEvidence(item, 'build_number'); return build ? n('span', 'ops-mono', '#' + integer(build)) : n('span', 'ops-cell-muted', '-'); }},
      {label: 'Failure observed', width: '180px', render: function (item) { return shortDate(ownershipFailureEvidence(item, 'observed_at')); }},
    ];
    content.append(panel(
      'Pending soft observations',
      pendingSoft.length ? 'First soft signals awaiting another distinct soft build; absent observations hold rather than advance or clear them' : 'No pending soft observations',
      dataTable(pendingColumns, pendingSoft, (publicationDetailIncomplete ? '≥' : '') + integer(pendingSoft.length) + ' published pending soft observations', {name: 'ownership-pending-soft', minWidth: '1010px'})
    ));
    const gaps = row.upstream_parity_gaps || [];
    const gapColumns = [
      {label: 'Upstream-only definition', sticky: true, width: '500px', render: function (item) { return item.url ? externalLink(item.label, item.url) : value(item.label); }},
    ];
    content.append(panel(
      'Upstream parity work',
      'Definitions present upstream without a one-to-one AMD definition',
      dataTable(gapColumns, gaps, (publicationDetailIncomplete ? '≥' : '') + integer(gaps.length) + ' published parity gaps', {name: 'ownership-parity', minWidth: '520px'})
    ));
    openOverlay(
      row.source_file || row.area || 'CI ownership',
      'Incident response, escalation, runtime signal, and parity obligations',
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
    const ownershipRetention = ownership.operations_publication_retention || ownership.publication_retention || {};
    boundedRowsNote(host, ownershipRetention, 'Ownership');
    host.append(statusStrip([
      {id: 'ownership-areas', label: 'OWNED TEST AREAS', value: integer(summary.areas), meta: 'active ranked routing chains'},
      {id: 'ownership-regressing', label: 'AREAS WITH CONFIRMED INCIDENTS', value: integer(summary.areas_with_incidents), meta: integer(summary.incidents) + ' confirmed target incidents', tone: Number(summary.areas_with_incidents) ? 'is-warning' : 'is-success'},
      {id: 'ownership-pending-soft', label: 'PENDING SOFT OBSERVATIONS', value: integer(summary.pending_soft), meta: integer(summary.areas_with_pending_soft) + ' areas awaiting a second distinct build', tone: Number(summary.pending_soft) ? 'is-warning' : 'is-success'},
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
      n('span', '', ' Hard observations confirm immediately; soft observations stay visible here and confirm only after two distinct completed builds. GitHub assignability is checked before mutation. Confirmed-incident issues tag the selected owner and verified assignee, then CC each remaining ranked area owner once.'),
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
      {label: 'Incident status', width: '170px', render: function (row) { const result = ownershipAreaState(row); return linkedBadge(ownershipAreaStatusLabel(row), (row.issue || {}).url, function () { openOwnershipAreaDetail(row); }, toneForState(result === 'pending_soft' ? 'soft' : result)); }},
      {label: 'Selected engineer', width: '240px', render: function (row) { return linkButton(ownershipSelectedName(row), function () { openOwnershipAreaDetail(row); }); }},
      {label: 'Runtime targets', width: '250px', render: function (row) { const counts = row.counts || {}; return integer(counts.incidents) + ' confirmed - ' + integer(counts.pending_soft) + ' pending soft; latest raw ' + integer(counts.passed) + ' pass - ' + integer(counts.unobserved) + ' unresolved'; }},
      {label: 'Parity gaps', width: '120px', render: function (row) { return integer((row.counts || {}).upstream_parity_gaps); }},
      {label: 'Managed issue', width: '130px', render: function (row) { const issue = row.issue || {}; if (issue.url) return externalLink('#' + integer(issue.number), issue.url, 'ops-mono'); if (issue.suppressed) return badge('suppressed', 'is-neutral'); return n('span', 'ops-cell-muted', '-'); }},
    ];
    host.append(compactTablePanel(
      'CI test-area ownership',
      'Confirmed incident areas first, then pending observations and source filename; owner chains are rank ordered',
      areaColumns,
      areas,
      {
        id: 'ci-ownership-browser',
        limit: 18,
        alwaysBrowse: areas.length > 0,
        browserTitle: 'CI test-area ownership and escalation',
        browserSubtitle: 'Confirmed runtime incidents, pending soft observations, parity, working-hours routing, and managed issue status',
        searchPlaceholder: 'Filter area, engineer, status, or assignment reason',
        searchText: function (row) { return [row.source_file, ownershipAreaState(row), ownershipAreaStatusLabel(row), ownershipSelectedName(row), ownershipChainText(row), row.assignment_reason, row.selection_reason, (row.regressions || []).map(function (item) { return item.label; }).join(' '), (row.pending_soft_observations || []).map(function (item) { return item.label; }).join(' ')].join(' '); },
        geometry: {name: 'ci-ownership', minWidth: '1150px'},
      }
    ));
  }

  async function renderHealth(host, ops) {
    const amd = latestAmd(ops);
    const build = (amd.builds || [])[0] || {};
    const amdRuntimeHealth = currentAmdHealth(ops.amd_test_health);
    const matrix = amdRuntimeHealth.runtime_matrix_source && amdRuntimeHealth.runtime_matrix_source.pipeline === 'ci'
      ? amdRuntimeHealth.runtime_matrix_summary || {} : {};
    const uniqueHealth = matrixHealthPolicy(matrix);
    const amdHealthSummary = currentAmdHealth(ops.amd_test_health).summary || {};
    const latestLogicalGroups = amdHealthSummary.latest_test_group_counts || {};
    const nightlyState = amdNightlyPresentation(build, amdHealthSummary, ops.generated_at);
    const viewDescriptions = {
      overview: 'Latest main CI AMD gating-job outcomes and failure movement. Logical test groups are separate from exact Buildkite job variants.',
      parity: 'Current main CI AMD route coverage against upstream source definitions. Configured route modes and runtime pass/fail are separate.',
      coverage: 'Configured AMD test groups by architecture and the fixed best-hardware health policy.',
      mirrors: 'Current AMD mirror declarations parsed from every .buildkite/test_areas/*.yaml file on vLLM main.',
    };
    let headerAction = null;
    let observedAt = ops.generated_at;
    if (state.healthView === 'overview' && exactPipelineBuildUrl(build, 'ci')) {
      headerAction = externalLink('Open main CI AMD nightly #' + value(build.number) + ' ↗', exactPipelineBuildUrl(build, 'ci'), 'ops-button');
      observedAt = build.created_at || observedAt;
    }
    if (state.healthView === 'parity') {
      const source = (ops.test_group_parity || {}).source || {};
      const mainCommit = source.main_commit || source.commit_sha || '';
      const mainUrl = source.main_commit_url || (mainCommit ? 'https://github.com/vllm-project/vllm/commit/' + mainCommit : '');
      if (mainUrl) headerAction = externalLink('Open current main CI source ↗', mainUrl, 'ops-button');
      observedAt = (ops.test_group_parity || {}).generated_at || observedAt;
    }
    if (state.healthView === 'mirrors') {
      const mirrorInventory = (ops.test_group_parity || {}).mirror_inventory || {};
      const source = mirrorInventory.source || {};
      const commit = source.current_definition_commit_sha || source.main_commit || '';
      if (/^[0-9a-f]{40}$/.test(commit)) headerAction = externalLink('Open current main CI source ↗', 'https://github.com/vllm-project/vllm/commit/' + commit, 'ops-button');
      observedAt = (ops.test_group_parity || {}).generated_at || observedAt;
    }
    const headerActions = n('div', 'ops-inline-actions');
    if (headerAction) headerActions.append(headerAction);
    headerActions.append(button('Data freshness', function () { openHealthDataFreshness(ops); }));
    add(host, pageHeader('CI Health', viewDescriptions[state.healthView] || viewDescriptions.overview, observedAt, headerActions));
    healthTabs(host);
    const ciHealthRetentionMessage = ciHealthPublicationRetentionMessage(amd);
    if (ciHealthRetentionMessage) {
      host.append(n('div', 'ops-evidence-note is-warning', ciHealthRetentionMessage));
    }

    if (state.healthView === 'overview') {
      const logicalGroups = logicalTestGroupPresentation(latestLogicalGroups);
      const amdHealth = currentAmdHealth(ops.amd_test_health);
      const latestAmdBuild = ((amdHealth.summary || {}).latest_build_number);
      const allAmdGroups = amdHealthGroups(amdHealth).filter(function (row) {
        return Number(row.latest_build_number) === Number(latestAmdBuild);
      });
      const logicalInventory = amdLogicalInventory(amdHealth);
      const logicalRows = logicalInventory.rows;
      const logicalTotal = Number(latestLogicalGroups.total || 0);
      const logicalPassing = Number(latestLogicalGroups.passing || 0);
      const logicalPassingAll = Number(latestLogicalGroups.passing_all || 0);
      const logicalPartial = Number(latestLogicalGroups.partial || 0);
      const logicalNonPassing = Number(latestLogicalGroups.non_passing || 0);
      function openLogicalRows(title, rows) {
        openAmdLogicalCatalog(
          title,
          'Build-pinned logical test groups from latest observed AMD test signal #' + value(latestAmdBuild) + '; select a row for every hardware route and exact job',
          rows,
          logicalInventory,
          amdHealth
        );
      }
      const overviewHero = n('div', 'ops-health-hero-grid');
      overviewHero.append(healthRingCard({
        eyebrow: 'LATEST AMD TEST GROUPS',
        title: 'Logical runtime health',
        available: logicalGroups.available,
        current: logicalPassing,
        total: logicalTotal,
        meta: logicalGroups.meta,
        tone: !logicalGroups.available ? 'is-warning' : logicalPassing === logicalTotal ? 'is-success' : 'is-warning',
        actionLabel: 'Inspect all logical test groups →',
        onOpen: function () { openLogicalRows('Latest AMD logical test groups', logicalRows); },
      }));
      overviewHero.append(healthDistributionCard(
        'LOGICAL GROUP OUTCOMES · ' + (latestAmdBuild ? '#' + integer(latestAmdBuild) : 'UNAVAILABLE'),
        'Hardware-distinct jobs are combined only when they represent the same source-aligned test group.',
        [
          {label: 'pass every route', count: logicalPassingAll, tone: 'is-success', onOpen: function () { openLogicalRows('Logical groups passing every route', logicalRows.filter(function (row) { return row.state === 'passing_all'; })); }},
          {label: 'pass some routes', count: logicalPartial, tone: 'is-warning', onOpen: function () { openLogicalRows('Logical groups with mixed hardware outcomes', logicalRows.filter(function (row) { return row.state === 'partial'; })); }},
          {label: 'non-passing', count: logicalNonPassing, tone: 'is-danger', onOpen: function () { openLogicalRows('Logical groups without a passing route', logicalRows.filter(function (row) { return row.state === 'non_passing'; })); }},
        ]
      ));
      host.append(overviewHero);
      const mirrorRenderer = window.AmdMirrorInventory;
      if (mirrorRenderer && typeof mirrorRenderer.summaryCard === 'function') {
        host.append(mirrorRenderer.summaryCard(
        (ops.test_group_parity || {}).mirror_inventory || {},
        logicalGroups.available ? logicalTotal : null,
          amdMirrorUiHelpers(),
          function () { navigateTo('ci-health', {healthView: 'mirrors'}); }
        ));
      }
      const movementBuilds = (amd.builds || []).filter(function (row) {
        const movement = nightlyFailureMovement(row);
        return row.has_test_results !== false && Boolean(movement) && movement.available !== false;
      }).slice(0, 14).reverse();
      if (!nightlyState.hasSignal) {
        const signalNote = n('div', 'ops-evidence-note is-warning');
        add(signalNote, [
          n('strong', '', 'Latest nightly has no test signal. '),
          n('span', '', 'The failure-observation list, matrix, and movement chart below use the latest observed main CI AMD test signal'),
          amdHealthSummary.latest_build_url ? externalLink(' #' + amdHealthSummary.latest_build_number, amdHealthSummary.latest_build_url) : n('span', '', ' #' + value(amdHealthSummary.latest_build_number)),
          n('span', '', '.'),
        ]);
        host.append(signalNote);
      }
      const grid = n('div', 'ops-grid ops-grid-main-aside ops-health-grid');
      const trend = chartPanel('Nightly failure movement', 'New and recurring failures are above zero; fixes are below. Missing or skipped jobs are omitted. Latest signal #' + value(amdHealthSummary.latest_build_number) + '.', 'health-nightly');
      trend.root.classList.add('ops-health-primary');
      grid.append(trend.root);
      const failures = allAmdGroups.filter(function (row) { return ['soft', 'hard'].includes(amdLatestState(row, latestAmdBuild)); }).sort(function (a, b) {
        return (amdLatestState(a, latestAmdBuild) === 'hard' ? 0 : 1) - (amdLatestState(b, latestAmdBuild) === 'hard' ? 0 : 1) || Number(amdGroupPassRate(a) || 0) - Number(amdGroupPassRate(b) || 0);
      });
      const overviewColumns = [
        {label: 'AMD job variant', sticky: true, width: '330px', render: function (row) { return amdGroupIdentity(row, function () { openAmdGroupDetail(row, amdHealth); }); }},
        {label: 'Latest', width: '120px', render: function (row) { const result = amdLatestState(row, latestAmdBuild); return linkedBadge(amdStateLabel(result), null, function () { openAmdGroupDetail(row, amdHealth); }, toneForState(result)); }},
        {label: 'Build', width: '110px', render: function (row) { return row.latest_url ? externalLink('#' + value(row.latest_build_number), row.latest_url, 'ops-mono') : n('span', 'ops-cell-muted', '-'); }},
      ];
      const failurePanel = compactTablePanel('Latest AMD failure observations', 'Non-passing exact job variants, hardest results first', overviewColumns, failures, {
        id: 'health-current-incidents',
        limit: 6,
        previewCaption: 'Latest non-passing AMD job variants',
        conciseCounts: true,
        buttonLabel: 'Browse all failure observations',
        browserSubtitle: 'Group and result open retained history; Build opens the exact Buildkite job',
        searchPlaceholder: 'Filter AMD job variant, hardware, or queue',
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
            {label: 'New failure', data: movementBuilds.map(function (b) { return nightlyFailureCount(b, 'new'); }), backgroundColor: '#e06464'},
            {label: 'Recurring failure', data: movementBuilds.map(function (b) { return nightlyFailureCount(b, 'recurring'); }), backgroundColor: '#c47732'},
            {label: 'Fixed', data: movementBuilds.map(function (b) { return -Number(nightlyFailureCount(b, 'fixed') || 0); }), backgroundColor: '#35bb78'},
          ],
        },
        options: {
          interaction: {mode: 'index', intersect: false},
          scales: {x: {stacked: true}, y: {stacked: true, beginAtZero: true}},
          plugins: {tooltip: {callbacks: {label: function (item) { return item.dataset.label + ': ' + integer(Math.abs(item.parsed.y)); }}}},
        },
        evidenceTitle: 'AMD nightly failure movement',
        evidence: movementBuilds.map(function (nightly) {
          const movement = nightlyFailureMovement(nightly);
          return {label: '#' + nightly.number, timestamp: nightly.created_at, url: exactPipelineBuildUrl(nightly, 'ci'), valueSummary: integer(movement.new.length) + ' new - ' + integer(movement.recurring.length) + ' recurring - ' + integer(movement.fixed.length) + ' fixed', details: {state: nightly.state, new_failure: movement.new.length, recurring_failure: movement.recurring.length, fixed: movement.fixed.length}};
        }),
      });
      return;
    }

    if (state.healthView === 'parity') {
      const parity = currentTestGroupParity(ops.test_group_parity);
      const parityRetention = parity.operations_publication_retention || {};
      const summary = parity.summary || {};
      const areas = Array.isArray(parity.areas) ? parity.areas : [];
      const source = parity.source || {};
      const rocmInventory = parity.rocm_inventory || summary.rocm_inventory || {};
      const upstreamTotal = Number(summary.upstream_logical_groups || 0);
      const applicableTotal = Number(summary.applicable_groups || 0);
      const mainTotal = Number(summary.main_complete_groups || 0);
      const unsupportedTotal = Number(summary.unsupported_groups || 0);
      const actionTotal = Number(summary.action_groups || 0);
      const mainMissingTotal = Number(summary.main_missing_groups || 0);
      const allRows = testGroupParityRows(parity, 'all', 'all');

      boundedRowsNote(host, parityRetention, 'Parity');

      if (!allRows.length || !upstreamTotal) {
        host.append(n('div', 'ops-evidence-note is-warning', 'The current main CI test-group parity inventory is unavailable in this snapshot.'));
        return;
      }

      const inventoryMain = rocmInventory.main || {};
      const mainRows = testGroupParityRows(parity, 'existing', 'all');
      const missingRows = testGroupParityRows(parity, 'action', 'all');
      const unsupportedRows = testGroupParityRows(parity, 'unsupported', 'all');
      const applicableRows = allRows.filter(function (row) { return row.state !== 'unsupported'; });
      const hero = n('div', 'ops-health-hero-grid');
      hero.append(healthRingCard({
        eyebrow: 'UPSTREAM PARITY ON MAIN',
        title: 'Applicable test groups covered',
        current: mainTotal,
        total: applicableTotal,
        meta: integer(mainMissingTotal) + ' potential open gaps remain',
        tone: mainMissingTotal ? 'is-warning' : 'is-success',
        onOpen: function () { openParityRows('Applicable upstream test groups', applicableRows, parity); },
      }));
      hero.append(healthDistributionCard(
        'CURRENT MAIN CI · ' + integer(upstreamTotal) + ' LOGICAL GROUPS',
        integer(unsupportedTotal) + ' hardware- or backend-specific groups are classified outside the parity denominator.',
        [
          {label: 'covered on main', count: mainTotal, tone: 'is-success', onOpen: function () { openParityRows('Covered on main', mainRows, parity); }},
          {label: 'potential open gaps', count: actionTotal, tone: 'is-danger', onOpen: function () { openParityRows('Potential open gaps', missingRows, parity); }},
          {label: 'not targeted', count: unsupportedTotal, tone: 'is-not-targeted', onOpen: function () { openParityRows('Not targeted / unsupported', unsupportedRows, parity); }},
        ]
      ));
      host.append(hero);
      const required = summary.main_required_groups;
      const requiredRate = summary.main_required_rate_pct;
      const sourceCommit = source.current_definition_commit_sha || source.main_commit || '';
      host.append(n('div', 'ops-evidence-note is-info',
        'Configured AMD routes cover ' + integer(mainTotal) + ' of ' + integer(applicableTotal) + ' applicable current CUDA logical groups. '
        + (required === undefined ? 'Required-route coverage is unavailable. ' : integer(required) + ' groups have a required source configuration'
          + (Number.isFinite(Number(requiredRate)) ? ' (' + Number(requiredRate).toFixed(1) + '%)' : '') + '; '
          + integer(summary.main_optional_only_groups) + ' are optional only and '
          + integer(summary.main_soft_fail_only_groups) + ' are soft fail only. ')
        + 'These are source configuration flags; individual nightly scheduling and blocking outcomes are separate.'
        + (/^[0-9a-f]{40}$/.test(sourceCommit) ? ' Source: main CI at ' + sourceCommit.slice(0, 12) + '.' : '')));

      const gapAreas = areas.filter(function (row) { return Number(row.action || 0) > 0; }).map(function (row) {
        const groupRows = missingRows.filter(function (group) { return group.area === row.area; });
        return {
          label: row.area,
          attention: Number(row.action || 0),
          countLabel: integer(row.action || 0) + ' potential open ' + (Number(row.action || 0) === 1 ? 'gap' : 'gaps'),
          total: Number(row.total || 0),
          rows: groupRows,
          preview: groupRows.map(function (group) { return '#' + integer(group.id) + ' ' + group.title; }),
          segments: [
            {label: 'covered on main', count: Number(row.existing || 0), tone: 'is-success'},
            {label: 'potential open gaps', count: Number(row.action || 0), tone: 'is-danger'},
            {label: 'not targeted', count: Number(row.unsupported || 0), tone: 'is-not-targeted'},
          ],
        };
      }).sort(function (left, right) { return right.attention - left.attention || left.label.localeCompare(right.label); });
      host.append(healthAreaBoard(
        'Potential open gaps by test area',
        'Potential gaps are shown first and grouped. Select an area to open its complete table.',
        gapAreas,
        function (area) { openParityRows(area.label + ' potential open gaps', area.rows, parity); }
      ));

      const actions = n('div', 'ops-related-actions');
      add(actions, [
        button('Browse all ' + integer(actionTotal) + ' potential open gaps', function () { openParityRows('Potential open gaps', missingRows, parity); }, true),
        button('Browse ' + integer(unsupportedTotal) + ' not-targeted groups', function () { openParityRows('Not targeted / unsupported', unsupportedRows, parity); }),
        button('Browse complete ' + integer(upstreamTotal) + '-group inventory', function () { openParityRows('Complete current upstream inventory', allRows, parity); }),
      ]);
      host.append(panel(
        'Inspect exact test groups',
        'Tables open in a searchable popup; selecting a test group opens its assessment.',
        actions
      ));
      if (inventoryMain.logical_groups || inventoryMain.physical_definitions) {
        host.append(n('div', 'ops-evidence-note is-info', 'Separate ROCm inventory on main: ' + integer(inventoryMain.logical_groups) + ' logical AMD test groups from ' + integer(inventoryMain.physical_definitions) + ' YAML definitions. These inventory counts are not the upstream-parity numerator.'));
      }
      return;
    }

    if (state.healthView === 'mirrors') {
      const renderer = window.AmdMirrorInventory;
      if (!renderer || typeof renderer.render !== 'function') {
        throw new Error('AMD mirror inventory render API is unavailable');
      }
      renderer.render(host, (ops.test_group_parity || {}).mirror_inventory || {}, amdMirrorUiHelpers());
      return;
    }

    if (state.healthView === 'coverage') {
      let matrixData = {};
      try { matrixData = await fetchJSON('data/vllm/ci/amd_test_matrix.json'); } catch (_) {}
      if (((matrixData || {}).source || {}).pipeline !== 'ci') {
        host.append(n('div', 'ops-evidence-note is-warning', 'Current main CI AMD hardware evidence is unavailable. No older AMD pipeline result is substituted.'));
        return;
      }
      const arch = matrixData.architectures || [];
      const coverageRows = sortAmdMatrixRows(matrixData.rows || [], state.healthCoverageSort);
      host.append(matrixHealthOverview(
        matrix,
        uniqueHealth,
        matrix.latest_build_number || amdHealthSummary.latest_build_number
      ));
      if ((matrixData.publication_retention || {}).complete_relative_to_source === false) {
        host.append(n('div', 'ops-evidence-note is-warning', 'AMD detail is storage-bounded; architecture rates and routes are hidden.'));
        return;
      }
      if (!uniqueHealth.best_hardware_unavailable) {
        const policyNote = n('div', 'ops-evidence-note is-info');
        add(policyNote, [
          n('strong', '', 'One fixed health policy. '),
          n('span', '', 'Generic test families pass when any configured AMD architecture passes. Explicit MI355-sensitive obligations use their exact MI355 route, so another architecture cannot hide a regression.'),
        ]);
        host.append(policyNote);
      }
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
          title: architecture.label + ' test-group routes',
          subtitle: integer(selectedRows.length) + ' configured hardware routes; non-passing latest results first, then test group A-Z; Build links open exact AMD jobs',
          rows: selectedRows,
          columns: [
            {label: 'Test group', sticky: true, width: '430px', render: function (row) { return linkButton(row.title, function () { openGroupDetailWithEvidence({name: row.title, area: row.area}, ops); }); }},
            {label: 'Area', width: '180px', render: function (row) { return value(row.area); }},
            {label: 'Latest result', width: '150px', render: function (row) { const cell = (row.cells || {})[architecture.id] || {}; return linkedBadge(cell.latest_state || 'unobserved', null, function () { openGroupDetailWithEvidence({name: row.title, area: row.area}, ops); }, toneForState(cell.latest_state)); }},
            {label: 'Build', width: '110px', render: function (row) { const cell = (row.cells || {})[architecture.id] || {}; const url = exactPipelineEvidenceUrl({latest_url: cell.latest_url, build_number: cell.latest_build_number}, 'ci'); return url ? externalLink('#' + value(cell.latest_build_number), url, 'ops-mono') : n('span', 'ops-cell-muted', '-'); }},
          ],
          searchText: function (row) { return [row.title, row.area, (((row.cells || {})[architecture.id] || {}).latest_state)].join(' '); },
          geometry: {name: 'amd-architecture', minWidth: '900px'},
        });
      }
      const scorecard = n('section', 'ops-architecture-scorecard');
      const scorecardHeader = n('header', 'ops-panel-header');
      add(scorecardHeader, [n('div', 'ops-panel-title', 'AMD architecture routes'), n('div', 'ops-panel-meta', 'Architecture counts are hardware routes; the best-hardware policy above deduplicates test groups')]);
      scorecard.append(scorecardHeader);
      const scorecardRows = n('div', 'ops-architecture-rows');
      architectureHealth.forEach(function (health) {
        const architecture = health.architecture;
        const configured = health.passed + health.incident + health.unknown;
        const passRate = configured ? health.passed / configured * 100 : null;
        const control = n('button', 'ops-architecture-row');
        control.type = 'button';
        control.setAttribute('aria-label', 'Inspect ' + architecture.label + ': ' + integer(health.passed) + ' passing routes, ' + integer(health.incident) + ' non-passing routes, ' + integer(health.unknown) + ' unobserved routes');
        control.addEventListener('click', function () { openArchitectureHealth(health); });
        const identity = n('div', 'ops-architecture-identity');
        add(identity, [n('strong', '', architecture.label), n('span', '', integer(configured) + ' configured routes')]);
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
          n('span', 'is-incident', integer(health.incident) + ' non-passing'),
          n('span', 'is-unknown', integer(health.unknown) + ' unobserved'),
        ]);
        const rate = n('div', 'ops-architecture-rate ' + (Number(passRate) >= 90 ? 'is-success' : Number(passRate) >= 50 ? 'is-warning' : 'is-danger'));
        add(rate, [n('strong', '', passRate === null ? '-' : passRate.toFixed(1) + '%'), n('span', '', 'passing')]);
        add(control, [identity, bar, metrics, rate]);
        scorecardRows.append(control);
      });
      scorecard.append(scorecardRows);
      host.append(scorecard);
      const cols = [{label: 'Group', sticky: true, render: function (r) { return linkButton(r.title, function () { openGroupDetailWithEvidence({name: r.title, area: r.area}, ops); }); }}, {label: 'Area', render: function (r) { return value(r.area); }}];
      for (const a of arch) {
        cols.push({label: a.label, render: function (r) {
          const c = (r.cells || {})[a.id] || {};
          if (!c.exists) return n('span', 'ops-cell-muted', '-');
          return linkedBadge(c.latest_state || 'unknown', null, function () { openGroupDetailWithEvidence({name: r.title, area: r.area}, ops); }, toneForState(c.latest_state));
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
        'Test-group routes by architecture',
        amdMatrixSortDescription(state.healthCoverageSort),
        cols,
        coverageRows,
        {
          id: 'coverage-browser',
          limit: 8,
          previewCaption: 'Preview of sorted test-group routes',
          conciseCounts: true,
          buttonLabel: 'Browse complete route matrix',
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
    if (['soft', 'soft_fail', 'soft_failed'].includes(result)) return 'Soft failure';
    if (['incident', 'error'].includes(result)) return 'Incident';
    if (isIncidentObservation(observation)) return 'Hard failure';
    return value(result, 'Unknown');
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

  function currentAmdHealth(payload) {
    return payload && payload.source_pipeline === 'ci' && payload.job_scope === 'amd_gpu' && payload.hardware_scope === 'amd_mi_gpu'
      && (payload.group_catalog || []).every(function (row) { return isAmdMiHardware(row.hardware) && isAmdRuntimeJob(row); })
      ? payload : {available: false, source_pipeline: 'ci', job_scope: 'amd_gpu', hardware_scope: 'amd_mi_gpu', summary: {}, group_catalog: []};
  }

  function amdHealthGroups(amdHealth) {
    return amdHealth && amdHealth.source_pipeline === 'ci' && amdHealth.job_scope === 'amd_gpu' && amdHealth.hardware_scope === 'amd_mi_gpu'
      && Array.isArray(amdHealth.group_catalog) ? amdHealth.group_catalog : [];
  }

  function amdHealthPublicationState(amdHealth) {
    const retention = ((amdHealth || {}).operations_publication_retention) || {};
    const complete = retention.complete_relative_to_amd_test_health !== false;
    return {
      complete: complete,
      retention: retention,
      prefix: complete ? '' : '≥',
    };
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
      {label: 'RETAINED-RUN PASS RATE', value: amdGroupPassRate(row) === null ? '-' : amdGroupPassRate(row).toFixed(1) + '%', meta: integer(row.passed) + ' passed - ' + integer(row.soft_failed) + ' soft - ' + integer(row.hard_failed) + ' hard'},
      {label: 'CURRENT PASS STREAK', value: integer(row.current_pass_streak), meta: integer(row.runs) + ' retained AMD nightlies'},
      {label: 'NON-PASSING RUNS', value: integer(incidents.length), meta: incidents.length ? 'Select any amber or red outcome for its Buildkite job' : 'None in retained history', tone: incidents.length ? 'is-warning' : 'is-success'},
    ]));

    const timeline = n('section', 'ops-history-panel ops-amd-history-timeline');
    const timelineHeader = n('header', 'ops-history-panel-header');
    const timelineHeading = n('div');
    add(timelineHeading, [n('h3', '', 'Main CI AMD gating-job outcomes'), n('p', '', integer(observations.length) + ' exact job runs - oldest to newest')]);
    const legend = n('div', 'ops-history-legend');
    [['is-passed', 'Passed'], ['is-soft', 'Soft fail'], ['is-hard', 'Hard fail'], ['is-unknown', 'Unknown']].forEach(function (entry) {
      const item = n('span', 'ops-history-legend-item');
      add(item, [n('i', 'ops-run-cell ' + entry[0]), entry[1]]);
      legend.append(item);
    });
    add(timelineHeader, [timelineHeading, legend]);
    const batches = n('div', 'ops-history-batches');
    for (let index = 0; index < observations.length; index += 10) batches.append(historyBatch(observations.slice(index, index + 10), index, 'ci'));
    add(timeline, [timelineHeader, batches]);
    content.append(timeline);

    if (incidents.length) {
      const incidentList = n('div', 'ops-incident-list ops-amd-incident-list');
      incidents.slice().reverse().forEach(function (observation) { incidentList.append(historyIncidentRow(observation, 'ci')); });
      content.append(panel('Failure evidence', integer(incidents.length) + ' exact AMD jobs', incidentList));
    }
    content.append(sourceActions([
      {label: 'Open latest AMD job', url: exactPipelineEvidenceUrl(observations[observations.length - 1], 'ci')},
      {label: 'Open main CI pipeline', url: 'https://buildkite.com/vllm/ci'},
      {label: 'Open published AMD health data', url: SOURCE_ASSETS.amdTestHealth},
    ]));
    openOverlay(row.display_name || row.name || row.job_name, value(row.hardware_variant || row.hardware) + ' - ' + value(row.queue) + ' - exact main CI AMD gating-job evidence', content, true, 'amd-group-' + row.id);
  }

  function amdLogicalInventory(amdHealth) {
    const current = currentAmdHealth(amdHealth);
    const inventory = current.latest_logical_test_groups || {};
    const counts = (current.summary || {}).latest_test_group_counts || {};
    const reconciliation = inventory.reconciliation || {};
    const inventoryBuild = Number(inventory.build_number);
    const countsBuild = Number(counts.build_number);
    const buildAligned = Number.isInteger(inventoryBuild)
      && inventoryBuild > 0
      && Number.isInteger(countsBuild)
      && countsBuild > 0
      && inventoryBuild === countsBuild;
    if (inventory.available !== true
      || reconciliation.matches_latest_test_group_counts !== true
      || counts.available !== true
      || !buildAligned
      || !Array.isArray(inventory.rows)
      || Number(counts.total) !== inventory.rows.length) {
      return Object.assign({}, inventory, {available: false, rows: []});
    }
    return inventory;
  }

  function amdLogicalStateLabel(stateName) {
    if (stateName === 'passing_all') return 'Passes every route';
    if (stateName === 'partial') return 'Passes some routes';
    if (stateName === 'non_passing') return 'Non-passing';
    return 'Unresolved';
  }

  function amdLogicalStateTone(stateName) {
    if (stateName === 'passing_all') return 'is-success';
    if (stateName === 'partial') return 'is-warning';
    if (stateName === 'non_passing') return 'is-danger';
    return 'is-neutral';
  }

  function amdLogicalSignalLabel(stateName) {
    if (stateName === 'passing') return 'Passing';
    if (stateName === 'failing') return 'Failing';
    return 'No pass signal';
  }

  function amdLogicalSignalTone(stateName) {
    if (stateName === 'passing') return 'is-success';
    if (stateName === 'failing') return 'is-danger';
    return 'is-neutral';
  }

  function amdLogicalCatalogGroup(variant, amdHealth) {
    const variantId = String((variant || {}).id || '');
    const exactName = String((variant || {}).exact_job_name || '');
    return amdHealthGroups(amdHealth).find(function (row) {
      return (variantId && String(row.id || '') === variantId)
        || (exactName && String(row.name || row.job_name || '') === exactName);
    }) || null;
  }

  function openAmdLogicalGroupDetail(row, inventory, amdHealth) {
    const variants = Array.isArray(row.job_variants) ? row.job_variants : [];
    const hardwareStates = Array.isArray(row.hardware_states) ? row.hardware_states : [];
    const content = n('div', 'ops-amd-logical-detail');
    content.append(statusStrip([
      {label: 'LOGICAL GROUP RESULT', value: amdLogicalStateLabel(row.state), meta: row.state === 'partial' ? 'At least one hardware route passes and at least one does not' : 'Build-pinned route aggregation', tone: amdLogicalStateTone(row.state), static: true},
      {label: 'HARDWARE ROUTES', value: integer(row.hardware_count), meta: hardwareStates.map(function (item) { return hardwareDisplayLabel(item.hardware) + ': ' + amdLogicalSignalLabel(item.state).toLowerCase(); }).join(' · '), static: true},
      {label: 'EXACT JOB VARIANTS', value: integer(row.job_variant_count), meta: 'Every variant is listed below', static: true},
      {label: 'OBSERVED BUILD', value: inventory.build_number ? '#' + integer(inventory.build_number) : '-', meta: inventory.route_map_aligned ? 'Definitions aligned to the observed commit' : 'Definition alignment unavailable', tone: inventory.route_map_aligned ? 'is-success' : 'is-warning', static: true},
    ], 'Logical AMD test-group summary'));

    const columns = [
      {label: 'Exact AMD job variant', sticky: true, width: '410px', render: function (variant) {
        const catalogGroup = amdLogicalCatalogGroup(variant, amdHealth);
        const label = variant.display_name || variant.exact_job_name || 'Unnamed variant';
        if (catalogGroup) return linkButton(label, function () { openAmdGroupDetail(catalogGroup, amdHealth); });
        if (variant.job_url) return externalLink(label, variant.job_url);
        return n('span', 'ops-cell-primary', label);
      }},
      {label: 'Hardware route', width: '170px', render: function (variant) { return badge(hardwareDisplayLabel(variant.hardware_variant || variant.hardware), 'is-neutral'); }},
      {label: 'Test signal', width: '150px', render: function (variant) { return badge(amdLogicalSignalLabel(variant.test_signal_state), amdLogicalSignalTone(variant.test_signal_state)); }},
      {label: 'Terminal job', width: '140px', render: function (variant) { return badge(amdStateLabel(amdLatestState({latest_state: variant.terminal_state}, null)), toneForState(variant.terminal_state)); }},
      {label: 'Tests passed', numeric: true, width: '130px', render: function (variant) { return integer(variant.passed_tests) + ' / ' + integer(variant.tests); }},
      {label: 'Evidence', width: '150px', render: function (variant) { const url = variant.job_url || variant.build_url; return url ? externalLink('Open exact job', url) : n('span', 'ops-cell-muted', 'Unavailable'); }},
    ];
    content.append(panel(
      'Hardware routes and exact jobs',
      'The logical result follows the published any-route passing policy; exact jobs remain separate evidence.',
      dataTable(columns, variants, integer(variants.length) + ' exact variants for this logical test group', {name: 'amd-logical-variants', minWidth: '1150px'})
    ));
    content.append(sourceActions([
      {label: 'Open observed AMD build', url: inventory.build_url},
      {label: 'Open published AMD health data', url: SOURCE_ASSETS.amdTestHealth},
    ]));
    openOverlay(
      row.label || row.logical_key || 'AMD logical test group',
      amdLogicalStateLabel(row.state) + ' · ' + integer(row.hardware_count) + ' hardware routes · ' + integer(row.job_variant_count) + ' exact job variants',
      content,
      true,
      'amd-logical-' + row.id
    );
  }

  function openAmdLogicalCatalog(title, subtitle, rows, inventory, amdHealth) {
    const stateRank = {non_passing: 0, partial: 1, passing_all: 2};
    const sorted = Array.from(rows || []).sort(function (left, right) {
      return Number(stateRank[left.state] === undefined ? 3 : stateRank[left.state])
        - Number(stateRank[right.state] === undefined ? 3 : stateRank[right.state])
        || String(left.label || left.logical_key).localeCompare(String(right.label || right.logical_key));
    });
    openTableBrowser({
      id: 'amd-logical-test-groups',
      title: title,
      subtitle: subtitle,
      rows: sorted,
      columns: [
        {label: 'Logical AMD test group', sticky: true, width: '410px', render: function (row) { return linkButton(row.label || row.logical_key, function () { openAmdLogicalGroupDetail(row, inventory, amdHealth); }); }},
        {label: 'Latest result', width: '180px', render: function (row) { return linkedBadge(amdLogicalStateLabel(row.state), null, function () { openAmdLogicalGroupDetail(row, inventory, amdHealth); }, amdLogicalStateTone(row.state)); }},
        {label: 'Hardware routes', numeric: true, width: '140px', render: function (row) { return linkButton(integer(row.hardware_count), function () { openAmdLogicalGroupDetail(row, inventory, amdHealth); }); }},
        {label: 'Exact job variants', numeric: true, width: '150px', render: function (row) { return linkButton(integer(row.job_variant_count), function () { openAmdLogicalGroupDetail(row, inventory, amdHealth); }); }},
        {label: 'Route outcomes', width: '360px', render: function (row) { const wrap = n('div', 'ops-inline-actions'); (row.hardware_states || []).forEach(function (item) { wrap.append(badge(hardwareDisplayLabel(item.hardware) + ' · ' + amdLogicalSignalLabel(item.state), amdLogicalSignalTone(item.state))); }); return wrap; }},
      ],
      searchPlaceholder: 'Filter logical test group, result, hardware, or exact job variant',
      searchText: function (row) { return [row.label, row.logical_key, row.state, (row.hardware_states || []).map(function (item) { return item.hardware + ' ' + item.state; }).join(' '), (row.job_variants || []).map(function (item) { return item.exact_job_name; }).join(' ')].join(' '); },
      geometry: {name: 'amd-logical-test-groups', minWidth: '1240px'},
    });
  }

  function openAmdCatalog(title, subtitle, rows, amdHealth, initialFilter) {
    const latestBuild = ((amdHealth || {}).summary || {}).latest_build_number;
    const content = n('div', 'ops-reliability-browser ops-amd-browser');
    const toolbar = n('div', 'ops-toolbar');
    const search = n('input', 'ops-input');
    search.type = 'search';
    search.placeholder = 'Search AMD job variant, hardware, or queue';
    search.setAttribute('aria-label', 'Search AMD job variants');
    const resultFilter = n('select', 'ops-select');
    resultFilter.setAttribute('aria-label', 'Filter AMD job variants by health');
    [['all', 'All retained variants'], ['attention', 'Needs attention'], ['passing', 'Passing now'], ['incident', 'Failures now'], ['missing', 'Historical only'], ['mixed', 'Mixed history']].forEach(function (pair) {
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
        {label: 'Retained-run pass rate', numeric: true, width: '160px', render: function (row) { const rate = amdGroupPassRate(row); return linkButton(rate === null ? '-' : rate.toFixed(1) + '%', function () { openAmdGroupDetail(row, amdHealth); }); }},
        {label: 'Runs', numeric: true, width: '90px', render: function (row) { return linkButton(integer(row.runs), function () { openAmdGroupDetail(row, amdHealth); }); }},
        {label: 'Pass streak', numeric: true, width: '120px', render: function (row) { return linkButton(integer(row.current_pass_streak), function () { openAmdGroupDetail(row, amdHealth); }); }},
        {label: 'Hardware', width: '120px', render: function (row) { return badge(value(row.hardware_variant || row.hardware), 'is-neutral'); }},
        {label: 'Queue', width: '170px', render: function (row) { return n('span', 'ops-mono', value(row.queue)); }},
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

  function amdHealthCluster(label, count, description, meta, tone, onOpen, incomplete) {
    const tile = n('button', 'ops-cluster-tile ' + (tone || ''));
    tile.type = 'button';
    const renderedCount = (incomplete ? '≥' : '') + integer(count);
    tile.setAttribute('aria-label', 'Open ' + label + ': ' + renderedCount + ' AMD job variants');
    const head = n('div', 'ops-cluster-tile-head');
    add(head, [n('span', 'ops-cluster-label', label), n('span', 'ops-cluster-count', renderedCount)]);
    add(tile, [head, n('div', 'ops-cluster-description', description), n('div', 'ops-cluster-meta', meta)]);
    tile.addEventListener('click', onOpen);
    return tile;
  }

  function renderAmdHealth(host, amdHealth) {
    const summary = amdHealth.summary || {};
    const groups = amdHealthGroups(amdHealth);
    const builds = Array.isArray(amdHealth.builds) ? amdHealth.builds : [];
    const publication = amdHealthPublicationState(amdHealth);
    if (amdHealth.available !== true || !groups.length) {
      host.append(n('div', 'ops-evidence-note is-warning', 'AMD job-variant history is unavailable. No upstream result has been substituted for AMD health.'));
      return;
    }
    const latestBuild = summary.latest_build_number;
    const latestCounts = summary.latest_job_variant_state_counts || summary.latest_state_counts || {};
    const latestVariantCount = Number(summary.latest_job_variant_count !== undefined ? summary.latest_job_variant_count : summary.latest_group_count || 0);
    const latestTestGroups = summary.latest_test_group_counts || {};
    const logicalInventory = amdLogicalInventory(amdHealth);
    const logicalRows = logicalInventory.rows;
    const logicalTotal = Number(latestTestGroups.total);
    const logicalPassing = Number(latestTestGroups.passing);
    const logicalBuild = Number(latestTestGroups.build_number);
    const logicalAvailable = latestTestGroups.available === true
      && Number.isFinite(logicalBuild)
      && logicalBuild === Number(latestBuild)
      && Number.isFinite(logicalTotal)
      && logicalTotal >= 0
      && Number.isFinite(logicalPassing)
      && logicalPassing >= 0
      && logicalPassing <= logicalTotal;
    const logicalPresentation = logicalAvailable
      ? logicalTestGroupPresentation(latestTestGroups)
      : {value: '-', meta: 'same-build logical test-group counts unavailable', tone: 'is-warning'};
    const passing = Number(latestCounts.passed || 0);
    const soft = Number(latestCounts.soft || latestCounts.soft_failed || 0);
    const hard = Number(latestCounts.hard || latestCounts.hard_failed || 0);
    const incidents = soft + hard;
    const unknown = Number(latestCounts.unknown || 0);
    const nonPassing = incidents + unknown;
    const retainedCount = Number(summary.retained_job_variant_count || summary.retained_group_count || summary.union_group_count || summary.group_count || groups.length);
    const notLatest = Math.max(0, retainedCount - latestVariantCount);
    const mixed = groups.filter(function (row) { return Number(row.passed || 0) > 0 && Number(row.soft_failed || 0) + Number(row.hard_failed || 0) > 0; });
    const currentIncidents = groups.filter(function (row) { return ['soft', 'hard'].includes(amdLatestState(row, latestBuild)); });
    const currentPassing = groups.filter(function (row) { return amdLatestState(row, latestBuild) === 'passed'; });
    const currentUnknown = groups.filter(function (row) { return amdLatestState(row, latestBuild) === 'unknown'; });
    const currentVariants = currentPassing.concat(currentIncidents, currentUnknown);
    const missing = groups.filter(function (row) { return amdLatestState(row, latestBuild) === 'missing'; });

    if (!publication.complete) {
      const groupRetention = publication.retention.group_catalog || {};
      const buildRetention = publication.retention.builds || {};
      host.append(n('div', 'ops-evidence-note is-warning', 'AMD test-health drill-down is storage-bounded: '
        + integer(groupRetention.published) + ' of ' + integer(groupRetention.source) + ' job-variant rows and '
        + integer(buildRetention.published) + ' of ' + integer(buildRetention.source) + ' nightly summary rows are published. '
        + 'Top-level latest-build totals remain source-complete; catalog counts, hardware distributions, and history charts are retained-row lower bounds, and aggregate rates are unavailable.'));
    }

    host.append(statusStrip([
      {id: 'amd-health-build', label: 'LATEST AMD NIGHTLY', value: latestBuild ? '#' + latestBuild : '-', meta: shortDate(summary.latest_observed_at), tone: hard ? 'is-danger' : soft ? 'is-warning' : 'is-success', url: summary.latest_build_url, actionLabel: 'Open Buildkite ↗'},
      {id: 'amd-health-test-groups', label: 'LATEST AMD TEST GROUPS', value: logicalPresentation.value, meta: logicalPresentation.meta, tone: logicalPresentation.tone, scope: 'Main CI AMD nightly #' + value(latestBuild), observed: latestTestGroups.observed_at || summary.latest_observed_at, provenance: latestTestGroups.count_basis || 'Unique source-aligned AMD test-group identities observed in this main CI nightly; topology-distinct routes remain separate and configured shards count once.', sources: [{label: 'Open published AMD health data', url: SOURCE_ASSETS.amdTestHealth}], actionLabel: logicalRows.length ? 'Browse logical test groups →' : 'Inspect count definition', onOpen: logicalRows.length ? function () { openAmdLogicalCatalog('Latest AMD logical test groups', 'Build-pinned logical groups; partial and non-passing groups are listed first', logicalRows, logicalInventory, amdHealth); } : null},
      {id: 'amd-health-observed', label: 'LATEST JOB VARIANTS', value: integer(latestVariantCount), meta: integer(passing) + ' passing - ' + integer(nonPassing) + ' non-passing exact jobs' + (notLatest ? '; ' + integer(notLatest) + ' older variants retained only for history' : ''), tone: hard ? 'is-danger' : nonPassing ? 'is-warning' : 'is-success', onOpen: function () { openAmdCatalog('Latest AMD job variants', 'Exact AMD gating-job variants observed in the latest main CI nightly', currentVariants, amdHealth, 'all'); }},
      {id: 'amd-health-incidents', label: 'FAILURE OBSERVATIONS', value: integer(incidents), meta: integer(soft) + ' soft - ' + integer(hard) + ' hard' + (unknown ? ' - ' + integer(unknown) + ' unknown' : ''), tone: hard ? 'is-danger' : soft ? 'is-warning' : 'is-success', onOpen: function () { openAmdCatalog('Current AMD failure observations', 'AMD gating-job variants with a soft or hard result in the latest main CI nightly', currentIncidents, amdHealth, 'incident'); }},
    ]));
    const note = n('div', 'ops-evidence-note is-info');
    add(note, [n('strong', '', 'Main CI AMD gating-job health. '), n('span', '', 'Each row is one exact AMD Buildkite job variant. Soft results are raw warning observations, not confirmed incidents until they recur on two distinct completed builds. The latest count is current-only; older names remain available as history and are not treated as missing failures. Upstream results are not used as AMD passes.')]);
    host.append(note);

    const hardware = amdHardwareRows(groups, latestBuild);
    const chartsGrid = n('div', 'ops-grid ops-grid-2 ops-amd-health-charts');
    const buildChart = chartPanel('AMD health by nightly', 'Passing, soft-failing, hard-failing, and unknown job variants in each retained AMD build', 'analytics-amd-build-health');
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
        options: {scales: {x: {stacked: true, grid: {display: false}, ticks: {maxTicksLimit: 10}}, y: {stacked: true, beginAtZero: true, title: {display: true, text: 'AMD job variants'}}}},
        evidenceTitle: 'AMD nightly job-variant health',
        evidence: builds.map(function (build) { return {label: '#' + build.number, timestamp: build.observed_at, url: build.url, valueSummary: integer(build.passed) + ' passing - ' + integer(build.soft_failed) + ' soft - ' + integer(build.hard_failed) + ' hard', details: {observed_job_variants: build.observed, passing: build.passed, soft_failed: build.soft_failed, hard_failed: build.hard_failed, unknown: build.unknown, job_variant_pass_rate: Number(build.pass_rate_pct || 0).toFixed(1) + '%'}}; }),
      });
      drawChart('analytics-amd-hardware-health', hardwareChart.canvas, {
        type: 'bar',
        data: {labels: hardware.map(function (row) { return row.label; }), datasets: [
          {label: 'Passing', data: hardware.map(function (row) { return row.passed; }), backgroundColor: '#35bb78'},
          {label: 'Soft fail', data: hardware.map(function (row) { return row.soft; }), backgroundColor: '#e3a63a'},
          {label: 'Hard fail', data: hardware.map(function (row) { return row.hard; }), backgroundColor: '#e06464'},
          {label: 'Not in latest', data: hardware.map(function (row) { return row.missing; }), backgroundColor: '#66717d'},
        ]},
        options: {indexAxis: 'y', scales: {x: {stacked: true, beginAtZero: true, title: {display: true, text: 'AMD job variants'}}, y: {stacked: true, grid: {display: false}}}},
        evidenceTitle: 'Latest AMD health by hardware variant',
        evidence: hardware.map(function (row) { return {label: row.label, valueSummary: integer(row.passed) + ' passing - ' + integer(row.soft) + ' soft - ' + integer(row.hard) + ' hard - ' + integer(row.missing) + ' not in latest', sources: [{label: 'Open published AMD health data', url: SOURCE_ASSETS.amdTestHealth}], onOpen: function () { openAmdCatalog(row.label + ' AMD job variants', 'Exact job variants assigned to ' + row.label, row.rows, amdHealth, 'all'); }}; }),
      });
    });

    const clusterSection = n('section', 'ops-cluster-section ops-amd-summary');
    const clusterHeader = n('header', 'ops-section-header');
    const clusterHeading = n('div', 'ops-section-heading');
    add(clusterHeading, [n('h2', 'ops-section-title', 'Retained AMD job-variant catalog'), n('p', 'ops-section-description', 'Start with the latest signal, then inspect exact nightly evidence for current or historical variants.')]);
    add(clusterHeader, [clusterHeading, button('Browse all ' + publication.prefix + integer(groups.length) + ' retained variants', function () { openAmdCatalog('Retained AMD job variants', 'Search exact AMD job variants retained across the nightly history window' + (publication.complete ? '' : '; the published catalog is incomplete'), groups, amdHealth, 'all'); })]);
    clusterSection.append(clusterHeader);
    const clusterGrid = n('div', 'ops-cluster-grid ops-amd-cluster-grid');
    clusterGrid.append(amdHealthCluster('Needs attention', currentIncidents.length + currentUnknown.length, 'Soft, hard, or unresolved result in the latest nightly', integer(soft) + ' soft - ' + integer(hard) + ' hard - ' + publication.prefix + integer(currentUnknown.length) + ' retained unresolved', hard ? 'is-danger' : 'is-warning', function () { openAmdCatalog('AMD variants needing attention', 'Current soft, hard, and unresolved outcomes only; historical-only names are excluded', currentVariants, amdHealth, 'attention'); }, !publication.complete));
    clusterGrid.append(amdHealthCluster('Passing now', currentPassing.length, 'Latest exact AMD job result passed', publication.complete ? percent(currentPassing.length, latestVariantCount) + ' of latest job variants' : 'Aggregate share unavailable for incomplete catalog', 'is-success', function () { openAmdCatalog('AMD job variants passing now', 'Latest exact main CI AMD gating-job outcomes', currentPassing, amdHealth, 'passing'); }, !publication.complete));
    clusterGrid.append(amdHealthCluster('Mixed history', mixed.length, 'Both passing and non-passing nightlies retained', integer(summary.build_count) + ' source nightlies; retained catalog only', 'is-warning', function () { openAmdCatalog('AMD job variants with mixed history', 'Job variants that passed on some AMD nightlies and had raw failures on others', mixed, amdHealth, 'mixed'); }, !publication.complete));
    clusterGrid.append(amdHealthCluster('Historical only', missing.length, 'Older job names not present in the latest nightly', 'Not classified as current failures', 'is-neutral', function () { openAmdCatalog('Historical AMD job variants', 'Retained older names that are not part of the latest nightly observation set', missing, amdHealth, 'missing'); }, !publication.complete));
    clusterSection.append(clusterGrid);
    host.append(clusterSection);

    const priority = currentIncidents.slice().sort(function (a, b) {
      const stateDelta = (amdLatestState(a, latestBuild) === 'hard' ? 0 : 1) - (amdLatestState(b, latestBuild) === 'hard' ? 0 : 1);
      return stateDelta || Number(amdGroupPassRate(a) || 0) - Number(amdGroupPassRate(b) || 0) || String(a.display_name || a.name).localeCompare(String(b.display_name || b.name));
    }).slice(0, 10);
    host.append(panel('Current AMD failures to inspect', integer(priority.length) + ' highest-priority raw results shown; every row opens exact nightly evidence', dataTable([
      {label: 'AMD job variant', sticky: true, width: '380px', render: function (row) { return amdGroupIdentity(row, function () { openAmdGroupDetail(row, amdHealth); }); }},
      {label: 'Queue', width: '170px', render: function (row) { return n('span', 'ops-mono', value(row.queue)); }},
      {label: 'Retained-run pass rate', numeric: true, width: '170px', render: function (row) { const rate = amdGroupPassRate(row); return linkButton(rate === null ? '-' : rate.toFixed(1) + '%', function () { openAmdGroupDetail(row, amdHealth); }); }},
      {label: 'Latest', width: '110px', render: function (row) { const latest = amdLatestState(row, latestBuild); return linkedBadge(amdStateLabel(latest), row.latest_url, function () { openAmdGroupDetail(row, amdHealth); }, toneForState(latest)); }},
      {label: 'Pass / soft / hard', numeric: true, width: '170px', render: function (row) { return linkButton(integer(row.passed) + ' / ' + integer(row.soft_failed) + ' / ' + integer(row.hard_failed), function () { openAmdGroupDetail(row, amdHealth); }); }},
      {label: 'Latest evidence', width: '160px', render: function (row) { return externalLink('#' + value(row.latest_build_number), row.latest_url, 'ops-mono'); }},
    ], priority, publication.prefix + integer(currentIncidents.length) + ' retained current AMD failure observations; use Browse all for the ' + (publication.complete ? 'complete' : 'bounded') + ' catalog', {name: 'amd-current-incidents', minWidth: '1020px'}), 'ops-amd-priority'));
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

  function clusterNodeCofailures(node, nodeRaw, hardware, runs, windowMins) {
    const failing = runs.filter(function (r) { return r._start !== null; })
      .slice().sort(function (a, b) { return a._start - b._start; });
    const windowMs = windowMins * 60000;
    const events = [];
    let cluster = [];
    let clusterEnd = null;
    function flush() {
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
    const rows = range
      ? allRows.filter(function (row) { return row.end >= range.min && row.start <= range.max; })
      : allRows;
    const pad = Math.max(60000, (dataMax - dataMin) * 0.02);
    const xMin = range ? range.min : dataMin - pad;
    const xMax = range ? range.max : dataMax + pad;
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

  const COFAIL_COMPACT_RUNS = 3;

  function agentCofailureCard(event, onSelectEvent, selected) {
    const card = n('div', 'ops-cofailure-card' + (event.cross_pipeline ? ' is-cross' : '') + (selected ? ' is-selected' : ''));
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
      card.append(n('p', 'ops-cofailure-detail',
        integer(event.run_count) + ' runs · ' + integer(event.hard_failed) + ' hard · '
        + integer(event.soft_failed) + ' soft · pipelines: ' + (event.pipelines || []).join(', ')));
    }
    const allRuns = event.runs || [];
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

  function agentFailureAccountingCounts(rows, options) {
    const totals = new Map();
    (Array.isArray(rows) ? rows : []).forEach(function (row) {
      if (String(row.d || '') < String(options.startDay || '')) return;
      if (options.nightlyOnly && !row.ng) return;
      if (options.excludeCancelled && row.bc) return;
      const count = Math.max(0, Number(row.c) || 0);
      if (!count || !row.nd || !['hard', 'soft'].includes(row.s)) return;
      let value = totals.get(row.nd);
      if (!value) { value = {hard: 0, soft: 0, signal: 0}; totals.set(row.nd, value); }
      value[row.s] += count;
      if ((options.signal === 'infra' && row.i)
        || (options.signal === 'hard' && row.s === 'hard')
        || options.signal === 'all') value.signal += count;
    });
    return totals;
  }

  function agentAggregateRunCounts(rows, options) {
    const totals = {runs: 0, identifiedRuns: 0};
    (Array.isArray(rows) ? rows : []).forEach(function (row) {
      if (String(row.d || '') < String(options.startDay || '')) return;
      if (options.hardware && options.hardware !== 'all' && row.h !== options.hardware) return;
      const bucket = options.nightlyOnly ? row.n : row.a;
      if (!Array.isArray(bucket) || !Number(bucket[0])) return;
      totals.runs += Math.max(0, Number(bucket[0]) || 0);
      if (row.id) totals.identifiedRuns += Math.max(0, Number(bucket[0]) || 0);
    });
    return totals;
  }

  function agentAggregateFailureCounts(rows, options) {
    const totals = {hard: 0, soft: 0, signal: 0};
    (Array.isArray(rows) ? rows : []).forEach(function (row) {
      if (String(row.d || '') < String(options.startDay || '')) return;
      if (options.hardware && options.hardware !== 'all' && row.h !== options.hardware) return;
      if (options.nightlyOnly && !row.ng) return;
      if (options.excludeCancelled && row.bc) return;
      const count = Math.max(0, Number(row.c) || 0);
      if (!count || !['hard', 'soft'].includes(row.s)) return;
      totals[row.s] += count;
      if ((options.signal === 'infra' && row.i)
        || (options.signal === 'hard' && row.s === 'hard')
        || options.signal === 'all') totals.signal += count;
    });
    return totals;
  }

  function renderAmdAgentHealth(host, agentHealth) {
    const scopeRows = ['node_days', 'failing_runs', 'failure_accounting', 'node_accounting_totals', 'failure_accounting_totals'];
    if (agentHealth.hardware_scope !== 'amd_mi_gpu' || !Array.isArray(agentHealth.pipelines)
      || agentHealth.pipelines.length !== 1 || agentHealth.pipelines[0] !== 'ci'
      || scopeRows.some(function (key) { return (agentHealth[key] || []).some(function (row) { return !isAmdMiHardware(row.h); }); })
      || (agentHealth.failing_runs || []).some(function (row) { return row.p !== 'ci' || !isAmdRuntimeJob({queue: row.q, name: row.g}); })) {
      host.append(n('div', 'ops-evidence-note is-warning', 'AMD MI agent-health data is unavailable. Current CI MI GPU observations are required.'));
      return;
    }
    const nodeDays = Array.isArray(agentHealth.node_days) ? agentHealth.node_days : [];
    const failingRaw = Array.isArray(agentHealth.failing_runs) ? agentHealth.failing_runs : [];
    const failureAccounting = Array.isArray(agentHealth.failure_accounting) ? agentHealth.failure_accounting : [];
    const nodeAccountingTotals = Array.isArray(agentHealth.node_accounting_totals) ? agentHealth.node_accounting_totals : [];
    const failureAccountingTotals = Array.isArray(agentHealth.failure_accounting_totals) ? agentHealth.failure_accounting_totals : [];
    const operationsRetention = (agentHealth || {}).operations_publication_retention || {};
    const operationsNodeRetention = operationsRetention.node_days || {};
    const operationsAccountingRetention = operationsRetention.failure_accounting || {};
    const sourceRetention = (agentHealth || {}).retention || {};
    const sourcePipelineScope = sourceRetention.pipeline_scope || {};
    const sourceCoverage = agentSourceCoverage(agentHealth);
    const buildCreatedCohort = sourcePipelineScope.version === 2
      && sourcePipelineScope.basis === 'terminal_jobs_by_build_created_at'
      && sourcePipelineScope.day_basis === 'build_created_at_utc';
    const pipelineHistoryIncomplete = !sourceCoverage || sourcePipelineScope.complete_window === false;
    let sourceHistoryIncomplete = !agentSourceHistoryComplete(agentHealth);
    let nodeDetailIncomplete = operationsNodeRetention.complete === false || sourceHistoryIncomplete;
    let failureDetailIncomplete = operationsAccountingRetention.complete === false || sourceHistoryIncomplete;
    const aggregateAccountingComplete = operationsRetention.aggregate_accounting_complete === true;
    const evidenceRetention = (((agentHealth || {}).retention || {}).failure_evidence) || {};
    const evidenceIncomplete = evidenceRetention.complete_relative_to_source === false
      || ((operationsRetention.failure_evidence || {}).complete === false);
    const hardwareTypes = Array.isArray(agentHealth.hardware_types) ? agentHealth.hardware_types.filter(isAmdMiHardware) : [];
    const windowOptions = Array.isArray(agentHealth.window_options) ? agentHealth.window_options : [1, 3, 7, 14, 30, 60];
    const cofailOptions = Array.isArray(agentHealth.cofailure_window_options) ? agentHealth.cofailure_window_options : [30, 60, 120, 180, 360, 720, 1440];
    const endMs = new Date(agentHealth.generated_at || Date.now()).getTime();

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
        day: r.d,
        _start: Number.isFinite(start) ? start : null,
        _end: Number.isFinite(end) ? end : null,
      };
    });

    let windowId = AGENT_WINDOW_DAYS[state.agentWindow] ? state.agentWindow : ((agentHealth.default_window_days || 7) + 'd');
    let gpu = (function (g) { return (g === 'all' || hardwareTypes.includes(g)) ? g : 'all'; })(state.agentGpu || 'all');
    let search = '';
    let selectedNode = state.agentNode || '';
    let cofailMins = (function (m) { return cofailOptions.includes(m) ? m : (agentHealth.default_cofailure_window_mins || 180); })(parseInt(state.agentCofail, 10));
    let excludeCancelled = state.agentExclCancel !== '0';
    let nightlyOnly = state.agentNightly === '1';
    let signal = ['infra', 'hard', 'all'].includes(state.agentSignal) ? state.agentSignal : 'infra';
    let sort = {key: 'infra_suspect', dir: 'desc'};
    let sortExplicit = false;
    let searchTimer = null;
    let current = null;
    let timelineRange = null;
    let timelineBounds = null;
    let eventNodeFilter = '';
    let selectedEventKey = '';
    function eventKey(e) { return e.node_raw + '@' + e._startMs; }

    add(host, pageHeaderNote());
    if (sourceCoverage && buildCreatedCohort) {
      add(host, n('div', 'ops-evidence-note is-info ops-agent-completion-scope',
        'Terminal runs from CI builds created in the selected UTC window. Runs require a recorded or bounded completion time. Fresh build creation coverage for '
        + agentPipelineScopeLabel(agentHealth) + ', from ' + value(sourcePipelineScope.collected_from)
        + ' through ' + value(sourcePipelineScope.collected_to) + ' UTC.'));
    }
    if (evidenceIncomplete || nodeDetailIncomplete || failureDetailIncomplete) {
      const retentionNote = n('div', 'ops-evidence-note is-warning');
      add(retentionNote, [
        n('strong', '', !sourceCoverage ? 'Complete agent-health history is unavailable. ' : pipelineHistoryIncomplete ? 'Agent-health pipeline history is incomplete. ' : 'Agent-health drill-down evidence is storage-bounded. '),
        n('span', '', integer(evidenceRetention.published) + ' of ' + integer(evidenceRetention.source) + ' failing-run links and ' + integer(operationsNodeRetention.published) + ' of ' + integer(operationsNodeRetention.source) + ' node-day rows are published in Operations. ' + (!sourceCoverage ? 'Retained counts describe observed runs; node-specific failure rates are unavailable. ' : pipelineHistoryIncomplete ? 'Complete fresh UTC history for ' + agentPipelineScopeLabel(agentHealth) + ' begins ' + value(sourcePipelineScope.collected_from) + ' and ends ' + value(sourcePipelineScope.collected_to) + '; the full ' + integer(agentHealth.max_window_days || 60) + '-day window is not yet complete. Older retained history describes observed runs. ' : sourceHistoryIncomplete ? 'The source ledger dropped ' + integer(sourceRetention.dropped_oldest_day_count) + ' oldest UTC days and begins ' + value(sourceRetention.retained_start) + '; compact accounting is exact only for that retained suffix. ' : 'Exact date/hardware ledger totals remain available through compact accounting. ') + (pipelineHistoryIncomplete ? 'For selections outside the complete fresh UTC interval or with omitted rows, ' : 'When node detail or source history is incomplete, ') + 'node counts and node-specific failure rates are unavailable; table counts, timelines, distinct groups, and co-failure events are retained-evidence lower bounds.'),
      ]);
      host.append(retentionNote);
    }
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

    const timelineToolbar = n('div', 'ops-toolbar ops-agent-toolbar');
    const nodeField = n('label', 'ops-field-label', 'Timeline node ');
    const nodeSelect = n('select', 'ops-select');
    nodeSelect.setAttribute('aria-label', 'Physical node for run timeline');
    nodeField.append(nodeSelect);
    timelineToolbar.append(nodeField);
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
      add(note, [n('strong', '', 'AMD physical CI agent health. '), n('span', '', (buildCreatedCohort ? 'Terminal runs from CI builds created in the selected UTC window on AMD GPU hardware' : 'Every build on AMD GPU hardware') + ' — all branches and PRs across ' + agentPipelineScopeLabel(agentHealth) + ' — ' + (buildCreatedCohort ? 'are' : 'is') + ' attributed to the physical node from its Buildkite agent tag. The timeline and co-failure clustering run on the Failure signal you pick below: "infra-suspect" (the default — anomalous, node-attributable failures, since most PR failures are code bugs), all hard failures, or all failures. Toggle the signal, build scope, cancelled-job handling, and co-failure window; click any node or event to load its timeline.')]);
      return note;
    }

    function criteriaNote() {
      const rate = Number(agentHealth.infra_suspect_min_pass_rate);
      const pct = Number.isFinite(rate) ? Math.round(rate * 100) + '%' : '50%';
      const samples = Number(agentHealth.infra_suspect_min_samples) || 3;
      const note = n('div', 'ops-evidence-note is-neutral ops-agent-criteria');
      add(note, [
        n('strong', '', 'What counts as an anomalous (infra-suspect) failure? '),
        n('span', '', 'When the Failure signal is set to Infra-suspect (the default), a soft or hard failure is treated as node-attributable when its exact test group, on that same ' + (buildCreatedCohort ? 'UTC build creation day' : 'day') + ', both: '),
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

    function signalMatch(r) {
      if (signal === 'infra') return r.infra_suspect;
      if (signal === 'hard') return r.state === 'hard';
      return true; // 'all' — every shipped record is a hard/soft failure
    }
    function signalTerm() {
      return signal === 'infra' ? 'infra-suspect' : signal === 'hard' ? 'hard' : 'all';
    }
    function signalColumnLabel() {
      return signal === 'infra' ? 'Infra-suspect failures' : 'Hard failures';
    }
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
      function runInWindow(run) {
        if (run._start === null || run._start > endMs) return false;
        if (!buildCreatedCohort) return run._start >= startMs;
        const day = typeof run.day === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(run.day)
          ? Date.parse(run.day + 'T00:00:00Z') : NaN;
        return Number.isFinite(day) && new Date(day).toISOString().slice(0, 10) === run.day
          && run.day >= startDay && day <= endMs;
      }
      sourceHistoryIncomplete = !agentSourceHistoryComplete(agentHealth, startDay);
      nodeDetailIncomplete = operationsNodeRetention.complete === false || sourceHistoryIncomplete;
      failureDetailIncomplete = operationsAccountingRetention.complete === false || sourceHistoryIncomplete;

      const byNode = new Map();
      let totalRuns = 0;
      let identifiedRuns = 0;
      nodeDays.forEach(function (nd) {
        if (String(nd.d) < startDay) return;
        if (!matchesFilter(nd.h, nd.nd)) return;
        const bucket = nightlyOnly ? nd.n : nd.a;
        if (!bucket || !bucket[0]) return;
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
      const aggregateRuns = agentAggregateRunCounts(nodeAccountingTotals, {
        startDay: startDay,
        hardware: gpu,
        nightlyOnly: nightlyOnly,
      });
      const aggregateFailures = agentAggregateFailureCounts(failureAccountingTotals, {
        startDay: startDay,
        hardware: gpu,
        nightlyOnly: nightlyOnly,
        excludeCancelled: excludeCancelled,
        signal: signal,
      });
      const exactAggregateScope = aggregateAccountingComplete
        && !sourceHistoryIncomplete
        && nodeAccountingTotals.length > 0
        && failureAccountingTotals.length > 0
        && !search;
      if (nodeDetailIncomplete && exactAggregateScope) {
        totalRuns = aggregateRuns.runs;
        identifiedRuns = aggregateRuns.identifiedRuns;
      }

      const fRuns = failing.filter(function (r) {
        if (!signalMatch(r)) return false;
        if (!runInWindow(r)) return false;
        if (nightlyOnly && !r.nightly) return false;
        if (excludeCancelled && r.build_canceled) return false;
        return matchesFilter(r.hardware, r.node_raw);
      });
      const runsByNode = new Map();
      fRuns.forEach(function (r) { if (!runsByNode.has(r.node_raw)) runsByNode.set(r.node_raw, []); runsByNode.get(r.node_raw).push(r); });
      const accountingRows = failureAccounting.filter(function (row) {
        return matchesFilter(row.h, row.nd);
      });
      const accountedByNode = agentFailureAccountingCounts(accountingRows, {
        startDay: startDay,
        nightlyOnly: nightlyOnly,
        excludeCancelled: excludeCancelled,
        signal: signal,
      });
      const failByNode = new Map();
      const signalByNode = {};
      if (failureAccounting.length) {
        accountedByNode.forEach(function (value, nodeRaw) {
          failByNode.set(nodeRaw, {hard: value.hard, soft: value.soft});
          signalByNode[nodeRaw] = value.signal;
        });
      } else {
        failing.forEach(function (r) {
          if (!runInWindow(r)) return;
          if (nightlyOnly && !r.nightly) return;
          if (excludeCancelled && r.build_canceled) return;
          if (!matchesFilter(r.hardware, r.node_raw)) return;
          let f = failByNode.get(r.node_raw);
          if (!f) { f = {hard: 0, soft: 0}; failByNode.set(r.node_raw, f); }
          if (r.state === 'hard') f.hard += 1; else if (r.state === 'soft') f.soft += 1;
        });
      }
      const events = [];
      const groupsByNode = {};
      runsByNode.forEach(function (runs, nodeRaw) {
        const hardware = (byNode.get(nodeRaw) || {}).hardware || (runs[0] && runs[0].hardware) || '';
        if (!failureAccounting.length) signalByNode[nodeRaw] = runs.length;
        groupsByNode[nodeRaw] = new Set(runs.map(function (x) { return x.group; })).size;
        clusterNodeCofailures(agentNodeLabel(nodeRaw, hardware), nodeRaw, hardware, runs, cofailMins).forEach(function (e) { events.push(e); });
      });
      const eventsByNode = {};
      events.forEach(function (e) { eventsByNode[e.node_raw] = (eventsByNode[e.node_raw] || 0) + 1; });

      const agents = [];
      byNode.forEach(function (agg, nodeRaw) {
        const identified = nodeRaw !== '(unidentified)';
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
      return {agents: agents, events: events, fRuns: fRuns, totalRuns: totalRuns, identifiedRuns: identifiedRuns, aggregateFailures: aggregateFailures, exactAggregateScope: exactAggregateScope, nodeDetailComplete: !nodeDetailIncomplete, failureDetailComplete: !failureDetailIncomplete};
    }

    function renderKpis(view) {
      clear(kpiHost);
      const identifiedNodes = view.agents.filter(function (a) { return a.identified; }).length;
      const unreliable = view.agents.filter(function (a) { return a.identified && a.infra_suspect > 0; }).length;
      const coveragePct = view.totalRuns ? (100 * view.identifiedRuns / view.totalRuns) : 0;
      const concurrent = view.events.filter(function (e) { return e.concurrent; }).length;
      const cross = view.events.filter(function (e) { return e.cross_pipeline; }).length;
      const nodeMetricsAvailable = view.nodeDetailComplete;
      const coverageAvailable = view.nodeDetailComplete || view.exactAggregateScope;
      kpiHost.append(statusStrip([
        {id: 'agent-nodes', label: 'IDENTIFIED AMD NODES', value: nodeMetricsAvailable ? integer(identifiedNodes) : 'Unavailable', meta: coverageAvailable ? integer(view.totalRuns) + ' exact runs in ' + windowId : 'node detail is incomplete', tone: 'is-info'},
        {id: 'agent-unreliable', label: 'NODES WITH FAILURES', value: nodeMetricsAvailable && view.failureDetailComplete ? integer(unreliable) : 'Unavailable', meta: view.exactAggregateScope ? integer(view.aggregateFailures.signal) + ' exact ' + signalTerm() + ' failures in aggregate' : 'node-specific accounting is incomplete', tone: unreliable ? 'is-warning' : 'is-success'},
        {id: 'agent-coverage', label: 'NODE COVERAGE', value: coverageAvailable ? coveragePct.toFixed(1) + '%' : 'Unavailable', meta: coverageAvailable ? integer(view.identifiedRuns) + ' / ' + integer(view.totalRuns) + ' exact runs identified' : 'aggregate accounting unavailable', tone: coverageAvailable && coveragePct >= 50 ? 'is-success' : coverageAvailable && coveragePct > 0 ? 'is-warning' : 'is-danger'},
        {id: 'agent-cofail', label: 'CO-FAILURE EVENTS', value: (evidenceIncomplete ? '≥' : '') + integer(view.events.length), meta: integer(concurrent) + ' retained concurrent · ' + integer(cross) + ' retained cross-pipeline', tone: view.events.length ? 'is-danger' : 'is-success', onOpen: function () { eventsHost.scrollIntoView({behavior: 'smooth', block: 'start'}); }},
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
      openHistoryEvidence(signalTerm() + ' failures on ' + agent.node, points, signalTerm() + ' failing runs on this node over ' + windowId + ', each linking to its BuildKite log', SOURCE_ASSETS.amdAgentHealth);
    }

    function renderTable(view) {
      clear(tableHost);
      const showSignalColumn = signal !== 'all';
      if (!sortExplicit) sort = {key: defaultSortKey(), dir: 'desc'};
      if (!showSignalColumn && sort.key === 'infra_suspect') sort.key = 'failures';
      const rows = sortedAgents(view);
      const columns = [
        {label: 'Physical node', sticky: true, width: '190px', sortKey: 'node', render: function (row) { return linkButton(value(row.node), function () { focusNode(row.node_raw); }, 'Show this node in the timeline and co-failure events', 'Show ' + value(row.node) + ' in the timeline and co-failure events'); }},
        {label: 'GPU', width: '62px', sortKey: 'hardware', render: function (row) { return value(row.hardware); }},
        {label: 'Runs', numeric: true, width: '66px', sortKey: 'runs', render: function (row) { return (nodeDetailIncomplete ? '≥' : '') + integer(row.runs); }},
        {label: 'Fail %', numeric: true, width: '74px', sortKey: 'incident_rate', render: function (row) { return nodeDetailIncomplete || failureDetailIncomplete ? 'Unavailable' : (Number(row.incident_rate) * 100).toFixed(1) + '%'; }},
        {label: 'Test group failures', numeric: true, width: '150px', sortKey: 'failures', render: function (row) {
          const cell = n('span', 'ops-failures-cell');
          cell.append(n('span', 'ops-failures-total', (failureDetailIncomplete ? '≥' : '') + integer(row.failures)));
          if (row.hard || row.soft) {
            cell.append(n('span', 'ops-failures-split', integer(row.hard) + ' hard · ' + integer(row.soft) + ' soft'));
          }
          return cell;
        }},
      ];
      if (showSignalColumn) {
        columns.push({label: signalColumnLabel(), numeric: true, width: '132px', sortKey: 'infra_suspect', render: function (row) { return (failureDetailIncomplete ? '≥' : '') + integer(row.infra_suspect); }});
      }
      columns.push(
        {label: 'Groups', numeric: true, width: '68px', sortKey: 'distinct_groups', render: function (row) { return (evidenceIncomplete ? '≥' : '') + integer(row.distinct_groups); }},
        {label: 'Co-fail', numeric: true, width: '68px', sortKey: 'cofailure_event_count', render: function (row) { return (evidenceIncomplete ? '≥' : '') + integer(row.cofailure_event_count); }},
        {label: 'Evidence', width: '86px', render: function (row) { return row._runs.length ? linkButton('runs ↗', function () { openNodeEvidence(row); }, 'Open retained ' + signalTerm() + ' failing runs and BuildKite logs for ' + value(row.node)) : n('span', 'ops-muted', 'not retained'); }}
      );
      const table = dataTable(columns, rows, 'Per-node AMD reliability in ' + windowId, {name: 'agent-nodes', minWidth: '930px', sort: sort, onSort: onSort});
      table.classList.add('ops-agent-table');
      tableHost.append(panel('AMD nodes by reliability', tableDescription(view), [table]));
    }

    function tableDescription(view) {
      const count = integer(view.agents.length) + ' node(s) in ' + windowId;
      const base = sourceHistoryIncomplete
        ? 'Only available source history is shown. Compact accounting covers that retained interval; node counts and node-specific Fail % are unavailable, and displayed counts are lower bounds. Groups, Co-fail, timelines, and linked Evidence use retained exact rows.'
        : nodeDetailIncomplete || failureDetailIncomplete
        ? 'Operations retained only a bounded suffix of node-granular rows. Exact date/hardware run and failure totals remain in compact accounting, but node counts and node-specific Fail % are unavailable; displayed node counts are lower bounds. Groups, Co-fail, timelines, and linked Evidence use retained exact rows.'
        : (buildCreatedCohort ? 'Terminal runs from CI builds created in the selected UTC window form the run and failure-rate denominator. Actual job timestamps remain in timelines. ' : 'Runs come from the node_days rollup of every build. ') + 'Test group failures and Fail % use complete compact failure accounting (with the hard/soft split) and honor the cancelled-build toggle. Groups, Co-fail, timelines, and linked Evidence use the retained exact-row evidence and may be lower bounds when its storage retention note is shown.';
      if (signal === 'infra') {
        return count + ', sorted by infra-suspect failures. The Infra-suspect failures column is the node-attributable subset of Test group failures — a group that otherwise passed that day, including on another node. ' + base + ' Click a node (or Evidence) to load its timeline; sort by any column.';
      }
      if (signal === 'hard') {
        return count + ', sorted by hard failures. The Hard failures column is the hard-only subset of Test group failures (soft failures excluded). ' + base + ' Click a node (or Evidence) to load its timeline; sort by any column.';
      }
      return count + ', sorted by Test group failures. With every failure counted, the total already carries the signal, so no separate signal column is shown. ' + base + ' Click a node (or Evidence) to load its timeline; sort by any column.';
    }

    function timelineAgents(view) {
      return view.agents.filter(function (agent) { return agent.identified && agent._runs.length; })
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

    function focusNode(nodeRaw) {
      eventNodeFilter = nodeRaw;
      selectNode(nodeRaw, true);
      renderEvents(current);
    }

    function currentTimelineRange() {
      if (timelineRange) return timelineRange;
      return timelineBounds ? {min: timelineBounds.min, max: timelineBounds.max} : null;
    }

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

    function timelineXScale() {
      const chart = charts.get('analytics-agent-timeline');
      return chart && chart.scales ? chart.scales.x : null;
    }

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

  function latencyMetric(raw) {
    if (raw === null || raw === undefined || raw === '') return null;
    const parsed = Number(raw);
    return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
  }

  function exactLatencyJobUrl(job, buildNumber) {
    if (!job || typeof job.job_id !== 'string' || !job.job_id.trim()
      || typeof job.url !== 'string' || job.url !== job.url.trim()
      || !Number.isSafeInteger(buildNumber) || buildNumber <= 0) return '';
    try {
      const parsed = new URL(job.url);
      if (parsed.protocol !== 'https:' || parsed.host !== 'buildkite.com'
        || parsed.username || parsed.password) return '';
      const buildPath = '/vllm/ci/builds/' + buildNumber;
      if (parsed.pathname === buildPath && !parsed.search
        && parsed.hash === '#' + job.job_id) return job.url;
      const keys = Array.from(parsed.searchParams.keys());
      if (parsed.pathname === buildPath + '/steps/canvas' && !parsed.hash
        && keys.length === 2 && keys.includes('jid') && keys.includes('tab')
        && parsed.searchParams.get('jid') === job.job_id
        && parsed.searchParams.get('tab') === 'output') return job.url;
    } catch (_) {}
    return '';
  }

  function latencyComparison(ops) {
    const payload = (ops || {}).latency || {};
    const cohort = payload.cohort || {};
    const nightlies = Array.isArray(cohort.nightlies) ? cohort.nightlies : [];
    const unavailable = function (reason) {
      return {available: false, unavailable_reason: reason, rows: [], cohort: cohort};
    };
    if (payload.schema_version !== 2 || payload.source_pipeline !== 'ci'
      || payload.job_scope !== 'amd_gpu' || payload.hardware_scope !== 'amd_mi_gpu'
      || payload.branch !== 'main' || payload.build_limit !== 5
      || payload.statistic !== 'median_of_per_nightly_group_wall_minutes') {
      return unavailable('The current main CI five-nightly timing contract is unavailable');
    }
    if (payload.available !== true || !nightlies.length || nightlies.length > 5) {
      return unavailable(payload.unavailable_reason || 'No completed main CI nightly timing is available');
    }
    const jobColumns = ['job_id', 'step_id', 'url', 'queue', 'hardware', 'raw_name', 'started_at', 'finished_at', 'duration_mins'];
    const compactJobs = Object.prototype.hasOwnProperty.call(payload, 'job_columns');
    if (compactJobs && (!Array.isArray(payload.job_columns)
      || payload.job_columns.length !== jobColumns.length
      || payload.job_columns.some(function (column, index) { return column !== jobColumns[index]; }))) {
      return unavailable('The current main CI timing evidence columns are invalid');
    }
    const numbers = new Set(nightlies.map(function (build) { return Number(build.number); }));
    if (numbers.size !== nightlies.length || nightlies.some(function (build) {
      return !Number.isInteger(Number(build.number)) || Number(build.number) <= 0
        || !Number.isFinite(Date.parse(build.created_at));
    })) return unavailable('The current main CI nightly cohort is invalid');
    function normalizeSample(sample) {
      if (!sample || typeof sample !== 'object' || Array.isArray(sample)) return null;
      if (!compactJobs) {
        if (sample.jobs !== undefined && (!Array.isArray(sample.jobs)
          || sample.jobs.some(function (job) { return !job || typeof job !== 'object' || Array.isArray(job); }))) return null;
        return sample;
      }
      if (!Array.isArray(sample.jobs) || !sample.jobs.length) return null;
      const jobs = [];
      for (const values of sample.jobs) {
        if (!Array.isArray(values) || values.length !== jobColumns.length
          || values.slice(0, 8).some(function (raw) { return typeof raw !== 'string'; })
          || [0, 2, 5, 6, 7].some(function (index) { return !values[index].trim(); })
          || !Number.isFinite(Date.parse(values[6])) || !Number.isFinite(Date.parse(values[7]))
          || typeof values[8] !== 'number' || !Number.isFinite(values[8]) || values[8] < 0) return null;
        const job = {};
        jobColumns.forEach(function (column, index) { job[column] = values[index]; });
        jobs.push(job);
      }
      return Object.assign({}, sample, {jobs: jobs});
    }
    function sideInCohort(side) {
      if (!side || side.source_pipeline !== 'ci' || side.job_scope !== 'amd_gpu'
        || side.hardware_scope !== 'amd_mi_gpu' || !Array.isArray(side.samples)) return null;
      const samples = side.samples.map(normalizeSample).filter(function (sample) {
        return sample && numbers.has(Number(sample.build_number))
          && latencyMetric(sample.duration_mins) !== null
          && Array.isArray(sample.jobs) && sample.jobs.length > 0
          && sample.jobs.every(function (job) { return isAmdRuntimeJob(job) && isAmdMiHardware(job.hardware)
            && exactLatencyJobUrl(job, Number(sample.build_number)); });
      });
      const unique = new Set(samples.map(function (sample) { return Number(sample.build_number); }));
      if (!samples.length || samples.length !== side.samples.length
        || unique.size !== samples.length || Number(side.sample_count) !== samples.length
        || latencyMetric(side.median_duration_mins) === null) return null;
      return Object.assign({}, side, {samples: samples, sample_count: samples.length});
    }
    const rows = (Array.isArray(payload.rows) ? payload.rows : []).map(function (row) {
      return {id: row.id, label: row.label, amd: sideInCohort(row.amd)};
    });
    return {available: true, rows: rows, cohort: cohort, interval: payload.interval, generated_at: payload.generated_at};
  }

  function latencySideDuration(side) {
    return side ? duration(side.median_duration_mins) : 'Unavailable';
  }

  function openLatencyEvidence(row, comparison) {
    const content = n('div', 'ops-stack');
    content.append(statusStrip([
      {label: 'AMD MI MEDIAN WALL TIME', value: latencySideDuration(row.amd), meta: row.amd ? integer(row.amd.sample_count) + ' / ' + integer(comparison.cohort.nightlies.length) + ' recent nightlies observed' : 'No AMD MI observation in the current five-nightly cohort', static: true},
    ]));
    content.append(n('p', 'ops-evidence-method', 'Each nightly contributes one observation: the longest wall completion time across that AMD MI test group’s parallel shards. The displayed metric is the median across observed nightlies in the fixed cohort below. Missing observations remain unavailable.'));
    const samples = row.amd ? row.amd.samples : [];
    const perBuild = new Map(samples.map(function (sample) { return [Number(sample.build_number), sample]; }));
    const rows = comparison.cohort.nightlies.map(function (build) {
      return {build: build, sample: perBuild.get(Number(build.number)) || null};
    });
    content.append(panel('AMD MI observations', 'The globally selected main CI nightlies; no older backfill', dataTable([
      {label: 'Main CI nightly', sticky: true, width: '150px', render: function (item) { const url = item.build.web_url || item.build.url; return pipelineUrlMatches(url, 'ci', false, item.build.number) ? externalLink('#' + item.build.number, url, 'ops-mono') : n('span', 'ops-mono', '#' + item.build.number); }},
      {label: 'Nightly created (UTC)', width: '190px', render: function (item) { return shortDate(item.build.created_at); }},
      {label: 'Group wall completion', numeric: true, width: '180px', render: function (item) { return item.sample ? duration(item.sample.duration_mins) : 'Not observed'; }},
      {label: 'Exact MI jobs', width: '420px', render: function (item) {
        if (!item.sample) return n('span', 'ops-cell-muted', 'No observation in this nightly');
        const links = n('div', 'ops-inline-links');
        item.sample.jobs.forEach(function (job) {
          const url = exactLatencyJobUrl(job, item.build.number);
          if (url) links.append(externalLink(value(job.queue || job.hardware || job.step_id, 'MI job') + ' · ' + duration(job.duration_mins), url));
        });
        return links.childNodes.length ? links : n('span', 'ops-cell-muted', 'Exact job links unavailable');
      }},
    ], rows, 'AMD MI timing evidence from the latest five main CI nightlies', {name: 'latency-evidence', minWidth: '960px'})));
    content.append(sourceActions([{label: 'Open current latency data', url: SOURCE_ASSETS.comparison}]));
    openOverlay(row.label || 'AMD MI nightly latency', 'Recent main CI AMD MI wall completion with exact nightly evidence', content, true, 'latency-' + row.id);
  }

  function renderLatencyComparison(host, comparison) {
    const cohort = comparison.cohort || {};
    const builds = cohort.nightlies || [];
    const rows = comparison.rows.slice().sort(function (left, right) {
      const leftValue = left.amd ? Number(left.amd.median_duration_mins) : -Infinity;
      const rightValue = right.amd ? Number(right.amd.median_duration_mins) : -Infinity;
      return rightValue - leftValue || compareText(left.label, right.label);
    });
    const timed = rows.filter(function (row) { return row.amd; });
    const context = n('div', 'ops-toolbar ops-analytics-window-toolbar');
    add(context, [
      n('strong', '', 'Latest five completed main CI nightlies'),
      n('span', 'ops-window-context', builds.map(function (build) { return '#' + build.number; }).join(' · ')),
    ]);
    host.append(context);
    const note = n('div', 'ops-evidence-note is-info');
    add(note, [
      n('strong', '', 'Current CI gating jobs only. '),
      n('span', '', 'One wall-completion observation per test group per nightly, then the median across up to five observations. Parallel shards contribute their maximum completion time. Missing recent observations stay unavailable; older builds are not substituted.'),
    ]);
    host.append(note);
    host.append(statusStrip([
      {label: 'MAIN CI NIGHTLIES', value: integer(builds.length) + ' / 5', meta: builds.length ? shortDate(builds[builds.length - 1].created_at) + ' – ' + shortDate(builds[0].created_at) : 'No recent nightly observations', static: true},
      {label: 'AMD GROUPS TIMED', value: integer(timed.length), meta: integer(rows.length - timed.length) + ' not observed in this cohort', static: true},
    ]));
    if (timed.length) {
      const top = timed.slice(0, 12);
      const chart = chartPanel('Recent AMD MI completion time', 'Median of per-nightly group wall completion in minutes', 'analytics-latency-comparison');
      chart.root.classList.add('ops-comparison-chart');
      host.append(chart.root);
      drawChart('analytics-latency-comparison', chart.canvas, {
        type: 'bar',
        data: {labels: top.map(function (row) { return compactChartLabel({name: row.label}, 42); }), datasets: [
          {label: 'AMD', data: top.map(function (row) { return row.amd.median_duration_mins; }), backgroundColor: '#e3a63a'},
        ]},
        options: {indexAxis: 'y', scales: {x: {beginAtZero: true, title: {display: true, text: 'Median group wall completion (minutes)'}}}},
        evidenceTitle: 'Recent gating-job timing evidence',
        evidence: top.map(function (row) { return {label: row.label, valueSummary: latencySideDuration(row.amd) + ' AMD MI', onOpen: function () { openLatencyEvidence(row, comparison); }}; }),
      });
    }
    const columns = [
      {label: 'Main CI test group', sticky: true, width: '330px', render: function (row) { return linkButton(row.label, function () { openLatencyEvidence(row, comparison); }); }},
      {label: 'AMD median', numeric: true, width: '140px', render: function (row) { return linkButton(latencySideDuration(row.amd), function () { openLatencyEvidence(row, comparison); }); }},
      {label: 'Observed nightlies', numeric: true, width: '180px', render: function (row) { return integer(row.amd ? row.amd.sample_count : 0) + ' / ' + integer(builds.length); }},
    ];
    host.append(compactTablePanel('Recent AMD MI nightly latency', integer(rows.length) + ' current main CI groups · fixed latest-five-nightly cohort', columns, rows, {
      id: 'latency-comparison-browser', limit: 12, alwaysBrowse: true,
      browserSubtitle: 'Each row opens all five recent nightlies and exact main CI gating jobs',
      searchPlaceholder: 'Filter current main CI test group',
      searchText: function (row) { return [row.label, row.id].join(' '); },
      geometry: {name: 'latency-comparison', minWidth: '800px'},
    }));
  }

  function analyticsViewSelector() {
    return segmented([
      {id: 'groups', label: 'AMD test health'}, {id: 'latency', label: 'AMD nightly latency'},
      {id: 'nightlies', label: 'AMD nightlies'}, {id: 'dns', label: 'DNS health'},
      {id: 'agent-health', label: 'CI agent health'},
    ], state.analyticsView, function (id) {
      setRouteState('ci-analytics', 'analyticsView', id, 'analytics_view');
    }, 'CI Analytics view');
  }

  async function renderAnalytics(host, ops) {
    const analyticsRenderToken = host.dataset.renderToken;
    if (state.analyticsView === 'dns') {
      add(host, pageHeader('CI Analytics', 'DNS resolver observations by AMD queue and physical node, with final job outcomes and exact Buildkite evidence.'));
      host.append(analyticsViewSelector());
      const loading = n('div', 'ops-loading ops-dns-loading', 'Loading DNS observations...');
      host.append(loading);
      try {
        const payload = await loadQueueDns();
        if (host.dataset.renderToken !== analyticsRenderToken || state.analyticsView !== 'dns') return;
        loading.remove();
        setFreshness(payload);
        renderAnalyticsDns(host, payload);
      } catch (error) {
        if (host.dataset.renderToken !== analyticsRenderToken || state.analyticsView !== 'dns') return;
        loading.remove();
        const unavailable = n('div', 'ops-error');
        add(unavailable, [
          n('strong', '', 'DNS observation data is unavailable. '),
          n('span', '', (error && error.message) || String(error)),
          externalLink('Open live DNS asset', SOURCE_ASSETS.queueDns, 'ops-button'),
          externalLink('Open Pages DNS fallback', SOURCE_ASSETS.queueDnsFallback, 'ops-button'),
        ]);
        host.append(unavailable);
      }
      return;
    }
    const amdHealth = currentAmdHealth(ops.amd_test_health);
    const comparison = latencyComparison(ops);
    const nightly = nightlyForCohort(ops, 'ci-amd');
    const builds = nightly.builds || [];
    const nightlyName = nightlyDisplayName(nightly);
    const analyticsObservedAt = state.analyticsView === 'agent-health'
      ? (ops.amd_agent_health || {}).generated_at
      : state.analyticsView === 'groups'
        ? ((amdHealth.summary || {}).latest_observed_at || ops.generated_at)
        : ops.generated_at;
    add(host, pageHeader('CI Analytics', 'Current main CI AMD MI health, nightly outcomes, and recent test-group timing.', analyticsObservedAt));
    host.append(analyticsViewSelector());
    if (state.analyticsView === 'groups') {
      renderAmdHealth(host, amdHealth);
      return;
    }

    if (state.analyticsView === 'agent-health') {
      renderAmdAgentHealth(host, ops.amd_agent_health || {});
      return;
    }

    if (!comparison.available && state.analyticsView === 'latency') {
      const unavailable = n('div', 'ops-evidence-note is-warning');
      add(unavailable, [n('strong', '', 'AMD MI nightly latency unavailable. '), n('span', '', comparison.unavailable_reason + '. Recent AMD MI timing data is required.')]);
      host.append(unavailable);
      return;
    }

    if (state.analyticsView === 'nightlies') {
      const latestNightly = builds[0] || {};
      const publishedLatestMovement = nightlyFailureMovement(latestNightly);
      const latestMovement = publishedLatestMovement && publishedLatestMovement.available !== false
        ? publishedLatestMovement
        : null;
      const movementBuilds = builds.filter(function (buildRow) {
        const movement = nightlyFailureMovement(buildRow);
        return buildRow.has_test_results !== false
          && Boolean(movement)
          && movement.available !== false;
      });
      const chronologicalMovementBuilds = movementBuilds.slice().reverse();
      host.append(statusStrip([
        {label: 'LATEST ' + nightlyName.toUpperCase() + ' NIGHTLY', value: latestNightly.number ? '#' + latestNightly.number : '-', meta: latestNightly.created_at ? shortDate(latestNightly.created_at) : 'No completed nightly', tone: toneForState(latestNightly.state), url: latestNightly.number ? exactPipelineBuildUrl(latestNightly, 'ci') : null},
        {label: 'JOB VARIANTS OBSERVED', value: integer(latestNightly.total_groups), meta: 'exact jobs in the latest completed nightly', onOpen: function () { if (latestNightly.number) openBuildDetail(latestNightly, nightlyName + ' build #' + value(latestNightly.number)); }},
        {label: 'NEW FAILURES', value: latestMovement ? integer(latestMovement.new.length) : '-', meta: latestMovement ? 'not failing in the preceding observed nightly' : 'unavailable in this snapshot', tone: latestMovement && latestMovement.new.length ? 'is-danger' : latestMovement ? 'is-success' : 'is-neutral', onOpen: function () { if (latestNightly.number) openBuildDetail(latestNightly, nightlyName + ' build #' + value(latestNightly.number), 'new'); }},
        {label: 'RECURRING FAILURES', value: latestMovement ? integer(latestMovement.recurring.length) : '-', meta: latestMovement ? 'failed in the preceding observed nightly too' : 'unavailable in this snapshot', tone: latestMovement && latestMovement.recurring.length ? 'is-warning' : latestMovement ? 'is-success' : 'is-neutral', onOpen: function () { if (latestNightly.number) openBuildDetail(latestNightly, nightlyName + ' build #' + value(latestNightly.number), 'recurring'); }},
        {label: 'FIXED', value: latestMovement ? integer(latestMovement.fixed.length) : '-', meta: latestMovement ? 'failed previously and passed now' : 'unavailable in this snapshot', tone: latestMovement && latestMovement.fixed.length ? 'is-success' : 'is-neutral', onOpen: function () { if (latestNightly.number) openBuildDetail(latestNightly, nightlyName + ' build #' + value(latestNightly.number), 'fixed'); }},
      ]));
      const nightlyNote = n('div', 'ops-evidence-note ' + (latestMovement ? 'is-info' : 'is-warning'));
      add(nightlyNote, latestMovement
        ? [
          n('strong', '', 'Main CI AMD MI job history. '),
          n('span', '', 'Every current hard or soft failure is counted once as new or recurring. A previous failure that passes is fixed. Missing or skipped jobs are omitted.'),
        ]
        : [n('strong', '', 'Failure movement unavailable. '), n('span', '', 'Refresh the operations snapshot to publish observed-failure-movement-v1 data.')]);
      host.append(nightlyNote);
      const cp = chartPanel(nightlyName + ' nightly failure movement', 'New and recurring failures are above zero; fixes are below. Missing or skipped jobs are omitted.', 'analytics-trend');
      host.append(cp.root);
      drawChart('analytics-trend', cp.canvas, {type: 'bar', data: {
        labels: chronologicalMovementBuilds.map(function (b) { return '#' + b.number; }),
        datasets: [
          {label: 'New failure', data: chronologicalMovementBuilds.map(function (b) { return nightlyFailureCount(b, 'new'); }), backgroundColor: '#e06464'},
          {label: 'Recurring failure', data: chronologicalMovementBuilds.map(function (b) { return nightlyFailureCount(b, 'recurring'); }), backgroundColor: '#c47732'},
          {label: 'Fixed', data: chronologicalMovementBuilds.map(function (b) { return -Number(nightlyFailureCount(b, 'fixed') || 0); }), backgroundColor: '#35bb78'},
        ],
      }, options: {
        interaction: {mode: 'index', intersect: false},
        scales: {x: {stacked: true}, y: {stacked: true, beginAtZero: true}},
        plugins: {tooltip: {callbacks: {label: function (item) { return item.dataset.label + ': ' + integer(Math.abs(item.parsed.y)); }}}},
      },
      evidenceTitle: nightlyName + ' nightly failure movement',
      evidence: chronologicalMovementBuilds.map(function (buildRow) { const movement = nightlyFailureMovement(buildRow); return {label: '#' + buildRow.number, timestamp: buildRow.created_at, url: exactPipelineBuildUrl(buildRow, 'ci'), valueSummary: integer(movement.new.length) + ' new - ' + integer(movement.recurring.length) + ' recurring - ' + integer(movement.fixed.length) + ' fixed', details: {state: buildRow.state, new_failure: movement.new.length, recurring_failure: movement.recurring.length, fixed: movement.fixed.length}}; })});
      host.append(dataTable([
        {label: nightlyName + ' nightly', sticky: true, width: '130px', render: function (r) { return externalLink('#' + r.number, exactPipelineBuildUrl(r, 'ci'), 'ops-mono'); }},
        {label: 'State', width: '120px', render: function (r) { return linkedBadge(r.state, exactPipelineBuildUrl(r, 'ci')); }},
        {label: 'Job variants observed', numeric: true, width: '160px', render: function (r) { return linkButton(integer(r.total_groups), function () { openBuildDetail(r, nightlyName + ' build #' + value(r.number)); }); }},
        {label: 'New failure', numeric: true, width: '120px', render: function (r) { const count = nightlyFailureCount(r, 'new'); return linkButton(count === null ? '-' : integer(count), function () { openBuildDetail(r, nightlyName + ' build #' + value(r.number), 'new'); }); }},
        {label: 'Recurring failure', numeric: true, width: '140px', render: function (r) { const count = nightlyFailureCount(r, 'recurring'); return linkButton(count === null ? '-' : integer(count), function () { openBuildDetail(r, nightlyName + ' build #' + value(r.number), 'recurring'); }); }},
        {label: 'Fixed', numeric: true, width: '90px', render: function (r) { const count = nightlyFailureCount(r, 'fixed'); return linkButton(count === null ? '-' : integer(count), function () { openBuildDetail(r, nightlyName + ' build #' + value(r.number), 'fixed'); }); }},
        {label: 'Started', width: '180px', render: function (r) { return shortDate(r.created_at); }},
      ], builds, nightlyName + ' AMD MI nightly failure movement', {name: 'nightly', minWidth: '980px'}));
      return;
    }

    if (state.analyticsView === 'latency') {
      renderLatencyComparison(host, comparison);
      return;
    }

}

  async function renderPerf(host, ops) {
    const perf = await fetchJSON('data/vllm/perf_eval/perf_eval.json');
    const models = Array.isArray(perf.models) ? perf.models : [];
    const summary = perf.summary || {};
    const miDevice = function (device) { return /^mi\d{3,4}[ax]?$/.test(String(device || '').toLowerCase()); };
    if (models.some(function (model) { return !Array.isArray(model.devices) || !model.devices.length
      || model.devices.some(function (device) { return !miDevice(device); })
      || (model.perf_configs || []).some(function (config) { return !miDevice(config.device) || config.no_gpu === true; }); })
      || (summary.amd_devices || []).some(function (device) { return !miDevice(device); })) {
      host.append(n('div', 'ops-evidence-note is-warning', 'AMD MI performance data is unavailable. Current MI GPU metric records are required.'));
      return;
    }
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

  const QUEUE_DNS_WINDOW_OPTIONS = [
    {id: '1h', label: 'Last hour', hours: 1},
    {id: '3h', label: 'Last 3 hours', hours: 3},
    {id: '12h', label: 'Last 12 hours', hours: 12},
    {id: '24h', label: 'Last day', hours: 24},
    {id: '72h', label: 'Last 3 days', hours: 72},
    {id: '168h', label: 'Last 7 days', hours: 168},
    {id: '720h', label: 'Last 30 days', hours: 720},
  ];
  const QUEUE_DNS_WINDOW_IDS = QUEUE_DNS_WINDOW_OPTIONS.map(function (option) { return option.id; });
  const QUEUE_DNS_STALE_MS = 12 * 60 * 60 * 1000;
  const QUEUE_DNS_FETCH_TIMEOUT_MS = 8 * 1000;
  const QUEUE_DNS_ARBITRATION_MS = 200;
  const QUEUE_DNS_OUTCOME_CONTRACT = 'dns-job-outcomes-v1';
  const QUEUE_DNS_PIPELINES = new Set(['ci']);
  const QUEUE_DNS_JOB_ID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
  const QUEUE_DNS_UTC_SECOND_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/;
  const QUEUE_DNS_WINDOW_METRIC_KEYS = ['first_at', 'last_at', 'episodes', 'match_count', 'signature_ids', 'target_categories'];
  let queueDnsPreferredCandidate = null;
  let queueDnsFetchGeneration = 0;

  function queueDnsCount(raw) {
    if (raw === null || raw === undefined || raw === '' || typeof raw === 'boolean') return 0;
    const parsed = Number(raw);
    return Number.isFinite(parsed) && parsed >= 0 ? Math.floor(parsed) : 0;
  }

  function queueDnsOutcomeCounts(raw) {
    const source = raw || {};
    const keys = ['passed_jobs', 'soft_failed_jobs', 'hard_failed_jobs'];
    const available = keys.every(function (key) {
      return Object.prototype.hasOwnProperty.call(source, key)
        && Number.isInteger(source[key]) && source[key] >= 0;
    }) && Number.isInteger(source.affected_jobs) && source.affected_jobs >= 0;
    const passed = queueDnsCount(source.passed_jobs);
    const softFailed = queueDnsCount(source.soft_failed_jobs);
    const hardFailed = queueDnsCount(source.hard_failed_jobs);
    return {
      available: available && passed + softFailed + hardFailed === queueDnsCount(source.affected_jobs),
      passed: passed,
      softFailed: softFailed,
      hardFailed: hardFailed,
    };
  }

  function queueDnsRowsComplete(payload, id, count) {
    if (payload.publication_retention === undefined) return true;
    const row = ((payload.publication_retention || {}).window_rows || {})[id];
    if (!row || typeof row !== 'object' || Array.isArray(row)) return null;
    return [row.source, row.published, row.omitted].every(Number.isSafeInteger)
      && row.published === count && row.omitted >= 0
      && row.source === row.published + row.omitted
      && row.complete === (row.omitted === 0)
      ? row.complete : null;
  }

  function queueDnsPayloadValid(payload) {
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return false;
    if (payload.schema_version !== 1 || queueTimestamp(payload.generated_at) === -Infinity) return false;
    if (!payload.retention || typeof payload.retention !== 'object' || Array.isArray(payload.retention)) return false;
    const outcomesMarked = Object.prototype.hasOwnProperty.call(payload, 'outcome_contract');
    if (outcomesMarked && payload.outcome_contract !== QUEUE_DNS_OUTCOME_CONTRACT) return false;
    if (!Array.isArray(payload.window_options) || payload.window_options.length !== QUEUE_DNS_WINDOW_OPTIONS.length) return false;
    if (!payload.window_options.every(function (option, index) {
      const expected = QUEUE_DNS_WINDOW_OPTIONS[index];
      return option && typeof option === 'object' && !Array.isArray(option)
        && option.id === expected.id && option.label === expected.label && option.hours === expected.hours;
    })) return false;
    if (payload.default_window !== '24h') return false;
    if (payload.count_basis === null || payload.count_basis === undefined) return false;
    if (!payload.scope || typeof payload.scope !== 'object' || Array.isArray(payload.scope)
      || payload.scope.hardware_scope !== 'amd_mi_gpu' || !Array.isArray(payload.scope.pipelines)
      || payload.scope.pipelines.length !== 1 || payload.scope.pipelines[0] !== 'ci') return false;
    if (!payload.classifier || typeof payload.classifier !== 'object' || Array.isArray(payload.classifier)) return false;
    if (!payload.coverage || typeof payload.coverage !== 'object' || Array.isArray(payload.coverage)) return false;
    if (!payload.windows || typeof payload.windows !== 'object' || Array.isArray(payload.windows)) return false;
    if (!payload.evidence || typeof payload.evidence !== 'object' || Array.isArray(payload.evidence) || !Array.isArray(payload.evidence.items)) return false;
    const publishedWindowIds = Object.keys(payload.windows);
    if (publishedWindowIds.length !== QUEUE_DNS_WINDOW_IDS.length
      || !QUEUE_DNS_WINDOW_IDS.every(function (id) { return publishedWindowIds.includes(id); })) return false;
    const generatedAtMs = queueTimestamp(payload.generated_at);
    if (queueTimestamp(payload.retention.end_exclusive) !== generatedAtMs) return false;
    const windowsValid = QUEUE_DNS_WINDOW_OPTIONS.every(function (option) {
      const windowBlock = payload.windows[option.id];
      const startMs = queueTimestamp(windowBlock && windowBlock.start);
      const endMs = queueTimestamp(windowBlock && windowBlock.end_exclusive);
      const structurallyValid = windowBlock && typeof windowBlock === 'object' && !Array.isArray(windowBlock)
        && startMs !== -Infinity && endMs === generatedAtMs
        && endMs - startMs === option.hours * 60 * 60 * 1000
        && windowBlock.coverage && typeof windowBlock.coverage === 'object' && !Array.isArray(windowBlock.coverage)
        && windowBlock.totals && typeof windowBlock.totals === 'object' && !Array.isArray(windowBlock.totals)
        && Array.isArray(windowBlock.rows);
      if (!structurallyValid) return false;
      const rowsComplete = queueDnsRowsComplete(payload, option.id, windowBlock.rows.length);
      if (rowsComplete === null || !outcomesMarked) return rowsComplete !== null;
      const totals = queueDnsOutcomeCounts(windowBlock.totals);
      if (!totals.available || !windowBlock.rows.every(function (row) {
        return queueDnsOutcomeCounts(row).available;
      })) return false;
      return [
        ['passed_jobs', totals.passed],
        ['soft_failed_jobs', totals.softFailed],
        ['hard_failed_jobs', totals.hardFailed],
      ].every(function (entry) {
        const published = windowBlock.rows.reduce(function (sum, row) {
          return sum + queueDnsCount(row[entry[0]]);
        }, 0);
        return rowsComplete ? entry[1] === published : published <= entry[1];
      });
    });
    return windowsValid && payload.evidence.items.every(function (row) {
      return queueDnsEvidenceItemValid(row, payload.windows);
    });
  }

  function queueDnsWithTimeout(promise, source, requestedTimeoutMs) {
    const parsed = Number(requestedTimeoutMs);
    const timeoutMs = Number.isFinite(parsed) && parsed > 0 ? parsed : QUEUE_DNS_FETCH_TIMEOUT_MS;
    return new Promise(function (resolve, reject) {
      const timer = window.setTimeout(function () {
        reject(new Error('DNS failure source timed out: ' + source));
      }, timeoutMs);
      Promise.resolve(promise).then(function (result) {
        window.clearTimeout(timer);
        resolve(result);
      }, function (error) {
        window.clearTimeout(timer);
        reject(error);
      });
    });
  }

  function compareQueueDnsCandidates(left, right) {
    const leftGeneratedAt = queueTimestamp(left.payload.generated_at);
    const rightGeneratedAt = queueTimestamp(right.payload.generated_at);
    if (leftGeneratedAt !== rightGeneratedAt) return rightGeneratedAt > leftGeneratedAt ? 1 : -1;
    return left.priority - right.priority;
  }

  async function loadQueueDns(requestedTimeoutMs) {
    const sources = [SOURCE_ASSETS.queueDns, SOURCE_ASSETS.queueDnsFallback];
    if (queueDnsPreferredCandidate) {
      return Object.assign({}, queueDnsPreferredCandidate.payload, {__sourceAsset: queueDnsPreferredCandidate.source});
    }
    const generation = queueDnsFetchGeneration;
    const resolved = [];
    const attempts = sources.map(function (source, index) {
      return queueDnsWithTimeout(fetchJSON(source), source, requestedTimeoutMs).then(function (payload) {
        if (!queueDnsPayloadValid(payload)) throw new Error('DNS failure source is invalid: ' + source);
        const candidate = {payload: payload, source: source, priority: index};
        resolved.push(candidate);
        return candidate;
      });
    });
    let first;
    try {
      first = await Promise.any(attempts);
    } catch (_) {
      throw new Error('No valid DNS failure aggregate is available');
    }
    const requested = Number(requestedTimeoutMs);
    const arbitrationMs = Number.isFinite(requested) && requested > 0
      ? Math.min(QUEUE_DNS_ARBITRATION_MS, requested)
      : QUEUE_DNS_ARBITRATION_MS;
    await Promise.race([
      Promise.allSettled(attempts),
      new Promise(function (resolve) { window.setTimeout(resolve, arbitrationMs); }),
    ]);
    const selected = resolved.slice().sort(compareQueueDnsCandidates)[0] || first;
    if (generation === queueDnsFetchGeneration) {
      queueDnsPreferredCandidate = selected;
      lastDnsRefreshAt = Date.now();
    }

    // A slower source may still carry a newer publication. Keep it bounded by
    // queueDnsWithTimeout, then upgrade the visible view only when it wins the
    // timestamp/priority comparison. Equal timestamps continue to prefer live.
    Promise.allSettled(attempts).then(function () {
      if (generation !== queueDnsFetchGeneration || !resolved.length) return;
      const newest = resolved.slice().sort(compareQueueDnsCandidates)[0];
      const current = queueDnsPreferredCandidate;
      if (!current || compareQueueDnsCandidates(newest, current) < 0) {
        queueDnsPreferredCandidate = newest;
        if (typeof document.querySelector === 'function'
          && activeTab() === 'ci-analytics'
          && state.analyticsView === 'dns') {
          render('ci-analytics', true);
        }
      }
    });
    return Object.assign({}, selected.payload, {__sourceAsset: selected.source});
  }

  function queueDnsWindow(payload, requestedWindow) {
    const windows = (payload || {}).windows || {};
    const publishedOptions = Array.isArray((payload || {}).window_options)
      ? payload.window_options.map(function (option) { return String(option && typeof option === 'object' ? option.id : option); })
      : [];
    const requested = QUEUE_DNS_WINDOW_IDS.includes(requestedWindow) && publishedOptions.includes(requestedWindow)
      ? requestedWindow
      : null;
    const publishedDefault = String((payload || {}).default_window || '');
    const fallback = QUEUE_DNS_WINDOW_IDS.includes(publishedDefault) && publishedOptions.includes(publishedDefault)
      ? publishedDefault
      : QUEUE_DNS_WINDOW_IDS.find(function (id) { return publishedOptions.includes(id) && windows[id]; });
    const id = requested && windows[requested] ? requested : fallback;
    return {id: id || requestedWindow, block: (id && windows[id]) || null};
  }

  function queueDnsCoverage(payload, windowBlock) {
    const globalCoverage = (payload || {}).coverage || {};
    const localCoverage = (windowBlock || {}).coverage || {};
    const hasLocalCoverage = Boolean(windowBlock && windowBlock.coverage
      && typeof windowBlock.coverage === 'object' && !Array.isArray(windowBlock.coverage));
    const selectedCoverage = hasLocalCoverage ? localCoverage : globalCoverage;
    const status = String(selectedCoverage.status || 'unknown').trim().toLowerCase();
    const explicitlyIncomplete = selectedCoverage.complete === false
      || selectedCoverage.discovery_complete === false
      || ['not_collected', 'partial', 'failed', 'error', 'incomplete', 'unavailable', 'unknown'].includes(status);
    const explicitlyComplete = selectedCoverage.complete === true
      || status === 'complete';
    const selectedWindowId = Object.keys((payload || {}).windows || {}).find(function (windowId) {
      return (payload.windows || {})[windowId] === windowBlock;
    });
    const publicationRows = ((((payload || {}).publication_retention || {}).window_rows) || {})[selectedWindowId] || {};
    const publicationIncomplete = publicationRows.complete === false;
    const complete = explicitlyComplete && !explicitlyIncomplete && !publicationIncomplete;
    const notes = [];
    [selectedCoverage].forEach(function (coverage) {
      ['reason', 'detail', 'limitation'].forEach(function (key) {
        if (coverage[key]) notes.push(String(coverage[key]));
      });
      ['problems', 'warnings', 'limitations'].forEach(function (key) {
        if (Array.isArray(coverage[key])) coverage[key].forEach(function (item) { if (item) notes.push(String(item)); });
      });
    });
    const numericFacts = [
      ['jobs scanned', selectedCoverage.scanned_jobs],
      ['eligible jobs', selectedCoverage.eligible_jobs],
      ['positive jobs', selectedCoverage.positive_jobs],
      ['jobs pending', selectedCoverage.pending_jobs],
      ['logs unavailable', selectedCoverage.unavailable_jobs],
      ['oversized logs', selectedCoverage.oversize_jobs],
      ['parse failures', selectedCoverage.parse_failures],
    ].filter(function (fact) { return fact[1] !== null && fact[1] !== undefined && fact[1] !== ''; });
    if (publicationIncomplete) {
      notes.push('public storage retained ' + integer(publicationRows.published) + ' of ' + integer(publicationRows.source) + ' aggregate rows for this window');
    }
    return {
      complete: complete,
      status: status || (complete ? 'complete' : 'unknown'),
      notes: Array.from(new Set(notes)),
      facts: numericFacts.map(function (fact) { return integer(fact[1]) + ' ' + fact[0]; }),
    };
  }

  function queueDnsFreshness(payload, windowBlock, nowMs) {
    const clock = Number.isFinite(Number(nowMs)) ? Number(nowMs) : Date.now();
    const generatedAtMs = queueTimestamp((payload || {}).generated_at);
    const windowEndMs = queueTimestamp((windowBlock || {}).end_exclusive);
    const generatedAgeMs = generatedAtMs === -Infinity ? Infinity : Math.max(0, clock - generatedAtMs);
    const windowAgeMs = windowEndMs === -Infinity ? Infinity : Math.max(0, clock - windowEndMs);
    return {
      stale: generatedAgeMs > QUEUE_DNS_STALE_MS || windowAgeMs > QUEUE_DNS_STALE_MS,
      generatedAgeMs: generatedAgeMs,
      windowAgeMs: windowAgeMs,
      thresholdMs: QUEUE_DNS_STALE_MS,
    };
  }

  function queueDnsScope(requestedScope) {
    return requestedScope === 'canonical' ? 'canonical' : 'amd';
  }

  function queueDnsMatchesPublishedScope(queue, requestedScope) {
    const name = String(queue || '').trim().toLowerCase();
    if (!/^amd_mi\d{3,4}(?:_|$)/i.test(name) || isRetiredQueue(name)) return false;
    return queueDnsScope(requestedScope) !== 'canonical' || isCanonicalAmdQueue(name);
  }

  function queueDnsNodeRows(windowBlock, requestedScope) {
    const grouped = new Map();
    ((windowBlock || {}).rows || []).forEach(function (raw) {
      const queue = String((raw || {}).queue || '').trim();
      if (!queue || !queueDnsMatchesPublishedScope(queue, requestedScope)) return;
      const nodeRaw = String((raw || {}).node || '').trim();
      const node = nodeRaw || '(unidentified)';
      const key = queue + '\u001f' + node;
      const outcomes = queueDnsOutcomeCounts(raw);
      const current = grouped.get(key) || {
        queue: queue,
        node: node,
        nodeRaw: nodeRaw,
        affectedJobs: 0,
        episodes: 0,
        huggingfaceAffectedJobs: 0,
        evidenceTotal: 0,
        passedJobs: 0,
        softFailedJobs: 0,
        hardFailedJobs: 0,
        outcomesAvailable: true,
      };
      current.affectedJobs += queueDnsCount(raw.affected_jobs);
      current.episodes += queueDnsCount(raw.episodes);
      current.huggingfaceAffectedJobs += queueDnsCount(raw.huggingface_affected_jobs);
      current.evidenceTotal += queueDnsCount(raw.evidence_total);
      current.passedJobs += outcomes.passed;
      current.softFailedJobs += outcomes.softFailed;
      current.hardFailedJobs += outcomes.hardFailed;
      current.outcomesAvailable = current.outcomesAvailable && outcomes.available;
      grouped.set(key, current);
    });
    return Array.from(grouped.values()).sort(function (left, right) {
      return right.affectedJobs - left.affectedJobs
        || right.episodes - left.episodes
        || compareText(left.node, right.node);
    });
  }

  function queueDnsQueueRows(windowBlock, requestedScope, queueRoster) {
    const byQueue = new Map();
    queueDnsNodeRows(windowBlock, requestedScope).forEach(function (node) {
      const row = byQueue.get(node.queue) || {
        queue: node.queue,
        affectedJobs: 0,
        episodes: 0,
        huggingfaceAffectedJobs: 0,
        evidenceTotal: 0,
        passedJobs: 0,
        softFailedJobs: 0,
        hardFailedJobs: 0,
        outcomesAvailable: true,
        nodes: [],
      };
      row.affectedJobs += node.affectedJobs;
      row.episodes += node.episodes;
      row.huggingfaceAffectedJobs += node.huggingfaceAffectedJobs;
      row.evidenceTotal += node.evidenceTotal;
      row.passedJobs += node.passedJobs;
      row.softFailedJobs += node.softFailedJobs;
      row.hardFailedJobs += node.hardFailedJobs;
      row.outcomesAvailable = row.outcomesAvailable && node.outcomesAvailable;
      row.nodes.push(node);
      byQueue.set(node.queue, row);
    });
    (queueRoster || []).forEach(function (queue) {
      const name = String(queue || '').trim();
      if (name && queueDnsMatchesPublishedScope(name, requestedScope) && !byQueue.has(name)) {
        byQueue.set(name, {queue: name, affectedJobs: 0, episodes: 0, huggingfaceAffectedJobs: 0, evidenceTotal: 0, passedJobs: 0, softFailedJobs: 0, hardFailedJobs: 0, outcomesAvailable: false, nodes: []});
      }
    });
    return Array.from(byQueue.values()).sort(function (left, right) {
      return right.affectedJobs - left.affectedJobs || compareText(left.queue, right.queue);
    });
  }

  function queueDnsEvidenceUrl(row) {
    const pipeline = String((row || {}).pipeline || '');
    const build = String((row || {}).build_number || '');
    const jobId = String((row || {}).job_id || '');
    if (!QUEUE_DNS_PIPELINES.has(pipeline)) return '';
    if (!/^[1-9]\d*$/.test(build) || !Number.isSafeInteger(Number(build))) return '';
    if (!QUEUE_DNS_JOB_ID_RE.test(jobId)) return '';
    return 'https://buildkite.com/vllm/' + encodeURIComponent(pipeline)
      + '/builds/' + encodeURIComponent(build)
      + '/list?jid=' + encodeURIComponent(jobId) + '&tab=output';
  }

  function queueDnsEvidenceMetricValid(metric, windowBlock) {
    if (!metric || typeof metric !== 'object' || Array.isArray(metric)) return false;
    const keys = Object.keys(metric);
    if (keys.length !== QUEUE_DNS_WINDOW_METRIC_KEYS.length
      || !QUEUE_DNS_WINDOW_METRIC_KEYS.every(function (key) { return keys.includes(key); })) return false;
    if (!QUEUE_DNS_UTC_SECOND_RE.test(String(metric.first_at || ''))
      || !QUEUE_DNS_UTC_SECOND_RE.test(String(metric.last_at || ''))) return false;
    const firstAt = queueTimestamp(metric.first_at);
    const lastAt = queueTimestamp(metric.last_at);
    const windowStart = queueTimestamp((windowBlock || {}).start);
    const windowEnd = queueTimestamp((windowBlock || {}).end_exclusive);
    if (firstAt === -Infinity || lastAt === -Infinity || windowStart === -Infinity || windowEnd === -Infinity
      || firstAt > lastAt || firstAt < windowStart || lastAt >= windowEnd) return false;
    if (!Number.isInteger(metric.episodes) || metric.episodes < 1
      || !Number.isInteger(metric.match_count) || metric.match_count < metric.episodes) return false;
    return ['signature_ids', 'target_categories'].every(function (key) {
      const values = metric[key];
      return Array.isArray(values) && values.length > 0
        && values.every(function (item) { return typeof item === 'string' && Boolean(item); })
        && new Set(values).size === values.length;
    });
  }

  function queueDnsEvidenceItemValid(row, windows) {
    if (!row || typeof row !== 'object' || Array.isArray(row)) return false;
    if (!Array.isArray(row.window_ids) || !row.window_ids.length) return false;
    const windowIds = row.window_ids.map(String);
    const canonicalIds = QUEUE_DNS_WINDOW_IDS.filter(function (id) { return windowIds.includes(id); });
    if (JSON.stringify(windowIds) !== JSON.stringify(canonicalIds) || !windowIds.includes('720h')) return false;
    if (!row.window_metrics || typeof row.window_metrics !== 'object' || Array.isArray(row.window_metrics)
      || JSON.stringify(Object.keys(row.window_metrics)) !== JSON.stringify(windowIds)) return false;
    if (!windowIds.every(function (id) {
      return queueDnsEvidenceMetricValid(row.window_metrics[id], (windows || {})[id]);
    })) return false;
    const retained = row.window_metrics['720h'];
    return QUEUE_DNS_WINDOW_METRIC_KEYS.every(function (key) {
      return Array.isArray(retained[key])
        ? JSON.stringify(row[key]) === JSON.stringify(retained[key])
        : row[key] === retained[key];
    });
  }

  function queueDnsEvidenceWindowRow(row, windowId, windows) {
    if (!queueDnsEvidenceItemValid(row, windows) || !row.window_ids.includes(windowId)) return null;
    const metric = row.window_metrics[windowId];
    return {
      id: row.id,
      first_at: metric.first_at,
      last_at: metric.last_at,
      time_basis: row.time_basis,
      pipeline: row.pipeline,
      queue: row.queue,
      node: row.node,
      hardware: row.hardware,
      build_number: row.build_number,
      job_id: row.job_id,
      state: row.state,
      episodes: metric.episodes,
      match_count: metric.match_count,
      signature_ids: metric.signature_ids.slice(),
      target_categories: metric.target_categories.slice(),
      window_id: windowId,
    };
  }

  function queueDnsEvidenceForNode(payload, windowId, queue, nodeRaw) {
    const deduplicated = new Map();
    ((((payload || {}).evidence || {}).items) || []).forEach(function (row) {
      if (!row || row.queue !== queue) return;
      if (String(row.node || '').trim() !== String(nodeRaw || '').trim()) return;
      const selected = queueDnsEvidenceWindowRow(row, windowId, (payload || {}).windows || {});
      if (!selected) return;
      const key = String(selected.id || [selected.pipeline, selected.build_number, selected.job_id].join('/'));
      deduplicated.set(key, selected);
    });
    return Array.from(deduplicated.values()).sort(function (left, right) {
      return queueTimestamp(right.last_at || right.first_at) - queueTimestamp(left.last_at || left.first_at);
    });
  }

  function queueDnsNodeOutcomes(payload, windowId, nodeRow) {
    if (payload.outcome_contract === QUEUE_DNS_OUTCOME_CONTRACT && nodeRow.outcomesAvailable) {
      return {
        available: true,
        passed: nodeRow.passedJobs,
        softFailed: nodeRow.softFailedJobs,
        hardFailed: nodeRow.hardFailedJobs,
      };
    }
    const retained = queueDnsEvidenceForNode(payload, windowId, nodeRow.queue, nodeRow.nodeRaw);
    if (retained.length !== nodeRow.affectedJobs || retained.length < nodeRow.evidenceTotal) {
      return {available: false, passed: 0, softFailed: 0, hardFailed: 0};
    }
    const counts = {available: true, passed: 0, softFailed: 0, hardFailed: 0};
    retained.forEach(function (row) {
      const stateName = String(row.state || '').toLowerCase();
      if (stateName === 'passed') counts.passed += 1;
      else if (stateName === 'soft' || stateName === 'soft_failed' || stateName === 'soft_fail') counts.softFailed += 1;
      else if (stateName === 'hard' || stateName === 'failed') counts.hardFailed += 1;
      else counts.available = false;
    });
    if (counts.passed + counts.softFailed + counts.hardFailed !== nodeRow.affectedJobs) counts.available = false;
    return counts;
  }

  function queueDnsDisplayCount(raw, coverage) {
    const count = queueDnsCount(raw);
    if ((coverage || {}).complete) return integer(count);
    return count > 0 ? '\u2265 ' + integer(count) : '-';
  }

  function queueDnsTargetLabels(row) {
    // cspell:ignore pypi
    const labels = {
      huggingface_hub: 'Hugging Face Hub',
      vllm_public_assets: 'vLLM public assets',
      aws_s3: 'AWS S3',
      github: 'GitHub',
      pypi: 'PyPI',
      other_public: 'Other public host',
      unknown: 'Unknown target',
    };
    const values = Array.isArray((row || {}).target_categories) ? row.target_categories : [];
    return values.map(function (category) { return labels[String(category)] || labels.unknown; })
      .filter(function (label, index, all) { return all.indexOf(label) === index; });
  }

  function queueDnsSignatureLabels(row) {
    return (Array.isArray((row || {}).signature_ids) ? row.signature_ids : [])
      .map(function (signature) { return String(signature || '').replace(/_/g, ' '); })
      .filter(Boolean);
  }

  function queueDnsOutcomePresentation(state) {
    const normalized = String(state || '').toLowerCase();
    if (normalized === 'passed') return {label: 'Passed after observation', tone: 'is-success'};
    if (normalized === 'soft' || normalized === 'soft_failed' || normalized === 'soft_fail') {
      return {label: 'Soft-failed', tone: 'is-warning'};
    }
    if (normalized === 'hard' || normalized === 'failed') return {label: 'Hard-failed', tone: 'is-danger'};
    return {label: value(state, 'Unknown'), tone: 'is-neutral'};
  }

  function openQueueDnsNodeEvidence(payload, windowId, queueRow, nodeRow, coverage) {
    const rows = queueDnsEvidenceForNode(payload, windowId, queueRow.queue, nodeRow.nodeRaw).filter(function (row) {
      return Boolean(queueDnsEvidenceUrl(row));
    });
    const evidenceTotal = Math.max(nodeRow.evidenceTotal, rows.length);
    const truncated = rows.length < evidenceTotal;
    const content = n('div', 'ops-dns-evidence');
    const interpretation = n('div', 'ops-evidence-note is-info');
    add(interpretation, [
      n('strong', '', 'DNS observation is not the job outcome. '),
      n('span', '', 'Passed means the final Buildkite job outcome was passed after a resolver signature was observed. Soft- and hard-failed outcomes are shown separately; none establishes DNS as the cause.'),
    ]);
    content.append(interpretation);
    if (truncated) {
      content.append(n('div', 'ops-evidence-note is-warning', 'Exact links are retained for ' + integer(rows.length) + ' of ' + integer(evidenceTotal) + ' affected jobs on this node. The histogram continues to use the published affected-job row count, independent of bounded evidence retention.'));
    } else if (!rows.length && nodeRow.affectedJobs) {
      content.append(n('div', 'ops-evidence-note is-info', 'The aggregate contains affected jobs for this node, but no exact log links were retained in the bounded public evidence set.'));
    }
    const columns = [
      {label: 'Job outcome', sticky: true, width: '160px', render: function (row) { const url = queueDnsEvidenceUrl(row); const outcome = queueDnsOutcomePresentation(row.state); return linkedBadge(outcome.label, url, null, outcome.tone); }},
      {label: 'Evidence', width: '150px', render: function (row) { const url = queueDnsEvidenceUrl(row); return url ? externalLink('Open exact log', url) : n('span', 'ops-cell-muted', 'Exact link unavailable'); }},
      {label: 'Observed', width: '170px', render: function (row) { return shortDate(row.first_at) + (row.last_at && row.last_at !== row.first_at ? ' \u2192 ' + shortDate(row.last_at) : ''); }},
      {label: 'Time basis', width: '140px', render: function (row) { return value(String(row.time_basis || '').replace(/_/g, ' ')); }},
      {label: 'Hardware', width: '90px', render: function (row) { return value(row.hardware); }},
      {label: 'Build', width: '120px', render: function (row) { return externalLink(value(row.pipeline) + ' #' + value(row.build_number), queueDnsEvidenceUrl(row), 'ops-mono'); }},
      {label: 'Episodes', numeric: true, width: '90px', render: function (row) { return integer(row.episodes); }},
      {label: 'Raw matches', numeric: true, width: '110px', render: function (row) { return integer(row.match_count); }},
      {label: 'Targets', width: '180px', render: function (row) { return value(queueDnsTargetLabels(row).join(', ')); }},
      {label: 'DNS signatures', width: '220px', render: function (row) { return value(queueDnsSignatureLabels(row).join(', ')); }},
    ];
    content.append(compactTablePanel('Exact Buildkite log evidence', integer(rows.length) + ' shown / ' + integer(evidenceTotal) + ' total' + (truncated ? ' - truncated' : ''), columns, rows, {
      id: 'queue-dns-node-evidence-browser',
      limit: 30,
      browserSubtitle: queueRow.queue + ' on ' + nodeRow.node + ' in ' + windowId,
      searchPlaceholder: 'Filter job, build, target, or signature',
      searchText: function (row) { return [row.pipeline, row.build_number, row.job_id, row.state, row.hardware, row.time_basis, queueDnsTargetLabels(row).join(' '), queueDnsSignatureLabels(row).join(' ')].join(' '); },
      geometry: {name: 'queue-dns-evidence', minWidth: '1510px'},
    }));
    const detailKey = ['queue-dns', queueRow.queue, nodeRow.node, windowId].join('-').toLowerCase().replace(/[^a-z0-9-]+/g, '-');
    openDetailDrawer({
      id: detailKey,
      title: nodeRow.node,
      subtitle: queueRow.queue + ' DNS evidence - ' + windowId,
      description: 'Each row is one distinct Buildkite job attempt with a DNS-specific resolver signature. Repeated lines within an attempt do not inflate the count. A passing job is an observation in a job that ultimately passed, not an incident.',
      fields: [
        {label: 'Queue', value: queueRow.queue},
        {label: 'Physical node', value: nodeRow.node},
        {label: 'Affected jobs', value: queueDnsDisplayCount(nodeRow.affectedJobs, coverage)},
        {label: 'DNS episodes', value: queueDnsDisplayCount(nodeRow.episodes, coverage)},
        {label: 'Hugging Face affected jobs', value: queueDnsDisplayCount(nodeRow.huggingfaceAffectedJobs, coverage)},
        {label: 'Passed after observation', value: nodeRow.outcomesAvailable ? queueDnsDisplayCount(nodeRow.passedJobs, coverage) : null},
        {label: 'Soft-failed after observation', value: nodeRow.outcomesAvailable ? queueDnsDisplayCount(nodeRow.softFailedJobs, coverage) : null},
        {label: 'Hard-failed after observation', value: nodeRow.outcomesAvailable ? queueDnsDisplayCount(nodeRow.hardFailedJobs, coverage) : null},
        {label: 'Exact evidence shown', value: integer(rows.length)},
        {label: 'Exact evidence total', value: integer(evidenceTotal)},
        {label: 'Evidence retention truncated', value: truncated ? 'Yes' : 'No'},
      ],
      sources: [{label: 'Open published DNS observations', url: payload.__sourceAsset || SOURCE_ASSETS.queueDnsFallback}],
      content: content,
    });
  }

  function queueDnsSummaryItem(label, renderedValue, meta, tone) {
    const item = n('div', 'ops-dns-summary-item ' + (tone || ''));
    add(item, [
      n('span', 'ops-dns-summary-label', label),
      n('strong', 'ops-dns-summary-value', renderedValue),
      meta ? n('span', 'ops-dns-summary-meta', meta) : null,
    ]);
    return item;
  }

  function queueDnsNodeBar(payload, windowId, queueRow, nodeRow, coverage, maximum) {
    const outcomes = queueDnsNodeOutcomes(payload, windowId, nodeRow);
    const control = n('button', 'ops-dns-node-bar');
    control.type = 'button';
    const renderedCount = queueDnsDisplayCount(nodeRow.affectedJobs, coverage);
    const nonpassing = outcomes.softFailed + outcomes.hardFailed;
    const outcomeText = outcomes.available
      ? queueDnsDisplayCount(outcomes.passed, coverage) + ' passed, '
        + queueDnsDisplayCount(nonpassing, coverage) + ' nonpassing'
      : 'job outcomes unavailable';
    control.setAttribute('aria-label', queueRow.queue + ', ' + nodeRow.node + ': ' + renderedCount
      + ' jobs with DNS observations; ' + outcomeText + '. Open exact Buildkite evidence.');
    control.addEventListener('click', function () {
      openQueueDnsNodeEvidence(payload, windowId, queueRow, Object.assign({}, nodeRow, {
        outcomesAvailable: outcomes.available,
        passedJobs: outcomes.passed,
        softFailedJobs: outcomes.softFailed,
        hardFailedJobs: outcomes.hardFailed,
      }), coverage);
    });

    const identity = n('span', 'ops-dns-node-identity');
    add(identity, [
      n('span', 'ops-dns-node-name ops-mono', nodeRow.node),
      n('span', 'ops-dns-node-meta', outcomeText + ' - ' + queueDnsDisplayCount(nodeRow.episodes, coverage) + ' episodes'),
    ]);
    const track = n('span', 'ops-dns-bar-track');
    const fill = n('span', 'ops-dns-bar-fill');
    fill.style.width = Math.max(3, nodeRow.affectedJobs / Math.max(1, maximum) * 100) + '%';
    if (outcomes.available && nodeRow.affectedJobs) {
      [
        ['is-passed', outcomes.passed],
        ['is-soft', outcomes.softFailed],
        ['is-hard', outcomes.hardFailed],
      ].forEach(function (entry) {
        if (!entry[1]) return;
        const segment = n('span', 'ops-dns-bar-segment ' + entry[0]);
        segment.style.width = entry[1] / nodeRow.affectedJobs * 100 + '%';
        fill.append(segment);
      });
    } else {
      fill.append(n('span', 'ops-dns-bar-segment is-observed'));
    }
    track.append(fill);
    add(control, [identity, track, n('strong', 'ops-dns-node-count', renderedCount)]);
    return control;
  }

  function renderQueueDnsNativeHistogram(payload, windowId, queueRow, coverage, maximum) {
    const card = n('article', 'ops-dns-queue-card');
    const header = n('header', 'ops-dns-queue-card-header');
    const stats = n('div', 'ops-dns-queue-card-stats');
    add(stats, [
      n('span', '', queueDnsDisplayCount(queueRow.affectedJobs, coverage) + ' jobs'),
      n('span', '', queueDnsDisplayCount(queueRow.nodes.length, coverage) + ' nodes'),
      n('span', '', queueDnsDisplayCount(queueRow.huggingfaceAffectedJobs, coverage) + ' HF'),
    ]);
    add(header, [n('h3', 'ops-dns-queue-card-title ops-mono', queueRow.queue), stats]);
    card.append(header);
    const bars = n('div', 'ops-dns-node-bars');
    queueRow.nodes.forEach(function (nodeRow) {
      bars.append(queueDnsNodeBar(payload, windowId, queueRow, nodeRow, coverage, maximum));
    });
    card.append(bars);
    return card;
  }

  function renderAnalyticsDns(host, payload) {
    const selected = queueDnsWindow(payload, state.analyticsDnsWindow);
    if (!selected.block) {
      host.append(n('div', 'ops-error', 'The selected DNS observation window is not present in the published aggregate.'));
      return;
    }
    if (selected.id !== state.analyticsDnsWindow) {
      state.analyticsDnsWindow = selected.id;
      setQueryValue('analytics_dns_window', selected.id);
    }
    const coverage = queueDnsCoverage(payload, selected.block);
    const freshness = queueDnsFreshness(payload, selected.block);
    const dnsScope = queueDnsScope(state.analyticsDnsScope);
    const affectedQueues = queueDnsQueueRows(selected.block, dnsScope, []).filter(function (row) {
      return row.affectedJobs > 0;
    });
    const affectedNodes = new Set();
    affectedQueues.forEach(function (queue) {
      queue.passedJobs = 0;
      queue.softFailedJobs = 0;
      queue.hardFailedJobs = 0;
      queue.outcomesAvailable = true;
      queue.nodes.forEach(function (node) {
        if (node.affectedJobs > 0) affectedNodes.add(node.node);
        const outcomes = queueDnsNodeOutcomes(payload, selected.id, node);
        node.outcomesAvailable = outcomes.available;
        node.passedJobs = outcomes.passed;
        node.softFailedJobs = outcomes.softFailed;
        node.hardFailedJobs = outcomes.hardFailed;
        queue.passedJobs += outcomes.passed;
        queue.softFailedJobs += outcomes.softFailed;
        queue.hardFailedJobs += outcomes.hardFailed;
        queue.outcomesAvailable = queue.outcomesAvailable && outcomes.available;
      });
    });
    const totals = affectedQueues.reduce(function (out, queue) {
      out.affectedJobs += queue.affectedJobs;
      out.episodes += queue.episodes;
      out.huggingfaceAffectedJobs += queue.huggingfaceAffectedJobs;
      out.passedJobs += queue.passedJobs;
      out.softFailedJobs += queue.softFailedJobs;
      out.hardFailedJobs += queue.hardFailedJobs;
      out.outcomesAvailable = out.outcomesAvailable && queue.outcomesAvailable;
      return out;
    }, {affectedJobs: 0, episodes: 0, huggingfaceAffectedJobs: 0, passedJobs: 0, softFailedJobs: 0, hardFailedJobs: 0, outcomesAvailable: true});

    const toolbar = n('div', 'ops-toolbar ops-dns-toolbar');
    const scopeControl = segmented([
      {id: 'canonical', label: 'Canonical AMD (12)'},
      {id: 'amd', label: 'All active AMD GPU'},
    ], dnsScope, function (id) {
      setRouteState('ci-analytics', 'analyticsDnsScope', id, 'analytics_dns_scope');
    }, 'DNS queue scope');
    const scopeHelp = n('p', 'ops-dns-scope-help');
    scopeHelp.id = 'ops-dns-scope-help';
    scopeHelp.textContent = 'Canonical AMD is the 12 standard MI250, MI300, and MI355 queues at widths 1, 2, 4, and 8. All active AMD GPU also includes other amd_mi* models and widths, such as MI325; retired MI355B queues are excluded.';
    scopeControl.setAttribute('aria-describedby', scopeHelp.id);
    const windowField = n('label', 'ops-dns-window-field');
    const windowSelect = n('select', 'ops-select ops-dns-window-select');
    windowSelect.setAttribute('aria-label', 'DNS observation window');
    QUEUE_DNS_WINDOW_OPTIONS.forEach(function (option) {
      const item = n('option', '', option.label);
      item.value = option.id;
      item.selected = option.id === selected.id;
      windowSelect.append(item);
    });
    windowSelect.addEventListener('change', function () {
      setRouteState('ci-analytics', 'analyticsDnsWindow', windowSelect.value, 'analytics_dns_window');
    });
    add(windowField, [n('span', 'ops-field-label', 'Window'), windowSelect]);
    const freshnessBadge = n('span', 'ops-badge ' + (freshness.stale ? 'is-warning' : 'is-success'),
      (freshness.stale ? 'Stale - ' : 'Updated ') + age(selected.block.end_exclusive));
    add(toolbar, [scopeControl, windowField, n('span', 'ops-toolbar-spacer'), freshnessBadge]);
    host.append(toolbar, scopeHelp);

    if (freshness.stale) {
      const selectedOption = QUEUE_DNS_WINDOW_OPTIONS.find(function (option) { return option.id === selected.id; });
      const warning = n('div', 'ops-evidence-note ops-dns-stale-warning');
      warning.setAttribute('role', 'alert');
      add(warning, [
        n('strong', '', 'DNS observations are stale. '),
        n('span', '', value(selectedOption && selectedOption.label, selected.id) + ' ended ' + shortDate(selected.block.end_exclusive) + '. Treat these as historical observations, not the current window.'),
      ]);
      host.append(warning);
    }

    if (!coverage.complete) {
      const warning = n('div', 'ops-evidence-note is-warning ops-dns-coverage-note');
      warning.setAttribute('role', 'status');
      add(warning, [
        n('strong', '', 'Partial coverage - counts are lower bounds. '),
        n('span', '', (coverage.facts.length ? coverage.facts.join(' - ') + '. ' : '') + 'Missing observations never mean zero.'),
      ]);
      host.append(warning);
    }
    const nonpassing = totals.softFailedJobs + totals.hardFailedJobs;
    const outcomeTone = totals.hardFailedJobs
      ? 'is-danger'
      : totals.softFailedJobs
        ? 'is-warning'
        : totals.passedJobs
          ? 'is-success'
          : '';
    const summary = n('section', 'ops-dns-summary');
    summary.setAttribute('aria-label', 'DNS observation summary');
    add(summary, [
      queueDnsSummaryItem('JOBS WITH DNS OBSERVATIONS', queueDnsDisplayCount(totals.affectedJobs, coverage), queueDnsDisplayCount(totals.episodes, coverage) + ' episodes', totals.affectedJobs ? 'is-warning' : 'is-success'),
      queueDnsSummaryItem('AFFECTED QUEUES', queueDnsDisplayCount(affectedQueues.length, coverage), dnsScope === 'canonical' ? 'canonical AMD' : 'active AMD GPU'),
      queueDnsSummaryItem('PHYSICAL NODES', queueDnsDisplayCount(affectedNodes.size, coverage), 'including unidentified'),
      queueDnsSummaryItem('HUGGING FACE JOBS', queueDnsDisplayCount(totals.huggingfaceAffectedJobs, coverage), 'resolver target'),
      queueDnsSummaryItem('PASSED / NONPASSING', totals.outcomesAvailable
        ? queueDnsDisplayCount(totals.passedJobs, coverage) + ' / ' + queueDnsDisplayCount(nonpassing, coverage)
        : '-', totals.outcomesAvailable ? 'final outcome after observation' : 'outcome aggregate unavailable', outcomeTone),
    ]);
    host.append(summary);

    const legend = n('div', 'ops-dns-outcome-legend');
    legend.setAttribute('aria-label', 'Job outcome legend');
    [
      ['is-passed', 'Passed after observation'],
      ['is-soft', 'Soft-failed after observation'],
      ['is-hard', 'Hard-failed after observation'],
    ].forEach(function (entry) {
      const item = n('span', 'ops-dns-legend-item');
      add(item, [n('span', 'ops-dns-legend-swatch ' + entry[0]), n('span', '', entry[1])]);
      legend.append(item);
    });
    legend.append(n('span', 'ops-dns-legend-note', 'Outcome is correlation, not proof DNS caused the result.'));
    host.append(legend);

    if (!affectedQueues.length) {
      host.append(n('div', 'ops-empty', coverage.complete
        ? 'No jobs with DNS resolver observations were found in this scope and window.'
        : 'No retained DNS observations are available in this scope. Partial coverage cannot establish a zero.'));
    } else {
      const section = n('section', 'ops-dns-section');
      const heading = n('header', 'ops-section-header');
      add(heading, [add(n('div', 'ops-section-heading'), [
        n('h2', 'ops-section-title', 'DNS observations by queue and physical node'),
        n('p', 'ops-section-description', 'Affected queues only. Select any node bar to open the exact retained Buildkite logs in the right-side drawer.'),
      ])]);
      section.append(heading);
      const maximum = Math.max.apply(null, affectedQueues.flatMap(function (queue) {
        return queue.nodes.map(function (node) { return node.affectedJobs; });
      }).concat([1]));
      const grid = n('div', 'ops-dns-queue-grid');
      affectedQueues.forEach(function (queueRow) {
        grid.append(renderQueueDnsNativeHistogram(payload, selected.id, queueRow, coverage, maximum));
      });
      section.append(grid);
      host.append(section);
    }

    const method = n('details', 'ops-dns-method');
    const methodSummary = n('summary', 'ops-dns-method-summary', 'Counting method and data provenance');
    const methodBody = n('div', 'ops-dns-method-body');
    add(methodBody, [
      n('p', '', 'One job attempt counts once when its complete log contains a DNS-specific resolver signature. Repeated matching lines collapse into episodes; retries remain distinct attempts. Generic connection, TLS, timeout, and HTTP failures do not count.'),
      n('p', '', 'Selected window: ' + shortDate(selected.block.start) + ' to ' + shortDate(selected.block.end_exclusive)
        + '. Schema v' + value(payload.schema_version) + ', generated ' + shortDate(payload.generated_at)
        + ', source: ' + (payload.__sourceAsset === SOURCE_ASSETS.queueDns ? 'live dns-health-data' : 'Pages fallback') + '.'),
      coverage.notes.length ? n('p', '', coverage.notes.join('; ') + '.') : null,
      sourceActions([
        {label: 'Open selected DNS data', url: payload.__sourceAsset || SOURCE_ASSETS.queueDnsFallback},
        {label: 'Open Pages DNS fallback', url: SOURCE_ASSETS.queueDnsFallback},
      ]),
    ]);
    method.append(methodSummary, methodBody);
    host.append(method);
  }

  function percentileValue(values, percentile) {
    const sorted = values.filter(function (item) { return Number.isFinite(Number(item)); }).map(Number).sort(function (a, b) { return a - b; });
    if (!sorted.length) return null;
    return sorted[Math.ceil((sorted.length - 1) * percentile)];
  }

  function observationTimestamp(observation) {
    return observation.observed_at || observation.finished_at || observation.created_at || observation.date || null;
  }

  const OMNI_REPOSITORIES = {omni: 'vllm-project/vllm-omni', main: 'vllm-project/vllm'};
  const OMNI_MAPPING_WINDOWS = [
    {id: '6h', label: '6 hours', shortLabel: '6h', hours: 6, hourlyBin: 1},
    {id: '1d', label: '1 day', shortLabel: '1d', hours: 24, hourlyBin: 1},
    {id: '3d', label: '3 days', shortLabel: '3d', hours: 72, hourlyBin: 3},
    {id: '7d', label: '7 days', shortLabel: '7d', hours: 168, hourlyBin: 6},
    {id: '1m', label: '1 month', shortLabel: '1m', hours: 24 * 30, hourlyBin: 24},
    {id: '3m', label: '3 months', shortLabel: '3m', hours: 24 * 90, hourlyBin: 24},
  ];
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

  const OMNI_MAPPING_NUMBER_FIELDS = [
    'mapped_jobs', 'started_jobs', 'finished_jobs', 'mapped_gpu_slots', 'gpu_hours',
  ];

  function emptyOmniMappingStats() {
    return {
      mapped_jobs: 0,
      started_jobs: 0,
      finished_jobs: 0,
      mapped_gpu_slots: 0,
      gpu_hours: 0,
      by_queue: {},
      by_pipeline: {},
    };
  }

  function addOmniMappingBreakdown(target, source) {
    Object.entries(source || {}).forEach(function (entry) {
      const name = entry[0];
      const stats = entry[1] || {};
      if (!target[name]) {
        target[name] = {
          mapped_jobs: 0,
          started_jobs: 0,
          finished_jobs: 0,
          mapped_gpu_slots: 0,
          gpu_hours: 0,
        };
      }
      OMNI_MAPPING_NUMBER_FIELDS.forEach(function (field) {
        target[name][field] += Number(stats[field] || 0);
      });
    });
  }

  function addOmniMappingStats(target, source) {
    const stats = source || {};
    OMNI_MAPPING_NUMBER_FIELDS.forEach(function (field) {
      target[field] += Number(stats[field] || 0);
    });
    addOmniMappingBreakdown(target.by_queue, stats.by_queue);
    addOmniMappingBreakdown(target.by_pipeline, stats.by_pipeline);
    return target;
  }

  function omniMappingTotals(rows) {
    const totals = {
      omni: emptyOmniMappingStats(),
      main: emptyOmniMappingStats(),
    };
    (rows || []).forEach(function (row) {
      const workloads = row.workloads || {};
      addOmniMappingStats(totals.omni, workloads.omni);
      addOmniMappingStats(totals.main, workloads.main);
    });
    return totals;
  }

  function omniMappingRowStart(row, resolution) {
    const raw = resolution === 'hourly'
      ? (row.hour || row.start || row.bucket_start || row.ts)
      : (row.date ? row.date + 'T00:00:00Z' : row.start || row.ts);
    const parsed = new Date(raw || '').getTime();
    return Number.isFinite(parsed) ? parsed : null;
  }

  function omniMappingRowEnd(row, resolution) {
    const explicit = new Date(row.end_exclusive || row.end || '').getTime();
    if (Number.isFinite(explicit)) return explicit;
    const start = omniMappingRowStart(row, resolution);
    if (start === null) return null;
    return start + (resolution === 'hourly' ? 60 * 60 * 1000 : 24 * 60 * 60 * 1000);
  }

  function omniMappingPopulationBoundary(mapping, resolution) {
    mapping = mapping || {};
    const windowScope = mapping.window || {};
    const attribution = (mapping.scope || {}).attribution || {};
    const query = mapping.query || {};
    const resolutionCoverage = ((mapping.coverage || {})[resolution]) || {};
    const exhaustiveValues = [
      windowScope.job_created_range_exhaustive,
      resolutionCoverage.job_created_range_exhaustive,
      attribution.job_created_range_exhaustive,
      query.job_created_range_exhaustive,
    ];
    const exhaustiveValue = exhaustiveValues.find(function (value) {
      return value === true || value === false;
    });
    const lookback = Number(
      attribution.parent_build_lookback_days === undefined
        ? query.parent_build_lookback_days
        : attribution.parent_build_lookback_days
    );
    return {
      jobCreatedRangeExhaustive: exhaustiveValue === undefined ? null : exhaustiveValue,
      parentBuildLookbackDays: Number.isFinite(lookback) && lookback > 0 ? lookback : null,
      sourceWindowExact: attribution.exact_within_declared_source_window === true,
      limitation: attribution.limitation || (
        exhaustiveValue === false
          ? 'Jobs added after the configured lookback to older parent builds can be absent from these aggregates.'
          : ''
      ),
    };
  }

  function omniMappingWindow(mapping, rangeId) {
    const selected = OMNI_MAPPING_WINDOWS.find(function (item) { return item.id === rangeId; })
      || OMNI_MAPPING_WINDOWS[3];
    const hourly = Array.isArray((mapping || {}).hourly) ? mapping.hourly : [];
    const daily = Array.isArray((mapping || {}).daily) ? mapping.daily : [];
    let resolution = selected.hours <= 168 && hourly.length ? 'hourly' : 'daily';
    if (selected.hours < 72 && !hourly.length) {
      return Object.assign({
        selected: selected,
        available: false,
        resolution: 'unavailable',
        rows: [],
        buckets: [],
        complete: false,
        apiCollectionComplete: false,
        lowerBound: false,
        hasOpenBucket: false,
        reason: 'Hourly mapping history is not available yet. Daily totals cannot answer a trailing ' + selected.label + ' question.',
      }, omniMappingPopulationBoundary(mapping, 'hourly'));
    }
    if (!daily.length && !hourly.length) {
      return Object.assign({
        selected: selected,
        available: false,
        resolution: 'unavailable',
        rows: [],
        buckets: [],
        complete: false,
        apiCollectionComplete: false,
        lowerBound: true,
        hasOpenBucket: false,
        reason: 'No unique-job mapping history has been collected yet.',
      }, omniMappingPopulationBoundary(mapping, resolution));
    }
    if (resolution === 'daily' && !daily.length) resolution = 'hourly';
    const declaredCoverage = ((((mapping || {}).coverage || {})[resolution]) || {});
    const source = (resolution === 'hourly' ? hourly : daily).map(function (row) {
      return {
        row: row,
        start: omniMappingRowStart(row, resolution),
        end: omniMappingRowEnd(row, resolution),
      };
    }).filter(function (item) {
      return item.start !== null && item.end !== null;
    }).sort(function (left, right) {
      return left.start - right.start;
    });
    if (!source.length) {
      return Object.assign({
        selected: selected,
        available: false,
        resolution: resolution,
        rows: [],
        buckets: [],
        complete: false,
        apiCollectionComplete: false,
        lowerBound: true,
        hasOpenBucket: false,
        reason: 'The retained mapping buckets do not contain valid UTC timestamps.',
      }, omniMappingPopulationBoundary(mapping, resolution));
    }
    const generated = new Date((mapping || {}).generated_at || '').getTime();
    const latestEnd = source[source.length - 1].end;
    const anchor = Number.isFinite(generated) ? Math.min(generated, latestEnd) : latestEnd;
    let selectedRows;
    let expectedBuckets;
    let bucketMs;
    if (resolution === 'hourly') {
      expectedBuckets = selected.hours;
      bucketMs = 60 * 60 * 1000;
      const eligible = source.filter(function (item) {
        return item.start <= anchor;
      });
      const latestStart = eligible.length ? eligible[eligible.length - 1].start : null;
      const earliestStart = latestStart === null ? null : latestStart - (expectedBuckets - 1) * bucketMs;
      selectedRows = eligible.filter(function (item) {
        return earliestStart !== null && item.start >= earliestStart;
      });
    } else {
      expectedBuckets = Math.ceil(selected.hours / 24);
      bucketMs = 24 * 60 * 60 * 1000;
      const anchorDay = new Date(anchor).toISOString().slice(0, 10);
      const eligible = source.filter(function (item) {
        return item.row.date <= anchorDay;
      });
      const latestStart = eligible.length ? eligible[eligible.length - 1].start : null;
      const earliestStart = latestStart === null ? null : latestStart - (expectedBuckets - 1) * bucketMs;
      selectedRows = eligible.filter(function (item) {
        return earliestStart !== null && item.start >= earliestStart;
      });
    }
    const rows = selectedRows.map(function (item) { return item.row; });
    const lowerBound = rows.some(function (row) {
      return row.lower_bound === true || row.collection_complete === false;
    });
    const hasOpenBucket = rows.some(function (row) {
      return row.state === 'open' || row.open === true;
    });
    const selectedContiguous = selectedRows.every(function (item, index) {
      return !index || item.start - selectedRows[index - 1].start === bucketMs;
    });
    const coverageComplete = rows.length === expectedBuckets && selectedContiguous;
    const retainedComplete = Boolean(rows.length && coverageComplete && !lowerBound);
    const complete = retainedComplete && !hasOpenBucket;
    const coverageStatus = lowerBound
      ? 'lower_bound'
      : !coverageComplete
        ? 'partial'
        : hasOpenBucket
          ? 'open'
          : 'complete';
    const lastRow = rows[rows.length - 1] || {};
    const observedThrough = lastRow.observed_through || (mapping || {}).generated_at || null;
    const reasonParts = [];
    if (resolution === 'daily' && selected.hours <= 168 && !hourly.length) {
      reasonParts.push('Hourly history is not retained yet, so this uses UTC-day buckets.');
    }
    if (!coverageComplete) {
      reasonParts.push(rows.length !== expectedBuckets
        ? 'Only ' + integer(rows.length) + ' of ' + integer(expectedBuckets) + ' selected ' + (resolution === 'hourly' ? 'UTC-hour' : 'UTC-day') + ' buckets are retained.'
        : 'The selected ' + (resolution === 'hourly' ? 'UTC-hour' : 'UTC-day') + ' buckets contain a gap.');
    }
    if (lowerBound) reasonParts.push('At least one source bucket is a collection lower bound.');
    if (hasOpenBucket) {
      reasonParts.push(
        'The current UTC ' + (resolution === 'hourly' ? 'hour' : 'day')
        + ' is open through ' + (observedThrough ? shortDate(observedThrough) : 'the latest collection') + '.'
      );
    }
    return Object.assign({
      selected: selected,
      available: Boolean(rows.length),
      resolution: resolution,
      rows: rows,
      expectedBuckets: expectedBuckets,
      declaredCoverage: declaredCoverage,
      windowStart: selectedRows.length ? selectedRows[0].start : null,
      anchor: anchor,
      complete: complete,
      apiCollectionComplete: retainedComplete,
      retainedComplete: retainedComplete,
      coverageStatus: coverageStatus,
      lowerBound: lowerBound,
      hasOpenBucket: hasOpenBucket,
      selectedContiguous: selectedContiguous,
      observedThrough: observedThrough,
      reason: reasonParts.join(' '),
    }, omniMappingPopulationBoundary(mapping, resolution));
  }

  function omniMappingBuckets(windowInfo) {
    if (!windowInfo || !windowInfo.available) return [];
    const resolution = windowInfo.resolution;
    const binHours = resolution === 'hourly' ? windowInfo.selected.hourlyBin : 24;
    const binMs = binHours * 60 * 60 * 1000;
    const declaredWindowStart = Number(windowInfo.windowStart);
    const hourlyAnchor = Number.isFinite(declaredWindowStart) ? declaredWindowStart : 0;
    const buckets = new Map();
    windowInfo.rows.forEach(function (row) {
      const start = omniMappingRowStart(row, resolution);
      if (start === null) return;
      const bucketStart = resolution === 'hourly'
        ? hourlyAnchor + Math.floor((start - hourlyAnchor) / binMs) * binMs
        : Date.parse(String(row.date) + 'T00:00:00Z');
      const key = new Date(bucketStart).toISOString();
      if (!buckets.has(key)) {
        buckets.set(key, {
          id: key,
          start: bucketStart,
          end: bucketStart + binMs,
          rows: [],
          complete: true,
          lowerBound: false,
          hasOpenBucket: false,
          workloads: {
            omni: emptyOmniMappingStats(),
            main: emptyOmniMappingStats(),
          },
        });
      }
      const bucket = buckets.get(key);
      bucket.rows.push(row);
      bucket.complete = bucket.complete && row.complete !== false && row.collection_complete !== false;
      bucket.lowerBound = bucket.lowerBound || row.lower_bound === true || row.collection_complete === false;
      bucket.hasOpenBucket = bucket.hasOpenBucket || row.state === 'open' || row.open === true;
      addOmniMappingStats(bucket.workloads.omni, ((row.workloads || {}).omni || {}));
      addOmniMappingStats(bucket.workloads.main, ((row.workloads || {}).main || {}));
    });
    const expectedSourceRows = resolution === 'hourly' ? binHours : 1;
    const sourceStepMs = resolution === 'hourly' ? 60 * 60 * 1000 : 24 * 60 * 60 * 1000;
    return Array.from(buckets.values()).map(function (bucket) {
      const starts = bucket.rows.map(function (row) {
        return omniMappingRowStart(row, resolution);
      }).filter(function (start) {
        return start !== null;
      }).sort(function (left, right) {
        return left - right;
      });
      const contiguous = starts.every(function (start, index) {
        return (!index || start - starts[index - 1] === sourceStepMs)
          && start === bucket.start + index * sourceStepMs;
      });
      bucket.expectedSourceRows = expectedSourceRows;
      bucket.sourceRows = starts.length;
      bucket.contiguous = contiguous;
      bucket.complete = bucket.complete
        && !bucket.lowerBound
        && !bucket.hasOpenBucket
        && starts.length === expectedSourceRows
        && contiguous;
      return bucket;
    }).sort(function (left, right) {
      return left.start - right.start;
    });
  }

  function omniHistoryPoints(omni) {
    const rows = ((((omni || {}).history || {}).points) || []);
    return rows.map(function (point) {
      const amd = point.amd || {};
      const allFleet = point.all_fleet || amd;
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

  function notifyFirstRenderSettled() {
    if (firstRenderSettled) return;
    firstRenderSettled = true;
    window.__opsV2FirstRenderSettled = true;
    window.dispatchEvent(new Event('ops-v2:first-render'));
  }

  async function renderOmni(host, ops) {
    const omni = ops.omni || {};
    if (omni.hardware_scope !== 'amd_mi_gpu') {
      host.append(n('div', 'ops-evidence-note is-warning', 'AMD MI Omni infrastructure data is unavailable. Current MI queue observations are required.'));
      return;
    }
    const current = omni.current || {};
    const currentLedger = current.ledger || {};
    const jobs = omni.current_jobs || {};
    const mappingSource = omni.mapping_history || {};
    const mapping = mappingSource.hardware_scope === 'amd_mi_gpu'
      && (((mappingSource.scope || {}).workload_pipelines || {}).main || []).join(',') === 'ci'
      ? mappingSource : {};
    const mappingPublication = (((mapping || {}).retention || {}).publication) || {};
    const mappingPublicationIncomplete = mappingPublication.complete_relative_to_source === false;
    const mappingView = omniMappingWindow(mapping, state.omniMappingRange);
    const mappingBuckets = omniMappingBuckets(mappingView);
    const mappingTotals = mappingView.available
      ? omniMappingTotals(mappingView.rows)
      : {omni: emptyOmniMappingStats(), main: emptyOmniMappingStats()};
    const omniTotal = mappingTotals.omni || {};
    const mainTotal = mappingTotals.main || {};
    const mappingAvailable = mappingView.available;
    const mappingRatesAvailable = mappingAvailable && mappingView.retainedComplete;
    const mappingCountPrefix = mappingRatesAvailable ? '' : '≥';
    const mappingPublicationRows = mappingPublication[mappingView.resolution] || {};
    const selectedMappingLabel = mappingView.selected.label;
    const jobRangeNonExhaustive = mappingView.jobCreatedRangeExhaustive === false;
    const jobRangeUnknown = mappingView.jobCreatedRangeExhaustive === null;
    const lookbackLabel = mappingView.parentBuildLookbackDays
      ? integer(mappingView.parentBuildLookbackDays) + '-day parent-build lookback'
      : 'configured parent-build lookback';
    const populationBoundaryText = jobRangeNonExhaustive
      ? (mappingView.sourceWindowExact
        ? 'UUID-deduplicated counts are exact only inside the ' + lookbackLabel + '; they are not provably exhaustive for every job created in the selected interval. '
        : 'Counts cover only the ' + lookbackLabel + ' and are not provably exhaustive for every job created in the selected interval. ')
        + (mappingView.limitation || 'Jobs attached later to older parent builds can be absent.')
      : jobRangeUnknown
        ? 'Job-created-range exhaustiveness is not published for this aggregate; treat the displayed mappings as observed counts.'
        : 'The source marks the job-created range exhaustive for this aggregate.';
    const mappingCountMeta = jobRangeNonExhaustive
      ? 'source-window count · job-created range non-exhaustive'
      : jobRangeUnknown
        ? 'observed count · population coverage unknown'
        : selectedMappingLabel;
    const omniRetiringMapped = Object.entries(omniTotal.by_queue || {}).reduce(function (sum, entry) {
      return sum + (entry[0].startsWith('amd_mi325_') ? Number(entry[1].mapped_jobs || 0) : 0);
    }, 0);
    const mainRetiringMapped = Object.entries(mainTotal.by_queue || {}).reduce(function (sum, entry) {
      return sum + (entry[0].startsWith('amd_mi325_') ? Number(entry[1].mapped_jobs || 0) : 0);
    }, 0);
    add(host, pageHeader(
      'Omni CI',
      'Incoming ' + OMNI_REPOSITORIES.omni + ' workload and its impact on the AMD queues shared with ' + OMNI_REPOSITORIES.main + '.',
      mapping.generated_at || (omni.provenance || {}).queue_snapshot_ts,
      externalLink('Open AMD mapping aggregate', SOURCE_ASSETS.workloadMapping, 'ops-button')
    ));
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
    const ledgerWaiting = Number.isFinite(Number(currentLedger.waiting)) ? Number(currentLedger.waiting) : pending.length;
    const ledgerRunning = Number.isFinite(Number(currentLedger.running)) ? Number(currentLedger.running) : running.length;
    function openJobsEvidence(title, rows, evidenceNote) {
      if (!rows.length) {
        openMetricDetail({label: title, value: 0, meta: 'No source-backed jobs in this scope.', sources: [{label: 'Open published Omni snapshot', url: SOURCE_ASSETS.omni}]});
        return;
      }
      openHistoryEvidence(title, rows.map(function (job) { return {id: job.job_id, label: job.name || 'Unnamed Omni job', timestamp: job.created_at || job.scheduled_at || job.started_at, valueSummary: value(job.state) + ' on ' + value(job.queue), url: job.url, details: {queue: job.queue, state: job.state, pipeline: job.pipeline, build: job.build, exclusion_reason: job.exclusion_reason || null}}; }), evidenceNote || 'Every active job links to its exact Buildkite source', SOURCE_ASSETS.omni);
    }
    function mappingBreakdownRows(stats, key) {
      return Object.entries((stats || {})[key] || {}).map(function (entry) {
        return {name: entry[0], stats: entry[1] || {}};
      }).sort(function (left, right) {
        return Number(right.stats.mapped_jobs || 0) - Number(left.stats.mapped_jobs || 0)
          || compareText(left.name, right.name);
      });
    }
    function openWorkloadMappingDetail(workload, title) {
      const stats = mappingTotals[workload] || emptyOmniMappingStats();
      const content = n('div', 'ops-stack');
      content.append(statusStrip([
        {label: 'OBSERVED MAPPINGS', value: mappingAvailable ? mappingCountPrefix + integer(stats.mapped_jobs) : '-', meta: mappingCountMeta},
        {label: 'STARTED JOBS', value: mappingAvailable ? mappingCountPrefix + integer(stats.started_jobs) : '-', meta: mappingRatesAvailable ? percent(stats.started_jobs, stats.mapped_jobs) + ' of mappings' : mappingAvailable ? 'Rate unavailable: selected bucket coverage is incomplete' : mappingView.reason},
        {label: 'GPU-SLOT REQUESTS', value: mappingAvailable ? integer(stats.mapped_gpu_slots) : '-', meta: 'Sum of configured GPU widths across observed mappings; not simultaneous use or GPU-hours'},
        {label: 'GPU-HOURS', value: mappingAvailable ? Number(stats.gpu_hours || 0).toLocaleString(undefined, {maximumFractionDigits: 1}) : '-', meta: 'Finished jobs with usable durations'},
      ]));
      const queueBreakdown = mappingBreakdownRows(stats, 'by_queue');
      if (queueBreakdown.length) {
        content.append(panel('Queue breakdown', integer(queueBreakdown.length) + ' queues in the selected window', dataTable([
          {label: 'Queue', sticky: true, render: function (row) { return n('span', 'ops-mono', row.name); }},
          {label: 'Mapped', numeric: true, render: function (row) { return integer(row.stats.mapped_jobs); }},
          {label: 'Started', numeric: true, render: function (row) { return integer(row.stats.started_jobs); }},
          {label: 'GPU-slot requests', numeric: true, render: function (row) { return integer(row.stats.mapped_gpu_slots); }},
          {label: 'GPU-hours', numeric: true, render: function (row) { return Number(row.stats.gpu_hours || 0).toLocaleString(undefined, {maximumFractionDigits: 1}); }},
        ], queueBreakdown)));
      }
      const pipelineBreakdown = mappingBreakdownRows(stats, 'by_pipeline');
      if (pipelineBreakdown.length) {
        content.append(panel('Pipeline breakdown', integer(pipelineBreakdown.length) + ' exact Buildkite pipelines', dataTable([
          {label: 'Pipeline', sticky: true, render: function (row) { return n('span', 'ops-mono', row.name); }},
          {label: 'Mapped', numeric: true, render: function (row) { return integer(row.stats.mapped_jobs); }},
          {label: 'Started', numeric: true, render: function (row) { return integer(row.stats.started_jobs); }},
          {label: 'GPU-slot requests', numeric: true, render: function (row) { return integer(row.stats.mapped_gpu_slots); }},
        ], pipelineBreakdown)));
      }
      openDetailDrawer({
        id: 'omni-workload-' + workload,
        title: title,
        subtitle: selectedMappingLabel + ' on monitored AMD queues',
        description: (mappingView.reason ? mappingView.reason + ' ' : '') + populationBoundaryText,
        sources: [{label: 'Open published AMD mapping aggregate', url: SOURCE_ASSETS.workloadMapping}],
        content: content,
      });
    }
    function openMappingMethodology() {
      const scope = mapping.scope || {};
      const pipelines = scope.workload_pipelines || {};
      const apiCollectionLabel = mappingView.apiCollectionComplete
        ? 'Complete inside the configured source window'
        : mappingView.lowerBound
          ? 'Incomplete inside the configured source window'
          : 'Partial or open selected bucket coverage';
      openDetailDrawer({
        id: 'omni-mapping-methodology',
        title: 'Mapping scope and coverage',
        subtitle: selectedMappingLabel + ' · ' + (mappingView.resolution === 'hourly' ? 'hourly' : 'UTC-day') + ' source buckets',
        description: (mappingView.reason ? mappingView.reason + ' ' : '') + populationBoundaryText,
        fields: [
          {label: OMNI_REPOSITORIES.omni + ' pipelines', value: ((pipelines.omni || []).join(', ')) || 'vllm-omni-amd-ci'},
          {label: OMNI_REPOSITORIES.main + ' pipelines', value: ((pipelines.main || []).join(', ')) || 'ci'},
          {label: 'Mapped job', value: 'Unique Buildkite command-job UUID observed inside the declared parent-build source window with an explicit monitored AMD MI queue mapping; retries remain distinct jobs.'},
          {label: 'Excluded', value: 'Perf-eval and every non-configured queue.'},
          {label: 'Resolution', value: mappingView.resolution === 'hourly' ? 'Hourly source aggregates' : mappingView.resolution === 'daily' ? 'UTC calendar-day aggregates' : 'Unavailable'},
          {label: 'Retained buckets', value: integer(mappingView.rows.length)},
          {label: 'Publication buckets', value: mappingPublicationRows.source === undefined ? 'Not published' : integer(mappingPublicationRows.published) + ' of ' + integer(mappingPublicationRows.source) + ' ' + mappingView.resolution + ' rows'},
          {label: 'Publication complete', value: mappingPublicationIncomplete ? 'No; oldest whole buckets were omitted to satisfy the storage cap' : 'Yes'},
          {label: 'API / UUID collection', value: apiCollectionLabel},
          {label: 'Job-created range exhaustive', value: mappingView.jobCreatedRangeExhaustive === true ? 'Yes' : mappingView.jobCreatedRangeExhaustive === false ? 'No' : 'Not published'},
          {label: 'Parent-build lookback', value: mappingView.parentBuildLookbackDays ? integer(mappingView.parentBuildLookbackDays) + ' days before the selected job-created range' : 'Not published'},
          {label: 'Count integrity', value: mappingView.sourceWindowExact ? 'UUID-exact only within the declared parent-build source window' : 'Published aggregate within the declared source window'},
          {label: 'Population limitation', value: mappingView.limitation || populationBoundaryText},
        ],
        sources: [{label: 'Open published AMD mapping aggregate', url: SOURCE_ASSETS.workloadMapping}],
      });
    }
    function openTrafficShareDetail() {
      openDetailDrawer({
        id: 'omni-traffic-share',
        title: 'Share of vLLM traffic',
        subtitle: selectedMappingLabel + ' on the same monitored AMD queue allowlist',
        description: 'Repository counts use the same selected source buckets, parent-build source window, and queue scope; no chart-axis normalization is involved. ' + populationBoundaryText,
        fields: [
          {label: OMNI_REPOSITORIES.omni + ' mapped', value: mappingAvailable ? integer(omniTotal.mapped_jobs) : '-'},
          {label: OMNI_REPOSITORIES.main + ' mapped', value: mappingAvailable ? integer(mainTotal.mapped_jobs) : '-'},
          {label: 'Combined mapped jobs', value: mappingAvailable ? integer(Number(omniTotal.mapped_jobs || 0) + Number(mainTotal.mapped_jobs || 0)) : '-'},
          {label: 'Omni share', value: mappingRatesAvailable ? percent(omniTotal.mapped_jobs, Number(omniTotal.mapped_jobs || 0) + Number(mainTotal.mapped_jobs || 0)) : 'Unavailable'},
          {label: 'API bucket status', value: mappingView.coverageStatus},
          {label: 'Job-created range', value: mappingView.jobCreatedRangeExhaustive === true ? 'exhaustive' : mappingView.jobCreatedRangeExhaustive === false ? 'not exhaustive' : 'not published'},
        ],
        sources: [{label: 'Open published AMD mapping aggregate', url: SOURCE_ASSETS.workloadMapping}],
      });
    }
    function openMi325ExposureDetail() {
      const rows = Object.entries(omniTotal.by_queue || {}).filter(function (entry) {
        return entry[0].startsWith('amd_mi325_');
      }).map(function (entry) {
        return {
          name: entry[0],
          omni: entry[1] || {},
          main: ((mainTotal.by_queue || {})[entry[0]]) || {},
        };
      }).sort(function (left, right) {
        return Number(right.omni.mapped_jobs || 0) - Number(left.omni.mapped_jobs || 0);
      });
      const content = rows.length ? dataTable([
        {label: 'Retiring queue', sticky: true, render: function (row) { return linkButton(row.name, function () { openQueueMappingDetail(row); }, 'Inspect MI325 impact for ' + row.name); }},
        {label: 'Omni mapped', numeric: true, render: function (row) { return integer(row.omni.mapped_jobs); }},
        {label: OMNI_REPOSITORIES.main + ' mapped', numeric: true, render: function (row) { return integer(row.main.mapped_jobs); }},
        {label: 'Omni GPU-slot requests', numeric: true, render: function (row) { return integer(row.omni.mapped_gpu_slots); }},
      ], rows) : n('div', 'ops-empty', 'No selected-window Omni mappings targeted MI325.');
      openDetailDrawer({
        id: 'omni-mi325-exposure',
        title: 'MI325 retirement exposure',
        subtitle: selectedMappingLabel + ' · retiring queues only',
        description: mappingCountPrefix + integer(omniRetiringMapped) + ' of ' + mappingCountPrefix + integer(omniTotal.mapped_jobs) + ' published source-window Omni mappings targeted MI325. ' + (mappingRatesAvailable ? '' : 'The exposure rate is unavailable because selected bucket coverage is incomplete. ') + populationBoundaryText,
        fields: [
          {label: 'Omni exposure', value: mappingRatesAvailable ? percent(omniRetiringMapped, omniTotal.mapped_jobs) : 'Unavailable'},
          {label: 'Observed Omni MI325 mappings', value: mappingAvailable ? integer(omniRetiringMapped) : '-'},
          {label: 'Observed ' + OMNI_REPOSITORIES.main + ' MI325 mappings', value: mappingAvailable ? integer(mainRetiringMapped) : '-'},
          {label: 'Job-created range', value: mappingView.jobCreatedRangeExhaustive === true ? 'exhaustive' : mappingView.jobCreatedRangeExhaustive === false ? 'not exhaustive' : 'not published'},
        ],
        sources: [{label: 'Open published AMD mapping aggregate', url: SOURCE_ASSETS.workloadMapping}],
        content: content,
      });
    }
    const mappingToolbar = n('div', 'ops-toolbar ops-analytics-window-toolbar ops-omni-mapping-toolbar');
    add(mappingToolbar, [
      n('span', 'ops-toolbar-label', 'Incoming workload'),
      segmented(OMNI_MAPPING_WINDOWS, state.omniMappingRange, function (range) {
        setRouteState('ci-omni', 'omniMappingRange', range, 'omni_mapping_range');
      }, 'Filter unique Omni mappings by time window'),
      button('Scope & coverage', openMappingMethodology),
    ]);
    host.append(mappingToolbar);
    host.append(statusStrip([
      {id: 'omni-mapped-jobs', label: jobRangeNonExhaustive || jobRangeUnknown ? 'OBSERVED OMNI MAPPINGS' : 'INCOMING OMNI JOBS', value: mappingAvailable ? mappingCountPrefix + integer(omniTotal.mapped_jobs) : '-', meta: mappingAvailable ? mappingCountPrefix + integer(omniTotal.started_jobs) + ' started · ' + mappingCountMeta : mappingView.reason, tone: jobRangeNonExhaustive || jobRangeUnknown || !mappingRatesAvailable ? 'is-warning' : 'is-info', onOpen: function () { openWorkloadMappingDetail('omni', OMNI_REPOSITORIES.omni); }},
      {id: 'omni-gpu-demand', label: 'GPU-SLOT REQUESTS', value: mappingAvailable ? integer(omniTotal.mapped_gpu_slots) : '-', meta: mappingAvailable ? 'summed job widths · not concurrency · ' + Number(omniTotal.gpu_hours || 0).toLocaleString(undefined, {maximumFractionDigits: 1}) + ' completed GPU-hours' : 'Hourly collection is required for this window', onOpen: function () { openWorkloadMappingDetail('omni', OMNI_REPOSITORIES.omni + ' GPU demand'); }},
      {id: 'omni-mapped-share', label: 'SHARE OF OBSERVED VLLM TRAFFIC', value: mappingRatesAvailable ? percent(omniTotal.mapped_jobs, Number(omniTotal.mapped_jobs || 0) + Number(mainTotal.mapped_jobs || 0)) : 'Unavailable', meta: mappingAvailable ? mappingCountPrefix + integer(omniTotal.mapped_jobs) + ' of ' + mappingCountPrefix + integer(Number(omniTotal.mapped_jobs || 0) + Number(mainTotal.mapped_jobs || 0)) + ' published source-window mappings' : 'No comparable mapping window', onOpen: openTrafficShareDetail},
      {id: 'omni-retiring-share', label: 'MI325 RETIREMENT EXPOSURE', value: mappingRatesAvailable ? percent(omniRetiringMapped, omniTotal.mapped_jobs) : 'Unavailable', meta: mappingAvailable ? mappingCountPrefix + integer(omniRetiringMapped) + ' Omni · ' + mappingCountPrefix + integer(mainRetiringMapped) + ' ' + OMNI_REPOSITORIES.main : 'No selected-window queue evidence', tone: omniRetiringMapped ? 'is-warning' : 'is-success', onOpen: openMi325ExposureDetail},
    ]));
    const apiCoverageHeading = mappingView.coverageStatus === 'complete'
      ? 'API/UUID collection complete for the selected closed buckets. '
      : mappingView.coverageStatus === 'open'
        ? 'API/UUID collection complete inside the source window; current bucket open. '
        : mappingView.coverageStatus === 'lower_bound'
          ? 'API/UUID collection is incomplete inside the source window. '
          : 'Selected API/UUID bucket coverage is incomplete. ';
    const coverageHeading = apiCoverageHeading + (
      jobRangeNonExhaustive
        ? 'All job-created mappings are not provably exhaustive. '
        : jobRangeUnknown
          ? 'Job-created population coverage is not published. '
          : ''
    );
    const scopeNote = n('div', 'ops-evidence-note ' + (
      mappingView.retainedComplete && !mappingPublicationIncomplete && !jobRangeNonExhaustive && !jobRangeUnknown ? 'is-info' : 'is-warning'
    ) + ' ops-omni-coverage-note');
    add(scopeNote, [
      n('strong', '', coverageHeading),
      n('span', '', (mappingPublicationIncomplete ? 'The repository publication omitted ' + integer(mappingPublicationRows.omitted) + ' oldest ' + mappingView.resolution + ' buckets (' + integer(mappingPublicationRows.published) + ' of ' + integer(mappingPublicationRows.source) + ' published). ' : '') + (mappingView.reason ? mappingView.reason + ' ' : integer(mappingView.rows.length) + ' retained ' + mappingView.resolution + ' buckets; mapped and started are separate counts. ') + (mappingRatesAvailable ? '' : 'Rates, shares, and deltas are unavailable for incomplete selected coverage. ') + populationBoundaryText),
      linkButton('Inspect methodology', openMappingMethodology, 'Inspect mapping scope, resolution, and source coverage'),
      excludedJobs.length ? linkButton('Inspect excluded stale jobs', function () { openJobsEvidence('Excluded stale Omni jobs', excludedJobs, 'Jobs beyond the collector age threshold; retained for exact Buildkite review but excluded from active analytics'); }, 'Inspect stale Omni jobs excluded from active analytics') : null,
    ]);
    host.append(scopeNote);

    const omniByQueue = omniTotal.by_queue || {};
    const mainByQueue = mainTotal.by_queue || {};
    const mappingQueueRows = Array.from(new Set(Object.keys(omniByQueue).concat(Object.keys(mainByQueue)))).map(function (queueName) {
      return {
        name: queueName,
        omni: omniByQueue[queueName] || {},
        main: mainByQueue[queueName] || {},
      };
    }).filter(function (row) {
      return Number(row.omni.mapped_jobs || 0) > 0;
    }).sort(function (left, right) {
      return Number(right.omni.mapped_jobs || 0) - Number(left.omni.mapped_jobs || 0)
        || compareText(left.name, right.name);
    });
    function openQueueMappingDetail(row, contextLabel) {
      const queueSnapshot = ((((ops.queue || {}).snapshot || {}).queues || {})[row.name]) || {};
      openDetailDrawer({
        id: 'omni-impact-' + row.name,
        title: row.name,
        subtitle: (contextLabel || selectedMappingLabel) + ' impact on a monitored AMD queue',
        fields: [
          {label: OMNI_REPOSITORIES.omni + ' observed mappings', value: integer(row.omni.mapped_jobs || 0)},
          {label: OMNI_REPOSITORIES.omni + ' started', value: integer(row.omni.started_jobs || 0)},
          {label: OMNI_REPOSITORIES.omni + ' GPU-slot requests', value: integer(row.omni.mapped_gpu_slots || 0)},
          {label: OMNI_REPOSITORIES.omni + ' GPU-hours', value: Number(row.omni.gpu_hours || 0).toLocaleString(undefined, {maximumFractionDigits: 1})},
          {label: OMNI_REPOSITORIES.main + ' observed mappings', value: integer(row.main.mapped_jobs || 0)},
          {label: 'Omni share on this queue', value: mappingRatesAvailable ? percent(row.omni.mapped_jobs, Number(row.omni.mapped_jobs || 0) + Number(row.main.mapped_jobs || 0)) : 'Unavailable'},
          {label: 'Lifecycle', value: row.name.startsWith('amd_mi325_') ? 'retiring' : 'active'},
          {label: 'Scope', value: contextLabel || selectedMappingLabel},
        ],
        sources: [
          {label: 'Open published AMD mapping aggregate', url: SOURCE_ASSETS.workloadMapping},
          queueSnapshot.queue_url ? {label: 'Open Buildkite queue', url: queueSnapshot.queue_url} : null,
        ],
      });
    }
    function openMappingBucket(bucket) {
      const bucketOmni = (bucket.workloads || {}).omni || {};
      const bucketMain = (bucket.workloads || {}).main || {};
      const queueRows = Object.entries(bucketOmni.by_queue || {}).map(function (entry) {
        return {
          name: entry[0],
          omni: entry[1] || {},
          main: ((bucketMain.by_queue || {})[entry[0]]) || {},
        };
      }).sort(function (left, right) {
        return Number(right.omni.mapped_jobs || 0) - Number(left.omni.mapped_jobs || 0);
      });
      const content = n('div', 'ops-stack');
      if (queueRows.length) {
        content.append(dataTable([
          {label: 'Queue', sticky: true, render: function (row) { return linkButton(row.name, function () { openQueueMappingDetail(row, omniMappingBucketLabel(bucket, mappingView.resolution) + ' UTC chart bucket'); }, 'Inspect chart-bucket impact for ' + row.name); }},
          {label: OMNI_REPOSITORIES.omni, numeric: true, render: function (row) { return integer(row.omni.mapped_jobs); }},
          {label: OMNI_REPOSITORIES.main, numeric: true, render: function (row) { return integer(row.main.mapped_jobs); }},
          {label: 'GPU-slot requests', numeric: true, render: function (row) { return integer(row.omni.mapped_gpu_slots); }},
        ], queueRows));
      }
      openDetailDrawer({
        id: 'omni-bucket-' + bucket.id,
        title: omniMappingBucketLabel(bucket, mappingView.resolution) + ' UTC',
        subtitle: 'Incoming Omni workload mapped during this chart bucket',
        fields: [
          {label: OMNI_REPOSITORIES.omni + ' mapped', value: integer(bucketOmni.mapped_jobs)},
          {label: OMNI_REPOSITORIES.omni + ' started', value: integer(bucketOmni.started_jobs)},
          {label: OMNI_REPOSITORIES.omni + ' GPU-slot requests', value: integer(bucketOmni.mapped_gpu_slots)},
          {label: OMNI_REPOSITORIES.main + ' mapped', value: integer(bucketMain.mapped_jobs)},
          {label: 'API / UUID coverage', value: bucket.lowerBound
            ? 'collection lower bound'
            : bucket.hasOpenBucket
              ? 'open current UTC ' + (mappingView.resolution === 'daily' ? 'day' : 'hour')
              : bucket.complete
                ? 'complete'
                : 'partial (' + integer(bucket.sourceRows) + '/' + integer(bucket.expectedSourceRows) + ' source buckets)'},
        ],
        sources: [{label: 'Open published AMD mapping aggregate', url: SOURCE_ASSETS.workloadMapping}],
        content: content,
      });
    }
    function mappingEvidence(bucket) {
      const bucketOmni = (bucket.workloads || {}).omni || {};
      return {
        id: bucket.id,
        label: omniMappingBucketLabel(bucket, mappingView.resolution) + ' UTC',
        timestamp: new Date(bucket.start).toISOString(),
        valueSummary: integer(bucketOmni.mapped_jobs) + ' mapped · ' + integer(bucketOmni.started_jobs) + ' started',
        details: {
          repository: OMNI_REPOSITORIES.omni,
          mapped_jobs: bucketOmni.mapped_jobs,
          started_jobs: bucketOmni.started_jobs,
          mapped_gpu_slots: bucketOmni.mapped_gpu_slots,
          api_uuid_coverage: bucket.lowerBound ? 'lower bound' : bucket.hasOpenBucket ? 'open' : bucket.complete ? 'complete' : 'partial',
          job_created_range_exhaustive: mappingView.jobCreatedRangeExhaustive,
        },
        sources: [{label: 'Open published AMD mapping aggregate', url: SOURCE_ASSETS.workloadMapping}],
        onOpen: function () { openMappingBucket(bucket); },
      };
    }
    const bucketColumns = [
      {label: 'UTC bucket', sticky: true, render: function (bucket) { return linkButton(omniMappingBucketLabel(bucket, mappingView.resolution), function () { openMappingBucket(bucket); }, 'Inspect mapping bucket ' + omniMappingBucketLabel(bucket, mappingView.resolution)); }},
      {label: 'Omni mapped', numeric: true, render: function (bucket) { return integer(((bucket.workloads || {}).omni || {}).mapped_jobs); }},
      {label: 'Omni started', numeric: true, render: function (bucket) { return integer(((bucket.workloads || {}).omni || {}).started_jobs); }},
      {label: 'GPU-slot requests', numeric: true, render: function (bucket) { return integer(((bucket.workloads || {}).omni || {}).mapped_gpu_slots); }},
      {label: 'API coverage', render: function (bucket) { return badge(bucket.lowerBound ? 'lower bound' : bucket.hasOpenBucket ? 'open' : bucket.complete ? 'complete' : 'partial', bucket.lowerBound ? 'is-warning' : bucket.complete ? 'is-success' : 'is-info'); }},
    ];
    function browseMappingBuckets() {
      openTableBrowser({
        id: 'omni-mapping-buckets',
        title: OMNI_REPOSITORIES.omni + ' mapping buckets',
        subtitle: selectedMappingLabel + ' · API/UUID coverage per bucket · ' + (jobRangeNonExhaustive ? 'job-created range not exhaustive' : 'population scope published separately'),
        rows: mappingBuckets.slice().reverse(),
        columns: bucketColumns,
        searchPlaceholder: 'Filter UTC bucket or coverage',
        searchText: function (bucket) { return [omniMappingBucketLabel(bucket, mappingView.resolution), bucket.complete, bucket.lowerBound, bucket.hasOpenBucket].join(' '); },
        geometry: {name: 'omni-mapping-buckets', minWidth: '760px'},
      });
    }
    const overviewGrid = n('div', 'ops-grid ops-omni-overview-grid');
    if (mappingBuckets.length) {
      const mappingChart = chartPanel(
        'Observed incoming mappings from ' + OMNI_REPOSITORIES.omni,
        integer(mappingBuckets.length) + ' chart buckets · ' + selectedMappingLabel + ' · source-window counts · one shared scale',
        'omni-amd-mapped-jobs'
      );
      mappingChart.root.classList.add('ops-omni-volume');
      overviewGrid.append(mappingChart.root);
      requestAnimationFrame(function () {
        drawChart('omni-amd-mapped-jobs', mappingChart.canvas, {
          type: 'bar',
          data: {
            labels: mappingBuckets.map(function (bucket) { return omniMappingBucketLabel(bucket, mappingView.resolution); }),
            datasets: [{
              label: OMNI_REPOSITORIES.omni + ' observed mapped jobs',
              data: mappingBuckets.map(function (bucket) { return Number((((bucket.workloads || {}).omni || {}).mapped_jobs) || 0); }),
              backgroundColor: '#22b8ad',
            }],
          },
          options: {
            plugins: {legend: {display: false}},
            scales: {y: {beginAtZero: true, title: {display: true, text: 'Observed mapped jobs'}}},
          },
          evidenceTitle: OMNI_REPOSITORIES.omni + ' mapping buckets',
          evidenceAsset: SOURCE_ASSETS.workloadMapping,
          evidence: mappingBuckets.map(mappingEvidence),
        });
      });
    } else {
      overviewGrid.append(panel('Incoming Omni workload unavailable', selectedMappingLabel, n('div', 'ops-empty', mappingView.reason), 'ops-omni-volume'));
    }
    if (mappingQueueRows.length) {
      const queueImpact = chartPanel(
        'Where Omni lands',
        integer(mappingQueueRows.length) + ' AMD queues · click a bar or row for impact details',
        'omni-queue-impact'
      );
      queueImpact.root.classList.add('ops-omni-impact');
      queueImpact.root.querySelector('.ops-panel-body').append(dataTable([
        {label: 'Queue', sticky: true, width: '150px', render: function (row) { return linkButton(row.name, function () { openQueueMappingDetail(row); }, 'Inspect selected-window impact for ' + row.name); }},
        {label: 'Mapped', numeric: true, width: '74px', render: function (row) { return linkButton(integer(row.omni.mapped_jobs), function () { openQueueMappingDetail(row); }, 'Inspect Omni mappings on ' + row.name); }},
        {label: 'Share', numeric: true, width: '68px', render: function (row) { return mappingRatesAvailable ? percent(row.omni.mapped_jobs, omniTotal.mapped_jobs) : 'Unavailable'; }},
      ], mappingQueueRows.slice(0, 6), null, {name: 'omni-impact-preview', minWidth: '292px'}));
      overviewGrid.append(queueImpact.root);
      requestAnimationFrame(function () {
        drawChart('omni-queue-impact', queueImpact.canvas, {
          type: 'bar',
          data: {
            labels: mappingQueueRows.map(function (row) { return row.name; }),
            datasets: [{
              label: 'Observed mapped jobs',
              data: mappingQueueRows.map(function (row) { return Number(row.omni.mapped_jobs || 0); }),
              backgroundColor: mappingQueueRows.map(function (row) { return row.name.startsWith('amd_mi325_') ? '#e3a63a' : '#22b8ad'; }),
            }],
          },
          options: {
            indexAxis: 'y',
            plugins: {legend: {display: false}},
            scales: {x: {beginAtZero: true, title: {display: true, text: 'Observed mapped jobs'}}},
          },
          evidenceTitle: 'Omni queue impact in ' + selectedMappingLabel,
          evidenceAsset: SOURCE_ASSETS.workloadMapping,
          evidence: mappingQueueRows.map(function (row) {
            return {
              id: row.name,
              label: row.name,
              valueSummary: integer(row.omni.mapped_jobs) + ' observed source-window mappings',
              details: {repository: OMNI_REPOSITORIES.omni, mapped_jobs: row.omni.mapped_jobs, share: mappingRatesAvailable ? percent(row.omni.mapped_jobs, omniTotal.mapped_jobs) : 'Unavailable'},
              onOpen: function () { openQueueMappingDetail(row); },
            };
          }),
        });
      });
    } else {
      overviewGrid.append(panel('Where Omni lands', 'No selected-window queue mappings', n('div', 'ops-empty', mappingView.reason || 'No Omni mappings were observed in this window.'), 'ops-omni-impact'));
    }
    host.append(overviewGrid);

    const comparisonRows = [
      {id: 'omni', repository: OMNI_REPOSITORIES.omni, stats: omniTotal},
      {id: 'main', repository: OMNI_REPOSITORIES.main, stats: mainTotal},
    ];
    const comparisonActions = n('div', 'ops-inline-actions');
    add(comparisonActions, [
      button('Inspect time buckets', browseMappingBuckets),
      mappingQueueRows.length ? button('Browse all queues', function () {
        openTableBrowser({
          id: 'omni-queue-impact-browser',
          title: 'Omni impact by AMD queue',
          subtitle: selectedMappingLabel + ' · UUID-deduplicated source-window mapping aggregates · ' + (jobRangeNonExhaustive ? 'job-created range not exhaustive' : 'population scope published separately'),
          rows: mappingQueueRows,
          columns: [
            {label: 'Queue', sticky: true, render: function (row) { return linkButton(row.name, function () { openQueueMappingDetail(row); }, 'Inspect ' + row.name); }},
            {label: 'Omni mapped', numeric: true, render: function (row) { return integer(row.omni.mapped_jobs); }},
            {label: 'Omni started', numeric: true, render: function (row) { return integer(row.omni.started_jobs); }},
            {label: OMNI_REPOSITORIES.main + ' mapped', numeric: true, render: function (row) { return integer(row.main.mapped_jobs); }},
            {label: 'Omni GPU-hours', numeric: true, render: function (row) { return Number(row.omni.gpu_hours || 0).toLocaleString(undefined, {maximumFractionDigits: 1}); }},
          ],
          searchPlaceholder: 'Filter queue',
          searchText: function (row) { return row.name; },
          geometry: {name: 'omni-queue-impact', minWidth: '900px'},
        });
      }) : null,
    ]);
    host.append(compactTablePanel(
      'Repository comparison',
      selectedMappingLabel + ' · comparison stays numeric instead of sharing a misleading chart scale',
      [
        {label: 'Repository', sticky: true, width: '230px', render: function (row) { return linkButton(row.repository, function () { openWorkloadMappingDetail(row.id, row.repository); }, 'Inspect ' + row.repository + ' mapping details'); }},
        {label: 'Mapped', numeric: true, width: '82px', render: function (row) { return mappingAvailable ? integer(row.stats.mapped_jobs) : '-'; }},
        {label: 'Started', numeric: true, width: '82px', render: function (row) { return mappingAvailable ? integer(row.stats.started_jobs) : '-'; }},
        {label: 'Start rate', numeric: true, width: '82px', render: function (row) { return mappingAvailable ? percent(row.stats.started_jobs, row.stats.mapped_jobs) : '-'; }},
        {label: 'GPU-slot requests', numeric: true, width: '112px', render: function (row) { return mappingAvailable ? integer(row.stats.mapped_gpu_slots) : '-'; }},
        {label: 'GPU-hours', numeric: true, width: '92px', render: function (row) { return mappingAvailable ? Number(row.stats.gpu_hours || 0).toLocaleString(undefined, {maximumFractionDigits: 1}) : '-'; }},
      ],
      comparisonRows,
      {limit: 2, headerActions: comparisonActions, className: 'ops-omni-comparison', geometry: {name: 'omni-repository-comparison', minWidth: '656px'}}
    ));

    const allHistoryPoints = omniHistoryPoints(omni);
    const waitingHistoryPoints = allHistoryPoints.filter(function (point) { return point.waitingSupported; });
    const occupancyHistoryPoints = allHistoryPoints.filter(function (point) {
      return point.waitingSupported || point.runningSupported;
    });
    const points = omniWindowPoints(occupancyHistoryPoints, state.omniRange);
    const selectedRange = OMNI_RANGE_WINDOWS.find(function (item) { return item.id === state.omniRange; }) || OMNI_RANGE_WINDOWS[4];
    const waitingPoints = points.filter(function (point) { return point.waitingSupported; });
    const runningPoints = points.filter(function (point) { return point.runningSupported; });
    const latestPoint = waitingPoints.length ? waitingPoints[waitingPoints.length - 1] : null;
    const waitingPeak = waitingPoints.length ? Math.max.apply(null, waitingPoints.map(function (point) { return point.allWaiting; })) : null;
    const windowCoverage = waitingPoints.length && waitingPoints.every(function (point) { return point.waitingCoverage === 'complete'; }) ? 'complete' : waitingPoints.length ? 'partial' : 'unavailable';
    function historyEvidence(rows) {
      return rows.map(function (point) {
        return {
          label: shortDate(point.ts),
          timestamp: point.ts,
          valueSummary: (point.waitingSupported ? integer(point.allWaiting) : 'unavailable') + ' observed waiting - ' + (point.runningSupported ? integer(point.allRunning) : 'unavailable') + ' observed running',
          details: {
            monitored_amd_waiting_observed: point.amdWaiting,
            monitored_amd_running_observed: point.amdRunning,
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
    function openOccupancyEvidence(title, rows, note) {
      if (rows.length) {
        openHistoryEvidence(title, historyEvidence(rows), note, SOURCE_ASSETS.queueHistory);
        return;
      }
      openMetricDetail({
        id: 'omni-occupancy-unavailable',
        label: title,
        value: 'unavailable',
        meta: 'No workload-attributed occupancy snapshots fall inside ' + selectedRange.label + '. Aggregate queue totals are not reclassified as Omni.',
        sources: [{label: 'Inspect published queue history', url: SOURCE_ASSETS.queueHistory}],
      });
    }
    const queueRows = Array.from(affected).sort().map(function (name) {
      const relatedPending = pending.filter(function (job) { return (job.queue || 'unknown') === name; }).length;
      const relatedRunning = running.filter(function (job) { return (job.queue || 'unknown') === name; }).length;
      return {
        name: name,
        waiting: Object.prototype.hasOwnProperty.call(waitingByQueue, name) ? Number(waitingByQueue[name] || 0) : relatedPending,
        running: Object.prototype.hasOwnProperty.call(runningByQueue, name) ? Number(runningByQueue[name] || 0) : relatedRunning,
        jobs: relatedPending + relatedRunning,
      };
    });
    const dailyRows = omniDailyRows(waitingHistoryPoints);
    const visibleJobs = activeJobs.slice().sort(function (left, right) {
      return Number(right.wait_min || 0) - Number(left.wait_min || 0)
        || compareText(left.name, right.name);
    });
    const jobColumns = [
      {label: 'Job', sticky: true, render: function (r) { return externalLink(r.name || 'Unnamed job', r.url); }},
      {label: 'Queue', render: function (r) { return linkButton(value(r.queue), function () { openQueueDetail(r.queue, ((((ops.queue || {}).snapshot || {}).queues || {})[r.queue]) || {}, activeJobs); }, 'Inspect queue and exact jobs for ' + value(r.queue)); }},
      {label: 'State', render: function (r) { return linkedBadge(r.state, r.url); }},
      {label: 'Age', numeric: true, render: function (r) { return externalLink(duration(r.wait_min !== undefined ? r.wait_min : r.run_min), r.url); }},
      {label: 'Analysis', render: function (r) { return badge(r.analysis_excluded ? 'stale excluded' : 'active', r.analysis_excluded ? 'is-warning' : 'is-success'); }},
      {label: 'Build', render: function (r) { return externalLink((r.pipeline || '?') + ' #' + value(r.build), r.build_url || buildUrl(r.pipeline, r.build), 'ops-mono'); }},
      {label: 'Source', render: function (r) { return linkedBadge(r.source || r.workload || 'omni', r.url, null, 'is-info'); }},
    ];
    function browseActiveJobs() {
      openTableBrowser({
        id: 'omni-job-browser',
        title: 'Current Omni CI jobs on monitored AMD queues',
        subtitle: 'Every row links to the exact Buildkite job; aggregate queue totals are never expanded into synthetic jobs',
        rows: visibleJobs,
        columns: jobColumns,
        searchPlaceholder: 'Filter job, queue, pipeline, branch, or state',
        searchText: function (row) { return [row.name, row.queue, row.pipeline, row.branch, row.state].join(' '); },
        geometry: {name: 'omni-jobs', minWidth: '1180px'},
      });
    }
    function browseCurrentQueues() {
      openTableBrowser({
        id: 'omni-current-queues',
        title: 'Current Omni CI queue distribution',
        subtitle: 'Exact active jobs on the monitored AMD allowlist',
        rows: queueRows,
        columns: [
          {label: 'Queue', sticky: true, render: function (row) { return linkButton(row.name, function () { openQueueDetail(row.name, ((((ops.queue || {}).snapshot || {}).queues || {})[row.name]) || {}, activeJobs); }, 'Inspect ' + row.name); }},
          {label: 'Waiting', numeric: true, render: function (row) { return integer(row.waiting); }},
          {label: 'Running', numeric: true, render: function (row) { return integer(row.running); }},
          {label: 'Exact jobs', numeric: true, render: function (row) { return integer(row.jobs); }},
        ],
        searchPlaceholder: 'Filter queue',
        searchText: function (row) { return row.name; },
        geometry: {name: 'omni-current-queues', minWidth: '680px'},
      });
    }
    function browseLegacyOccupancy() {
      openTableBrowser({
        id: 'omni-legacy-occupancy',
        title: 'Closing occupancy context by UTC day',
        subtitle: 'Snapshot occupancy, not unique-job volume',
        rows: dailyRows,
        columns: [
          {label: 'UTC day', sticky: true, render: function (row) { return linkButton(row.day, function () { openHistoryEvidence('Omni queue observations on ' + row.day, historyEvidence(allHistoryPoints.filter(function (point) { return new Date(point.time).toISOString().slice(0, 10) === row.day; })), 'Every workload-attributed snapshot retained for this UTC day', SOURCE_ASSETS.queueHistory); }); }},
          {label: 'Closing queued', numeric: true, render: function (row) { return integer(row.waiting); }},
          {label: 'Day change', numeric: true, render: function (row) { return row.delta === null ? '-' : signedInteger(row.delta); }},
          {label: 'Daily peak', numeric: true, render: function (row) { return integer(row.peak); }},
          {label: 'Samples', numeric: true, render: function (row) { return integer(row.samples); }},
          {label: 'Attribution', render: function (row) { return badge(row.complete ? 'complete' : 'partial lower bound', row.complete ? 'is-success' : 'is-warning'); }},
        ],
        searchPlaceholder: 'Filter UTC day or attribution',
        searchText: function (row) { return [row.day, row.complete].join(' '); },
        geometry: {name: 'omni-legacy-occupancy', minWidth: '850px'},
      });
    }
    function liveFact(label, renderedValue, meta, onOpen, tone) {
      const fact = n('button', 'ops-omni-live-fact ' + (tone || ''));
      fact.type = 'button';
      fact.setAttribute('aria-label', label + ': ' + renderedValue + '. ' + meta);
      fact.addEventListener('click', onOpen);
      add(fact, [
        n('span', 'ops-stat-label', label),
        n('strong', 'ops-omni-live-value', renderedValue),
        n('span', 'ops-stat-meta', meta),
      ]);
      return fact;
    }
    const liveBody = n('div', 'ops-stack ops-omni-live-body');
    const liveFacts = n('div', 'ops-omni-live-facts');
    add(liveFacts, [
      liveFact('EXACT ACTIVE JOBS', integer(activeJobs.length), integer(ledgerWaiting) + ' waiting · ' + integer(ledgerRunning) + ' running', browseActiveJobs, pending.length ? 'is-warning' : ''),
      liveFact('OBSERVED QUEUED', latestPoint ? integer(latestPoint.allWaiting) : '-', latestPoint ? shortDate(latestPoint.ts) : 'No waiting-attributed snapshot in ' + selectedRange.label, function () { openOccupancyEvidence('Latest observed Omni queue', latestPoint ? [latestPoint] : [], 'Explicit waiting-attributed counts only'); }, latestPoint && latestPoint.allWaiting ? 'is-warning' : ''),
      liveFact('QUEUED PEAK', waitingPeak === null ? '-' : integer(waitingPeak), integer(waitingPoints.length) + ' waiting-attributed snapshots · ' + selectedRange.label, function () { openOccupancyEvidence('Omni queued observations', waitingPoints, 'Snapshot occupancy is operational context, not unique-job volume'); }, waitingPeak ? 'is-warning' : ''),
      liveFact('ATTRIBUTED SNAPSHOTS', points.length ? integer(points.length) : '-', integer(waitingPoints.length) + ' waiting · ' + integer(runningPoints.length) + ' running', function () { openOccupancyEvidence('Omni workload attribution coverage', points, 'Waiting and running availability are retained independently; partial attribution remains a lower bound'); }, points.length && windowCoverage === 'complete' ? 'is-success' : 'is-warning'),
    ]);
    liveBody.append(liveFacts);
    const liveActions = n('div', 'ops-inline-actions ops-omni-live-actions');
    add(liveActions, [
      segmented(OMNI_RANGE_WINDOWS, state.omniRange, function (range) {
        setRouteState('ci-omni', 'omniRange', range, 'omni_range');
      }, 'Filter live Omni occupancy context by time window'),
      button('Active jobs (' + integer(activeJobs.length) + ')', browseActiveJobs),
      button('Current queues (' + integer(queueRows.length) + ')', browseCurrentQueues),
      button('Occupancy history', function () { openOccupancyEvidence('Omni occupancy observations in ' + selectedRange.label, points, 'Observed workload-attributed snapshots only'); }),
      button('Daily closing context', browseLegacyOccupancy),
      externalLink('Inspect published queue history', SOURCE_ASSETS.queueHistory, 'ops-button'),
    ]);
    liveBody.append(liveActions);
    host.append(panel(
      'Live AMD queue state',
      'Exact jobs now plus ' + selectedRange.label + ' observed occupancy; distinct from unique mapping volume above',
      liveBody,
      'ops-omni-live'
    ));
  }

  function omniMappingBucketLabel(bucket, resolution) {
    const date = new Date(bucket.start);
    if (resolution === 'daily') return date.toISOString().slice(0, 10);
    return date.toISOString().slice(5, 13).replace('T', ' ') + ':00';
  }

  function openQueueDetail(name, row, jobs) {
    const related = (jobs || []).filter(function (job) { return job.queue === name; });
    const nativeObservedAt = row.metrics_ts || null;
    const nativeSources = [row.official_wait_source, row.jobs_passed_source, row.jobs_failed_source]
      .filter(Boolean).filter(function (source, index, all) { return all.indexOf(source) === index; });
    const p50Source = waitSourceDetail(row, 'p50');
    const p95Source = waitSourceDetail(row, 'p95');
    const sampledP50 = sampleWaitValue(row, 'p50');
    const sampledP95 = sampleWaitValue(row, 'p95');
    const p99Value = waitValue(row, 'p99');
    const sampleCount = waitSampleCount(row);
    const sampleExpected = Number.isFinite(Number(row.wait_sample_expected_count)) ? Number(row.wait_sample_expected_count) : null;
    const sampleCoverage = sampleExpected === null
      ? 'Unavailable'
      : (sampleCount === null ? '0' : integer(sampleCount)) + ' / ' + integer(sampleExpected) + ' non-zombie waiting jobs - ' + (row.wait_sample_complete === true ? 'reconciled' : 'not reconciled');
    const content = related.length ? dataTable([
      {label: 'Job', sticky: true, render: function (job) { return externalLink(job.name || 'Unnamed job', job.url); }},
      {label: 'State', render: function (job) { return linkedBadge(job.state || 'unknown', job.url); }},
      {label: 'Age', numeric: true, render: function (job) { return duration(job.wait_min !== undefined ? job.wait_min : job.run_min); }},
      {label: 'Build', render: function (job) { return externalLink((job.pipeline || '?') + ' #' + value(job.build), job.build_url || buildUrl(job.pipeline, job.build), 'ops-mono'); }},
    ], related, integer(related.length) + ' active jobs on this queue') : n('div', 'ops-empty', 'No active jobs are retained for this queue.');
    openDetailDrawer({
      id: 'queue-' + name,
      title: name,
      subtitle: 'Current queue state; queue-native waits include the visible backlog, while scheduled samples exclude jobs flagged at 4+ hours',
      fields: [
        {label: 'Running', value: integer(row.running)},
        {label: 'Waiting', value: integer(row.waiting)},
        {label: 'Connected agents', value: hasAgentMeasurement(row) ? integer(row.connected_agents !== undefined ? row.connected_agents : row.agents) : 'Unavailable'},
        {label: 'Min wait - latest Buildkite metrics bucket', value: duration(officialWaitValue(row, 'min'))},
        {label: 'p50 Buildkite native', value: duration(officialWaitValue(row, 'p50'))},
        {label: 'p95 Buildkite native', value: duration(officialWaitValue(row, 'p95'))},
        {label: 'Max wait - latest Buildkite metrics bucket', value: duration(officialWaitValue(row, 'max'))},
        {label: 'Jobs passed - latest Buildkite metrics bucket', value: row.jobs_passed === null || row.jobs_passed === undefined ? '-' : integer(row.jobs_passed)},
        {label: 'Jobs failed - latest Buildkite metrics bucket', value: row.jobs_failed === null || row.jobs_failed === undefined ? '-' : integer(row.jobs_failed)},
        {label: 'Latest metrics bucket observed', value: value(nativeObservedAt)},
        {label: 'Native metrics provenance', value: nativeSources.length ? nativeSources.join(', ') : '-'},
        {label: 'p50 primary / fallback', value: duration(waitValue(row, 'p50')) + (p50Source ? ' - ' + p50Source : '')},
        {label: 'p95 primary / fallback', value: duration(waitValue(row, 'p95')) + (p95Source ? ' - ' + p95Source : '')},
        {label: 'p50 reconstructed sample', value: sampledP50 === null ? 'Not measured' : duration(sampledP50)},
        {label: 'p95 reconstructed sample', value: sampledP95 === null ? 'Not measured' : duration(sampledP95)},
        {label: 'p99 scheduled sample', value: p99Value === null || p99Value === undefined ? 'Not measured' : duration(p99Value) + (sampleCount !== null ? ' - n=' + integer(sampleCount) : '')},
        {label: 'p99 source', value: value(waitSourceDetail(row, 'p99'))},
        {label: 'Scheduled sample coverage', value: sampleCoverage},
        {label: '4h+ waiting jobs excluded from sample', value: integer(row.zombie_waiting)},
        {label: 'Count source', value: row.count_source},
      ],
      sources: row.queue_url || row.url
        ? [{label: 'Open Buildkite queue', url: row.queue_url || row.url}, {label: 'Open published queue snapshot', url: SOURCE_ASSETS.queueSection}]
        : [{label: 'Open published queue snapshot', url: SOURCE_ASSETS.queueSection}],
      content: content,
    });
  }

  function buildUrl(pipeline, number) {
    if (!pipeline || number === null || number === undefined || number === '') return '';
    return 'https://buildkite.com/vllm/' + encodeURIComponent(pipeline) + '/builds/' + encodeURIComponent(number);
  }

  function signedInteger(number) {
    if (!Number.isFinite(Number(number))) return '-';
    const value = Number(number);
    return (value > 0 ? '+' : '') + integer(value);
  }

  function waitSourceDetail(row, metric) {
    const family = waitSource(row || {}, metric);
    if (!family) return null;
    const key = String(family).toLowerCase();
    const provider = key === 'official_wait' ? row.official_wait_source : key === 'sample_wait' ? row.sample_wait_source : null;
    return provider && provider !== family ? family + ' - ' + provider : family;
  }

  function sampleWaitValue(row, metric) {
    const measured = ((row || {}).sample_wait || {})[metric];
    return measured !== null && measured !== undefined && Number.isFinite(Number(measured)) ? Number(measured) : null;
  }

  function waitValue(row, metric) {
    const nativeValue = officialWaitValue(row, metric);
    if ((metric === 'p50' || metric === 'p95') && nativeValue !== null) return nativeValue;
    const sampledValue = sampleWaitValue(row, metric);
    if (metric === 'p99' && sampledValue !== null) return sampledValue;
    const current = (row || {}).current_wait || {};
    if (current[metric] && current[metric].value !== undefined) return current[metric].value;
    if (metric === 'p99' && (row || {}).p99_wait_source !== 'sample_wait') return null;
    return (row || {})[metric + '_wait'];
  }

  function waitSampleCount(row) {
    const nested = (row || {}).sample_wait || {};
    const count = row && row.wait_sample_count !== undefined ? row.wait_sample_count : nested.count;
    return Number.isFinite(Number(count)) ? Number(count) : null;
  }

  function hasAgentMeasurement(row) {
    if (row.connected_agents === null || row.connected_agents === undefined || row.connected_agents === '' || !Number.isFinite(Number(row.connected_agents))) return false;
    if (row.connected_agents_available !== undefined) return row.connected_agents_available === true;
    const source = String(row.connected_agents_source || row.agent_count_source || row.metrics_source || row.count_source || '').toLowerCase();
    return !!source && !['active_jobs', 'webhook', 'job_scan', 'none', 'unknown'].includes(source);
  }

  function officialWaitValue(row, metric) {
    const measured = ((row || {}).official_wait || {})[metric];
    return measured !== null && measured !== undefined && Number.isFinite(Number(measured)) ? Number(measured) : null;
  }

  function waitSource(row, metric) {
    if ((metric === 'p50' || metric === 'p95') && officialWaitValue(row, metric) !== null) return 'official_wait';
    if (metric === 'p99' && sampleWaitValue(row, metric) !== null) return 'sample_wait';
    const current = (row || {}).current_wait || {};
    const source = (current[metric] && current[metric].source) || (row || {})[metric + '_wait_source'] || (row || {}).wait_source || null;
    return source && !['none', 'unavailable', 'unknown'].includes(String(source).toLowerCase()) ? source : null;
  }

  async function render(tabId, force) {
    if (!OWNED_TABS.has(tabId)) return false;
    syncRouteState(tabId);
    const host = ownedHost(tabId);
    if (!host) return false;
    const token = String(Date.now()) + Math.random();
    host.dataset.renderToken = token;
    host.dataset.renderState = 'loading';
    clear(host);
    pruneInactiveCharts();
    host.append(n('div', 'ops-loading', 'Loading operational data...'));
    try {
      if (tabId === 'ci-analytics' && state.analyticsView === 'dns'
        && queueDnsRefreshDue()) {
        invalidateDnsData();
      }
      const ops = await loadOperations(tabId);
      if (host.dataset.renderToken !== token) return false;
      clear(host);
      setFreshness(ops);
      if (tabId === 'projects') await renderHome(host, ops);
      else if (tabId === 'ci-health') await renderHealth(host, ops);
      else if (tabId === 'ci-analytics') await renderAnalytics(host, ops);
      else if (tabId === 'ci-perf-eval') await renderPerf(host, ops);
      else if (tabId === 'ci-omni') await renderOmni(host, ops);
      if (host.dataset.renderToken !== token) return false;
      host.dataset.renderState = 'ready';
      notifyFirstRenderSettled();
      return true;
    } catch (error) {
      if (host.dataset.renderToken !== token) return false;
      clear(host);
      const retry = button('Retry', function () {
        cache.clear();
        operationsManifestPromise = null;
        invalidateDnsData();
        render(tabId, true);
      }, true);
      add(host, [pageHeader('AMD CI Operations', 'The requested operational data could not be loaded.', null, retry), n('div', 'ops-error', error.message || String(error))]);
      host.dataset.renderState = 'error';
      if (typeof window.__recordBootIssue === 'function') {
        window.__recordBootIssue('ops-v2 render', tabId + ': ' + (error.message || String(error)));
      }
      console.error('Ops v2 render failed:', error);
      notifyFirstRenderSettled();
      return false;
    }
  }

  function invalidateDnsData() {
    cache.delete(SOURCE_ASSETS.queueDns);
    cache.delete(SOURCE_ASSETS.queueDnsFallback);
    queueDnsPreferredCandidate = null;
    queueDnsFetchGeneration += 1;
  }

  function queueDnsRefreshDue(nowMs) {
    const parsed = Number(nowMs);
    const current = Number.isFinite(parsed) ? parsed : Date.now();
    return current - lastDnsRefreshAt >= DNS_AUTO_REFRESH_MS;
  }

  async function refreshDnsData() {
    if (activeTab() !== 'ci-analytics' || state.analyticsView !== 'dns' || document.visibilityState === 'hidden') return;
    invalidateDnsData();
    if (!await render('ci-analytics', true)) throw new Error('DNS refresh did not render successfully');
  }

  window.OpsV2 = {
    render: render,
    refreshDns: refreshDnsData,
    renderOwnership: renderOwnership,
    state: state,
    openTestGroupHistory: openTestGroupHistory,
    loadSections: function (names) { return loadOperationSections(null, names || []); },
  };
  if (window.__OPS_V2_TEST__) {
    window.OpsV2Test = {
      currentTestGroupParity: currentTestGroupParity,
      nightlyForCohort: nightlyForCohort,
      latencyComparison: latencyComparison,
      latencyMetric: latencyMetric,
      exactLatencyJobUrl: exactLatencyJobUrl,
      matrixHealthPolicy: matrixHealthPolicy,
      populationSemantics: populationSemantics,
      observedCountLabel: observedCountLabel,
      bestHardwareMatrixContract: bestHardwareMatrixContract,
      matrixHealthCollection: matrixHealthCollection,
      matrixGroupEvidence: matrixGroupEvidence,
      nightlyFailureMovement: nightlyFailureMovement,
      nightlyFailureCount: nightlyFailureCount,
      nightlyBuildEvidence: nightlyBuildEvidence,
      amdNightlyMovement: amdNightlyMovement,
      amdNightlyPresentation: amdNightlyPresentation,
      ciHealthPublicationRetentionMessage: ciHealthPublicationRetentionMessage,
      omniMappingWindow: omniMappingWindow,
      omniMappingBuckets: omniMappingBuckets,
      omniMappingTotals: omniMappingTotals,
      omniHistoryPoints: omniHistoryPoints,
      omniWindowPoints: omniWindowPoints,
      omniAgeBand: omniAgeBand,
      omniDailyRows: omniDailyRows,
      reliabilityPublicationState: reliabilityPublicationState,
      groupPublicationHistoryComplete: groupPublicationHistoryComplete,
      agentSourceHistoryComplete: agentSourceHistoryComplete,
      agentPipelineScopeLabel: agentPipelineScopeLabel,
      amdHealthPublicationState: amdHealthPublicationState,
      agentFailureAccountingCounts: agentFailureAccountingCounts,
      agentAggregateRunCounts: agentAggregateRunCounts,
      agentAggregateFailureCounts: agentAggregateFailureCounts,
      isCanonicalAmdQueue: isCanonicalAmdQueue,
      queueDnsPayloadValid: queueDnsPayloadValid,
      compareQueueDnsCandidates: compareQueueDnsCandidates,
      queueDnsWithTimeout: queueDnsWithTimeout,
      loadQueueDns: loadQueueDns,
      invalidateDnsData: invalidateDnsData,
      queueDnsLastRefreshAt: function () { return lastDnsRefreshAt; },
      queueDnsRefreshDue: queueDnsRefreshDue,
      queueDnsOutcomeCounts: queueDnsOutcomeCounts,
      queueDnsWindow: queueDnsWindow,
      queueDnsCoverage: queueDnsCoverage,
      queueDnsFreshness: queueDnsFreshness,
      queueDnsScope: queueDnsScope,
      queueDnsMatchesPublishedScope: queueDnsMatchesPublishedScope,
      queueDnsNodeRows: queueDnsNodeRows,
      queueDnsQueueRows: queueDnsQueueRows,
      queueDnsEvidenceUrl: queueDnsEvidenceUrl,
      queueDnsEvidenceMetricValid: queueDnsEvidenceMetricValid,
      queueDnsEvidenceItemValid: queueDnsEvidenceItemValid,
      queueDnsEvidenceWindowRow: queueDnsEvidenceWindowRow,
      queueDnsEvidenceForNode: queueDnsEvidenceForNode,
      queueDnsNodeOutcomes: queueDnsNodeOutcomes,
      queueDnsOutcomePresentation: queueDnsOutcomePresentation,
      queueDnsDisplayCount: queueDnsDisplayCount,
    };
  }

  function activeTab() {
    const panelEl = document.querySelector('.tab-panel.active');
    return panelEl && panelEl.id ? panelEl.id.replace(/^tab-/, '') : 'projects';
  }

  document.addEventListener('DOMContentLoaded', function () {
    render(activeTab());
    window.setInterval(function () {
      refreshDnsData().catch(function (error) {
        console.error('DNS auto-refresh failed:', error);
      });
    }, DNS_AUTO_REFRESH_MS);
    document.addEventListener('visibilitychange', function () {
      if (
        document.visibilityState === 'visible'
        && activeTab() === 'ci-analytics'
        && state.analyticsView === 'dns'
        && queueDnsRefreshDue()
      ) {
        refreshDnsData().catch(function (error) {
          console.error('DNS visibility refresh failed:', error);
        });
      }
    });
  });
})();
