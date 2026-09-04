/**
 * Lazy AMD mirror inventory renderer for the CI Health mirrors view.
 */
(function (global) {
  'use strict';

  const SOURCE_ASSET_URL = 'data/vllm/ci/capacity_monitor.json';
  const REQUIRED_HELPERS = [
    'badge', 'button', 'compareText', 'externalLink', 'hardwareDisplayLabel',
    'integer', 'linkButton', 'methodDisclosure', 'n', 'openDetailDrawer',
    'openTableBrowser', 'panel', 'value',
  ];

  function validatedHelpers(helpers) {
    const ui = helpers || {};
    const missing = REQUIRED_HELPERS.filter(function (name) {
      return typeof ui[name] !== 'function';
    });
    if (missing.length) {
      throw new Error('AMD mirror inventory is missing UI helpers: ' + missing.join(', '));
    }
    return ui;
  }

  function amdMirrorInventoryRows(inventory, ui) {
    return (Array.isArray((inventory || {}).groups) ? inventory.groups : [])
      .filter(function (row) { return row && typeof row === 'object'; })
      .slice()
      .sort(function (left, right) {
        return ui.compareText(left.area, right.area)
          || ui.compareText(left.yaml_file, right.yaml_file)
          || Number(left.yaml_index || 0) - Number(right.yaml_index || 0)
          || ui.compareText(left.label, right.label);
      });
  }

  function amdMirrorInventoryState(inventory, ui) {
    const summary = (inventory || {}).summary || {};
    const rawTotal = summary.gated_group_count;
    const parsedTotal = rawTotal === null || rawTotal === undefined || rawTotal === ''
      ? null
      : Number(rawTotal);
    const total = Number.isFinite(parsedTotal) && parsedTotal >= 0 ? Math.floor(parsedTotal) : null;
    const rows = amdMirrorInventoryRows(inventory, ui);
    const retention = (inventory || {}).publication_retention || {};
    const groupIndex = retention.group_index || {};
    const aggregateComplete = retention.aggregate_summaries_complete !== false;
    const detailComplete = aggregateComplete
      && groupIndex.complete_relative_to_source !== false
      && total !== null
      && rows.length === total;
    return {
      total: total,
      rows: rows,
      aggregateComplete: aggregateComplete,
      detailComplete: detailComplete,
      omitted: Math.max(0, total === null ? 0 : total - rows.length),
    };
  }

  function amdMirrorSourceUrl(inventory, row) {
    const source = (inventory || {}).source || {};
    const repository = String(source.github_repo || 'vllm-project/vllm').trim();
    const commit = String(source.commit_sha || '').trim().toLowerCase();
    const path = String((row || {}).yaml_file || '').replace(/^\/+/, '');
    if (!/^[a-z0-9_.-]+\/[a-z0-9_.-]+$/i.test(repository)
      || !/^[0-9a-f]{40}$/.test(commit) || !path) return '';
    return 'https://github.com/' + repository + '/blob/' + commit + '/'
      + path.split('/').map(encodeURIComponent).join('/');
  }

  function amdMirrorAreaRows(inventory, groups, ui) {
    const byFile = new Map();
    (groups || []).forEach(function (group) {
      const yamlFile = String(group.yaml_file || 'unknown');
      if (!byFile.has(yamlFile)) {
        byFile.set(yamlFile, {
          area: group.area || yamlFile.split('/').pop().replace(/\.ya?ml$/i, '').replaceAll('_', ' '),
          yaml_file: yamlFile,
          count: 0,
          optional: 0,
          devices: new Set(),
          queues: new Set(),
        });
      }
      const rollup = byFile.get(yamlFile);
      rollup.count += 1;
      if (group.optional) rollup.optional += 1;
      if (group.device) rollup.devices.add(String(group.device));
      if (group.queue) rollup.queues.add(String(group.queue));
    });
    return Array.from(byFile.values()).map(function (row) {
      return Object.assign({}, row, {
        non_optional: row.count - row.optional,
        devices: Array.from(row.devices).sort(ui.compareText),
        queues: Array.from(row.queues).sort(ui.compareText),
        source_url: amdMirrorSourceUrl(inventory, row),
      });
    }).sort(function (left, right) {
      return Number(right.count) - Number(left.count)
        || ui.compareText(left.area, right.area)
        || ui.compareText(left.yaml_file, right.yaml_file);
    });
  }

  function amdMirrorHardwareRows(groups, ui) {
    const families = new Map();
    (groups || []).forEach(function (group) {
      const device = String(group.device || '').trim().toLowerCase();
      const familyMatch = device.match(/^(mi\d+)/);
      const family = familyMatch ? familyMatch[1] : device || 'unassigned';
      if (!families.has(family)) {
        families.set(family, {id: family, count: 0, devices: new Set()});
      }
      const row = families.get(family);
      row.count += 1;
      if (device) row.devices.add(device);
    });
    return Array.from(families.values()).map(function (row) {
      return {
        id: row.id,
        label: row.id === 'unassigned' ? 'Unassigned' : ui.hardwareDisplayLabel(row.id),
        count: row.count,
        devices: Array.from(row.devices).sort(ui.compareText),
      };
    }).sort(function (left, right) {
      return Number(right.count) - Number(left.count) || ui.compareText(left.label, right.label);
    });
  }

  function mirrorPalette(index) {
    return [
      'var(--ops-chart-1)',
      'var(--ops-chart-2)',
      'var(--ops-chart-3)',
      'var(--ops-chart-4)',
      'var(--ops-chart-5)',
    ][index % 5];
  }

  function mirrorRingGradient(rows) {
    const total = (rows || []).reduce(function (sum, row) { return sum + Number(row.count || 0); }, 0);
    if (!total) return 'conic-gradient(var(--ops-neutral) 0 100%)';
    let cursor = 0;
    const stops = rows.map(function (row, index) {
      const start = cursor;
      cursor += Number(row.count || 0) / total * 100;
      return mirrorPalette(index) + ' ' + start.toFixed(2) + '% ' + cursor.toFixed(2) + '%';
    });
    return 'conic-gradient(' + stops.join(', ') + ')';
  }

  function amdMirrorLabel(row, ui) {
    if ((row || {}).amd_label) return row.amd_label;
    const sourceLabel = String((row || {}).label || (row || {}).key || 'AMD mirror');
    const deviceFamily = String((row || {}).device || '').split('_')[0];
    const hardware = deviceFamily ? ui.hardwareDisplayLabel(deviceFamily) : 'AMD';
    return sourceLabel
      .replace(/:nvidia:/i, ':amd:')
      .replace(/\((?:A|B|H|L)\d[^)]*\)/i, '(' + hardware + ')');
  }

  function openAmdMirrorDetail(row, inventory, ui) {
    const source = (inventory || {}).source || {};
    const sourceUrl = amdMirrorSourceUrl(inventory, row);
    const sources = [
      sourceUrl ? {label: 'Open pinned test-area YAML', url: sourceUrl} : null,
      source.commit_url ? {label: 'Open scanned vLLM commit', url: source.commit_url} : null,
      {label: 'Open published AMD mirror scan', url: SOURCE_ASSET_URL},
    ].filter(Boolean);
    const identity = [row.yaml_file, row.yaml_index, row.key].join('-')
      .toLowerCase().replace(/[^a-z0-9-]+/g, '-');
    ui.openDetailDrawer({
      id: 'amd-mirror-' + identity,
      title: amdMirrorLabel(row, ui),
      subtitle: 'Physical mirror.amd declaration on vLLM main',
      description: 'This source definition counts once in the configured AMD mirror total. Parallel execution changes potential job fan-out, not the declaration count.',
      fields: [
        {label: 'Source test group', value: row.label},
        {label: 'Source step key', value: row.key},
        {label: 'Test area', value: row.area},
        {label: 'YAML definition', value: row.yaml_file},
        {label: 'YAML step index', value: row.yaml_index === undefined ? null : ui.integer(Number(row.yaml_index) + 1)},
        {label: 'AMD device', value: row.device},
        {label: 'Mapped queue', value: row.queue},
        {label: 'Parallelism', value: ui.integer(row.parallelism || 1)},
        {label: 'Optional', value: row.optional ? 'Yes' : 'No'},
        {label: 'Timeout', value: row.timeout_in_minutes ? ui.integer(row.timeout_in_minutes) + ' minutes' : null},
        {label: 'Dependency files', value: row.dependency_file_count === undefined ? null : ui.integer(row.dependency_file_count)},
      ],
      sources: sources,
    });
  }

  function amdMirrorAreaColumns(inventory, ui) {
    return [
      {label: 'Test-area file', sticky: true, width: '310px', render: function (row) { return row.source_url ? ui.externalLink(row.yaml_file.split('/').pop() + ' ↗', row.source_url, 'ops-cell-primary') : ui.n('span', 'ops-cell-primary', row.yaml_file); }},
      {label: 'Test area', width: '230px', render: function (row) { return ui.value(row.area); }},
      {label: 'Mirror groups', numeric: true, width: '130px', render: function (row) { return ui.integer(row.count); }},
      {label: 'Required', numeric: true, width: '110px', render: function (row) { return ui.integer(row.non_optional); }},
      {label: 'Optional', numeric: true, width: '110px', render: function (row) { return ui.integer(row.optional); }},
      {label: 'AMD devices', width: '260px', render: function (row) { return row.devices.map(ui.hardwareDisplayLabel).join(', ') || '-'; }},
    ];
  }

  function openAmdMirrorAreaBrowser(inventory, areas, ui) {
    ui.openTableBrowser({
      id: 'amd-mirror-area-browser',
      title: 'AMD mirrors by test-area file',
      subtitle: 'Physical mirror.amd declarations grouped by their pinned source YAML file',
      rows: areas,
      columns: amdMirrorAreaColumns(inventory, ui),
      searchPlaceholder: 'Filter test area, YAML file, device, or queue',
      searchText: function (row) {
        return [row.area, row.yaml_file, row.devices.join(' '), row.queues.join(' ')].join(' ');
      },
      geometry: {name: 'amd-mirror-areas', minWidth: '1150px'},
    });
  }

  function amdMirrorInventoryColumns(inventory, ui) {
    return [
      {label: 'Source test group', sticky: true, width: '390px', render: function (row) { return ui.linkButton(amdMirrorLabel(row, ui), function () { openAmdMirrorDetail(row, inventory, ui); }); }},
      {label: 'Test area', width: '210px', render: function (row) { return ui.value(row.area); }},
      {label: 'AMD device', width: '130px', render: function (row) { return ui.badge(ui.hardwareDisplayLabel(row.device), 'is-info'); }},
      {label: 'Queue', width: '175px', render: function (row) { return ui.n('span', 'ops-mono', ui.value(row.queue)); }},
      {label: 'Parallelism', numeric: true, width: '115px', render: function (row) { return ui.integer(row.parallelism || 1); }},
      {label: 'Mode', width: '125px', render: function (row) { return ui.badge(row.optional ? 'optional' : 'required', row.optional ? 'is-warning' : 'is-neutral'); }},
      {label: 'Source', width: '145px', render: function (row) { const url = amdMirrorSourceUrl(inventory, row); return url ? ui.externalLink('Pinned YAML ↗', url) : ui.n('span', 'ops-cell-muted', '-'); }},
    ];
  }

  function openAmdMirrorInventoryBrowser(inventory, groups, ui) {
    ui.openTableBrowser({
      id: 'amd-mirror-inventory-browser',
      title: 'AMD mirror inventory',
      subtitle: ui.integer(groups.length) + ' published mirror groups from vLLM main',
      rows: groups,
      columns: amdMirrorInventoryColumns(inventory, ui),
      searchPlaceholder: 'Filter test group, area, YAML file, device, queue, or key',
      searchText: function (row) {
        return [row.amd_label, row.label, row.key, row.area, row.yaml_file, row.device, row.queue].join(' ');
      },
      geometry: {name: 'amd-mirror-inventory', minWidth: '1285px'},
    });
  }

  function mirrorCountHero(inventoryState, hardwareRows, ui) {
    const totalExact = inventoryState.total !== null && inventoryState.aggregateComplete;
    const breakdownComplete = inventoryState.detailComplete;
    const totalDisplay = inventoryState.total === null
      ? '—'
      : (inventoryState.aggregateComplete ? '' : '≥') + ui.integer(inventoryState.total);
    const card = ui.n('section', 'ops-mirror-card ops-mirror-total-card');
    const ring = ui.n('div', 'ops-mirror-count-ring');
    ring.style.background = breakdownComplete
      ? mirrorRingGradient(hardwareRows)
      : 'conic-gradient(var(--ops-neutral) 0 100%)';
    ring.setAttribute('role', 'img');
    ring.setAttribute(
      'aria-label',
      (inventoryState.total === null ? 'Configured AMD mirror group total unavailable' : totalDisplay + ' configured AMD mirror groups')
        + (breakdownComplete && hardwareRows.length
          ? '. ' + hardwareRows.map(function (row) { return row.label + ': ' + ui.integer(row.count); }).join(', ')
          : '. Complete hardware breakdown unavailable')
    );
    ring.append(ui.n('strong', '', totalDisplay));
    ring.append(ui.n('span', '', 'mirror groups'));

    const copy = ui.n('div', 'ops-mirror-hero-copy');
    copy.append(ui.n('div', 'ops-eyebrow', 'AMD GATING CONFIGURATION ON MAIN'));
    copy.append(ui.n('h2', 'ops-mirror-hero-title', 'Configured AMD mirror groups'));
    copy.append(ui.n(
      'p',
      'ops-mirror-hero-meta',
      inventoryState.total === null
        ? 'The configured group total is unavailable in this snapshot.'
        : !totalExact
          ? 'The published aggregate is a lower bound in this snapshot.'
          : !breakdownComplete
            ? 'The total is exact; the hardware breakdown is unavailable because published detail is incomplete.'
            : 'Each group is one mirror.amd declaration on vLLM main.'
    ));
    const legend = ui.n('div', 'ops-mirror-hardware-legend');
    hardwareRows.forEach(function (row, index) {
      const item = ui.n('span', 'ops-mirror-hardware-item');
      const dot = ui.n('span', 'ops-mirror-legend-dot');
      dot.style.background = mirrorPalette(index);
      item.append(dot, ui.n('strong', '', ui.integer(row.count)), ui.n('span', '', row.label));
      legend.append(item);
    });
    if (breakdownComplete && hardwareRows.length) copy.append(legend);
    card.append(ring, copy);
    return card;
  }

  function mirrorConfigurationCard(inventoryState, areas, optional, queueCount, ui) {
    const publishedTotal = inventoryState.rows.length;
    const required = publishedTotal - optional;
    const exact = inventoryState.detailComplete;
    const prefix = exact ? '' : '≥';
    const card = ui.n('section', 'ops-mirror-card ops-mirror-configuration-card');
    card.append(ui.n('div', 'ops-eyebrow', 'CONFIGURATION SHAPE'));
    const headline = ui.n('div', 'ops-mirror-mode-headline');
    headline.append(
      ui.n('strong', '', publishedTotal || inventoryState.total === 0 ? prefix + ui.integer(required) : '—'),
      ui.n('span', '', 'required'),
      ui.n('strong', '', publishedTotal || inventoryState.total === 0 ? prefix + ui.integer(optional) : '—'),
      ui.n('span', '', 'optional')
    );
    card.append(headline);

    const track = ui.n('div', 'ops-mirror-mode-track');
    track.setAttribute('role', 'img');
    track.setAttribute('aria-label', ui.integer(required) + ' required and ' + ui.integer(optional) + ' optional published mirror groups');
    if (publishedTotal) {
      const requiredBar = ui.n('span', 'is-required');
      requiredBar.style.width = required / publishedTotal * 100 + '%';
      const optionalBar = ui.n('span', 'is-optional');
      optionalBar.style.width = optional / publishedTotal * 100 + '%';
      track.append(requiredBar, optionalBar);
    }
    if (exact) card.append(track);

    const facts = ui.n('div', 'ops-mirror-facts');
    [
      {value: publishedTotal || inventoryState.total === 0 ? prefix + ui.integer(areas.length) : '—', label: 'source files'},
      {value: Number.isFinite(queueCount) ? ui.integer(queueCount) : '—', label: 'queues used'},
      {value: publishedTotal || inventoryState.total === 0 ? prefix + ui.integer(amdMirrorHardwareRows(inventoryState.rows, ui).length) : '—', label: 'GPU families'},
    ].forEach(function (fact) {
      const item = ui.n('div', 'ops-mirror-fact');
      item.append(ui.n('strong', '', fact.value), ui.n('span', '', fact.label));
      facts.append(item);
    });
    card.append(facts);
    if (!exact && inventoryState.total !== null) {
      card.append(ui.n('p', 'ops-mirror-lower-bound', 'File, mode, and hardware breakdowns reflect published detail rows.'));
    }
    return card;
  }

  function mirrorAreaChart(inventory, areas, inventoryState, ui) {
    const topAreas = areas.slice(0, 8);
    const maxCount = Math.max.apply(null, topAreas.map(function (row) { return Number(row.count || 0); }).concat([1]));
    const body = ui.n('div', 'ops-mirror-area-chart');
    const legend = ui.n('div', 'ops-mirror-area-legend');
    legend.append(ui.n('span', 'is-required', 'Required'), ui.n('span', 'is-optional', 'Optional'));
    body.append(legend);
    const bars = ui.n('div', 'ops-mirror-area-bars');
    topAreas.forEach(function (row) {
      const control = ui.n(row.source_url ? 'a' : 'div', 'ops-mirror-area-row');
      if (row.source_url) {
        control.href = row.source_url;
        control.target = '_blank';
        control.rel = 'noopener';
      }
      control.setAttribute('aria-label', row.area + ': ' + ui.integer(row.count) + ' mirror groups, ' + ui.integer(row.non_optional) + ' required and ' + ui.integer(row.optional) + ' optional' + (row.source_url ? '. Opens pinned YAML in a new tab.' : ''));
      const label = ui.n('span', 'ops-mirror-area-label');
      label.append(ui.n('strong', '', row.area), ui.n('small', '', row.yaml_file.split('/').pop()));
      const track = ui.n('span', 'ops-mirror-area-track');
      const fill = ui.n('span', 'ops-mirror-area-fill');
      fill.style.width = Number(row.count || 0) / maxCount * 100 + '%';
      const requiredSegment = ui.n('span', 'is-required');
      requiredSegment.style.width = Number(row.non_optional || 0) / Math.max(1, Number(row.count || 0)) * 100 + '%';
      const optionalSegment = ui.n('span', 'is-optional');
      optionalSegment.style.width = Number(row.optional || 0) / Math.max(1, Number(row.count || 0)) * 100 + '%';
      fill.append(requiredSegment, optionalSegment);
      track.append(fill);
      const breakdown = ui.n(
        'span',
        'ops-mirror-area-breakdown',
        ui.integer(row.non_optional) + ' R · ' + ui.integer(row.optional) + ' O'
      );
      breakdown.setAttribute('aria-hidden', 'true');
      control.append(label, track, breakdown, ui.n('strong', 'ops-mirror-area-count', ui.integer(row.count)));
      bars.append(control);
    });
    body.append(bars);
    if (areas.length) {
      const actions = ui.n('div', 'ops-mirror-actions');
      actions.append(ui.button('Browse all ' + ui.integer(areas.length) + ' test-area files', function () {
        openAmdMirrorAreaBrowser(inventory, areas, ui);
      }));
      body.append(actions);
    }
    return ui.panel(
      'Where the mirror groups live',
      (inventoryState.detailComplete ? '' : 'Published detail · ') + 'Top ' + ui.integer(topAreas.length) + ' of ' + ui.integer(areas.length) + ' source files by group count',
      body,
      'ops-mirror-area-panel'
    );
  }

  function mirrorInventoryPreview(inventory, groups, inventoryState, ui) {
    const body = ui.n('div', 'ops-mirror-preview');
    const list = ui.n('div', 'ops-mirror-preview-list');
    groups.slice(0, 6).forEach(function (row) {
      const control = ui.n('button', 'ops-mirror-preview-row');
      control.type = 'button';
      control.addEventListener('click', function () { openAmdMirrorDetail(row, inventory, ui); });
      control.setAttribute('aria-label', 'Inspect ' + amdMirrorLabel(row, ui) + ', ' + ui.hardwareDisplayLabel(row.device) + ', ' + (row.optional ? 'optional' : 'required'));
      const identity = ui.n('span', 'ops-mirror-preview-copy');
      identity.append(
        ui.n('strong', '', amdMirrorLabel(row, ui)),
        ui.n('small', '', ui.value(row.area) + ' · ' + String(row.yaml_file || '').split('/').pop())
      );
      control.append(
        ui.badge(ui.hardwareDisplayLabel(row.device), 'is-info'),
        identity,
        ui.badge(row.optional ? 'optional' : 'required', row.optional ? 'is-warning' : 'is-neutral'),
        ui.n('span', 'ops-mirror-preview-arrow', '→')
      );
      list.append(control);
    });
    if (!groups.length) list.append(ui.n('div', 'ops-empty', 'No published AMD mirror groups are available.'));
    body.append(list);
    if (groups.length) {
      const mirrorCount = inventoryState.total === null ? groups.length : inventoryState.total;
      const actions = ui.n('div', 'ops-mirror-actions');
      actions.append(ui.button(
        inventoryState.detailComplete
          ? 'Browse all ' + ui.integer(mirrorCount) + ' AMD mirrors'
          : 'Browse ' + ui.integer(groups.length) + ' published AMD mirrors',
        function () { openAmdMirrorInventoryBrowser(inventory, groups, ui); },
        true
      ));
      body.append(actions);
    }
    return ui.panel(
      'Configured mirror groups',
      inventoryState.total === null
        ? ui.integer(groups.length) + ' published groups; exact aggregate unavailable'
        : ui.integer(groups.length) + ' of ' + ui.integer(inventoryState.total) + ' physical mirror.amd declarations',
      body,
      'ops-mirror-inventory-panel'
    );
  }

  function renderAmdMirrorSummary(inventory, runtimeGroupCount, helpers, onOpen) {
    const ui = validatedHelpers(helpers);
    const payload = inventory || {};
    const inventoryState = amdMirrorInventoryState(payload, ui);
    const summary = payload.summary || {};
    const rows = inventoryState.rows;
    const optional = rows.filter(function (row) { return Boolean((row || {}).optional); }).length;
    const required = rows.length - optional;
    const files = new Set(rows.map(function (row) { return (row || {}).yaml_file; }).filter(Boolean)).size;
    const queueRaw = summary.queues_with_gated_work;
    const queueParsed = queueRaw === null || queueRaw === undefined || queueRaw === '' ? null : Number(queueRaw);
    const queues = Number.isFinite(queueParsed) && queueParsed >= 0 ? Math.floor(queueParsed) : null;
    const countLabel = inventoryState.total === null
      ? '—'
      : (inventoryState.aggregateComplete ? '' : '≥') + ui.integer(inventoryState.total);
    const detailPrefix = inventoryState.detailComplete ? '' : '≥';
    const root = ui.n('button', 'ops-health-mirror-summary' + (inventoryState.total === null ? ' is-unavailable' : ''));
    root.type = 'button';
    root.addEventListener('click', typeof onOpen === 'function' ? onOpen : function () {});
    root.setAttribute(
      'aria-label',
      (inventoryState.total === null ? 'AMD mirror count unavailable' : countLabel + ' configured AMD mirror groups')
        + '. Open the AMD mirror inventory.'
    );

    const mark = ui.n('span', 'ops-health-mirror-mark');
    mark.setAttribute('aria-hidden', 'true');
    mark.append(
      ui.n('span', 'ops-health-mirror-node is-left'),
      ui.n('span', 'ops-health-mirror-node is-center'),
      ui.n('span', 'ops-health-mirror-node is-right')
    );
    const copy = ui.n('span', 'ops-health-mirror-copy');
    const title = ui.n('span', 'ops-health-mirror-title');
    title.append(
      ui.n('strong', '', countLabel),
      ui.n('span', '', 'configured AMD mirror groups')
    );
    copy.append(
      ui.n('span', 'ops-eyebrow', 'AMD GATING CONFIGURATION ON MAIN'),
      title,
      ui.n(
        'span',
        'ops-health-mirror-meta',
        'Physical mirror.amd declarations · '
          + (runtimeGroupCount === null || runtimeGroupCount === undefined
            ? 'runtime group count unavailable'
            : 'separate from ' + ui.integer(runtimeGroupCount) + ' runtime groups')
      )
    );

    const shape = ui.n('span', 'ops-health-mirror-shape');
    if (inventoryState.detailComplete && rows.length) {
      const track = ui.n('span', 'ops-health-mirror-track');
      const requiredSegment = ui.n('span', 'is-required');
      requiredSegment.style.width = required / rows.length * 100 + '%';
      const optionalSegment = ui.n('span', 'is-optional');
      optionalSegment.style.width = optional / rows.length * 100 + '%';
      track.append(requiredSegment, optionalSegment);
      shape.append(track);
    }
    const facts = [];
    if (rows.length || inventoryState.total === 0) {
      facts.push(
        detailPrefix + ui.integer(required) + ' required',
        detailPrefix + ui.integer(optional) + ' optional',
        detailPrefix + ui.integer(files) + ' files'
      );
    }
    if (queues !== null) facts.push(ui.integer(queues) + ' queues');
    shape.append(ui.n('span', 'ops-health-mirror-facts', facts.length ? facts.join(' · ') : 'Open the inventory for configuration details'));

    root.append(mark, copy, shape, ui.n('span', 'ops-health-mirror-action', 'Open AMD mirrors →'));
    return root;
  }

  function renderAmdMirrorInventory(host, inventory, helpers) {
    const ui = validatedHelpers(helpers);
    const inventoryState = amdMirrorInventoryState(inventory, ui);
    const summary = (inventory || {}).summary || {};
    const source = (inventory || {}).source || {};
    const groups = inventoryState.rows;
    const areas = amdMirrorAreaRows(inventory, groups, ui);
    const hardwareRows = amdMirrorHardwareRows(groups, ui);
    const optional = groups.filter(function (row) { return Boolean(row.optional); }).length;
    const rawQueueCount = summary.queues_with_gated_work;
    const parsedQueueCount = rawQueueCount === null || rawQueueCount === undefined || rawQueueCount === ''
      ? null
      : Number(rawQueueCount);
    const queueCount = Number.isFinite(parsedQueueCount) && parsedQueueCount >= 0
      ? Math.floor(parsedQueueCount)
      : null;
    const hero = ui.n('div', 'ops-mirror-hero');
    hero.append(
      mirrorCountHero(inventoryState, hardwareRows, ui),
      mirrorConfigurationCard(inventoryState, areas, optional, queueCount, ui)
    );
    host.append(hero);

    if (!inventoryState.detailComplete && inventoryState.total !== null) {
      const coverageLead = inventoryState.aggregateComplete
        ? 'The aggregate total remains exact, but '
        : 'The published aggregate is marked incomplete, and ';
      host.append(ui.n(
        'div',
        'ops-evidence-note is-warning',
        coverageLead + 'the published detail index contains '
          + ui.integer(groups.length) + ' of ' + ui.integer(inventoryState.total)
          + ' AMD mirror declarations. File and optionality breakdowns are lower bounds.'
      ));
    }

    if (areas.length) host.append(mirrorAreaChart(inventory, areas, inventoryState, ui));
    host.append(mirrorInventoryPreview(inventory, groups, inventoryState, ui));

    host.append(ui.methodDisclosure('How this live count is built', [
      ui.n('span', '', 'The dashboard pins vLLM main to one commit and scans every .buildkite/test_areas/*.yaml file.'),
      ui.n('span', '', 'One top-level YAML step with a non-empty mirror.amd mapping counts once. Optional mirrors are included; parallelism and shard templates do not multiply the total.'),
      ui.n('span', '', 'This physical configuration count is separate from runtime logical groups and the reviewed parity target plan.'),
      source.commit_url ? ui.externalLink('Open scanned commit ' + String(source.commit_sha || '').slice(0, 12) + ' ↗', source.commit_url) : null,
    ]));
  }

  global.AmdMirrorInventory = Object.freeze({
    render: renderAmdMirrorInventory,
    summaryCard: renderAmdMirrorSummary,
  });
})(window);
