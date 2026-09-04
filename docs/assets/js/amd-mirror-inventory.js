/**
 * Lazy AMD mirror inventory renderer for the CI Health mirrors view.
 */
(function (global) {
  'use strict';

  const SOURCE_ASSET_URL = 'data/vllm/ci/capacity_monitor.json';
  const REQUIRED_HELPERS = [
    'badge', 'compactTablePanel', 'compareText', 'externalLink',
    'hardwareDisplayLabel', 'integer', 'linkButton', 'methodDisclosure',
    'n', 'openDetailDrawer', 'statusStrip', 'value',
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
      title: row.amd_label || row.label || row.key || 'AMD mirror',
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

  function renderAmdMirrorInventory(host, inventory, helpers) {
    const ui = validatedHelpers(helpers);
    const inventoryState = amdMirrorInventoryState(inventory, ui);
    const summary = (inventory || {}).summary || {};
    const source = (inventory || {}).source || {};
    const groups = inventoryState.rows;
    const mirrorCount = inventoryState.total;
    const areas = amdMirrorAreaRows(inventory, groups, ui);
    const optional = groups.filter(function (row) { return Boolean(row.optional); }).length;
    const nonOptional = groups.length - optional;
    const displayedPrefix = inventoryState.detailComplete ? '' : '≥';
    const rawQueueCount = summary.queues_with_gated_work;
    const parsedQueueCount = rawQueueCount === null || rawQueueCount === undefined || rawQueueCount === ''
      ? null
      : Number(rawQueueCount);
    const queueCount = Number.isFinite(parsedQueueCount) && parsedQueueCount >= 0
      ? Math.floor(parsedQueueCount)
      : null;
    const totalDisplay = inventoryState.total === null
      ? 'Unavailable'
      : (inventoryState.aggregateComplete ? '' : '≥') + ui.integer(inventoryState.total);

    host.append(ui.statusStrip([
      {
        id: 'amd-mirror-declarations',
        label: 'AMD MIRROR DECLARATIONS',
        value: totalDisplay,
        meta: 'physical YAML steps; parallelism does not multiply the count',
        tone: inventoryState.total === null || !inventoryState.aggregateComplete ? 'is-warning' : 'is-info',
        static: true,
      },
      {
        id: 'amd-mirror-files',
        label: 'TEST-AREA FILES',
        value: groups.length || inventoryState.total === 0 ? displayedPrefix + ui.integer(areas.length) : 'Unavailable',
        meta: inventoryState.detailComplete ? 'files containing at least one AMD mirror' : 'minimum represented by published detail rows',
        tone: inventoryState.detailComplete ? 'is-neutral' : 'is-warning',
        static: true,
      },
      {
        id: 'amd-mirror-optionality',
        label: 'NON-OPTIONAL / OPTIONAL',
        value: groups.length || inventoryState.total === 0
          ? displayedPrefix + ui.integer(nonOptional) + ' / ' + displayedPrefix + ui.integer(optional)
          : 'Unavailable',
        meta: inventoryState.detailComplete ? 'all configured declarations' : 'minimum represented by published detail rows',
        tone: inventoryState.detailComplete ? 'is-neutral' : 'is-warning',
        static: true,
      },
      {
        id: 'amd-mirror-queues',
        label: 'MONITORED QUEUES USED',
        value: Number.isFinite(queueCount) ? ui.integer(queueCount) : 'Unavailable',
        meta: 'configured capacity queues with at least one mirror',
        tone: Number.isFinite(queueCount) ? 'is-neutral' : 'is-warning',
        static: true,
      },
    ], 'Current AMD mirror inventory'));

    host.append(ui.methodDisclosure('How this live count is built', [
      ui.n('span', '', 'Each canonical dashboard refresh resolves vLLM main to one immutable commit, downloads that snapshot, and parses every .buildkite/test_areas/*.yaml file.'),
      ui.n('span', '', 'One top-level YAML step with a non-empty mirror.amd mapping counts once.'),
      ui.n('span', '', 'Parallelism and shard templates do not multiply this count. Optional mirrors are included.'),
      ui.n('span', '', 'This physical declaration count is separate from runtime logical groups, exact job variants, and the reviewed parity target plan.'),
      source.commit_url ? ui.externalLink('Open scanned commit ' + String(source.commit_sha || '').slice(0, 12) + ' ↗', source.commit_url) : null,
    ]));

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

    if (areas.length) {
      host.append(ui.compactTablePanel(
        'AMD mirrors by test-area file',
        (inventoryState.detailComplete ? '' : 'Published detail only · ')
          + ui.integer(areas.length) + ' files represented',
        [
          {label: 'Test-area file', sticky: true, width: '310px', render: function (row) { return row.source_url ? ui.externalLink(row.yaml_file.split('/').pop() + ' ↗', row.source_url, 'ops-cell-primary') : ui.n('span', 'ops-cell-primary', row.yaml_file); }},
          {label: 'Test area', width: '230px', render: function (row) { return ui.value(row.area); }},
          {label: 'AMD mirrors', numeric: true, width: '120px', render: function (row) { return ui.integer(row.count); }},
          {label: 'Non-optional', numeric: true, width: '130px', render: function (row) { return ui.integer(row.non_optional); }},
          {label: 'Optional', numeric: true, width: '110px', render: function (row) { return ui.integer(row.optional); }},
          {label: 'AMD devices', width: '240px', render: function (row) { return row.devices.map(ui.hardwareDisplayLabel).join(', ') || '-'; }},
        ],
        areas,
        {
          id: 'amd-mirror-area-browser',
          limit: 8,
          alwaysBrowse: true,
          conciseCounts: true,
          buttonLabel: 'Browse all test-area files',
          browserSubtitle: 'Physical mirror.amd declarations grouped by their source YAML file',
          searchPlaceholder: 'Filter test area, YAML file, device, or queue',
          searchText: function (row) { return [row.area, row.yaml_file, row.devices.join(' '), row.queues.join(' ')].join(' '); },
          geometry: {name: 'amd-mirror-areas', minWidth: '1140px'},
        }
      ));
    }

    const mirrorColumns = [
      {label: 'Source test group', sticky: true, width: '390px', render: function (row) { return ui.linkButton(row.amd_label || row.label || row.key || 'AMD mirror', function () { openAmdMirrorDetail(row, inventory, ui); }); }},
      {label: 'Test area', width: '210px', render: function (row) { return ui.value(row.area); }},
      {label: 'AMD device', width: '130px', render: function (row) { return ui.badge(ui.hardwareDisplayLabel(row.device), 'is-info'); }},
      {label: 'Queue', width: '175px', render: function (row) { return ui.n('span', 'ops-mono', ui.value(row.queue)); }},
      {label: 'Parallelism', numeric: true, width: '115px', render: function (row) { return ui.integer(row.parallelism || 1); }},
      {label: 'Optional', width: '120px', render: function (row) { return ui.badge(row.optional ? 'optional' : 'non-optional', row.optional ? 'is-warning' : 'is-neutral'); }},
      {label: 'Source', width: '145px', render: function (row) { const url = amdMirrorSourceUrl(inventory, row); return url ? ui.externalLink('Pinned YAML ↗', url) : ui.n('span', 'ops-cell-muted', '-'); }},
    ];
    host.append(ui.compactTablePanel(
      'AMD mirror inventory',
      inventoryState.total === null
        ? ui.integer(groups.length) + ' published mirror rows; the exact aggregate is unavailable'
        : ui.integer(groups.length) + ' of ' + ui.integer(inventoryState.total) + ' configured AMD mirror declarations from vLLM main',
      mirrorColumns,
      groups,
      {
        id: 'amd-mirror-inventory-browser',
        limit: 10,
        alwaysBrowse: groups.length > 0,
        conciseCounts: true,
        buttonLabel: inventoryState.detailComplete
          ? 'Browse all ' + ui.integer(mirrorCount) + ' AMD mirrors'
          : 'Browse ' + ui.integer(groups.length) + ' published AMD mirrors',
        browserTitle: 'AMD mirror inventory',
        browserSubtitle: inventoryState.total === null
          ? ui.integer(groups.length) + ' published mirror rows'
          : ui.integer(groups.length) + ' of ' + ui.integer(inventoryState.total) + ' configured AMD mirror declarations from vLLM main',
        searchPlaceholder: 'Filter test group, area, YAML file, device, queue, or key',
        searchText: function (row) { return [row.amd_label, row.label, row.key, row.area, row.yaml_file, row.device, row.queue].join(' '); },
        geometry: {name: 'amd-mirror-inventory', minWidth: '1285px'},
      }
    ));
  }

  global.AmdMirrorInventory = Object.freeze({
    render: renderAmdMirrorInventory,
  });
})(window);
