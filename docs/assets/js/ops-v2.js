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
  const state = {
    healthView: 'overview',
    analyticsView: 'trends',
    homeWork: 'issues',
    healthSearch: '',
    queueScope: 'amd',
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

  function badge(label, tone) {
    return n('span', 'ops-badge ' + (tone || toneForState(label)), label || 'unknown');
  }

  function externalLink(label, url, cls) {
    if (!url) return n('span', cls || 'ops-muted', label || '-');
    const a = n('a', cls || '', label || 'Open');
    a.href = url;
    a.target = '_blank';
    a.rel = 'noopener';
    return a;
  }

  function button(label, onClick, active) {
    const b = n('button', 'ops-button' + (active ? ' is-primary' : ''), label);
    b.type = 'button';
    b.addEventListener('click', onClick);
    return b;
  }

  function linkButton(label, onClick, title) {
    const b = n('button', 'ops-link-button', label);
    b.type = 'button';
    if (title) b.title = title;
    b.addEventListener('click', onClick);
    return b;
  }

  let activeOverlay = null;

  function closeOverlay() {
    if (!activeOverlay) return;
    const trigger = activeOverlay.trigger;
    activeOverlay.root.remove();
    activeOverlay = null;
    document.body.classList.remove('ops-overlay-open');
    if (trigger && trigger.focus) trigger.focus();
  }

  function openOverlay(title, subtitle, content, wide) {
    closeOverlay();
    const trigger = document.activeElement;
    const root = n('div', 'ops-overlay');
    const shell = n('section', 'ops-overlay-panel' + (wide ? ' is-wide' : ''));
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
    root.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeOverlay();
      }
    });
    document.body.append(root);
    document.body.classList.add('ops-overlay-open');
    activeOverlay = {root: root, trigger: trigger};
    close.focus();
  }

  function segmented(items, current, onChange) {
    const wrap = n('div', 'ops-segmented');
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
    const strip = n('section', 'ops-status-strip');
    for (const item of items) {
      const cell = n('div', 'ops-status-item ' + (item.tone || ''));
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

  function dataTable(columns, rows, caption) {
    const wrap = n('div', 'ops-table-wrap');
    const scroll = n('div', 'ops-table-scroll');
    const table = n('table', 'ops-table');
    if (caption) table.append(n('caption', 'ops-table-caption', caption));
    const thead = n('thead');
    const hr = n('tr');
    for (const col of columns) {
      const th = n('th', (col.numeric ? 'is-numeric ' : '') + (col.sticky ? 'is-sticky-left' : ''), col.label);
      th.scope = 'col';
      hr.append(th);
    }
    thead.append(hr);
    const tbody = n('tbody');
    for (const row of rows) {
      const tr = n('tr');
      for (const col of columns) {
        const td = n('td', (col.numeric ? 'is-numeric ' : '') + (col.sticky ? 'is-sticky-left ' : '') + (col.className || ''));
        const result = col.render ? col.render(row) : row[col.key];
        td.append(cellContent(result));
        tr.append(td);
      }
      tbody.append(tr);
    }
    add(table, [thead, tbody]);
    scroll.append(table);
    wrap.append(scroll);
    if (!rows.length) wrap.append(n('div', 'ops-empty', 'No matching observations.'));
    return wrap;
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

  function evidenceSummaryItem(label, metric, tone) {
    const item = n('div', 'ops-evidence-stat ' + (tone || ''));
    add(item, [n('div', 'ops-stat-label', label), n('div', 'ops-stat-value', metric)]);
    return item;
  }

  function openMixedOutcomeEvidence(candidate) {
    const observations = evidenceObservations(candidate);
    const content = n('div', 'ops-evidence');
    const notice = n('div', 'ops-evidence-note is-info');
    add(notice, [
      n('strong', '', 'Classification: mixed-outcome candidate. '),
      n('span', '', 'This group has both passing and incident observations in the selected AMD nightly window. The incident rate is not a test-case flake probability.'),
    ]);
    content.append(notice);

    const summary = n('div', 'ops-evidence-summary');
    add(summary, [
      evidenceSummaryItem('OBSERVATIONS', integer(candidate.runs !== undefined ? candidate.runs : observations.length)),
      evidenceSummaryItem('PASSED', integer(candidate.passed), 'is-success'),
      evidenceSummaryItem('HARD INCIDENTS', integer(candidate.failed), Number(candidate.failed) ? 'is-danger' : ''),
      evidenceSummaryItem('SOFT INCIDENTS', integer(candidate.soft_failed), Number(candidate.soft_failed) ? 'is-warning' : ''),
      evidenceSummaryItem('INCIDENT RATE', value(candidate.fail_rate) + '%', Number(candidate.fail_rate) ? 'is-warning' : 'is-success'),
    ]);
    content.append(summary);

    if (!observations.length) {
      content.append(n('div', 'ops-empty', 'The aggregate is available, but this snapshot predates per-run evidence. Regenerate operations_v2.json to populate exact links.'));
      openOverlay(candidate.name || 'Group evidence', 'AMD nightly reliability evidence', content, true);
      return;
    }

    const toolbar = n('div', 'ops-toolbar ops-evidence-toolbar');
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
    add(toolbar, [search, resultFilter]);
    content.append(toolbar);
    const tableHost = n('div', 'ops-evidence-table-host');
    content.append(tableHost);

    function renderEvidenceRows() {
      const query = search.value.trim().toLowerCase();
      const mode = resultFilter.value;
      const filtered = observations.filter(function (row) {
        const incident = isIncidentObservation(row);
        if (mode === 'passing' && observationState(row) !== 'passed') return false;
        if (mode === 'incident' && !incident) return false;
        if (!query) return true;
        return [row.build_number, row.queue, row.raw_name, row.name, observationState(row)]
          .some(function (part) { return String(part || '').toLowerCase().includes(query); });
      });
      clear(tableHost);
      tableHost.append(dataTable([
        {label: 'Build', sticky: true, render: function (row) {
          const label = row.build_number ? '#' + row.build_number : 'Build';
          return externalLink(label, row.build_url || row.url || row.job_url, 'ops-mono');
        }},
        {label: 'Observed', render: function (row) { return shortDate(row.observed_at || row.created_at || row.date); }},
        {label: 'Result', render: function (row) {
          const stateName = observationState(row);
          return badge(stateName === 'soft' ? 'soft fail' : stateName === 'hard' ? 'hard fail' : stateName);
        }},
        {label: 'Queue', render: function (row) { return n('span', 'ops-mono', value(row.queue)); }},
        {label: 'Completion', numeric: true, render: function (row) {
          const minutes = row.duration_mins !== undefined ? row.duration_mins : row.duration_min !== undefined ? row.duration_min : row.dur;
          return duration(minutes);
        }},
        {label: 'Tests', numeric: true, render: function (row) { return integer(row.tests); }},
        {label: 'Retry evidence', render: function (row) {
          const retry = row.retry_evidence || row;
          const retries = Number(retry.retries_count || 0);
          if (retry.retried || retry.retried_in_job_id || retries) return badge(retries ? retries + ' retries' : 'retried', 'is-info');
          return n('span', 'ops-cell-muted', '-');
        }},
        {label: 'Job evidence', render: function (row) { return externalLink('Open log', row.job_url || row.url || row.step_url); }},
      ], filtered, integer(filtered.length) + ' of ' + integer(observations.length) + ' contributing AMD job observations'));
    }

    search.addEventListener('input', renderEvidenceRows);
    resultFilter.addEventListener('change', renderEvidenceRows);
    renderEvidenceRows();
    content.append(n('p', 'ops-evidence-method', 'Method: group outcomes combine Buildkite job state with parsed test-result summaries from collected logs. Retry badges require explicit Buildkite retry metadata; mixed nightly outcomes alone are not labeled as confirmed flakes.'));
    openOverlay(candidate.name || 'Group evidence', 'Exact Buildkite evidence for every contributing observation', content, true);
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
      {label: 'vLLM commit', render: function (row) { return n('span', 'ops-mono', String(row.vllm_commit || '-').slice(0, 7)); }},
      {label: 'Image', render: function (row) { return n('span', 'ops-cell-primary ops-mono', value(row.image)); }},
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
    titleRow.append(n('h2', 'ops-perf-model-title', model.model));
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
    const frame = n('div', 'ops-chart');
    frame.append(canvas);
    return {root: panel(title, subtitle, frame, 'ops-chart-card'), canvas};
  }

  function drawChart(key, canvas, config) {
    if (!window.Chart || !canvas) return;
    if (charts.has(key)) charts.get(key).destroy();
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
    charts.set(key, new window.Chart(canvas, config));
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

  function latestAmd(ops) {
    const pipelines = ((ops.nightly || {}).pipelines || []);
    return pipelines.find(function (p) { return p.pipeline === 'amd-ci'; }) || {builds: []};
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
      mixed_state_flaky_candidates: 'Groups with both green and failing observations',
      omni_waiting: 'Omni jobs waiting on AMD hardware',
    };
    return labels[item.kind] || item.kind.replace(/_/g, ' ');
  }

  async function renderHome(host, ops) {
    const amd = latestAmd(ops);
    const build = (amd.builds || [])[0] || {};
    const trans = build.transitions || {};
    const matrix = (ops.gating || {}).matrix_summary || {};
    const queue = (ops.queue || {}).snapshot || {};
    const omni = ops.omni || {};
    add(host, pageHeader('Command Center', 'One operational read of AMD nightly reliability, gating, capacity, and workload ownership.', ops.generated_at));
    add(host, statusStrip([
      {label: 'AMD NIGHTLY', value: build.number ? '#' + build.number : '-', meta: value(build.state, 'No completed build'), tone: toneForState(build.state)},
      {label: 'HARDWARE CELLS GREEN', value: integer(matrix.passing_cells) + ' / ' + integer(matrix.hardware_cells), meta: percent(matrix.passing_cells, matrix.hardware_cells) + ' of configured cells', tone: Number(matrix.failing_cells) ? 'is-danger' : 'is-success'},
      {label: 'FAILURE LIFECYCLE', value: integer((trans.new || []).length) + ' new', meta: integer((trans.recurring || []).length) + ' recurring - ' + integer((trans.fixed || []).length) + ' fixed', tone: (trans.new || []).length ? 'is-danger' : 'is-success'},
      {label: 'QUEUE SNAPSHOT', value: integer(queue.total_waiting) + ' waiting', meta: integer(queue.total_running) + ' running - ' + age(queue.ts), tone: Number(queue.total_waiting) ? 'is-warning' : 'is-success'},
      {label: 'OMNI ON AMD', value: integer((omni.current || {}).waiting) + ' waiting', meta: value(omni.status, 'unknown') + ' - ' + integer((omni.current || {}).running) + ' running', tone: toneForState(omni.status)},
    ]));

    const grid = n('div', 'ops-grid ops-grid-main-aside ops-home-grid');
    const attentionRows = ops.attention || [];
    grid.append(panel('Needs attention', attentionRows.length + ' active signals', dataTable([
      {label: 'Operational signal', sticky: true, render: function (item) { return attentionLabel(item); }},
      {label: 'Severity', render: function (item) { return badge(item.severity); }},
      {label: 'Count', numeric: true, render: function (item) { return integer(item.count); }},
    ], attentionRows), 'ops-home-primary'));

    const recent = (amd.builds || []).slice(0, 7);
    grid.append(panel('AMD nightly movement', 'Latest seven completed observations', dataTable([
      {label: 'Build', render: function (r) { return externalLink('#' + r.number, r.url, 'ops-mono'); }},
      {label: 'New', numeric: true, render: function (r) { return integer((r.transitions.new || []).length); }},
      {label: 'Recurring', numeric: true, render: function (r) { return integer((r.transitions.recurring || []).length); }},
      {label: 'Fixed', numeric: true, render: function (r) { return integer((r.transitions.fixed || []).length); }},
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
    ], state.homeWork, function (id) { state.homeWork = id; render('projects', true); })]);
    const rows = state.homeWork === 'prs' ? workData.prs : workData.issues;
    const body = dataTable([
      {label: state.homeWork === 'prs' ? 'Pull request' : 'Issue', sticky: true, render: function (r) { return externalLink('#' + r.number + ' ' + r.title, r.html_url, 'ops-cell-primary'); }},
      {label: 'Owner', render: function (r) { return r.author || (r.assignees || []).join(', ') || 'Unassigned'; }},
      {label: 'State', render: function (r) { return badge(r.merged ? 'merged' : r.state); }},
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
    ], state.healthView, function (id) { state.healthView = id; render('ci-health', true); }));
  }

  async function renderHealth(host, ops) {
    const amd = latestAmd(ops);
    const build = (amd.builds || [])[0] || {};
    const trans = build.transitions || {};
    const gating = ops.gating || {};
    const targetSummary = gating.active_target_summary || gating.target_summary || {};
    const targetSignals = targetSummary.by_target_signal || {};
    const matrix = gating.matrix_summary || {};
    add(host, pageHeader('CI Health', 'Current AMD reliability and gating. Every metric names its observation grain.', ops.generated_at));
    healthTabs(host);
    host.append(statusStrip([
      {label: 'BUILD GATE STATE', value: value(build.state), meta: build.number ? 'AMD nightly #' + build.number : 'No build', tone: toneForState(build.state)},
      {label: 'HARDWARE CELLS', value: integer(matrix.passing_cells) + ' green', meta: integer(matrix.failing_cells) + ' failing of ' + integer(matrix.hardware_cells), tone: Number(matrix.failing_cells) ? 'is-danger' : 'is-success'},
      {label: 'ACTIVE TARGET COVERAGE', value: integer(targetSignals.green) + ' ready', meta: integer(targetSignals.red) + ' red of ' + integer(targetSummary.target_group_count), tone: Number(targetSignals.red) ? 'is-warning' : 'is-success'},
      {label: 'NIGHTLY TRANSITION', value: integer((trans.new || []).length) + ' new', meta: integer((trans.recurring || []).length) + ' recurring - ' + integer((trans.fixed || []).length) + ' fixed', tone: (trans.new || []).length ? 'is-danger' : 'is-success'},
    ]));

    if (state.healthView === 'overview') {
      const grid = n('div', 'ops-grid ops-grid-main-aside ops-health-grid');
      const trend = chartPanel('Nightly failure lifecycle', 'Group state versus the immediately preceding AMD nightly', 'health-nightly');
      trend.root.classList.add('ops-health-primary');
      grid.append(trend.root);
      const failures = (build.failed_groups || []).concat(build.soft_failed_groups || []).slice(0, 15);
      grid.append(panel('Latest blockers', (build.failed_groups || []).length + ' hard - ' + (build.soft_failed_groups || []).length + ' soft', dataTable([
        {label: 'Group', render: function (r) { return externalLink(r.display_name || r.name, r.url); }},
        {label: 'State', render: function (r) { return badge(r.state); }},
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
      });
      return;
    }

    if (state.healthView === 'gating') {
      const activeGroups = gating.active_target_groups || gating.target_groups || [];
      const total = Number(targetSummary.target_group_count || activeGroups.length);
      const summary = panel('Target readiness', integer(targetSummary.canonical_group_count || total) + ' canonical + ' + integer(targetSummary.active_outside_canonical_count || 0) + ' currently gated outside review', [
        progress('Ready now', targetSignals.green || 0, total, 'is-success'),
        progress('Failing target signal', targetSignals.red || 0, total, 'is-danger'),
        progress('No assigned signal', targetSignals.gray || 0, total, 'is-neutral'),
      ]);
      host.append(summary);
      const toolbar = n('div', 'ops-toolbar');
      const search = n('input', 'ops-input');
      search.type = 'search'; search.placeholder = 'Filter all target groups'; search.value = state.healthSearch;
      search.addEventListener('input', function () { state.healthSearch = search.value; render('ci-health', true); });
      toolbar.append(search);
      host.append(toolbar);
      const q = state.healthSearch.trim().toLowerCase();
      const groups = activeGroups.filter(function (r) { return !q || JSON.stringify(r).toLowerCase().includes(q); });
      host.append(dataTable([
        {label: '#', numeric: true, render: function (r) { return value(r.id); }},
        {label: 'Target group', sticky: true, key: 'label'},
        {label: 'Area', key: 'area'},
        {label: 'Current target', render: function (r) { return badge(r.target_signal); }},
        {label: 'Readiness', render: function (r) { return badge(r.readiness_signal); }},
        {label: 'Target origin', render: function (r) { return r.target_origin === 'active_outside_canonical' ? badge('active outside review', 'is-info') : 'canonical'; }},
        {label: 'Owner', render: function (r) { return r.owner || '-'; }},
      ], groups, groups.length + ' of ' + total + ' active target groups'));
      return;
    }

    if (state.healthView === 'coverage') {
      let matrixData = {};
      try { matrixData = await fetchJSON('data/vllm/ci/amd_test_matrix.json'); } catch (_) {}
      const arch = matrixData.architectures || [];
      host.append(statusStrip(arch.map(function (a) {
        return {label: a.label + ' DEFINITIONS', value: integer(a.nightly_match_count) + ' / ' + integer(a.group_count), meta: 'nightly matched groups', tone: a.nightly_match_count === a.group_count ? 'is-success' : 'is-warning'};
      })));
      const cols = [{label: 'Group', sticky: true, render: function (r) { return r.title; }}, {label: 'Area', key: 'area'}];
      for (const a of arch) {
        cols.push({label: a.label, render: function (r) {
          const c = (r.cells || {})[a.id] || {};
          if (!c.exists) return n('span', 'ops-cell-muted', '-');
          return c.latest_url ? externalLink(c.latest_state || 'unknown', c.latest_url, 'ops-badge ' + toneForState(c.latest_state)) : badge(c.latest_state || 'unknown');
        }});
      }
      host.append(dataTable(cols, matrixData.rows || [], integer((matrixData.rows || []).length) + ' configured AMD definition rows'));
      return;
    }

    const reliability = ops.reliability || {};
    const retry = reliability.retry_analysis || {};
    const diagnosticGrid = n('div', 'ops-stack ops-reliability-stack');
    diagnosticGrid.append(panel('Mixed-outcome candidates', 'Groups with both passing and incident observations; inspect the run evidence before classifying a flake', dataTable([
      {label: 'Group', sticky: true, render: candidateNameCell}, {label: 'Runs', key: 'runs', numeric: true},
      {label: 'Passed', key: 'passed', numeric: true}, {label: 'Failed / soft', numeric: true, render: function (r) { return integer(Number(r.failed || 0) + Number(r.soft_failed || 0)); }},
      {label: 'Mixed-outcome incident rate', numeric: true, render: function (r) { return value(r.fail_rate) + '%'; }},
      {label: 'Evidence', render: candidateEvidenceCell},
    ], reliability.flaky_candidates || [])));
    diagnosticGrid.append(panel('Retry recoveries', 'Explicit Buildkite retry chains; log confirmation is a separate evidence level', dataTable([
      {label: 'Build', render: function (r) { return '#' + value(r.build_number); }},
      {label: 'Group', key: 'name'},
      {label: 'Failed attempt', render: function (r) { return externalLink('Open', r.failed_url); }},
      {label: 'Passing retry', render: function (r) { return externalLink('Open', r.passed_url); }},
    ], retry.failed_then_passed_recoveries || [])));
    host.append(diagnosticGrid);
  }

  async function renderAnalytics(host, ops) {
    const amd = latestAmd(ops);
    const builds = amd.builds || [];
    const reliability = ops.reliability || {};
    const retry = reliability.retry_analysis || {};
    add(host, pageHeader('CI Analytics', 'AMD nightly change, group reliability, retries, and time to completion.', ops.generated_at));
    host.append(segmented([
      {id: 'trends', label: 'Trends'}, {id: 'builds', label: 'Builds'},
      {id: 'flakes', label: 'Flakes & retries'}, {id: 'latency', label: 'Latency'},
    ], state.analyticsView, function (id) { state.analyticsView = id; render('ci-analytics', true); }));
    const retrySummary = retry.summary || {};
    const slowest = (((reliability.latency_rankings || {}).by_median_duration || [])[0] || {});
    host.append(statusStrip([
      {label: 'NIGHTLIES COMPARED', value: integer(builds.length), meta: 'AMD completed builds'},
      {label: 'MIXED-OUTCOME GROUPS', value: integer((reliability.flaky_candidates || []).length), meta: 'pass plus incident in window', tone: (reliability.flaky_candidates || []).length ? 'is-warning' : 'is-success'},
      {label: 'RETRY RECOVERIES', value: integer(retrySummary.failed_then_passed_recovery_count), meta: integer(retrySummary.retry_attempt_count) + ' retry attempts', tone: Number(retrySummary.failed_then_passed_recovery_count) ? 'is-warning' : 'is-neutral'},
      {label: 'SLOWEST GROUP P50', value: duration(slowest.median_dur), meta: value(slowest.name, 'No duration data')},
    ]));

    if (state.analyticsView === 'trends') {
      const cp = chartPanel('AMD nightly regressions', 'New, recurring, and fixed group observations per completed nightly', 'analytics-trend');
      host.append(cp.root);
      drawChart('analytics-trend', cp.canvas, {type: 'bar', data: {
        labels: builds.slice().reverse().map(function (b) { return '#' + b.number; }),
        datasets: [
          {label: 'New', data: builds.slice().reverse().map(function (b) { return (b.transitions.new || []).length; }), backgroundColor: '#e06464'},
          {label: 'Recurring', data: builds.slice().reverse().map(function (b) { return (b.transitions.recurring || []).length; }), backgroundColor: '#e3a63a'},
          {label: 'Fixed', data: builds.slice().reverse().map(function (b) { return (b.transitions.fixed || []).length; }), backgroundColor: '#35bb78'},
        ],
      }, options: {scales: {x: {stacked: true}, y: {stacked: true, beginAtZero: true}}}});
      return;
    }
    if (state.analyticsView === 'builds') {
      host.append(dataTable([
        {label: 'AMD nightly', sticky: true, render: function (r) { return externalLink('#' + r.number, r.url, 'ops-mono'); }},
        {label: 'State', render: function (r) { return badge(r.state); }},
        {label: 'Observed groups', key: 'total_groups', numeric: true},
        {label: 'Hard fail', numeric: true, render: function (r) { return integer((r.failed_groups || []).length); }},
        {label: 'Soft fail', numeric: true, render: function (r) { return integer((r.soft_failed_groups || []).length); }},
        {label: 'New', numeric: true, render: function (r) { return integer((r.transitions.new || []).length); }},
        {label: 'Recurring', numeric: true, render: function (r) { return integer((r.transitions.recurring || []).length); }},
        {label: 'Fixed', numeric: true, render: function (r) { return integer((r.transitions.fixed || []).length); }},
        {label: 'Started', render: function (r) { return shortDate(r.created_at); }},
      ], builds, 'AMD-only build history; upstream is used only on parity surfaces'));
      return;
    }
    if (state.analyticsView === 'flakes') {
      const grid = n('div', 'ops-stack ops-reliability-stack');
      grid.append(panel('Mixed-outcome candidates', 'A candidate has both passing and incident observations across AMD nightlies; this rate is not a test-case flake probability', dataTable([
        {label: 'Group', sticky: true, render: candidateNameCell}, {label: 'Runs', key: 'runs', numeric: true},
        {label: 'Passed', key: 'passed', numeric: true}, {label: 'Hard', key: 'failed', numeric: true},
        {label: 'Soft', key: 'soft_failed', numeric: true}, {label: 'Mixed-outcome incident rate', numeric: true, render: function (r) { return value(r.fail_rate) + '%'; }},
        {label: 'Evidence', render: candidateEvidenceCell},
      ], reliability.flaky_candidates || [])));
      grid.append(panel('Failed then passed retries', 'Explicit Buildkite job-attempt edges', dataTable([
        {label: 'Build', render: function (r) { return '#' + value(r.build_number); }}, {label: 'Group', key: 'name'},
        {label: 'Failed', render: function (r) { return externalLink('Log', r.failed_url); }},
        {label: 'Recovered', render: function (r) { return externalLink('Log', r.passed_url); }},
      ], retry.failed_then_passed_recoveries || [])));
      host.append(grid);
      return;
    }
    const latencyRows = (reliability.latency_rankings || {}).by_p90_duration || [];
    host.append(dataTable([
      {label: 'Test group', sticky: true, key: 'name'}, {label: 'Runs', key: 'runs', numeric: true},
      {label: 'Median completion', numeric: true, render: function (r) { return duration(r.median_dur); }},
      {label: 'p90 completion', numeric: true, render: function (r) { return duration(r.p90_dur); }},
      {label: 'Maximum', numeric: true, render: function (r) { return duration(r.max_dur); }},
      {label: 'Incident rate', numeric: true, render: function (r) { return value(r.fail_rate) + '%'; }},
      {label: 'Queues', render: function (r) { return (r.queues || []).join(', ') || '-'; }},
    ], latencyRows, 'Completion time per AMD test group; queue wait is not included unless the collector reports it separately'));
  }

  async function renderPerf(host, ops) {
    const perf = await fetchJSON('data/vllm/perf_eval/perf_eval.json');
    const models = Array.isArray(perf.models) ? perf.models : [];
    const summary = perf.summary || {};
    const pipeline = externalLink('Open perf-eval pipeline', (perf.pipeline || {}).url || 'https://buildkite.com/vllm/perf-eval', 'ops-button');
    add(host, pageHeader('Performance & Evaluation', 'Webhook-fed AMD nightly throughput, latency, and accuracy with source-build provenance.', perf.generated_at || ops.generated_at, pipeline));
    host.append(statusStrip([
      {label: 'AMD MODELS', value: integer(summary.models !== undefined ? summary.models : models.length), meta: 'nightly model families'},
      {label: 'NIGHTLIES TRACKED', value: integer(summary.nightlies), meta: 'across retained series'},
      {label: 'PERFORMANCE POINTS', value: integer(summary.perf_points), meta: 'throughput and latency'},
      {label: 'ACCURACY POINTS', value: integer(summary.accuracy_points), meta: 'lm-eval observations'},
      {label: 'AMD HARDWARE', value: (summary.amd_devices || []).map(function (device) { return String(device).toUpperCase(); }).join(' / ') || '-', meta: 'ROCm only'},
    ]));

    const toolbar = n('div', 'ops-toolbar ops-perf-toolbar');
    toolbar.append(segmented([{id: 'performance', label: 'Performance'}, {id: 'accuracy', label: 'Accuracy'}], state.perfView, function (id) {
      state.perfView = id;
      render('ci-perf-eval', true);
    }));
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
      })), state.perfDevice, function (id) { state.perfDevice = id; render('ci-perf-eval', true); }));
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
        {label: 'Model', sticky: true, render: function (row) { return row.model.model; }},
        {label: 'Task', render: function (row) { return row.task.task; }},
        {label: 'Metric', render: function (row) { return n('span', 'ops-mono', row.task.metric); }},
        {label: 'Latest', numeric: true, render: function (row) { return perfValue(row.task.latest, 'ratio'); }},
        {label: 'vs previous', numeric: true, render: function (row) { return perfDelta(row.task); }},
        {label: 'Status', render: function (row) { return badge(row.task.status === 'good' ? 'improved' : row.task.status === 'bad' ? 'regressed' : 'within band', row.task.status === 'good' ? 'is-success' : row.task.status === 'bad' ? 'is-danger' : 'is-neutral'); }},
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

  function selectedQueues(snapshot) {
    return Object.entries(snapshot.queues || {}).filter(function (entry) {
      return state.queueScope === 'all' || entry[0].startsWith('amd_');
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
    return (current[metric] && current[metric].source) || row[metric + '_wait_source'] || row.wait_source || null;
  }

  async function renderQueue(host, ops) {
    const queueBlock = ops.queue || {};
    const snapshot = queueBlock.snapshot || {};
    const entries = selectedQueues(snapshot);
    const sums = entries.reduce(function (a, item) {
      const q = item[1] || {}; a.waiting += Number(q.waiting || 0); a.running += Number(q.running || 0); a.agents += Number(q.connected_agents || 0); return a;
    }, {waiting: 0, running: 0, agents: 0});
    function highest(metric) {
      const vals = entries.map(function (e) { return {queue: e[0], value: waitValue(e[1], metric), source: waitSource(e[1], metric)}; }).filter(function (r) { return Number.isFinite(Number(r.value)); });
      return vals.sort(function (a, b) { return Number(b.value) - Number(a.value); })[0] || {};
    }
    const p50 = highest('p50'), p95 = highest('p95'), p99 = highest('p99');
    add(host, pageHeader('Queue Monitor', 'Current queue-native counts, official p50/p95, and sample-only p99 with explicit provenance.', snapshot.ts));
    host.append(segmented([{id: 'amd', label: 'AMD queues'}, {id: 'all', label: 'All queues'}], state.queueScope, function (id) { state.queueScope = id; render('ci-queue', true); }));
    host.append(statusStrip([
      {label: 'RUNNING NOW', value: integer(sums.running), meta: entries.length + ' queues in scope'},
      {label: 'WAITING NOW', value: integer(sums.waiting), meta: 'queue-native current count', tone: sums.waiting ? 'is-warning' : 'is-success'},
      {label: 'CONNECTED AGENTS', value: integer(sums.agents), meta: 'current queue metrics'},
      {label: 'HIGHEST P50', value: duration(p50.value), meta: p50.queue ? p50.queue + ' - ' + value(p50.source) : 'No reported wait'},
      {label: 'HIGHEST P95', value: duration(p95.value), meta: p95.queue ? p95.queue + ' - ' + value(p95.source) : 'No reported wait'},
      {label: 'HIGHEST P99', value: duration(p99.value), meta: p99.queue ? p99.queue + ' - sampled jobs' : 'Unavailable without job samples'},
    ]));
    host.append(dataTable([
      {label: 'Queue', sticky: true, render: function (r) { return r.url ? externalLink(r.name, r.url, 'ops-mono') : n('span', 'ops-mono', r.name); }},
      {label: 'Running', key: 'running', numeric: true}, {label: 'Waiting', key: 'waiting', numeric: true},
      {label: 'Agents', key: 'agents', numeric: true},
      {label: 'p50', numeric: true, render: function (r) { return duration(r.p50); }},
      {label: 'p95 official', numeric: true, render: function (r) { return duration(r.p95); }},
      {label: 'p99 sampled', numeric: true, render: function (r) { return duration(r.p99); }},
      {label: 'Wait provenance', render: function (r) { return [r.p50s, r.p95s, r.p99s].filter(Boolean).filter(function (x, i, a) { return a.indexOf(x) === i; }).join(', ') || 'No wait sample'; }},
    ], entries.map(function (e) { return {name: e[0], url: e[1].queue_url, running: e[1].running, waiting: e[1].waiting, agents: e[1].connected_agents, p50: waitValue(e[1], 'p50'), p95: waitValue(e[1], 'p95'), p99: waitValue(e[1], 'p99'), p50s: waitSource(e[1], 'p50'), p95s: waitSource(e[1], 'p95'), p99s: waitSource(e[1], 'p99')}; }), entries.length + ' current queues'));

    let history = [];
    try { history = await fetchJSONL('data/vllm/ci/queue_timeseries.jsonl'); } catch (_) {}
    history = history.slice(-336);
    const points = history.map(function (snap) {
      let waiting = 0, running = 0;
      for (const [name, row] of Object.entries(snap.queues || {})) {
        if (state.queueScope === 'all' || name.startsWith('amd_')) { waiting += Number(row.waiting || 0); running += Number(row.running || 0); }
      }
      return {ts: snap.ts, waiting: waiting, running: running};
    });
    const cp = chartPanel('Fleet activity', points.length + ' provenance-bearing snapshots', 'queue-history');
    host.append(cp.root);
    drawChart('queue-history', cp.canvas, {type: 'line', data: {
      labels: points.map(function (p) { return shortDate(p.ts); }),
      datasets: [
        {label: 'Running', data: points.map(function (p) { return p.running; }), borderColor: '#22b8ad', backgroundColor: '#22b8ad', pointRadius: 0, borderWidth: 2},
        {label: 'Waiting', data: points.map(function (p) { return p.waiting; }), borderColor: '#e3a63a', backgroundColor: '#e3a63a', pointRadius: 0, borderWidth: 2},
      ],
    }});

    const jobs = queueBlock.queue_jobs || {};
    const activeJobs = (jobs.pending || []).concat(jobs.running || []).filter(function (j) { return state.queueScope === 'all' || String(j.queue || '').startsWith('amd_'); });
    const workloadCounts = activeJobs.reduce(function (out, job) { const k = job.workload || 'unknown'; out[k] = (out[k] || 0) + 1; return out; }, {});
    host.append(panel('Current jobs', Object.entries(workloadCounts).map(function (x) { return x[0] + ': ' + x[1]; }).join(' - '), dataTable([
      {label: 'Job', sticky: true, render: function (r) { return externalLink(r.name || 'Unnamed job', r.url); }},
      {label: 'Queue', key: 'queue'}, {label: 'Workload', render: function (r) { return badge(r.workload || 'unknown', r.workload === 'omni' ? 'is-info' : 'is-neutral'); }},
      {label: 'State', render: function (r) { return badge(r.state); }},
      {label: 'Age', numeric: true, render: function (r) { return duration(r.wait_min !== undefined ? r.wait_min : r.run_min); }},
      {label: 'Build', render: function (r) { return (r.pipeline || '?') + ' #' + value(r.build); }},
    ], activeJobs.slice(0, 50))));
  }

  async function renderTrajectory(host, ops) {
    let hotness = {};
    try { hotness = await fetchJSON('data/vllm/ci/hotness.json'); } catch (_) {}
    const windows = hotness.windows || {};
    const block = windows[state.trajectoryWindow] || windows['24h'] || {};
    let rows = block.test_groups || [];
    const hardware = Array.from(new Set(rows.map(function (r) { return r.hw || 'unknown'; }))).sort();
    if (state.trajectoryWorkload !== 'all') rows = rows.filter(function (r) { return (r.workload || 'vllm') === state.trajectoryWorkload; });
    if (state.trajectoryHardware !== 'all') rows = rows.filter(function (r) { return (r.hw || 'unknown') === state.trajectoryHardware; });
    const query = state.trajectorySearch.trim().toLowerCase();
    if (query) rows = rows.filter(function (r) { return String(r.group || '').toLowerCase().includes(query); });
    add(host, pageHeader('CI Workload Trajectory', 'AMD demand composition, frequency, latency, and failure pressure by test group.', hotness.generated_at));
    const toolbar = n('div', 'ops-toolbar');
    toolbar.append(segmented(Object.keys(windows).map(function (id) { return {id: id, label: id}; }), state.trajectoryWindow, function (id) { state.trajectoryWindow = id; render('ci-hotness', true); }));
    const workloadSelect = n('select', 'ops-select');
    for (const id of ['all', 'vllm', 'omni']) { const o = n('option', '', id === 'all' ? 'All workloads' : id); o.value = id; o.selected = id === state.trajectoryWorkload; workloadSelect.append(o); }
    workloadSelect.addEventListener('change', function () { state.trajectoryWorkload = workloadSelect.value; render('ci-hotness', true); });
    const hwSelect = n('select', 'ops-select');
    for (const id of ['all'].concat(hardware)) { const o = n('option', '', id === 'all' ? 'All hardware' : id); o.value = id; o.selected = id === state.trajectoryHardware; hwSelect.append(o); }
    hwSelect.addEventListener('change', function () { state.trajectoryHardware = hwSelect.value; render('ci-hotness', true); });
    const search = n('input', 'ops-input'); search.type = 'search'; search.placeholder = 'Filter test groups'; search.value = state.trajectorySearch;
    search.addEventListener('change', function () { state.trajectorySearch = search.value; render('ci-hotness', true); });
    add(toolbar, [workloadSelect, hwSelect, search]); host.append(toolbar);
    const totalRuns = rows.reduce(function (sum, r) { return sum + Number(r.count || 0); }, 0);
    const omniRuns = (block.test_groups || []).filter(function (r) { return r.workload === 'omni'; }).reduce(function (s, r) { return s + Number(r.count || 0); }, 0);
    const slowest = rows.slice().sort(function (a, b) { return Number(b.p90_min || 0) - Number(a.p90_min || 0); })[0] || {};
    const failing = rows.filter(function (r) { return Number(r.fail_rate || 0) > 0; }).length;
    host.append(statusStrip([
      {label: 'JOBS IN WINDOW', value: integer(block.jobs_in_window !== undefined ? block.jobs_in_window : totalRuns), meta: state.trajectoryWindow + ' AMD observations'},
      {label: 'TEST GROUPS', value: integer(rows.length), meta: 'after active filters'},
      {label: 'OMNI SHARE', value: percent(omniRuns, (block.test_groups || []).reduce(function (s, r) { return s + Number(r.count || 0); }, 0)), meta: integer(omniRuns) + ' executions'},
      {label: 'GROUPS WITH FAILURES', value: integer(failing), meta: 'non-zero incident rate', tone: failing ? 'is-warning' : 'is-success'},
      {label: 'SLOWEST P90', value: duration(slowest.p90_min), meta: value(slowest.group, 'No group data')},
    ]));
    const top = rows.slice().sort(function (a, b) { return Number(b.count || 0) - Number(a.count || 0); }).slice(0, 15);
    const cp = chartPanel('Most active groups', 'Execution count in the selected AMD window', 'trajectory-groups');
    host.append(cp.root);
    drawChart('trajectory-groups', cp.canvas, {type: 'bar', data: {labels: top.map(function (r) { return r.group; }), datasets: [{label: 'Executions', data: top.map(function (r) { return r.count; }), backgroundColor: '#22b8ad'}]}, options: {indexAxis: 'y'}});
    host.append(dataTable([
      {label: 'Test group', sticky: true, key: 'group'}, {label: 'Hardware', key: 'hw'},
      {label: 'Workload', render: function (r) { return badge(r.workload || 'vllm', r.workload === 'omni' ? 'is-info' : 'is-neutral'); }},
      {label: 'Runs', key: 'count', numeric: true}, {label: 'Median', numeric: true, render: function (r) { return duration(r.p50_min); }},
      {label: 'p90', numeric: true, render: function (r) { return duration(r.p90_min); }},
      {label: 'Maximum', numeric: true, render: function (r) { return duration(r.max_min); }},
      {label: 'Failure rate', numeric: true, render: function (r) { return Number.isFinite(Number(r.fail_rate)) ? Number(r.fail_rate).toFixed(1) + '%' : '-'; }},
      {label: 'Last seen', render: function (r) { return shortDate(r.last_seen); }},
    ], rows.slice(0, 150), rows.length + ' AMD test groups'));
  }

  async function renderOmni(host, ops) {
    const omni = ops.omni || {};
    const current = omni.current || {};
    const heuristic = omni.heuristic_thresholds || {};
    const jobs = omni.current_jobs || {};
    add(host, pageHeader('Omni', 'vLLM-Omni demand and resource consumption on AMD queues.', (omni.provenance || {}).queue_snapshot_ts));
    const affected = new Set(Object.keys(current.waiting_by_queue || {}).concat(Object.keys(current.running_by_queue || {})));
    const pending = jobs.pending || [], running = jobs.running || [];
    const oldest = pending.slice().sort(function (a, b) { return Number(b.wait_min || 0) - Number(a.wait_min || 0); })[0] || {};
    host.append(statusStrip([
      {label: 'SURGE STATE', value: value(omni.status), meta: 'trigger at ' + integer(heuristic.trigger) + ' waiting', tone: toneForState(omni.status)},
      {label: 'WAITING ON AMD', value: integer(current.waiting), meta: 'healthy at or below ' + integer(heuristic.healthy), tone: Number(current.waiting) > Number(heuristic.healthy || 0) ? 'is-warning' : 'is-success'},
      {label: 'RUNNING ON AMD', value: integer(current.running), meta: 'current Omni jobs'},
      {label: 'AFFECTED QUEUES', value: integer(affected.size), meta: 'AMD pools with Omni demand'},
      {label: 'OLDEST WAIT', value: duration(oldest.wait_min), meta: value(oldest.queue, 'No queued Omni job')},
    ]));
    const queueRows = Array.from(affected).sort().map(function (name) { return {name: name, waiting: (current.waiting_by_queue || {})[name] || 0, running: (current.running_by_queue || {})[name] || 0}; });
    const grid = n('div', 'ops-grid ops-grid-main-aside ops-omni-grid');
    let history = [];
    try { history = await fetchJSONL('data/vllm/ci/queue_timeseries.jsonl'); } catch (_) {}
    const points = history.slice(-336).map(function (snap) {
      let w = 0, r = 0;
      for (const [name, q] of Object.entries(snap.queues || {})) {
        if (!name.startsWith('amd_')) continue;
        w += Number(((q.waiting_by_workload || {}).omni) || 0);
        r += Number(((q.running_by_workload || {}).omni) || 0);
      }
      return {ts: snap.ts, waiting: w, running: r};
    });
    const cp = chartPanel('Omni demand history', points.length + ' AMD queue snapshots', 'omni-history');
    cp.root.classList.add('ops-omni-detail');
    grid.append(cp.root);
    grid.append(panel('Current queue distribution', queueRows.length + ' affected queues', dataTable([
      {label: 'AMD queue', key: 'name'}, {label: 'Waiting', key: 'waiting', numeric: true}, {label: 'Running', key: 'running', numeric: true},
    ], queueRows), 'ops-omni-summary'));
    host.append(grid);
    drawChart('omni-history', cp.canvas, {type: 'line', data: {labels: points.map(function (p) { return shortDate(p.ts); }), datasets: [
      {label: 'Running', data: points.map(function (p) { return p.running; }), borderColor: '#cf8dd9', backgroundColor: '#cf8dd9', pointRadius: 0, borderWidth: 2},
      {label: 'Waiting', data: points.map(function (p) { return p.waiting; }), borderColor: '#e3a63a', backgroundColor: '#e3a63a', pointRadius: 0, borderWidth: 2},
    ]}});
    host.append(panel('Current Omni jobs', integer(pending.length) + ' pending - ' + integer(running.length) + ' running', dataTable([
      {label: 'Job', sticky: true, render: function (r) { return externalLink(r.name || 'Unnamed job', r.url); }},
      {label: 'Queue', key: 'queue'}, {label: 'State', render: function (r) { return badge(r.state); }},
      {label: 'Age', numeric: true, render: function (r) { return duration(r.wait_min !== undefined ? r.wait_min : r.run_min); }},
      {label: 'Build', render: function (r) { return (r.pipeline || '?') + ' #' + value(r.build); }},
      {label: 'Source', render: function (r) { return r.source || r.workload || 'omni'; }},
    ], pending.concat(running).slice(0, 80))));
  }

  async function render(tabId, force) {
    if (!OWNED_TABS.has(tabId)) return;
    const host = ownedHost(tabId);
    if (!host) return;
    const token = String(Date.now()) + Math.random();
    host.dataset.renderToken = token;
    clear(host);
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

  window.OpsV2 = {render: render, state: state};

  function activeTab() {
    const panelEl = document.querySelector('.tab-panel.active');
    return panelEl && panelEl.id ? panelEl.id.replace(/^tab-/, '') : 'projects';
  }

  document.addEventListener('DOMContentLoaded', function () {
    render(activeTab());
  });
})();
