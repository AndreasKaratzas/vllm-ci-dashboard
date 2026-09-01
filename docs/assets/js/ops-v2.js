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
  const QUEUE_AUTO_REFRESH_MS = 5 * 60 * 1000;
  const DNS_AUTO_REFRESH_MS = 5 * 60 * 1000;
  const OPS_SNAPSHOT_MAX_AGE_MS = 3 * 60 * 60 * 1000;
  const QUEUE_LIVE_BASE = 'https://raw.githubusercontent.com/AndreasKaratzas/vllm-ci-dashboard/queue-data/data/vllm/ci/';
  const QUEUE_LIFECYCLE_LIVE_BASE = 'https://raw.githubusercontent.com/AndreasKaratzas/vllm-ci-dashboard/queue-lifecycle-data/data/vllm/ci/';
  const QUEUE_DNS_LIVE_BASE = 'https://raw.githubusercontent.com/AndreasKaratzas/vllm-ci-dashboard/dns-health-data/data/vllm/ci/';
  let operationsManifestPromise = null;
  let comparisonRetryEvidencePromise = null;
  let chartLibraryPromise = null;
  let lastQueueRefreshAt = 0;
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
    comparisonRetryEvidence: 'data/vllm/ci/operations_v2/comparison_retry_evidence.json',
    gating: 'data/vllm/ci/operations_v2/gating.json',
    testGroupParity: 'data/vllm/ci/operations_v2/test_group_parity.json',
    trajectory: 'data/vllm/ci/operations_v2/trajectory.json',
    omni: 'data/vllm/ci/operations_v2/omni.json',
    queueSection: QUEUE_LIVE_BASE + 'operations_v2/queue.json',
    queueChartHistory: QUEUE_LIVE_BASE + 'queue_history_chart.json',
    queueChartHistoryFallback: 'data/vllm/ci/queue_history_chart.json',
    queueHistory: QUEUE_LIVE_BASE + 'queue_timeseries.jsonl',
    queueHistoryFallback: 'data/vllm/ci/queue_timeseries.jsonl',
    queueLifecycle: QUEUE_LIFECYCLE_LIVE_BASE + 'queue_lifecycle.json',
    queueLifecycleFallback: 'data/vllm/ci/queue_lifecycle.json',
    queueDns: QUEUE_DNS_LIVE_BASE + 'dns_failures.json',
    queueDnsFallback: 'data/vllm/ci/dns_failures.json',
    workloadMapping: 'data/vllm/ci/workload_mapping.json',
    perf: 'data/vllm/perf_eval/perf_eval.json',
    amdPipeline: 'https://buildkite.com/vllm/amd-ci',
    upstreamScheduledGating: 'data/vllm/ci/operations_v2/gating.json',
    upstreamGatingCapacity: 'data/vllm/ci/capacity_monitor.json',
    upstreamScheduledBuilds: 'https://buildkite.com/vllm/ci/builds?query=full+ci+run+-+',
  };
  const CHART_LIBRARY_URL = 'https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js';
  const state = {
    healthView: 'overview',
    healthCoverageSort: 'platform',
    healthParityState: 'action',
    healthParityArea: 'all',
    analyticsView: 'groups',
    analyticsPipeline: 'amd-ci',
    homeWork: 'issues',
    healthSearch: '',
    healthPlan: 'upstream_only',
    healthResult: 'attention',
    analyticsSearch: '',
    analyticsGroupId: '',
    analyticsGroupCohort: 'main',
    analyticsAmdFilter: 'attention',
    analyticsWindow: '30d',
    analyticsDnsScope: 'amd',
    analyticsDnsWindow: '24h',
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
    trajectoryView: 'workload',
    trajectoryWorkload: 'all',
    trajectoryHardware: 'all',
    trajectorySearch: '',
    capacityMode: 'groups',
    capacityBaseline: 'peak',
    capacityTrafficMode: 'burst',
    capacityPlacement: '',
    capacityGroups: '160',
    capacityJobs: '196',
    capacityQueue: 'amd_mi300_1',
    capacityQueueGroups: '1',
    capacityParallel: '1',
    capacityDuration: '30',
    capacitySuites: '1',
    capacitySuitesPerHour: '1',
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
      'ops_health_view', 'ops_health_sort', 'ops_health_result',
      'ops_health_parity_state', 'ops_health_parity_area',
    ]),
    'ci-analytics': new Set([
      'ops_analytics_view', 'ops_analytics_pipeline', 'ops_analytics_search',
      'ops_analytics_group', 'ops_analytics_cohort', 'ops_analytics_amd_filter',
      'ops_analytics_window', 'ops_analytics_dns_scope', 'ops_analytics_dns_window',
      'ops_agent_window', 'ops_agent_gpu', 'ops_agent_node',
      'ops_agent_cofail', 'ops_agent_excl_cancel', 'ops_agent_nightly',
      'ops_agent_signal', 'ops_detail',
    ]),
    'ci-queue': new Set(['ops_queue_view', 'ops_queue_range', 'ops_queue_scope', 'ops_queue_history_queue', 'ops_detail']),
    'ci-hotness': new Set([
      'ops_trajectory_view', 'ops_trajectory_window',
      'ops_capacity_mode', 'ops_capacity_baseline', 'ops_capacity_groups',
      'ops_capacity_jobs', 'ops_capacity_queue', 'ops_capacity_queue_groups',
      'ops_capacity_parallel', 'ops_capacity_duration', 'ops_capacity_suites',
      'ops_capacity_traffic', 'ops_capacity_suites_per_hour',
      'ops_capacity_placement',
      'ops_detail',
    ]),
    'ci-omni': new Set(['ops_omni_mapping_range', 'ops_omni_range', 'ops_omni_age', 'ops_detail']),
    'ci-perf-eval': new Set(['ops_perf_view', 'ops_perf_model', 'ops_perf_device', 'ops_detail']),
  };
  const ROUTE_DEFAULTS = {
    health_view: 'overview',
    health_sort: 'platform',
    health_result: 'attention',
    health_parity_state: 'action',
    health_parity_area: 'all',
    health_definition_filter: 'upstream_only',
    health_definition_search: '',
    analytics_view: 'groups',
    analytics_pipeline: 'amd-ci',
    analytics_search: '',
    analytics_group: '',
    analytics_cohort: 'main',
    analytics_amd_filter: 'attention',
    analytics_window: '30d',
    analytics_dns_scope: 'amd',
    analytics_dns_window: '24h',
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
    trajectory_view: 'workload',
    capacity_mode: 'groups',
    capacity_baseline: 'peak',
    capacity_traffic: 'burst',
    capacity_placement: '',
    capacity_groups: '160',
    capacity_jobs: '196',
    capacity_queue: 'amd_mi300_1',
    capacity_queue_groups: '1',
    capacity_parallel: '1',
    capacity_duration: '30',
    capacity_suites: '1',
    capacity_suites_per_hour: '1',
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

  function isAmdQueue(queue) {
    const name = String(queue || '').toLowerCase();
    return (name === 'amd-cpu' || name.startsWith('amd_')) && !isRetiredQueue(name);
  }

  function isCanonicalAmdQueue(queue) {
    const name = String(queue || '').trim().toLowerCase();
    return /^amd_mi(?:250|300|355)_(?:1|2|4|8)$/.test(name);
  }

  function queueMatchesScope(queue, requestedScope) {
    const scope = requestedScope || state.queueScope;
    if (isRetiredQueue(queue)) return false;
    if (scope === 'all') return true;
    if (scope === 'canonical') return isCanonicalAmdQueue(queue);
    return isAmdQueue(queue);
  }

  function queueScopeLabel(requestedScope, combined) {
    const scope = requestedScope || state.queueScope;
    const label = scope === 'canonical' ? 'Canonical AMD queues'
      : scope === 'amd' ? 'All AMD queues'
        : 'All queues';
    return combined ? label + ' combined' : label;
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
        ['healthView', 'health_view', ['overview', 'parity', 'targets', 'coverage']],
        ['healthCoverageSort', 'health_sort', ['platform', 'name', 'area']],
        ['healthResult', 'health_result', ['attention', 'non_passing', 'partial', 'passing', 'all']],
        ['healthParityState', 'health_parity_state', ['all', 'existing', 'unsupported', 'action']],
        ['healthParityArea', 'health_parity_area', null],
      ],
      'ci-analytics': [
        ['analyticsView', 'analytics_view', ['groups', 'flakes', 'nightlies', 'retries', 'latency', 'dns', 'agent-health']],
        ['analyticsPipeline', 'analytics_pipeline', ['ci', 'amd-ci']],
        ['analyticsSearch', 'analytics_search', null],
        ['analyticsGroupId', 'analytics_group', null],
        ['analyticsGroupCohort', 'analytics_cohort', ['main', 'nightly']],
        ['analyticsAmdFilter', 'analytics_amd_filter', ['attention', 'all', 'passing', 'incident', 'missing', 'mixed']],
        ['analyticsWindow', 'analytics_window', ['30d']],
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
      'ci-queue': [
        ['queueView', 'queue_view', ['current', 'lifecycle', 'history', 'jobs']],
        ['queueRange', 'queue_range', ['24h', '7d', '30d']],
        ['queueScope', 'queue_scope', ['canonical', 'amd', 'all']],
        ['queueHistoryQueue', 'queue_history_queue', null],
      ],
      'ci-hotness': [
        ['trajectoryView', 'trajectory_view', ['workload', 'capacity']],
        ['trajectoryWindow', 'trajectory_window', ['24h', '72h', '7d', '30d']],
        ['capacityMode', 'capacity_mode', ['groups', 'jobs', 'queue']],
        ['capacityBaseline', 'capacity_baseline', ['current', 'typical', 'peak', 'stress']],
        ['capacityTrafficMode', 'capacity_traffic', ['burst', 'sustained']],
        ['capacityPlacement', 'capacity_placement', ['mi355_preferred', 'current_definition_precedence']],
        ['capacityGroups', 'capacity_groups', null],
        ['capacityJobs', 'capacity_jobs', null],
        ['capacityQueue', 'capacity_queue', null],
        ['capacityQueueGroups', 'capacity_queue_groups', null],
        ['capacityParallel', 'capacity_parallel', null],
        ['capacityDuration', 'capacity_duration', null],
        ['capacitySuites', 'capacity_suites', null],
        ['capacitySuitesPerHour', 'capacity_suites_per_hour', null],
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

  function migrateLegacyQueueDnsRoute(tabId) {
    if (tabId !== 'ci-queue' || queryValue('queue_view') !== 'dns') return false;
    try {
      const url = new URL(window.location.href);
      const oldWindow = String(url.searchParams.get('ops_queue_dns_window') || '24h');
      const dnsWindow = ['1h', '3h', '12h', '24h', '72h', '168h', '720h'].includes(oldWindow)
        ? oldWindow
        : '24h';
      const dnsScope = url.searchParams.get('ops_queue_scope') === 'canonical' ? 'canonical' : 'amd';
      Array.from(url.searchParams.keys()).forEach(function (key) {
        if (key.startsWith('ops_queue_')) url.searchParams.delete(key);
      });
      url.searchParams.set('ops_analytics_view', 'dns');
      if (dnsWindow === '24h') url.searchParams.delete('ops_analytics_dns_window');
      else url.searchParams.set('ops_analytics_dns_window', dnsWindow);
      if (dnsScope === 'amd') url.searchParams.delete('ops_analytics_dns_scope');
      else url.searchParams.set('ops_analytics_dns_scope', dnsScope);
      url.hash = 'ci-analytics';
      window.history.replaceState(null, '', url.pathname + url.search + url.hash);
      if (window.__dashboardNav && typeof window.__dashboardNav.switchTab === 'function') {
        window.__dashboardNav.switchTab('ci-analytics', {updateHash: false});
      } else {
        window.location.hash = 'ci-analytics';
      }
      return true;
    } catch (_) {
      return false;
    }
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
      content.append(n('div', 'ops-empty', 'The aggregate is available, but this snapshot predates per-run evidence. Regenerate operations_v2.json.gz to populate exact links.'));
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
        {label: 'Soft failure', state: 'soft', color: '#e3a63a'},
        {label: 'Hard failure', state: 'hard', color: '#e06464'},
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
      [['all', 'All results'], ['passing', 'Passing only'], ['incident', 'Failures only']].forEach(function (pair) {
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

  const reliabilityCatalogCache = new WeakMap();
  const reliabilityCatalogIndexCache = new WeakMap();

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

  function groupReliabilityRowsByIds(reliability, references, fallbackName) {
    const ids = (references || []).filter(function (id) { return id !== null && id !== undefined && id !== ''; }).map(String);
    if (ids.length) {
      const byId = reliabilityCatalogIndex(reliability).byId;
      return ids.map(function (id) { return byId.get(id); }).filter(Boolean);
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
    const content = n('div', 'ops-stack');
    if ((reference || name) && !reliabilityCatalog(reliability).length) {
      const loadHistory = button('Load 30-day run history', function () {
        loadHistory.disabled = true;
        loadHistory.textContent = 'Loading run history…';
        loadOperationSections(ops, ['reliability']).then(function (expanded) {
          backOverlay();
          openGroupDetail(group, expanded, reliabilityRow);
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
    definition_parity: 'Source-definition identity',
  };

  function humanizeIdentifier(identifier) {
    return String(identifier || '').replace(/_/g, ' ').trim();
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
      same_commit: 'AMD matrix and source mapping use the same commit',
      different_commits: 'AMD matrix and source mapping use different commits',
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
    if (!reliabilityCatalog(reliability).length) {
      const loadHistory = button('Load full 30-day variant history', function () {
        loadHistory.disabled = true;
        loadHistory.textContent = 'Loading variant history…';
        loadOperationSections(ops, ['reliability']).then(function (expanded) {
          backOverlay();
          openGatingDetail(group, expanded);
        }).catch(function (error) {
          loadHistory.disabled = false;
          loadHistory.textContent = 'Retry loading variant history';
          console.error('Reviewed target reliability load failed:', error);
        });
      }, true);
      content.append(loadHistory);
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
        {label: 'Source-mapping commit', value: shortCommit(resolution.sourceCommits.definitionParity)},
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
        {label: 'Open published target data', url: SOURCE_ASSETS.upstreamScheduledGating},
      ],
      content: content,
    });
  }

  function openGatingDetailWithEvidence(group, ops) {
    openGatingDetail(group, ops);
  }

  function openGroupDetailWithEvidence(group, ops) {
    openGroupDetail(group, ops);
  }

  function definitionParityComparisonRows(parity) {
    const rows = [];
    (parity.matches || []).forEach(function (row) { rows.push(Object.assign({category: 'direct_match'}, row)); });
    (parity.inline_mirror_variants || []).forEach(function (row) { rows.push(Object.assign({category: 'inline_mirror_variant'}, row)); });
    (parity.additional_variants || []).forEach(function (row) { rows.push(Object.assign({category: 'additional_variant'}, row)); });
    (parity.amd_only || []).forEach(function (row) { rows.push(Object.assign({category: 'amd_only'}, row)); });
    (parity.nvidia_only || []).forEach(function (row) { rows.push(Object.assign({category: 'upstream_only'}, row)); });
    return rows;
  }

  function definitionParityMirrorRows(parity) {
    return (parity.mirrors || []).map(function (row) {
      return Object.assign({
        category: 'inline_mirror',
        match_method: 'inline_mirror',
        nvidia_source: row.source_file,
        nvidia_source_url: row.source_url,
      }, row, {
        inline_mirror_command_similarity: row.command_similarity,
      });
    });
  }

  function definitionParityEvidence(row) {
    const candidates = [];
    function addCandidate(score, label) {
      if (score === undefined || score === null || score === '') return;
      const numericScore = Number(score);
      if (!Number.isFinite(numericScore)) return;
      candidates.push({score: numericScore, label: label});
    }
    if (row.category === 'inline_mirror') {
      addCandidate(
        row.inline_mirror_command_similarity !== undefined
          ? row.inline_mirror_command_similarity
          : row.command_similarity,
        'inline AMD ↔ upstream'
      );
    } else if (row.category === 'inline_mirror_variant') {
      addCandidate(row.amd_route_similarity, 'standalone ↔ inline AMD');
      addCandidate(row.command_similarity, 'standalone ↔ upstream');
      addCandidate(row.inline_mirror_command_similarity, 'inline AMD ↔ upstream');
    } else if (['direct_match', 'additional_variant'].includes(row.category)) {
      addCandidate(row.command_similarity, 'standalone ↔ upstream');
    }
    const primary = candidates.slice().sort(function (a, b) {
      return a.score - b.score;
    })[0] || null;
    return {
      primarySimilarity: primary ? primary.score : null,
      evidenceLabel: primary ? primary.label : '',
      changed: candidates.some(function (candidate) {
        return candidate.score < 0.999999;
      }),
    };
  }

  function definitionParityFilter(rows, plan) {
    return (rows || []).filter(function (row) {
      if (plan === 'mirror_inventory') return row.category === 'inline_mirror';
      if (row.category === 'inline_mirror') return false;
      if (plan === 'all') return true;
      if (plan === 'amd') return ['direct_match', 'inline_mirror_variant', 'additional_variant', 'amd_only'].includes(row.category);
      if (plan === 'covered') return ['direct_match', 'inline_mirror_variant', 'additional_variant'].includes(row.category);
      if (plan === 'direct') return row.category === 'direct_match';
      if (plan === 'inline_variant') return row.category === 'inline_mirror_variant';
      if (plan === 'additional_variant') return row.category === 'additional_variant';
      if (plan === 'twins') return row.match_method === 'command_twin';
      if (plan === 'changed') {
        if (!['direct_match', 'inline_mirror_variant', 'additional_variant'].includes(row.category)) return false;
        return definitionParityEvidence(row).changed;
      }
      if (plan === 'unlinked') return ['amd_only', 'upstream_only'].includes(row.category);
      if (plan === 'amd_only') return row.category === 'amd_only';
      if (plan === 'upstream_only') return row.category === 'upstream_only';
      return true;
    });
  }

  function definitionParityPresentation(row) {
    const evidence = definitionParityEvidence(row);
    if (row.category === 'inline_mirror') {
      return {
        label: 'Inline mirror · ' + (row.commands_overridden ? 'override' : 'inherits'),
        tone: 'is-info',
        primarySimilarity: evidence.primarySimilarity,
        evidenceLabel: evidence.evidenceLabel,
      };
    }
    if (row.category === 'inline_mirror_variant') {
      const relationship = row.mirror_relationship === 'effective_command_duplicate'
        ? 'Inline mirror duplicate'
        : row.mirror_relationship === 'hardware_variant'
          ? 'Inline mirror hardware variant'
          : 'Inline mirror command variant';
      return {
        label: relationship,
        tone: 'is-info',
        primarySimilarity: evidence.primarySimilarity,
        evidenceLabel: evidence.evidenceLabel,
      };
    }
    if (row.category === 'additional_variant') {
      return {
        label: row.variant_relationship === 'additional_hardware_variant'
          ? 'Additional AMD hardware variant'
          : 'Additional AMD variant',
        tone: 'is-info',
        primarySimilarity: evidence.primarySimilarity,
        evidenceLabel: evidence.evidenceLabel,
      };
    }
    if (row.category === 'amd_only') {
      return {label: 'AMD-only standalone', tone: 'is-warning', primarySimilarity: null, evidenceLabel: ''};
    }
    if (row.category === 'upstream_only') {
      return {label: 'Upstream-only', tone: 'is-warning', primarySimilarity: null, evidenceLabel: ''};
    }
    return {
      label: row.match_method === 'command_twin' ? 'Direct command twin' : 'Direct identity',
      tone: row.match_method === 'command_twin' ? 'is-info' : 'is-success',
      primarySimilarity: evidence.primarySimilarity,
      evidenceLabel: evidence.evidenceLabel,
    };
  }

  function openDefinitionDetail(row, definitionParity) {
    const content = n('div', 'ops-stack');
    function commandPanel(title, commands) {
      const pre = n('pre', 'ops-code-block');
      pre.textContent = (commands || []).length ? commands.join('\n') : 'No command list is present in this definition.';
      return panel(title, integer((commands || []).length) + ' normalized command lines', pre);
    }
    const presentation = definitionParityPresentation(row);
    if (row.amd_commands || row.category === 'amd_only') {
      content.append(commandPanel(
        row.category === 'inline_mirror' ? 'Inline AMD mirror commands' : 'Standalone AMD command definition',
        row.amd_commands || (row.category === 'amd_only' ? row.commands : [])
      ));
    }
    if (row.category === 'inline_mirror_variant') {
      content.append(commandPanel('Inline AMD mirror commands', row.inline_mirror_amd_commands || []));
    }
    if (row.nvidia_commands || row.category === 'upstream_only') {
      content.append(commandPanel('Upstream command definition', row.nvidia_commands || (row.category === 'upstream_only' ? row.commands : [])));
    }
    const source = (definitionParity || {}).source || {};
    const description = row.category === 'inline_mirror_variant'
      ? 'This standalone test-amd.yaml definition is linked to an upstream definition that also declares mirror.amd. It is covered, not an AMD-only gap.'
      : row.category === 'additional_variant'
        ? row.variant_relationship === 'additional_hardware_variant'
          ? 'This standalone test-amd.yaml definition belongs to the same canonical test family as an upstream definition already used by a direct match, but its explicit reference hardware differs. It is an additional AMD hardware variant, not an AMD-only gap.'
          : 'This standalone test-amd.yaml definition shares an exact compatible identity with an upstream definition already used by another direct match. It is an additional AMD execution variant, not an AMD-only gap.'
        : row.category === 'inline_mirror'
          ? 'This upstream test_areas definition declares an inline AMD execution route. The mirror inventory is separate from the standalone test-amd.yaml denominator.'
          : row.category === 'direct_match'
            ? (row.match_method === 'command_twin'
              ? 'The titles differ, but this unique direct pair has an exact normalized command match and passed the platform-neutral title threshold.'
              : 'The standalone AMD and upstream YAML definitions share the same normalized identity. Command similarity is reported separately.')
            : row.category === 'amd_only'
              ? 'No compatible upstream identity, inline AMD mirror, or exact-command twin was found for this standalone AMD definition.'
              : 'No standalone test-amd.yaml definition or inline AMD mirror was found for this upstream definition.';
    openDetailDrawer({
      id: 'definition-' + (row.identity_key || row.amd_label || row.nvidia_label || row.label),
      title: row.amd_label || row.label || row.nvidia_label || 'CI definition',
      subtitle: 'Commit-pinned vLLM CI source comparison',
      description: description,
      fields: [
        {label: 'AMD definition', value: row.category === 'inline_mirror' ? 'mirror.amd' : row.amd_label || (row.category === 'amd_only' ? row.label : null)},
        {label: 'Upstream definition', value: row.nvidia_label || (row.category === 'upstream_only' ? row.label : null)},
        {label: 'Relationship', value: presentation.label},
        {label: 'Command evidence', value: presentation.primarySimilarity !== undefined && presentation.primarySimilarity !== null ? (Number(presentation.primarySimilarity) * 100).toFixed(1) + '% ' + presentation.evidenceLabel : null},
        {label: 'Standalone ↔ upstream', value: row.category !== 'inline_mirror' && row.command_similarity !== undefined ? (Number(row.command_similarity) * 100).toFixed(1) + '%' : null},
        {label: 'Standalone ↔ inline AMD', value: row.amd_route_similarity !== undefined ? (Number(row.amd_route_similarity) * 100).toFixed(1) + '%' : null},
        {label: 'Inline AMD ↔ upstream', value: row.inline_mirror_command_similarity !== undefined ? (Number(row.inline_mirror_command_similarity) * 100).toFixed(1) + '%' : null},
        {label: 'Inline mirror commands', value: row.inline_mirror_commands_overridden === true || row.commands_overridden === true ? 'Overridden for AMD' : row.category === 'inline_mirror_variant' || row.category === 'inline_mirror' ? 'Inherited from upstream' : null},
        {label: 'Inline AMD device', value: row.inline_mirror_amd_device || row.amd_device},
        {label: 'AMD agent pools', value: (row.amd_member_agent_pools || []).join(', ')},
        {label: 'AMD definition ID', value: row.amd_definition_id},
        {label: 'Upstream definition ID', value: row.nvidia_definition_id},
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
    const failureMovement = nightlyFailureMovement(build);
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
    const rows = [];
    if (failureMovement && failureMovement.available !== false) {
      [
        ['New failure', failureMovement.new || []],
        ['Recurring failure', failureMovement.recurring || []],
        ['Fixed', failureMovement.fixed || []],
      ].forEach(function (bucket) {
        bucket[1].forEach(function (group) { rows.push(Object.assign({lifecycle: bucket[0]}, group)); });
      });
    } else {
      content.append(n('div', 'ops-evidence-note is-warning', 'Failure movement is unavailable for this build. Raw build outcomes remain visible below.'));
    }
    if (rows.length) {
      const transitionColumns = [
      {label: 'Job variant', sticky: true, render: function (row) { return externalLink(row.display_name || row.name, exactPipelineEvidenceUrl(row, sourcePipeline)); }},
      {label: 'Change', render: function (row) { return linkedBadge(row.lifecycle, exactPipelineEvidenceUrl(row, sourcePipeline), null, row.lifecycle === 'New failure' ? 'is-danger' : row.lifecycle === 'Fixed' ? 'is-success' : 'is-warning'); }},
      {label: 'Queue', render: function (row) { return n('span', 'ops-mono', value(row.queue)); }},
      ];
      content.append(compactTablePanel('Failure movement', integer(rows.length) + ' observed changes', transitionColumns, rows, {
        id: 'build-transition-browser',
        limit: 30,
        browserSubtitle: 'New failures, recurring failures, and fixes observed in this exact Buildkite nightly comparison',
        searchPlaceholder: 'Filter job variant, change, or queue',
        searchText: function (row) { return [row.display_name, row.name, row.lifecycle, row.queue].join(' '); },
      }));
    }
    openDetailDrawer({
      id: 'build-' + value(build.number),
      title: title || (build.number ? 'AMD build #' + build.number : 'AMD build'),
      subtitle: 'Build result and failure movement evidence',
      fields: [
        {label: 'State', value: value(build.state)},
        {label: 'Started', value: shortDate(build.created_at)},
        {label: 'Job variants observed', value: integer(build.total_groups)},
        {label: 'Test signal', value: build.has_test_results === false ? 'Unavailable' : 'Observed'},
        {label: 'Dependency-blocked test jobs', value: build.test_jobs_blocked ? integer(build.test_jobs_blocked) : null},
        {label: 'New failure / recurring failure / fixed', value: failureMovement && failureMovement.available !== false ? integer((failureMovement.new || []).length) + ' / ' + integer((failureMovement.recurring || []).length) + ' / ' + integer((failureMovement.fixed || []).length) : 'Unavailable'},
      ],
      sources: exactPipelineBuildUrl(build, sourcePipeline) ? [{label: 'Open Buildkite build', url: exactPipelineBuildUrl(build, sourcePipeline)}] : [],
      content: content,
    });
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

  async function fetchJSONL(path) {
    const key = 'jsonl:' + path;
    if (!cache.has(key)) {
      memoizedFetch(key, fetchDecoded(path, async function (response) {
        const text = await response.text();
        return text.split(/\r?\n/).filter(Boolean).map(function (line, index) {
          try {
            return JSON.parse(line);
          } catch (_) {
            throw new Error(path + ' contains invalid JSONL at line ' + (index + 1));
          }
        });
      }));
    }
    return cache.get(key);
  }

  function queueTimestamp(value) {
    const parsed = new Date(value || '').getTime();
    return Number.isFinite(parsed) ? parsed : -Infinity;
  }

  function queueSectionTimestamp(section) {
    return queueTimestamp((((section || {}).queue || {}).snapshot || {}).ts);
  }

  function decodeQueueChartHistory(payload) {
    if (!payload || payload.schema_version !== 1 || !Array.isArray(payload.points)) return [];
    const names = payload.queue_names || [];
    const sources = payload.wait_sources || [null];
    const providers = payload.wait_providers || [null];
    return payload.points.map(function (point) {
      const queues = {};
      (point[1] || []).forEach(function (values, index) {
        if (!Array.isArray(values) || !names[index]) return;
        const row = {
          waiting: Number(values[0] || 0),
          running: Number(values[1] || 0),
          p50_wait: values[2],
          p95_wait: values[3],
          p99_wait: values[4],
          p50_wait_source: sources[values[5]] || null,
          p95_wait_source: sources[values[6]] || null,
          p99_wait_source: sources[values[7]] || null,
          official_wait_source: providers[values[8]] || null,
          sample_wait_source: providers[values[9]] || null,
          wait_sample_count: values[10],
          wait_sample_expected_count: values[11],
          wait_sample_complete: values[12] === null || values[12] === undefined ? null : values[12] === 1,
        };
        if (Array.isArray(values[13])) {
          row.archive_wait_peaks = {};
          ['p50', 'p95', 'p99'].forEach(function (metric, peakIndex) {
            const peak = values[13][peakIndex];
            if (!Array.isArray(peak)) return;
            row.archive_wait_peaks[metric] = {
              value: peak[0],
              source: sources[peak[1]] || null,
              provider: providers[peak[2]] || null,
              sample_count: peak[3],
              observed_at: peak[4],
              sample_expected: peak[5],
              sample_complete: peak[6],
            };
          });
        }
        if (Array.isArray(values[14])) {
          row.official_wait = {p50: values[14][0], p95: values[14][1], max: values[14][2]};
        }
        if (Array.isArray(values[15])) {
          row.sample_wait = {available: true, count: values[10], p50: values[15][0], p95: values[15][1], p99: values[15][2]};
        }
        if (Array.isArray(values[16])) {
          row.archive_sample_wait_peaks = {};
          ['p50', 'p95', 'p99'].forEach(function (metric, peakIndex) {
            const peak = values[16][peakIndex];
            if (!Array.isArray(peak)) return;
            row.archive_sample_wait_peaks[metric] = {
              value: peak[0],
              source: sources[peak[1]] || 'sample_wait',
              provider: providers[peak[2]] || null,
              sample_count: peak[3],
              observed_at: peak[4],
              sample_expected: peak[5],
              sample_complete: peak[6],
            };
          });
        }
        if (values[17] === 1) row.history_observation_only = true;
        queues[names[index]] = row;
      });
      return {ts: point[0], queues: queues};
    }).filter(function (snapshot) { return queueTimestamp(snapshot.ts) > -Infinity; });
  }

  function mergeQueueHistory(rows) {
    const byTimestamp = new Map();
    (rows || []).forEach(function (snapshot) {
      if (snapshot && queueTimestamp(snapshot.ts) > -Infinity && snapshot.queues) {
        const key = String(snapshot.ts);
        const previous = byTimestamp.get(key);
        if (!previous) {
          byTimestamp.set(key, snapshot);
          return;
        }
        const queues = Object.assign({}, previous.queues || {});
        Object.entries(snapshot.queues || {}).forEach(function (entry) {
          const oldRow = queues[entry[0]] || {};
          const newRow = entry[1] || {};
          queues[entry[0]] = Object.assign({}, oldRow, newRow, {
            archive_wait_peaks: Object.assign({}, oldRow.archive_wait_peaks || {}, newRow.archive_wait_peaks || {}),
            archive_sample_wait_peaks: Object.assign({}, oldRow.archive_sample_wait_peaks || {}, newRow.archive_sample_wait_peaks || {}),
          });
        });
        byTimestamp.set(key, Object.assign({}, previous, snapshot, {queues: queues}));
      }
    });
    return Array.from(byTimestamp.values()).sort(function (a, b) { return String(a.ts).localeCompare(String(b.ts)); });
  }

  async function loadQueueHistory(queueBlock) {
    const compactResults = await Promise.allSettled([
      fetchJSON(SOURCE_ASSETS.queueChartHistory),
      fetchJSON(SOURCE_ASSETS.queueChartHistoryFallback),
    ]);
    // Apply older payloads first, then merge newer same-timestamp rows while
    // retaining any richer hourly peak envelopes from either publication.
    const compactPayloads = compactResults.filter(function (result) { return result.status === 'fulfilled'; }).map(function (result) {
      return result.value;
    }).sort(function (a, b) {
      return queueTimestamp(a.generated_at) - queueTimestamp(b.generated_at);
    });
    const compactRows = compactPayloads.flatMap(decodeQueueChartHistory);
    const compact = mergeQueueHistory(compactRows);
    const current = queueBlock.snapshot && queueBlock.snapshot.ts ? queueBlock.snapshot : null;
    const compactLastMs = compact.length ? queueTimestamp(compact[compact.length - 1].ts) : -Infinity;
    const currentMs = current ? queueTimestamp(current.ts) : -Infinity;
    let fallback = [];
    if (!compact.length || currentMs - compactLastMs > 30 * 60 * 1000) {
      try { fallback = await fetchJSONL(SOURCE_ASSETS.queueHistoryFallback); } catch (_) {
        fallback = Array.isArray(queueBlock.history) ? queueBlock.history : [];
      }
    }
    return mergeQueueHistory([].concat(fallback, compact, current ? [current] : []));
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
      if (state.healthView === 'targets') return ['amd_test_health', 'gating'];
      if (state.healthView === 'quality') return [state.healthQualityView === 'collectors' ? 'diagnostics' : 'definition_parity'];
      if (state.healthView === 'gating') return ['definition_parity'];
      if (state.healthView === 'diagnostics') return ['diagnostics'];
      return [];
    }
    if (tabId === 'ci-analytics') {
      if (state.analyticsView === 'groups') return ['amd_test_health'];
      if (state.analyticsView === 'agent-health') return ['amd_agent_health'];
      if (state.analyticsView === 'nightlies') return ['nightly'];
      if (state.analyticsView === 'dns') return [];
      if (['flakes', 'retries', 'latency'].includes(state.analyticsView)) return ['comparison'];
      return ['reliability'];
    }
    if (tabId === 'ci-queue') return ['queue'];
    if (tabId === 'ci-hotness') return ['reliability', 'trajectory'];
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

  function loadComparisonRetryEvidence() {
    if (!comparisonRetryEvidencePromise) {
      comparisonRetryEvidencePromise = loadOperationSections(null, ['comparison_retry_evidence']).then(function (payload) {
        const retry = (canonicalReliability(payload).retry_analysis || {});
        if (retry.evidence_deferred === true) throw new Error('Exact retry evidence is still deferred');
        return retry;
      }).catch(function (error) {
        comparisonRetryEvidencePromise = null;
        throw error;
      });
    }
    return comparisonRetryEvidencePromise;
  }

  async function loadOperations(tabId) {
    if (tabId === 'ci-analytics' && state.analyticsView === 'dns') return {};
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

  function nightlyDisplayName(nightly, pipeline) {
    return nightly.display_name || (pipeline === 'ci' ? 'Upstream CI' : 'AMD CI');
  }

  function latestAmd(ops) {
    return nightlyForPipeline(ops, 'amd-ci');
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
      queue_zombies: 'Queue jobs older than the analysis threshold',
      queue_waiting: 'Jobs currently waiting across tracked queues',
      gating_red_targets: 'Canonical target groups not ready',
      target_groups_with_current_incidents: 'Reviewed target groups with current AMD failures',
      amd_logical_groups_not_fully_passing: 'AMD logical test groups not passing every route',
      mixed_state_flaky_candidates: 'Upstream groups with mixed pass and incident history',
      omni_waiting: 'Omni jobs waiting across the fleet',
    };
    return labels[item.kind] || item.kind.replace(/_/g, ' ');
  }

  function inspectAttention(item, ops) {
    if (String(item.kind || '').startsWith('queue_')) navigateTo('ci-queue', {queueView: item.kind === 'queue_waiting' ? 'jobs' : 'current', queueScope: 'all'});
    else if (item.kind === 'gating_red_targets' || item.kind === 'target_groups_with_current_incidents') navigateTo('ci-health', {healthView: 'targets', healthResult: 'all'});
    else if (item.kind === 'amd_logical_groups_not_fully_passing') navigateTo('ci-health', {healthView: 'targets', healthResult: 'attention'});
    else if (item.kind === 'mixed_state_flaky_candidates') navigateTo('ci-analytics', {analyticsView: 'flakes'});
    else if (item.kind === 'omni_waiting') navigateTo('ci-omni');
    else {
      const build = ((latestAmd(ops).builds || [])[0]) || {};
      if (build.number) openBuildDetail(build, attentionLabel(item));
      else openMetricDetail({label: attentionLabel(item), value: item.count, meta: 'No linked build is available in this snapshot.'});
    }
  }

  function upstreamScheduledGating(ops) {
    return ((ops || {}).gating || {}).upstream_scheduled || {};
  }

  function scheduledGatingSummary(run) {
    return (run || {}).summary || {};
  }

  function scheduledGatingKind(run) {
    return String((run || {}).kind || (run || {}).build_kind || 'scheduled').toLowerCase();
  }

  function scheduledGatingBuildNumber(run) {
    const row = run || {};
    return row.build_number !== undefined ? row.build_number : row.number;
  }

  function scheduledGatingBuildState(run) {
    const row = run || {};
    return row.build_state || row.state || 'unknown';
  }

  function scheduledGatingBuildUrl(run) {
    const row = run || {};
    const exact = exactPipelineBuildUrl(row, 'ci');
    return exact || buildUrl('ci', scheduledGatingBuildNumber(row));
  }

  function scheduledGatingWait(row) {
    return (row || {}).queue_wait_mins || (row || {}).wait_mins || {};
  }

  function scheduledWaitSampleCount(wait) {
    const stats = wait || {};
    const count = stats.sample_count !== undefined ? stats.sample_count : stats.count;
    return Number.isFinite(Number(count)) ? Number(count) : null;
  }

  function scheduledGatingQueues(run) {
    const queues = (run || {}).queues;
    if (Array.isArray(queues)) return queues;
    if (!queues || typeof queues !== 'object') return [];
    return Object.entries(queues).map(function (entry) {
      return Object.assign({queue: entry[0]}, entry[1] || {});
    });
  }

  function scheduledGatingRuns(block) {
    const configured = (block || {}).recent || (block || {}).recent_runs;
    if (Array.isArray(configured) && configured.length) return configured;
    const byKind = (block || {}).latest_by_kind || {};
    return Object.values(byKind).filter(Boolean).sort(function (left, right) {
      return String((right || {}).created_at || (right || {}).observed_at || '').localeCompare(String((left || {}).created_at || (left || {}).observed_at || ''));
    });
  }

  function scheduledGatingPresentation(block) {
    const data = block || {};
    const run = data.latest || {};
    const summary = scheduledGatingSummary(run);
    if (data.available === false || !Object.keys(run).length) {
      return {
        value: 'Unavailable',
        meta: 'No retained Full CI run - nightly or daily build',
        tone: 'is-warning',
      };
    }
    const missing = Number(summary.missing || 0);
    const failing = Number(summary.failing || summary.failed || 0);
    const soft = Number(summary.soft_failing || summary.soft_failed || summary.soft || 0);
    const pending = Number(summary.pending || summary.waiting || 0);
    const buildNumber = scheduledGatingBuildNumber(run);
    return {
      value: integer(summary.gated) + ' / ' + integer(summary.total) + ' selected',
      meta: scheduledGatingKind(run) + (buildNumber ? ' #' + buildNumber : ''),
      tone: failing ? 'is-danger' : soft || pending || missing ? 'is-warning' : 'is-success',
    };
  }

  function openUpstreamScheduledGatingDetail(block) {
    const data = block || {};
    const latest = data.latest || {};
    const summary = scheduledGatingSummary(latest);
    const latestWait = scheduledGatingWait(latest);
    const queues = scheduledGatingQueues(latest);
    const usedQueues = queues.filter(function (row) { return Number(row.gated || 0) > 0; });
    const content = n('div', 'ops-stack');
    const note = n('div', 'ops-evidence-note is-info');
    add(note, [
      n('strong', '', 'Exact upstream scheduled cohort. '),
      n('span', '', 'Only main-branch Full CI run - nightly and Full CI run - daily builds are included. Logical AMD mirror groups are joined by stable Buildkite step key, retry attempts are collapsed, and queue wait is measured from runnable_at to started_at.'),
    ]);
    content.append(note);

    const evidenceSummary = n('div', 'ops-evidence-summary');
    add(evidenceSummary, [
      evidenceSummaryItem('SELECTED MIRROR GROUPS', integer(summary.gated) + ' / ' + integer(summary.total), Number(summary.missing || 0) ? 'is-warning' : ''),
      evidenceSummaryItem('PASSING', integer(summary.passing) + ' / ' + integer(summary.gated), Number(summary.failing || summary.failed || 0) ? 'is-danger' : 'is-success'),
      evidenceSummaryItem('USED / CONFIGURED QUEUES', integer(summary.queue_count !== undefined ? summary.queue_count : usedQueues.length) + ' / ' + integer(summary.configured_queue_count !== undefined ? summary.configured_queue_count : queues.length)),
      evidenceSummaryItem('QUEUE WAIT P50 / P95', duration(latestWait.p50) + ' / ' + duration(latestWait.p95), Number(latestWait.p95 || 0) >= 30 ? 'is-warning' : ''),
    ]);
    content.append(evidenceSummary);

    const queueColumns = [
      {label: 'Queue', sticky: true, width: '180px', render: function (row) { return n('span', 'ops-mono', value(row.queue || row.name || row.id)); }},
      {label: 'Use', width: '100px', render: function (row) { return badge(Number(row.gated || 0) > 0 ? 'used' : 'not used', Number(row.gated || 0) > 0 ? 'is-success' : 'is-neutral'); }},
      {label: 'Selected / total', numeric: true, width: '130px', render: function (row) { return integer(row.gated) + ' / ' + integer(row.total); }},
      {label: 'Passing', numeric: true, width: '100px', render: function (row) { return integer(row.passing); }},
      {label: 'Fail / soft', numeric: true, width: '120px', render: function (row) { return integer(row.failing !== undefined ? row.failing : row.failed) + ' / ' + integer(row.soft_failing !== undefined ? row.soft_failing : row.soft_failed); }},
      {label: 'Selected jobs', numeric: true, width: '130px', render: function (row) { return integer(row.selected_jobs !== undefined ? row.selected_jobs : row.job_count); }},
      {label: 'Wait p50', numeric: true, width: '105px', render: function (row) { return duration(scheduledGatingWait(row).p50); }},
      {label: 'Wait p95', numeric: true, width: '105px', render: function (row) { return duration(scheduledGatingWait(row).p95); }},
      {label: 'Wait max', numeric: true, width: '105px', render: function (row) { return duration(scheduledGatingWait(row).max); }},
      {label: 'Samples', numeric: true, width: '95px', render: function (row) { return integer(scheduledWaitSampleCount(scheduledGatingWait(row))); }},
    ];
    content.append(panel(
      'Selected mirror groups by Buildkite queue',
      integer(usedQueues.length) + ' used of ' + integer(queues.length) + ' configured queues in ' + scheduledGatingKind(latest) + ' #' + value(scheduledGatingBuildNumber(latest)),
      dataTable(queueColumns, queues, integer(queues.length) + ' configured AMD mirror queues', {name: 'scheduled-gating-queues', minWidth: '1190px'})
    ));

    const runs = scheduledGatingRuns(data);
    if (runs.length) {
      content.append(panel('Retained nightly and daily runs', integer(runs.length) + ' exact scheduled builds', dataTable([
        {label: 'Build', sticky: true, width: '110px', render: function (row) { return externalLink('#' + value(scheduledGatingBuildNumber(row)), scheduledGatingBuildUrl(row), 'ops-mono'); }},
        {label: 'Cohort', width: '100px', render: function (row) { return badge(scheduledGatingKind(row), 'is-info'); }},
        {label: 'State', width: '110px', render: function (row) { return linkedBadge(scheduledGatingBuildState(row), scheduledGatingBuildUrl(row)); }},
        {label: 'Selected / total', numeric: true, width: '130px', render: function (row) { const counts = scheduledGatingSummary(row); return integer(counts.gated) + ' / ' + integer(counts.total); }},
        {label: 'Passing', numeric: true, width: '100px', render: function (row) { return integer(scheduledGatingSummary(row).passing); }},
        {label: 'Queues', numeric: true, width: '90px', render: function (row) { const counts = scheduledGatingSummary(row); return integer(counts.queue_count !== undefined ? counts.queue_count : scheduledGatingQueues(row).length); }},
        {label: 'Wait p50', numeric: true, width: '105px', render: function (row) { return duration(scheduledGatingWait(row).p50); }},
        {label: 'Wait p95', numeric: true, width: '105px', render: function (row) { return duration(scheduledGatingWait(row).p95); }},
        {label: 'Observed', width: '170px', render: function (row) { return shortDate(row.created_at || row.observed_at); }},
      ], runs, integer(runs.length) + ' retained Full CI scheduled builds', {name: 'scheduled-gating-runs', minWidth: '1020px'})));
    }

    const groups = Array.isArray(latest.groups) ? latest.groups : [];
    if (groups.length) {
      content.append(compactTablePanel('Scheduled mirror groups', integer(groups.length) + ' groups matched by stable Buildkite step key', [
        {label: 'Test group', sticky: true, width: '320px', render: function (row) { const url = exactPipelineEvidenceUrl(row, 'ci') || row.job_url || row.url; return externalLink(row.label || row.name || row.key, url); }},
        {label: 'Result', width: '120px', render: function (row) { return linkedBadge(value(row.state, 'missing'), exactPipelineEvidenceUrl(row, 'ci') || row.job_url || row.url); }},
        {label: 'Queue', width: '170px', render: function (row) { return n('span', 'ops-mono', value(row.queue || (row.queues || []).join(', '))); }},
        {label: 'Selected jobs', numeric: true, width: '130px', render: function (row) { return integer(row.selected_jobs !== undefined ? row.selected_jobs : row.job_count); }},
        {label: 'Wait p50', numeric: true, width: '105px', render: function (row) { return duration(scheduledGatingWait(row).p50); }},
        {label: 'Wait p95', numeric: true, width: '105px', render: function (row) { return duration(scheduledGatingWait(row).p95); }},
      ], groups, {
        id: 'upstream-scheduled-gating-groups',
        limit: 18,
        alwaysBrowse: true,
        browserSubtitle: 'Configured AMD mirrors observed in the selected upstream nightly or daily build',
        searchPlaceholder: 'Filter group, result, or queue',
        searchText: function (row) { return [row.label, row.name, row.key, row.state, row.queue, (row.queues || []).join(' ')].join(' '); },
        geometry: {name: 'scheduled-gating-groups', minWidth: '950px'},
      }));
    }

    openDetailDrawer({
      id: 'upstream-scheduled-gating',
      title: 'Upstream scheduled mirror cohort',
      subtitle: 'vllm/ci - nightly and daily only',
      description: data.available === false
        ? 'No retained main-branch nightly or daily Buildkite build can be joined to the configured AMD mirror inventory.'
        : 'Configured logical AMD mirror groups, their latest scheduled outcomes, selected queues, and queue-wait samples.',
      fields: [
        {label: 'Selected build', value: scheduledGatingKind(latest) + ' #' + value(scheduledGatingBuildNumber(latest))},
        {label: 'Result', value: scheduledGatingBuildState(latest)},
        {label: 'Configured groups', value: integer(summary.total)},
        {label: 'Observed selected groups', value: integer(summary.gated)},
        {label: 'Passing groups', value: integer(summary.passing)},
        {label: 'Selected job executions', value: integer(summary.selected_jobs !== undefined ? summary.selected_jobs : summary.job_count)},
        {label: 'Queue-wait samples', value: integer(scheduledWaitSampleCount(latestWait))},
      ],
      sources: [
        {label: 'Open scheduled-cohort JSON', url: SOURCE_ASSETS.upstreamScheduledGating},
        {label: 'Open configured-group JSON', url: SOURCE_ASSETS.upstreamGatingCapacity},
        scheduledGatingBuildUrl(latest) ? {label: 'Open selected Buildkite build', url: scheduledGatingBuildUrl(latest)} : null,
        {label: 'Open nightly + daily Buildkite filter', url: SOURCE_ASSETS.upstreamScheduledBuilds},
      ],
      content: content,
    });
  }

  async function renderHome(host, ops) {
    const amd = latestAmd(ops);
    const build = (amd.builds || [])[0] || {};
    const amdHealthSummary = ((ops.amd_test_health || {}).summary) || {};
    const nightlyState = amdNightlyPresentation(build, amdHealthSummary, ops.generated_at);
    const paritySummary = ((ops.test_group_parity || {}).summary) || {};
    const queue = (ops.queue || {}).snapshot || {};
    const allFleetQueues = Object.entries(queue.queues || {}).filter(function (entry) { return !isRetiredQueue(entry[0]); });
    const allFleetWaiting = allFleetQueues.length ? allFleetQueues.reduce(function (sum, entry) { return sum + Number((entry[1] || {}).waiting || 0); }, 0) : Number(queue.total_waiting || 0);
    const allFleetRunning = allFleetQueues.length ? allFleetQueues.reduce(function (sum, entry) { return sum + Number((entry[1] || {}).running || 0); }, 0) : Number(queue.total_running || 0);
    add(host, pageHeader('Command Center', 'Current AMD operations with observed nightly failure movement and direct paths to source evidence.', ops.generated_at));
    add(host, statusStrip([
      {id: 'home-amd-nightly', label: 'LATEST AMD NIGHTLY', value: nightlyState.label, meta: nightlyState.meta, tone: nightlyState.tone, url: exactPipelineBuildUrl(build, 'amd-ci'), observed: build.created_at, actionLabel: 'Open Buildkite ↗'},
      {id: 'home-upstream-parity', label: 'UPSTREAM TEST-GROUP PARITY', value: paritySummary.main_complete_groups === undefined ? 'Unavailable' : integer(paritySummary.main_complete_groups) + ' / ' + integer(paritySummary.applicable_groups) + ' on main', meta: paritySummary.upstream_logical_groups === undefined ? 'Reviewed parity inventory unavailable' : percent(paritySummary.main_complete_groups, paritySummary.applicable_groups) + ' · ' + integer(paritySummary.main_missing_groups) + ' missing', tone: Number(paritySummary.action_groups) ? 'is-warning' : 'is-success', onOpen: function () { navigateTo('ci-health', {healthView: 'parity'}); }, actionLabel: 'Open Upstream parity →'},
      {id: 'home-queue-snapshot', label: 'ALL-FLEET QUEUE ACTIVITY', value: integer(allFleetWaiting) + ' waiting', meta: integer(allFleetRunning) + ' running across ' + integer(allFleetQueues.length) + ' queues', tone: allFleetWaiting ? 'is-warning' : 'is-success', observed: queue.ts, provenance: 'Same all-queue scope as destination', onOpen: function () { navigateTo('ci-queue', {queueView: 'current', queueScope: 'all'}); }, actionLabel: 'Open Queue Monitor →'},
    ], 'Command Center summary'));

    const grid = n('div', 'ops-grid ops-grid-main-aside ops-home-grid');
    const attentionRows = ops.attention || [];
    grid.append(panel('Needs attention', attentionRows.length + ' active signals', dataTable([
      {label: 'Operational signal', sticky: true, render: function (item) { return linkButton(attentionLabel(item), function () { inspectAttention(item, ops); }); }},
      {label: 'Severity', render: function (item) { return linkedBadge(item.severity, null, function () { inspectAttention(item, ops); }); }},
      {label: 'Count', numeric: true, render: function (item) { return linkButton(integer(item.count), function () { inspectAttention(item, ops); }); }},
    ], attentionRows), 'ops-home-primary'));

    const recent = (amd.builds || []).slice(0, 7);
    grid.append(panel('AMD nightly failure movement', 'Latest seven completed observations', dataTable([
      {label: 'Build', render: function (r) { return externalLink('#' + r.number, exactPipelineBuildUrl(r, 'amd-ci'), 'ops-mono'); }},
      {label: 'Test signal', render: function (r) { return linkedBadge(r.has_test_results === false ? (Number(r.test_jobs_blocked || 0) ? 'Infra blocked' : 'Unavailable') : 'Observed', exactPipelineBuildUrl(r, 'amd-ci'), function () { openBuildDetail(r); }, r.has_test_results === false ? 'is-danger' : 'is-success'); }},
      {label: 'New failure', numeric: true, render: function (r) { const count = nightlyFailureCount(r, 'new'); return linkButton(count === null ? '-' : integer(count), function () { openBuildDetail(r); }); }},
      {label: 'Recurring failure', numeric: true, render: function (r) { const count = nightlyFailureCount(r, 'recurring'); return linkButton(count === null ? '-' : integer(count), function () { openBuildDetail(r); }); }},
      {label: 'Fixed', numeric: true, render: function (r) { const count = nightlyFailureCount(r, 'fixed'); return linkButton(count === null ? '-' : integer(count), function () { openBuildDetail(r); }); }},
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
            url: exactPipelineEvidenceUrl({latest_url: definition.url || definition.latest_url || member.url || member.latest_url}, 'amd-ci'),
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
        n('span', '', 'This compatibility snapshot predates the fixed best-hardware policy. Refresh after the matrix collector publishes the policy and complete configured test-group inventory together.'),
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
    host.append(tabList([
      {id: 'overview', label: 'Overview'}, {id: 'parity', label: 'Upstream parity'},
      {id: 'targets', label: 'Target health'},
      {id: 'coverage', label: 'AMD hardware'},
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

  function openTestGroupParityDetail(row, payload) {
    const source = (payload || {}).source || {};
    const commit = source.main_commit || source.commit_sha || '';
    const commitUrl = source.main_commit_url || (commit ? 'https://github.com/vllm-project/vllm/commit/' + commit : '');
    const presentation = testGroupParityState(row.state);
    openDetailDrawer({
      id: 'parity-group-' + row.id,
      title: row.title || 'Upstream logical test group',
      subtitle: 'Reviewed CUDA-to-ROCm coverage on vLLM main',
      description: row.assessment || 'No reviewed assessment is available.',
      fields: [
        {label: 'Inventory number', value: row.id},
        {label: 'Test area', value: row.area},
        {label: 'Main status', value: presentation.label.replace(/^[●■]\s*/, '')},
        {label: 'Upstream CUDA variants', value: row.cuda_variants},
        {label: 'ROCm assessment', value: row.assessment},
      ],
      sources: [
        commitUrl ? {label: 'Open reviewed vLLM main commit', url: commitUrl} : null,
      ],
    });
  }

  function testGroupParityColumns(payload) {
    return [
      {label: '#', numeric: true, width: '70px', render: function (row) { return n('span', 'ops-mono', integer(row.id)); }},
      {label: 'Upstream logical test group', sticky: true, width: '330px', render: function (row) { return linkButton(row.title, function () { openTestGroupParityDetail(row, payload); }); }},
      {label: 'Area', width: '180px', render: function (row) { return value(row.area); }},
      {label: 'CUDA variants', width: '180px', render: function (row) { return value(row.cuda_variants); }},
      {label: 'Main status', width: '225px', render: function (row) { const presentation = testGroupParityState(row.state); return linkedBadge(presentation.label, null, function () { openTestGroupParityDetail(row, payload); }, presentation.tone); }},
      {label: 'ROCm counterpart or assessment', width: '480px', render: function (row) { return linkButton(row.assessment, function () { openTestGroupParityDetail(row, payload); }); }},
    ];
  }

  function openParityRows(title, rows, payload, subtitle) {
    openTableBrowser({
      id: 'parity-' + String(title || 'groups').toLowerCase().replace(/[^a-z0-9]+/g, '-'),
      title: title,
      subtitle: subtitle || integer(rows.length) + ' reviewed upstream logical test groups',
      rows: rows,
      columns: testGroupParityColumns(payload),
      searchPlaceholder: 'Filter test group, area, CUDA variant, status, or assessment',
      searchText: function (row) { return [row.id, row.title, row.area, row.cuda_variants, row.state, row.assessment].join(' '); },
      geometry: {name: 'upstream-test-group-parity', minWidth: '1465px'},
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

  function healthAreaLabel(area) {
    const key = String(area || 'other').trim().toLowerCase().replaceAll('_', '-');
    const labels = {
      'lm-eval': 'LM Eval',
      'models-language': 'Models · Language',
      'models-multimodal': 'Models · Multimodal',
      'pytorch': 'PyTorch',
      'spec-decode': 'Spec Decode',
    };
    return labels[key] || key.split('-').map(function (word) {
      return word ? word[0].toUpperCase() + word.slice(1) : '';
    }).join(' ');
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

  function openOwnershipAreaDetail(row) {
    const counts = row.counts || {};
    const confirmedHard = counts.confirmed_hard === undefined ? counts.hard : counts.confirmed_hard;
    const confirmedSoft = counts.confirmed_soft === undefined ? counts.soft : counts.confirmed_soft;
    const content = n('div', 'ops-detail-stack');
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
      dataTable(incidentColumns, incidents, integer(incidents.length) + ' confirmed incidents', {name: 'ownership-incidents', minWidth: '970px'})
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
      dataTable(pendingColumns, pendingSoft, integer(pendingSoft.length) + ' pending soft observations', {name: 'ownership-pending-soft', minWidth: '1010px'})
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
    const gating = ops.gating || {};
    const matrix = gating.matrix_summary || {};
    const uniqueHealth = matrixHealthPolicy(matrix);
    const amdHealthSummary = ((ops.amd_test_health || {}).summary) || {};
    const latestLogicalGroups = amdHealthSummary.latest_test_group_counts || {};
    const amdLatestStates = amdHealthSummary.latest_job_variant_state_counts || amdHealthSummary.latest_state_counts || {};
    const nightlyState = amdNightlyPresentation(build, amdHealthSummary, ops.generated_at);
    const viewDescriptions = {
      overview: 'Latest AMD nightly outcomes and failure movement. Logical test groups are separate from exact Buildkite job variants.',
      parity: 'Reviewed upstream logical test-group coverage on vLLM main. Runtime pass/fail is separate.',
      targets: 'Build-pinned health for the logical AMD test groups observed in the latest complete test signal.',
      coverage: 'Configured AMD test groups by architecture and the fixed best-hardware health policy.',
    };
    let headerAction = null;
    let observedAt = ops.generated_at;
    if (state.healthView === 'overview' && exactPipelineBuildUrl(build, 'amd-ci')) {
      headerAction = externalLink('Open AMD nightly #' + value(build.number) + ' ↗', exactPipelineBuildUrl(build, 'amd-ci'), 'ops-button');
      observedAt = build.created_at || observedAt;
    }
    if (state.healthView === 'parity') {
      const source = (ops.test_group_parity || {}).source || {};
      const mainCommit = source.main_commit || source.commit_sha || '';
      const mainUrl = source.main_commit_url || (mainCommit ? 'https://github.com/vllm-project/vllm/commit/' + mainCommit : '');
      if (mainUrl) headerAction = externalLink('Open reviewed vLLM main ↗', mainUrl, 'ops-button');
      observedAt = (ops.test_group_parity || {}).reviewed_at || observedAt;
    }
    if (state.healthView === 'targets') {
      if (amdHealthSummary.latest_build_url) {
        headerAction = externalLink('Open AMD test build #' + value(amdHealthSummary.latest_build_number) + ' ↗', amdHealthSummary.latest_build_url, 'ops-button');
      }
      observedAt = amdHealthSummary.latest_observed_at || observedAt;
    }
    const headerActions = n('div', 'ops-inline-actions');
    if (headerAction) headerActions.append(headerAction);
    headerActions.append(button('Data freshness', function () { openHealthDataFreshness(ops); }));
    add(host, pageHeader('CI Health', viewDescriptions[state.healthView] || viewDescriptions.overview, observedAt, headerActions));
    healthTabs(host);

    if (state.healthView === 'overview') {
      const logicalGroups = logicalTestGroupPresentation(latestLogicalGroups);
      const amdHealth = ops.amd_test_health || {};
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
      const movementBuilds = (amd.builds || []).filter(function (row) {
        const movement = nightlyFailureMovement(row);
        return row.has_test_results !== false && Boolean(movement) && movement.available !== false;
      }).slice(0, 14).reverse();
      if (!nightlyState.hasSignal) {
        const signalNote = n('div', 'ops-evidence-note is-warning');
        add(signalNote, [
          n('strong', '', 'Latest nightly has no test signal. '),
          n('span', '', 'The failure-observation list, matrix, and movement chart below use the latest observed AMD test build'),
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
          return {label: '#' + nightly.number, timestamp: nightly.created_at, url: exactPipelineBuildUrl(nightly, 'amd-ci'), valueSummary: integer(movement.new.length) + ' new - ' + integer(movement.recurring.length) + ' recurring - ' + integer(movement.fixed.length) + ' fixed', details: {state: nightly.state, new_failure: movement.new.length, recurring_failure: movement.recurring.length, fixed: movement.fixed.length}};
        }),
      });
      return;
    }

    if (state.healthView === 'parity') {
      const parity = ops.test_group_parity || {};
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

      if (!allRows.length || !upstreamTotal) {
        host.append(n('div', 'ops-evidence-note is-warning', 'The reviewed upstream test-group parity inventory is unavailable in this snapshot.'));
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
        'REVIEWED SCOPE · ' + integer(upstreamTotal) + ' LOGICAL GROUPS',
        integer(unsupportedTotal) + ' hardware- or backend-specific groups are classified outside the parity denominator.',
        [
          {label: 'covered on main', count: mainTotal, tone: 'is-success', onOpen: function () { openParityRows('Covered on main', mainRows, parity); }},
          {label: 'potential open gaps', count: actionTotal, tone: 'is-danger', onOpen: function () { openParityRows('Potential open gaps', missingRows, parity); }},
          {label: 'not targeted', count: unsupportedTotal, tone: 'is-not-targeted', onOpen: function () { openParityRows('Not targeted / unsupported', unsupportedRows, parity); }},
        ]
      ));
      host.append(hero);

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
        button('Browse complete ' + integer(upstreamTotal) + '-group inventory', function () { openParityRows('Complete reviewed upstream inventory', allRows, parity); }),
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

    if (state.healthView === 'targets') {
      const amdHealth = ops.amd_test_health || {};
      const logicalInventory = amdLogicalInventory(amdHealth);
      const allTargets = Array.from(logicalInventory.rows || []);
      const latestAmdBuild = logicalInventory.build_number || amdHealthSummary.latest_build_number;
      const passingAllTargets = allTargets.filter(function (row) { return row.state === 'passing_all'; });
      const partialTargets = allTargets.filter(function (row) { return row.state === 'partial'; });
      const nonPassingTargets = allTargets.filter(function (row) { return row.state === 'non_passing'; });
      const attentionTargets = nonPassingTargets.concat(partialTargets);
      const passingTargets = passingAllTargets.concat(partialTargets);
      function sortTargetRows(rows) {
        const rank = {non_passing: 0, partial: 1, passing_all: 2};
        return Array.from(rows || []).sort(function (left, right) {
          return Number(rank[left.state] === undefined ? 3 : rank[left.state])
            - Number(rank[right.state] === undefined ? 3 : rank[right.state])
            || compareText(left.label || left.logical_key, right.label || right.logical_key)
            || compareText(left.id, right.id);
        });
      }
      const filters = {
        all: allTargets,
        attention: attentionTargets,
        non_passing: nonPassingTargets,
        partial: partialTargets,
        passing: passingAllTargets,
      };
      const targetRows = sortTargetRows(filters[state.healthResult] || attentionTargets);
      const reviewedPlanRows = Array.isArray(gating.target_groups) ? gating.target_groups : [];
      function appendReviewedPlan() {
        if (!reviewedPlanRows.length) return;
        const noDefinitionPlanRows = reviewedPlanRows.filter(function (row) { return targetResolutionPresentation(row).status === 'no_amd_definition'; });
        const mappingReviewPlanRows = reviewedPlanRows.filter(function (row) { return ['stale_target_alias', 'ambiguous'].includes(targetResolutionPresentation(row).status); });
        const notObservedPlanRows = reviewedPlanRows.filter(function (row) { return targetResolutionPresentation(row).status === 'not_observed'; });
        function openReviewedPlanRows(title, rows) {
          openTableBrowser({
            id: 'reviewed-coverage-plan-browser',
            title: title,
            subtitle: 'Manually reviewed coverage-plan entries; mapping quality is separate from AMD runtime health',
            rows: sortRuntimeTargetRows(rows),
            columns: [
              {label: 'Reviewed plan entry', sticky: true, width: '390px', render: function (row) { return linkButton(row.label, function () { openGatingDetailWithEvidence(row, ops); }); }},
              {label: 'Area', width: '170px', render: function (row) { return healthAreaLabel(row.area); }},
              {label: 'Mapping', width: '220px', render: function (row) { const resolution = targetResolutionPresentation(row); return linkButton(resolution.label, function () { openGatingDetailWithEvidence(row, ops); }); }},
              {label: 'Plan note / assessment', width: '520px', render: function (row) { return linkButton(targetAssessmentText(row), function () { openGatingDetailWithEvidence(row, ops); }); }},
            ],
            searchPlaceholder: 'Filter plan entry, area, mapping, or assessment',
            searchText: function (row) { const resolution = targetResolutionPresentation(row); return [row.label, row.area, targetAssessmentText(row), resolution.label, resolution.reason, resolution.amdDefinitionLabels.join(' ')].join(' '); },
            geometry: {name: 'reviewed-coverage-plan', minWidth: '1300px'},
          });
        }
        const planActions = n('div', 'ops-related-actions');
        add(planActions, [
          button('Browse all ' + integer(reviewedPlanRows.length) + ' plan entries', function () { openReviewedPlanRows('Reviewed coverage plan', reviewedPlanRows); }, true),
          button('Browse ' + integer(noDefinitionPlanRows.length) + ' without one-to-one AMD definitions', function () { openReviewedPlanRows('Plan entries without one-to-one AMD definitions', noDefinitionPlanRows); }),
          button('Browse ' + integer(mappingReviewPlanRows.length) + ' mapping-review entries', function () { openReviewedPlanRows('Plan entries needing mapping review', mappingReviewPlanRows); }),
          notObservedPlanRows.length ? button('Browse ' + integer(notObservedPlanRows.length) + ' mapped but unobserved entries', function () { openReviewedPlanRows('Mapped plan entries not observed', notObservedPlanRows); }) : null,
        ]);
        const denominatorCopy = allTargets.length
          ? 'excluded from the ' + integer(allTargets.length) + '-group runtime-health denominator.'
          : 'excluded from the runtime-health denominator.';
        host.append(panel(
          'Reviewed coverage plan',
          integer(reviewedPlanRows.length) + ' manually reviewed plan entries. They are retained for coverage planning and mapping review but ' + denominatorCopy,
          planActions
        ));
      }
      if (!logicalInventory.available || !allTargets.length) {
        host.append(n('div', 'ops-evidence-note is-warning', 'The build-pinned AMD logical test-group inventory is unavailable in this snapshot.'));
        appendReviewedPlan();
        return;
      }
      function openTargetRows(title, rows) {
        openAmdLogicalCatalog(
          title,
          'Build-pinned logical AMD test groups from test signal #' + value(latestAmdBuild) + '; select a row for every hardware route and exact job',
          sortTargetRows(rows),
          logicalInventory,
          amdHealth
        );
      }

      const targetHero = n('div', 'ops-health-hero-grid');
      targetHero.append(healthRingCard({
        eyebrow: 'AMD RUNTIME TEST GROUPS',
        title: 'Passing now',
        current: passingTargets.length,
        total: allTargets.length,
        meta: integer(passingAllTargets.length) + ' pass every route · ' + integer(partialTargets.length) + ' partial · ' + integer(nonPassingTargets.length) + ' non-passing',
        tone: attentionTargets.length ? 'is-warning' : 'is-success',
        actionLabel: 'Inspect all logical test groups →',
        onOpen: function () { openTargetRows('AMD runtime test groups', allTargets); },
      }));
      targetHero.append(healthDistributionCard(
        'LOGICAL GROUP OUTCOMES · ' + (latestAmdBuild ? '#' + integer(latestAmdBuild) : 'UNAVAILABLE'),
        'Hardware routes combine only when the build-pinned identity rules identify the same logical AMD test group.',
        [
          {label: 'pass every route', count: passingAllTargets.length, tone: 'is-success', onOpen: function () { openTargetRows('AMD groups passing every route', passingAllTargets); }},
          {label: 'pass some routes', count: partialTargets.length, tone: 'is-warning', onOpen: function () { openTargetRows('AMD groups passing some routes', partialTargets); }},
          {label: 'non-passing', count: nonPassingTargets.length, tone: 'is-danger', onOpen: function () { openTargetRows('Non-passing AMD groups', nonPassingTargets); }},
        ]
      ));
      host.append(targetHero);

      const targetToolbar = n('div', 'ops-toolbar');
      targetToolbar.append(segmented([
        {id: 'attention', label: 'Not fully passing (' + integer(attentionTargets.length) + ')'},
        {id: 'non_passing', label: 'Non-passing (' + integer(nonPassingTargets.length) + ')'},
        {id: 'partial', label: 'Partial (' + integer(partialTargets.length) + ')'},
        {id: 'passing', label: 'Pass every route (' + integer(passingAllTargets.length) + ')'},
        {id: 'all', label: 'All (' + integer(allTargets.length) + ')'},
      ], state.healthResult, function (result) {
        setRouteState('ci-health', 'healthResult', result, 'health_result');
      }, 'Filter logical AMD test groups by latest result'));
      const attentionList = n('div', 'ops-health-attention-list');
      targetRows.slice(0, 8).forEach(function (row) {
        const control = n('button', 'ops-health-attention-row');
        control.type = 'button';
        control.addEventListener('click', function () { openAmdLogicalGroupDetail(row, logicalInventory, amdHealth); });
        const identity = n('span', 'ops-health-attention-copy');
        add(identity, [
          n('strong', '', row.label || row.logical_key),
          n('small', '', integer(row.hardware_count) + ' hardware ' + (Number(row.hardware_count) === 1 ? 'route' : 'routes') + ' · ' + integer(row.job_variant_count) + ' exact job ' + (Number(row.job_variant_count) === 1 ? 'variant' : 'variants')),
        ]);
        const routeSummary = (row.hardware_states || []).map(function (item) {
          return hardwareDisplayLabel(item.hardware) + ': ' + amdLogicalSignalLabel(item.state).toLowerCase();
        }).join(' · ');
        add(control, [
          n('span', 'ops-health-attention-state ' + amdLogicalStateTone(row.state), amdLogicalStateLabel(row.state)),
          identity,
          n('span', 'ops-health-attention-reason', routeSummary),
          n('span', 'ops-stat-action', 'Inspect →'),
        ]);
        attentionList.append(control);
      });
      if (!targetRows.length) attentionList.append(n('div', 'ops-empty', 'No logical AMD test groups match this filter.'));
      const browse = button('Browse all ' + integer(targetRows.length) + ' selected test groups', function () { openTargetRows('AMD runtime test groups · ' + state.healthResult.replaceAll('_', ' '), targetRows); }, true);
      const body = n('div', 'ops-stack');
      add(body, [targetToolbar, attentionList, browse]);
      host.append(panel(
        state.healthResult === 'attention' ? 'AMD test groups not fully passing' : 'AMD runtime test-group selection',
        'Select a row for its build-pinned hardware routes, exact job variants, and execution evidence.',
        body,
        'ops-health-target-panel'
      ));

      appendReviewedPlan();
      return;
    }

    if (state.healthView === 'quality') {
      host.append(segmented([
        {id: 'mapping', label: 'Source mapping'},
        {id: 'collectors', label: 'Collector freshness'},
      ], state.healthQualityView, function (qualityView) {
        setRouteState('ci-health', 'healthQualityView', qualityView, 'health_quality_view');
      }, 'Data quality view'));
    }

    if (state.healthView === 'quality' && state.healthQualityView === 'mapping') {
      const parity = ops.definition_parity || {};
      const summary = parity.summary || {};
      const source = parity.source || {};
      const comparisonRows = definitionParityComparisonRows(parity);
      const mirrorRows = definitionParityMirrorRows(parity);
      const mirrorOverrides = (parity.mirrors || []).filter(function (row) { return row.commands_overridden; }).length;
      if (state.healthPlan === 'matched') state.healthPlan = 'covered';
      if (state.healthPlan === 'unmatched') state.healthPlan = 'unlinked';
      if (state.healthPlan === 'upstream') state.healthPlan = 'all';
      const note = n('div', 'ops-evidence-note is-info');
      add(note, [
        n('strong', '', 'Upstream-only source definitions are shown first. '),
        n('span', '', 'This matcher inventory is not runtime health or upstream logical test-group parity. Use the relationship filter to inspect linked, AMD-only, mirror, or changed definitions.'),
      ]);
      host.append(note);
      host.append(methodDisclosure('Source-mapping methodology', [
        n('span', '', 'The comparison preserves ' + integer(summary.total_amd_steps) + ' collision-safe source nodes; ' + integer(summary.covered) + ' are linked (' + integer(summary.direct_matches) + ' direct, ' + integer(summary.inline_mirror_variants) + ' mirror-linked, and ' + integer(summary.additional_variants) + ' additional).'),
        n('span', '', integer(summary.mirrors) + ' inline mirrors include ' + integer(mirrorOverrides) + ' command overrides. The ' + integer(summary.amd_only_identity_families) + ' AMD-only families are classifications, not runtime failures or an automatic backlog.'),
        source.commit_url ? externalLink('Open pinned vLLM commit ↗', source.commit_url) : null,
      ]));
      const toolbar = n('div', 'ops-toolbar');
      const search = n('input', 'ops-input');
      search.type = 'search'; search.placeholder = 'Search AMD or upstream definitions'; search.value = state.healthSearch;
      search.setAttribute('aria-label', 'Search CI source definitions');
      search.addEventListener('change', function () { setRouteState('ci-health', 'healthSearch', search.value, 'health_definition_search'); });
      const planFilter = n('select', 'ops-select');
      planFilter.setAttribute('aria-label', 'Filter source-definition relationship');
      [
        ['all', 'All standalone comparisons'],
        ['amd', 'All AMD definitions'],
        ['covered', 'Covered AMD definitions'],
        ['direct', 'Direct matches'],
        ['inline_variant', 'Mirror-linked standalone variants'],
        ['additional_variant', 'Additional AMD variants'],
        ['twins', 'Command twins'],
        ['changed', 'Command differences'],
        ['unlinked', 'All unlinked'],
        ['amd_only', 'AMD-only standalone'],
        ['upstream_only', 'Upstream-only (' + integer(summary.nvidia_only) + ')'],
        ['mirror_inventory', 'Inline mirror inventory'],
      ].forEach(function (pair) { const option = n('option', '', pair[1]); option.value = pair[0]; option.selected = state.healthPlan === pair[0]; planFilter.append(option); });
      planFilter.addEventListener('change', function () { setRouteState('ci-health', 'healthPlan', planFilter.value, 'health_definition_filter'); });
      add(toolbar, [search, planFilter]);
      host.append(toolbar);
      const q = state.healthSearch.trim().toLowerCase();
      const definitions = definitionParityFilter(
        comparisonRows.concat(mirrorRows),
        state.healthPlan
      ).filter(function (row) {
        if (!q) return true;
        return [
          row.amd_label,
          row.nvidia_label,
          row.label,
          row.identity_key,
          row.definition_id,
          row.amd_definition_id,
          row.nvidia_definition_id,
          row.source,
          row.source_file,
          row.amd_source,
          row.nvidia_source,
          row.inline_mirror_amd_device,
          row.amd_device,
          (row.amd_member_agent_pools || []).join(' '),
        ].some(function (part) { return String(part || '').toLowerCase().includes(q); });
      }).sort(function (a, b) {
        function priority(row) {
          if (row.category === 'amd_only') return 0;
          if (row.category === 'upstream_only') return 1;
          if (row.category === 'inline_mirror' && row.commands_overridden) return 2;
          if (definitionParityEvidence(row).changed) return 3;
          if (row.match_method === 'command_twin') return 6;
          if (row.category === 'inline_mirror') return 7;
          if (row.category === 'inline_mirror_variant') return 8;
          if (row.category === 'additional_variant') return 9;
          return 10;
        }
        const aEvidence = definitionParityEvidence(a);
        const bEvidence = definitionParityEvidence(b);
        return priority(a) - priority(b)
          || Number(aEvidence.primarySimilarity === null ? 1 : aEvidence.primarySimilarity) - Number(bEvidence.primarySimilarity === null ? 1 : bEvidence.primarySimilarity)
          || String(a.amd_label || a.label || a.nvidia_label).localeCompare(String(b.amd_label || b.label || b.nvidia_label));
      });
      const definitionColumns = [
        {label: 'AMD definition', sticky: true, width: '285px', render: function (row) { const label = row.category === 'inline_mirror' ? 'mirror.amd' + (row.amd_device ? ' · ' + row.amd_device : '') : row.amd_label || (row.category === 'amd_only' ? row.label : ''); return label ? linkButton(label, function () { openDefinitionDetail(row, parity); }) : n('span', 'ops-cell-muted', '-'); }},
        {label: 'Upstream definition', width: '285px', render: function (row) { const label = row.nvidia_label || (row.category === 'upstream_only' ? row.label : ''); return label ? linkButton(label, function () { openDefinitionDetail(row, parity); }) : n('span', 'ops-cell-muted', '-'); }},
        {label: 'Relationship', width: '220px', render: function (row) { const presentation = definitionParityPresentation(row); return linkedBadge(presentation.label, null, function () { openDefinitionDetail(row, parity); }, presentation.tone); }},
        {label: 'Command evidence', numeric: true, width: '225px', render: function (row) { const presentation = definitionParityPresentation(row); if (presentation.primarySimilarity === undefined || presentation.primarySimilarity === null) return n('span', 'ops-cell-muted', '-'); const evidenceText = (Number(presentation.primarySimilarity) * 100).toFixed(1) + '% · ' + presentation.evidenceLabel; return linkButton(evidenceText, function () { openDefinitionDetail(row, parity); }, 'Command evidence: ' + evidenceText, 'Open command evidence details for ' + evidenceText); }},
        {label: 'Sources', width: '180px', render: function (row) { const wrap = n('div', 'ops-inline-actions'); if (row.amd_source_url || (row.category === 'amd_only' && row.source_url)) wrap.append(externalLink('AMD YAML', row.amd_source_url || row.source_url)); if (row.nvidia_source_url || (row.category === 'upstream_only' && row.source_url)) wrap.append(externalLink('Upstream YAML', row.nvidia_source_url || row.source_url)); return wrap.childNodes.length ? wrap : n('span', 'ops-cell-muted', '-'); }},
      ];
      const definitionPreviewColumns = state.healthPlan === 'upstream_only'
        ? [definitionColumns[1], definitionColumns[4]]
        : definitionColumns;
      const definitionPanelMeta = state.healthPlan === 'mirror_inventory'
        ? 'Inline mirror declarations; overrides and command differences are shown first'
        : 'Exact source relationships matching the active filter; literal gaps and command differences are shown first';
      host.append(compactTablePanel(
        'Source-definition comparison',
        definitionPanelMeta,
        definitionPreviewColumns,
        definitions,
        {
          id: 'definition-parity-browser',
          limit: 10,
          alwaysBrowse: definitions.length > 0,
          previewCaption: state.healthPlan === 'upstream_only' ? 'Upstream-only source definitions requiring classification' : 'Preview of source-definition relationships',
          conciseCounts: true,
          buttonLabel: 'Inspect complete mapping details',
          browserColumns: definitionColumns,
          browserTitle: 'vLLM CI source-definition mapping',
          browserSubtitle: 'Exact source links and command evidence from commit ' + String(source.commit_sha || '').slice(0, 12),
          searchPlaceholder: 'Filter label, definition ID, queue, source, or identity',
          searchText: function (row) { return [row.amd_label, row.nvidia_label, row.label, row.identity_key, row.definition_id, row.amd_definition_id, row.nvidia_definition_id, row.source, row.source_file, row.amd_source, row.nvidia_source, row.inline_mirror_amd_device, row.amd_device, (row.amd_member_agent_pools || []).join(' ')].join(' '); },
          initialQuery: state.healthSearch,
          geometry: {name: 'definition-parity-preview', minWidth: state.healthPlan === 'upstream_only' ? '465px' : '1195px'},
          browserGeometry: {name: 'definition-parity', minWidth: '1195px'},
        }
      ));
      return;
    }

    if (state.healthView === 'coverage') {
      let matrixData = {};
      try { matrixData = await fetchJSON('data/vllm/ci/amd_test_matrix.json'); } catch (_) {}
      const arch = matrixData.architectures || [];
      const coverageRows = sortAmdMatrixRows(matrixData.rows || [], state.healthCoverageSort);
      host.append(matrixHealthOverview(
        matrix,
        uniqueHealth,
        matrix.latest_build_number || amdHealthSummary.latest_build_number
      ));
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
            {label: 'Build', width: '110px', render: function (row) { const cell = (row.cells || {})[architecture.id] || {}; const url = exactPipelineEvidenceUrl({latest_url: cell.latest_url, build_number: cell.latest_build_number}, 'amd-ci'); return url ? externalLink('#' + value(cell.latest_build_number), url, 'ops-mono') : n('span', 'ops-cell-muted', '-'); }},
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

    if (state.healthView === 'quality' && state.healthQualityView === 'collectors') {
      const amdProvenance = ((ops.amd_test_health || {}).provenance || {});
      const amdJoin = amdProvenance.nightly_metadata || {};
      const totalAmdObservations = Number(amdJoin.joined_group_observations || 0) + Number(amdJoin.unjoined_group_observations || 0);
      const sourcePresentations = {
        analytics: {label: 'Build outcomes', description: 'Buildkite build and job outcomes'},
        agent_health: {label: 'AMD agent health', description: 'AMD CI agent observations'},
        amd_test_signal: {label: 'AMD test signal', description: 'Latest observed AMD nightly test signal'},
        ci_health: {label: 'CI health snapshot', description: 'Published CI health snapshot'},
        gating_targets: {label: 'Reviewed target groups', description: 'Reviewed AMD runtime target plan'},
        gating_target_candidates: {label: 'Target candidates', description: 'Proposed runtime target candidates'},
        amd_test_matrix: {label: 'AMD test matrix', description: 'AMD architecture definition matrix'},
        capacity_monitor: {label: 'Capacity monitor', description: 'Queue capacity and connected-agent snapshot'},
        queue_timeseries: {label: 'Queue history', description: 'Retained queue counts and wait measurements'},
        queue_jobs: {label: 'Active queue jobs', description: 'Last complete active Buildkite job overlay'},
        group_changes: {label: 'Definition changes', description: 'Test-group definition changes'},
        omni_heuristic: {label: 'Omni thresholds', description: 'Omni surge thresholds'},
        omni_issue_state: {label: 'Omni issues', description: 'Open Omni operational issues'},
      };
      function sourceIsStale(row) {
        const observed = new Date(row.record.timestamp).getTime();
        return !Number.isFinite(observed) || Date.now() - observed > 48 * 3600000;
      }
      function publishedSourceUrl(row) {
        return row.record.published === false ? '' : 'data/vllm/ci/' + row.record.path;
      }
      const sourceRows = Object.entries(ops.sources || {}).map(function (entry) {
        const presentation = sourcePresentations[entry[0]] || {};
        return {
          id: entry[0],
          label: presentation.label || entry[0].replaceAll('_', ' '),
          record: entry[1] || {},
          description: presentation.description || 'Published collector input',
        };
      }).sort(function (left, right) {
        return Number(sourceIsStale(right)) - Number(sourceIsStale(left)) || String(left.label).localeCompare(String(right.label));
      });
      const publishedSources = sourceRows.filter(function (row) { return Boolean(publishedSourceUrl(row)); });
      const staleSources = publishedSources.filter(sourceIsStale);
      const internalSourceCount = sourceRows.length - publishedSources.length;

      host.append(statusStrip([
        {id: 'diagnostic-amd-join', label: 'AMD OUTCOME JOINS', value: integer(amdJoin.joined_group_observations) + ' / ' + integer(totalAmdObservations), meta: integer(amdJoin.unjoined_group_observations) + ' unjoined observations', tone: Number(amdJoin.unjoined_group_observations) ? 'is-danger' : 'is-success', static: true},
        {id: 'diagnostic-source-freshness', label: 'PUBLISHED INPUTS', value: integer(publishedSources.length - staleSources.length) + ' / ' + integer(publishedSources.length) + ' current', meta: integer(staleSources.length) + ' older than 48 hours', tone: staleSources.length ? 'is-warning' : 'is-success', static: true},
      ], 'Collector integrity summary'));
      const diagnosticNote = n('div', 'ops-evidence-note is-info');
      add(diagnosticNote, [n('strong', '', 'Collector integrity, not another failure list. '), n('span', '', 'Only the input name opens its published JSON. Timestamps and freshness badges are informational, so they no longer send you to a raw data page. ' + integer(internalSourceCount) + ' internal-only contracts are omitted from this published-input table.')]);
      host.append(diagnosticNote);

      host.append(compactTablePanel(
        'Collector input freshness',
        'Stale inputs first',
        [
          {label: 'Input', sticky: true, width: '210px', render: function (row) { const url = publishedSourceUrl(row); return url ? externalLink(row.label + ' ↗', url, 'ops-cell-primary') : n('span', 'ops-cell-primary', row.label); }},
          {label: 'Purpose', width: '360px', render: function (row) { return row.description; }},
          {label: 'Observed', width: '190px', render: function (row) { return shortDate(row.record.timestamp); }},
          {label: 'Freshness', width: '130px', render: function (row) { return badge(age(row.record.timestamp), sourceIsStale(row) ? 'is-warning' : 'is-success'); }},
          {label: 'Timestamp source', width: '160px', render: function (row) { return badge(value(row.record.timestamp_source), 'is-neutral'); }},
        ],
        publishedSources,
        {
          id: 'diagnostic-sources-browser',
          limit: 10,
          previewCaption: 'Published collector inputs, stale first',
          conciseCounts: true,
          buttonLabel: 'Browse all published inputs',
          browserTitle: 'Collector input freshness',
          browserSubtitle: 'Published inputs, purpose, observation time, and timestamp provenance',
          searchPlaceholder: 'Filter collector input or purpose',
          searchText: function (row) { return [row.label, row.id, row.description, row.record.timestamp_source].join(' '); },
          geometry: {name: 'diagnostic-sources', minWidth: '1050px'},
        }
      ));

      const relatedActions = n('div', 'ops-related-actions');
      add(relatedActions, [
        button('Open test-group analytics →', function () { navigateTo('ci-analytics', {analyticsView: 'groups'}); }),
        button('Open flake analysis →', function () { navigateTo('ci-analytics', {analyticsView: 'flakes'}); }),
        button('Open retry analysis →', function () { navigateTo('ci-analytics', {analyticsView: 'retries'}); }),
        button('Open queue history →', function () { navigateTo('ci-queue', {queueView: 'history'}); }),
      ]);
      host.append(panel('Related investigation views', 'These explicit actions switch dashboard sections', relatedActions));
      return;
    }
  }

  function reliabilityIncidentRate(row) {
    const raw = row.incident_rate_pct !== undefined ? row.incident_rate_pct : row.fail_rate;
    return Number.isFinite(Number(raw)) ? Number(raw) : 0;
  }

  const groupHistoryCache = new WeakMap();

  function groupHistoryObservations(row, cohort) {
    if (!row || typeof row !== 'object') return [];
    let cached = groupHistoryCache.get(row);
    if (!cached) {
      cached = new Map();
      groupHistoryCache.set(row, cached);
    }
    const key = cohort || 'main';
    if (cached.has(key)) return cached.get(key);
    const observations = evidenceObservations(row).filter(function (observation) {
      return (!observation.source_pipeline || observation.source_pipeline === 'ci')
        && Boolean(exactPipelineEvidenceUrl(observation, 'ci'))
        && (cohort !== 'nightly' || isNightlyObservation(observation));
    }).sort(function (a, b) {
      return new Date(observationTimestamp(a) || 0) - new Date(observationTimestamp(b) || 0);
    });
    cached.set(key, observations);
    return observations;
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
    [['all', 'All latest results'], ['passing', 'Currently passing'], ['incident', 'Current failures'], ['mixed', 'Mixed outcomes']].forEach(function (pair) {
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
    add(tile, [head, n('div', 'ops-cluster-description', cluster.description), n('div', 'ops-cluster-meta', median + ' - ' + integer(cluster.latestIncidents || 0) + ' current failures')]);
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
    if (['soft', 'soft_fail', 'soft_failed'].includes(result)) return 'Soft failure';
    if (['incident', 'error'].includes(result)) return 'Incident';
    if (isIncidentObservation(observation)) return 'Hard failure';
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
        {id: 'all', label: 'All'}, {id: 'incident', label: 'Failure latest'},
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
        {label: 'FAILING LATEST', value: integer(latestIncidents), meta: 'groups ending in a failure observation', tone: latestIncidents ? 'is-danger' : 'is-success'},
        {label: 'MEDIAN RETAINED-RUN PASS RATE', value: medianRate === null ? '-' : medianRate.toFixed(1) + '%', meta: integer(mixed) + ' groups have mixed outcomes', tone: medianRate >= 95 ? 'is-success' : medianRate >= 80 ? 'is-warning' : 'is-danger'},
      ]));
      clear(mapHost);
      if (!prepared.length) {
        mapHost.append(n('div', 'ops-empty', 'No retained groups match these filters.'));
        return;
      }
      const viewport = n('div', 'ops-history-map-viewport');
      const map = n('div', 'ops-history-map');
      const mapHeader = n('div', 'ops-history-map-row ops-history-map-header');
      add(mapHeader, [n('span', '', 'Test group'), n('span', '', 'Retained-run pass rate'), n('span', '', 'Latest'), n('span', '', 'Latest 30 exact runs - oldest to newest')]);
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
      n('div', 'ops-history-fact-label', 'RETAINED-RUN PASS RATE'),
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
    add(incidentFact, [n('div', 'ops-history-fact-label', 'LAST FAILURE'), incidentValue, n('div', 'ops-history-fact-meta', lastIncidentObservation ? integer(incidentDistance) + ' runs ago - ' + shortDate(observationTimestamp(lastIncidentObservation)) : 'No failures in this cohort')]);
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
    add(incidentHeading, [n('h3', '', 'Failures to inspect'), n('p', '', integer(incidents.length) + ' retained failure observations in this cohort')]);
    incidentHeader.append(incidentHeading);
    incidentPanel.append(incidentHeader);
    const incidentList = n('div', 'ops-incident-list');
    if (incidents.length) {
      incidents.slice().reverse().slice(0, 6).forEach(function (observation) { incidentList.append(historyIncidentRow(observation)); });
    } else {
      incidentList.append(n('div', 'ops-history-all-clear', 'No failure observations are retained for this cohort.'));
    }
    incidentPanel.append(incidentList);
    if (incidents.length) {
      const inspectAll = button('Inspect all ' + integer(incidents.length) + ' failures', function () {
        openHistoryEvidence(
          selected.name + ' failures',
          incidents.slice().reverse().map(function (observation) { return observationHistoryPoint(observation, 'ci'); }),
          integer(incidents.length) + ' exact failure observations in the selected cohort',
          SOURCE_ASSETS.reliability
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
      {label: 'RETAINED-RUN PASS RATE', value: amdGroupPassRate(row) === null ? '-' : amdGroupPassRate(row).toFixed(1) + '%', meta: integer(row.passed) + ' passed - ' + integer(row.soft_failed) + ' soft - ' + integer(row.hard_failed) + ' hard'},
      {label: 'CURRENT PASS STREAK', value: integer(row.current_pass_streak), meta: integer(row.runs) + ' retained AMD nightlies'},
      {label: 'NON-PASSING RUNS', value: integer(incidents.length), meta: incidents.length ? 'Select any amber or red outcome for its Buildkite job' : 'None in retained history', tone: incidents.length ? 'is-warning' : 'is-success'},
    ]));

    const timeline = n('section', 'ops-history-panel ops-amd-history-timeline');
    const timelineHeader = n('header', 'ops-history-panel-header');
    const timelineHeading = n('div');
    add(timelineHeading, [n('h3', '', 'AMD nightly outcomes'), n('p', '', integer(observations.length) + ' exact job runs - oldest to newest')]);
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
      content.append(panel('Failure evidence', integer(incidents.length) + ' exact AMD jobs', incidentList));
    }
    content.append(sourceActions([
      {label: 'Open latest AMD job', url: exactPipelineEvidenceUrl(observations[observations.length - 1], 'amd-ci')},
      {label: 'Open AMD pipeline', url: SOURCE_ASSETS.amdPipeline},
      {label: 'Open published AMD health data', url: SOURCE_ASSETS.amdTestHealth},
    ]));
    openOverlay(row.display_name || row.name || row.job_name, value(row.hardware_variant || row.hardware) + ' - ' + value(row.queue) + ' - exact AMD nightly evidence', content, true, 'amd-group-' + row.id);
  }

  function amdLogicalInventory(amdHealth) {
    const inventory = (amdHealth || {}).latest_logical_test_groups || {};
    const counts = ((amdHealth || {}).summary || {}).latest_test_group_counts || {};
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

    host.append(statusStrip([
      {id: 'amd-health-build', label: 'LATEST AMD NIGHTLY', value: latestBuild ? '#' + latestBuild : '-', meta: shortDate(summary.latest_observed_at), tone: hard ? 'is-danger' : soft ? 'is-warning' : 'is-success', url: summary.latest_build_url, actionLabel: 'Open Buildkite ↗'},
      {id: 'amd-health-test-groups', label: 'LATEST AMD TEST GROUPS', value: logicalPresentation.value, meta: logicalPresentation.meta, tone: logicalPresentation.tone, scope: 'AMD nightly #' + value(latestBuild), observed: latestTestGroups.observed_at || summary.latest_observed_at, provenance: latestTestGroups.count_basis || 'Unique source-aligned test-group identities observed in this AMD nightly; topology-distinct routes remain separate and configured shards count once.', sources: [{label: 'Open published AMD health data', url: SOURCE_ASSETS.amdTestHealth}], actionLabel: logicalRows.length ? 'Browse logical test groups →' : 'Inspect count definition', onOpen: logicalRows.length ? function () { openAmdLogicalCatalog('Latest AMD logical test groups', 'Build-pinned logical groups; partial and non-passing groups are listed first', logicalRows, logicalInventory, amdHealth); } : null},
      {id: 'amd-health-observed', label: 'LATEST JOB VARIANTS', value: integer(latestVariantCount), meta: integer(passing) + ' passing - ' + integer(nonPassing) + ' non-passing exact jobs' + (notLatest ? '; ' + integer(notLatest) + ' older variants retained only for history' : ''), tone: hard ? 'is-danger' : nonPassing ? 'is-warning' : 'is-success', onOpen: function () { openAmdCatalog('Latest AMD job variants', 'Exact Buildkite job variants observed in the latest AMD nightly', currentVariants, amdHealth, 'all'); }},
      {id: 'amd-health-incidents', label: 'FAILURE OBSERVATIONS', value: integer(incidents), meta: integer(soft) + ' soft - ' + integer(hard) + ' hard' + (unknown ? ' - ' + integer(unknown) + ' unknown' : ''), tone: hard ? 'is-danger' : soft ? 'is-warning' : 'is-success', onOpen: function () { openAmdCatalog('Current AMD failure observations', 'Raw job variants with a soft or hard result in the latest AMD nightly', currentIncidents, amdHealth, 'incident'); }},
    ]));
    const note = n('div', 'ops-evidence-note is-info');
    add(note, [n('strong', '', 'AMD nightly test health. '), n('span', '', 'Each row is one exact AMD Buildkite job variant. Soft results are raw warning observations, not confirmed incidents until they recur on two distinct completed builds. The latest count is current-only; older names remain available as history and are not treated as missing failures. Upstream results are not used as AMD passes.')]);
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
    add(clusterHeader, [clusterHeading, button('Browse all ' + integer(groups.length) + ' retained variants', function () { openAmdCatalog('Retained AMD job variants', 'Search every exact AMD job variant retained across the nightly history window', groups, amdHealth, 'all'); })]);
    clusterSection.append(clusterHeader);
    const clusterGrid = n('div', 'ops-cluster-grid ops-amd-cluster-grid');
    clusterGrid.append(amdHealthCluster('Needs attention', currentIncidents.length + currentUnknown.length, 'Soft, hard, or unresolved result in the latest nightly', integer(soft) + ' soft - ' + integer(hard) + ' hard - ' + integer(currentUnknown.length) + ' unresolved', hard ? 'is-danger' : 'is-warning', function () { openAmdCatalog('AMD variants needing attention', 'Current soft, hard, and unresolved outcomes only; historical-only names are excluded', currentVariants, amdHealth, 'attention'); }));
    clusterGrid.append(amdHealthCluster('Passing now', currentPassing.length, 'Latest exact AMD job result passed', percent(currentPassing.length, latestVariantCount) + ' of latest job variants', 'is-success', function () { openAmdCatalog('AMD job variants passing now', 'Latest exact AMD nightly outcomes', currentPassing, amdHealth, 'passing'); }));
    clusterGrid.append(amdHealthCluster('Mixed history', mixed.length, 'Both passing and non-passing nightlies retained', integer(summary.build_count) + ' nightlies retained', 'is-warning', function () { openAmdCatalog('AMD job variants with mixed history', 'Job variants that passed on some AMD nightlies and had raw failures on others', mixed, amdHealth, 'mixed'); }));
    clusterGrid.append(amdHealthCluster('Historical only', missing.length, 'Older job names not present in the latest nightly', 'Not classified as current failures', 'is-neutral', function () { openAmdCatalog('Historical AMD job variants', 'Retained older names that are not part of the latest nightly observation set', missing, amdHealth, 'missing'); }));
    clusterSection.append(clusterGrid);
    host.append(clusterSection);

    const priority = currentIncidents.slice().sort(function (a, b) {
      const stateDelta = (amdLatestState(a, latestBuild) === 'hard' ? 0 : 1) - (amdLatestState(b, latestBuild) === 'hard' ? 0 : 1);
      return stateDelta || Number(amdGroupPassRate(a) || 0) - Number(amdGroupPassRate(b) || 0) || String(a.display_name || a.name).localeCompare(String(b.display_name || b.name));
    }).slice(0, 10);
    host.append(panel('Current AMD failures to inspect', integer(priority.length) + ' highest-priority raw results shown; every row opens exact nightly evidence', dataTable([
      {label: 'AMD job variant', sticky: true, width: '380px', render: function (row) { return amdGroupIdentity(row, function () { openAmdGroupDetail(row, amdHealth); }); }},
      {label: 'Queue', width: '170px', render: function (row) { return linkButton(value(row.queue), function () { navigateTo('ci-queue', {queueView: 'history', queueHistoryQueue: row.queue, queueScope: 'amd'}); }); }},
      {label: 'Retained-run pass rate', numeric: true, width: '170px', render: function (row) { const rate = amdGroupPassRate(row); return linkButton(rate === null ? '-' : rate.toFixed(1) + '%', function () { openAmdGroupDetail(row, amdHealth); }); }},
      {label: 'Latest', width: '110px', render: function (row) { const latest = amdLatestState(row, latestBuild); return linkedBadge(amdStateLabel(latest), row.latest_url, function () { openAmdGroupDetail(row, amdHealth); }, toneForState(latest)); }},
      {label: 'Pass / soft / hard', numeric: true, width: '170px', render: function (row) { return linkButton(integer(row.passed) + ' / ' + integer(row.soft_failed) + ' / ' + integer(row.hard_failed), function () { openAmdGroupDetail(row, amdHealth); }); }},
      {label: 'Latest evidence', width: '160px', render: function (row) { return externalLink('#' + value(row.latest_build_number), row.latest_url, 'ops-mono'); }},
    ], priority, integer(currentIncidents.length) + ' current AMD failure observations; use Browse all for the complete catalog', {name: 'amd-current-incidents', minWidth: '1020px'}), 'ops-amd-priority'));
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
      openHistoryEvidence(signalTerm() + ' failures on ' + agent.node, points, signalTerm() + ' failing runs on this node over ' + windowId + ', each linking to its BuildKite log', SOURCE_ASSETS.amdAgentHealth);
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
        evidence: risks.map(function (cluster) { return {id: cluster.id, label: cluster.label, valueSummary: integer(cluster.count) + ' groups', details: {definition: cluster.description, median_incident_rate: cluster.medianRate === null ? '-' : Number(cluster.medianRate).toFixed(1) + '%', current_incidents: cluster.latestIncidents}, sources: [{label: 'Open published upstream reliability', url: SOURCE_ASSETS.reliability}], onOpen: function () { openReliabilityList(cluster.label + ' reliability', cluster.description, cluster.rows, ops, reliability); }}; }),
      });
      drawChart('analytics-group-hardware', hardwareChart.canvas, {
        type: 'bar',
        data: {labels: hardware.map(function (cluster) { return cluster.label; }), datasets: [
          {label: 'Stable', data: hardware.map(function (cluster) { return cluster.count - cluster.incidentObserved; }), backgroundColor: '#35bb78'},
          {label: 'Incident observed', data: hardware.map(function (cluster) { return cluster.incidentObserved; }), backgroundColor: '#e3a63a'},
        ]},
        options: {scales: {x: {stacked: true, grid: {display: false}}, y: {stacked: true, beginAtZero: true, title: {display: true, text: 'Strict groups'}}}},
        evidenceTitle: 'Hardware reliability clusters',
        evidence: hardware.map(function (cluster) { return {id: cluster.id, label: cluster.label, valueSummary: integer(cluster.count) + ' groups - ' + integer(cluster.incidentObserved) + ' incident observed', sources: [{label: 'Open published upstream reliability', url: SOURCE_ASSETS.reliability}], onOpen: function () { openReliabilityList(cluster.label + ' test groups', 'Strict groups assigned to the ' + cluster.label + ' hardware family', cluster.rows, ops, reliability); }}; }),
      });
    });
  }

  function renderFlakeOverviewCharts(host, rows, ops, reliability) {
    const risks = reliabilityRiskClusters(rows).filter(function (cluster) { return cluster.count > 0; });
    const dispositions = [
      {id: 'passing', label: 'Passing latest', tone: '#35bb78', rows: rows.filter(function (row) { return observationState(latestObservation(row) || {}) === 'passed'; })},
      {id: 'soft', label: 'Soft failure latest', tone: '#e3a63a', rows: rows.filter(function (row) { return ['soft', 'soft_fail', 'soft_failed'].includes(observationState(latestObservation(row) || {})); })},
      {id: 'hard', label: 'Hard failure latest', tone: '#e06464', rows: rows.filter(function (row) { const latest = latestObservation(row) || {}; return isIncidentObservation(latest) && !['soft', 'soft_fail', 'soft_failed'].includes(observationState(latest)); })},
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
        evidence: risks.map(function (cluster) { return {id: cluster.id, label: cluster.label, valueSummary: integer(cluster.count) + ' candidates - ' + integer(cluster.latestIncidents) + ' incident latest', details: {definition: cluster.description, median_incident_rate: cluster.medianRate === null ? '-' : Number(cluster.medianRate).toFixed(1) + '%'}, sources: [{label: 'Open published upstream reliability', url: SOURCE_ASSETS.reliability}], onOpen: function () { openReliabilityList(cluster.label + ' flake candidates', cluster.description, cluster.rows, ops, reliability); }}; }),
      });
      drawChart('analytics-flake-disposition', dispositionChart.canvas, {
        type: 'bar',
        data: {labels: dispositions.map(function (group) { return group.label; }), datasets: [{label: 'Candidates', data: dispositions.map(function (group) { return group.rows.length; }), backgroundColor: dispositions.map(function (group) { return group.tone; })}]},
        options: {indexAxis: 'y', scales: {x: {beginAtZero: true, title: {display: true, text: 'Mixed-outcome candidates'}}, y: {grid: {display: false}}}},
        evidenceTitle: 'Latest exact result for flake candidates',
        evidence: dispositions.map(function (group) { return {id: group.id, label: group.label, valueSummary: integer(group.rows.length) + ' candidates', sources: [{label: 'Open published upstream reliability', url: SOURCE_ASSETS.reliability}], onOpen: function () { openReliabilityList(group.label, 'Mixed-history groups with this latest exact upstream result', group.rows, ops, reliability); }}; }),
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
    const seenGroupSets = new Set();
    const sides = [];
    rows.forEach(function (row) {
      const side = row[sideName] || {};
      const groupIds = (side.group_ids || []).slice().sort();
      const identity = groupIds.length ? groupIds.join('|') : String(row.id || '') + ':' + sideName;
      if (seenGroupSets.has(identity)) return;
      seenGroupSets.add(identity);
      sides.push(side);
    });
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

  const platformComparisonWindowCache = new WeakMap();

  function platformComparisonForWindow(comparison, reliability, retry, ops, windowId) {
    const cacheOwner = reliability && typeof reliability === 'object' ? reliability : comparison;
    const cacheKey = [windowId, (ops || {}).generated_at || '', (comparison || {}).generated_at || ''].join('|');
    let cached = cacheOwner && platformComparisonWindowCache.get(cacheOwner);
    if (cached && cached.has(cacheKey)) return cached.get(cacheKey);
    if (!cached && cacheOwner && typeof cacheOwner === 'object') {
      cached = new Map();
      platformComparisonWindowCache.set(cacheOwner, cached);
    }
    function remember(result) {
      if (cached) cached.set(cacheKey, result);
      return result;
    }
    if (windowId === '30d') {
      return remember(Object.assign({}, comparison, {
        rows: comparison.rows.map(function (row) { return Object.assign({}, row, {_window: analyticsWindowBounds(ops, windowId), _priorAvailable: false}); }),
        window: Object.assign(analyticsWindowBounds(ops, windowId), {completeAggregate: true, buildCount: comparison.cohort_build_count}),
      }));
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
    return remember(Object.assign({}, comparison, {
      rows: rows,
      summary: summary,
      window: Object.assign(bounds, {completeAggregate: false, buildCount: buildCount, priorBuildCount: priorBuildCount}),
    }));
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

  function comparisonGroupById(reliability, groupId) {
    return groupReliabilityByRef(reliability, groupId);
  }

  function comparisonVariantCell(variant, ops, reliability, platform) {
    const group = comparisonGroupById(reliability, variant.group_id);
    const cell = n('div', 'ops-entity-cell');
    if (group) {
      cell.append(linkButton(variant.name || 'Unnamed variant', function () {
        openGroupDetail(variant, ops, group, reliability);
      }, 'Inspect exact ' + platform + ' group history'));
    } else if (variant.latest_url) {
      cell.append(externalLink(variant.name || 'Unnamed variant', variant.latest_url));
    } else {
      cell.append(n('span', 'ops-cell-primary', variant.name || 'Unnamed variant'));
    }
    const meta = [platform, hardwareDisplayLabel(variant.hardware), (variant.queues || []).join(', ')].filter(Boolean).join(' - ');
    if (meta) cell.append(n('span', 'ops-entity-meta', meta));
    return cell;
  }

  function comparisonVariantMetric(variant, ops, reliability, textValue) {
    const group = comparisonGroupById(reliability, variant.group_id);
    if (!group) return n('span', '', textValue);
    return linkButton(textValue, function () {
      openGroupDetail(variant, ops, group, reliability);
    });
  }

  function comparisonRetryRows(row, retry, bounds) {
    const rowId = String(row.id || '');
    const identityField = row.comparison_eligible
      ? 'comparison_eligible_row_ids'
      : 'comparison_row_ids';
    function selected(source) {
      return (source || []).filter(function (item) {
        const platform = String(item.comparison_platform || '').toLowerCase();
        const rowIds = Array.isArray(item[identityField]) ? item[identityField].map(String) : [];
        if (!rowId || !['amd', 'cuda'].includes(platform) || !rowIds.includes(rowId)) return false;
        if (!bounds) return true;
        const timestamp = comparisonRetryTimestamp(item);
        return timestamp !== null && timestamp >= bounds.start && timestamp <= bounds.end;
      }).map(function (item) {
        return Object.assign({}, item, {
          _platform: String(item.comparison_platform).toUpperCase(),
          _role: item.retry_source ? 'Child retry' : 'Original attempt',
          _comparisonTimestamp: comparisonRetryTimestamp(item),
        });
      });
    }
    return {
      attempts: selected(retry && retry.retry_attempts),
      recoveries: selected(retry && retry.failed_then_passed_recoveries),
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
      {label: 'Runs', numeric: true, width: '90px', render: function (variant) { return comparisonVariantMetric(variant, ops, reliability, integer(variant.runs)); }},
      {label: 'Incidents', numeric: true, width: '110px', render: function (variant) { return comparisonVariantMetric(variant, ops, reliability, integer(variant.incidents)); }},
      {label: 'Incident frequency', numeric: true, width: '150px', render: function (variant) { return comparisonVariantMetric(variant, ops, reliability, comparisonPercent(variant, 'incident_rate_pct')); }},
      {label: 'p90 completion', numeric: true, width: '140px', render: function (variant) { return comparisonVariantMetric(variant, ops, reliability, duration(variant.p90_duration_mins)); }},
      {label: 'Latest evidence', width: '150px', render: function (variant) { const url = exactPipelineEvidenceUrl({latest_url: variant.latest_url}, 'ci'); return url ? externalLink('Open job', url) : n('span', 'ops-cell-muted', 'Unavailable'); }},
    ], variants, integer(variants.length) + (row.comparison_eligible ? ' matched upstream execution histories' : ' AMD and candidate CUDA execution histories'), {name: 'amd-cuda-variants', minWidth: '1070px'}), 'ops-comparison-variants'));

    const retryEvidenceDeferred = retry.evidence_deferred === true;
    if (focus === 'retries' && retryEvidenceDeferred) {
      const loadEvidence = button('Load exact retry attempts', function () {
        loadEvidence.disabled = true;
        loadEvidence.textContent = 'Loading exact retry evidence…';
        loadComparisonRetryEvidence().then(function (expandedRetry) {
          backOverlay();
          openPlatformComparisonDetail(row, ops, reliability, expandedRetry, focus);
        }).catch(function (error) {
          loadEvidence.disabled = false;
          loadEvidence.textContent = 'Retry loading exact evidence';
          console.error('Comparison retry evidence load failed:', error);
        });
      }, true);
      content.append(panel(
        'Exact retry evidence on demand',
        'The comparison table stays fast by loading the large attempt ledger only when requested.',
        loadEvidence,
        'ops-comparison-retry-loader'
      ));
    }
    const retryRows = retryEvidenceDeferred
      ? {attempts: [], recoveries: []}
      : comparisonRetryRows(row, retry, row._window && row._window.id !== '30d' ? row._window : null);
    if (!retryEvidenceDeferred && (focus === 'retries' || retryRows.attempts.length || retryRows.recoveries.length)) {
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
    content.append(sourceActions([{label: 'Open published comparison data', url: SOURCE_ASSETS.comparison}, {label: 'Open upstream CI pipeline', url: 'https://buildkite.com/vllm/ci'}]));
    openOverlay(
      row.label + (row.comparison_eligible ? ': AMD vs CUDA' : ': AMD comparison review'),
      (row.comparison_eligible ? 'Exact matched execution histories' : 'AMD and candidate CUDA execution histories requiring review') + ', ' + value((row._window || {}).id, '30d') + ' rates, latency, and Buildkite evidence',
      content,
      true,
      'amd-cuda-' + row.id
    );
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
    toolbar.append(n('span', 'ops-toolbar-label', 'Complete 30-day comparison'));
    const context = n('span', 'ops-window-context');
    context.textContent = integer(windowInfo.buildCount) + ' completed upstream main builds · precomputed for fast inspection';
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
          return {label: row.label, valueSummary: config.evidenceSummary(row), sources: [{label: 'Open published upstream comparison', url: SOURCE_ASSETS.comparison}], onOpen: function () { openPlatformComparisonDetail(row, config.ops, config.reliability, config.retry, config.focus); }};
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
      return Number(b.amd.incident_rate_pct || 0) - Number(a.amd.incident_rate_pct || 0) || a.label.localeCompare(b.label);
    });
    const active = sorted.filter(function (row) { return Number(row.amd.runs || 0) > 0; });
    const comparable = active.filter(function (row) { return row.comparison_eligible; });
    const chartRows = comparable.filter(function (row) {
      return Number(row.amd.incidents || 0) > 0 || Number(row.cuda.incidents || 0) > 0;
    });
    analyticsWindowControl(host, comparison);
    host.append(statusStrip([
      {label: 'ACTIVE AMD VARIANTS', value: integer(active.length) + ' / ' + integer(summary.amd_comparison_row_count || summary.amd_base_group_count), meta: integer(amd.runs) + ' exact attempts in ' + state.analyticsWindow, onOpen: function () { openTableBrowser({id: 'flake-comparison-all', title: 'AMD and CUDA incident comparison', subtitle: state.analyticsWindow + ' upstream branch=main window', rows: sorted, columns: comparisonFlakeColumns(ops, reliability, retry), searchText: comparisonSearchText, geometry: {name: 'flake-comparison', minWidth: '1260px'}}); }},
      {label: 'AMD INCIDENTS', value: integer(amd.incidents), meta: comparisonPercent(amd, 'incident_rate_pct') + ' of ' + integer(amd.runs) + ' attempts', tone: Number(amd.incidents) ? 'is-warning' : 'is-success'},
      {label: 'PAIRED AMD / CUDA', value: comparisonPercent(pairedAmd, 'incident_rate_pct') + ' / ' + comparisonPercent(cuda, 'incident_rate_pct'), meta: integer(comparable.length) + ' active exact pairs'},
    ]));
    const note = n('div', 'ops-evidence-note is-info');
    const retentionNote = Number(summary.history_incomplete_variant_count || 0)
      ? ' ' + integer(summary.history_incomplete_variant_count) + ' high-frequency variants reached the retained-history cap; their window values are lower bounds.' : '';
    add(note, [n('strong', '', 'AMD-first, upstream-only incident evidence. '), n('span', '', 'The complete 30-day aggregate is precomputed. Exact CUDA deltas exclude generic or ambiguous references. Incident frequency is not a test-case flake probability.' + retentionNote)]);
    host.append(note);
    renderComparisonChart(host, {
      title: 'AMD incident frequency - ' + state.analyticsWindow,
      subtitle: integer(chartRows.length) + ' exact pairs with current incidents; complete 30-day AMD burden beside CUDA equivalents; zero-incident groups remain in the table',
      key: 'analytics-platform-flakes',
      rows: chartRows.slice(0, 12),
      emptyTitle: 'No incidents in exact AMD/CUDA pairs for this window.',
      emptyMessage: 'Active zero-incident groups remain available in the table.',
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
    host.append(compactTablePanel('AMD incident comparison', integer(active.length) + ' active AMD variant rows - ' + integer(comparable.length) + ' active exact CUDA pairs', comparisonFlakeColumns(ops, reliability, retry), sorted, {
      id: 'flake-comparison-browser',
      limit: 12,
      alwaysBrowse: true,
      browserSubtitle: 'Exact 30-day counts, percentages, and matched CUDA context',
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
      return Number(b.amd.retry_frequency_pct || 0) - Number(a.amd.retry_frequency_pct || 0) || Number(b.amd.child_retry_attempts || 0) - Number(a.amd.child_retry_attempts || 0) || a.label.localeCompare(b.label);
    });
    const active = sorted.filter(function (row) { return Number(row.amd.runs || 0) > 0; });
    const comparable = active.filter(function (row) { return row.comparison_eligible; });
    const chartRows = comparable.filter(function (row) {
      return Number(row.amd.child_retry_attempts || 0) > 0
        || Number(row.cuda.child_retry_attempts || 0) > 0
        || Number(row.amd.recovered_chains || 0) > 0;
    });
    analyticsWindowControl(host, comparison);
    host.append(statusStrip([
      {label: 'AMD CHILD RETRIES', value: integer(amd.child_retry_attempts), meta: integer(amd.retry_involved_attempts) + ' total retry-involved attempts', tone: Number(amd.child_retry_attempts) ? 'is-warning' : 'is-success'},
      {label: 'AMD CHILD RETRY SHARE', value: comparisonPercent(amd, 'retry_frequency_pct'), meta: integer(amd.child_retry_attempts) + ' / ' + integer(amd.runs) + ' terminal attempts'},
      {label: 'RECOVERED / PAIRED CUDA', value: integer(amd.recovered_chains) + ' / ' + comparisonPercent(cuda, 'retry_frequency_pct'), meta: comparisonPercent(amd, 'retry_recovery_rate_pct') + ' AMD recovery rate - ' + integer(comparable.length) + ' active pairs'},
    ]));
    const note = n('div', 'ops-evidence-note is-info');
    const retryRetention = Number(summary.history_incomplete_variant_count || 0)
      ? ' ' + integer(summary.history_incomplete_variant_count) + ' high-frequency variants reached the retained-history cap.' : '';
    add(note, [n('strong', '', 'Explicit Buildkite retry metadata only. '), n('span', '', 'The complete 30-day aggregate is precomputed. Recovery means an exact failed attempt linked to a passing retry; mixed outcomes alone are not counted.' + retryRetention)]);
    host.append(note);
    renderComparisonChart(host, {
      title: 'AMD retry burden - ' + state.analyticsWindow,
      subtitle: integer(chartRows.length) + ' exact pairs with retry activity; child retry and AMD recovery shares are shown; zero-retry groups remain in the table',
      key: 'analytics-platform-retries',
      rows: chartRows.slice(0, 12),
      emptyTitle: 'No explicit child retries in exact AMD/CUDA pairs for this window.',
      emptyMessage: 'Active zero-retry groups remain available in the table.',
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
      {label: 'AMD recovered', numeric: true, width: '115px', render: function (row) { return linkButton(integer(row.amd.recovered_chains), function () { openPlatformComparisonDetail(row, ops, reliability, retry, 'retries'); }); }},
      {label: 'CUDA child retries / attempts', numeric: true, width: '180px', render: function (row) { return linkButton(comparisonCountRate(row.cuda, 'child_retry_attempts', 'retry_frequency_pct'), function () { openPlatformComparisonDetail(row, ops, reliability, retry, 'retries'); }); }},
      {label: 'AMD / CUDA gap', numeric: true, width: '120px', render: function (row) { const control = linkButton(comparisonDelta(row.retry_frequency_delta_pp, ' pp'), function () { openPlatformComparisonDetail(row, ops, reliability, retry, 'retries'); }); control.classList.add('ops-comparison-delta', comparisonTone(row.retry_frequency_delta_pp, 2)); return control; }},
      {label: 'Evidence', width: '90px', render: function (row) { return linkButton('Inspect', function () { openPlatformComparisonDetail(row, ops, reliability, retry, 'retries'); }, 'Inspect exact retry attempts and recoveries'); }},
    ];
    host.append(compactTablePanel('AMD retry comparison', integer(active.length) + ' active AMD variant rows - ' + integer(comparable.length) + ' active exact CUDA pairs', columns, sorted, {
      id: 'retry-comparison-browser',
      limit: 12,
      alwaysBrowse: true,
      browserSubtitle: 'Exact 30-day child-retry counts, recoveries, and matched CUDA evidence',
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

  function analyticsViewSelector() {
    return segmented([
      {id: 'groups', label: 'AMD test health'}, {id: 'flakes', label: 'Flake comparison'},
      {id: 'retries', label: 'Retry comparison'}, {id: 'latency', label: 'Latency comparison'},
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
    host.append(analyticsViewSelector());
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
        {id: 'ci', label: 'Upstream CI'},
      ], state.analyticsPipeline, function (pipeline) { setRouteState('ci-analytics', 'analyticsPipeline', pipeline, 'analytics_pipeline'); }, 'Nightly pipeline'));
      host.append(controls);
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
        {label: 'LATEST ' + nightlyName.toUpperCase() + ' NIGHTLY', value: latestNightly.number ? '#' + latestNightly.number : '-', meta: latestNightly.created_at ? shortDate(latestNightly.created_at) : 'No completed nightly', tone: toneForState(latestNightly.state), url: latestNightly.number ? exactPipelineBuildUrl(latestNightly, state.analyticsPipeline) : null},
        {label: 'JOB VARIANTS OBSERVED', value: integer(latestNightly.total_groups), meta: 'exact jobs in the latest completed nightly', onOpen: function () { if (latestNightly.number) openBuildDetail(latestNightly, nightlyName + ' build #' + value(latestNightly.number)); }},
        {label: 'NEW FAILURES', value: latestMovement ? integer(latestMovement.new.length) : '-', meta: latestMovement ? 'not failing in the preceding observed nightly' : 'unavailable in this snapshot', tone: latestMovement && latestMovement.new.length ? 'is-danger' : latestMovement ? 'is-success' : 'is-neutral', onOpen: function () { if (latestNightly.number) openBuildDetail(latestNightly, nightlyName + ' build #' + value(latestNightly.number)); }},
        {label: 'RECURRING FAILURES', value: latestMovement ? integer(latestMovement.recurring.length) : '-', meta: latestMovement ? 'failed in the preceding observed nightly too' : 'unavailable in this snapshot', tone: latestMovement && latestMovement.recurring.length ? 'is-warning' : latestMovement ? 'is-success' : 'is-neutral', onOpen: function () { if (latestNightly.number) openBuildDetail(latestNightly, nightlyName + ' build #' + value(latestNightly.number)); }},
        {label: 'FIXED', value: latestMovement ? integer(latestMovement.fixed.length) : '-', meta: latestMovement ? 'failed previously and passed now' : 'unavailable in this snapshot', tone: latestMovement && latestMovement.fixed.length ? 'is-success' : 'is-neutral', onOpen: function () { if (latestNightly.number) openBuildDetail(latestNightly, nightlyName + ' build #' + value(latestNightly.number)); }},
      ]));
      const nightlyNote = n('div', 'ops-evidence-note ' + (latestMovement ? 'is-info' : 'is-warning'));
      add(nightlyNote, latestMovement
        ? [
          n('strong', '', state.analyticsPipeline === 'amd-ci' ? 'AMD nightly history. ' : 'Upstream CI nightly history. '),
          n('span', '', (state.analyticsPipeline === 'amd-ci'
            ? 'AMD is the default operational signal. '
            : 'This alternate upstream CI view does not replace AMD health. ')
            + 'Every current hard or soft failure is counted once as new or recurring. A previous failure that passes is fixed. Missing or skipped jobs are omitted.'),
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
      evidence: chronologicalMovementBuilds.map(function (buildRow) { const movement = nightlyFailureMovement(buildRow); return {label: '#' + buildRow.number, timestamp: buildRow.created_at, url: exactPipelineBuildUrl(buildRow, state.analyticsPipeline), valueSummary: integer(movement.new.length) + ' new - ' + integer(movement.recurring.length) + ' recurring - ' + integer(movement.fixed.length) + ' fixed', details: {state: buildRow.state, new_failure: movement.new.length, recurring_failure: movement.recurring.length, fixed: movement.fixed.length}}; })});
      host.append(dataTable([
        {label: nightlyName + ' nightly', sticky: true, width: '130px', render: function (r) { return externalLink('#' + r.number, exactPipelineBuildUrl(r, state.analyticsPipeline), 'ops-mono'); }},
        {label: 'State', width: '120px', render: function (r) { return linkedBadge(r.state, exactPipelineBuildUrl(r, state.analyticsPipeline)); }},
        {label: 'Job variants observed', numeric: true, width: '160px', render: function (r) { return linkButton(integer(r.total_groups), function () { openBuildDetail(r, nightlyName + ' build #' + value(r.number)); }); }},
        {label: 'New failure', numeric: true, width: '120px', render: function (r) { const count = nightlyFailureCount(r, 'new'); return linkButton(count === null ? '-' : integer(count), function () { openBuildDetail(r, nightlyName + ' build #' + value(r.number)); }); }},
        {label: 'Recurring failure', numeric: true, width: '140px', render: function (r) { const count = nightlyFailureCount(r, 'recurring'); return linkButton(count === null ? '-' : integer(count), function () { openBuildDetail(r, nightlyName + ' build #' + value(r.number)); }); }},
        {label: 'Fixed', numeric: true, width: '90px', render: function (r) { const count = nightlyFailureCount(r, 'fixed'); return linkButton(count === null ? '-' : integer(count), function () { openBuildDetail(r, nightlyName + ' build #' + value(r.number)); }); }},
        {label: 'Started', width: '180px', render: function (r) { return shortDate(r.created_at); }},
      ], builds, nightlyName + ' nightly failure movement; this selector does not change canonical upstream reliability', {name: 'nightly', minWidth: '980px'}));
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
          evidence: outcomes.map(function (row) { return {label: row[0], valueSummary: integer(row[1]) + ' attempts', sources: [{label: 'Open published retry ledger', url: SOURCE_ASSETS.comparisonRetryEvidence}]}; }),
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
        evidence: slowestRows.map(function (row) { const full = groupReliabilityByRef(reliability, row.evidence_ref); return {label: row.name, valueSummary: 'p90 ' + duration(row.p90_dur) + ' - median ' + duration(row.median_dur), sources: [{label: 'Open published all-main history', url: SOURCE_ASSETS.reliability}], onOpen: function () { if (full) openGroupDetail(row, ops, full, reliability); }}; }),
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
      {label: 'Evidence', width: '150px', render: function (r) { const rel = groupReliabilityByRef(reliability, r.evidence_ref); return rel ? linkButton(integer(evidenceObservations(rel).length) + ' runs', function () { openGroupDetail(r, ops, rel, reliability); }, 'Inspect exact observations for evidence ID ' + value(r.evidence_ref)) : externalLink('Published ranking', SOURCE_ASSETS.reliability); }},
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
      if (!queueMatchesScope(entry[0])) return false;
      return includeIdle || queueIsActiveProblem(entry[1] || {});
    });
  }

  function officialWaitValue(row, metric) {
    const measured = ((row || {}).official_wait || {})[metric];
    return measured !== null && measured !== undefined && Number.isFinite(Number(measured)) ? Number(measured) : null;
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

  function waitSource(row, metric) {
    if ((metric === 'p50' || metric === 'p95') && officialWaitValue(row, metric) !== null) return 'official_wait';
    if (metric === 'p99' && sampleWaitValue(row, metric) !== null) return 'sample_wait';
    const current = (row || {}).current_wait || {};
    const source = (current[metric] && current[metric].source) || (row || {})[metric + '_wait_source'] || (row || {}).wait_source || null;
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

  function historyWaitObservation(row, metric, sourceFamily) {
    const sourceKey = sourceFamily === 'sample_wait' ? 'archive_sample_wait_peaks' : 'archive_wait_peaks';
    let peak = ((row || {})[sourceKey] || {})[metric];
    if (!peak && sourceFamily === 'sample_wait') {
      const compatibilityPeak = ((row || {}).archive_wait_peaks || {})[metric];
      if (compatibilityPeak && compatibilityPeak.source === 'sample_wait') peak = compatibilityPeak;
    }
    if (peak && peak.value !== null && peak.value !== undefined) {
      const detail = peak.provider && peak.provider !== peak.source ? peak.source + ' - ' + peak.provider : peak.source;
      return {value: peak.value, source: peak.source, sourceDetail: detail, sampleCount: peak.sample_count, sampleExpected: peak.sample_expected, sampleComplete: peak.sample_complete, observedAt: peak.observed_at};
    }
    if (sourceFamily === 'official_wait') {
      const nativeValue = officialWaitValue(row || {}, metric);
      return {value: nativeValue, source: nativeValue === null ? null : 'official_wait', sourceDetail: nativeValue === null ? null : value((row || {}).official_wait_source || 'queue_native_metrics'), sampleCount: null, sampleExpected: null, sampleComplete: null, observedAt: null};
    }
    if (sourceFamily === 'sample_wait') {
      const sampledValue = sampleWaitValue(row || {}, metric);
      return {value: sampledValue, source: sampledValue === null ? null : 'sample_wait', sourceDetail: sampledValue === null ? null : 'sample_wait - ' + value((row || {}).sample_wait_source || 'scheduled_jobs'), sampleCount: waitSampleCount(row || {}), sampleExpected: row.wait_sample_expected_count, sampleComplete: row.wait_sample_complete, observedAt: null};
    }
    return {value: waitValue(row || {}, metric), source: waitSource(row || {}, metric), sourceDetail: waitSourceDetail(row || {}, metric), sampleCount: waitSampleCount(row || {}), sampleExpected: row.wait_sample_expected_count, sampleComplete: row.wait_sample_complete, observedAt: null};
  }

  function queueHasWaitMeasurement(row) {
    return ['p50', 'p95', 'p99'].some(function (metric) {
      return [
        historyWaitObservation(row || {}, metric),
        historyWaitObservation(row || {}, metric, 'sample_wait'),
      ].some(function (observation) {
        return observation.value !== null && observation.value !== undefined && Number.isFinite(Number(observation.value));
      });
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
    const scopeLabel = selectedQueue || queueScopeLabel(state.queueScope, true);
    const content = rows.length ? dataTable([
      {label: 'Queue', sticky: true, render: function (item) { return linkButton(item.name, function () { openQueueDetail(item.name, item.row, []); }); }},
      {label: 'Running', numeric: true, render: function (item) { return integer(item.row.running); }},
      {label: 'Waiting', numeric: true, render: function (item) { return integer(item.row.waiting); }},
      {label: 'p50 primary', numeric: true, render: function (item) { return duration(historyWaitObservation(item.row, 'p50').value); }},
      {label: 'p95 primary', numeric: true, render: function (item) { return duration(historyWaitObservation(item.row, 'p95').value); }},
      {label: 'p95 reconstructed', numeric: true, render: function (item) { return duration(historyWaitObservation(item.row, 'p95', 'sample_wait').value); }},
      {label: 'p99 sampled', numeric: true, render: function (item) { return duration(historyWaitObservation(item.row, 'p99', 'sample_wait').value); }},
      {label: 'Wait source', render: function (item) { return value([historyWaitObservation(item.row, 'p95').sourceDetail, historyWaitObservation(item.row, 'p95', 'sample_wait').sourceDetail, historyWaitObservation(item.row, 'p99', 'sample_wait').sourceDetail].filter(Boolean).filter(function (sourceName, index, all) { return all.indexOf(sourceName) === index; }).join(', ')); }},
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
        {label: 'Open current queue snapshot', url: SOURCE_ASSETS.queueSection},
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
    function highest(metric, sourceFamily) {
      return entries.map(function (entry) {
        const observed = historyWaitObservation(entry[1] || {}, metric, sourceFamily);
        return {queue: entry[0], value: observed.value, source: observed.source, sourceDetail: observed.sourceDetail, sampleCount: observed.sampleCount, sampleExpected: observed.sampleExpected, sampleComplete: observed.sampleComplete, observedAt: observed.observedAt};
      }).filter(function (row) {
        return row.value !== null && row.value !== undefined && Number.isFinite(Number(row.value));
      }).sort(function (a, b) { return Number(b.value) - Number(a.value) || a.queue.localeCompare(b.queue); });
    }
    function leaders(metric, sourceFamily) {
      const ranked = highest(metric, sourceFamily);
      if (!ranked.length) return {leader: {}, rows: []};
      const max = Number(ranked[0].value);
      return {leader: ranked[0], rows: ranked.filter(function (row) { return Number(row.value) === max; })};
    }
    const p50Rank = leaders('p50'), p95Rank = leaders('p95'), p99Rank = leaders('p99');
    const sampleP50Rank = leaders('p50', 'sample_wait');
    const sampleP95Rank = leaders('p95', 'sample_wait');
    const p50 = p50Rank.leader, p95 = p95Rank.leader, p99 = p99Rank.leader;
    const sampleP50 = sampleP50Rank.leader, sampleP95 = sampleP95Rank.leader;
    return {
      ts: snapshot.ts,
      snapshot: snapshot,
      p50: p50.value !== undefined ? Number(p50.value) : null,
      p95: p95.value !== undefined ? Number(p95.value) : null,
      p99: p99.value !== undefined ? Number(p99.value) : null,
      sampleP50: sampleP50.value !== undefined ? Number(sampleP50.value) : null,
      sampleP95: sampleP95.value !== undefined ? Number(sampleP95.value) : null,
      p50Queue: p50.queue,
      p95Queue: p95.queue,
      p99Queue: p99.queue,
      sampleP50Queue: sampleP50.queue,
      sampleP95Queue: sampleP95.queue,
      p50Queues: p50Rank.rows.map(function (row) { return row.queue; }),
      p95Queues: p95Rank.rows.map(function (row) { return row.queue; }),
      p99Queues: p99Rank.rows.map(function (row) { return row.queue; }),
      sampleP50Queues: sampleP50Rank.rows.map(function (row) { return row.queue; }),
      sampleP95Queues: sampleP95Rank.rows.map(function (row) { return row.queue; }),
      p50Source: p50.source,
      p95Source: p95.source,
      p99Source: p99.source,
      sampleP50Source: sampleP50.source,
      sampleP95Source: sampleP95.source,
      p50SourceDetail: p50.sourceDetail,
      p95SourceDetail: p95.sourceDetail,
      p99SourceDetail: p99.sourceDetail,
      sampleP50SourceDetail: sampleP50.sourceDetail,
      sampleP95SourceDetail: sampleP95.sourceDetail,
      p50ObservedAt: p50.observedAt,
      p95ObservedAt: p95.observedAt,
      p99ObservedAt: p99.observedAt,
      sampleP50ObservedAt: sampleP50.observedAt,
      sampleP95ObservedAt: sampleP95.observedAt,
      p50SampleCount: p50.sampleCount,
      p95SampleCount: p95.sampleCount,
      p99SampleCount: p99.sampleCount,
      sampleP50SampleCount: sampleP50.sampleCount,
      sampleP95SampleCount: sampleP95.sampleCount,
      p50SampleExpected: p50.sampleExpected,
      p95SampleExpected: p95.sampleExpected,
      p99SampleExpected: p99.sampleExpected,
      sampleP50SampleExpected: sampleP50.sampleExpected,
      sampleP95SampleExpected: sampleP95.sampleExpected,
      p50SampleComplete: p50.sampleComplete,
      p95SampleComplete: p95.sampleComplete,
      p99SampleComplete: p99.sampleComplete,
      sampleP50SampleComplete: sampleP50.sampleComplete,
      sampleP95SampleComplete: sampleP95.sampleComplete,
    };
  }

  function queueLeaderSummary(queues) {
    const names = (queues || []).filter(Boolean);
    if (!names.length) return 'not measured';
    if (names.length === 1) return names[0];
    if (names.length === 2) return names.join(', ');
    return integer(names.length) + ' queues tied';
  }

  function queuePressureRows(snapshot, history, publishedBaseline) {
    return selectedQueues(snapshot, true).map(function (entry) {
      const name = entry[0], currentRow = entry[1] || {};
      const loads = (history || []).filter(function (point) { return point && point.ts !== snapshot.ts; }).map(function (point) {
        const row = ((point.queues || {})[name]);
        return row && !row.history_observation_only ? Number(row.running || 0) + Number(row.waiting || 0) : null;
      }).filter(function (load) { return Number.isFinite(load); });
      const retained = (publishedBaseline || {})[name] || {};
      const current = Number(currentRow.running || 0) + Number(currentRow.waiting || 0);
      const baselineMedian = loads.length ? percentileValue(loads, 0.5) : Number.isFinite(Number(retained.median)) ? Number(retained.median) : null;
      const baselineP95 = loads.length ? percentileValue(loads, 0.95) : Number.isFinite(Number(retained.p95)) ? Number(retained.p95) : null;
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
        historyPoints: loads.length || Number(retained.snapshot_count || 0),
        elevated: baselineP95 !== null && current > Number(baselineP95),
      };
    }).filter(function (row) {
      return row.current > 0 || Number(row.baselineP95 || 0) > 0;
    }).sort(function (a, b) {
      if (a.elevated !== b.elevated) return a.elevated ? -1 : 1;
      return Number(b.current || 0) - Number(a.current || 0);
    });
  }

  function queueChartPointsWithBreaks(points, nowMs) {
    const highResolutionCutoff = nowMs - 48 * 60 * 60 * 1000;
    const output = [];
    (points || []).forEach(function (point, index) {
      const previous = index ? points[index - 1] : null;
      const previousMs = previous ? queueTimestamp(previous.ts) : -Infinity;
      const currentMs = queueTimestamp(point.ts);
      if (
        previous
        && previousMs >= highResolutionCutoff
        && currentMs >= highResolutionCutoff
        && currentMs - previousMs > 30 * 60 * 1000
      ) {
        output.push({ts: new Date(previousMs + (currentMs - previousMs) / 2).toISOString(), isGap: true});
      }
      output.push(point);
    });
    return output;
  }

  const QUEUE_LIFECYCLE_COUNT_FIELDS = [
    'incoming', 'served', 'completed', 'passed', 'failed', 'soft_failed',
    'canceled', 'timed_out', 'expired', 'broken', 'skipped', 'other_outcomes',
    'retry_attempts_completed', 'retried_jobs_completed',
  ];

  function queueLifecycleNumber(raw) {
    return raw !== null && raw !== undefined && raw !== '' && Number.isFinite(Number(raw))
      ? Number(raw)
      : null;
  }

  function queueLifecycleRows(payload, requestedScope) {
    return Object.entries((payload || {}).queues || {}).filter(function (entry) {
      return queueMatchesScope(entry[0], requestedScope);
    }).map(function (entry) {
      return {name: entry[0], metrics: entry[1] || {}};
    }).sort(function (left, right) {
      return Number((right.metrics || {}).incoming || 0) - Number((left.metrics || {}).incoming || 0)
        || compareText(left.name, right.name);
    });
  }

  function queueLifecycleTotals(payload, rows) {
    const published = (payload || {}).totals || {};
    const publishedQueues = Object.keys((payload || {}).queues || {});
    if (!publishedQueues.length) return Object.assign({}, published);
    const totals = Object.assign({}, published);
    QUEUE_LIFECYCLE_COUNT_FIELDS.forEach(function (field) {
      totals[field] = (rows || []).reduce(function (sum, row) {
        return sum + Number((row.metrics || {})[field] || 0);
      }, 0);
    });
    return totals;
  }

  function queueLifecycleNestedMetric(row, blockName, metric) {
    return queueLifecycleNumber((((row || {})[blockName] || {})[metric]));
  }

  function queueLifecycleMinutes(row, blockName, metric) {
    const seconds = queueLifecycleNestedMetric(row, blockName, metric);
    return seconds === null ? null : seconds / 60;
  }

  function queueLifecycleDuration(row, blockName, metric) {
    return duration(queueLifecycleMinutes(row, blockName, metric));
  }

  function queueLifecycleHourlyRows(payload) {
    return ((payload || {}).hourly || []).map(function (row) {
      const timestamp = row.ts || row.hour || row.start || row.window_start || null;
      return Object.assign({}, row.totals || {}, row, {ts: timestamp});
    }).filter(function (row) {
      return queueTimestamp(row.ts) > -Infinity;
    }).sort(function (left, right) {
      return queueTimestamp(left.ts) - queueTimestamp(right.ts);
    });
  }

  function queueLifecycleCoverage(payload) {
    payload = payload || {};
    const coverage = payload.coverage || {};
    const windowBlock = payload.window || {};
    const problems = [];
    const status = String(coverage.status || '').trim().toLowerCase();
    if (coverage.api_collection_performed === false) {
      problems.push('Buildkite API collection has not run; zero-valued seed placeholders are not observations');
    }
    if (coverage.api_complete === false) {
      problems.push('Buildkite API collection is incomplete');
    }
    if (coverage.complete === false) {
      problems.push(coverage.reason || coverage.detail || 'overall lifecycle coverage is incomplete');
    }
    if (status && !['complete', 'full', 'ok', 'healthy'].includes(status)) {
      problems.push('collector coverage status is ' + status);
    }
    const ratio = queueLifecycleNumber(coverage.ratio);
    const percentValue = queueLifecycleNumber(coverage.percent !== undefined ? coverage.percent : coverage.coverage_percent);
    if (ratio !== null && ratio < 1) problems.push((ratio * 100).toFixed(1) + '% of expected coverage was observed');
    if (percentValue !== null && percentValue < 100) problems.push(percentValue.toFixed(1) + '% of expected coverage was observed');
    const expected = queueLifecycleNumber(coverage.expected !== undefined ? coverage.expected : coverage.expected_count);
    const observed = queueLifecycleNumber(coverage.observed !== undefined ? coverage.observed : coverage.observed_count);
    if (expected !== null && observed !== null && observed < expected) {
      problems.push(integer(observed) + ' of ' + integer(expected) + ' expected observations were retained');
    }
    const expectedHours = queueLifecycleNumber(coverage.expected_hours);
    const observedHours = queueLifecycleNumber(coverage.observed_hours);
    if (expectedHours !== null && observedHours !== null && observedHours < expectedHours) {
      problems.push(observedHours.toFixed(1) + ' of ' + expectedHours.toFixed(1) + ' expected hours were covered');
    }
    Object.entries(coverage.metric_exhaustiveness || {}).forEach(function (entry) {
      const metric = entry[0], detail = entry[1] || {};
      if (detail.complete === false) {
        problems.push(metric + ' exhaustiveness is limited' + (detail.limitation ? ': ' + detail.limitation : ''));
      }
    });
    const hours = queueLifecycleNumber(windowBlock.hours);
    if (hours === null) problems.push('rolling window duration is missing; expected an exact rolling 2h window');
    else if (hours !== 2) problems.push('published window is ' + hours + 'h; expected an exact rolling 2h window');
    if (!windowBlock.start || !(windowBlock.end_exclusive || windowBlock.end)) problems.push('rolling window boundaries are missing');
    if (!queueLifecycleHourlyRows(payload).length) problems.push('no hourly lifecycle buckets were published');
    const summaryParts = [];
    if (coverage.api_complete === true) summaryParts.push('API collection complete');
    if (coverage.complete === true) summaryParts.push('collection complete');
    if (status) summaryParts.push(status);
    if (observed !== null && expected !== null) summaryParts.push(integer(observed) + ' / ' + integer(expected) + ' observations');
    else if (percentValue !== null) summaryParts.push(percentValue.toFixed(1) + '%');
    else if (ratio !== null) summaryParts.push((ratio * 100).toFixed(1) + '%');
    if (coverage.source) summaryParts.push(String(coverage.source));
    return {
      complete: problems.length === 0,
      problems: Array.from(new Set(problems.map(String))),
      summary: summaryParts.join(' - ') || 'collector did not publish a numeric coverage summary',
    };
  }

  function queueLifecycleObservationsAvailable(payload) {
    return (((payload || {}).coverage || {}).api_collection_performed) !== false;
  }

  function queueLifecycleDisplayCount(payload, raw) {
    return queueLifecycleObservationsAvailable(payload) ? integer(raw) : '-';
  }

  function queueLifecyclePayloadValid(payload) {
    return !!(
      payload && typeof payload === 'object'
      && payload.schema_version !== undefined
      && payload.generated_at
      && payload.window && typeof payload.window === 'object'
      && payload.coverage && typeof payload.coverage === 'object'
      && payload.totals && typeof payload.totals === 'object'
      && payload.queues && typeof payload.queues === 'object'
      && Array.isArray(payload.hourly)
    );
  }

  function queueLifecycleCandidateQuality(payload) {
    const coverage = (payload || {}).coverage || {};
    if (coverage.api_collection_performed === true || coverage.api_complete === true) return 2;
    if (coverage.api_collection_performed === false) return 0;
    return 1;
  }

  function compareQueueLifecycleCandidates(left, right) {
    const qualityOrder = queueLifecycleCandidateQuality(right.payload) - queueLifecycleCandidateQuality(left.payload);
    if (qualityOrder) return qualityOrder;
    const leftGeneratedAt = queueTimestamp(left.payload.generated_at);
    const rightGeneratedAt = queueTimestamp(right.payload.generated_at);
    if (leftGeneratedAt !== rightGeneratedAt) return rightGeneratedAt > leftGeneratedAt ? 1 : -1;
    return left.priority - right.priority;
  }

  async function loadQueueLifecycle() {
    const sources = [SOURCE_ASSETS.queueLifecycle, SOURCE_ASSETS.queueLifecycleFallback];
    const results = await Promise.allSettled(sources.map(function (source) {
      return fetchJSON(source);
    }));
    const candidates = results.map(function (result, index) {
      return result.status === 'fulfilled' && queueLifecyclePayloadValid(result.value)
        ? {payload: result.value, source: sources[index], priority: index}
        : null;
    }).filter(Boolean);
    if (!candidates.length) throw new Error('No queue lifecycle aggregate is available');
    candidates.sort(compareQueueLifecycleCandidates);
    return Object.assign({}, candidates[0].payload, {__sourceAsset: candidates[0].source});
  }

  function renderQueueLifecycle(host, payload) {
    payload = payload || {};
    const windowBlock = payload.window || {};
    const windowEnd = windowBlock.end_exclusive || windowBlock.end;
    const rows = queueLifecycleRows(payload, 'canonical');
    const totals = queueLifecycleTotals(payload, rows);
    const coverage = queueLifecycleCoverage(payload);
    const observationsAvailable = queueLifecycleObservationsAvailable(payload);
    const lifecycleSource = payload.__sourceAsset || SOURCE_ASSETS.queueLifecycle;
    const windowHours = queueLifecycleNumber(windowBlock.hours);
    const windowLabel = windowHours === null ? 'rolling lifecycle window' : 'rolling ' + windowHours + 'h lifecycle window';
    const windowNote = n('div', 'ops-evidence-note is-info');
    add(windowNote, observationsAvailable ? [
      n('strong', '', 'Exact observed direct events - rolling 2h. '),
      n('span', '', 'Observed runnable_at, started_at, and finished_at events cover ' + shortDate(windowBlock.start) + ' through ' + shortDate(windowEnd)
        + ' for the collector\'s fixed twelve canonical AMD queues. Buildkite REST does not filter these job event timestamps directly; the finished, created, and active organization-build cohorts retain exact observations, but the population is not presented as exhaustive. See the published coverage warning. Outcomes apply only to observed completed jobs.'),
    ] : [
      n('strong', '', 'Lifecycle observations unavailable. '),
      n('span', '', 'This is a structural bootstrap aggregate; Buildkite API collection has not run. Its zero-valued seed placeholders are not observations and are rendered as unavailable.'),
    ]);
    host.append(windowNote);
    if (coverage.problems.length) {
      const warning = n('div', 'ops-evidence-note is-warning');
      add(warning, [n('strong', '', 'Lifecycle coverage warning. '), n('span', '', coverage.problems.join('; ') + '. Missing observations are not rendered as zero.')]);
      host.append(warning);
    }
    const completed = Number(totals.completed || 0);
    const passed = Number(totals.passed || 0);
    const failed = Number(totals.failed || 0);
    const softFailed = Number(totals.soft_failed || 0);
    const canceled = Number(totals.canceled || 0);
    const timedOut = Number(totals.timed_out || 0);
    const expired = Number(totals.expired || 0);
    const broken = Number(totals.broken || 0);
    const skipped = Number(totals.skipped || 0);
    const otherOutcomes = Number(totals.other_outcomes || 0);
    const retriedJobs = Number(totals.retried_jobs_completed || 0);
    const cardMetaUnavailable = 'Buildkite API collection has not run';
    host.append(statusStrip([
      {id: 'queue-lifecycle-incoming', label: 'OBSERVED INCOMING - ROLLING 2H', value: queueLifecycleDisplayCount(payload, totals.incoming), meta: observationsAvailable ? 'direct runnable_at events - not exhaustive' : cardMetaUnavailable, observed: observationsAvailable ? windowEnd : null, provenance: lifecycleSource},
      {id: 'queue-lifecycle-served', label: 'OBSERVED SERVED - ROLLING 2H', value: queueLifecycleDisplayCount(payload, totals.served), meta: observationsAvailable ? 'direct started_at events - not exhaustive' : cardMetaUnavailable, observed: observationsAvailable ? windowEnd : null, provenance: lifecycleSource},
      {id: 'queue-lifecycle-completed', label: 'OBSERVED COMPLETED - ROLLING 2H', value: queueLifecycleDisplayCount(payload, totals.completed), meta: observationsAvailable ? (completed ? percent(passed, completed, 1) + ' passed - direct observed finished_at; not exhaustive' : 'no observed completed jobs; not exhaustive') : cardMetaUnavailable, observed: observationsAvailable ? windowEnd : null, provenance: lifecycleSource},
      {id: 'queue-lifecycle-outcomes', label: 'OBSERVED PASSED - ROLLING 2H', value: queueLifecycleDisplayCount(payload, totals.passed), meta: observationsAvailable ? integer(failed) + ' failed - ' + integer(canceled) + ' canceled - ' + integer(timedOut) + ' timed out - ' + integer(expired) + ' expired - ' + integer(broken) + ' broken - ' + integer(skipped) + ' skipped - ' + integer(retriedJobs) + ' retried' : cardMetaUnavailable, tone: !observationsAvailable ? 'is-neutral' : failed || canceled || timedOut || expired || broken ? 'is-danger' : softFailed || skipped || otherOutcomes ? 'is-warning' : 'is-success', observed: observationsAvailable ? windowEnd : null, provenance: lifecycleSource},
    ]));

    if (!observationsAvailable) {
      host.append(n('div', 'ops-empty', 'Per-queue lifecycle observations are unavailable until the first successful Buildkite API collection.'));
    } else if (rows.length) {
      const lifecycleColumns = [
        {label: 'Queue', sticky: true, width: '180px', render: function (row) { return n('span', 'ops-mono', row.name); }},
        {label: 'Incoming', numeric: true, width: '100px', render: function (row) { return integer(row.metrics.incoming); }},
        {label: 'Served', numeric: true, width: '90px', render: function (row) { return integer(row.metrics.served); }},
        {label: 'Completed', numeric: true, width: '110px', render: function (row) { return integer(row.metrics.completed); }},
        {label: 'Passed', numeric: true, width: '90px', render: function (row) { return integer(row.metrics.passed); }},
        {label: 'Soft fail', numeric: true, width: '100px', render: function (row) { return integer(row.metrics.soft_failed); }},
        {label: 'Failed', numeric: true, width: '90px', render: function (row) { return integer(row.metrics.failed); }},
        {label: 'Canceled', numeric: true, width: '100px', render: function (row) { return integer(row.metrics.canceled); }},
        {label: 'Timed out', numeric: true, width: '110px', render: function (row) { return integer(row.metrics.timed_out); }},
        {label: 'Expired', numeric: true, width: '90px', render: function (row) { return integer(row.metrics.expired); }},
        {label: 'Broken', numeric: true, width: '90px', render: function (row) { return integer(row.metrics.broken); }},
        {label: 'Skipped', numeric: true, width: '90px', render: function (row) { return integer(row.metrics.skipped); }},
        {label: 'Other / unknown', numeric: true, width: '130px', render: function (row) { return integer(row.metrics.other_outcomes); }},
        {label: 'Retry attempts', numeric: true, width: '120px', render: function (row) { return integer(row.metrics.retry_attempts_completed); }},
        {label: 'Retried jobs', numeric: true, width: '110px', render: function (row) { return integer(row.metrics.retried_jobs_completed); }},
        {label: 'Wait n', numeric: true, width: '90px', render: function (row) { return integer(queueLifecycleNestedMetric(row.metrics, 'queue_wait_seconds', 'count')); }},
        {label: 'Wait avg', numeric: true, width: '100px', render: function (row) { return queueLifecycleDuration(row.metrics, 'queue_wait_seconds', 'avg'); }},
        {label: 'Wait p50', numeric: true, width: '100px', render: function (row) { return queueLifecycleDuration(row.metrics, 'queue_wait_seconds', 'p50'); }},
        {label: 'Wait p95', numeric: true, width: '100px', render: function (row) { return queueLifecycleDuration(row.metrics, 'queue_wait_seconds', 'p95'); }},
        {label: 'Wait max', numeric: true, width: '100px', render: function (row) { return queueLifecycleDuration(row.metrics, 'queue_wait_seconds', 'max'); }},
        {label: 'Runtime n', numeric: true, width: '100px', render: function (row) { return integer(queueLifecycleNestedMetric(row.metrics, 'runtime_seconds', 'count')); }},
        {label: 'Runtime avg', numeric: true, width: '110px', render: function (row) { return queueLifecycleDuration(row.metrics, 'runtime_seconds', 'avg'); }},
        {label: 'Runtime p50', numeric: true, width: '110px', render: function (row) { return queueLifecycleDuration(row.metrics, 'runtime_seconds', 'p50'); }},
        {label: 'Runtime p95', numeric: true, width: '110px', render: function (row) { return queueLifecycleDuration(row.metrics, 'runtime_seconds', 'p95'); }},
        {label: 'Runtime max', numeric: true, width: '110px', render: function (row) { return queueLifecycleDuration(row.metrics, 'runtime_seconds', 'max'); }},
      ];
      host.append(compactTablePanel('Per-queue observed direct lifecycle events', integer(rows.length) + ' canonical queues in the ' + windowLabel, lifecycleColumns, rows, {
        id: 'queue-lifecycle-browser',
        limit: 20,
        browserSubtitle: 'Exact observed direct events, outcomes, queue waits, and runtimes for the twelve canonical AMD queues',
        searchPlaceholder: 'Filter lifecycle queue',
        searchText: function (row) { return row.name; },
        geometry: {name: 'queue-lifecycle', minWidth: '2750px'},
      }));
    } else {
      host.append(n('div', 'ops-empty', 'No canonical AMD queues are present in the lifecycle aggregate.'));
    }

    const hourly = observationsAvailable ? queueLifecycleHourlyRows(payload) : [];
    if (hourly.length) {
      const chartGrid = n('div', 'ops-grid ops-grid-2');
      const flowChart = chartPanel('Hourly observed direct lifecycle flow', 'Observed runnable_at, started_at, and finished_at events in each published UTC bucket', 'queue-lifecycle-flow');
      chartGrid.append(flowChart.root);
      const hasLatency = hourly.some(function (row) {
        return queueLifecycleNestedMetric(row, 'queue_wait_seconds', 'p95') !== null
          || queueLifecycleNestedMetric(row, 'runtime_seconds', 'p95') !== null;
      });
      let latencyChart = null;
      if (hasLatency) {
        latencyChart = chartPanel('Hourly lifecycle latency', 'Published p95 queue wait and runtime in minutes', 'queue-lifecycle-latency');
        chartGrid.append(latencyChart.root);
      }
      host.append(chartGrid);
      requestAnimationFrame(function () {
        drawChart('queue-lifecycle-flow', flowChart.canvas, {
          type: 'bar',
          data: {
            labels: hourly.map(function (row) { return shortDate(row.ts); }),
            datasets: [
              {label: 'Incoming', data: hourly.map(function (row) { return queueLifecycleNumber(row.incoming); }), backgroundColor: '#cf8dd9'},
              {label: 'Served', data: hourly.map(function (row) { return queueLifecycleNumber(row.served); }), backgroundColor: '#22b8ad'},
              {label: 'Completed', data: hourly.map(function (row) { return queueLifecycleNumber(row.completed); }), backgroundColor: '#66717d'},
            ],
          },
          options: {scales: {x: {grid: {display: false}}, y: {beginAtZero: true, title: {display: true, text: 'Jobs'}}}},
          evidenceTitle: 'Hourly queue lifecycle flow',
          evidenceAsset: lifecycleSource,
          evidence: hourly.map(function (row) { return {label: shortDate(row.ts), timestamp: row.ts, valueSummary: integer(row.incoming) + ' incoming - ' + integer(row.served) + ' served - ' + integer(row.completed) + ' completed', details: {end_exclusive: row.end_exclusive, partial: row.partial, passed: row.passed, failed: row.failed, soft_failed: row.soft_failed, other_outcomes: row.other_outcomes}, sources: [{label: 'Open selected queue lifecycle source', url: lifecycleSource}]}; }),
        });
        if (latencyChart) {
          drawChart('queue-lifecycle-latency', latencyChart.canvas, {
            type: 'line',
            data: {
              labels: hourly.map(function (row) { return shortDate(row.ts); }),
              datasets: [
                {label: 'Queue wait p95', data: hourly.map(function (row) { return queueLifecycleMinutes(row, 'queue_wait_seconds', 'p95'); }), borderColor: '#e3a63a', backgroundColor: '#e3a63a', pointRadius: 3, borderWidth: 2, spanGaps: false},
                {label: 'Runtime p95', data: hourly.map(function (row) { return queueLifecycleMinutes(row, 'runtime_seconds', 'p95'); }), borderColor: '#22b8ad', backgroundColor: '#22b8ad', pointRadius: 3, borderWidth: 2, spanGaps: false},
              ],
            },
            options: {scales: {x: {grid: {display: false}}, y: {beginAtZero: true, title: {display: true, text: 'Minutes'}}}},
            evidenceTitle: 'Hourly queue lifecycle latency',
            evidenceAsset: lifecycleSource,
            evidence: hourly.map(function (row) { return {label: shortDate(row.ts), timestamp: row.ts, valueSummary: 'wait p95 ' + queueLifecycleDuration(row, 'queue_wait_seconds', 'p95') + ' - runtime p95 ' + queueLifecycleDuration(row, 'runtime_seconds', 'p95'), details: {end_exclusive: row.end_exclusive, partial: row.partial, queue_wait_count: queueLifecycleNestedMetric(row, 'queue_wait_seconds', 'count'), runtime_count: queueLifecycleNestedMetric(row, 'runtime_seconds', 'count')}, sources: [{label: 'Open selected queue lifecycle source', url: lifecycleSource}]}; }),
          });
        }
      });
    }

    const provenance = n('div', 'ops-evidence-note is-info');
    add(provenance, [
      n('strong', '', 'Lifecycle provenance. '),
      n('span', '', 'Schema v' + value(payload.schema_version) + ' - generated ' + shortDate(payload.generated_at)
        + ' - provider ' + value((payload.provenance || {}).provider) + ' - coverage ' + coverage.summary
        + '. An API-collected aggregate wins over a structural seed; otherwise the freshest live or Pages copy is used, with live queue-lifecycle-data winning equal-timestamp ties.'),
      externalLink('Open selected lifecycle data', lifecycleSource, 'ops-button'),
      externalLink('Open Pages lifecycle fallback', SOURCE_ASSETS.queueLifecycleFallback, 'ops-button'),
    ]);
    host.append(provenance);
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
  const QUEUE_DNS_PIPELINES = new Set(['amd-ci', 'ci']);
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
    if (!payload.scope || typeof payload.scope !== 'object' || Array.isArray(payload.scope)) return false;
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
      if (!structurallyValid || !outcomesMarked) return Boolean(structurallyValid);
      const totals = queueDnsOutcomeCounts(windowBlock.totals);
      if (!totals.available || !windowBlock.rows.every(function (row) {
        return queueDnsOutcomeCounts(row).available;
      })) return false;
      return [
        ['passed_jobs', totals.passed],
        ['soft_failed_jobs', totals.softFailed],
        ['hard_failed_jobs', totals.hardFailed],
      ].every(function (entry) {
        return entry[1] === windowBlock.rows.reduce(function (sum, row) {
          return sum + queueDnsCount(row[entry[0]]);
        }, 0);
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
    const complete = explicitlyComplete && !explicitlyIncomplete;
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

  async function renderQueue(host, ops) {
    const queueBlock = ops.queue || {};
    const snapshot = queueBlock.snapshot || {};
    let lifecyclePayload = null;
    let lifecycleError = null;
    if (state.queueView === 'lifecycle') {
      try {
        lifecyclePayload = await loadQueueLifecycle();
      } catch (error) {
        lifecycleError = error;
      }
    }
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
    function highestNative(metric) {
      const vals = allScopeEntries.map(function (entry) { return {queue: entry[0], value: officialWaitValue(entry[1], metric), row: entry[1]}; }).filter(function (result) { return result.value !== null; });
      return vals.sort(function (a, b) { return Number(b.value) - Number(a.value); })[0] || {};
    }
    function highestSample(metric) {
      const vals = allScopeEntries.map(function (entry) { return {queue: entry[0], value: sampleWaitValue(entry[1], metric), row: entry[1]}; }).filter(function (result) { return result.value !== null; });
      return vals.sort(function (a, b) { return Number(b.value) - Number(a.value); })[0] || {};
    }
    const p95 = highestNative('p95'), sampledP95 = highestSample('p95');
    const p95Coverage = allScopeEntries.filter(function (entry) { return officialWaitValue(entry[1] || {}, 'p95') !== null; }).length;
    const sampledP95Coverage = allScopeEntries.filter(function (entry) { return sampleWaitValue(entry[1] || {}, 'p95') !== null; }).length;
    const queueObservedAt = lifecyclePayload
      ? lifecyclePayload.generated_at || ((lifecyclePayload.window || {}).end_exclusive) || ((lifecyclePayload.window || {}).end)
      : snapshot.ts;
    add(host, pageHeader('Queue Monitor', 'Current queue counts, direct lifecycle outcomes, retained history, and active jobs.', queueObservedAt));
    const controls = n('div', 'ops-toolbar ops-queue-toolbar');
    controls.append(segmented([{id: 'current', label: 'Current'}, {id: 'lifecycle', label: 'Lifecycle'}, {id: 'history', label: 'History'}, {id: 'jobs', label: 'Jobs'}], state.queueView, function (id) { setRouteState('ci-queue', 'queueView', id, 'queue_view'); }, 'Queue monitor mode'));
    if (state.queueView === 'lifecycle') controls.append(n('span', 'ops-badge is-info', 'Canonical AMD lifecycle scope'));
    else controls.append(segmented([{id: 'canonical', label: 'Canonical AMD'}, {id: 'amd', label: 'All AMD'}, {id: 'all', label: 'All queues'}], state.queueScope, function (id) { setRouteState('ci-queue', 'queueScope', id, 'queue_scope'); }, 'Queue hardware scope'));
    if (state.queueView === 'history') controls.append(segmented([{id: '24h', label: '24h'}, {id: '7d', label: '7d'}, {id: '30d', label: '30d'}], state.queueRange, function (id) { setRouteState('ci-queue', 'queueRange', id, 'queue_range'); }, 'Queue history range'));
    if (state.queueView === 'current') {
      const idleLabel = n('label', 'ops-toggle');
      const idle = n('input'); idle.type = 'checkbox'; idle.checked = state.queueIncludeIdle;
      idle.addEventListener('change', function () { state.queueIncludeIdle = idle.checked; render('ci-queue', true); });
      add(idleLabel, [idle, n('span', '', 'Include idle')]);
      controls.append(idleLabel);
    }
    host.append(controls);
    if (state.queueView === 'lifecycle') {
      if (lifecycleError) {
        const unavailable = n('div', 'ops-error');
        add(unavailable, [
          n('strong', '', 'Lifecycle data is unavailable. '),
          n('span', '', (lifecycleError && lifecycleError.message) || String(lifecycleError)),
          externalLink('Open live lifecycle asset', SOURCE_ASSETS.queueLifecycle, 'ops-button'),
          externalLink('Open Pages lifecycle fallback', SOURCE_ASSETS.queueLifecycleFallback, 'ops-button'),
        ]);
        host.append(unavailable);
      } else {
        renderQueueLifecycle(host, lifecyclePayload);
      }
      return;
    }
    host.append(statusStrip([
      {id: 'queue-running', label: 'RUNNING NOW', value: integer(sums.running), meta: allScopeEntries.length + ' queues in scope', onOpen: function () { setRouteState('ci-queue', 'queueView', 'jobs', 'queue_view'); }},
      {id: 'queue-waiting', label: 'WAITING NOW', value: integer(sums.waiting), meta: 'count source: ' + countProvenance, tone: sums.waiting ? 'is-warning' : 'is-success', provenance: countProvenance, onOpen: function () { setRouteState('ci-queue', 'queueView', 'jobs', 'queue_view'); }},
      {id: 'queue-p95-leader', label: 'BUILDKITE P95 LEADER', value: p95.queue ? duration(p95.value) : '-', meta: p95.queue ? p95.queue + ' - ' + value(waitSourceDetail(p95.row, 'p95')) : 'No p95 source - ' + integer(p95Coverage) + ' measured queues', tone: p95.queue ? 'is-warning' : 'is-neutral', onOpen: function () { p95.queue ? openQueueDetail(p95.queue, p95.row, []) : openMetricDetail({label: 'Current p95 queue leader', value: '-', meta: 'No queue in scope reported a current p95. Missing values are not zero.'}); }},
      {id: 'queue-sampled-p95-leader', label: 'RECONSTRUCTED P95', value: sampledP95.queue ? duration(sampledP95.value) : '-', meta: sampledP95.queue ? sampledP95.queue + ' - n=' + integer(waitSampleCount(sampledP95.row)) + ' scheduled jobs' : 'No reconstructed p95 - ' + integer(sampledP95Coverage) + ' measured queues', tone: sampledP95.queue ? 'is-danger' : 'is-neutral', onOpen: function () { sampledP95.queue ? openQueueDetail(sampledP95.queue, sampledP95.row, []) : openMetricDetail({label: 'Current reconstructed p95 queue leader', value: '-', meta: 'The reconstructed series uses scheduled jobs fetched separately from Buildkite queue-native metrics.'}); }},
    ]));

    const jobs = queueBlock.queue_jobs || {};
    const activeJobs = (jobs.pending || []).concat(jobs.running || []).filter(function (job) {
      return queueMatchesScope(job.queue);
    });
    const detailsStatus = jobs.details_status || snapshot.details_status || 'legacy_current';
    const detailsObservedAt = jobs.details_observed_at || jobs.ts || snapshot.details_observed_at;
    if (String(detailsStatus).indexOf('retained_') === 0) {
      const retainedReason = detailsStatus === 'retained_due_to_page_cap'
        ? 'the active-job connection exceeded its twelve-page safety cap'
        : detailsStatus === 'retained_due_to_error'
          ? 'the bounded detail refresh did not complete'
          : 'the hourly detail refresh was not due';
      host.append(n(
        'div',
        'ops-evidence-note is-warning',
        'Queue counts and Buildkite-native waits are current as of ' + shortDate(snapshot.metrics_observed_at || snapshot.ts)
          + '. Job rows are the last complete overlay from ' + shortDate(detailsObservedAt)
          + ' because ' + retainedReason + '; they are not relabeled as current.'
      ));
    }

    if (state.queueView === 'current') {
      const pressureRows = queuePressureRows(snapshot, Array.isArray(queueBlock.history) ? queueBlock.history : [], queueBlock.pressure_baseline || {});
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
        {label: 'p50 Buildkite', numeric: true, render: function (item) { return linkButton(duration(officialWaitValue(item[1], 'p50')), function () { openQueueDetail(item[0], item[1], activeJobs); }); }},
        {label: 'p95 Buildkite', numeric: true, render: function (item) { return linkButton(duration(officialWaitValue(item[1], 'p95')), function () { openQueueDetail(item[0], item[1], activeJobs); }); }},
        {label: 'Latest passed / failed', numeric: true, render: function (item) { const row = item[1]; const passed = row.jobs_passed === null || row.jobs_passed === undefined ? '-' : integer(row.jobs_passed); const failed = row.jobs_failed === null || row.jobs_failed === undefined ? '-' : integer(row.jobs_failed); return linkButton(passed + ' / ' + failed, function () { openQueueDetail(item[0], row, activeJobs); }); }},
        {label: 'p50 / p95 reconstructed', numeric: true, render: function (item) { const p50 = sampleWaitValue(item[1], 'p50'), p95 = sampleWaitValue(item[1], 'p95'); return linkButton((p50 === null ? '-' : duration(p50)) + ' / ' + (p95 === null ? '-' : duration(p95)), function () { openQueueDetail(item[0], item[1], activeJobs); }); }},
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

    let history = await loadQueueHistory(queueBlock);
    const rangeEndMs = Date.now();
    const rangeHours = state.queueRange === '30d' ? 720 : state.queueRange === '7d' ? 168 : 24;
    const rangeStartMs = rangeEndMs - rangeHours * 3600000;
    history = history.filter(function (snap) {
      const time = queueTimestamp(snap.ts);
      return time >= rangeStartMs && time <= rangeEndMs + 5 * 60 * 1000;
    });
    const queueNames = Array.from(new Set(history.flatMap(function (snap) {
      return Object.keys(snap.queues || {}).filter(function (name) {
        return queueMatchesScope(name);
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
    [['fleet', queueScopeLabel(state.queueScope, true)]].concat(queueNames.map(function (name) { return [name, name]; })).forEach(function (pair) {
      const option = n('option', '', pair[1]);
      option.value = pair[0];
      option.selected = pair[0] === state.queueHistoryQueue;
      queueSelect.append(option);
    });
    queueSelect.addEventListener('change', function () { setRouteState('ci-queue', 'queueHistoryQueue', queueSelect.value, 'queue_history_queue'); });
    queueField.append(queueSelect);
    const historyToolbar = n('div', 'ops-toolbar');
    historyToolbar.append(queueField);
    historyToolbar.append(n('span', 'ops-evidence-method', 'Times shown in ' + (Intl.DateTimeFormat().resolvedOptions().timeZone || 'browser local time')));
    host.append(historyToolbar);
    const selectedHistory = state.queueHistoryQueue === 'fleet' ? history : history.filter(function (snap) {
      return Object.prototype.hasOwnProperty.call(snap.queues || {}, state.queueHistoryQueue);
    });
    const points = selectedHistory.map(function (snap) {
      let waiting = 0, running = 0, queues = 0;
      for (const [name, row] of Object.entries(snap.queues || {})) {
        if (!queueMatchesScope(name)) continue;
        if (state.queueHistoryQueue !== 'fleet' && name !== state.queueHistoryQueue) continue;
        if (row.history_observation_only) continue;
        waiting += Number(row.waiting || 0);
        running += Number(row.running || 0);
        queues += 1;
      }
      return {ts: snap.ts, waiting: waiting, running: running, snapshot: snap, queues: queues};
    });
    const expectedCoverageStartMs = Math.max(rangeStartMs, rangeEndMs - 48 * 60 * 60 * 1000);
    const highResolutionPoints = points.filter(function (point) { return queueTimestamp(point.ts) >= expectedCoverageStartMs; });
    const coverageProblems = [];
    if (!highResolutionPoints.length) {
      coverageProblems.push('no retained snapshots in the recent high-resolution window');
    } else {
      const firstMs = queueTimestamp(highResolutionPoints[0].ts);
      const lastMs = queueTimestamp(highResolutionPoints[highResolutionPoints.length - 1].ts);
      if (firstMs - expectedCoverageStartMs > 30 * 60 * 1000) coverageProblems.push('coverage begins ' + duration((firstMs - expectedCoverageStartMs) / 60000) + ' late');
      if (rangeEndMs - lastMs > 30 * 60 * 1000) coverageProblems.push('latest snapshot is ' + duration((rangeEndMs - lastMs) / 60000) + ' old');
      const largestRecentGapMs = highResolutionPoints.slice(1).reduce(function (largest, point, index) {
        return Math.max(largest, queueTimestamp(point.ts) - queueTimestamp(highResolutionPoints[index].ts));
      }, 0);
      if (largestRecentGapMs > 30 * 60 * 1000) coverageProblems.push('largest interior gap is ' + duration(largestRecentGapMs / 60000));
    }
    if (coverageProblems.length) {
      host.append(n('div', 'ops-evidence-note is-warning', 'Collection coverage warning: ' + coverageProblems.join('; ') + '. Chart lines are broken across missing high-resolution intervals; hourly archive spacing older than 48 hours is intentional.'));
    }
    const summary = queueBlock.history_summary || {};
    const selectedHistoryStart = selectedHistory.length ? selectedHistory[0].ts : summary.first_observed_at;
    const historyLabel = state.queueHistoryQueue === 'fleet' ? queueScopeLabel(state.queueScope, false) : state.queueHistoryQueue;
    if (state.queueHistoryQueue === 'fleet') {
      const aggregationNote = n('div', 'ops-evidence-note is-info');
      add(aggregationNote, [n('strong', '', 'Combined scope has two different reducers. '), n('span', '', 'Running and waiting below are summed across queues. Wait charts show the worst named queue at each snapshot; they are not fleet percentiles and the leading queue can change.')]);
      host.append(aggregationNote);
    }
    const activityTitle = state.queueHistoryQueue === 'fleet' ? historyLabel + ': total active jobs' : historyLabel + ': active jobs';
    const activityMeta = integer(points.length) + ' snapshots in ' + state.queueRange + ' - running and waiting ' + (state.queueHistoryQueue === 'fleet' ? 'summed across observed queues' : 'for this queue') + (selectedHistoryStart ? ' - begins ' + shortDate(selectedHistoryStart) : '');
    const cp = chartPanel(activityTitle, activityMeta, 'queue-history');
    host.append(cp.root);
    const activityChartPoints = queueChartPointsWithBreaks(points, rangeEndMs);
    drawChart('queue-history', cp.canvas, {type: 'line', data: {
      labels: activityChartPoints.map(function (p) { return shortDate(p.ts); }),
      datasets: [
        {label: 'Running', data: activityChartPoints.map(function (p) { return p.isGap ? null : p.running; }), borderColor: '#22b8ad', backgroundColor: '#22b8ad', pointRadius: 0, borderWidth: 2, spanGaps: false},
        {label: 'Waiting', data: activityChartPoints.map(function (p) { return p.isGap ? null : p.waiting; }), borderColor: '#e3a63a', backgroundColor: '#e3a63a', pointRadius: 0, borderWidth: 2, spanGaps: false},
      ],
    }, evidenceTitle: historyLabel + ' queue activity history', evidenceAsset: SOURCE_ASSETS.queueHistory, evidence: points.map(function (point) { return {label: shortDate(point.ts), timestamp: point.ts, valueSummary: integer(point.running) + ' running - ' + integer(point.waiting) + ' waiting', details: {running: point.running, waiting: point.waiting, queues: point.queues, selected_queue: state.queueHistoryQueue}, sources: [{label: 'Open published queue history', url: SOURCE_ASSETS.queueHistory}], onOpen: function () { openQueueSnapshotDetail(point.snapshot, Object.assign({}, point, {selectedQueue: state.queueHistoryQueue})); }}; })});

    const waitPoints = selectedHistory.map(function (snap) { return queueWaitHistoryPoint(snap, state.queueHistoryQueue); });
    const waitEvidenceCount = waitPoints.filter(function (point) { return point.p50 !== null || point.p95 !== null || point.sampleP50 !== null || point.sampleP95 !== null || point.p99 !== null; }).length;
    if (waitEvidenceCount) {
      const waitTitle = state.queueHistoryQueue === 'fleet' ? 'Worst individual queue wait at each snapshot' : state.queueHistoryQueue + ': reported wait history';
      const waitSubtitle = state.queueHistoryQueue === 'fleet'
        ? 'Each point names the queue with the largest reported value; ties are preserved and no fleet percentile is calculated'
        : 'Solid p50/p95 are Buildkite-native when available; dashed p50/p95 are separately reconstructed from scheduled jobs; p99 is sample-only';
      const waitChart = chartPanel(waitTitle, waitSubtitle + ' - ' + integer(waitEvidenceCount) + ' measured snapshots', 'queue-wait-history');
      host.append(waitChart.root);
      const waitChartPoints = queueChartPointsWithBreaks(waitPoints, rangeEndMs);
      drawChart('queue-wait-history', waitChart.canvas, {
        type: 'line',
        data: {
          labels: waitChartPoints.map(function (point) { return shortDate(point.ts); }),
          datasets: [
            {label: 'p50 primary', metric: 'p50', pointKey: 'p50', data: waitChartPoints.map(function (point) { return point.isGap ? null : point.p50; }), borderColor: '#22b8ad', backgroundColor: '#22b8ad', pointRadius: 3, borderWidth: 2, spanGaps: false},
            {label: 'p95 primary', metric: 'p95', pointKey: 'p95', data: waitChartPoints.map(function (point) { return point.isGap ? null : point.p95; }), borderColor: '#e3a63a', backgroundColor: '#e3a63a', pointRadius: 3, borderWidth: 2, spanGaps: false},
            {label: 'p50 reconstructed', metric: 'p50', pointKey: 'sampleP50', data: waitChartPoints.map(function (point) { return point.isGap ? null : point.sampleP50; }), borderColor: '#78d9d1', backgroundColor: '#78d9d1', borderDash: [6, 4], pointRadius: 2, borderWidth: 1.5, spanGaps: false},
            {label: 'p95 reconstructed', metric: 'p95', pointKey: 'sampleP95', data: waitChartPoints.map(function (point) { return point.isGap ? null : point.sampleP95; }), borderColor: '#f4c66f', backgroundColor: '#f4c66f', borderDash: [6, 4], pointRadius: 2, borderWidth: 1.5, spanGaps: false},
            {label: 'p99 scheduled sample', metric: 'p99', pointKey: 'p99', data: waitChartPoints.map(function (point) { return point.isGap ? null : point.p99; }), borderColor: '#cf8dd9', backgroundColor: '#cf8dd9', pointRadius: 3, borderWidth: 1.5, spanGaps: false},
          ],
        },
        options: {
          scales: {x: {grid: {display: false}, ticks: {maxTicksLimit: 8}}, y: {beginAtZero: true, title: {display: true, text: 'Wait minutes'}}},
          plugins: {tooltip: {callbacks: {
            label: function (context) {
              const key = context.dataset.pointKey || context.dataset.metric;
              const point = waitChartPoints[context.dataIndex] || {};
              const queues = point[key + 'Queues'] || [point[key + 'Queue']].filter(Boolean);
              return context.dataset.label + ': ' + duration(context.parsed.y) + (queues.length ? ' - ' + queueLeaderSummary(queues) : '');
            },
            afterLabel: function (context) {
              const key = context.dataset.pointKey || context.dataset.metric;
              const point = waitChartPoints[context.dataIndex] || {};
              const source = point[key + 'SourceDetail'] || point[key + 'Source'];
              const count = point[key + 'SampleCount'];
              const expected = point[key + 'SampleExpected'];
              const complete = point[key + 'SampleComplete'];
              const observedAt = point[key + 'ObservedAt'];
              const coverage = source && source.indexOf('sample_wait') !== -1 && count !== null && count !== undefined
                ? 'Scheduled sample: ' + integer(count) + (expected !== null && expected !== undefined ? ' / ' + integer(expected) : '') + (complete === true ? ' - reconciled' : complete === false ? ' - partial' : '')
                : null;
              return [source ? 'Source: ' + source : null, observedAt ? 'Hourly peak observed: ' + shortDate(observedAt) : null, coverage].filter(Boolean);
            },
          }}},
        },
        evidenceTitle: waitTitle,
        evidenceAsset: SOURCE_ASSETS.queueHistory,
        evidence: waitPoints.map(function (point) { return {label: shortDate(point.ts), timestamp: point.ts, valueSummary: 'primary p50 ' + duration(point.p50) + ' (' + queueLeaderSummary(point.p50Queues) + ') - primary p95 ' + duration(point.p95) + ' (' + queueLeaderSummary(point.p95Queues) + ') - reconstructed p95 ' + duration(point.sampleP95) + ' (' + queueLeaderSummary(point.sampleP95Queues) + ')', details: {p50_primary: duration(point.p50), p50_queues: (point.p50Queues || []).join(', '), p50_source: point.p50SourceDetail || point.p50Source, p50_peak_observed_at: point.p50ObservedAt, p95_primary: duration(point.p95), p95_queues: (point.p95Queues || []).join(', '), p95_source: point.p95SourceDetail || point.p95Source, p95_peak_observed_at: point.p95ObservedAt, p50_reconstructed: duration(point.sampleP50), p50_reconstructed_queues: (point.sampleP50Queues || []).join(', '), p95_reconstructed: duration(point.sampleP95), p95_reconstructed_queues: (point.sampleP95Queues || []).join(', '), p95_reconstructed_sample_count: point.sampleP95SampleCount, p99_sampled: duration(point.p99), p99_queues: (point.p99Queues || []).join(', '), p99_source: point.p99SourceDetail || point.p99Source, p99_peak_observed_at: point.p99ObservedAt, p99_sample_count: point.p99SampleCount}, sources: [{label: 'Open published queue history', url: SOURCE_ASSETS.queueHistory}], onOpen: function () { const activityPoint = points.find(function (row) { return row.ts === point.ts; }) || {}; openQueueSnapshotDetail(point.snapshot, {running: activityPoint.running, waiting: activityPoint.waiting, queues: activityPoint.queues, selectedQueue: state.queueHistoryQueue}); }}; }),
      });

      function peak(metric) {
        return waitPoints.filter(function (point) { return point[metric] !== null && point[metric] !== undefined; }).sort(function (a, b) { return Number(b[metric]) - Number(a[metric]); })[0] || null;
      }
      const leaderGrid = n('div', 'ops-wait-leader-grid');
      [['p50', 'PEAK PRIMARY P50', ''], ['p95', 'PEAK PRIMARY P95', 'is-warning'], ['sampleP95', 'PEAK RECONSTRUCTED P95', 'is-warning'], ['p99', 'PEAK SAMPLED P99', 'is-danger']].forEach(function (spec) {
        const metric = spec[0];
        const point = peak(metric);
        const queueNamesForPoint = point ? point[metric + 'Queues'] || [point[metric + 'Queue']].filter(Boolean) : [];
        const card = n('button', 'ops-wait-leader ' + spec[2]);
        card.type = 'button';
        add(card, [
          n('span', 'ops-stat-label', spec[1]),
          n('strong', 'ops-wait-leader-value', point ? duration(point[metric]) : '-'),
          n('span', 'ops-wait-leader-queue', point ? queueLeaderSummary(queueNamesForPoint) : 'No measurement'),
          n('span', 'ops-wait-leader-meta', point ? shortDate(point[metric + 'ObservedAt'] || point.ts) + ' - ' + value(point[metric + 'SourceDetail'] || point[metric + 'Source']) + (metric === 'p99' && point.p99SampleCount !== null && point.p99SampleCount !== undefined ? ' - n=' + integer(point.p99SampleCount) : '') : 'Missing values are not zero'),
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
    if (points.length === 0) host.append(n('div', 'ops-evidence-note is-info', 'Historical collection has no snapshots in this range.'));
    else if (points.length === 1) host.append(n('div', 'ops-evidence-note is-info', 'Historical collection has only one snapshot in this range. The dashboard will not infer a trend until another source-backed point exists.'));
    const waitsByTimestamp = new Map(waitPoints.map(function (point) { return [point.ts, point]; }));
    const historyColumns = [
      {label: 'Snapshot', sticky: true, render: function (point) { return linkButton(shortDate(point.ts), function () { openQueueSnapshotDetail(point.snapshot, Object.assign({}, point, {selectedQueue: state.queueHistoryQueue})); }); }},
      {label: 'Running', numeric: true, render: function (point) { return linkButton(integer(point.running), function () { openQueueSnapshotDetail(point.snapshot, Object.assign({}, point, {selectedQueue: state.queueHistoryQueue})); }); }},
      {label: 'Waiting', numeric: true, render: function (point) { return linkButton(integer(point.waiting), function () { openQueueSnapshotDetail(point.snapshot, Object.assign({}, point, {selectedQueue: state.queueHistoryQueue})); }); }},
      {label: 'Queues', numeric: true, render: function (point) { return linkButton(integer(point.queues), function () { openQueueSnapshotDetail(point.snapshot, Object.assign({}, point, {selectedQueue: state.queueHistoryQueue})); }); }},
      {label: 'Worst primary p95 queue', render: function (point) { const wait = waitsByTimestamp.get(point.ts) || {}; return linkButton(wait.p95Queue ? queueLeaderSummary(wait.p95Queues) + ' - ' + duration(wait.p95) : '-', function () { openQueueSnapshotDetail(point.snapshot, Object.assign({}, point, {selectedQueue: state.queueHistoryQueue})); }); }},
      {label: 'Worst reconstructed p95', render: function (point) { const wait = waitsByTimestamp.get(point.ts) || {}; return linkButton(wait.sampleP95Queue ? queueLeaderSummary(wait.sampleP95Queues) + ' - ' + duration(wait.sampleP95) : '-', function () { openQueueSnapshotDetail(point.snapshot, Object.assign({}, point, {selectedQueue: state.queueHistoryQueue})); }); }},
      {label: 'Worst sampled p99 queue', render: function (point) { const wait = waitsByTimestamp.get(point.ts) || {}; return linkButton(wait.p99Queue ? queueLeaderSummary(wait.p99Queues) + ' - ' + duration(wait.p99) : '-', function () { openQueueSnapshotDetail(point.snapshot, Object.assign({}, point, {selectedQueue: state.queueHistoryQueue})); }); }},
    ];
    host.append(compactTablePanel('Queue history snapshots', integer(points.length) + ' snapshots; worst-wait columns always name the queue', historyColumns, points.slice().reverse(), {
      id: 'queue-history-browser',
      limit: 14,
      browserSubtitle: historyLabel + ' in the selected ' + state.queueRange + ' range',
      searchPlaceholder: 'Filter by timestamp or leading queue',
      searchText: function (point) { const wait = waitsByTimestamp.get(point.ts) || {}; return [point.ts, wait.p95Queue, wait.sampleP95Queue, wait.p99Queue].join(' '); },
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

  function trajectoryFrequencySignal(raw) {
    const change = raw === null || raw === undefined || raw === '' ? NaN : Number(raw);
    if (!Number.isFinite(change)) return {text: 'baseline limited', tone: 'is-info'};
    return {
      text: (change >= 0 ? '+' : '') + change.toFixed(0) + '%',
      tone: change >= 100 ? 'is-warning' : 'is-info',
    };
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

  const CAPACITY_MAX_INTERACTIVE_BURST_JOBS = 50000;

  function capacityInteger(raw, fallback, minimum, maximum) {
    const parsed = Number(raw);
    if (!Number.isFinite(parsed)) return fallback;
    return Math.max(minimum, Math.min(maximum, Math.round(parsed)));
  }

  function capacityLargestRemainder(weights, total) {
    const target = Math.max(0, Math.round(Number(total) || 0));
    const safe = (weights || []).map(function (weight) {
      return Math.max(0, Number(weight) || 0);
    });
    const weightTotal = safe.reduce(function (sum, weight) { return sum + weight; }, 0);
    if (!safe.length) return [];
    if (!target) return safe.map(function () { return 0; });
    if (!weightTotal) {
      return safe.map(function (_, index) { return index ? 0 : target; });
    }
    const quotas = safe.map(function (weight) { return weight / weightTotal * target; });
    const allocated = quotas.map(function (quota) { return Math.floor(quota); });
    let remaining = target - allocated.reduce(function (sum, count) { return sum + count; }, 0);
    const order = quotas.map(function (quota, index) {
      return {index: index, remainder: quota - Math.floor(quota)};
    }).sort(function (left, right) {
      return right.remainder - left.remainder || left.index - right.index;
    });
    for (let index = 0; index < remaining; index += 1) {
      allocated[order[index % order.length].index] += 1;
    }
    return allocated;
  }

  function capacityInterpolatedValue(current, target, selected, currentTotal, targetTotal) {
    current = Math.max(0, Number(current) || 0);
    target = Math.max(0, Number(target) || 0);
    selected = Math.max(0, Number(selected) || 0);
    currentTotal = Math.max(0, Number(currentTotal) || 0);
    targetTotal = Math.max(0, Number(targetTotal) || 0);
    if (selected <= currentTotal) return currentTotal ? current * selected / currentTotal : 0;
    if (selected <= targetTotal && targetTotal > currentTotal) {
      return current + (target - current) * (selected - currentTotal) / (targetTotal - currentTotal);
    }
    return targetTotal ? target * selected / targetTotal : 0;
  }

  function capacityPairedAllocation(groupWeights, jobWeights, rawGroups, rawJobs) {
    const queueCount = Math.max((groupWeights || []).length, (jobWeights || []).length);
    const groups = Math.max(0, Math.round(Number(rawGroups) || 0));
    const jobs = Math.max(0, Math.round(Number(rawJobs) || 0));
    const safeGroups = Array.from({length: queueCount}, function (_, index) {
      return Math.max(0, Number((groupWeights || [])[index]) || 0);
    });
    const safeJobs = Array.from({length: queueCount}, function (_, index) {
      return Math.max(0, Number((jobWeights || [])[index]) || 0);
    });
    if (!queueCount) return {groups: [], jobs: [], valid: groups === 0 && jobs === 0};
    const exactGroups = safeGroups.every(function (count) {
      return Number.isInteger(count);
    }) && safeGroups.reduce(function (sum, count) {
      return sum + count;
    }, 0) === groups;
    const exactJobs = safeJobs.every(function (count) {
      return Number.isInteger(count);
    }) && safeJobs.reduce(function (sum, count) {
      return sum + count;
    }, 0) === jobs;
    const exactPaired = safeGroups.every(function (count, index) {
      return (count > 0) === (safeJobs[index] > 0);
    });
    if (exactGroups && exactJobs && exactPaired) {
      return {
        groups: safeGroups.slice(),
        jobs: safeJobs.slice(),
        valid: true,
        exact: true,
      };
    }
    if (!groups && !jobs) {
      return {
        groups: safeGroups.map(function () { return 0; }),
        jobs: safeJobs.map(function () { return 0; }),
        valid: true,
        exact: false,
      };
    }
    const pairedWeights = safeGroups.map(function (weight, index) {
      if (!safeJobs[index]) return 0;
      return weight || safeJobs[index];
    });
    let allocatedGroups = capacityLargestRemainder(pairedWeights, groups);
    let active = allocatedGroups.map(function (count, index) {
      return count > 0 ? index : null;
    }).filter(function (index) {
      return index !== null;
    });
    if (jobs > 0 && active.length > jobs) {
      const keep = new Set(pairedWeights.map(function (weight, index) {
        return {index: index, weight: weight};
      }).filter(function (row) {
        return row.weight > 0;
      }).sort(function (left, right) {
        return right.weight - left.weight || left.index - right.index;
      }).slice(0, jobs).map(function (row) {
        return row.index;
      }));
      allocatedGroups = capacityLargestRemainder(pairedWeights.map(function (weight, index) {
        return keep.has(index) ? weight : 0;
      }), groups);
      active = allocatedGroups.map(function (count, index) {
        return count > 0 ? index : null;
      }).filter(function (index) {
        return index !== null;
      });
    }
    const allocatedJobs = safeJobs.map(function () { return 0; });
    if (jobs && active.length && jobs >= active.length) {
      active.forEach(function (index) { allocatedJobs[index] = 1; });
      const extras = capacityLargestRemainder(active.map(function (index) {
        return safeJobs[index];
      }), jobs - active.length);
      active.forEach(function (index, activeIndex) {
        allocatedJobs[index] += extras[activeIndex];
      });
    }
    const groupTotal = allocatedGroups.reduce(function (sum, count) { return sum + count; }, 0);
    const jobTotal = allocatedJobs.reduce(function (sum, count) { return sum + count; }, 0);
    const paired = allocatedGroups.every(function (count, index) {
      return (count > 0) === (allocatedJobs[index] > 0);
    });
    return {
      groups: allocatedGroups,
      jobs: allocatedJobs,
      valid: groupTotal === groups && jobTotal === jobs && paired,
      exact: false,
    };
  }

  function capacityPlacementStrategy(profile, requestedId) {
    const placement = (profile && profile.placement_profiles) || {};
    const strategies = Array.isArray(placement.strategies) ? placement.strategies : [];
    if (!strategies.length) return null;
    const defaultId = placement.default_strategy_id || strategies[0].id;
    return strategies.find(function (strategy) {
      return strategy.id === requestedId;
    }) || strategies.find(function (strategy) {
      return strategy.id === defaultId;
    }) || strategies[0];
  }

  function capacityProfileForPlacement(profile, requestedId) {
    profile = profile || {};
    const strategy = capacityPlacementStrategy(profile, requestedId);
    if (!strategy) return profile;
    const strategyRows = strategy.queues || strategy.queue_targets || [];
    const byQueue = new Map(strategyRows.map(function (row) {
      return [row.id, row];
    }));
    const queues = (profile.queues || []).map(function (queue) {
      const selected = byQueue.get(queue.id) || {};
      const current = (queue.demand || {}).current || {};
      const priorTarget = (queue.demand || {}).target || {};
      const targetGroups = Math.max(0, Number(selected.groups) || 0);
      const targetJobs = Math.max(0, Number(selected.jobs) || 0);
      const gpusPerJob = Math.max(1, Number(queue.gpus_per_job) || 1);
      const publishedStrategyService = Number(selected.service_minutes);
      const observedFallbackService = Number((queue.workload || {}).observed_service_minutes);
      const serviceMinutes = Number.isFinite(publishedStrategyService) && publishedStrategyService > 0
        ? publishedStrategyService
        : Number.isFinite(observedFallbackService) && observedFallbackService > 0
          ? observedFallbackService
          : null;
      const serviceSource = Number.isFinite(publishedStrategyService) && publishedStrategyService > 0
        ? (selected.service_minutes_source || 'placement_strategy_target_command_job_median_average')
        : Number.isFinite(observedFallbackService) && observedFallbackService > 0
          ? 'completed_agent_minutes_per_finished_job_proxy_fallback'
          : 'unavailable';
      const targetAgentMinutes = Number.isFinite(serviceMinutes) && serviceMinutes > 0
        ? targetJobs * serviceMinutes
        : (Number(priorTarget.jobs) === targetJobs ? priorTarget.agent_minutes : null);
      const currentAgentMinutes = Number(current.agent_minutes);
      return Object.assign({}, queue, {
        workload: Object.assign({}, queue.workload || {}, {
          service_minutes: serviceMinutes,
          service_minutes_source: serviceSource,
          service_minutes_is_proxy: serviceSource === 'completed_agent_minutes_per_finished_job_proxy_fallback',
          placement_strategy_id: strategy.id,
        }),
        demand: Object.assign({}, queue.demand || {}, {
          target: {
            groups: targetGroups,
            jobs: targetJobs,
            gpu_slots: Number.isFinite(Number(selected.gpu_slots))
              ? Number(selected.gpu_slots)
              : targetJobs * gpusPerJob,
            agent_minutes: targetAgentMinutes,
          },
          delta: {
            groups: targetGroups - Number(current.groups || 0),
            jobs: targetJobs - Number(current.jobs || 0),
            gpu_slots: (
              targetJobs - Number(current.jobs || 0)
            ) * gpusPerJob,
            agent_minutes: targetAgentMinutes !== null && Number.isFinite(currentAgentMinutes)
              ? targetAgentMinutes - currentAgentMinutes
              : null,
          },
        }),
      });
    });
    const publishedTotals = strategy.topology || strategy.target || strategy.totals || {};
    const target = {
      groups: Number.isFinite(Number(publishedTotals.groups))
        ? Number(publishedTotals.groups)
        : queues.reduce(function (sum, queue) { return sum + Number(((queue.demand || {}).target || {}).groups || 0); }, 0),
      jobs: Number.isFinite(Number(publishedTotals.jobs))
        ? Number(publishedTotals.jobs)
        : queues.reduce(function (sum, queue) { return sum + Number(((queue.demand || {}).target || {}).jobs || 0); }, 0),
      gpu_slots: Number.isFinite(Number(publishedTotals.gpu_slots))
        ? Number(publishedTotals.gpu_slots)
        : queues.reduce(function (sum, queue) { return sum + Number(((queue.demand || {}).target || {}).gpu_slots || 0); }, 0),
      agent_minutes: queues.reduce(function (sum, queue) {
        const raw = ((queue.demand || {}).target || {}).agent_minutes;
        return Number.isFinite(Number(raw)) ? sum + Number(raw) : sum;
      }, 0),
    };
    const topology = profile.topology || {};
    const current = topology.current || {};
    return Object.assign({}, profile, {
      queues: queues,
      topology: Object.assign({}, topology, {
        target: target,
        delta: {
          groups: target.groups - Number(current.groups || 0),
          jobs: target.jobs - Number(current.jobs || 0),
          gpu_slots: target.gpu_slots - Number(current.gpu_slots || 0),
          agent_minutes: target.agent_minutes - Number(current.agent_minutes || 0),
        },
      }),
      selected_placement_strategy: strategy,
    });
  }

  function capacityTopologyForGroups(profile, rawGroups, forcedJobs) {
    const queues = (profile && profile.queues) || [];
    const topology = (profile && profile.topology) || {};
    const currentTotal = topology.current || {};
    const targetTotal = topology.target || {};
    const groups = capacityInteger(rawGroups, Number(targetTotal.groups || 160), 0, 5000);
    const groupWeights = queues.map(function (queue) {
      const demand = queue.demand || {};
      return capacityInterpolatedValue(
        (demand.current || {}).groups,
        (demand.target || {}).groups,
        groups,
        currentTotal.groups,
        targetTotal.groups
      );
    });
    const jobWeights = queues.map(function (queue) {
      const demand = queue.demand || {};
      return capacityInterpolatedValue(
        (demand.current || {}).jobs,
        (demand.target || {}).jobs,
        groups,
        currentTotal.groups,
        targetTotal.groups
      );
    });
    const expectedJobs = capacityInterpolatedValue(
      currentTotal.jobs,
      targetTotal.jobs,
      groups,
      currentTotal.groups,
      targetTotal.groups
    );
    const totalJobs = forcedJobs === undefined || forcedJobs === null
      ? Math.max(0, Math.round(expectedJobs))
      : capacityInteger(forcedJobs, Math.round(expectedJobs), 0, 50000);
    const allocation = capacityPairedAllocation(groupWeights, jobWeights, groups, totalJobs);
    return {
      mode: forcedJobs === undefined || forcedJobs === null ? 'groups' : 'jobs',
      groups: groups,
      jobs: totalJobs,
      allocationValid: allocation.valid,
      allocationExact: allocation.exact,
      rows: queues.map(function (queue, index) {
        const currentJobs = Math.max(0, Number((((queue || {}).demand || {}).current || {}).jobs) || 0);
        return {
          queue: queue,
          id: queue.id,
          label: queue.label || queue.id,
          family: queue.family || 'unknown',
          gpusPerJob: Math.max(1, Number(queue.gpus_per_job) || 1),
          groups: allocation.groups[index],
          jobs: allocation.jobs[index],
          incrementalJobsPerSuite: Math.max(0, allocation.jobs[index] - currentJobs),
          serviceMinutes: Number((queue.workload || {}).service_minutes),
          serviceSource: (queue.workload || {}).service_minutes_source || 'unavailable',
        };
      }),
    };
  }

  function capacityGroupsForJobs(profile, rawJobs) {
    const topology = (profile && profile.topology) || {};
    const current = topology.current || {};
    const target = topology.target || {};
    const jobs = capacityInteger(rawJobs, Number(target.jobs || 196), 0, 50000);
    if (jobs <= Number(current.jobs || 0)) {
      return Number(current.jobs || 0)
        ? Math.round(Number(current.groups || 0) * jobs / Number(current.jobs))
        : 0;
    }
    if (jobs <= Number(target.jobs || 0) && Number(target.jobs || 0) > Number(current.jobs || 0)) {
      return Math.round(
        Number(current.groups || 0)
        + (Number(target.groups || 0) - Number(current.groups || 0))
        * (jobs - Number(current.jobs || 0))
        / (Number(target.jobs || 0) - Number(current.jobs || 0))
      );
    }
    return Number(target.jobs || 0)
      ? Math.round(Number(target.groups || 0) * jobs / Number(target.jobs))
      : 0;
  }

  function capacityTopologyForQueue(profile, queueId, rawGroups, rawParallel, rawDuration) {
    const queues = (profile && profile.queues) || [];
    const addedGroups = capacityInteger(rawGroups, 1, 0, 5000);
    const parallel = capacityInteger(rawParallel, 1, 1, 256);
    const durationMinutes = capacityInteger(rawDuration, 30, 1, 1440);
    const current = ((profile || {}).topology || {}).current || {};
    let found = false;
    const rows = queues.map(function (queue) {
      const selected = queue.id === queueId;
      found = found || selected;
      return {
        queue: queue,
        id: queue.id,
        label: queue.label || queue.id,
        family: queue.family || 'unknown',
        gpusPerJob: Math.max(1, Number(queue.gpus_per_job) || 1),
        groups: selected ? addedGroups : 0,
        jobs: selected ? addedGroups * parallel : 0,
        incrementalJobsPerSuite: selected ? addedGroups * parallel : 0,
        serviceMinutes: selected ? durationMinutes : Number((queue.workload || {}).service_minutes),
        serviceSource: selected ? 'user_input_for_specific_test_shape' : ((queue.workload || {}).service_minutes_source || 'unavailable'),
      };
    });
    return {
      mode: 'queue',
      groups: found ? addedGroups : 0,
      jobs: rows.reduce(function (sum, row) { return sum + row.jobs; }, 0),
      totalGateGroups: Number(current.groups || 0) + (found ? addedGroups : 0),
      totalGateJobs: Number(current.jobs || 0) + (found ? addedGroups * parallel : 0),
      addedGroups: addedGroups,
      parallel: parallel,
      durationMinutes: durationMinutes,
      selectedQueue: found ? queueId : '',
      rows: rows,
    };
  }

  function capacityBurstWait(demandJobs, capacityJobs, baseline, serviceMinutes) {
    const jobs = Math.max(0, Math.round(Number(demandJobs) || 0));
    if (!jobs) {
      return {
        status: 'finite',
        p50: 0,
        p95: 0,
        max: 0,
        allStartedBy: 0,
        allCompletedBy: 0,
        samples: [],
        completionSamples: [],
        effectiveSlots: 0,
        backlogJobs: 0,
      };
    }
    if (jobs > CAPACITY_MAX_INTERACTIVE_BURST_JOBS) {
      return {
        status: 'unavailable',
        reason: 'The one-time burst exceeds the 50,000-job interactive safety limit; reduce the scenario size.',
        samples: [],
        completionSamples: [],
        limitJobs: CAPACITY_MAX_INTERACTIVE_BURST_JOBS,
      };
    }
    if (!baseline || baseline.available !== true || !Number.isFinite(Number(baseline.running)) || !Number.isFinite(Number(baseline.waiting))) {
      return {status: 'unavailable', reason: 'Observed running/waiting baseline is unavailable.', samples: []};
    }
    const capacity = Math.max(0, Math.floor(Number(capacityJobs) || 0));
    if (!capacity) return {status: 'unavailable', reason: 'Configured concurrent-job capacity is unavailable.', samples: []};
    const service = Number(serviceMinutes);
    if (!Number.isFinite(service) || service <= 0) {
      return {status: 'unavailable', reason: 'A positive service-time estimate is unavailable.', samples: []};
    }
    const runningJobs = Math.ceil(Math.max(0, Number(baseline.running)));
    const busyServers = Math.min(runningJobs, capacity);
    const backlogJobs = Math.max(0, runningJobs - capacity)
      + Math.ceil(Math.max(0, Number(baseline.waiting)));
    const serverAvailableAt = Array.from({length: capacity}, function (_, index) {
      return index < busyServers ? service : 0;
    });
    function assignJob() {
      let serverIndex = 0;
      for (let index = 1; index < serverAvailableAt.length; index += 1) {
        if (serverAvailableAt[index] < serverAvailableAt[serverIndex]) serverIndex = index;
      }
      const startsAt = serverAvailableAt[serverIndex];
      serverAvailableAt[serverIndex] += service;
      return startsAt;
    }
    for (let index = 0; index < backlogJobs; index += 1) assignJob();
    const samples = Array.from({length: jobs}, function () { return assignJob(); });
    const completionSamples = samples.map(function (startsAt) {
      return startsAt + service;
    });
    function observedQuantile(percentile) {
      if (!samples.length) return 0;
      return samples[Math.max(0, Math.ceil(percentile * samples.length) - 1)];
    }
    return {
      status: 'finite',
      p50: observedQuantile(0.5),
      p95: observedQuantile(0.95),
      max: samples[samples.length - 1],
      allStartedBy: samples[samples.length - 1],
      allCompletedBy: completionSamples[completionSamples.length - 1],
      samples: samples,
      completionSamples: completionSamples,
      effectiveSlots: capacity,
      busyServers: busyServers,
      backlogJobs: backlogJobs,
    };
  }

  function capacityErlangC(arrivalRateJobsPerHour, capacityJobs, serviceMinutes) {
    if (arrivalRateJobsPerHour === null || arrivalRateJobsPerHour === undefined || arrivalRateJobsPerHour === '') {
      return {status: 'unavailable', reason: 'The weekday started-cohort arrival-rate proxy is unavailable.'};
    }
    const arrivalRate = Number(arrivalRateJobsPerHour);
    const capacity = Math.floor(Number(capacityJobs));
    const service = Number(serviceMinutes);
    if (!Number.isFinite(arrivalRate) || arrivalRate < 0) {
      return {status: 'unavailable', reason: 'The weekday started-cohort arrival-rate proxy is unavailable.'};
    }
    if (!Number.isFinite(capacity) || capacity <= 0) {
      return {status: 'unavailable', reason: 'Configured concurrent-job capacity is unavailable.'};
    }
    if (!Number.isFinite(service) || service <= 0) {
      return {status: 'unavailable', reason: 'A positive service-time estimate is unavailable.'};
    }
    const offeredLoad = arrivalRate * service / 60;
    const rho = offeredLoad / capacity;
    if (rho >= 1) {
      return {
        status: 'unstable',
        arrivalRateJobsPerHour: arrivalRate,
        offeredLoadJobs: offeredLoad,
        rho: rho,
        requiredCapacityJobs: Math.floor(offeredLoad) + 1,
        capacityGapJobs: Math.max(0, Math.floor(offeredLoad) + 1 - capacity),
        reason: 'Long-run offered load meets or exceeds configured runner capacity (ρ ≥ 1).',
      };
    }
    if (!arrivalRate) {
      return {
        status: 'finite',
        arrivalRateJobsPerHour: 0,
        offeredLoadJobs: 0,
        rho: 0,
        probabilityWait: 0,
        mean: 0,
        p50: 0,
        p95: 0,
        requiredCapacityJobs: 1,
        capacityGapJobs: 0,
      };
    }
    let erlangB = 1;
    for (let servers = 1; servers <= capacity; servers += 1) {
      erlangB = offeredLoad * erlangB / (servers + offeredLoad * erlangB);
    }
    const probabilityWait = erlangB / (1 - rho + rho * erlangB);
    const spareServers = capacity - offeredLoad;
    const mean = probabilityWait * service / spareServers;
    function waitQuantile(percentile) {
      if (percentile <= 1 - probabilityWait) return 0;
      return -service / spareServers * Math.log((1 - percentile) / probabilityWait);
    }
    return {
      status: 'finite',
      arrivalRateJobsPerHour: arrivalRate,
      offeredLoadJobs: offeredLoad,
      rho: rho,
      probabilityWait: probabilityWait,
      mean: mean,
      p50: waitQuantile(0.5),
      p95: waitQuantile(0.95),
      requiredCapacityJobs: Math.floor(offeredLoad) + 1,
      capacityGapJobs: 0,
    };
  }

  function capacityScenario(profile, inputs) {
    inputs = inputs || {};
    const mode = ['groups', 'jobs', 'queue'].includes(inputs.mode) ? inputs.mode : 'groups';
    profile = mode === 'queue'
      ? (profile || {})
      : capacityProfileForPlacement(profile || {}, inputs.placement);
    const trafficMode = inputs.trafficMode === 'sustained' ? 'sustained' : 'burst';
    let topology;
    if (mode === 'jobs') {
      const jobs = capacityInteger(inputs.jobs, Number((((profile.topology || {}).target || {}).jobs) || 196), 0, 50000);
      topology = capacityTopologyForGroups(profile, capacityGroupsForJobs(profile, jobs), jobs);
    } else if (mode === 'queue') {
      topology = capacityTopologyForQueue(
        profile,
        inputs.queue,
        inputs.queueGroups,
        inputs.parallel,
        inputs.duration
      );
    } else {
      topology = capacityTopologyForGroups(profile, inputs.groups, null);
    }
    const baselineName = ['current', 'typical', 'peak', 'stress'].includes(inputs.baseline) ? inputs.baseline : 'peak';
    const suites = capacityInteger(inputs.suites, 1, 1, 20);
    const rawSuitesPerHour = Number(inputs.suitesPerHour);
    const suitesPerHour = Number.isFinite(rawSuitesPerHour)
      ? Math.max(0, Math.min(1000, rawSuitesPerHour))
      : 1;
    const burstLimitExceeded = trafficMode === 'burst'
      && Math.max(0, Number(topology.jobs) || 0) * suites > CAPACITY_MAX_INTERACTIVE_BURST_JOBS;
    const rows = topology.rows.map(function (topologyRow) {
      const queue = topologyRow.queue || {};
      const baseline = ((queue.history || {})[baselineName]) || {};
      const capacityJobs = Math.max(0, Number(queue.capacity_jobs) || 0);
      const demandJobs = Math.max(
        0,
        topologyRow.jobs * (trafficMode === 'burst' ? suites : 1)
      );
      const incrementalJobsPerSuite = Math.max(0, Number(topologyRow.incrementalJobsPerSuite) || 0);
      const rawHistoricalRate = (queue.workload || {}).weekday_started_cohort_rate_jobs_per_hour;
      const historicalArrivalRate = rawHistoricalRate !== null
        && rawHistoricalRate !== undefined
        && Number.isFinite(Number(rawHistoricalRate))
        ? Math.max(0, Number(rawHistoricalRate))
        : null;
      const incrementalArrivalRate = suitesPerHour * incrementalJobsPerSuite;
      const totalArrivalRate = historicalArrivalRate === null
        ? null
        : historicalArrivalRate + incrementalArrivalRate;
      const wait = burstLimitExceeded
        ? {
          status: 'unavailable',
          reason: 'The one-time burst exceeds the 50,000-job interactive safety limit; reduce the scenario size.',
          samples: [],
          completionSamples: [],
          limitJobs: CAPACITY_MAX_INTERACTIVE_BURST_JOBS,
        }
        : trafficMode === 'sustained'
          ? capacityErlangC(totalArrivalRate, capacityJobs, topologyRow.serviceMinutes)
          : capacityBurstWait(demandJobs, capacityJobs, baseline, topologyRow.serviceMinutes);
      const baselineRunning = baseline.available === true && Number.isFinite(Number(baseline.running))
        ? Number(baseline.running)
        : null;
      const baselineWaiting = baseline.available === true && Number.isFinite(Number(baseline.waiting))
        ? Number(baseline.waiting)
        : null;
      const combinedJobs = baselineRunning === null || baselineWaiting === null
        ? null
        : Math.ceil(baselineRunning) + Math.ceil(baselineWaiting) + demandJobs;
      const steadyOfferedLoad = wait.status === 'unavailable'
        ? null
        : Number(wait.offeredLoadJobs || 0);
      const pressureJobs = trafficMode === 'sustained' ? steadyOfferedLoad : combinedJobs;
      const combinedGapJobs = trafficMode === 'sustained'
        ? (wait.status === 'unavailable' ? null : Number(wait.capacityGapJobs || 0))
        : (combinedJobs === null ? null : Math.max(0, combinedJobs - capacityJobs));
      return {
        id: topologyRow.id,
        label: topologyRow.label,
        family: topologyRow.family,
        gpusPerJob: topologyRow.gpusPerJob,
        groups: topologyRow.groups,
        jobsPerSuite: topologyRow.jobs,
        incrementalJobsPerSuite: incrementalJobsPerSuite,
        demandJobs: demandJobs,
        demandGpuSlots: demandJobs * topologyRow.gpusPerJob,
        capacityJobs: capacityJobs,
        capacityGpuSlots: capacityJobs * topologyRow.gpusPerJob,
        baseline: baseline,
        baselineRunning: baselineRunning,
        baselineWaiting: baselineWaiting,
        historicalArrivalRate: historicalArrivalRate,
        incrementalArrivalRate: incrementalArrivalRate,
        arrivalRate: totalArrivalRate,
        offeredLoadJobs: steadyOfferedLoad,
        combinedJobs: combinedJobs,
        pressurePct: pressureJobs !== null && capacityJobs ? pressureJobs / capacityJobs * 100 : null,
        shapeGapJobs: Math.max(0, demandJobs - capacityJobs),
        shapeGapGpus: Math.max(0, demandJobs - capacityJobs) * topologyRow.gpusPerJob,
        combinedGapJobs: combinedGapJobs,
        combinedGapGpus: combinedGapJobs === null ? null : combinedGapJobs * topologyRow.gpusPerJob,
        serviceMinutes: topologyRow.serviceMinutes,
        serviceSource: topologyRow.serviceSource,
        wait: wait,
        sourceQueue: queue,
      };
    });
    const activeRows = rows.filter(function (row) {
      if (trafficMode === 'burst') return row.demandJobs > 0;
      return row.arrivalRate === null
        ? row.jobsPerSuite > 0 || Number((((row.sourceQueue || {}).demand || {}).current || {}).jobs || 0) > 0
        : row.arrivalRate > 0 || row.incrementalJobsPerSuite > 0;
    });
    const families = {};
    rows.forEach(function (row) {
      const family = families[row.family] || {
        family: row.family,
        demandGpus: 0,
        combinedDemandGpus: 0,
        combinedAvailable: true,
        capacityGpus: 0,
      };
      family.demandGpus += row.demandGpuSlots;
      const familyPressureJobs = trafficMode === 'sustained' ? row.offeredLoadJobs : row.combinedJobs;
      if (familyPressureJobs === null) family.combinedAvailable = false;
      else family.combinedDemandGpus += familyPressureJobs * row.gpusPerJob;
      family.capacityGpus += row.capacityGpuSlots;
      families[row.family] = family;
    });
    const familyRows = Object.values(families).map(function (family) {
      return Object.assign({}, family, {
        gapGpus: Math.max(0, family.demandGpus - family.capacityGpus),
        zeroWaitGapGpus: family.combinedAvailable
          ? Math.max(0, family.combinedDemandGpus - family.capacityGpus)
          : null,
      });
    });
    const unavailableRows = activeRows.filter(function (row) { return row.wait.status === 'unavailable'; });
    const unstableRows = activeRows.filter(function (row) { return row.wait.status === 'unstable'; });
    const waitStatus = unstableRows.length ? 'unstable' : unavailableRows.length ? 'unavailable' : 'finite';
    const samples = trafficMode === 'burst' && waitStatus === 'finite'
      ? activeRows.reduce(function (all, row) { return all.concat(row.wait.samples || []); }, []).sort(function (left, right) { return left - right; })
      : [];
    const completionSamples = trafficMode === 'burst' && waitStatus === 'finite'
      ? activeRows.reduce(function (all, row) { return all.concat(row.wait.completionSamples || []); }, []).sort(function (left, right) { return left - right; })
      : [];
    function sampleQuantile(percentile) {
      if (!samples.length) return 0;
      return samples[Math.max(0, Math.ceil(percentile * samples.length) - 1)];
    }
    const rankedRows = activeRows.slice().sort(function (left, right) {
      const statusRank = {unstable: 3, unavailable: 2, finite: 1};
      return (statusRank[right.wait.status] || 0) - (statusRank[left.wait.status] || 0)
        || Number(right.pressurePct || 0) - Number(left.pressurePct || 0)
        || Number(right.wait.p95 || 0) - Number(left.wait.p95 || 0)
        || compareText(left.label, right.label);
    });
    const baselineComplete = rows.every(function (row) { return row.baseline.available === true; });
    const totalCapacityGpus = rows.reduce(function (sum, row) { return sum + row.capacityGpuSlots; }, 0);
    const baselineQueuedGpus = baselineComplete
      ? rows.reduce(function (sum, row) {
        return sum + (
          Math.ceil(Number(row.baselineRunning || 0))
          + Math.ceil(Number(row.baselineWaiting || 0))
        ) * row.gpusPerJob;
      }, 0)
      : null;
    const demandGpuSlots = activeRows.reduce(function (sum, row) { return sum + row.demandGpuSlots; }, 0);
    const zeroWaitFamilyAvailable = familyRows.every(function (row) { return row.zeroWaitGapGpus !== null; });
    const finiteSteadyRows = activeRows.filter(function (row) { return row.wait.status === 'finite'; });
    const steadyP50 = finiteSteadyRows.length
      ? Math.max.apply(null, finiteSteadyRows.map(function (row) { return Number(row.wait.p50 || 0); }))
      : 0;
    const steadyP95 = finiteSteadyRows.length
      ? Math.max.apply(null, finiteSteadyRows.map(function (row) { return Number(row.wait.p95 || 0); }))
      : 0;
    const allStartedBy = trafficMode === 'burst' && waitStatus === 'finite'
      ? (samples[samples.length - 1] || 0)
      : null;
    const allCompletedBy = trafficMode === 'burst' && waitStatus === 'finite'
      ? (completionSamples[completionSamples.length - 1] || 0)
      : null;
    return {
      mode: mode,
      trafficMode: trafficMode,
      burstLimitExceeded: burstLimitExceeded,
      burstLimitJobs: CAPACITY_MAX_INTERACTIVE_BURST_JOBS,
      baseline: baselineName,
      suites: suites,
      suitesPerHour: suitesPerHour,
      placementStrategy: mode === 'queue' ? null : (profile.selected_placement_strategy || null),
      groups: topology.groups,
      totalGateGroups: topology.totalGateGroups === undefined ? topology.groups : topology.totalGateGroups,
      totalGateJobs: topology.totalGateJobs === undefined ? topology.jobs : topology.totalGateJobs,
      jobsPerSuite: topology.jobs,
      jobs: activeRows.reduce(function (sum, row) { return sum + row.demandJobs; }, 0),
      gpuSlots: demandGpuSlots,
      rows: rows,
      activeRows: activeRows,
      familyRows: familyRows,
      familyGapGpus: familyRows.reduce(function (sum, row) { return sum + row.gapGpus; }, 0),
      shapeGapGpus: activeRows.reduce(function (sum, row) { return sum + row.shapeGapGpus; }, 0),
      zeroWaitShapeGapGpus: activeRows.some(function (row) { return row.combinedGapGpus === null; })
        ? null
        : activeRows.reduce(function (sum, row) { return sum + row.combinedGapGpus; }, 0),
      zeroWaitFamilyGapGpus: zeroWaitFamilyAvailable
        ? familyRows.reduce(function (sum, row) { return sum + row.zeroWaitGapGpus; }, 0)
        : null,
      waitStatus: waitStatus,
      p50Wait: waitStatus === 'finite'
        ? (trafficMode === 'sustained' ? steadyP50 : sampleQuantile(0.5))
        : null,
      p95Wait: waitStatus === 'finite'
        ? (trafficMode === 'sustained' ? steadyP95 : sampleQuantile(0.95))
        : null,
      maxWait: waitStatus === 'finite' && trafficMode === 'burst' ? (samples[samples.length - 1] || 0) : null,
      allStartedBy: allStartedBy,
      allCompletedBy: allCompletedBy,
      unavailableQueues: unavailableRows.map(function (row) { return row.id; }),
      unstableQueues: unstableRows.map(function (row) { return row.id; }),
      bottleneck: rankedRows[0] || null,
      totalCapacityGpus: totalCapacityGpus,
      baselineGpus: baselineQueuedGpus,
      baselineQueuedGpus: baselineQueuedGpus,
      aggregatePressurePct: trafficMode === 'sustained'
        ? (
          totalCapacityGpus && !activeRows.some(function (row) { return row.offeredLoadJobs === null; })
            ? activeRows.reduce(function (sum, row) {
              return sum + Number(row.offeredLoadJobs || 0) * row.gpusPerJob;
            }, 0) / totalCapacityGpus * 100
            : null
        )
        : (
          baselineQueuedGpus !== null && totalCapacityGpus
            ? (baselineQueuedGpus + demandGpuSlots) / totalCapacityGpus * 100
            : null
        ),
      historicalArrivalRate: activeRows.reduce(function (sum, row) {
        return sum + Number(row.historicalArrivalRate || 0);
      }, 0),
      incrementalArrivalRate: activeRows.reduce(function (sum, row) {
        return sum + Number(row.incrementalArrivalRate || 0);
      }, 0),
      offeredLoadGpuSlots: activeRows.some(function (row) { return row.offeredLoadJobs === null; })
        ? null
        : activeRows.reduce(function (sum, row) {
          return sum + Number(row.offeredLoadJobs || 0) * row.gpusPerJob;
        }, 0),
      maximumRho: activeRows.some(function (row) { return row.wait.status === 'unavailable'; })
        ? null
        : activeRows.reduce(function (maximum, row) {
          return Math.max(maximum, Number(row.wait.rho || 0));
        }, 0),
      stabilityGapGpus: activeRows.reduce(function (sum, row) {
        return sum + Number(row.wait.capacityGapJobs || 0) * row.gpusPerJob;
      }, 0),
      unplacedRetiring: profile.unplaced_retiring_workload || null,
      topology: topology,
    };
  }

  function capacityGrowthCurve(profile, inputs, selectedValue) {
    inputs = inputs || {};
    const topology = (profile || {}).topology || {};
    const current = topology.current || {};
    const target = topology.target || {};
    const mode = ['groups', 'jobs', 'queue'].includes(inputs.mode) ? inputs.mode : 'groups';
    let start;
    let end;
    let selected;
    let axisLabel;
    let pointInputs;
    if (mode === 'jobs') {
      start = Math.max(0, Number(current.jobs || 0));
      selected = capacityInteger(
        selectedValue === undefined ? inputs.jobs : selectedValue,
        Number(target.jobs || 196),
        0,
        50000
      );
      end = Math.max(Number(target.jobs || 196), selected, Math.ceil(Number(target.jobs || 196) * 1.5), start + 8);
      axisLabel = 'Total command jobs';
      pointInputs = function (point) { return {mode: 'jobs', jobs: point}; };
    } else if (mode === 'queue') {
      start = 0;
      selected = capacityInteger(
        selectedValue === undefined ? inputs.queueGroups : selectedValue,
        1,
        0,
        5000
      );
      end = Math.max(8, selected, Math.ceil(selected * 2), start + 8);
      axisLabel = 'New mirror groups on selected queue';
      pointInputs = function (point) { return {mode: 'queue', queueGroups: point}; };
    } else {
      start = Math.max(0, Number(current.groups || 0));
      selected = capacityInteger(
        selectedValue === undefined ? inputs.groups : selectedValue,
        Number(target.groups || 160),
        0,
        5000
      );
      end = Math.max(Number(target.groups || 160), selected, Math.ceil(Number(target.groups || 160) * 1.5), start + 8);
      axisLabel = 'Selected test groups';
      pointInputs = function (point) { return {mode: 'groups', groups: point}; };
    }
    const points = [];
    for (let index = 0; index <= 8; index += 1) {
      const point = Math.round(start + (end - start) * index / 8);
      if (!points.includes(point)) points.push(point);
    }
    if (!points.includes(selected)) points.push(selected);
    points.sort(function (left, right) { return left - right; });
    return points.map(function (point) {
      const scenario = capacityScenario(profile, Object.assign({}, inputs, pointInputs(point)));
      return {
        mode: mode,
        x: point,
        axisLabel: axisLabel,
        selected: point === selected,
        groups: scenario.totalGateGroups,
        jobs: scenario.totalGateJobs,
        burstJobs: scenario.jobs,
        trafficMode: scenario.trafficMode,
        suitesPerHour: scenario.suitesPerHour,
        status: scenario.waitStatus,
        p95Wait: scenario.p95Wait,
        maxWait: scenario.maxWait,
        maximumRho: scenario.maximumRho,
        stabilityGapGpus: scenario.stabilityGapGpus,
        bottleneck: scenario.bottleneck ? scenario.bottleneck.id : '',
        pressurePct: scenario.bottleneck ? scenario.bottleneck.pressurePct : null,
      };
    });
  }

  function capacityWaitLabel(status, minutes) {
    if (status === 'unstable') return 'Unstable (ρ ≥ 1)';
    if (status === 'unavailable') return 'Not estimable';
    return duration(minutes);
  }

  function capacityServiceSourceLabel(source) {
    const labels = {
      target_command_job_median_average: 'target-runtime command-job median average',
      placement_strategy_target_command_job_median_average: 'selected-placement target-runtime command-job median average',
      completed_agent_minutes_per_finished_job_proxy_fallback: 'completed mapping proxy fallback (potentially downward biased)',
      target_suite_global_median_average_fallback: 'global target-suite median average fallback',
      user_input_for_specific_test_shape: 'manual scenario input',
    };
    return labels[source] || source || 'unavailable';
  }

  function capacityVerdict(result) {
    const unplacedText = result.unplacedRetiring && result.unplacedRetiring.excluded_from_wait_and_headroom
      ? 'Retiring MI325 workload is unplaced and excluded from every wait and headroom figure below until a compatible destination is chosen. '
      : '';
    let waitText;
    if (result.burstLimitExceeded) {
      waitText = 'This one-time burst exceeds the ' + integer(result.burstLimitJobs)
        + '-job browser safety limit, so wait is intentionally not simulated. Reduce jobs or simultaneous suites.';
    } else if (result.trafficMode === 'sustained' && result.waitStatus === 'unstable') {
      waitText = 'Sustained arrivals are unstable on ' + result.unstableQueues.join(', ')
        + ': long-run offered load meets or exceeds configured runners, so the queue grows without a finite steady-state wait. '
        + 'Add at least ' + integer(result.stabilityGapGpus) + ' queue-shaped GPUs, reduce suites/hour, or change an explicitly supported placement.';
    } else if (result.waitStatus === 'unavailable') {
      waitText = 'Wait cannot be estimated because configured capacity, weekday cohort-rate, or service-time inputs are missing for '
        + result.unavailableQueues.join(', ') + '.';
    } else if (result.trafficMode === 'sustained') {
      waitText = 'The sustained Erlang-C approximation is stable at every used queue: worst-queue p95 wait is '
        + duration(result.p95Wait) + ' and maximum utilization is '
        + (Number(result.maximumRho || 0) * 100).toFixed(1) + '%.';
    } else {
      waitText = 'For this one-time burst with no future arrivals, projected FCFS start wait is ' + duration(result.p50Wait) + ' p50, '
        + duration(result.p95Wait) + ' p95, and ' + duration(result.maxWait)
        + ' maximum; all scenario jobs start by ' + duration(result.allStartedBy)
        + ' and finish by ' + duration(result.allCompletedBy)
        + ' under the conservative full-service residual assumption.';
    }
    let standaloneText;
    if (result.familyGapGpus > 0) {
      standaloneText = 'To start every standalone-suite job at once, the configured fixed-family pools are short '
        + integer(result.familyGapGpus)
        + ' GPU slots; add capacity, serialize the workload, or validate cross-family migration.';
    } else if (result.shapeGapGpus > 0) {
      standaloneText = 'To start every standalone-suite job at once, reallocate '
        + integer(result.shapeGapGpus)
        + ' GPUs into the constrained queue shapes. The FCFS estimate above shows the wait if those jobs run in waves instead.';
    } else {
      standaloneText = 'Every standalone-suite job can start at once within the configured queue shapes and hardware-family pools.';
    }
    let zeroWaitText;
    if (result.zeroWaitShapeGapGpus === null || result.zeroWaitFamilyGapGpus === null) {
      zeroWaitText = 'Zero-wait headroom cannot be calculated without a complete background baseline.';
    } else if (result.zeroWaitFamilyGapGpus > 0) {
      zeroWaitText = 'At the selected background, zero start wait would require '
        + integer(result.zeroWaitFamilyGapGpus) + ' additional fixed-family GPUs and '
        + integer(result.zeroWaitShapeGapGpus) + ' queue-shaped GPU headroom, or the modeled wait must be tolerated.';
    } else if (result.zeroWaitShapeGapGpus > 0) {
      zeroWaitText = 'At the selected background, zero start wait would require '
        + integer(result.zeroWaitShapeGapGpus)
        + ' queue-shaped GPU headroom; this transient requirement is separate from the suite-alone simultaneous-start gap.';
    } else {
      zeroWaitText = 'The selected background retains enough configured headroom for zero start wait.';
    }
    return unplacedText + waitText + ' ' + standaloneText
      + (result.trafficMode === 'burst' ? ' ' + zeroWaitText : '');
  }

  function openCapacityQueueDetail(row, result) {
    const wait = row.wait || {};
    const history = (row.sourceQueue || {}).history || {};
    const note = n('div', 'ops-evidence-note is-info', result.trafficMode === 'sustained'
      ? 'Planning estimate, not an SLA. Erlang-C uses the weekday created-at cohort that later started as an arrival-rate proxy, plus only the selected expansion delta. It does not add snapshot occupancy. Cross-queue migration is never inferred.'
      : 'Planning estimate, not an SLA. This one-time burst assumes no future arrivals. FCFS gives every observed running job one conservative full service interval of residual work, places observed waiting jobs ahead, and then list-schedules the scenario onto the configured runners. Cross-queue migration is never inferred.');
    openDetailDrawer({
      id: 'capacity-queue-' + row.id,
      title: row.label,
      subtitle: row.family + ' queue bottleneck evidence',
      description: wait.reason || 'Projected queue response for the selected route-shareable scenario.',
      fields: [
        {label: 'Traffic model', value: result.trafficMode === 'sustained' ? 'Sustained arrivals' : 'One-time burst'},
        {label: 'Provider', value: (row.sourceQueue || {}).provider || 'Not specified'},
        {label: 'Baseline', value: result.trafficMode === 'sustained' ? 'Five-weekday started-cohort rate proxy' : result.baseline},
        {label: 'Observed running / waiting', value: row.baselineRunning === null ? null : value(row.baselineRunning) + ' / ' + value(row.baselineWaiting)},
        {label: 'Scenario jobs', value: integer(row.demandJobs)},
        {label: 'Historical / added / total jobs per hour', value: result.trafficMode === 'sustained' ? (row.historicalArrivalRate === null ? 'Unavailable' : row.historicalArrivalRate.toFixed(2) + ' / ' + row.incrementalArrivalRate.toFixed(2) + ' / ' + row.arrivalRate.toFixed(2)) : 'Not applicable'},
        {label: 'Offered load / utilization', value: result.trafficMode === 'sustained' && row.offeredLoadJobs !== null ? row.offeredLoadJobs.toFixed(2) + ' jobs / ' + (Number(wait.rho || 0) * 100).toFixed(1) + '%' : 'Not applicable'},
        {label: 'Configured slots', value: integer(row.capacityJobs)},
        {label: 'Combined pressure', value: row.pressurePct === null ? null : row.pressurePct.toFixed(1) + '%'},
        {label: 'Service estimate', value: Number.isFinite(Number(row.serviceMinutes)) ? duration(row.serviceMinutes) + ' · ' + capacityServiceSourceLabel(row.serviceSource) : 'Unavailable'},
        {label: 'p50 / p95 start wait', value: capacityWaitLabel(wait.status, wait.p50) + ' / ' + capacityWaitLabel(wait.status, wait.p95)},
        {label: 'All one-time jobs started / completed by', value: result.trafficMode === 'burst' ? capacityWaitLabel(wait.status, wait.allStartedBy) + ' / ' + capacityWaitLabel(wait.status, wait.allCompletedBy) : 'Not applicable'},
        {label: 'Suite-alone simultaneous-start gap', value: integer(row.shapeGapJobs) + ' jobs · ' + integer(row.shapeGapGpus) + ' GPUs'},
        {label: 'Background + suite zero-wait gap', value: row.combinedGapJobs === null ? 'Unavailable' : integer(row.combinedGapJobs) + ' jobs · ' + integer(row.combinedGapGpus) + ' GPUs'},
        {label: 'History samples', value: integer(history.sample_count || 0)},
        {label: 'Snapshots above today’s quota', value: integer(history.snapshots_above_configured_capacity || 0)},
      ],
      sources: [
        {label: 'Open capacity projection aggregate', url: SOURCE_ASSETS.trajectory},
        {label: 'Inspect raw queue history', url: SOURCE_ASSETS.queueHistory},
      ],
      content: note,
    });
  }

  function capacityRouteNumberField(label, stateKey, queryKey, rawValue, minimum, maximum, suffix) {
    const field = n('label', 'ops-capacity-field');
    const input = n('input', 'ops-input ops-capacity-number');
    input.type = 'number';
    input.min = String(minimum);
    input.max = String(maximum);
    input.step = '1';
    input.value = String(rawValue);
    input.setAttribute('aria-label', label);
    input.addEventListener('change', function () {
      const next = String(capacityInteger(input.value, Number(rawValue), minimum, maximum));
      setRouteState('ci-hotness', stateKey, next, queryKey);
    });
    add(field, [n('span', 'ops-field-label', label), n('div', 'ops-capacity-input-wrap')]);
    field.lastChild.append(input);
    if (suffix) field.lastChild.append(n('span', 'ops-capacity-input-suffix', suffix));
    return field;
  }

  function capacityRouteRateField(label, stateKey, queryKey, rawValue, minimum, maximum, suffix) {
    const field = n('label', 'ops-capacity-field');
    const input = n('input', 'ops-input ops-capacity-number');
    input.type = 'number';
    input.min = String(minimum);
    input.max = String(maximum);
    input.step = '0.1';
    input.value = String(rawValue);
    input.setAttribute('aria-label', label);
    input.addEventListener('change', function () {
      const parsed = Number(input.value);
      const fallback = Number(rawValue);
      const next = Number.isFinite(parsed)
        ? Math.max(minimum, Math.min(maximum, parsed))
        : fallback;
      setRouteState('ci-hotness', stateKey, String(Math.round(next * 10) / 10), queryKey);
    });
    add(field, [n('span', 'ops-field-label', label), n('div', 'ops-capacity-input-wrap')]);
    field.lastChild.append(input);
    if (suffix) field.lastChild.append(n('span', 'ops-capacity-input-suffix', suffix));
    return field;
  }

  function renderCapacityProjection(host, capacity) {
    capacity = capacity || {};
    const rawProfile = capacity.simulation_profile || {};
    if (!capacity.available || !rawProfile.available || !(rawProfile.queues || []).length) {
      host.append(n('div', 'ops-evidence-note is-warning', 'Interactive capacity planning is unavailable until the AMD semantic matrix, queue quota catalog, mapping aggregate, and queue history are rebuilt into the operations snapshot.'));
      return;
    }
    const publishedPlacement = rawProfile.placement_profiles || capacity.placement_profiles || {};
    const defaultPlacementId = publishedPlacement.default_strategy_id || 'mi355_preferred';
    const requestedPlacementId = state.capacityPlacement || defaultPlacementId;
    const profile = capacityProfileForPlacement(
      Object.assign({}, rawProfile, {placement_profiles: publishedPlacement}),
      requestedPlacementId
    );
    const selectedPlacement = profile.selected_placement_strategy || null;
    const selectedPlacementId = selectedPlacement ? selectedPlacement.id : requestedPlacementId;
    const topology = profile.topology || {};
    const currentTopology = topology.current || {};
    const targetTopology = topology.target || {};
    const queueIds = profile.queues.map(function (queue) { return queue.id; });
    const selectedQueue = queueIds.includes(state.capacityQueue) ? state.capacityQueue : queueIds[0];
    const inputs = {
      mode: state.capacityMode,
      baseline: state.capacityBaseline,
      trafficMode: state.capacityTrafficMode,
      placement: selectedPlacementId,
      groups: capacityInteger(state.capacityGroups, Number(targetTopology.groups || 160), 0, 5000),
      jobs: capacityInteger(state.capacityJobs, Number(targetTopology.jobs || 196), 0, 50000),
      queue: selectedQueue,
      queueGroups: capacityInteger(state.capacityQueueGroups, 1, 0, 5000),
      parallel: capacityInteger(state.capacityParallel, 1, 1, 256),
      duration: capacityInteger(state.capacityDuration, 30, 1, 1440),
      suites: capacityInteger(state.capacitySuites, 1, 1, 20),
      suitesPerHour: Math.max(0, Math.min(1000, Number(state.capacitySuitesPerHour) || 0)),
    };
    const result = capacityScenario(profile, inputs);
    const curve = capacityGrowthCurve(profile, inputs);
    const bottleneck = result.bottleneck;
    const placementMi355 = (((result.placementStrategy || {}).families) || []).find(function (row) {
      return row.family === 'MI355';
    }) || {};

    const plannerControls = n('div', 'ops-capacity-controls');
    plannerControls.append(segmented([
      {id: 'groups', label: 'Target groups · auto mix'},
      {id: 'jobs', label: 'Total jobs · auto mix'},
      {id: 'queue', label: 'Specific queue / test'},
    ], inputs.mode, function (id) {
      setRouteState('ci-hotness', 'capacityMode', id, 'capacity_mode');
    }, 'Capacity simulation input mode'));
    plannerControls.append(segmented([
      {id: 'burst', label: 'One-time burst'},
      {id: 'sustained', label: 'Sustained arrivals'},
    ], inputs.trafficMode, function (id) {
      setRouteState('ci-hotness', 'capacityTrafficMode', id, 'capacity_traffic');
    }, 'Choose a one-time test-suite burst or a continuing arrival-rate model'));
    const fields = n('div', 'ops-capacity-fields');
    if (inputs.mode === 'groups') {
      fields.append(capacityRouteNumberField('Target test groups', 'capacityGroups', 'capacity_groups', inputs.groups, 0, 5000, 'groups'));
    } else if (inputs.mode === 'jobs') {
      fields.append(capacityRouteNumberField('Total command jobs', 'capacityJobs', 'capacity_jobs', inputs.jobs, 0, 50000, 'jobs'));
    } else {
      const queueField = n('label', 'ops-capacity-field');
      const queueSelect = n('select', 'ops-select ops-capacity-queue-select');
      queueSelect.setAttribute('aria-label', 'AMD queue for the new mirror');
      profile.queues.forEach(function (queue) {
        const option = n('option', '', (queue.label || queue.id) + ' · ' + queue.family + ' · ' + integer(queue.gpus_per_job) + ' GPU/job');
        option.value = queue.id;
        option.selected = queue.id === selectedQueue;
        queueSelect.append(option);
      });
      queueSelect.addEventListener('change', function () {
        setRouteState('ci-hotness', 'capacityQueue', queueSelect.value, 'capacity_queue');
      });
      add(queueField, [n('span', 'ops-field-label', 'AMD queue'), queueSelect]);
      fields.append(queueField);
      fields.append(capacityRouteNumberField('New mirror groups', 'capacityQueueGroups', 'capacity_queue_groups', inputs.queueGroups, 0, 5000, 'groups'));
      fields.append(capacityRouteNumberField('Parallel jobs / group', 'capacityParallel', 'capacity_parallel', inputs.parallel, 1, 256, 'jobs'));
      fields.append(capacityRouteNumberField('Expected duration', 'capacityDuration', 'capacity_duration', inputs.duration, 1, 1440, 'min'));
    }
    if (inputs.mode !== 'queue' && (publishedPlacement.strategies || []).length) {
      const placementField = n('label', 'ops-capacity-field');
      const placementSelect = n('select', 'ops-select ops-capacity-queue-select');
      placementSelect.setAttribute('aria-label', 'Target AMD placement strategy');
      (publishedPlacement.strategies || []).forEach(function (strategy) {
        const option = n('option', '', strategy.label || strategy.id);
        option.value = strategy.id;
        option.selected = strategy.id === selectedPlacementId;
        placementSelect.append(option);
      });
      placementSelect.addEventListener('change', function () {
        setRouteState('ci-hotness', 'capacityPlacement', placementSelect.value, 'capacity_placement');
      });
      add(placementField, [n('span', 'ops-field-label', 'Target placement'), placementSelect]);
      fields.append(placementField);
    }
    if (inputs.trafficMode === 'sustained') {
      fields.append(capacityRouteRateField('Added test suites / hour', 'capacitySuitesPerHour', 'capacity_suites_per_hour', inputs.suitesPerHour, 0, 1000, 'suites/h'));
    } else {
      fields.append(capacityRouteNumberField('Simultaneous suites', 'capacitySuites', 'capacity_suites', inputs.suites, 1, 20, 'suites'));
      const baselineField = n('div', 'ops-capacity-field ops-capacity-baseline');
      add(baselineField, [
        n('span', 'ops-field-label', 'Observed background'),
        segmented([
          {id: 'current', label: 'Current'},
          {id: 'typical', label: '5-day joint p50'},
          {id: 'peak', label: '5-day joint p95'},
          {id: 'stress', label: 'Observed stress'},
        ], inputs.baseline, function (id) {
          setRouteState('ci-hotness', 'capacityBaseline', id, 'capacity_baseline');
        }, 'Coherent whole-cluster background snapshot'),
      ]);
      fields.append(baselineField);
    }
    add(plannerControls, [
      fields,
      n('p', 'ops-capacity-control-note', (inputs.mode === 'queue'
        ? 'Simulates only the newly added YAML-like mirror shape; the displayed total suite becomes today’s topology plus those groups. Queue choice is explicit and compatibility is never inferred. '
        : 'Auto mix interpolates the observed ' + integer(currentTopology.groups)
          + '-group queue topology to the exact ' + integer(targetTopology.groups)
          + '-group target. Largest-remainder allocation preserves the displayed total jobs. ')
        + (inputs.trafficMode === 'sustained'
          ? 'Sustained load adds only the expansion delta to the measured weekday started-cohort rate; it does not add snapshot occupancy again.'
          : 'A one-time burst assumes no future arrivals after the selected test suite is submitted.')),
    ]);
    host.append(panel('Capacity scenario planner', 'Inputs are encoded in the URL for review and sharing', plannerControls, 'ops-capacity-planner'));
    const futurePool = capacity.future_capacity || {};
    host.append(n(
      'div',
      'ops-evidence-note is-info',
      'Configured planning quota: ' + integer(futurePool.gpus || 0)
        + ' GPU slots across active MI250, MI300, and MI355 queues. '
        + 'amd-cpu is Docker-build-only; perf_eval and retiring MI325 queues are excluded. '
        + 'Live connected-agent capacity is reported separately below.'
    ));
    if (selectedPlacement && selectedPlacement.limitation) {
      const placementNote = n('div', 'ops-evidence-note is-info');
      add(placementNote, [
        n('strong', '', (selectedPlacement.label || selectedPlacement.id) + '. '),
        n('span', '', selectedPlacement.limitation),
      ]);
      host.append(placementNote);
    }
    const historySummary = profile.history || {};
    const analysisWindow = historySummary.analysis_window || profile.analysis_window || {};
    const jointBaselines = historySummary.joint_baselines || profile.joint_baselines || {};
    function jointLoadLabel(preset) {
      const row = jointBaselines[preset] || {};
      if (row.available !== true) return 'Unavailable';
      return integer(row.running_gpu_slots) + ' running / ' + integer(row.waiting_gpu_slots) + ' waiting GPUs';
    }
    function jointTimestamp(preset) {
      const row = jointBaselines[preset] || {};
      return row.observed_at ? shortDate(row.observed_at) : 'No complete snapshot';
    }
    if (analysisWindow.start_at || Object.keys(jointBaselines).length) {
      host.append(statusStrip([
        {
          id: 'capacity-window',
          label: 'PEAK WINDOW',
          value: '5 weekdays / 7 days',
          meta: integer(analysisWindow.complete_snapshot_count || 0) + ' complete snapshots · weekends excluded · UTC',
        },
        {
          id: 'capacity-joint-typical',
          label: 'JOINT P50',
          value: jointLoadLabel('typical'),
          meta: jointTimestamp('typical') + ' · one real whole-cluster snapshot',
        },
        {
          id: 'capacity-joint-peak',
          label: 'JOINT P95',
          value: jointLoadLabel('peak'),
          meta: jointTimestamp('peak') + ' · ranked by running + waiting GPU pressure',
          tone: 'is-info',
        },
        {
          id: 'capacity-joint-stress',
          label: 'OBSERVED STRESS MAX',
          value: jointLoadLabel('stress'),
          meta: jointTimestamp('stress') + ' · raw values retained even above configured quota',
          tone: 'is-warning',
        },
      ]));
    }
    if (Number(capacity.declared_current_mirror_groups) !== Number(capacity.observed_current_mirror_groups)) {
      const drift = n('div', 'ops-capacity-drift');
      add(drift, [
        n('strong', '', 'Baseline count needs reconciliation. '),
        n('span', '', 'Planning input says ' + integer(capacity.declared_current_mirror_groups)
          + ' current groups; checked-out test_areas resolves to ' + integer(capacity.observed_current_mirror_groups)
          + '. Simulation uses the observed topology.'),
      ]);
      host.append(drift);
    }
    const quotaIntegrity = profile.quota_integrity || profile.integrity || {};
    if (quotaIntegrity.quota_drift_detected === true || quotaIntegrity.status === 'warning') {
      const queueIntegrity = quotaIntegrity.queue || {};
      const familyIntegrity = quotaIntegrity.family || {};
      const connectedIntegrity = quotaIntegrity.connected_agents || {};
      const queueViolations = queueIntegrity.violations || quotaIntegrity.queue_violations || [];
      const familyViolations = familyIntegrity.violations || quotaIntegrity.family_violations || [];
      function openQuotaIntegrity() {
        const evidence = n('div', 'ops-stack');
        if (queueViolations.length) {
          evidence.append(dataTable([
            {label: 'Queue', sticky: true, render: function (row) { return row.id; }},
            {label: 'Configured jobs', numeric: true, render: function (row) { return integer(row.configured_capacity_jobs); }},
            {label: 'Maximum running', numeric: true, render: function (row) { return integer(row.maximum_running_occupancy_jobs); }},
            {label: 'Waiting then', numeric: true, render: function (row) { return integer(row.waiting_demand_jobs_at_maximum); }},
            {label: 'Maximum excess GPUs', numeric: true, render: function (row) { return integer(row.maximum_excess_running_gpu_slots); }},
            {label: 'Observed at', render: function (row) { return shortDate(row.maximum_observed_at); }},
          ], queueViolations, 'Observed occupancy above configured quota', {name: 'capacity-quota-integrity', minWidth: '850px'}));
        }
        if ((connectedIntegrity.queues || []).length) {
          evidence.append(dataTable([
            {label: 'Queue', sticky: true, render: function (row) { return row.id; }},
            {label: 'Configured jobs', numeric: true, render: function (row) { return integer(row.configured_capacity_jobs); }},
            {label: 'Latest connected', numeric: true, render: function (row) { return row.available ? integer(row.latest_connected_agents) : 'Unavailable'; }},
            {label: 'Delta', numeric: true, render: function (row) { return row.available ? (Number(row.signed_delta_jobs) > 0 ? '+' : '') + integer(row.signed_delta_jobs) : '-'; }},
            {label: 'Direction', render: function (row) { return row.direction; }},
            {label: 'Source / timestamp', render: function (row) { return row.available ? value(row.source) + ' · ' + value(row.metrics_timestamp) : '-'; }},
          ], connectedIntegrity.queues, 'Queue-native connected agents versus planning quota', {name: 'capacity-connected-integrity', minWidth: '900px'}));
        }
        openDetailDrawer({
          id: 'capacity-quota-integrity',
          title: 'Configured planning-quota integrity',
          subtitle: 'Five-weekday occupancy plus queue-native connected-agent evidence',
          description: (quotaIntegrity.semantics || 'Observed running occupancy is compared with configured planning quota. Waiting demand is reported separately.')
            + ' ' + value(connectedIntegrity.semantics),
          fields: [
            {label: 'Affected queues', value: integer(queueIntegrity.affected_queue_count || queueViolations.length)},
            {label: 'Affected hardware families', value: integer(familyIntegrity.affected_family_count || familyViolations.length)},
            {label: 'Connected-agent mismatches', value: integer(connectedIntegrity.mismatch_queue_count || 0)},
            {label: 'Connected-agent data unavailable', value: integer(connectedIntegrity.unavailable_queue_count || 0)},
            {label: 'Observed snapshots', value: integer(quotaIntegrity.observed_snapshot_count || 0)},
            {label: 'Window', value: value(quotaIntegrity.window_start_at || analysisWindow.start_at) + ' → ' + value(quotaIntegrity.window_end_at || analysisWindow.end_at)},
            {label: 'Planning behavior', value: 'Configured quota retained; transient observations do not silently enlarge capacity'},
          ],
          content: evidence,
          sources: [
            {label: 'Inspect raw queue history', url: SOURCE_ASSETS.queueHistory},
            {label: 'Inspect configured capacity inputs', url: SOURCE_ASSETS.trajectory},
          ],
        });
      }
      const warning = n('div', 'ops-evidence-note is-warning');
      add(warning, [
        n('strong', '', 'Configured quota does not reconcile with observed capacity signals. '),
        n('span', '', integer(queueIntegrity.affected_queue_count || queueViolations.length)
          + ' queues exceeded today’s configured job quota in the five-weekday window; '
          + integer(connectedIntegrity.mismatch_queue_count || 0)
          + ' latest queue-native connected-agent counts differ from planning quota. Waiting jobs are demand, not occupancy. The planner keeps the configured quota and does not treat transient connected capacity as guaranteed future hardware. '),
        linkButton('Inspect quota evidence', openQuotaIntegrity),
      ]);
      host.append(warning);
    }

    const unplaced = profile.unplaced_retiring_workload || {};
    const unplacedTotals = unplaced.totals || {};
    const unplacedMain = (unplaced.by_workload || {}).main || {};
    const unplacedOmni = (unplaced.by_workload || {}).omni || {};
    const unplacedWindow = unplaced.window || {};
    const unplacedJobRangeExhaustive = unplacedWindow.job_created_range_exhaustive === true
      ? true
      : unplacedWindow.job_created_range_exhaustive === false
        ? false
        : null;
    const unplacedLookback = Number(unplacedWindow.parent_build_lookback_days);
    const unplacedMappingBoundary = unplacedJobRangeExhaustive === false
      ? 'MI325 mapping counts are UUID-deduplicated observations inside the '
        + (Number.isFinite(unplacedLookback) && unplacedLookback > 0 ? integer(unplacedLookback) + '-day' : 'configured')
        + ' parent-build lookback, not a provably exhaustive population of every job created in the displayed interval. '
        + (unplacedWindow.source_limitation || unplacedWindow.limitation || 'Jobs attached later to older parent builds can be absent.')
      : unplacedJobRangeExhaustive === null
        ? 'MI325 mapping population exhaustiveness is not published; treat these as observed source-window counts.'
        : 'The source marks the displayed MI325 job-created range exhaustive.';
    const retiringCapacity = capacity.retiring_capacity || {};
    const hasUnplacedMi325 = unplaced.available === true || Number(retiringCapacity.gpus || 0) > 0;
    function unplacedNumber(raw, decimals) {
      return raw !== null && raw !== undefined && raw !== '' && Number.isFinite(Number(raw))
        ? Number(raw).toLocaleString(undefined, {maximumFractionDigits: decimals})
        : '-';
    }
    function unplacedOccupancyLabel(preset) {
      const row = ((unplaced.occupancy || {})[preset]) || {};
      if (!row.available) return 'Unavailable';
      return unplacedNumber(row.running_gpu_slots, 1) + ' running / '
        + unplacedNumber(row.waiting_gpu_slots, 1) + ' waiting GPU slots'
        + (row.complete === false ? ' · partial queue coverage' : '');
    }
    function openUnplacedMi325Detail() {
      const rows = unplaced.queues || [];
      const guidance = n('div', 'ops-stack');
      guidance.append(n('div', 'ops-evidence-note ' + (unplacedJobRangeExhaustive === true ? 'is-info' : 'is-warning'), unplacedMappingBoundary));
      guidance.append(n('div', 'ops-evidence-note is-warning',
        'Manual placement only: first confirm a compatible active queue with the test owner. Then choose Specific queue / test in this planner and enter that queue, mirror-group count, jobs per group, and expected duration. The dashboard does not infer an MI250, MI300, or MI355 destination.'));
      if (rows.length) {
        guidance.append(dataTable([
          {label: 'Retiring queue', sticky: true, render: function (row) { return n('span', 'ops-mono', row.id); }},
          {label: (unplacedWindow.days ? integer(unplacedWindow.days) + 'd' : 'Window') + ' observed mappings', numeric: true, render: function (row) { return integer((row.totals || {}).mapped_jobs); }},
          {label: 'GPU-hours', numeric: true, render: function (row) { return unplacedNumber((row.totals || {}).gpu_hours, 1); }},
          {label: 'Current run / wait', numeric: true, render: function (row) {
            const baseline = (row.history || {}).current || {};
            return baseline.available ? value(baseline.running) + ' / ' + value(baseline.waiting) : '-';
          }},
          {label: 'p50 run / wait', numeric: true, render: function (row) {
            const baseline = (row.history || {}).typical || {};
            return baseline.available ? value(baseline.running) + ' / ' + value(baseline.waiting) : '-';
          }},
          {label: 'p95 run / wait', numeric: true, render: function (row) {
            const baseline = (row.history || {}).peak || {};
            return baseline.available ? value(baseline.running) + ' / ' + value(baseline.waiting) : '-';
          }},
          {label: 'Stress run / wait', numeric: true, render: function (row) {
            const baseline = (row.history || {}).stress || {};
            return baseline.available ? value(baseline.running) + ' / ' + value(baseline.waiting) : '-';
          }},
        ], rows, 'Retiring MI325 evidence', {name: 'capacity-unplaced-mi325', minWidth: '820px'}));
      }
      openDetailDrawer({
        id: 'capacity-unplaced-mi325',
        title: 'Unplaced retiring MI325 workload',
        subtitle: (unplacedWindow.days ? integer(unplacedWindow.days) + '-day' : 'Published') + ' evidence · compatibility unknown',
        description: (unplaced.reason || 'MI325 capacity is retiring, but no compatible destination has been selected. It is excluded from this simulation.') + ' ' + unplacedMappingBoundary,
        fields: [
          {label: 'Observed mapped jobs', value: unplacedNumber(unplacedTotals.mapped_jobs, 0)},
          {label: 'Completed GPU-hours', value: unplacedNumber(unplacedTotals.gpu_hours, 1)},
          {label: 'Average completed load', value: unplacedTotals.average_gpus !== null && unplacedTotals.average_gpus !== undefined && Number.isFinite(Number(unplacedTotals.average_gpus)) ? unplacedNumber(unplacedTotals.average_gpus, 1) + ' GPUs' : 'Unavailable'},
          {label: 'vllm-project/vllm', value: unplacedNumber(unplacedMain.mapped_jobs, 0) + ' observed mappings · ' + unplacedNumber(unplacedMain.gpu_hours, 1) + ' GPU-hours'},
          {label: 'vllm-project/vllm-omni', value: unplacedNumber(unplacedOmni.mapped_jobs, 0) + ' observed mappings · ' + unplacedNumber(unplacedOmni.gpu_hours, 1) + ' GPU-hours'},
          {label: 'Job-created range exhaustive', value: unplacedJobRangeExhaustive === true ? 'Yes' : unplacedJobRangeExhaustive === false ? 'No' : 'Not published'},
          {label: 'Parent-build lookback', value: Number.isFinite(unplacedLookback) && unplacedLookback > 0 ? integer(unplacedLookback) + ' days' : 'Not published'},
          {label: 'Current occupancy', value: unplacedOccupancyLabel('current')},
          {label: 'Typical p50 occupancy', value: unplacedOccupancyLabel('typical')},
          {label: 'Peak p95 occupancy', value: unplacedOccupancyLabel('peak')},
          {label: 'Observed stress occupancy', value: unplacedOccupancyLabel('stress')},
          {label: 'Included in wait/headroom', value: 'No'},
          {label: 'Placement rule', value: 'User-confirmed compatible destination only'},
        ],
        sources: [
          {label: 'Open unique-job mapping evidence', url: SOURCE_ASSETS.workloadMapping},
          {label: 'Open queue occupancy history', url: SOURCE_ASSETS.queueHistory},
          {label: 'Open capacity projection inputs', url: SOURCE_ASSETS.trajectory},
        ],
        content: guidance,
      });
    }
    if (hasUnplacedMi325) {
      const warning = n('div', 'ops-capacity-unplaced');
      const warningCopy = n('div', 'ops-capacity-unplaced-copy');
      add(warningCopy, [
        n('strong', '', 'MI325 workload is unplaced—and excluded from this answer.'),
        n('span', '', ' Wait and headroom model only the active MI250, MI300, and MI355 queues. Choose a compatible destination before adding this retiring workload; compatibility is not inferred. Mapping counts are observed source-window evidence, with job-created exhaustiveness shown in the drilldown.'),
      ]);
      const facts = n('div', 'ops-capacity-unplaced-facts');
      [
        {label: (unplacedWindow.days ? integer(unplacedWindow.days) + 'd' : 'Window') + ' observed mappings', value: unplacedNumber(unplacedTotals.mapped_jobs, 0)},
        {label: 'Completed GPU-hours', value: unplacedNumber(unplacedTotals.gpu_hours, 1)},
        {label: 'Average load', value: unplacedTotals.average_gpus !== null && unplacedTotals.average_gpus !== undefined && Number.isFinite(Number(unplacedTotals.average_gpus)) ? unplacedNumber(unplacedTotals.average_gpus, 1) + ' GPUs' : '-'},
        {label: '5-day stress occupancy', value: unplacedOccupancyLabel('stress')},
      ].forEach(function (fact) {
        const item = n('span', 'ops-capacity-unplaced-fact');
        add(item, [n('small', '', fact.label), n('strong', '', fact.value)]);
        facts.append(item);
      });
      add(warning, [
        warningCopy,
        facts,
        linkButton('Inspect and model manually', openUnplacedMi325Detail, 'Inspect retiring MI325 workload and manual placement guidance'),
      ]);
      host.append(warning);
    }

    function openScenarioSummary() {
      openDetailDrawer({
        id: 'capacity-scenario-summary',
        title: 'Selected capacity scenario',
        subtitle: 'Route-shareable planning inputs',
        fields: [
          {label: 'Mode', value: inputs.mode},
          {label: inputs.mode === 'queue' ? 'New groups' : 'Groups per suite', value: integer(result.groups)},
          {label: inputs.mode === 'queue' ? 'New jobs per suite' : 'Jobs per suite', value: integer(result.jobsPerSuite)},
          {label: 'Resulting total suite', value: integer(result.totalGateGroups) + ' groups · ' + integer(result.totalGateJobs) + ' jobs'},
          {label: 'Traffic model', value: result.trafficMode === 'sustained' ? 'Sustained arrivals' : 'One-time burst; no future arrivals'},
          {label: result.trafficMode === 'sustained' ? 'Added suites / hour' : 'Simultaneous suites', value: result.trafficMode === 'sustained' ? result.suitesPerHour : integer(result.suites)},
          {label: 'Placement', value: (result.placementStrategy || {}).label || (result.placementStrategy || {}).id || (inputs.mode === 'queue' ? 'Explicit queue' : 'Published default')},
          {label: 'Burst jobs / GPU slots', value: result.trafficMode === 'burst' ? integer(result.jobs) + ' / ' + integer(result.gpuSlots) : 'Not used by the steady-state model'},
          {label: 'Background baseline', value: result.trafficMode === 'burst' ? result.baseline : 'Five-weekday started-cohort rate proxy'},
          {label: 'Historical / incremental arrival rate', value: result.trafficMode === 'sustained' ? result.historicalArrivalRate.toFixed(2) + ' / ' + result.incrementalArrivalRate.toFixed(2) + ' jobs/h' : 'Not applicable'},
          {label: 'Aggregate pressure', value: result.aggregatePressurePct === null ? 'Unavailable' : result.aggregatePressurePct.toFixed(1) + '%'},
          {label: 'All burst jobs started / completed by', value: result.trafficMode === 'burst' ? duration(result.allStartedBy) + ' / ' + duration(result.allCompletedBy) : 'Not applicable'},
        ],
        sources: [{label: 'Open published planning inputs', url: SOURCE_ASSETS.trajectory}],
      });
    }
    function openWaitSummary() {
      const waitRows = result.activeRows.slice().sort(function (left, right) {
        return Number(right.wait.p95 || 0) - Number(left.wait.p95 || 0);
      });
      openDetailDrawer({
        id: 'capacity-wait-summary',
        title: result.trafficMode === 'sustained' ? 'Projected sustained queue response' : 'Projected one-time FCFS burst wait',
        subtitle: result.trafficMode === 'sustained' ? 'Erlang-C steady-state approximation; not an SLA' : 'No future arrivals after the burst; not an SLA',
        description: capacityVerdict(result),
        content: dataTable([
          {label: 'Queue', sticky: true, render: function (row) { return row.label; }},
          {label: 'Arrival jobs/h', numeric: true, render: function (row) { return result.trafficMode === 'sustained' && row.arrivalRate !== null ? row.arrivalRate.toFixed(2) : '-'; }},
          {label: 'Utilization', numeric: true, render: function (row) { return result.trafficMode === 'sustained' && row.wait.status !== 'unavailable' ? (Number(row.wait.rho || 0) * 100).toFixed(1) + '%' : '-'; }},
          {label: 'p50', numeric: true, render: function (row) { return capacityWaitLabel(row.wait.status, row.wait.p50); }},
          {label: 'p95', numeric: true, render: function (row) { return capacityWaitLabel(row.wait.status, row.wait.p95); }},
          {label: 'All started by', numeric: true, render: function (row) { return result.trafficMode === 'burst' ? capacityWaitLabel(row.wait.status, row.wait.allStartedBy) : '-'; }},
          {label: 'All completed by', numeric: true, render: function (row) { return result.trafficMode === 'burst' ? capacityWaitLabel(row.wait.status, row.wait.allCompletedBy) : '-'; }},
        ], waitRows, 'Queue-level planning results', {name: 'capacity-wait-detail', minWidth: '820px'}),
        sources: [{label: 'Inspect queue history', url: SOURCE_ASSETS.queueHistory}],
      });
    }
    function openHardwareSummary() {
      const sustained = result.trafficMode === 'sustained';
      const fields = sustained
        ? [
          {label: 'Steady-state status', value: capacityWaitLabel(result.waitStatus, result.p95Wait)},
          {label: 'Queue-shaped capacity needed for ρ < 1', value: result.waitStatus === 'unavailable' ? 'Unavailable' : integer(result.stabilityGapGpus) + ' GPUs'},
          {label: 'Modeled offered load', value: result.offeredLoadGpuSlots === null ? 'Unavailable' : result.offeredLoadGpuSlots.toFixed(1) + ' GPU slots'},
          {label: 'Post-MI325 configured pool', value: integer(result.totalCapacityGpus) + ' GPUs'},
          {label: 'One-suite immediate-start queue-shape gap (separate)', value: integer(result.shapeGapGpus) + ' GPUs'},
        ]
        : [
          {label: 'Suite-alone simultaneous-start queue-shape gap', value: integer(result.shapeGapGpus) + ' GPUs'},
          {label: 'Suite-alone simultaneous-start fixed-family gap', value: integer(result.familyGapGpus) + ' GPUs'},
          {label: 'Background + suite zero-wait queue-shape gap', value: result.zeroWaitShapeGapGpus === null ? 'Unavailable' : integer(result.zeroWaitShapeGapGpus) + ' GPUs'},
          {label: 'Background + suite zero-wait fixed-family gap', value: result.zeroWaitFamilyGapGpus === null ? 'Unavailable' : integer(result.zeroWaitFamilyGapGpus) + ' GPUs'},
          {label: 'Post-MI325 configured pool', value: integer(result.totalCapacityGpus) + ' GPUs'},
        ];
      const columns = sustained
        ? [
          {label: 'Family', render: function (row) { return row.family; }},
          {label: 'Steady offered load', numeric: true, render: function (row) { return row.combinedAvailable ? row.combinedDemandGpus.toFixed(1) + ' GPUs' : '-'; }},
          {label: 'Configured capacity', numeric: true, render: function (row) { return integer(row.capacityGpus) + ' GPUs'; }},
          {label: 'Family offered-load gap', numeric: true, render: function (row) { return row.zeroWaitGapGpus === null ? '-' : badge(Math.ceil(row.zeroWaitGapGpus) + ' GPUs', row.zeroWaitGapGpus ? 'is-warning' : 'is-success'); }},
        ]
        : [
          {label: 'Family', render: function (row) { return row.family; }},
          {label: 'Demand', numeric: true, render: function (row) { return integer(row.demandGpus) + ' GPUs'; }},
          {label: 'Capacity', numeric: true, render: function (row) { return integer(row.capacityGpus) + ' GPUs'; }},
          {label: 'Suite-alone start-at-once gap', numeric: true, render: function (row) { return badge(integer(row.gapGpus) + ' GPUs', row.gapGpus ? 'is-warning' : 'is-success'); }},
          {label: 'Background + suite zero-wait gap', numeric: true, render: function (row) { return row.zeroWaitGapGpus === null ? '-' : badge(integer(row.zeroWaitGapGpus) + ' GPUs', row.zeroWaitGapGpus ? 'is-warning' : 'is-success'); }},
        ];
      openDetailDrawer({
        id: 'capacity-hardware-summary',
        title: 'Hardware and queue-shape action',
        subtitle: sustained ? 'Steady offered load and queue-level stability constraints' : 'Simultaneous-start queue shape and fixed-family constraints',
        description: capacityVerdict(result),
        fields: fields,
        content: dataTable(columns, result.familyRows, sustained ? 'Steady offered load by fixed family' : 'Fixed-family capacity', {name: 'capacity-family-detail', minWidth: '620px'}),
        sources: [{label: 'Open configured capacity evidence', url: SOURCE_ASSETS.trajectory}],
      });
    }
    host.append(statusStrip([
      {
        id: 'capacity-selected-scenario',
        label: 'SELECTED SCENARIO',
        value: integer(result.totalGateGroups) + ' total groups',
        meta: (inputs.mode === 'queue' ? '+' + integer(result.groups) + ' groups · ' : '')
          + (result.trafficMode === 'sustained'
            ? result.suitesPerHour + ' added suites/h · ' + result.incrementalArrivalRate.toFixed(1) + ' incremental jobs/h'
            : integer(result.jobs) + ' one-time jobs · ' + integer(result.gpuSlots) + ' GPU slots · '
              + integer(result.suites) + ' suite' + (result.suites === 1 ? '' : 's'))
          + ((result.placementStrategy || {}).id
            ? ' · ' + integer(placementMi355.groups || 0) + ' groups / ' + integer(placementMi355.gpu_slots || 0) + ' GPU slots on MI355'
            : '')
          + (hasUnplacedMi325 ? ' · MI325 excluded/unplaced' : ''),
        onOpen: openScenarioSummary,
      },
      {
        id: 'capacity-bottleneck',
        label: 'BOTTLENECK QUEUE',
        value: bottleneck ? bottleneck.label : 'None',
        meta: bottleneck
          ? (bottleneck.pressurePct === null ? 'pressure unavailable' : bottleneck.pressurePct.toFixed(0) + '% '
            + (result.trafficMode === 'sustained' ? 'steady utilization' : 'combined pressure')) + ' · '
            + (result.trafficMode === 'sustained'
              ? (bottleneck.arrivalRate === null ? '-' : bottleneck.arrivalRate.toFixed(1)) + ' jobs/h'
              : integer(bottleneck.demandJobs) + '/' + integer(bottleneck.capacityJobs) + ' burst/quota')
          : 'No scenario load',
        tone: bottleneck && (bottleneck.wait.status !== 'finite' || Number(bottleneck.pressurePct || 0) >= 100) ? 'is-warning' : 'is-info',
        onOpen: function () { if (bottleneck) openCapacityQueueDetail(bottleneck, result); else openScenarioSummary(); },
      },
      {
        id: 'capacity-projected-wait',
        label: result.trafficMode === 'sustained' ? 'STEADY-STATE P95 WAIT' : 'ONE-TIME P95 START WAIT',
        value: capacityWaitLabel(result.waitStatus, result.p95Wait),
        meta: result.trafficMode === 'sustained'
          ? 'p50 ' + capacityWaitLabel(result.waitStatus, result.p50Wait) + ' · max utilization '
            + (result.maximumRho === null ? '-' : (result.maximumRho * 100).toFixed(1) + '%')
          : 'p50 ' + capacityWaitLabel(result.waitStatus, result.p50Wait) + ' · all started '
            + capacityWaitLabel(result.waitStatus, result.allStartedBy) + ' · completed ' + capacityWaitLabel(result.waitStatus, result.allCompletedBy),
        tone: result.waitStatus === 'finite' && Number(result.p95Wait || 0) < 30 ? 'is-success' : 'is-warning',
        onOpen: openWaitSummary,
      },
      {
        id: 'capacity-hardware-action',
        label: result.trafficMode === 'sustained' ? 'STABLE RUNNER GAP' : 'START-AT-ONCE GAP',
        value: result.trafficMode === 'sustained'
          ? (
            result.waitStatus === 'unavailable'
              ? 'Not estimable'
              : result.stabilityGapGpus
                ? '+' + integer(result.stabilityGapGpus) + ' queue-shaped GPUs'
                : 'Stable at configured quota'
          )
          : result.familyGapGpus
            ? integer(result.familyGapGpus) + ' family GPU slots'
            : result.shapeGapGpus
              ? 'Reallocate ' + integer(result.shapeGapGpus) + ' GPUs'
              : 'Suite fits at once',
        meta: result.trafficMode === 'sustained'
          ? result.historicalArrivalRate.toFixed(1) + ' historical + ' + result.incrementalArrivalRate.toFixed(1)
            + ' incremental jobs/h · no snapshot occupancy double count'
          : 'suite alone: ' + integer(result.shapeGapGpus) + ' queue-shape / ' + integer(result.familyGapGpus)
            + ' family GPU gap · with background: '
            + (result.zeroWaitShapeGapGpus === null ? '-' : integer(result.zeroWaitShapeGapGpus)) + ' shape / '
            + (result.zeroWaitFamilyGapGpus === null ? '-' : integer(result.zeroWaitFamilyGapGpus)) + ' family zero-wait gap',
        tone: result.trafficMode === 'sustained'
          ? (result.stabilityGapGpus || result.waitStatus !== 'finite' ? 'is-warning' : 'is-success')
          : result.familyGapGpus || Number(result.zeroWaitFamilyGapGpus || 0) ? 'is-warning' : result.shapeGapGpus || Number(result.zeroWaitShapeGapGpus || 0) ? 'is-info' : 'is-success',
        onOpen: openHardwareSummary,
      },
    ]));

    const verdict = n('div', 'ops-capacity-verdict ' + (
      result.familyGapGpus
      || result.shapeGapGpus
      || Number(result.zeroWaitFamilyGapGpus || 0)
      || Number(result.zeroWaitShapeGapGpus || 0)
      || result.waitStatus !== 'finite'
        ? 'is-warning'
        : 'is-success'
    ));
    add(verdict, [n('strong', '', 'What happens. '), n('span', '', capacityVerdict(result))]);
    host.append(verdict);

    const visualGrid = n('div', 'ops-grid ops-grid-2 ops-capacity-visuals');
    const demandRows = result.rows.filter(function (row) {
      return result.trafficMode === 'sustained'
        ? row.arrivalRate === null || row.arrivalRate > 0
        : row.demandJobs > 0 || Number(row.baselineRunning || 0) > 0 || Number(row.baselineWaiting || 0) > 0;
    });
    const demandChart = chartPanel(
      result.trafficMode === 'sustained' ? 'Steady offered load vs configured queue capacity' : 'Demand vs configured queue capacity',
      result.trafficMode === 'sustained'
        ? 'Five-weekday started-cohort proxy plus only the selected expansion delta'
        : 'One-time burst plus the ' + result.baseline + ' coherent observed snapshot',
      'capacity-sim-demand'
    );
    visualGrid.append(demandChart.root);
    requestAnimationFrame(function () {
      drawChart('capacity-sim-demand', demandChart.canvas, {
        type: 'bar',
        data: {
          labels: demandRows.map(function (row) { return row.label; }),
          datasets: result.trafficMode === 'sustained'
            ? [
              {label: 'Historical offered load', data: demandRows.map(function (row) {
                return row.historicalArrivalRate === null || !Number.isFinite(Number(row.serviceMinutes))
                  ? null
                  : row.historicalArrivalRate * row.serviceMinutes / 60;
              }), backgroundColor: '#5d8ea8'},
              {label: 'Historical + expansion offered load', data: demandRows.map(function (row) { return row.offeredLoadJobs; }), backgroundColor: '#22b8ad'},
              {label: 'Configured queue slots', data: demandRows.map(function (row) { return row.capacityJobs; }), backgroundColor: '#66717d'},
            ]
            : [
              {label: 'Running + waiting + one-time burst', data: demandRows.map(function (row) { return row.combinedJobs; }), backgroundColor: '#22b8ad'},
              {label: 'Configured queue slots', data: demandRows.map(function (row) { return row.capacityJobs; }), backgroundColor: '#66717d'},
            ],
        },
        options: {scales: {y: {beginAtZero: true, title: {display: true, text: 'Concurrent jobs'}}}},
        evidenceTitle: 'Scenario demand versus queue capacity',
        evidenceAsset: SOURCE_ASSETS.trajectory,
        evidence: demandRows.map(function (row) {
          return {
            id: row.id,
            label: row.label,
            valueSummary: result.trafficMode === 'sustained'
              ? value(row.offeredLoadJobs) + ' offered-load jobs / ' + integer(row.capacityJobs) + ' configured slots'
              : value(row.combinedJobs) + ' combined jobs / ' + integer(row.capacityJobs) + ' configured slots',
            details: {
              baseline_running: row.baselineRunning,
              baseline_waiting: row.baselineWaiting,
              burst_jobs: row.demandJobs,
              historical_arrival_jobs_per_hour: row.historicalArrivalRate,
              incremental_arrival_jobs_per_hour: row.incrementalArrivalRate,
              offered_load_jobs: row.offeredLoadJobs,
              standalone_queue_shape_gap_gpus: row.shapeGapGpus,
              zero_wait_queue_shape_gap_gpus: row.combinedGapGpus,
            },
            onOpen: function () { openCapacityQueueDetail(row, result); },
          };
        }),
      });
    });
    const growthTitle = inputs.mode === 'queue'
      ? 'Wait as this mirror expands'
      : inputs.mode === 'jobs'
        ? 'Wait as command jobs grow'
        : 'Wait as the test suite grows';
    const growthContext = inputs.mode === 'queue'
      ? ((profile.queues.find(function (queue) { return queue.id === selectedQueue; }) || {}).label || selectedQueue) + ' only · manual queue placement'
      : ((result.placementStrategy || {}).label || 'Published target placement');
    const growthChart = chartPanel(
      growthTitle,
      growthContext + (result.trafficMode === 'sustained'
        ? ' · ' + result.suitesPerHour + ' added suites/h over weekday cohort load'
        : ' · ' + result.baseline + ' background · ' + integer(result.suites) + ' simultaneous suite' + (result.suites === 1 ? '' : 's')),
      'capacity-sim-growth'
    );
    visualGrid.append(growthChart.root);
    requestAnimationFrame(function () {
      drawChart('capacity-sim-growth', growthChart.canvas, {
        type: 'line',
        data: {
          labels: curve.map(function (row) { return row.x; }),
          datasets: result.trafficMode === 'sustained'
            ? [
              {label: 'Worst-queue p95 wait', data: curve.map(function (row) { return row.status === 'finite' ? row.p95Wait : null; }), borderColor: '#22b8ad', backgroundColor: '#22b8ad', tension: 0.18, spanGaps: false, yAxisID: 'y'},
              {label: 'Maximum queue utilization', data: curve.map(function (row) { return row.maximumRho === null ? null : row.maximumRho * 100; }), borderColor: '#e3a63a', backgroundColor: '#e3a63a', tension: 0.18, spanGaps: false, yAxisID: 'y1'},
            ]
            : [
              {label: 'Projected p95 start wait', data: curve.map(function (row) { return row.status === 'finite' ? row.p95Wait : null; }), borderColor: '#22b8ad', backgroundColor: '#22b8ad', tension: 0.18, spanGaps: false},
              {label: 'Projected all-started time', data: curve.map(function (row) { return row.status === 'finite' ? row.maxWait : null; }), borderColor: '#e3a63a', backgroundColor: '#e3a63a', tension: 0.18, spanGaps: false},
            ],
        },
        options: {scales: Object.assign(
          {
            x: {title: {display: true, text: curve.length ? curve[0].axisLabel : 'Scenario size'}},
            y: {beginAtZero: true, title: {display: true, text: 'Start wait (minutes)'}},
          },
          result.trafficMode === 'sustained'
            ? {y1: {beginAtZero: true, position: 'right', grid: {drawOnChartArea: false}, title: {display: true, text: 'Queue utilization (%)'}}}
            : {}
        )},
        evidenceTitle: 'Projected wait along workload growth',
        evidenceAsset: SOURCE_ASSETS.trajectory,
        evidence: curve.map(function (row) {
          return {
            id: 'capacity-growth-' + row.mode + '-' + row.x,
            label: integer(row.x) + ' ' + (
              row.mode === 'jobs' ? 'command jobs' : row.mode === 'queue' ? 'new mirror groups' : 'selected groups'
            ) + (row.selected ? ' · selected' : ''),
            valueSummary: row.status === 'finite' ? duration(row.p95Wait) + ' p95 start wait' : capacityWaitLabel(row.status, null),
            details: {status: row.status, selected: row.selected, resulting_total_groups: row.groups, resulting_total_jobs: row.jobs, burst_jobs: row.burstJobs, maximum_wait: row.maxWait, maximum_rho: row.maximumRho, stability_gap_gpus: row.stabilityGapGpus, bottleneck: row.bottleneck, queue_pressure_pct: row.pressurePct},
          };
        }),
      });
    });
    host.append(visualGrid);

    const bottleneckRows = result.activeRows.slice().sort(function (left, right) {
      const rank = {unstable: 3, unavailable: 2, finite: 1};
      return (rank[right.wait.status] || 0) - (rank[left.wait.status] || 0)
        || Number(right.pressurePct || 0) - Number(left.pressurePct || 0);
    });
    const bottleneckColumns = [
      {label: 'Queue', sticky: true, width: '190px', render: function (row) { return linkButton(row.label, function () { openCapacityQueueDetail(row, result); }); }},
      {label: 'Family', width: '90px', render: function (row) { return badge(row.family, 'is-info'); }},
    ];
    if (result.trafficMode === 'sustained') {
      bottleneckColumns.push(
        {label: 'Historical + added jobs/h', numeric: true, width: '180px', render: function (row) {
          return row.historicalArrivalRate === null ? '-' : row.historicalArrivalRate.toFixed(2) + ' + ' + row.incrementalArrivalRate.toFixed(2);
        }},
        {label: 'Offered load / quota', numeric: true, width: '160px', render: function (row) {
          return row.offeredLoadJobs === null ? '-' : row.offeredLoadJobs.toFixed(1) + ' / ' + integer(row.capacityJobs);
        }},
        {label: 'Utilization', numeric: true, width: '110px', render: function (row) {
          return row.wait.status === 'unavailable' ? '-' : (Number(row.wait.rho || 0) * 100).toFixed(1) + '%';
        }},
        {label: 'Projected p95', numeric: true, width: '150px', render: function (row) { return capacityWaitLabel(row.wait.status, row.wait.p95); }},
        {label: 'Stable runner gap', numeric: true, width: '150px', render: function (row) {
          const gap = Number(row.wait.capacityGapJobs || 0) * row.gpusPerJob;
          return badge(integer(gap) + ' GPUs', gap ? 'is-warning' : 'is-success');
        }}
      );
    } else {
      bottleneckColumns.push(
        {label: 'Base run / wait', numeric: true, width: '130px', render: function (row) { return row.baselineRunning === null ? '-' : value(row.baselineRunning) + ' / ' + value(row.baselineWaiting); }},
        {label: 'One-time jobs', numeric: true, width: '110px', render: function (row) { return integer(row.demandJobs); }},
        {label: 'Quota', numeric: true, width: '90px', render: function (row) { return integer(row.capacityJobs); }},
        {label: 'Combined pressure', numeric: true, width: '140px', render: function (row) { return row.pressurePct === null ? '-' : row.pressurePct.toFixed(1) + '%'; }},
        {label: 'Projected p95', numeric: true, width: '130px', render: function (row) { return capacityWaitLabel(row.wait.status, row.wait.p95); }},
        {label: 'Suite-only / background zero-wait gap', numeric: true, width: '220px', render: function (row) {
          const zeroWait = row.combinedGapGpus === null ? '-' : integer(row.combinedGapGpus);
          return badge(integer(row.shapeGapGpus) + ' / ' + zeroWait + ' GPUs', row.shapeGapGpus || Number(row.combinedGapGpus || 0) ? 'is-warning' : 'is-success');
        }}
      );
    }
    host.append(compactTablePanel(
      'Queue bottlenecks',
      integer(bottleneckRows.length) + ' used queue shapes · click any queue for evidence and assumptions',
      bottleneckColumns,
      bottleneckRows,
      {
        id: 'capacity-simulation-queues',
        limit: 12,
        browserTitle: 'Capacity simulation by queue',
        browserSubtitle: result.trafficMode === 'sustained'
          ? 'Erlang-C planning estimate with weekday cohort arrivals and exact queue widths'
          : 'One-time FCFS planning estimate with exact queue widths and coherent observed background presets',
        searchPlaceholder: 'Filter queue or hardware family',
        searchText: function (row) { return [row.id, row.label, row.family, row.wait.status].join(' '); },
        geometry: {name: 'capacity-simulation-queues', minWidth: '1040px'},
        className: 'ops-capacity-bottlenecks',
      }
    ));

    const method = n('div', 'ops-evidence-note is-info ops-capacity-method');
    add(method, [
      n('strong', '', 'Planning model, not an SLA. '),
      n('span', '', result.trafficMode === 'sustained'
        ? 'Steady-state uses Erlang-C per queue with λ = the five-weekday started-cohort proxy + added suites/hour × expansion-delta jobs per suite, and A = λ × service time. Snapshot occupancy is not added to that arrival load. The cohort metric is grouped by job.created_at and later-started status, not exact started_at events. '
          + value(((profile.model || {}).steady_wait_assumptions))
          + ' No compatibility or cross-family migration is inferred.'
        : 'This is one deterministic burst with no future arrivals. Each configured runner is list-scheduled independently; observed running jobs receive one conservative full service interval of residual work, observed waiting jobs stay ahead, and scenario jobs go to the earliest available runner. '
          + value(((profile.assumptions || {}).history))
          + ' Suite-alone simultaneous-start gaps and background-plus-suite zero-wait gaps are separate views; queue-shape and fixed-family gaps are not additive. No compatibility or cross-family migration is inferred.'),
      linkButton('Inspect model inputs', openScenarioSummary, 'Inspect the exact scenario inputs and provenance'),
    ]);
    host.append(method);
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
    const sourceAction = externalLink('Open upstream main history', SOURCE_ASSETS.reliability, 'ops-button');
    add(host, pageHeader('CI Workload Trajectory', 'Historical workload signals and queue-shaped AMD capacity planning for the expanded test suite.', windowData.observedTo, sourceAction));
    const viewToolbar = n('div', 'ops-toolbar');
    add(viewToolbar, [
      segmented([
        {id: 'workload', label: 'Workload history'},
        {id: 'capacity', label: 'Capacity projection'},
      ], state.trajectoryView, function (id) {
        setRouteState('ci-hotness', 'trajectoryView', id, 'trajectory_view');
      }, 'Choose workload history or capacity projection'),
    ]);
    host.append(viewToolbar);
    if (state.trajectoryView === 'capacity') {
      renderCapacityProjection(host, (ops.trajectory || {}).capacity_projection || {});
      return;
    }
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
      {id: 'trajectory-jobs', label: 'TERMINAL OBSERVATIONS', value: integer(totalRuns), meta: integer(uniqueBuilds.size) + ' builds in ' + state.trajectoryWindow, window: state.trajectoryWindow, observed: windowData.observedTo, provenance: 'reliability.group_catalog observations', sources: [{label: 'Open published all-main history', url: SOURCE_ASSETS.reliability}]},
      {id: 'trajectory-groups', label: 'STRICT GROUP VARIANTS', value: integer(rows.length), meta: 'after active filters', onOpen: function () { openMetricDetail({label: 'Filtered strict group variants', value: rows.length, meta: state.trajectoryWindow + ' selected window', provenance: 'reliability.group_catalog IDs', sources: [{label: 'Open published all-main history', url: SOURCE_ASSETS.reliability}]}); }},
      {id: 'trajectory-incidents', label: 'VARIANTS WITH INCIDENTS', value: integer(failing), meta: 'non-zero incident rate', tone: failing ? 'is-warning' : 'is-success', onOpen: function () { openHistoryEvidence('Variants with incidents', rows.filter(function (row) { return hotnessRatePercent(row) > 0; }).map(function (row) { return {id: row.id, label: row.name + ' - ' + row.hardware, timestamp: row.last_seen, valueSummary: hotnessRatePercent(row).toFixed(1) + '%', sources: [{label: 'Open published all-main history', url: SOURCE_ASSETS.reliability}], onOpen: function () { openTrajectoryGroupHistory(row); }}; }), 'Strict all-main group identities in the selected window', SOURCE_ASSETS.reliability); }},
      {id: 'trajectory-slowest', label: 'SLOWEST P90', value: duration(slowest.p90_min), meta: value(slowest.name, 'No duration data'), onOpen: function () { slowest.id ? openTrajectoryGroupHistory(slowest) : openMetricDetail({label: 'Slowest p90', value: '-', meta: 'No duration data in this window', sources: [{label: 'Open published all-main history', url: SOURCE_ASSETS.reliability}]}); }},
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
          evidence: frequencyRows.map(function (row) { return {id: row.id, label: row.name + ' - ' + row.hardware, timestamp: observationTimestamp(row.latest), valueSummary: (row.frequencyChangePct >= 0 ? '+' : '') + row.frequencyChangePct.toFixed(0) + '% execution cadence', details: {latest_distinct_builds: row.cadenceRecentCount, latest_cadence_per_day: row.recentRate.toFixed(2), prior_distinct_builds: row.cadenceBaselineCount, prior_cadence_per_day: row.baselineRate === null ? '-' : row.baselineRate.toFixed(2), queues: row.queues.join(', '), incident_rate: row.incidentRatePct.toFixed(1) + '%'}, sources: [{label: 'Open published all-main history', url: SOURCE_ASSETS.reliability}], onOpen: function () { openTrajectoryAnomalyHistory(row, anomalyData); }}; }),
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
          evidence: durationRows.map(function (row) { return {id: row.id, label: row.name + ' - ' + row.hardware, timestamp: observationTimestamp(row.latest), valueSummary: '+' + row.durationChangePct.toFixed(0) + '% median completion time', details: {recent_median: duration(row.recentMedian), baseline_median: duration(row.baselineMedian), recent_observations: row.recentCount, baseline_observations: row.baselineCount, queues: row.queues.join(', '), incident_rate: row.incidentRatePct.toFixed(1) + '%'}, sources: [{label: 'Open published all-main history', url: SOURCE_ASSETS.reliability}], onOpen: function () { openTrajectoryAnomalyHistory(row, anomalyData); }}; }),
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
        {label: 'Frequency signal', width: '150px', render: function (row) { const signal = trajectoryFrequencySignal(row.frequencyChangePct); return linkedBadge(signal.text, exactPipelineEvidenceUrl(row.latest, 'ci'), function () { openTrajectoryAnomalyHistory(row, anomalyData); }, signal.tone); }},
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
    drawChart('trajectory-groups', cp.canvas, {type: 'bar', data: {labels: top.map(function (row) { return compactChartLabel(row, 54); }), datasets: [{label: 'Observations', data: top.map(function (row) { return row.count; }), backgroundColor: '#22b8ad'}]}, options: {indexAxis: 'y'}, evidenceTitle: 'Strict test-group execution volume', evidence: top.map(function (row) { return {id: row.id, label: row.name + ' - ' + row.hardware, timestamp: row.last_seen, valueSummary: integer(row.count) + ' observations', details: {catalog_id: row.id, hardware: row.hardware, queues: row.queues.join(', '), median: duration(row.p50_min), p90: duration(row.p90_min), incident_rate: hotnessRatePercent(row).toFixed(1) + '%'}, sources: [{label: 'Open published all-main history', url: SOURCE_ASSETS.reliability}], onOpen: function () { openTrajectoryGroupHistory(row); }}; })});
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

  const OMNI_REPOSITORIES = {
    omni: 'vllm-project/vllm-omni',
    main: 'vllm-project/vllm',
  };
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

  function omniMappingBucketLabel(bucket, resolution) {
    const date = new Date(bucket.start);
    if (resolution === 'daily') return date.toISOString().slice(0, 10);
    return date.toISOString().slice(5, 13).replace('T', ' ') + ':00';
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

  function signedInteger(number) {
    if (!Number.isFinite(Number(number))) return '-';
    const value = Number(number);
    return (value > 0 ? '+' : '') + integer(value);
  }

  async function renderOmni(host, ops) {
    const omni = ops.omni || {};
    const current = omni.current || {};
    const currentLedger = current.ledger || {};
    const jobs = omni.current_jobs || {};
    const mapping = omni.mapping_history || {};
    const mappingView = omniMappingWindow(mapping, state.omniMappingRange);
    const mappingBuckets = omniMappingBuckets(mappingView);
    const mappingTotals = mappingView.available
      ? omniMappingTotals(mappingView.rows)
      : {omni: emptyOmniMappingStats(), main: emptyOmniMappingStats()};
    const omniTotal = mappingTotals.omni || {};
    const mainTotal = mappingTotals.main || {};
    const mappingAvailable = mappingView.available;
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
        {label: 'OBSERVED MAPPINGS', value: mappingAvailable ? integer(stats.mapped_jobs) : '-', meta: mappingCountMeta},
        {label: 'STARTED JOBS', value: mappingAvailable ? integer(stats.started_jobs) : '-', meta: mappingAvailable ? percent(stats.started_jobs, stats.mapped_jobs) + ' of mappings' : mappingView.reason},
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
          {label: OMNI_REPOSITORIES.main + ' pipelines', value: ((pipelines.main || []).join(', ')) || 'ci, amd-ci, amd-distributed-inference-ci'},
          {label: 'Mapped job', value: 'Unique Buildkite command-job UUID observed inside the declared parent-build source window with an explicit monitored AMD queue mapping; retries remain distinct jobs.'},
          {label: 'Excluded', value: 'Perf-eval and every non-configured queue.'},
          {label: 'Resolution', value: mappingView.resolution === 'hourly' ? 'Hourly source aggregates' : mappingView.resolution === 'daily' ? 'UTC calendar-day aggregates' : 'Unavailable'},
          {label: 'Retained buckets', value: integer(mappingView.rows.length)},
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
          {label: 'Omni share', value: mappingAvailable ? percent(omniTotal.mapped_jobs, Number(omniTotal.mapped_jobs || 0) + Number(mainTotal.mapped_jobs || 0)) : '-'},
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
        description: integer(omniRetiringMapped) + ' of ' + integer(omniTotal.mapped_jobs) + ' observed source-window Omni mappings targeted MI325. ' + populationBoundaryText,
        fields: [
          {label: 'Omni exposure', value: mappingAvailable ? percent(omniRetiringMapped, omniTotal.mapped_jobs) : '-'},
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
      {id: 'omni-mapped-jobs', label: jobRangeNonExhaustive || jobRangeUnknown ? 'OBSERVED OMNI MAPPINGS' : 'INCOMING OMNI JOBS', value: mappingAvailable ? integer(omniTotal.mapped_jobs) : '-', meta: mappingAvailable ? integer(omniTotal.started_jobs) + ' started · ' + mappingCountMeta : mappingView.reason, tone: jobRangeNonExhaustive || jobRangeUnknown ? 'is-warning' : 'is-info', onOpen: function () { openWorkloadMappingDetail('omni', OMNI_REPOSITORIES.omni); }},
      {id: 'omni-gpu-demand', label: 'GPU-SLOT REQUESTS', value: mappingAvailable ? integer(omniTotal.mapped_gpu_slots) : '-', meta: mappingAvailable ? 'summed job widths · not concurrency · ' + Number(omniTotal.gpu_hours || 0).toLocaleString(undefined, {maximumFractionDigits: 1}) + ' completed GPU-hours' : 'Hourly collection is required for this window', onOpen: function () { openWorkloadMappingDetail('omni', OMNI_REPOSITORIES.omni + ' GPU demand'); }},
      {id: 'omni-mapped-share', label: 'SHARE OF OBSERVED VLLM TRAFFIC', value: mappingAvailable ? percent(omniTotal.mapped_jobs, Number(omniTotal.mapped_jobs || 0) + Number(mainTotal.mapped_jobs || 0)) : '-', meta: mappingAvailable ? integer(omniTotal.mapped_jobs) + ' of ' + integer(Number(omniTotal.mapped_jobs || 0) + Number(mainTotal.mapped_jobs || 0)) + ' source-window mappings' : 'No comparable mapping window', onOpen: openTrafficShareDetail},
      {id: 'omni-retiring-share', label: 'MI325 RETIREMENT EXPOSURE', value: mappingAvailable ? percent(omniRetiringMapped, omniTotal.mapped_jobs) : '-', meta: mappingAvailable ? integer(omniRetiringMapped) + ' Omni · ' + integer(mainRetiringMapped) + ' ' + OMNI_REPOSITORIES.main : 'No selected-window queue evidence', tone: omniRetiringMapped ? 'is-warning' : 'is-success', onOpen: openMi325ExposureDetail},
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
      mappingView.retainedComplete && !jobRangeNonExhaustive && !jobRangeUnknown ? 'is-info' : 'is-warning'
    ) + ' ops-omni-coverage-note');
    add(scopeNote, [
      n('strong', '', coverageHeading),
      n('span', '', (mappingView.reason ? mappingView.reason + ' ' : integer(mappingView.rows.length) + ' retained ' + mappingView.resolution + ' buckets; mapped and started are separate counts. ') + populationBoundaryText),
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
          {label: 'Omni share on this queue', value: percent(row.omni.mapped_jobs, Number(row.omni.mapped_jobs || 0) + Number(row.main.mapped_jobs || 0))},
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
        {label: 'Share', numeric: true, width: '68px', render: function (row) { return percent(row.omni.mapped_jobs, omniTotal.mapped_jobs); }},
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
              details: {repository: OMNI_REPOSITORIES.omni, mapped_jobs: row.omni.mapped_jobs, share: percent(row.omni.mapped_jobs, omniTotal.mapped_jobs)},
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

  function notifyFirstRenderSettled() {
    if (firstRenderSettled) return;
    firstRenderSettled = true;
    window.__opsV2FirstRenderSettled = true;
    window.dispatchEvent(new Event('ops-v2:first-render'));
  }

  async function render(tabId, force) {
    if (!OWNED_TABS.has(tabId)) return false;
    if (migrateLegacyQueueDnsRoute(tabId)) return true;
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
      if (tabId === 'ci-queue' && !force && Date.now() - lastQueueRefreshAt >= QUEUE_AUTO_REFRESH_MS) {
        await invalidateQueueData();
      }
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
      else if (tabId === 'ci-queue') await renderQueue(host, ops);
      else if (tabId === 'ci-hotness') await renderTrajectory(host, ops);
      else if (tabId === 'ci-omni') await renderOmni(host, ops);
      if (host.dataset.renderToken !== token) return false;
      if (tabId === 'ci-queue') lastQueueRefreshAt = Date.now();
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

  async function invalidateQueueData() {
    cache.delete(SOURCE_ASSETS.operationsManifest);
    cache.delete(SOURCE_ASSETS.queueSection);
    cache.delete(SOURCE_ASSETS.queueChartHistory);
    cache.delete(SOURCE_ASSETS.queueChartHistoryFallback);
    cache.delete(SOURCE_ASSETS.queueLifecycle);
    cache.delete(SOURCE_ASSETS.queueLifecycleFallback);
    cache.delete('jsonl:' + SOURCE_ASSETS.queueHistory);
    cache.delete('jsonl:' + SOURCE_ASSETS.queueHistoryFallback);
    operationsManifestPromise = null;
    const manifest = await operationsManifest();
    const descriptor = manifest && manifest.sections && manifest.sections.queue;
    if (descriptor && descriptor.path) cache.delete(resolveOperationSectionPath(descriptor.path));
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

  async function refreshQueueData() {
    if (activeTab() !== 'ci-queue' || document.visibilityState === 'hidden') return;
    await invalidateQueueData();
    if (!await render('ci-queue', true)) throw new Error('Queue refresh did not render successfully');
  }

  async function refreshDnsData() {
    if (activeTab() !== 'ci-analytics' || state.analyticsView !== 'dns' || document.visibilityState === 'hidden') return;
    invalidateDnsData();
    if (!await render('ci-analytics', true)) throw new Error('DNS refresh did not render successfully');
  }

  window.OpsV2 = {
    render: render,
    refreshQueue: refreshQueueData,
    refreshDns: refreshDnsData,
    renderOwnership: renderOwnership,
    state: state,
    openTestGroupHistory: openTestGroupHistory,
    loadSections: function (names) { return loadOperationSections(null, names || []); },
  };
  if (window.__OPS_V2_TEST__) {
    window.OpsV2Test = {
      matrixHealthPolicy: matrixHealthPolicy,
      bestHardwareMatrixContract: bestHardwareMatrixContract,
      matrixHealthCollection: matrixHealthCollection,
      matrixGroupEvidence: matrixGroupEvidence,
      sortRuntimeTargetRows: sortRuntimeTargetRows,
      targetResolutionPresentation: targetResolutionPresentation,
      targetAssessmentText: targetAssessmentText,
      targetNoSignalBreakdown: targetNoSignalBreakdown,
      definitionParityComparisonRows: definitionParityComparisonRows,
      definitionParityMirrorRows: definitionParityMirrorRows,
      definitionParityEvidence: definitionParityEvidence,
      definitionParityFilter: definitionParityFilter,
      definitionParityPresentation: definitionParityPresentation,
      nightlyFailureMovement: nightlyFailureMovement,
      nightlyFailureCount: nightlyFailureCount,
      amdNightlyMovement: amdNightlyMovement,
      amdNightlyPresentation: amdNightlyPresentation,
      capacityLargestRemainder: capacityLargestRemainder,
      capacityPairedAllocation: capacityPairedAllocation,
      capacityPlacementStrategy: capacityPlacementStrategy,
      capacityProfileForPlacement: capacityProfileForPlacement,
      capacityTopologyForGroups: capacityTopologyForGroups,
      capacityGroupsForJobs: capacityGroupsForJobs,
      capacityTopologyForQueue: capacityTopologyForQueue,
      capacityBurstWait: capacityBurstWait,
      capacityErlangC: capacityErlangC,
      capacityScenario: capacityScenario,
      capacityGrowthCurve: capacityGrowthCurve,
      capacityVerdict: capacityVerdict,
      omniMappingWindow: omniMappingWindow,
      omniMappingBuckets: omniMappingBuckets,
      omniMappingTotals: omniMappingTotals,
      omniHistoryPoints: omniHistoryPoints,
      omniWindowPoints: omniWindowPoints,
      omniAgeBand: omniAgeBand,
      omniDailyRows: omniDailyRows,
      trajectoryFrequencySignal: trajectoryFrequencySignal,
      isCanonicalAmdQueue: isCanonicalAmdQueue,
      queueMatchesScope: queueMatchesScope,
      queueLifecycleRows: queueLifecycleRows,
      queueLifecycleTotals: queueLifecycleTotals,
      queueLifecycleHourlyRows: queueLifecycleHourlyRows,
      queueLifecycleCoverage: queueLifecycleCoverage,
      queueLifecycleObservationsAvailable: queueLifecycleObservationsAvailable,
      queueLifecycleDisplayCount: queueLifecycleDisplayCount,
      queueLifecyclePayloadValid: queueLifecyclePayloadValid,
      queueLifecycleCandidateQuality: queueLifecycleCandidateQuality,
      compareQueueLifecycleCandidates: compareQueueLifecycleCandidates,
      queueLifecycleMinutes: queueLifecycleMinutes,
      loadQueueLifecycle: loadQueueLifecycle,
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
      refreshQueueData().catch(function (error) {
        console.error('Queue auto-refresh failed:', error);
      });
      refreshDnsData().catch(function (error) {
        console.error('DNS auto-refresh failed:', error);
      });
    }, QUEUE_AUTO_REFRESH_MS);
    document.addEventListener('visibilitychange', function () {
      if (
        document.visibilityState === 'visible'
        && activeTab() === 'ci-queue'
        && Date.now() - lastQueueRefreshAt >= QUEUE_AUTO_REFRESH_MS
      ) {
        refreshQueueData().catch(function (error) {
          console.error('Queue visibility refresh failed:', error);
        });
      }
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
