/**
 * CI Health Dashboard v3 — Full visual redesign.
 * Project selector, 4-card metrics, 3-col hardware, heatmap, collapsible groups.
 */
(function () {
  const CI = 'data/vllm/ci', VD = 'data/vllm';
  const _s=getComputedStyle(document.documentElement);
  const C = { g:_s.getPropertyValue('--accent-green').trim()||'#238636',y:_s.getPropertyValue('--accent-orange').trim()||'#d29922',o:'#db6d28',r:_s.getPropertyValue('--badge-closed').trim()||'#da3633',b:_s.getPropertyValue('--accent-blue').trim()||'#1f6feb',p:_s.getPropertyValue('--accent-purple').trim()||'#8957e5',m:_s.getPropertyValue('--text-muted').trim()||'#8b949e',t:_s.getPropertyValue('--text').trim()||'#e6edf3',bg:_s.getPropertyValue('--card-bg').trim()||'#161b22',bg2:_s.getPropertyValue('--bg').trim()||'#0d1117',bd:_s.getPropertyValue('--border').trim()||'#30363d' };
  const LC = { passing:C.g,failing:C.r,new_failure:'#f85149',fixed:'#3fb950',flaky:C.y,skipped:C.m,new_test:C.b,quarantined:C.p };
  const AREAS = ['kernels','entrypoints','distributed','compile','engine','lora','multi-modal','multimodal','quantiz','language models','basic correctness','benchmark','regression','examples','v1','lm eval','gpqa','ray','nixl','weight loading','fusion','batch invariance','model executor','attention benchmark','spec decode','transformers','plugin','sampler','python-only','pytorch','model runner'];

  const _jsonCache = new Map();
  const _sourceAliasesCache = new Map();
  const _upstreamNightlyBuildsCache = new WeakMap();
  const _internalBuildsCache = new WeakMap();
  const _upstreamAmdIndexCache = new WeakMap();
  const _upstreamCudaIndexCache = new WeakMap();
  const _internalAmdIndexCache = new WeakMap();
  const _targetAliasSetCache = new WeakMap();
  const J = async (u, opts={}) => {
    if (!opts.forceRefresh && _jsonCache.has(u)) return _jsonCache.get(u);
    const p = (async () => {
      try {
        const fetchOpts = opts.forceRefresh ? {cache:'no-cache'} : {};
        const r = await fetch(u, fetchOpts);
        return r.ok ? r.json() : null;
      } catch {
        return null;
      }
    })();
    if (opts.forceRefresh) {
      const data = await p;
      if (data != null) _jsonCache.set(u, Promise.resolve(data));
      return data;
    }
    _jsonCache.set(u, p);
    return p;
  };
  const pct = (v,d=2) => (v*100).toFixed(d)+'%';
  const rc = r => r>=.95?C.g:r>=.85?C.y:r>=.7?C.o:C.r;
  const PROPOSAL_COLOR = C.o;

  const h = el;  // shared element factory defined in utils.js

  function area(name) {
    const l=(name||'').toLowerCase();
    for(const a of AREAS) if(l.startsWith(a)||l.includes(a)) return a.replace(/\s+/g,'-');
    return 'other'
  }

  function bar(rate,w='120px') {
    return h('div',{style:{display:'inline-flex',alignItems:'center',gap:'6px'}},[
      h('div',{style:{width:w,height:'6px',background:C.bd,borderRadius:'3px',overflow:'hidden'}},[
        h('div',{style:{width:Math.round(rate*100)+'%',height:'100%',background:rc(rate),borderRadius:'3px'}})
      ]),
      h('span',{text:pct(rate,0),style:{fontSize:'12px',color:rc(rate),fontWeight:'600',minWidth:'36px'}})
    ])
  }

  function dots(hist) {
    const s=h('span',{style:{display:'inline-flex',gap:'2px',alignItems:'center'}});
    for(const x of hist)s.append(h('span',{style:{width:'7px',height:'7px',borderRadius:'50%',background:x==='P'?C.g:x==='F'?C.r:C.m,display:'inline-block'}}));
    return s
  }

  // Project selector removed — handled by sidebar navigation

  // ═══════════════════════ AMD GATING EXECUTIVE VIEW ═══════════════════════
  function fmtInt(n) {
    return Math.round(Number(n || 0)).toLocaleString();
  }

  function normalizeGatingTitle(value) {
    return String(value || '')
      .toLowerCase()
      .replace(/^mi\d+b?_\d+:\s*/, '')
      .replace(/%n/g, '')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function gatingTitleAliases(value) {
    const base = normalizeGatingTitle(value);
    const aliases = new Set([base]);
    aliases.add(base.replace(/\s*\((?:[^)]*(?:h100|h200|b200|a100|mi250|mi300|mi325|mi355)[^)]*)\)\s*$/i, '').trim());
    aliases.add(base.replace(/\s+test(s)?$/i, '').trim());
    return [...aliases].filter(Boolean);
  }

  function matrixRowsByTitle(matrix) {
    const byTitle = {};
    for (const row of matrix?.rows || []) {
      for (const alias of gatingTitleAliases(row.title)) {
        if (!byTitle[alias]) byTitle[alias] = row;
      }
    }
    return byTitle;
  }

  function archFromDevice(device) {
    const raw = String(device || '').split('_')[0] || 'unknown';
    return raw.toLowerCase();
  }

  function archLabel(arch) {
    const labels = {mi250:'MI250', mi300:'MI300', mi325:'MI325', mi355:'MI355', cuda:'CUDA'};
    return labels[arch] || String(arch || 'unknown').toUpperCase();
  }

  function archColor(arch) {
    const colors = {mi325:C.p, mi300:C.g, mi250:C.b, mi355:C.y, cuda:C.o};
    return colors[arch] || C.m;
  }

  function archSortValue(arch) {
    const order = {mi325:0, mi300:1, mi250:2, mi355:3, cuda:4};
    return order[arch] ?? 99;
  }

  function chipHtml(text, color) {
    return `<span style="display:inline-flex;align-items:center;gap:5px;padding:2px 7px;border-radius:4px;border:1px solid ${color}66;background:${color}18;color:${color};font-weight:700;font-size:12px">${escapeHtml(text)}</span>`;
  }

  function parityHardFailCount(group, side) {
    const data = group?.[side] || {};
    return (data.failed || 0) + (data.error || 0);
  }

  function paritySideForHw(group, hw) {
    const links = group?.job_links || [];
    const exact = links.find(link => link?.hw === hw);
    if (exact?.side === 'amd' || exact?.side === 'upstream') return exact.side;
    return /^mi/i.test(String(hw || '')) ? 'amd' : 'upstream';
  }

  function parityHwFailureCount(group, hw) {
    const count = ((group?.hw_failures || {})[hw]) || 0;
    if (!count) return 0;
    const side = paritySideForHw(group, hw);
    return parityHardFailCount(group, side) > 0 ? count : 0;
  }

  function parityHwCanceledCount(group, hw) {
    const count = ((group?.hw_canceled || {})[hw]) || 0;
    if (!count || parityHwFailureCount(group, hw) > 0) return 0;
    const side = paritySideForHw(group, hw);
    const data = group?.[side] || {};
    return (data.canceled || 0) > 0 ? count : 0;
  }

  function gatingStatus(cell) {
    if (!cell || !cell.exists || !cell.latest_matched) {
      return {key:'not_observed', label:'Not observed', color:C.m, rank:2};
    }
    const state = String(cell.latest_state || '').toLowerCase();
    if (state === 'passed') return {key:'green', label:'Green', color:C.g, rank:4};
    if (state === 'soft_fail') return {key:'soft_fail', label:'Soft fail', color:C.y, rank:1};
    if (state === 'failed' || state === 'error') return {key:'failing', label:'Failing', color:C.r, rank:0};
    if (state === 'canceled') return {key:'canceled', label:'Canceled', color:C.m, rank:3};
    return {key:state || 'unknown', label:state ? state.replace(/_/g, ' ') : 'Unknown', color:C.m, rank:2};
  }

  function upstreamNightlyBuilds(analytics) {
    const ci = analytics?.ci;
    if (!ci) return [];
    if (_upstreamNightlyBuildsCache.has(ci)) return _upstreamNightlyBuildsCache.get(ci);
    const builds = (ci.builds || [])
      .filter(b => /^Full CI run\s*-\s*nightly(?:\s|$)/i.test(String(b.message || '')))
      .sort((a,b) => String(b.created_at || '').localeCompare(String(a.created_at || '')));
    _upstreamNightlyBuildsCache.set(ci, builds);
    return builds;
  }

  function upstreamBuildUrl(build) {
    const pipeline = LinkRegistry.bk.pipeline('upstream');
    return build?.web_url || (build?.number ? `${pipeline}/builds/${build.number}` : `${pipeline}/builds?query=nightly`);
  }

  function jobBuildkiteUrl(job, build) {
    const base = upstreamBuildUrl(build).replace(/\/$/, '');
    if (job?.job_id) return `${base}/steps/canvas?jid=${encodeURIComponent(job.job_id)}&tab=output`;
    if (job?.step_id) return `${base}/steps/canvas?sid=${encodeURIComponent(job.step_id)}&tab=output`;
    if (job?.url || job?.web_url) return job.url || job.web_url;
    return base;
  }

  function sourceJobLabel(job, build, count, idx) {
    const raw = String(job?.raw_name || job?.name || '').replace(/^AMD:\s*/i, '').trim();
    if (raw) return raw;
    if (count > 1) return `job ${idx + 1}`;
    return `ci #${build?.number || '?'}`;
  }

  function cleanSourceName(value) {
    return String(value || '')
      .replace(/^AMD:\s*/i, '')
      .replace(/^mi\d{3,4}b?_\d+:\s*/i, '')
      .replace(/\s*\((mi\d{3,4}b?_\d+)\)\s*$/i, '')
      .replace(/%N/g, '')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function sourceDevice(job) {
    const raw = String(job?.raw_name || job?.name || '');
    const suffix = raw.match(/\((mi\d{3}_\d+)\)\s*$/i);
    if (suffix) return suffix[1].toLowerCase();
    const queue = String(job?.q || '');
    const q = queue.match(/amd_(mi\d{3}_\d+)/i);
    return q ? q[1].toLowerCase() : '';
  }

  function sourceKey(value) {
    return cleanSourceName(value).toLowerCase();
  }

  function normalizeHardwareDecoratedName(value) {
    return sourceKey(value)
      .replace(/\((\d+)x(h100|h200|a100|b200|gh200)(?:\s*-\s*\d+xmi\d{3,4}b?)?\)$/i, '($1 gpus)($2)')
      .replace(/\((\d+)\s*gpus?\)\s*\((h100|h200|a100|b200|gh200)\)$/i, '($1 gpus)($2)')
      .replace(/\((h100|h200|a100|b200|gh200)\s*-\s*mi\d{3,4}b?\)$/i, '($1)')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function pairedHardwareTopologyAliases(value) {
    const match = sourceKey(value).match(/^(.*?)\s*\((\d+)x(h100|h200|a100|b200|gh200)\s*-\s*\d+xmi\d{3,4}b?\)\s*$/i);
    if (!match) return [];
    const base = match[1].trim();
    const count = match[2];
    const cuda = match[3].toLowerCase();
    return [
      `${base} (${cuda})`,
      `${base} (${count} gpus)`,
      base,
    ];
  }

  function addHardwareAliasVariants(aliases, alias) {
    const variants = [
      alias.replace(/\s*\((?:\d+\s*)?(?:h100s?|h200s?|a100s?|b200s?|gh200s?|mi\d{3,4}b?s?)\)\s*$/i, '').trim(),
      alias.replace(/\s*\((\d+)\s*gpus?\)\s*\((?:h100s?|h200s?|a100s?|b200s?|gh200s?|mi\d{3,4}b?s?)\)\s*$/i, ' ($1 gpus)').trim(),
      alias.replace(/\s*\((\d+)x(?:h100|h200|a100|b200|gh200)(?:\s*-\s*\d+xmi\d{3,4}b?)?\)\s*$/i, ' ($1 gpus)').trim(),
      alias.replace(/\s*\((?:h100|h200|a100|b200|gh200)\s*-\s*mi\d{3,4}b?\)\s*$/i, '').trim(),
    ];
    for (const variant of variants) if (variant) aliases.add(variant);
  }

  function sourceAliases(value, shardable) {
    const cacheKey = `${shardable ? '1' : '0'}:${String(value || '')}`;
    const cached = _sourceAliasesCache.get(cacheKey);
    if (cached) return cached;
    const base = sourceKey(value);
    const aliases = new Set([base, normalizeHardwareDecoratedName(value)]);
    for (const alias of pairedHardwareTopologyAliases(value)) aliases.add(alias);
    if (shardable || /\s+\d+$/.test(base)) aliases.add(base.replace(/\s+\d+$/, '').trim());
    for (const alias of [...aliases]) {
      aliases.add(alias.replace(/\s+test(s)?(?=\s*(?:$|\())/i, ' test').trim());
      aliases.add(alias.replace(/\s*\((cpu|cuda)\)\s*$/i, '').trim());
      addHardwareAliasVariants(aliases, alias);
    }
    const out = [...aliases].filter(Boolean);
    _sourceAliasesCache.set(cacheKey, out);
    return out;
  }

  function hasCommonHint(a, b) {
    return [...a].some(hint => b.has(hint));
  }

  function labelMatchScore(label, job) {
    const raw = job?.raw_name || job?.name || String(job || '');
    const labelKey = sourceKey(label);
    const rawKey = sourceKey(raw);
    if (rawKey === labelKey) return 100;
    if (normalizeHardwareDecoratedName(raw) === normalizeHardwareDecoratedName(label)) return 95;

    const labelHints = cudaHardwareHints(label);
    const rawHints = cudaHardwareHints(raw);
    if (labelHints.size && rawHints.size) {
      return hasCommonHint(labelHints, rawHints) ? 80 : -1;
    }
    if (!labelHints.size && !rawHints.size) return 60;
    return 40;
  }

  function closestLabelMatches(label, jobs) {
    if (!jobs || jobs.length <= 1) return jobs || [];
    let best = -Infinity;
    const scored = jobs.map(job => {
      const score = labelMatchScore(label, job);
      best = Math.max(best, score);
      return {job, score};
    });
    return scored.filter(row => row.score === best).map(row => row.job);
  }

  function buildUpstreamAmdIndex(build) {
    if (!build) return {};
    if (_upstreamAmdIndexCache.has(build)) return _upstreamAmdIndexCache.get(build);
    const byDevice = {};
    for (const job of build?.jobs || []) {
      const raw = String(job.raw_name || job.name || '');
      if (!/^AMD:\s*/i.test(raw)) continue;
      const device = sourceDevice(job);
      if (!device) continue;
      const bucket = byDevice[device] || (byDevice[device] = {});
      for (const alias of sourceAliases(raw, false)) {
        (bucket[alias] || (bucket[alias] = [])).push(job);
      }
    }
    _upstreamAmdIndexCache.set(build, byDevice);
    return byDevice;
  }

  function matchingSourceJobs(group, sourceIndex) {
    const device = String(group.device || '').toLowerCase();
    const bucket = sourceIndex[device] || {};
    const shardable = /%N/i.test(String(group.label || ''));
    const seen = new Set();
    const matches = [];
    for (const alias of sourceAliases(group.label, shardable)) {
      for (const job of bucket[alias] || []) {
        const key = job.raw_name || job.name || JSON.stringify(job);
        if (!seen.has(key)) {
          seen.add(key);
          matches.push(job);
        }
      }
    }
    return closestLabelMatches(group.label, matches)
      .sort((a,b) => String(a.raw_name || a.name || '').localeCompare(String(b.raw_name || b.name || '')));
  }


  function internalAmdBuilds(analytics) {
    const amd = analytics?.['amd-ci'];
    if (!amd) return [];
    if (_internalBuildsCache.has(amd)) return _internalBuildsCache.get(amd);
    const builds = [...(amd.builds || [])]
      .sort((a,b) => String(b.created_at || '').localeCompare(String(a.created_at || '')));
    _internalBuildsCache.set(amd, builds);
    return builds;
  }

  const GATING_PROGRESS_WINDOWS = [
    {key:'3d', label:'3d', days:3},
    {key:'7d', label:'7d', days:7},
    {key:'14d', label:'14d', days:14},
    {key:'30d', label:'1m', days:30},
  ];
  const DEFAULT_GATING_PROGRESS_WINDOW = '7d';

  function buildTimeMs(build) {
    const raw = build?.created_at || build?.date || '';
    const value = Date.parse(raw);
    return Number.isFinite(value) ? value : NaN;
  }

  function buildNightlyDate(build) {
    return build?.date || String(build?.created_at || '').slice(0, 10);
  }

  function internalBuildForGatingSnapshot(analytics, sourceBuild) {
    const builds = internalAmdBuilds(analytics);
    if (!builds.length) return null;
    const sourceDate = buildNightlyDate(sourceBuild);
    const sameDate = builds.find(build => buildNightlyDate(build) === sourceDate);
    if (sameDate) return sameDate;

    const sourceTime = buildTimeMs(sourceBuild);
    if (!Number.isFinite(sourceTime)) return builds[0];
    let best = null;
    let bestDiff = Infinity;
    for (const build of builds) {
      const time = buildTimeMs(build);
      if (!Number.isFinite(time)) continue;
      const diff = Math.abs(time - sourceTime);
      if (diff < bestDiff) {
        best = build;
        bestDiff = diff;
      }
    }
    return bestDiff <= 36 * 60 * 60 * 1000 ? best : builds[0];
  }

  function internalBuildUrl(build) {
    const pipeline = LinkRegistry.bk.pipeline('amd');
    return build?.web_url || (build?.number ? `${pipeline}/builds/${build.number}` : pipeline);
  }

  function internalBuildLabel(build) {
    if (!build) return 'amd-ci nightly';
    const date = build.created_at ? new Date(build.created_at).toLocaleString([], {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'}) : build.date || '';
    return `amd-ci #${build.number || '?'}${date ? ` (${date})` : ''}`;
  }

  function internalJobBuildkiteUrl(job, build) {
    const base = internalBuildUrl(build).replace(/\/$/, '');
    if (job?.step_id) return `${base}/steps/canvas?sid=${encodeURIComponent(job.step_id)}&tab=output`;
    if (job?.job_id) return `${base}/steps/canvas?jid=${encodeURIComponent(job.job_id)}&tab=output`;
    if (job?.url || job?.web_url) return job.url || job.web_url;
    return base;
  }

  function isInternalAmdJob(job) {
    const raw = String(job?.raw_name || job?.name || '');
    const queue = String(job?.q || '');
    return /^mi\d{3,4}b?_\d+:\s*/i.test(raw) || /^amd_mi\d{3,4}b?_\d+/i.test(queue);
  }

  function buildInternalAmdIndex(build) {
    if (!build) return {};
    if (_internalAmdIndexCache.has(build)) return _internalAmdIndexCache.get(build);
    const byAlias = {};
    for (const job of build?.jobs || []) {
      if (!isInternalAmdJob(job)) continue;
      const raw = job.raw_name || job.name || '';
      const shardable = /\s+\d+$/.test(sourceKey(raw));
      for (const alias of sourceAliases(raw, shardable)) {
        (byAlias[alias] || (byAlias[alias] = [])).push(job);
      }
    }
    _internalAmdIndexCache.set(build, byAlias);
    return byAlias;
  }

  function matchingInternalAmdJobs(label, index) {
    const seen = new Set();
    const matches = [];
    const shardable = /%N/i.test(String(label || ''));
    for (const alias of sourceAliases(label, shardable)) {
      for (const job of index[alias] || []) {
        const key = job.raw_name || job.name || JSON.stringify(job);
        if (!seen.has(key)) {
          seen.add(key);
          matches.push(job);
        }
      }
    }
    return closestLabelMatches(label, matches).sort((a,b) =>
      sourceDevice(a).localeCompare(sourceDevice(b)) ||
      String(a.raw_name || a.name || '').localeCompare(String(b.raw_name || b.name || ''))
    );
  }

  function rowWithInternalSignal(row, internalIndex, internalBuild) {
    const label = row.group?.label || '';
    const jobs = internalBuild ? targetRuntimeJobs(label, matchingInternalAmdJobs(label, internalIndex)) : [];
    return {
      ...row,
      internalSignal: {
        build: internalBuild,
        jobs,
        status: targetRuntimeStatus(label, jobs),
      },
    };
  }

  function addInternalSignals(rows, internalBuild) {
    const index = buildInternalAmdIndex(internalBuild);
    return rows.map(row => rowWithInternalSignal(row, index, internalBuild));
  }

  function mirroredAliasSet(rows) {
    const aliases = new Set();
    for (const row of rows || []) {
      const label = row.group?.label || '';
      for (const alias of sourceAliases(label, /%N/i.test(label))) aliases.add(alias);
      for (const job of row.sourceJobs || []) {
        const raw = job.raw_name || job.name || '';
        for (const alias of sourceAliases(raw, /\s+\d+$/.test(sourceKey(raw)))) aliases.add(alias);
      }
    }
    return aliases;
  }

  function isUpstreamCudaJob(job) {
    const raw = String(job?.raw_name || job?.name || '');
    const queue = String(job?.q || '').toLowerCase();
    if (!raw || /^AMD:\s*/i.test(raw)) return false;
    if (/(^|[^a-z0-9])(arm-cpu|small_cpu|medium_cpu|intel-cpu|intel|cpu|xpu|hpu|npu|ascend)(?=$|[^a-z0-9])/i.test(`${raw} ${queue}`)) return false;
    return /(gpu|cuda|h100|h200|a100|b200|gh200|mithril)/i.test(`${raw} ${queue}`);
  }

  function buildUpstreamCudaIndex(build) {
    if (!build) return {};
    if (_upstreamCudaIndexCache.has(build)) return _upstreamCudaIndexCache.get(build);
    const byAlias = {};
    for (const job of build?.jobs || []) {
      if (!isUpstreamCudaJob(job)) continue;
      const raw = job.raw_name || job.name || '';
      const shardable = /\s+\d+$/.test(sourceKey(raw));
      for (const alias of sourceAliases(raw, shardable)) {
        (byAlias[alias] || (byAlias[alias] = [])).push(job);
      }
    }
    _upstreamCudaIndexCache.set(build, byAlias);
    return byAlias;
  }

  function upstreamCudaCandidateRows(build, currentRows, limit) {
    const mirrored = mirroredAliasSet(currentRows);
    const seen = new Set();
    const candidates = [];
    for (const job of build?.jobs || []) {
      if (!isUpstreamCudaJob(job)) continue;
      const raw = job.raw_name || job.name || '';
      const aliases = sourceAliases(raw, /\s+\d+$/.test(sourceKey(raw)));
      if (aliases.some(alias => mirrored.has(alias))) continue;
      const key = sourceKey(raw);
      if (seen.has(key)) continue;
      seen.add(key);
      candidates.push({
        group: {
          label: cleanSourceName(raw),
          area: area(raw),
          queue: job.q || '',
          yaml_file: 'upstream vllm/ci nightly',
        },
        arch: 'cuda',
        row: null,
        cell: {
          exists: true,
          latest_matched: true,
          latest_state: 'not_gated',
          latest_url: jobBuildkiteUrl(job, build),
        },
        sourceBuild: build,
        sourceJobs: [job],
        status: {key:'not_gated', label:'Not gated yet', color:C.y, rank:5},
        isGreen: false,
        section: 'Not gated yet upstream CUDA candidates',
      });
    }
    candidates.sort((a,b) =>
      (a.group.area || '').localeCompare(b.group.area || '') ||
      (a.group.label || '').localeCompare(b.group.label || '')
    );
    return Number.isFinite(limit) ? candidates.slice(0, Math.max(0, limit)) : candidates;
  }

  function matchingUpstreamJobsForLabel(label, build) {
    const index = buildUpstreamCudaIndex(build);
    const matches = [];
    const seen = new Set();
    for (const alias of sourceAliases(label, /%N/i.test(String(label || '')))) {
      for (const job of index[alias] || []) {
        const key = job.raw_name || job.name || JSON.stringify(job);
        if (!seen.has(key)) {
          seen.add(key);
          matches.push(job);
        }
      }
    }
    return closestLabelMatches(label, matches)
      .sort((a,b) => String(a.raw_name || a.name || '').localeCompare(String(b.raw_name || b.name || '')));
  }

  function buildProposedRows(proposals, sourceBuild, currentRows) {
    const alreadyGated = mirroredAliasSet(currentRows);
    const rows = [];
    const seen = new Set();
    for (const pr of proposals?.pull_requests || []) {
      for (const mirror of pr.new_mirrors || []) {
        const label = mirror.label || '';
        const aliases = sourceAliases(label, /%N/i.test(label));
        if (aliases.some(alias => alreadyGated.has(alias))) continue;
        const key = `${mirror.yaml_file}:${mirror.key || label}:${mirror.device || ''}`;
        if (seen.has(key)) continue;
        seen.add(key);
        const sourceJobs = matchingUpstreamJobsForLabel(label, sourceBuild);
        rows.push({
          group: {
            label,
            area: mirror.area || area(label),
            queue: mirror.device || '',
            yaml_file: mirror.yaml_file || '',
            source_file_dependencies: mirror.source_file_dependencies || [],
          },
          arch: archFromDevice(mirror.device),
          row: null,
          cell: {
            exists: sourceJobs.length > 0,
            latest_matched: sourceJobs.length > 0,
            latest_state: 'proposed',
            latest_url: sourceJobs.length ? jobBuildkiteUrl(sourceJobs[0], sourceBuild) : null,
          },
          sourceBuild,
          sourceJobs,
          status: {key:'proposed', label:'Proposed', color:PROPOSAL_COLOR, rank:6},
          isGreen: false,
          section: 'Proposed for gating',
          proposal: {
            number: pr.number,
            title: pr.title || '',
            url: pr.url || '',
            author: pr.author || '',
            head_ref: pr.head_ref || '',
            updated_at: pr.updated_at || '',
          },
        });
      }
    }
    rows.sort((a,b) =>
      (b.proposal?.updated_at || '').localeCompare(a.proposal?.updated_at || '') ||
      Number(b.proposal?.number || 0)-Number(a.proposal?.number || 0) ||
      archSortValue(a.arch)-archSortValue(b.arch) ||
      (a.group.area || '').localeCompare(b.group.area || '') ||
      (a.group.label || '').localeCompare(b.group.label || '')
    );
    return rows;
  }

  function sourceStatus(jobs) {
    if (!jobs.length) return {key:'not_observed', label:'Not in nightly', color:C.m, rank:2};
    const states = jobs.map(j => String(j.state || '').toLowerCase());
    if (states.some(s => ['failed','timed_out','broken','error'].includes(s))) return {key:'failing', label:'Failing', color:C.r, rank:0};
    if (states.some(s => ['soft_fail','soft_failed'].includes(s))) return {key:'soft_fail', label:'Soft fail', color:C.y, rank:1};
    if (states.some(s => ['running','scheduled','assigned'].includes(s))) return {key:'running', label:'Running', color:C.y, rank:3};
    if (states.length && states.every(s => s === 'passed')) return {key:'green', label:'Green', color:C.g, rank:4};
    if (states.some(s => s === 'canceled')) return {key:'canceled', label:'Canceled', color:C.m, rank:3};
    return {key:'unknown', label:'Unknown', color:C.m, rank:2};
  }

  function cudaHardwareHints(value) {
    const hints = new Set();
    for (const match of sourceKey(value).matchAll(/(?:^|[^a-z0-9])(?:\d+x)?(h100|h200|a100|b200|gh200)(?=$|[^a-z0-9])/gi)) {
      hints.add(match[1].toLowerCase());
    }
    return hints;
  }

  function matchesTargetHardwareHint(label, job) {
    const targetHints = cudaHardwareHints(label);
    if (!targetHints.size) return true;
    const jobHints = cudaHardwareHints(job?.raw_name || job?.name || '');
    if (!jobHints.size) return true;
    return [...targetHints].some(hint => jobHints.has(hint));
  }

  function targetRuntimeJobs(label, jobs) {
    const allowCpu = /(^|[^a-z0-9])cpu(?=$|[^a-z0-9])/i.test(String(label || ''));
    if (allowCpu) return closestLabelMatches(label, jobs || []);
    const filtered = (jobs || []).filter(job => {
      const raw = String(job?.raw_name || job?.name || '');
      return !/(^|[^a-z0-9])cpu(?=$|[^a-z0-9])/i.test(raw) && matchesTargetHardwareHint(label, job);
    });
    return closestLabelMatches(label, filtered);
  }

  function targetRuntimeStatus(label, jobs) {
    const runtimeJobs = targetRuntimeJobs(label, jobs);
    if (!runtimeJobs.length) return sourceStatus([]);
    const byDevice = {};
    for (const job of runtimeJobs) {
      const device = sourceDevice(job) || String(job.q || 'unknown');
      (byDevice[device] || (byDevice[device] = [])).push(job);
    }
    const deviceStatuses = Object.values(byDevice).map(bucket => sourceStatus(bucket));
    if (deviceStatuses.some(status => status.key === 'green')) {
      return {key:'green', label:'Green', color:C.g, rank:4};
    }
    if (deviceStatuses.some(status => ['failing','soft_fail'].includes(status.key))) {
      return {key:'failing', label:'Failing', color:C.r, rank:0};
    }
    const unresolved = deviceStatuses.find(status => ['running','canceled'].includes(status.key));
    if (unresolved) return unresolved;
    return deviceStatuses[0] || sourceStatus([]);
  }

  function buildGatingRows(capacity, matrix, analytics, selectedBuild) {
    const sourceBuild = selectedBuild || upstreamNightlyBuilds(analytics)[0] || null;
    const sourceIndex = buildUpstreamAmdIndex(sourceBuild);
    const groups = (capacity?.groups || []).filter(g => g && g.in_capacity_scope !== false);
    return groups.map(g => {
      const arch = archFromDevice(g.device);
      const sourceJobs = matchingSourceJobs(g, sourceIndex);
      const status = sourceStatus(sourceJobs);
      const cell = {
        exists: sourceJobs.length > 0,
        latest_matched: sourceJobs.length > 0,
        latest_state: status.key === 'green' ? 'passed' : status.key,
        latest_url: sourceJobs.length ? jobBuildkiteUrl(sourceJobs[0], sourceBuild) : null,
      };
      return {
        group: g,
        arch,
        row: null,
        cell,
        sourceBuild,
        sourceJobs,
        status,
        isGreen: status.key === 'green',
      };
    });
  }

  function sourceBuildLabel(build) {
    if (!build) return 'vllm/ci nightly';
    const date = build.created_at ? new Date(build.created_at).toLocaleString([], {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'}) : build.date || '';
    return `vllm/ci #${build.number || '?'}${date ? ` (${date})` : ''}`;
  }

  function rowSourceLinks(row) {
    const build = row.sourceBuild;
    const jobs = row.sourceJobs || [];
    if (!jobs.length && build) {
      return `<span style="color:${C.m}">no matching job in ${LinkRegistry.aTag(upstreamBuildUrl(build), `ci #${build.number || '?'}`)}</span>`;
    }
    if (!jobs.length) return '<span style="color:var(--text-muted)">not in nightly</span>';
    return jobs.map((job, idx) => {
      const label = sourceJobLabel(job, build, jobs.length, idx);
      return LinkRegistry.aTag(jobBuildkiteUrl(job, build), label);
    }).join(' ');
  }


  function internalSignalLinks(row) {
    const signal = row.internalSignal;
    if (!signal) return `<span style="color:${C.m}">already gated</span>`;
    const build = signal.build;
    const jobs = signal.jobs || [];
    if (!build) return `<span style="color:${C.m}">no amd-ci data</span>`;
    const buildLink = LinkRegistry.aTag(internalBuildUrl(build), internalBuildLabel(build));
    if (!jobs.length) return `<span style="color:${C.m}">not seen in ${buildLink}</span>`;
    const status = signal.status || sourceStatus(jobs);
    const displayStatus = status.key === 'soft_fail'
      ? {key:'soft_fail', label:'Failing (soft-fail)', color:C.r}
      : status;
    return jobs.map((job, idx) => {
      const device = sourceDevice(job) || String(job.q || '').replace(/^amd_/, '');
      const state = String(job.state || '').replace(/_/g, ' ') || 'unknown';
      const label = jobs.length === 1
        ? `${displayStatus.label}${device ? ` on ${device}` : ''}`
        : `${device || `job ${idx + 1}`}: ${state}`;
      return LinkRegistry.aTag(internalJobBuildkiteUrl(job, build), label, {style:`color:${displayStatus.color}`});
    }).join('<br>');
  }

  function internalFailureSection(row) {
    return row.internalSignal?.status?.key === 'soft_fail'
      ? 'Soft-fail in amd-ci, not gated upstream'
      : 'Failing in amd-ci, not gated upstream';
  }

  function internalNoSignalSection(row) {
    const key = row.internalSignal?.status?.key;
    if (key === 'not_observed') return 'No amd-ci signal, not gated upstream';
    if (key === 'canceled') return 'Canceled in amd-ci, not gated upstream';
    return 'Unresolved in amd-ci, not gated upstream';
  }

  function sourceRowsTable(rows, build, targetGap=0, opts={}) {
    const sorted = opts.preserveOrder ? [...rows] : [...rows].sort((a,b) =>
      (a.status.rank-b.status.rank) ||
      archSortValue(a.arch)-archSortValue(b.arch) ||
      (a.group.area || '').localeCompare(b.group.area || '') ||
      (a.group.label || '').localeCompare(b.group.label || '')
    );
    const includeInternal = opts.includeInternalSignal || sorted.some(row => row.internalSignal);
    const colCount = includeInternal ? 8 : 7;
    let html = opts.includeSource === false ? '' : `<p style="color:${C.m};font-size:13px;margin:0 0 12px">Source: ${build ? LinkRegistry.aTag(upstreamBuildUrl(build), sourceBuildLabel(build)) : 'no upstream nightly source'} on main.</p>`;
    html += '<table style="width:100%;border-collapse:collapse;font-size:13px">';
    html += '<thead><tr>';
    const heads = includeInternal ? ['#','Status','Test Group','Arch','Area','Upstream','amd-ci nightly','YAML'] : ['#','Status','Test Group','Arch','Area','Buildkite','YAML'];
    heads.forEach((head,idx) => {
      html += `<th style="text-align:${idx===0?'center':'left'};padding:8px 10px;border-bottom:2px solid ${C.bd};color:${C.m};font-size:12px;text-transform:uppercase">${head}</th>`;
    });
    html += '</tr></thead><tbody>';
    let lastSection = '';
    sorted.forEach((row, idx) => {
      if (opts.showSections && row.section && row.section !== lastSection) {
        lastSection = row.section;
        html += `<tr><td colspan="${colCount}" style="padding:10px 10px 6px;color:${C.m};font-size:12px;text-transform:uppercase;letter-spacing:.4px;border-bottom:1px solid ${C.bd}">${escapeHtml(lastSection)}</td></tr>`;
      }
      html += `<tr style="border-bottom:1px solid ${C.bd}">`;
      html += `<td style="text-align:center;padding:8px 10px;color:${C.m}">${idx+1}</td>`;
      html += `<td style="padding:8px 10px;color:${row.status.color};font-weight:700">${escapeHtml(row.status.label)}</td>`;
      html += `<td style="padding:8px 10px">${escapeHtml(row.group.label || '')}</td>`;
      html += `<td style="padding:8px 10px">${chipHtml(archLabel(row.arch), archColor(row.arch))}</td>`;
      html += `<td style="padding:8px 10px">${escapeHtml(row.group.area || 'Other')}</td>`;
      html += `<td style="padding:8px 10px">${rowSourceLinks(row)}</td>`;
      if (includeInternal) html += `<td style="padding:8px 10px;line-height:1.45">${internalSignalLinks(row)}</td>`;
      html += `<td style="padding:8px 10px;color:${C.m};font-size:12px">${escapeHtml(row.group.yaml_file || '')}</td>`;
      html += '</tr>';
    });
    if (targetGap > 0) {
      html += `<tr style="border-bottom:1px solid ${C.bd};background:${C.bd}22">`;
      html += `<td style="text-align:center;padding:8px 10px;color:${C.m}">+</td>`;
      html += `<td style="padding:8px 10px;color:${C.y};font-weight:700">Target gap</td>`;
      html += `<td style="padding:8px 10px" colspan="3">${targetGap.toLocaleString()} additional AMD mirror groups needed to reach the configured target</td>`;
      html += `<td style="padding:8px 10px;color:${C.m}">no Buildkite source yet</td>`;
      if (includeInternal) html += `<td style="padding:8px 10px;color:${C.m}">not applicable</td>`;
      html += `<td style="padding:8px 10px;color:${C.m};font-size:12px">capacity_monitor.json target</td>`;
      html += '</tr>';
    }
    html += '</tbody></table>';
    return html;
  }

  function showGatingSourceOverlay(title, rows, build, targetGap=0) {
    showOverlayPanel(
      `<span style="color:${C.b}">${escapeHtml(title)}</span> <span style="color:${C.m};font-weight:400">(${rows.length.toLocaleString()} groups${targetGap ? ` + ${targetGap.toLocaleString()} target gap` : ''})</span>`,
      sourceRowsTable(rows, build, targetGap)
    );
  }

  function showGatingPathOverlay(title, rows, build) {
    showOverlayPanel(
      `<span style="color:${C.b}">${escapeHtml(title)}</span> <span style="color:${C.m};font-weight:400">(${rows.length.toLocaleString()} groups)</span>`,
      sourceRowsTable(rows, build, 0, {preserveOrder:true, showSections:true, includeInternalSignal:rows.some(row => row.internalSignal)})
    );
  }

  function architectureFootprintHtml(rows, build) {
    const byArch = {};
    for (const row of rows) (byArch[row.arch] || (byArch[row.arch] = [])).push(row);
    let html = `<p style="color:${C.m};font-size:13px;margin:0 0 12px">Source: ${build ? LinkRegistry.aTag(upstreamBuildUrl(build), sourceBuildLabel(build)) : 'no upstream nightly source'} on main. Groups are separated by AMD architecture.</p>`;
    for (const [arch, archRows] of Object.entries(byArch).sort((a,b)=>archSortValue(a[0])-archSortValue(b[0]))) {
      const color = archColor(arch);
      html += `<details open style="border:1px solid ${C.bd};border-radius:8px;margin:0 0 10px;background:${C.bg2}">`;
      html += `<summary style="cursor:pointer;padding:10px 12px;font-weight:700">${chipHtml(archLabel(arch), color)} <span style="color:${C.m};font-weight:400">${archRows.length.toLocaleString()} groups</span></summary>`;
      html += `<div style="padding:0 12px 12px">${sourceRowsTable(archRows, build, 0, {includeSource:false})}</div>`;
      html += '</details>';
    }
    return html;
  }

  function showArchitectureFootprintOverlay(rows, build) {
    showOverlayPanel(
      `<span style="color:${C.p}">Architecture footprint</span> <span style="color:${C.m};font-weight:400">(${rows.length.toLocaleString()} gated groups)</span>`,
      architectureFootprintHtml(rows, build)
    );
  }

  function proposalRowsTable(rows, build, proposals) {
    const summary = proposals?.summary || {};
    let html = `<p style="color:${C.m};font-size:13px;margin:0 0 12px">Open PRs from tracked engineers that add new <code>mirror.amd</code> blocks under <code>.buildkite/test_areas</code>. Upstream links use ${build ? LinkRegistry.aTag(upstreamBuildUrl(build), sourceBuildLabel(build)) : 'the latest vllm/ci nightly'}.</p>`;
    html += `<p style="color:${C.m};font-size:12px;margin:0 0 12px">${fmtInt(summary.proposal_pr_count || 0)} PRs with proposals from ${fmtInt(summary.scanned_pr_count || 0)} scanned open PRs by ${fmtInt(summary.tracked_author_count || 0)} tracked authors.</p>`;
    html += '<table style="width:100%;border-collapse:collapse;font-size:13px">';
    html += '<thead><tr>';
    ['#','PR','Test Group','Device','Area','Upstream','YAML'].forEach((head,idx) => {
      html += `<th style="text-align:${idx===0?'center':'left'};padding:8px 10px;border-bottom:2px solid ${C.bd};color:${C.m};font-size:12px;text-transform:uppercase">${head}</th>`;
    });
    html += '</tr></thead><tbody>';
    rows.forEach((row, idx) => {
      const pr = row.proposal || {};
      const prLink = pr.url ? LinkRegistry.aTag(pr.url, `#${pr.number}`) : `#${escapeHtml(pr.number || '?')}`;
      html += `<tr style="border-bottom:1px solid ${C.bd}">`;
      html += `<td style="text-align:center;padding:8px 10px;color:${C.m}">${idx+1}</td>`;
      html += `<td style="padding:8px 10px">${prLink}<div style="color:${C.m};font-size:12px">${escapeHtml(pr.author || '')}${pr.head_ref ? ` · ${escapeHtml(pr.head_ref)}` : ''}</div></td>`;
      html += `<td style="padding:8px 10px">${escapeHtml(row.group.label || '')}<div style="color:${C.m};font-size:12px">${escapeHtml(pr.title || '')}</div></td>`;
      html += `<td style="padding:8px 10px">${chipHtml(row.group.queue || archLabel(row.arch), archColor(row.arch))}</td>`;
      html += `<td style="padding:8px 10px">${escapeHtml(row.group.area || 'Other')}</td>`;
      html += `<td style="padding:8px 10px">${rowSourceLinks(row)}</td>`;
      html += `<td style="padding:8px 10px;color:${C.m};font-size:12px">${escapeHtml(row.group.yaml_file || '')}</td>`;
      html += '</tr>';
    });
    html += '</tbody></table>';
    return html;
  }

  function showProposedGatingOverlay(rows, build, proposals) {
    showOverlayPanel(
      `<span style="color:${PROPOSAL_COLOR}">Proposed for gating</span> <span style="color:${C.m};font-weight:400">(${rows.length.toLocaleString()} groups)</span>`,
      proposalRowsTable(rows, build, proposals)
    );
  }

  function targetAuditDecisionMeta(decision) {
    const meta = {
      new_candidate: {label:'New candidate', color:C.b, rank:0},
      likely_duplicate: {label:'Likely duplicate', color:C.y, rank:1},
      missing_from_upstream: {label:'Missing canonical', color:C.m, rank:2},
      excluded: {label:'Excluded', color:C.r, rank:3},
      canonical: {label:'Canonical', color:C.g, rank:4},
    };
    return meta[decision] || {label:String(decision || 'Unknown').replace(/_/g, ' '), color:C.m, rank:9};
  }

  function targetAuditProposalLinks(row) {
    const matches = row.proposal_matches || [];
    if (!matches.length) return '<span style="color:var(--text-muted)">no tracked PR</span>';
    return matches.map(pr => {
      const label = pr.pr ? `#${pr.pr}` : 'PR';
      const link = pr.url ? LinkRegistry.aTag(pr.url, label) : escapeHtml(label);
      const device = pr.device ? ` ${chipHtml(pr.device, archColor(archFromDevice(pr.device)))}` : '';
      return `${link}${device}<div style="color:${C.m};font-size:12px">${escapeHtml(pr.author || '')}${pr.title ? ` · ${escapeHtml(pr.title)}` : ''}</div>`;
    }).join('<br>');
  }

  function targetAuditInternalLinks(row) {
    const signal = row.internal_signal || row.internalSignal;
    if (!signal) return targetAuditProposalLinks(row);
    const jobs = signal.jobs || [];
    if (!jobs.length) return targetAuditProposalLinks(row);
    return jobs.map(job => {
      const state = String(job.state || signal.state || 'unknown').replace(/_/g, ' ');
      const queue = job.queue ? ` on ${job.queue}` : '';
      const text = `${state}${queue}`;
      return job.url ? LinkRegistry.aTag(job.url, text) : escapeHtml(text);
    }).join('<br>');
  }

  function targetCandidateAuditTable(candidates) {
    const summary = candidates?.summary || {};
    const rows = [...(candidates?.rows || [])].sort((a,b) => {
      const ma = targetAuditDecisionMeta(a.decision);
      const mb = targetAuditDecisionMeta(b.decision);
      return ma.rank - mb.rank || String(a.label || '').localeCompare(String(b.label || ''));
    });
    const source = candidates?.source || {};
    const canonicalCount = summary.canonical_target_count || 0;
    let html = `<p style="color:${C.m};font-size:13px;margin:0 0 12px">Review-only daily audit. It folds obvious hardware-only suffix differences while preserving GPU counts, then compares upstream nightly GPU jobs with the canonical ${fmtInt(canonicalCount)}-row target list.</p>`;
    html += `<p style="color:${C.m};font-size:12px;margin:0 0 12px">${fmtInt(summary.new_candidate_count || 0)} new candidates, ${fmtInt(summary.likely_duplicate_count || 0)} likely duplicates, ${fmtInt(summary.excluded_count || 0)} exclusions, ${fmtInt(summary.missing_from_upstream_count || 0)} missing canonical rows. Source: ${escapeHtml(source.nightly_signal || 'gating_nightlies.json')}.</p>`;
    html += '<table style="width:100%;border-collapse:collapse;font-size:13px">';
    html += '<thead><tr>';
    ['#','Decision','Test Group','Canonical / Reason','Upstream','amd-ci / PR'].forEach((head,idx) => {
      html += `<th style="text-align:${idx===0?'center':'left'};padding:8px 10px;border-bottom:2px solid ${C.bd};color:${C.m};font-size:12px;text-transform:uppercase">${head}</th>`;
    });
    html += '</tr></thead><tbody>';
    let lastDecision = '';
    rows.forEach((row, idx) => {
      const meta = targetAuditDecisionMeta(row.decision);
      if (row.decision !== lastDecision) {
        lastDecision = row.decision;
        html += `<tr><td colspan="6" style="padding:10px 10px 6px;color:${meta.color};font-size:12px;text-transform:uppercase;letter-spacing:.4px;border-bottom:1px solid ${C.bd};font-weight:700">${escapeHtml(meta.label)}</td></tr>`;
      }
      const upstream = row.url ? LinkRegistry.aTag(row.url, `ci job${row.state ? ` (${row.state})` : ''}`) : '<span style="color:var(--text-muted)">not seen upstream</span>';
      const reason = row.decision === 'likely_duplicate'
        ? `Duplicate of ${escapeHtml(row.duplicate_of || '')}`
        : row.decision === 'excluded'
          ? escapeHtml((row.exclusion_reasons || []).join(', ') || 'excluded by heuristic')
          : row.decision === 'missing_from_upstream'
            ? `Canonical target #${escapeHtml(row.target_id || '')}`
            : row.decision === 'canonical'
              ? `Canonical target #${escapeHtml(row.target_id || '')}`
              : escapeHtml(row.canonical_key || '');
      html += `<tr style="border-bottom:1px solid ${C.bd}">`;
      html += `<td style="text-align:center;padding:8px 10px;color:${C.m}">${idx+1}</td>`;
      html += `<td style="padding:8px 10px;color:${meta.color};font-weight:700">${escapeHtml(meta.label)}</td>`;
      html += `<td style="padding:8px 10px">${escapeHtml(row.label || '')}<div style="color:${C.m};font-size:12px">${escapeHtml(row.queue || '')}</div></td>`;
      html += `<td style="padding:8px 10px;color:${C.m};font-size:12px">${reason}</td>`;
      html += `<td style="padding:8px 10px">${upstream}</td>`;
      html += `<td style="padding:8px 10px;line-height:1.45">${targetAuditInternalLinks(row)}</td>`;
      html += '</tr>';
    });
    if (!rows.length) {
      html += `<tr><td colspan="6" style="padding:12px;color:${C.m}">No target candidate audit rows are available yet.</td></tr>`;
    }
    html += '</tbody></table>';
    return html;
  }

  function showTargetCandidateAuditOverlay(candidates) {
    const summary = candidates?.summary || {};
    showOverlayPanel(
      `<span style="color:${C.b}">Target list audit</span> <span style="color:${C.m};font-weight:400">(${fmtInt(summary.row_count || 0)} rows)</span>`,
      targetCandidateAuditTable(candidates)
    );
  }

  function legendItem(color, label) {
    return h('span',{style:{display:'inline-flex',alignItems:'center',gap:'6px',fontSize:'12px',color:C.m}},[
      h('span',{style:{width:'10px',height:'10px',borderRadius:'2px',background:color,border:`1px solid ${color}`}}),
      h('span',{text:label})
    ]);
  }

  function proposalsVisibleByBuild(proposals, build) {
    const buildTime = Date.parse(build?.created_at || build?.date || '');
    if (!Number.isFinite(buildTime)) return proposals;
    return {
      ...proposals,
      pull_requests: (proposals?.pull_requests || []).filter(pr => {
        const proposalTime = Date.parse(pr.created_at || pr.updated_at || '');
        return !Number.isFinite(proposalTime) || proposalTime <= buildTime;
      }),
    };
  }

  function targetGroups(targets) {
    return Array.isArray(targets?.groups) ? targets.groups : [];
  }

  function canonicalTargetCandidateRows(targets, sourceBuild, currentRows, limit) {
    const mirrored = mirroredAliasSet(currentRows);
    const candidates = [];
    for (const target of targetGroups(targets)) {
      const label = target.label || '';
      const aliases = sourceAliases(label, /%N/i.test(label));
      if (aliases.some(alias => mirrored.has(alias))) continue;
      const sourceJobs = matchingUpstreamJobsForLabel(label, sourceBuild);
      candidates.push({
        group: {
          label,
          area: target.area || area(label),
          queue: '',
          yaml_file: 'gating_targets.json',
        },
        arch: 'cuda',
        row: null,
        cell: {
          exists: sourceJobs.length > 0,
          latest_matched: sourceJobs.length > 0,
          latest_state: 'not_gated',
          latest_url: sourceJobs.length ? jobBuildkiteUrl(sourceJobs[0], sourceBuild) : null,
        },
        sourceBuild,
        sourceJobs,
        status: {key:'not_gated', label:'Not gated yet', color:C.y, rank:5},
        isGreen: false,
        section: 'Not gated yet target list',
        target,
      });
    }
    candidates.sort((a,b) =>
      Number(a.target?.id || 0)-Number(b.target?.id || 0) ||
      (a.group.label || '').localeCompare(b.group.label || '')
    );
    return Number.isFinite(limit) ? candidates.slice(0, Math.max(0, limit)) : candidates;
  }

  function targetAliasSet(targets) {
    if (!targets) return new Set();
    if (_targetAliasSetCache.has(targets)) return _targetAliasSetCache.get(targets);
    const aliases = new Set();
    for (const target of targetGroups(targets)) {
      const label = target.label || '';
      for (const alias of sourceAliases(label, /%N/i.test(label))) aliases.add(alias);
    }
    _targetAliasSetCache.set(targets, aliases);
    return aliases;
  }

  function rowMatchesTargetList(row, targets) {
    const aliases = targetAliasSet(targets);
    if (!aliases.size) return true;
    const label = row.group?.label || '';
    return sourceAliases(label, /%N/i.test(label)).some(alias => aliases.has(alias));
  }

  function buildStillToGateSnapshot(target, rows, proposalsForBuild, sourceBuild, internalBuild, targets) {
    const gatedRows = rows.filter(r => (r.sourceJobs || []).length > 0);
    const remaining = Math.max(0, target - gatedRows.length);
    const proposedRows = buildProposedRows(proposalsForBuild, sourceBuild, rows);
    const targetScopedProposedRows = proposedRows.filter(row => rowMatchesTargetList(row, targets));
    const proposed = Math.min(targetScopedProposedRows.length, remaining);
    const unplanned = Math.max(0, remaining - proposed);
    const missingSourceRows = targetGroups(targets).length
      ? canonicalTargetCandidateRows(targets, sourceBuild, [...rows, ...targetScopedProposedRows], unplanned)
      : upstreamCudaCandidateRows(sourceBuild, [...rows, ...targetScopedProposedRows], unplanned);
    const missingCandidateRows = missingSourceRows
      .map(r => ({...r, section:'Not yet proposed'}));
    const proposedPathRows = targetScopedProposedRows.slice(0, proposed);
    const stillToGateRows = addInternalSignals([
      ...proposedPathRows.map(r => ({...r, section:'Proposed for gating'})),
      ...missingCandidateRows,
    ], internalBuild);
    const internalGreenRows = stillToGateRows.filter(r => r.internalSignal?.status?.key === 'green');
    const internalHardFailRows = stillToGateRows.filter(r => r.internalSignal?.status?.key === 'failing');
    const internalSoftFailRows = stillToGateRows.filter(r => r.internalSignal?.status?.key === 'soft_fail');
    const internalFailingRows = [...internalHardFailRows, ...internalSoftFailRows];
    const internalNoSignalRows = stillToGateRows.filter(r =>
      !['green','failing','soft_fail'].includes(r.internalSignal?.status?.key || '')
    );
    return {
      gatedRows,
      remaining,
      proposedRows,
      targetScopedProposedRows,
      proposed,
      unplanned,
      missingCandidateRows,
      proposedPathRows,
      stillToGateRows,
      internalGreenRows,
      internalGreenStillToGate: internalGreenRows.length,
      internalHardFailRows,
      internalHardFailStillToGate: internalHardFailRows.length,
      internalFailingRows,
      internalFailingStillToGate: internalFailingRows.length,
      internalSoftFailRows,
      internalSoftFailStillToGate: internalSoftFailRows.length,
      internalNoSignalRows,
      internalNoSignalStillToGate: internalNoSignalRows.length,
    };
  }

  function targetSignal(target, key, fallbackKey) {
    return String(target?.[key] || target?.[fallbackKey] || 'unknown').toLowerCase();
  }

  function buildCurrentRowAliasIndex(rows) {
    const index = new Map();
    for (const row of rows || []) {
      const rowLabel = row.group?.label || '';
      for (const alias of sourceAliases(rowLabel, /%N/i.test(rowLabel))) {
        if (!index.has(alias)) index.set(alias, row);
      }
    }
    return index;
  }

  function currentRowForTarget(target, rows, rowIndex) {
    const label = target?.label || '';
    const index = rowIndex || buildCurrentRowAliasIndex(rows);
    for (const alias of sourceAliases(label, /%N/i.test(label))) {
      const row = index.get(alias);
      if (row) return row;
    }
    return null;
  }

  function canonicalStatusForTarget(target, sourceRuntimeStatus=null, internalRuntimeStatus=null) {
    const gating = targetSignal(target, 'gating_signal', 'source_signal');
    const pf = targetSignal(target, 'pf_signal', 'readiness_signal');
    const sourceKey = sourceRuntimeStatus?.key || '';
    const internalKey = internalRuntimeStatus?.key || '';
    if (sourceKey === 'green') {
      return {key:'target_gated_green', label:'Gated/proposed green', color:C.g, rank:0, section:'Gated/proposed green'};
    }
    if (internalKey === 'green') {
      return {key:'target_ready', label:'Passing ready to gate', color:'#2dd4bf', rank:1, section:'Passing ready to gate'};
    }
    if (['failing','soft_fail'].includes(sourceKey)) {
      return {key:'target_failing', label:'Failing target gap', color:C.r, rank:2, section:'Failing target gap'};
    }
    if (['failing','soft_fail'].includes(internalKey)) {
      return {key:'target_failing', label:'Failing target gap', color:C.r, rank:2, section:'Failing target gap'};
    }
    if (gating === 'green') {
      return {key:'target_gated_green', label:'Gated/proposed green', color:C.g, rank:0, section:'Gated/proposed green'};
    }
    if (pf === 'green') {
      return {key:'target_ready', label:'Passing ready to gate', color:'#2dd4bf', rank:1, section:'Passing ready to gate'};
    }
    if (pf === 'red') {
      return {key:'target_failing', label:'Failing target gap', color:C.r, rank:2, section:'Failing target gap'};
    }
    if (pf === 'yellow') {
      return {key:'target_todo', label:'No amd-ci signal', color:C.y, rank:3, section:'Todo / no amd-ci signal'};
    }
    if (pf === 'purple') {
      return {key:'target_infra', label:'Infra blocked', color:C.m, rank:4, section:'Infra blocked'};
    }
    return {key:'target_unknown', label:'Unknown target state', color:C.m, rank:5, section:'Unknown target state'};
  }

  function buildCanonicalTargetRows(targets, currentRows, sourceBuild, internalBuild) {
    const internalIndex = buildInternalAmdIndex(internalBuild);
    const currentRowIndex = buildCurrentRowAliasIndex(currentRows);
    return targetGroups(targets).map(target => {
      const currentRow = currentRowForTarget(target, currentRows, currentRowIndex);
      const label = target.label || '';
      const mirroredSourceJobs = currentRow?.sourceJobs?.length
        ? targetRuntimeJobs(label, currentRow.sourceJobs)
        : [];
      const sourceJobs = mirroredSourceJobs.length
        ? mirroredSourceJobs
        : matchingUpstreamJobsForLabel(label, sourceBuild);
      const sourceRuntimeStatus = targetRuntimeStatus(label, mirroredSourceJobs);
      const fallbackStatus = canonicalStatusForTarget(target);
      const row = {
        group: {
          label,
          area: target.area || area(label),
          queue: currentRow?.group?.queue || '',
          yaml_file: currentRow?.group?.yaml_file || 'gating_targets.json',
        },
        arch: currentRow?.arch || 'cuda',
        row: currentRow?.row || null,
        cell: {
          exists: sourceJobs.length > 0,
          latest_matched: sourceJobs.length > 0,
          latest_state: fallbackStatus.key,
          latest_url: sourceJobs.length ? jobBuildkiteUrl(sourceJobs[0], sourceBuild) : null,
        },
        sourceBuild,
        sourceJobs,
        status: fallbackStatus,
        isGreen: false,
        section: '',
        target,
        canonicalSignals: {
          gating: targetSignal(target, 'gating_signal', 'source_signal'),
          pf: targetSignal(target, 'pf_signal', 'readiness_signal'),
          assigned: targetSignal(target, 'assigned_signal', 'target_signal'),
        },
      };
      const withSignal = rowWithInternalSignal(row, internalIndex, internalBuild);
      const status = canonicalStatusForTarget(target, sourceRuntimeStatus, withSignal.internalSignal?.status);
      return {
        ...withSignal,
        status,
        isGreen: status.key === 'target_gated_green' || status.key === 'target_ready',
        section: status.section,
        cell: {
          ...withSignal.cell,
          latest_state: status.key,
        },
        canonicalSignals: {
          ...withSignal.canonicalSignals,
          source_runtime: sourceRuntimeStatus.key,
          internal_runtime: withSignal.internalSignal?.status?.key || 'not_observed',
        },
      };
    }).sort((a,b) =>
      (a.status.rank-b.status.rank) ||
      Number(a.target?.id || 0)-Number(b.target?.id || 0) ||
      (a.group.label || '').localeCompare(b.group.label || '')
    );
  }

  function gatingProgressBuilds(builds, days) {
    const times = builds
      .map(build => ({build, time:buildTimeMs(build)}))
      .filter(row => Number.isFinite(row.time));
    if (!times.length) return builds.slice(0, Math.min(14, builds.length));
    const refTime = Math.max(...times.map(row => row.time));
    const cutoff = refTime - days * 24 * 60 * 60 * 1000;
    const filtered = times.filter(row => row.time >= cutoff).map(row => row.build);
    return filtered.length ? filtered : builds.slice(0, Math.min(14, builds.length));
  }

  function renderGatingProgressChart(box, capacity, analytics, target, proposals, targets) {
    const builds = upstreamNightlyBuilds(analytics);
    if (!builds.length) return;
    let activeWindow = DEFAULT_GATING_PROGRESS_WINDOW;
    let snapshots = [];
    const readyColor = '#2dd4bf';

    function snapshotForBuild(build) {
      const rows = buildGatingRows(capacity, null, analytics, build);
      const gated = rows.filter(r => (r.sourceJobs || []).length > 0).length;
      const green = rows.filter(r => r.isGreen).length;
      const internalBuild = internalBuildForGatingSnapshot(analytics, build);
      const still = buildStillToGateSnapshot(
        target,
        rows,
        proposalsVisibleByBuild(proposals, build),
        build,
        internalBuild,
        targets
      );
      const canonicalRows = buildCanonicalTargetRows(targets, rows, build, internalBuild);
      const hasCanonicalTargets = canonicalRows.length > 0;
      const canonicalGreenRows = canonicalRows.filter(r => ['target_gated_green','target_ready'].includes(r.status.key));
      const canonicalFailingRows = canonicalRows.filter(r => r.status.key === 'target_failing');
      const canonicalNoSignalRows = canonicalRows.filter(r => ['target_todo','target_unknown'].includes(r.status.key));
      return {
        build,
        rows,
        gated,
        green,
        proposed:still.proposed,
        gatedPlusProposed:gated + still.proposed,
        internalGreenStillToGate:hasCanonicalTargets ? Math.max(0, canonicalGreenRows.length - green) : still.internalGreenStillToGate,
        internalFailingStillToGate:hasCanonicalTargets ? canonicalFailingRows.length : still.internalFailingStillToGate,
        internalFailingRows:hasCanonicalTargets ? canonicalFailingRows : still.internalFailingRows.map(r => ({...r, section:internalFailureSection(r)})),
        internalNoSignalStillToGate:hasCanonicalTargets ? canonicalNoSignalRows.length : still.internalNoSignalStillToGate,
        internalNoSignalRows:hasCanonicalTargets ? canonicalNoSignalRows : still.internalNoSignalRows.map(r => ({...r, section:internalNoSignalSection(r)})),
        greenReady:hasCanonicalTargets ? canonicalGreenRows.length : green + still.internalGreenStillToGate,
        stillToGateRows:hasCanonicalTargets ? canonicalRows.filter(r => r.canonicalSignals?.gating !== 'green') : still.stillToGateRows,
      };
    }

    function snapshotsForWindow(windowKey) {
      const entry = GATING_PROGRESS_WINDOWS.find(w => w.key === windowKey) || GATING_PROGRESS_WINDOWS[0];
      return gatingProgressBuilds(builds, entry.days).reverse().map(snapshotForBuild);
    }

    function yMaxFor(nextSnapshots) {
      const values = nextSnapshots.flatMap(s => [s.gated, s.green, s.greenReady, s.gatedPlusProposed, s.internalFailingStillToGate, s.internalNoSignalStillToGate]);
      return Math.max(target, ...values, 1) + 5;
    }

    const wrap = h('div',{style:{background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'8px',padding:'16px',marginBottom:'14px'}});
    const header = h('div',{style:{display:'flex',alignItems:'center',justifyContent:'space-between',gap:'12px',flexWrap:'wrap',marginBottom:'6px'}});
    header.append(h('h4',{text:'Gating Progress Over Time',style:{fontSize:'15px',margin:'0'}}));
    const controls = h('div',{style:{display:'flex',alignItems:'center',gap:'6px',flexWrap:'wrap'}});
    controls.append(h('span',{text:'Window:',style:{color:C.m,fontSize:'12px',fontWeight:'700',textTransform:'uppercase',letterSpacing:'.4px'}}));
    const buttons = {};
    for (const entry of GATING_PROGRESS_WINDOWS) {
      const btn = h('button',{text:entry.label,style:{
        background:entry.key===activeWindow?C.b:C.bd,
        border:'none',
        color:C.t,
        padding:'5px 10px',
        borderRadius:'4px',
        cursor:'pointer',
        fontSize:'12px',
        fontFamily:'inherit',
        fontWeight:entry.key===activeWindow?'700':'600',
      }});
      buttons[entry.key] = btn;
      controls.append(btn);
    }
    header.append(controls);
    wrap.append(header);
    const summary = h('p',{style:{color:C.m,fontSize:'12px',margin:'0 0 10px'}});
    wrap.append(summary);
    const canvas = h('canvas',{style:{maxHeight:'260px'}});
    wrap.append(canvas);
    box.append(wrap);

    if (typeof Chart !== 'function') {
      summary.textContent = 'Chart renderer is unavailable; the current cards and clickable lists above still show the gating state.';
      canvas.style.display = 'none';
      return;
    }

    snapshots = snapshotsForWindow(activeWindow);
    const chart = new Chart(canvas,{type:'line',data:{
      labels:snapshots.map(s => s.build.date || String(s.build.created_at || '').slice(0,10)),
      datasets:[
        {label:'Gated groups',data:snapshots.map(s => s.gated),borderColor:C.b,backgroundColor:C.b+'33',tension:.25,pointRadius:4,fill:false},
        {label:'Green gated',data:snapshots.map(s => s.green),borderColor:C.g,backgroundColor:C.g+'33',tension:.25,pointRadius:4,fill:false},
        {label:'Green incl. amd-ci',data:snapshots.map(s => s.greenReady),borderColor:readyColor,backgroundColor:readyColor+'33',tension:.25,pointRadius:4,fill:false},
        {label:'Failing target gaps',data:snapshots.map(s => s.internalFailingStillToGate),borderColor:C.r,backgroundColor:C.r+'33',borderDash:[4,3],tension:.25,pointRadius:4,fill:false},
        {label:'No amd-ci signal',data:snapshots.map(s => s.internalNoSignalStillToGate),borderColor:C.m,backgroundColor:C.m+'33',borderDash:[2,4],tension:.25,pointRadius:4,fill:false},
        {label:'Gated + proposed',data:snapshots.map(s => s.gatedPlusProposed),borderColor:PROPOSAL_COLOR,backgroundColor:PROPOSAL_COLOR+'33',tension:.25,pointRadius:4,fill:false},
        {label:'Target',data:snapshots.map(() => target),borderColor:C.m,borderDash:[6,4],pointRadius:0,fill:false},
      ]},options:{responsive:true,plugins:{legend:{labels:{color:C.t}},tooltip:{callbacks:{afterTitle:items => {
        const snap = snapshots[items[0]?.dataIndex ?? 0];
        return snap?.build?.number ? `ci #${snap.build.number}` : '';
      }}}},onClick:(evt, elements, chart) => {
        const points = chart.getElementsAtEventForMode(evt, 'nearest', {intersect:false}, true);
        if (!points.length) return;
        const snap = snapshots[points[0].index];
        if (points[0].datasetIndex === 3) {
          showGatingPathOverlay(`Failing AMD target gaps: ${sourceBuildLabel(snap.build)}`, snap.internalFailingRows, snap.build);
          return;
        }
        if (points[0].datasetIndex === 4) {
          showGatingPathOverlay(`No amd-ci signal: ${sourceBuildLabel(snap.build)}`, snap.internalNoSignalRows, snap.build);
          return;
        }
        showGatingSourceOverlay(`Nightly Source: ${sourceBuildLabel(snap.build)}`, snap.rows.filter(r => (r.sourceJobs || []).length > 0), snap.build, Math.max(0, target - snap.gated));
      },scales:{
        y:{min:0,max:yMaxFor(snapshots),ticks:{color:C.m,precision:0},grid:{color:C.bd},title:{display:true,text:'AMD mirror groups',color:C.m}},
        x:{ticks:{color:C.m},grid:{color:C.bd}},
      }}
    });

    function refreshWindow(windowKey) {
      activeWindow = windowKey;
      snapshots = snapshotsForWindow(activeWindow);
      const entry = GATING_PROGRESS_WINDOWS.find(w => w.key === activeWindow) || GATING_PROGRESS_WINDOWS[0];
      Object.entries(buttons).forEach(([key, btn]) => {
        btn.style.background = key === activeWindow ? C.b : C.bd;
        btn.style.fontWeight = key === activeWindow ? '700' : '600';
      });
      summary.textContent = `${entry.label} view, ${snapshots.length} nightly points. Blue is gated upstream; green is gated and passing; teal adds not-yet-gated groups that are green in amd-ci; red is not-yet-gated groups failing or soft-failing in amd-ci; dotted gray is not-yet-gated groups with no amd-ci signal; orange adds open proposed mirror PRs; dashed gray is the ${fmtInt(target)}-group target.`;
      chart.data.labels = snapshots.map(s => s.build.date || String(s.build.created_at || '').slice(0,10));
      chart.data.datasets[0].data = snapshots.map(s => s.gated);
      chart.data.datasets[1].data = snapshots.map(s => s.green);
      chart.data.datasets[2].data = snapshots.map(s => s.greenReady);
      chart.data.datasets[3].data = snapshots.map(s => s.internalFailingStillToGate);
      chart.data.datasets[4].data = snapshots.map(s => s.internalNoSignalStillToGate);
      chart.data.datasets[5].data = snapshots.map(s => s.gatedPlusProposed);
      chart.data.datasets[6].data = snapshots.map(() => target);
      chart.options.scales.y.max = yMaxFor(snapshots);
      chart.update();
    }

    for (const [key, btn] of Object.entries(buttons)) btn.onclick = () => refreshWindow(key);
    refreshWindow(activeWindow);
  }

  function renderMiniMetric(label, value, sub, color, opts={}) {
    const card = h('div',{style:{background:C.bg,border:`1px solid ${C.bd}`,borderTop:`3px solid ${color}`,borderRadius:'8px',padding:'14px 16px',minWidth:'0',cursor:opts.onClick?'pointer':'default'}});
    if (opts.onClick) {
      card.onclick = opts.onClick;
      card.onmouseenter = () => { card.style.boxShadow = '0 4px 12px rgba(0,0,0,.25)'; };
      card.onmouseleave = () => { card.style.boxShadow = ''; };
      card.title = opts.title || 'Open source list';
    }
    card.append(h('div',{text:label,style:{fontSize:'12px',color:C.m,textTransform:'uppercase',letterSpacing:'.4px',marginBottom:'6px'}}));
    card.append(h('div',{text:String(value),style:{fontSize:'30px',lineHeight:'1',fontWeight:'800',color}}));
    if (sub) card.append(h('div',{html:sub,style:{fontSize:'12px',color:C.m,marginTop:'7px',lineHeight:'1.35'}}));
    return card;
  }

  function renderGatingExecutive(box, capacity, matrix, health, analytics, proposals, targets, targetCandidates) {
    const sourceBuild = upstreamNightlyBuilds(analytics)[0] || null;
    const internalBuild = internalAmdBuilds(analytics)[0] || null;
    const rows = buildGatingRows(capacity, matrix, analytics, sourceBuild);
    const configuredTarget = Number(targets?.summary?.target_group_count || capacity?.assumptions?.default_theoretical_groups || 125);
    const gatedRows = rows.filter(r => (r.sourceJobs || []).length > 0);
    const greenRows = rows.filter(r => r.isGreen);
    const gatedNotGreenRows = gatedRows.filter(r => !r.isGreen);
    const current = gatedRows.length;
    const green = greenRows.length;
    const attention = rows.filter(r => ['failing','soft_fail'].includes(r.status.key)).length;
    const notObserved = rows.filter(r => r.status.key === 'not_observed').length;
    const optional = rows.filter(r => r.group.optional).length;
    const sourceLabel = sourceBuild ? sourceBuildLabel(sourceBuild) : 'vllm/ci nightly main';
    const allProposedRows = buildProposedRows(proposals, sourceBuild, []);
    const canonicalRows = buildCanonicalTargetRows(targets, rows, sourceBuild, internalBuild);
    const hasCanonicalTargets = canonicalRows.length > 0;
    const extraGatedRows = hasCanonicalTargets
      ? gatedRows.filter(row => !rowMatchesTargetList(row, targets)).map(row => ({...row, section:'Gated AMD label outside reviewed target list'}))
      : [];
    const target = hasCanonicalTargets
      ? Math.max(configuredTarget, canonicalRows.length + extraGatedRows.length)
      : configuredTarget;
    const remaining = Math.max(0, target - current);
    const still = buildStillToGateSnapshot(target, rows, proposals, sourceBuild, internalBuild, targets);
    const canonicalGatedGreenRows = canonicalRows.filter(r => r.status.key === 'target_gated_green');
    const canonicalReadyRows = canonicalRows.filter(r => r.status.key === 'target_ready');
    const canonicalGreenRows = [...canonicalGatedGreenRows, ...canonicalReadyRows];
    const canonicalFailingRows = canonicalRows.filter(r => r.status.key === 'target_failing');
    const canonicalTodoRows = canonicalRows.filter(r => r.status.key === 'target_todo');
    const canonicalInfraRows = canonicalRows.filter(r => r.status.key === 'target_infra');
    const canonicalNotYetRows = canonicalRows.filter(r => r.status.key !== 'target_gated_green');
    const proposedRows = allProposedRows;
    const targetAuditSummary = targetCandidates?.summary || {};
    const proposed = still.proposed;
    const unplanned = Math.max(0, remaining - proposedRows.length);
    const stillToGateRows = hasCanonicalTargets ? canonicalNotYetRows : still.stillToGateRows;
    const internalGreenStillToGate = still.internalGreenStillToGate;
    const internalHardFailStillToGate = still.internalHardFailStillToGate;
    const internalFailingStillToGate = still.internalFailingStillToGate;
    const internalSoftFailStillToGate = still.internalSoftFailStillToGate;
    const internalFailingRows = still.internalFailingRows.map(r => ({...r, section:internalFailureSection(r)}));
    const internalNoSignalStillToGate = still.internalNoSignalStillToGate;
    const internalNoSignalRows = still.internalNoSignalRows.map(r => ({...r, section:internalNoSignalSection(r)}));
    const greenReadyRows = [
      ...greenRows.map(r => ({...r, section:'Gated green upstream'})),
      ...still.internalGreenRows.map(r => ({...r, section:'Green in amd-ci, not gated upstream'})),
    ];
    const effectiveGreenRows = hasCanonicalTargets ? [...canonicalGreenRows, ...extraGatedRows.filter(r => r.isGreen)] : greenReadyRows;
    const greenReady = effectiveGreenRows.length;
    const pathRows = hasCanonicalTargets ? [
      ...canonicalGatedGreenRows.map(r => ({...r, section:'Gated/proposed green'})),
      ...extraGatedRows,
      ...canonicalNotYetRows,
    ] : [
      ...greenRows.map(r => ({...r, section:'Gated AMD labels'})),
      ...gatedNotGreenRows.map(r => ({...r, section:'Gated but not green'})),
      ...stillToGateRows,
    ];
    const archEntries = Object.entries(gatedRows.reduce((acc, r) => {
      acc[r.arch] = (acc[r.arch] || 0) + 1;
      return acc;
    }, {})).sort((a,b)=>archSortValue(a[0])-archSortValue(b[0]));
    const archSummary = archEntries.map(([arch,count]) => chipHtml(`${archLabel(arch)} ${fmtInt(count)}`, archColor(arch))).join(' ');

    const section = h('section',{style:{marginBottom:'22px'}});
    section.append(h('h3',{text:'Upstream CI signal',style:{fontSize:'18px',margin:'0 0 6px'}}));
    section.append(h('p',{text:'Only AMD mirror labels from .buildkite/test_areas are counted here, matched against the vllm/ci nightly build on main at 1:00 AM Central.',style:{color:C.m,fontSize:'13px',margin:'0 0 14px',maxWidth:'980px'}}));

    if (!capacity || !analytics?.ci?.builds?.length) {
      section.append(h('div',{text:'Waiting for capacity_monitor.json and gating_nightlies.json to publish the upstream nightly AMD gating view.',style:{color:C.y,background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'8px',padding:'12px 14px'}}));
      box.append(section);
      return;
    }

    const cardGrid = h('div',{style:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(190px,1fr))',gap:'12px',marginBottom:'14px'}});
    cardGrid.append(
      renderMiniMetric('AMD labels gated', `${fmtInt(current)} / ${fmtInt(target)}`, `${pct(current / Math.max(1, target), 0)} of target from ${sourceLabel}`, C.b, {
        onClick: () => showGatingPathOverlay('AMD labels gated + still to gate', pathRows, sourceBuild),
      }),
      renderMiniMetric('Green now', `${fmtInt(green)} / ${fmtInt(current)}`, `${sourceLabel}; click for green sources`, C.g, {
        onClick: () => showGatingSourceOverlay('Green AMD labels', greenRows, sourceBuild),
      }),
      renderMiniMetric('Green of target', `${fmtInt(greenReady)} / ${fmtInt(target)}`, hasCanonicalTargets ? `${fmtInt(canonicalGatedGreenRows.length + extraGatedRows.filter(r => r.isGreen).length)} gated/proposed green + ${fmtInt(canonicalReadyRows.length)} passing ready to gate` : `${fmtInt(green)} gated + ${fmtInt(internalGreenStillToGate)} not gated yet green in ${internalBuildLabel(internalBuild)}`, '#2dd4bf', {
        onClick: () => showGatingPathOverlay('AMD target coverage', hasCanonicalTargets ? pathRows : effectiveGreenRows, sourceBuild),
      }),
      renderMiniMetric('Failing target gaps', fmtInt(hasCanonicalTargets ? canonicalFailingRows.length : internalFailingStillToGate), hasCanonicalTargets ? `${fmtInt(canonicalFailingRows.length)} failing in the canonical target list` : `${fmtInt(internalHardFailStillToGate)} hard fail + ${fmtInt(internalSoftFailStillToGate)} soft-fail in ${internalBuildLabel(internalBuild)}`, C.r, {
        onClick: () => showGatingPathOverlay('Failing AMD target gaps', hasCanonicalTargets ? canonicalFailingRows : internalFailingRows, sourceBuild),
      }),
      renderMiniMetric('No amd-ci signal', fmtInt(hasCanonicalTargets ? canonicalTodoRows.length : internalNoSignalStillToGate), hasCanonicalTargets ? `${fmtInt(canonicalTodoRows.length)} todo / no amd-ci signal in target list` : `not matched in ${internalBuildLabel(internalBuild)}`, C.m, {
        onClick: () => showGatingPathOverlay('No amd-ci signal', hasCanonicalTargets ? canonicalTodoRows : internalNoSignalRows, sourceBuild),
      }),
      renderMiniMetric('Infra blocked', fmtInt(hasCanonicalTargets ? canonicalInfraRows.length : 0), hasCanonicalTargets ? `${fmtInt(canonicalInfraRows.length)} infra-blocked target groups` : 'canonical target list unavailable', C.m, {
        onClick: () => showGatingPathOverlay('Infra blocked target groups', canonicalInfraRows, sourceBuild),
      }),
      renderMiniMetric('Proposed for gating', fmtInt(proposedRows.length), `${fmtInt(proposals?.summary?.proposal_pr_count || 0)} tracked PRs adding AMD mirrors`, PROPOSAL_COLOR, {
        onClick: () => showProposedGatingOverlay(proposedRows, sourceBuild, proposals),
      }),
      renderMiniMetric('Target list audit', fmtInt(targetAuditSummary.new_candidate_count || 0), `${fmtInt(targetAuditSummary.likely_duplicate_count || 0)} likely duplicates, ${fmtInt(targetAuditSummary.excluded_count || 0)} excluded`, C.b, {
        onClick: () => showTargetCandidateAuditOverlay(targetCandidates),
      }),
      renderMiniMetric('Still to gate', fmtInt(remaining), hasCanonicalTargets ? `${fmtInt(canonicalReadyRows.length)} ready, ${fmtInt(canonicalFailingRows.length)} failing, ${fmtInt(canonicalTodoRows.length)} todo, ${fmtInt(canonicalInfraRows.length)} infra-blocked` : `${fmtInt(proposed)} proposed, ${fmtInt(internalGreenStillToGate)} green, ${fmtInt(internalFailingStillToGate)} failing, ${fmtInt(internalNoSignalStillToGate)} no amd-ci signal`, remaining ? C.y : C.g, {
        onClick: () => showGatingPathOverlay('Still to gate', stillToGateRows, sourceBuild),
      }),
      renderMiniMetric('Architecture footprint', fmtInt(new Set(gatedRows.map(r => r.arch)).size), archSummary, C.m, {
        onClick: () => showArchitectureFootprintOverlay(gatedRows, sourceBuild),
      })
    );
    section.append(cardGrid);
    section.append(h('p',{text:hasCanonicalTargets ? `Runtime target accounting: ${fmtInt(canonicalGatedGreenRows.length)} gated/proposed green + ${fmtInt(canonicalReadyRows.length)} passing ready to gate + ${fmtInt(canonicalFailingRows.length)} failing + ${fmtInt(canonicalTodoRows.length)} todo/no signal + ${fmtInt(canonicalInfraRows.length)} infra-blocked${extraGatedRows.length ? ` + ${fmtInt(extraGatedRows.length)} currently gated outside the reviewed target list` : ''} = ${fmtInt(target)} active groups. Live upstream and amd-ci evidence is used before the reviewed fallback columns.` : `Target accounting: ${fmtInt(greenReady)} green + ${fmtInt(internalFailingStillToGate)} failing or soft-failing + ${fmtInt(internalNoSignalStillToGate)} no amd-ci signal = ${fmtInt(greenReady + internalFailingStillToGate + internalNoSignalStillToGate)} / ${fmtInt(target)} target groups.`,style:{color:C.m,fontSize:'12px',margin:'-4px 0 14px'}}));

    const chartSlot = h('div',{},[
      h('p',{text:'Loading gating progress...',style:{color:C.m,fontSize:'12px',margin:'0 0 14px'}})
    ]);
    section.append(chartSlot);
    setTimeout(() => {
      if (!chartSlot.isConnected) return;
      chartSlot.innerHTML = '';
      renderGatingProgressChart(chartSlot, capacity, analytics, target, proposals, targets);
    }, 0);

    const progress = h('div',{style:{background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'8px',padding:'14px 16px',marginBottom:'14px'}});
    progress.append(h('strong',{text:`Path to ${fmtInt(target)} gated groups`,style:{display:'block',fontSize:'14px',marginBottom:'8px'}}));
    const barHost = h('div',{style:{height:'16px',display:'flex',borderRadius:'4px',overflow:'hidden',background:C.bd}});
    const greenPct = Math.max(0, Math.min(100, (hasCanonicalTargets ? effectiveGreenRows.length : green) / Math.max(1, target) * 100));
    const failingPct = hasCanonicalTargets ? Math.max(0, Math.min(100 - greenPct, canonicalFailingRows.length / Math.max(1, target) * 100)) : 0;
    const todoPct = hasCanonicalTargets ? Math.max(0, Math.min(100 - greenPct - failingPct, canonicalTodoRows.length / Math.max(1, target) * 100)) : 0;
    const infraPct = hasCanonicalTargets ? Math.max(0, Math.min(100 - greenPct - failingPct - todoPct, canonicalInfraRows.length / Math.max(1, target) * 100)) : 0;
    const definedPct = hasCanonicalTargets ? 0 : Math.max(0, Math.min(100 - greenPct, (current - green) / Math.max(1, target) * 100));
    const proposedPct = hasCanonicalTargets ? 0 : Math.max(0, Math.min(100 - greenPct - definedPct, proposed / Math.max(1, target) * 100));
    if (greenPct) barHost.append(h('div',{title:'Green/ready target groups',style:{width:greenPct+'%',background:C.g}}));
    if (failingPct) barHost.append(h('div',{title:'Failing target gaps',style:{width:failingPct+'%',background:C.r}}));
    if (todoPct) barHost.append(h('div',{title:'Todo / no amd-ci signal',style:{width:todoPct+'%',background:C.y}}));
    if (infraPct) barHost.append(h('div',{title:'Infra blocked',style:{width:infraPct+'%',background:C.m}}));
    if (definedPct) barHost.append(h('div',{title:'Defined but not green or not observed',style:{width:definedPct+'%',background:C.y}}));
    if (proposedPct) barHost.append(h('div',{title:'Proposed for gating',style:{width:proposedPct+'%',background:PROPOSAL_COLOR}}));
    barHost.append(h('div',{title:'Not gated yet',style:{flex:'1',background:C.bd}}));
    progress.append(barHost);
    progress.append(h('div',{style:{display:'flex',gap:'16px',flexWrap:'wrap',marginTop:'10px'}}, hasCanonicalTargets ? [
      legendItem(C.g, `${fmtInt(canonicalGreenRows.length)} green/ready`),
      legendItem(C.r, `${fmtInt(canonicalFailingRows.length)} failing`),
      legendItem(C.y, `${fmtInt(canonicalTodoRows.length)} todo/no signal`),
      legendItem(C.m, `${fmtInt(canonicalInfraRows.length)} infra-blocked`),
    ] : [
      legendItem(C.g, `${fmtInt(green)} green`),
      legendItem(C.y, `${fmtInt(current - green)} gated but not green`),
      legendItem(PROPOSAL_COLOR, `${fmtInt(proposed)} proposed for gating`),
      legendItem(C.bd, `${fmtInt(unplanned)} not yet proposed`),
    ]));
    section.append(progress);

    const byArch = {};
    const byArea = {};
    for (const r of rows) {
      const arch = r.arch;
      const areaName = r.group.area || 'Other';
      if (!byArch[arch]) byArch[arch] = {total:0, gated:0, green:0, attention:0, notObserved:0, queues:new Set()};
      if (!byArea[areaName]) byArea[areaName] = {total:0, gated:0, green:0, attention:0, notObserved:0, arches:new Set()};
      for (const bucket of [byArch[arch], byArea[areaName]]) {
        bucket.total += 1;
        if ((r.sourceJobs || []).length) bucket.gated += 1;
        if (r.isGreen) bucket.green += 1;
        if (['failing','soft_fail'].includes(r.status.key)) bucket.attention += 1;
        if (r.status.key === 'not_observed') bucket.notObserved += 1;
      }
      byArch[arch].queues.add(r.group.queue || r.group.device || arch);
      byArea[areaName].arches.add(archLabel(arch));
    }

    const tables = h('div',{style:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(320px,1fr))',gap:'14px',marginBottom:'14px'}});
    tables.append(summaryTable('By Architecture', ['Architecture','Gated','Green','Needs signal','Queues'],
      Object.entries(byArch).sort((a,b)=>b[1].total-a[1].total).map(([arch,d]) => [
        archLabel(arch), fmtInt(d.gated), fmtInt(d.green), fmtInt(d.attention + d.notObserved), [...d.queues].sort().join(', ')
      ])
    ));
    tables.append(summaryTable('By Test Area', ['Area','Gated','Green','Needs signal','Architectures'],
      Object.entries(byArea).sort((a,b)=>b[1].total-a[1].total).map(([name,d]) => [
        name, fmtInt(d.gated), fmtInt(d.green), fmtInt(d.attention + d.notObserved), [...d.arches].sort().join(', ')
      ])
    ));
    section.append(tables);

    const det = h('details',{open:true,style:{background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'8px',marginBottom:'10px'}});
    det.append(h('summary',{html:`Our AMD mirror test groups <span style="color:${C.m}">(${fmtInt(current)} in ${sourceBuild ? `ci #${sourceBuild.number}` : 'latest ci nightly'}, ${fmtInt(optional)} optional, ${fmtInt(notObserved)} not observed)</span>`,style:{padding:'12px 16px',cursor:'pointer',fontSize:'14px',fontWeight:'700'}}));
    const tbl = h('table',{style:{width:'100%',borderCollapse:'collapse',fontSize:'13px'}});
    tbl.append(h('thead',{},[h('tr',{},[
      h('th',{text:'Status',style:ts()}),
      h('th',{text:'Test Group',style:ts()}),
      h('th',{text:'Architecture',style:ts('center')}),
      h('th',{text:'Area',style:ts()}),
      h('th',{text:'Queue',style:ts()}),
      h('th',{text:'Buildkite',style:ts()}),
      h('th',{text:'Required',style:ts('center')}),
      h('th',{text:'YAML',style:ts()}),
    ])]));
    const tb = h('tbody');
    const sortedRows = [...rows].sort((a,b) => (a.status.rank-b.status.rank) || a.arch.localeCompare(b.arch) || (a.group.area || '').localeCompare(b.group.area || '') || (a.group.label || '').localeCompare(b.group.label || ''));
    for (const r of sortedRows) {
      const tr = h('tr',{style:{borderBottom:`1px solid ${C.bd}`}});
      const statusCell = h('td',{style:td()});
      statusCell.append(h('span',{text:r.status.label,style:{display:'inline-block',minWidth:'84px',textAlign:'center',padding:'3px 8px',borderRadius:'4px',border:`1px solid ${r.status.color}55`,background:r.status.color+'18',color:r.status.color,fontWeight:'700',fontSize:'12px'}}));
      tr.append(statusCell);
      const nameCell = h('td',{style:td()});
      if (r.cell?.latest_url) {
        nameCell.append(h('a',{text:r.group.label,href:r.cell.latest_url,target:'_blank',rel:'noopener',style:{color:C.b,textDecoration:'none'}}));
      } else {
        nameCell.textContent = r.group.label;
      }
      tr.append(nameCell);
      tr.append(h('td',{text:archLabel(r.arch),style:tdo('center')}));
      tr.append(h('td',{text:r.group.area || 'Other',style:td()}));
      tr.append(h('td',{text:r.group.queue || r.group.device || '',style:td()}));
      const bkCell = h('td',{style:td()});
      if (r.cell?.latest_url) {
        bkCell.append(h('a',{text:r.sourceJobs?.length > 1 ? `${r.sourceJobs.length} jobs` : `ci #${r.sourceBuild?.number || '?'}`,href:r.cell.latest_url,target:'_blank',rel:'noopener',style:{color:C.b,textDecoration:'none'}}));
      } else {
        bkCell.append(h('span',{text:'not in nightly',style:{color:C.m}}));
      }
      tr.append(bkCell);
      tr.append(h('td',{text:r.group.optional ? 'Optional' : 'Required',style:{...tdo('center'),color:r.group.optional ? C.m : C.t}}));
      tr.append(h('td',{text:r.group.yaml_file || '',style:{...td(),color:C.m,fontSize:'12px'}}));
      tb.append(tr);
    }
    tbl.append(tb);
    det.append(h('div',{style:{overflowX:'auto',padding:'0 12px 12px'}},[tbl]));
    section.append(det);

    const foot = h('p',{style:{color:C.m,fontSize:'12px',margin:'8px 0 0'}});
    foot.textContent = `${fmtInt(attention)} mirror labels have an explicit failing/soft-fail observation in ${sourceBuild ? `vllm/ci #${sourceBuild.number}` : 'the latest vllm/ci nightly'}; ${fmtInt(notObserved)} are defined in YAML but were not observed in that nightly.`;
    section.append(foot);
    box.append(section);
  }

  function summaryTable(title, headers, rows) {
    const wrap = h('div',{style:{background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'8px',padding:'12px 14px',minWidth:'0'}});
    wrap.append(h('h4',{text:title,style:{fontSize:'14px',margin:'0 0 10px'}}));
    const tbl = h('table',{style:{width:'100%',borderCollapse:'collapse',fontSize:'13px'}});
    tbl.append(h('thead',{},[h('tr',{},headers.map(head => h('th',{text:head,style:ts(head === 'Labels' || head === 'Gated' || head === 'Green' || head === 'Needs signal' ? 'center' : 'left')})))]));
    const tb = h('tbody');
    for (const row of rows) {
      tb.append(h('tr',{},row.map((cell,idx) => h('td',{text:String(cell),style:idx>0 && idx<4 ? tdo('center') : td()}))));
    }
    tbl.append(tb);
    wrap.append(tbl);
    return wrap;
  }

  // ═══════════════════════ METRIC CARDS ROW ═══════════════════════
  function renderMetrics(box,health,parity) {
    if(!health?.amd?.latest_build) return;
    const a=health.amd.latest_build;
    const u=health.upstream?.latest_build;

    const row=h('div',{style:{display:'grid',gridTemplateColumns:'repeat(4,1fr)',gap:'12px',marginBottom:'20px'}});

    // Clickable card — shows overlay with details
    // bigHtml can be a string (text) or raw HTML
    const card=(label,big,sub,color,onclick,{bigHtml}={})=>{
      const c=h('div',{style:{background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'8px',padding:'16px 20px',borderTop:`3px solid ${color}`,cursor:'pointer',transition:'transform .15s,box-shadow .15s'}});
      c.onmouseenter=()=>{c.style.transform='translateY(-2px)';c.style.boxShadow='0 4px 12px rgba(0,0,0,.3)'};
      c.onmouseleave=()=>{c.style.transform='';c.style.boxShadow=''};
      if(onclick) c.onclick=onclick;
      c.append(h('div',{text:label,style:{fontSize:'clamp(12px,0.85vw,16px)',color:C.m,textTransform:'uppercase',letterSpacing:'.5px',marginBottom:'6px'}}));
      if(bigHtml) c.append(h('div',{html:bigHtml,style:{fontSize:'clamp(28px,2.2vw,42px)',fontWeight:'800',lineHeight:'1.1'}}));
      else c.append(h('div',{text:String(big),style:{fontSize:'clamp(28px,2.2vw,42px)',fontWeight:'800',color,lineHeight:'1.1'}}));
      if(sub)c.append(h('div',{html:sub,style:{fontSize:'clamp(12px,0.85vw,16px)',color:C.m,marginTop:'6px'}}));
      return c
    };

    // Use merged group counts
    const mergedGroups=parity?.job_groups?(typeof mergeShardedGroups==='function'?mergeShardedGroups(parity.job_groups):parity.job_groups):[];
    // Top-card coverage counts should remain group-level so:
    // common + AMD-only == AMD unique test groups.
    // Family-merged parity rows are still used in the detailed parity sections.
    const coverageGroups=mergedGroups;
    const mergedAmdGroups=mergedGroups.filter(g=>g.amd).length;
    // A group is "failing" if it has ANY hard-fail signal — both pytest
    // ``failed`` assertions AND job-level ``error`` (timeouts, crashes,
    // infra failures that produced no pytest output). The old filter only
    // counted ``failed`` and silently dropped timed-out soft-fail jobs
    // (e.g. build 7791's "Basic Models Tests (Other)" — 2 retries, 3h
    // timeout, soft_failed=true → analyzer records error=1 not failed=1).
    // Buildkite displays both as FAIL/soft-FAIL, so the dashboard must too.
    const _hasFail=g=>g&&((g.failed||0)>0||(g.error||0)>0);
    const failingGroups=mergedGroups.filter(g=>_hasFail(g.amd)||_hasFail(g.upstream));
    const canceledGroups=mergedGroups.filter(g=>g.amd&&!_hasFail(g.amd)&&(g.amd.canceled||0)>0&&(g.amd.passed||0)===0);
    const passingGroups=mergedGroups.filter(g=>g.amd&&!_hasFail(g.amd)&&!((g.amd.canceled||0)>0&&(g.amd.passed||0)===0));

    // AMD runtime card -> opens build link
    const amdGroupCount=mergedAmdGroups||a.unique_test_groups||0;
    const sfInfo=a.jobs_soft_failed?` &bull; ${a.jobs_soft_failed} soft-failed`:'';
    const runInfo=a.is_running?` &bull; <span style="color:${C.y}">&#9888; running</span>`:'';
    row.append(card('amd-ci runtime',pct(a.pass_rate,1),`Build #${a.build_number} &bull; ${amdGroupCount} runtime groups${sfInfo}${runInfo}`,rc(a.pass_rate),
      ()=>{ if(a.build_url) window.open(a.build_url,'_blank'); }));

    // Test Failures card -> overlay with failing groups (split AMD / upstream)
    // Use totals from the groups that actually appear in the overlay (parity excludes non-GPU groups)
    // Include errors alongside failures so timed-out/crashed jobs (analyzer
    // records error=1) contribute to the headline number, mirroring the
    // failingGroups filter above.
    const overlayAmdFail=failingGroups.reduce((s,g)=>s+(g.amd?((g.amd.failed||0)+(g.amd.error||0)):0),0);
    const overlayUpFail=failingGroups.reduce((s,g)=>s+(g.upstream?((g.upstream.failed||0)+(g.upstream.error||0)):0),0);
    const failBigHtml=`<span style="color:${C.r}">${overlayAmdFail}</span>`+(u?`<span style="color:${C.m};font-size:clamp(16px,1.2vw,24px);font-weight:400"> / </span><span style="color:${C.b}">${overlayUpFail}</span>`:'');
    const failSub=`<span style="color:${C.r}">AMD</span>${u?` &bull; <span style="color:${C.b}">Upstream</span>`:''} &bull; ${failingGroups.length} groups`;
    row.append(card('Test Failures',null,failSub,C.r,
      ()=>{
        if(!failingGroups.length){const el=document.querySelector('h3[data-parity-title]');if(el)el.scrollIntoView({behavior:'smooth'});return}
        showGroupOverlay_health('Failing Tests',failingGroups,C.r,overlayAmdFail,overlayUpFail);
      },{bigHtml:failBigHtml}));

    // Test groups card -> overlay with ALL groups (failing first, then passing)
    const allAmdGroups=mergedGroups.filter(g=>g.amd);
    const amdTotalGroups=a.unique_test_groups||mergedAmdGroups||0;
    if(amdTotalGroups) {
      const amdPassAny=a.test_groups_passing_or ?? passingGroups.length;
      const amdPassAll=a.test_groups_passing_all ?? passingGroups.length;
      const amdPartial=a.test_groups_partial ?? Math.max(0, amdPassAny-amdPassAll);
      const amdFailAll=Math.max(0, amdTotalGroups-amdPassAny);
      const groupRate=amdPassAny/amdTotalGroups;
      const sub=[
        `${amdPassAll} strict all-HW`,
        amdPartial>0?`<span style="color:${C.y}">${amdPartial} partial</span>`:'',
        amdFailAll>0?`<span style="color:${C.r}">${amdFailAll} failing</span>`:'',
      ].filter(Boolean).join(' &bull; ');
      row.append(card('Test Groups',`${amdPassAny}/${amdTotalGroups}`,sub,rc(groupRate),
        ()=>showGroupOverlay_health('All Test Groups (AMD)',allAmdGroups,C.b,null,null,true)));
    } else {
      row.append(card('Test Groups',mergedAmdGroups||a.test_groups,`${a.jobs_passed||0} jobs passed`,C.b,
        ()=>showGroupOverlay_health('All Test Groups (AMD)',allAmdGroups,C.b,null,null,true)));
    }

    // Parity card -> overlay with 3-tab parity breakdown
    if(mergedGroups.length) {
      const hasUpstreamCoverage=g=>!!g.upstream||g.status==='upstream_only'||g.backfilled||g.hw_backfilled;
      const bothGroups=coverageGroups.filter(g=>g.amd&&g.upstream);
      const aOnlyGroups=coverageGroups.filter(g=>g.amd&&!g.upstream);
      const uOnlyGroups=coverageGroups.filter(g=>!g.amd&&hasUpstreamCoverage(g));
      row.append(card('Coverage Parity',`${bothGroups.length} common`,`${aOnlyGroups.length} AMD-only &bull; ${uOnlyGroups.length} upstream-only`,C.p,
        ()=>showParityOverlay(bothGroups,aOnlyGroups,uOnlyGroups)));
    } else if(u) {
      const upGroups=u.unique_test_groups||0;
      row.append(card('External Upstream',pct(u.pass_rate,1),`Build #${u.build_number} &bull; ${upGroups} runtime groups`,rc(u.pass_rate)));
    }

    box.append(row);
  }

  // ═══════════════════════ HARDWARE BREAKDOWN (consolidated) ═══════════════════════

  function _buildHwTable(hws, hwNames, hwGroupMap, currentBuildUrl) {
    const tbl=h('table',{style:{width:'100%',borderCollapse:'collapse'}});
    tbl.append(h('thead',{},[h('tr',{},[
      h('th',{text:'Hardware',style:ts()}),
      h('th',{text:'Group Pass Rate',style:ts()}),
      h('th',{text:'Groups Passing',style:ts('center')}),
      h('th',{text:'Groups Failing',style:ts('center')}),
      h('th',{text:'Total Groups',style:ts('center')}),
      h('th',{text:'Tests (P/F/S)',style:ts('center')}),
    ])]));
    const tb=h('tbody');
    for(const[hw,c]of hws) {
      // Use parity-derived group counts (hwGroupMap) as single source of truth.
      const parityGroups=hwGroupMap[hw];
      const gFail=parityGroups?parityGroups.failing.length:(c.groups_failed||0);
      const gPass=parityGroups?parityGroups.passing.length:((c.groups||0)-(c.groups_failed||0));
      const gPending=parityGroups?parityGroups.pending.length:0;
      const gCanceled=parityGroups?(parityGroups.canceled||[]).length:0;
      const gCurrent=gPass+gFail+gCanceled;
      const gTotal=gCurrent+gPending;
      const gRate=gCurrent>0?gPass/gCurrent:1;
      const tr=h('tr',{style:{cursor:'pointer',transition:'background .15s'}});
      tr.onmouseenter=()=>{tr.style.background=C.bd+'44'};
      tr.onmouseleave=()=>{tr.style.background=''};
      tr.onclick=()=>showHwGroupOverlay(hw,hwNames[hw]||hw.toUpperCase(),hwGroupMap[hw],c,currentBuildUrl);
      tr.append(h('td',{text:hwNames[hw]||String(hw||'unknown').toUpperCase(),style:{...td(),fontWeight:'700',textDecoration:'underline',color:C.b}}));
      tr.append(h('td',{style:td()},[ bar(gRate,'120px') ]));
      tr.append(h('td',{text:String(gPass),style:{...tdo('center'),color:C.g,fontWeight:'600'}}));
      tr.append(h('td',{text:String(gFail),style:{...tdo('center'),color:gFail>0?C.r:C.g,fontWeight:'600'}}));
      tr.append(h('td',{html:String(gTotal)+(gPending>0?` <span style="color:${C.y};font-size:11px">(${gPending} pending)</span>`:'')+(gCanceled>0?` <span style="color:${C.m};font-size:11px">(${gCanceled} canceled)</span>`:''),style:tdo('center')}));
      tr.append(h('td',{html:`<span style="color:${C.g}">${c.passed.toLocaleString()}</span> / <span style="color:${c.failed>0?C.r:C.m}">${c.failed}</span> / <span style="color:${C.m}">${c.skipped.toLocaleString()}</span>`,style:tdo('center')}));
      tb.append(tr);
    }
    tbl.append(tb);
    return tbl;
  }

  function renderHardware(box,health,parity) {
    const hwNames={
      mi250:'MI250 (gfx90a)',mi325:'MI325 (gfx942)',mi355:'MI355 (gfx950)',
      h100:'H100',h200:'H200',b200:'B200',a100:'A100',l40:'L40',cpu:'CPU',
    };

    // Pre-compute per-hardware groups from parity data
    const allMerged=parity?.job_groups?(typeof mergeShardedGroups==='function'?mergeShardedGroups(parity.job_groups):parity.job_groups):[];
    const hwGroupMap={};
    for(const g of allMerged){
      if(!g.amd&&!g.upstream&&!g.backfilled&&!g.hw_backfilled) continue;
      for(const hw of (g.hardware||[])){
        if(!hwGroupMap[hw]) hwGroupMap[hw]={passing:[],failing:[],pending:[],canceled:[]};
        // Per-HW pending: group-level backfilled OR this specific HW is backfilled
        const hwPending=g.backfilled||(g.hw_backfilled&&g.hw_backfilled[hw]);
        if(hwPending){
          hwGroupMap[hw].pending.push(g);
        } else {
          const hwFail=parityHwFailureCount(g, hw)>0;
          const hwCancel=parityHwCanceledCount(g, hw)>0;
          if(hwFail) hwGroupMap[hw].failing.push(g);
          else if(hwCancel) hwGroupMap[hw].canceled.push(g);
          else hwGroupMap[hw].passing.push(g);
        }
      }
    }

    // amd-ci runtime hardware breakdown
    if(health?.amd?.latest_build?.by_hardware) {
      const bh=health.amd.latest_build.by_hardware;
      const hws=Object.entries(bh).filter(([k])=>k!=='unknown'&&k!=='cpu').sort();
      if(hws.length) {
        const det=h('details',{open:true,style:{marginBottom:'20px',background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'8px'}});
        det.append(h('summary',{html:'<span style="color:#da3633;font-weight:700">amd-ci</span> Runtime Hardware Breakdown',style:{padding:'12px 16px',cursor:'pointer',fontSize:'14px',fontWeight:'600'}}));
        const inner=h('div',{style:{padding:'0 16px 16px'}});
        inner.append(_buildHwTable(hws, hwNames, hwGroupMap, health?.amd?.latest_build?.build_url));
        det.append(inner);
        box.append(det);
      }
    }

    // External upstream hardware breakdown. This can include CPU and non-NVIDIA labels,
    // so do not present it as a pure NVIDIA signal.
    if(health?.upstream?.latest_build?.by_hardware) {
      const ubh=health.upstream.latest_build.by_hardware;
      const uhws=Object.entries(ubh).filter(([k])=>k!=='unknown').sort();
      if(uhws.length) {
        // Split: B200 separate, rest grouped
        const b200=uhws.filter(([k])=>k==='b200');
        const others=uhws.filter(([k])=>k!=='b200');
        const upBuildUrl=health?.upstream?.latest_build?.build_url;

        const det=h('details',{open:true,style:{marginBottom:'20px',background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'8px'}});
        det.append(h('summary',{html:'<span style="color:#1f6feb;font-weight:700">External Upstream</span> Hardware Breakdown',style:{padding:'12px 16px',cursor:'pointer',fontSize:'14px',fontWeight:'600'}}));
        const inner=h('div',{style:{padding:'0 16px 16px'}});
        if(others.length) {
          inner.append(_buildHwTable(others, hwNames, hwGroupMap, upBuildUrl));
        }
        if(b200.length) {
          inner.append(h('div',{html:'<strong style="color:#d2a8ff">B200 Queue</strong>',style:{marginTop:'12px',marginBottom:'6px',fontSize:'13px'}}));
          inner.append(_buildHwTable(b200, hwNames, hwGroupMap, upBuildUrl));
        }
        det.append(inner);
        box.append(det);
      }
    }
  }

  // Hardware group overlay — shows all groups for a specific hardware
  function showHwGroupOverlay(hw,hwLabel,groups,counts,currentBuildUrl){
    if(!groups) groups={passing:[],failing:[],pending:[],canceled:[]};
    const pending=groups.pending||[];
    const canceled=groups.canceled||[];
    const current=[...groups.failing,...groups.passing,...canceled];
    const all=[...current,...pending];
    const pendingCount=pending.length;
    const canceledCount=canceled.length;

    const backdrop=h('div',{style:{position:'fixed',inset:'0',background:'rgba(0,0,0,.6)',zIndex:'1000',display:'flex',justifyContent:'center',alignItems:'flex-start',paddingTop:'40px',overflow:'auto'}});
    backdrop.onclick=e=>{if(e.target===backdrop)backdrop.remove()};

    const panel=h('div',{style:{background:C.bg2,border:`1px solid ${C.bd}`,borderRadius:'12px',width:'min(900px,90vw)',maxHeight:'85vh',overflow:'auto',padding:'24px'}});

    const gPass=groups.passing.length;
    const gFail=groups.failing.length;
    const gTotal=gPass+gFail+canceledCount;

    // Header
    panel.append(h('div',{style:{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:'16px'}},[
      h('h3',{html:`${hwLabel} — <span style="color:${C.g}">${gPass} passing</span>, <span style="color:${C.r}">${gFail} failing</span>`+(canceledCount>0?`, <span style="color:${C.m}">${canceledCount} canceled</span>`:'')+` <span style="color:${C.m}">of ${gTotal} groups</span>`+(pendingCount>0?` <span style="color:${C.y}">(${pendingCount} pending)</span>`:''),style:{margin:'0',fontSize:'18px'}}),
      h('button',{text:'✕',onclick:()=>backdrop.remove(),style:{background:'none',border:'none',color:C.m,fontSize:'20px',cursor:'pointer',padding:'4px 8px'}})
    ]));

    // Stats bar
    if(counts){
      panel.append(h('div',{html:`Tests: <span style="color:${C.g}">${(counts.passed||0).toLocaleString()} passed</span> / <span style="color:${C.r}">${counts.failed||0} failed</span> / <span style="color:${C.m}">${(counts.skipped||0).toLocaleString()} skipped</span>`,
        style:{fontSize:'13px',color:C.m,marginBottom:'16px',padding:'8px 12px',background:C.bg,borderRadius:'6px',border:`1px solid ${C.bd}`}}));
    }

    // Group table
    const tbl=h('table',{style:{width:'100%',borderCollapse:'collapse',fontSize:'14px'}});
    tbl.append(h('thead',{},[h('tr',{},[
      h('th',{text:'#',style:{...ts('center'),width:'36px'}}),
      h('th',{text:'Test Group',style:ts()}),
      h('th',{text:'Tests P/F/S',style:ts('center')}),
      h('th',{text:'Status',style:ts('center')}),
      h('th',{text:'Links',style:ts('center')}),
    ])]));
    const tbody=h('tbody');
    // Sort: failing first, then canceled, then passing, then pending
    const sortedAll=[
      ...groups.failing.sort((a,b)=>(a.name||'').localeCompare(b.name||'')),
      ...canceled.sort((a,b)=>(a.name||'').localeCompare(b.name||'')),
      ...groups.passing.sort((a,b)=>(a.name||'').localeCompare(b.name||'')),
      ...pending.sort((a,b)=>(a.name||'').localeCompare(b.name||'')),
    ];
    let idx=0;
    for(const g of sortedAll){
      idx++;
      const isGroupPending=pending.includes(g);
      const isGroupCanceled=canceled.includes(g);
      const isFail=!isGroupPending&&!isGroupCanceled&&groups.failing.includes(g);
      const hwFails=isGroupPending?0:parityHwFailureCount(g, hw);
      // For AMD hardware, show AMD test counts. For upstream hardware (h100, b200, etc.), show upstream counts.
      const isAmdHw=hw.startsWith('mi')||hw==='cpu';
      const a=isGroupPending?{}:(isAmdHw?(g.amd||g.upstream||{}):(g.upstream||g.amd||{}));
      const tr=h('tr',{style:{borderBottom:`1px solid ${C.bd}`}});
      tr.append(h('td',{text:String(idx),style:{...tdo('center'),color:C.m,fontSize:'12px'}}));

      // Name cell
      const nameCell=h('td',{style:td()});
      nameCell.textContent=g.name;
      tr.append(nameCell);

      // Test counts P/F/S (or "—" for pending groups)
      if(isGroupPending){
        tr.append(h('td',{text:'—',style:{...tdo('center'),color:C.m}}));
      } else {
        tr.append(h('td',{html:`<span style="color:${C.g}">${a.passed||0}</span>/<span style="color:${(a.failed||0)>0?C.r:C.m}">${a.failed||0}</span>/<span style="color:${C.m}">${a.skipped||0}</span>`,style:tdo('center')}));
      }

      const statusText=isGroupPending?'PENDING':isGroupCanceled?'CANCELED':isFail?'FAIL':'PASS';
      const statusColor=isGroupPending?C.y:isGroupCanceled?C.m:isFail?C.r:C.g;
      tr.append(h('td',{text:statusText,style:{...tdo('center'),color:statusColor,fontWeight:'600',fontSize:'12px'}}));
      if(isGroupPending||isGroupCanceled) tr.style.opacity='0.5';

      // Links column — for pending groups, link to current build overview
      const linkCell=h('td',{style:tdo('center')});
      if((isGroupPending||isGroupCanceled)&&currentBuildUrl){
        linkCell.append(h('a',{text:'Buildkite',href:currentBuildUrl,target:'_blank',style:{color:C.y,fontSize:'12px',textDecoration:'none',padding:'2px 8px',background:C.y+'15',borderRadius:'3px',border:`1px solid ${C.y}33`}}));
      } else {
        const hwLinks=(g.job_links||[]).filter(l=>l.hw===hw);
        if(hwLinks.length){
          for(const l of hwLinks){
            linkCell.append(h('a',{text:'Buildkite',href:l.url,target:'_blank',style:{color:C.b,fontSize:'12px',textDecoration:'none',padding:'2px 8px',background:C.b+'15',borderRadius:'3px',border:`1px solid ${C.b}33`}}));
          }
        } else {
          linkCell.append(h('span',{text:'—',style:{color:C.m}}));
        }
      }
      tr.append(linkCell);
      tbody.append(tr);
    }
    tbl.append(tbody);
    panel.append(tbl);
    backdrop.append(panel);
    document.body.append(backdrop);
  }

  // ═══════════════════════ TREND CHART ═══════════════════════
  function renderTrend(box,health) {
    if(!health?.amd?.builds||health.amd.builds.length<2) return;
    const det=h('details',{open:true,style:{marginBottom:'20px',background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'8px'}});
    det.append(h('summary',{text:'Test Group Pass Rate Trend (7 days)',style:{padding:'12px 16px',cursor:'pointer',fontSize:'14px',fontWeight:'600'}}));
    const canvas=h('canvas',{style:{maxHeight:'200px',padding:'0 16px 16px'}});
    det.append(canvas);
    box.append(det);

    const amd=[...health.amd.builds].reverse();
    const up=health.upstream?.builds?[...health.upstream.builds].reverse():[];
    // Group pass rate: test_groups_passing_or / unique_test_groups
    function groupRate(b){ const t=b.unique_test_groups||0,p=b.test_groups_passing_or||0; return t>0?+(p/t*100).toFixed(1):null; }

    // Align both datasets by "nightly date" — matching backend nightly_date().
    // Boundary at 12:00 UTC: current upstream (~06:00 UTC) and AMD
    // (~09:00 UTC) runs stay on the same calendar day; older after-noon
    // upstream runs still map to the next nightly date.
    function nightlyDate(iso){
      if(!iso) return '';
      const d=new Date(iso);
      if(d.getUTCHours()>=12) d.setUTCDate(d.getUTCDate()+1);
      return d.toISOString().slice(0,10);
    }
    const allDates=new Set();
    const amdByDate={}, upByDate={};
    for(const b of amd){ const d=nightlyDate(b.created_at); if(d){allDates.add(d); if(!amdByDate[d])amdByDate[d]=b;} }
    for(const b of up){ const d=nightlyDate(b.created_at); if(d){allDates.add(d); if(!upByDate[d])upByDate[d]=b;} }
    const dates=[...allDates].sort();
    const amdData=dates.map(d=>amdByDate[d]?groupRate(amdByDate[d]):null);
    const upData=dates.map(d=>upByDate[d]?groupRate(upByDate[d]):null);

    // Dynamic Y-axis
    const allVals=[...amdData,...upData].filter(v=>v!=null);
    const yMin=allVals.length?Math.max(0,Math.floor(Math.min(...allVals)/5)*5-5):60;
    new Chart(canvas,{type:'line',data:{
      labels:dates.map(d=>d.slice(5)),
      datasets:[
        {label:'AMD',data:amdData,borderColor:C.r,backgroundColor:'rgba(218,54,51,.08)',tension:.3,fill:true,pointRadius:3,spanGaps:true},
        ...(up.length?[{label:'Upstream',data:upData,borderColor:C.b,backgroundColor:'rgba(31,111,235,.08)',tension:.3,fill:true,pointRadius:3,spanGaps:true}]:[]),
      ]},options:{responsive:true,plugins:{legend:{labels:{color:C.t}},tooltip:{callbacks:{label:ctx=>ctx.dataset.label+': '+ctx.parsed.y+'% groups passing'}}},scales:{
        y:{min:yMin,max:100,ticks:{color:C.m,callback:v=>v+'%'},grid:{color:C.bd}},
        x:{ticks:{color:C.m},grid:{color:C.bd}},
      }}
    });
  }

  // ═══════════════════════ HEALTH BAR ═══════════════════════
  function renderHealthBar(box,health) {
    if(!health?.test_counts) return;
    const tc=health.test_counts;
    const total=Object.values(tc).reduce((a,b)=>a+b,0);
    if(!total) return;

    const det=h('details',{style:{marginBottom:'20px',background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'8px'}});
    det.append(h('summary',{text:'Test Group Health (across 7-day history)',style:{padding:'12px 16px',cursor:'pointer',fontSize:'14px',fontWeight:'600'}}));
    const inner=h('div',{style:{padding:'0 16px 16px'}});

    const b=h('div',{style:{display:'flex',height:'16px',borderRadius:'4px',overflow:'hidden',marginBottom:'8px'}});
    const leg=h('div',{style:{display:'flex',flexWrap:'wrap',gap:'10px',fontSize:'12px'}});
    for(const l of ['passing','new_test','skipped','flaky','failing','new_failure','fixed']) {
      const n=tc[l]||0; if(!n) continue;
      b.append(h('div',{title:`${l}: ${n}`,style:{width:(n/total*100)+'%',background:LC[l]||C.m,minWidth:'2px'}}));
      leg.append(h('span',{},[h('span',{style:{display:'inline-block',width:'8px',height:'8px',borderRadius:'2px',background:LC[l]||C.m,marginRight:'4px'}}),`${l} (${n})`]));
    }
    inner.append(b,leg);
    det.append(inner);
    box.append(det);
  }

  // ═══════════════════════ HEATMAP ═══════════════════════
  function renderHeatmap(box,parity) {
    if(!parity?.job_groups) return;
    const allMerged=typeof mergeParityGroups==='function'?mergeParityGroups(parity.job_groups):parity.job_groups;
    const groups=allMerged.filter(g=>g.amd&&g.upstream);
    if(!groups.length) return;

    const areas={};
    for(const g of groups) {
      const a=area(g.name);
      if(!areas[a])areas[a]={pass:0,fail:0,total:0};
      if((g.amd.failed||0)>0)areas[a].fail++;else areas[a].pass++;
      areas[a].total++;
    }

    const det=h('details',{open:true,style:{marginBottom:'20px',background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'8px'}});
    det.append(h('summary',{text:'Test Area Health',style:{padding:'12px 16px',cursor:'pointer',fontSize:'14px',fontWeight:'600'}}));
    const grid=h('div',{style:{display:'flex',flexWrap:'wrap',gap:'6px',padding:'0 16px 16px'}});

    for(const[a,d]of Object.entries(areas).sort((a,b)=>b[1].total-a[1].total)) {
      const r=d.pass/d.total;
      const label=a.replace(/-/g,' ');
      const cell=h('div',{title:`${label}: ${d.pass}/${d.total} pass`,style:{
        minWidth:'80px',padding:'8px 14px',background:rc(r),borderRadius:'6px',display:'flex',alignItems:'center',
        justifyContent:'center',cursor:'pointer',fontSize:'12px',color:'#fff',fontWeight:'600',
        textAlign:'center',wordBreak:'break-word',opacity:r>=1?'.65':'1',
      },text:label});
      cell.onclick=()=>{const el=document.querySelector(`details[data-area="${a}"]`);if(el){el.open=true;el.scrollIntoView({behavior:'smooth',block:'nearest'})}};
      grid.append(cell);
    }
    det.append(grid);
    box.append(det);
  }

  // ═══════════════════════ GROUPED PARITY ═══════════════════════
  function renderGroups(box,parity) {
    if(!parity?.job_groups) return;
    const all=typeof mergeParityGroups==='function'?mergeParityGroups(parity.job_groups):parity.job_groups;
    const both=all.filter(g=>g.amd&&g.upstream);
    const aOnly=all.filter(g=>g.amd&&!g.upstream);
    const uOnly=all.filter(g=>!g.amd&&g.upstream);

    const section=h('div',{style:{marginBottom:'20px'}});
    section.append(h('h3',{text:'Runtime Parity','data-parity-title':'1',style:{marginBottom:'8px',fontSize:'16px'}}));

    // Filters
    const fb=h('div',{style:{display:'flex',gap:'4px',flexWrap:'wrap',marginBottom:'12px'}});
    const filters=[{l:'All',v:'all'},{l:`Regressions`,v:'regression'},{l:'Both Pass',v:'pass'},{l:`AMD-only (${aOnly.length})`,v:'amd-only'},{l:`Upstream-only (${uOnly.length})`,v:'up-only'}];
    let active='all';
    const container=h('div');

    for(const f of filters) {
      const btn=h('button',{text:f.l,'data-filter':f.v,style:{background:f.v==='all'?C.b:C.bd,border:'none',color:C.t,padding:'6px 14px',borderRadius:'4px',cursor:'pointer',fontSize:'13px',fontFamily:'inherit'}});
      btn.onclick=()=>{
        active=f.v;fb.querySelectorAll('button').forEach(b=>b.style.background=C.bd);btn.style.background=C.b;
        container.querySelectorAll('details[data-area]').forEach(d=>{
          if(f.v==='all')d.style.display='';
          else if(f.v==='amd-only'||f.v==='up-only')d.style.display='none';
          else d.style.display=d.dataset.status===f.v||(f.v==='regression'&&d.dataset.status==='regression')?'':'none';
        });
        const amdSec=container.querySelector('[data-sec="amd-only"]');if(amdSec)amdSec.style.display=f.v==='amd-only'?'':'none';
        const upSec=container.querySelector('[data-sec="up-only"]');if(upSec)upSec.style.display=f.v==='up-only'?'':'none';
      };
      fb.append(btn);
    }
    section.append(fb);

    // Group by area
    const byArea={};
    for(const g of both){const a=area(g.name);(byArea[a]=byArea[a]||[]).push(g)}

    for(const[a,gs]of Object.entries(byArea).sort((a,b)=>a[0].localeCompare(b[0]))) {
      const regs=gs.filter(g=>(g.amd.failed||0)>0&&(g.upstream.failed||0)===0);
      const allP=gs.every(g=>(g.amd.failed||0)===0);
      const det=h('details',{'data-area':a,'data-status':regs.length>0?'regression':allP?'pass':'fail',style:{marginBottom:'4px',background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'6px'}});
      if(regs.length>0) det.open=true;

      const r=gs.filter(g=>(g.amd.failed||0)===0).length/gs.length;
      det.append(h('summary',{style:{padding:'10px 14px',cursor:'pointer',display:'flex',justifyContent:'space-between',alignItems:'center',fontSize:'13px'}},[
        h('span',{style:{fontWeight:'600'}},[
          h('span',{style:{width:'8px',height:'8px',borderRadius:'50%',background:regs.length>0?C.r:allP?C.g:C.o,display:'inline-block',marginRight:'6px'}}),
          a.replace(/-/g,' ')+' ',
          h('span',{text:`(${gs.length} groups${regs.length?`, ${regs.length} regressions`:''})`,style:{color:C.m,fontWeight:'400'}})
        ]),
        bar(r,'80px')
      ]));

      const tbl=h('table',{style:{width:'100%',borderCollapse:'collapse',fontSize:'14px'}});
      tbl.append(h('thead',{},[h('tr',{},[
        h('th',{text:'Test Group',style:ts()}),h('th',{text:'Links',style:{...ts('center'),width:'50px'}}),h('th',{html:'AMD P/F/S',style:ts('center')}),
        h('th',{html:'Upstream P/F/S',style:ts('center')}),h('th',{text:'Hardware',style:ts('center')}),
        h('th',{text:'Status',style:ts('center')})
      ])]));
      const tb=h('tbody');
      for(const g of gs.sort((a,b)=>(b.amd.failed||0)-(a.amd.failed||0))) {
       try {
        const af=(g.amd.failed||0),uf=(g.upstream?.failed||0);
        let st,sc;
        if(!af&&!uf){st='Both pass';sc=C.g}
        else if(af&&!uf){st='AMD regression';sc=C.r}
        else if(!af&&uf){st='AMD advantage';sc=C.b}
        else{st='Both fail';sc=C.o}

        // Hardware column — show all hardware this TG runs on
        const hwList = g.hardware || [];
        const hwHtml = hwList.length ? hwList.map(hw => {
          const failCnt = parityHwFailureCount(g, hw);
          if (failCnt) return `<span style="background:${C.r}22;color:${C.r};padding:3px 8px;border-radius:3px;font-size:13px;margin:1px;font-weight:600">${hw}: ${failCnt}f</span>`;
          return `<span style="color:${C.g};font-size:13px;margin:1px">${hw}</span>`;
        }).join(' ') : '<span style="color:'+C.m+'">—</span>';

        // Main row
        const mainRow = h('tr', {style:{cursor:af>0?'pointer':'default'}});
        const nameCell=h('td',{style:td()});
        nameCell.textContent=g.name;
        mainRow.append(nameCell);
        const linksCell=h('td',{style:td('center')});
        if(typeof makeGroupLinksColumn==='function'){linksCell.append(makeGroupLinksColumn(g.name,!!g.amd,!!g.upstream))}
        mainRow.append(linksCell);
        mainRow.append(h('td',{html:`<span style="color:${C.g}">${g.amd.passed||0}</span>/<span style="color:${C.r}">${af}</span>/<span style="color:${C.m}">${g.amd.skipped||0}</span>`,style:td('center')}));
        mainRow.append(h('td',{html:`<span style="color:${C.g}">${g.upstream?.passed||0}</span>/<span style="color:${C.r}">${uf}</span>/<span style="color:${C.m}">${g.upstream?.skipped||0}</span>`,style:td('center')}));
        mainRow.append(h('td',{html:hwHtml,style:td('center')}));
        mainRow.append(h('td',{html:`<span style="color:${sc};font-weight:600${af>0?';cursor:pointer;text-decoration:underline':''}">${st}</span>`,style:td('center')}));
        tb.append(mainRow);

        // Expandable detail row for failures
        if (af > 0) {
          const detailRow = h('tr',{style:{display:'none'}});
          const detailCell = h('td',{colspan:'6',style:{padding:'12px 16px',background:C.bg2,borderBottom:`1px solid ${C.bd}`}});
          const dc = h('div',{style:{fontSize:'13px'}});

          // HW failure breakdown
          if (hwf && typeof hwf==='object' && Object.keys(hwf).length) {
            dc.append(h('div',{style:{marginBottom:'10px'}},[
              h('span',{text:'Failures by hardware: ',style:{color:C.m,fontWeight:'600'}}),
              ...Object.entries(hwf).map(([hw,cnt])=>h('span',{text:`${String(hw||'unknown').toUpperCase()}: ${cnt}`,style:{background:C.r+'22',color:C.r,padding:'4px 10px',borderRadius:'4px',marginLeft:'4px',fontWeight:'700',fontSize:'13px'}}))
            ]));
          }

          // Job links — only for AMD hardware that has failures
          const amdLinks = (g.job_links||[]).filter(jl=>jl&&jl.side==='amd'&&parityHwFailureCount(g, jl.hw)>0);
          if (amdLinks.length) {
            dc.append(h('div',{text:'View logs on Buildkite:',style:{color:C.m,fontWeight:'600',marginBottom:'6px'}}));
            const linkRow = h('div',{style:{display:'flex',gap:'8px',flexWrap:'wrap'}});
            for (const jl of amdLinks) {
              linkRow.append(h('a',{text:`${String(jl.hw||'unknown').toUpperCase()} — ${jl.job_name||'unknown'}`,href:jl.url||'#',target:'_blank',style:{color:C.b,fontSize:'13px',padding:'4px 10px',background:C.b+'15',borderRadius:'4px',textDecoration:'none',border:`1px solid ${C.b}33`}}));
            }
            dc.append(linkRow);
          }

          // Individual failure test names
          if (g.failure_tests?.length) {
            dc.append(h('div',{text:'Failed tests:',style:{color:C.m,fontWeight:'600',marginTop:'10px',marginBottom:'4px'}}));
            const ul = h('ul',{style:{margin:'0 0 0 16px',color:C.t}});
            for (const t of g.failure_tests) ul.append(h('li',{text:t,style:{fontFamily:'monospace',fontSize:'12px',padding:'2px 0'}}));
            dc.append(ul);
          }

          detailCell.append(dc);
          detailRow.append(detailCell);
          tb.append(detailRow);

          mainRow.onclick = () => { detailRow.style.display = detailRow.style.display === 'none' ? '' : 'none'; };
        }
       } catch(ge) { console.error('Group render error:',g.name,ge); }
      }
      tbl.append(tb);
      det.append(h('div',{style:{padding:'0 12px 10px'}},[tbl]));
      container.append(det);
    }

    // AMD-only / Upstream-only — always visible as collapsible sections
    for(const[key,list,color,label]of[['amd-only',aOnly,C.r,'AMD-Only'],['up-only',uOnly,C.b,'Upstream-Only']]) {
      if(!list.length) continue;
      const det=h('details',{'data-sec':key,style:{marginTop:'8px',background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'6px'}});
      det.append(h('summary',{html:`<span style="color:${color};font-weight:600">${label} Test Groups</span> <span style="color:${C.m}">(${list.length})</span>`,style:{padding:'12px 16px',cursor:'pointer',fontSize:'14px'}}));
      const grid=h('div',{style:{display:'flex',flexWrap:'wrap',gap:'6px',padding:'4px 16px 14px'}});
      for(const g of list.sort((a,b)=>(a.name||'').localeCompare(b.name||''))) {
        const pipeline=key==='amd-only'?'amd':'upstream';
        const chip=h('a',{text:(g.amd_job_name||g.upstream_job_name||g.name),href:bkSearchUrl(g.name,pipeline),target:'_blank',style:{
          padding:'4px 10px',borderRadius:'4px',fontSize:'13px',
          background:color+'15',border:`1px solid ${color}33`,color:C.t,
          textDecoration:'none',transition:'all .15s',display:'inline-block',
        }});
        chip.onmouseenter=()=>{chip.style.background=color+'30';chip.style.color='#58a6ff'};
        chip.onmouseleave=()=>{chip.style.background=color+'15';chip.style.color=C.t};
        grid.append(chip);
      }
      det.append(grid);
      container.append(det);
    }

    section.append(container);
    box.append(section);
  }

  // ═══════════════════════ COLLAPSIBLE SECTIONS ═══════════════════════

  function renderFlaky(box,flaky) {
    if(!flaky?.tests?.length) return;
    const det=h('details',{style:{marginBottom:'8px',background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'8px'}});
    det.append(h('summary',{html:`Flaky Tests <span style="color:${C.y}">(${flaky.total_flaky})</span>`,style:{padding:'12px 16px',cursor:'pointer',fontSize:'14px',fontWeight:'600'}}));
    const tbl=h('table',{style:{width:'100%',borderCollapse:'collapse',fontSize:'14px',margin:'0 0 12px'}});
    tbl.append(h('thead',{},[h('tr',{},[h('th',{text:'Test',style:ts()}),h('th',{text:'Rate',style:ts('center')}),h('th',{text:'History',style:ts('center')})])]));
    const tb=h('tbody');
    for(const t of flaky.tests)
      tb.append(h('tr',{},[
        h('td',{text:t.test_id.replace('::__job_level__',''),style:td()}),
        h('td',{text:pct(t.pass_rate),style:{...tdo('center'),color:C.y,fontWeight:'600'}}),
        h('td',{style:td('center')},[dots(t.history)])
      ]));
    tbl.append(tb);
    det.append(h('div',{style:{padding:'0 16px 12px'}},[tbl]));
    box.append(det);
  }

  function renderOffenders(box,trends) {
    if(!trends?.top_offenders?.length) return;
    const det=h('details',{style:{marginBottom:'8px',background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'8px'}});
    det.append(h('summary',{html:`Top Offenders <span style="color:${C.r}">(${trends.top_offenders.length})</span>`,style:{padding:'12px 16px',cursor:'pointer',fontSize:'14px',fontWeight:'600'}}));
    const tbl=h('table',{style:{width:'100%',borderCollapse:'collapse',fontSize:'14px'}});
    tbl.append(h('thead',{},[h('tr',{},[h('th',{text:'Test',style:ts()}),h('th',{text:'Streak',style:ts('center')}),h('th',{text:'History',style:ts('center')})])]));
    const tb=h('tbody');
    for(const t of trends.top_offenders.slice(0,15))
      tb.append(h('tr',{},[
        h('td',{text:t.test_id.replace('::__unidentified_failures__',' (failures)').replace('::__job_level__',''),style:td()}),
        h('td',{text:`${t.failure_streak}`,style:{...tdo('center'),color:C.r}}),
        h('td',{style:td('center')},[dots(t.history)])
      ]));
    tbl.append(tb);
    det.append(h('div',{style:{padding:'0 16px 12px'}},[tbl]));
    box.append(det);
  }

  function renderConfigParity(box,cp) {
    if(!cp?.matches) return;
    const s=cp.summary;
    const divergent=cp.matches.filter(m=>m.command_similarity<1.0);
    const det=h('details',{style:{marginBottom:'8px',background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'8px'}});
    det.append(h('summary',{html:`Config Parity <span style="color:${C.m}">${s.matched} matched, ${s.avg_command_similarity_pct}% avg similarity${divergent.length?`, <span style="color:${C.y}">${divergent.length} divergent</span>`:''}</span>`,style:{padding:'12px 16px',cursor:'pointer',fontSize:'14px',fontWeight:'600'}}));
    if(!divergent.length){det.append(h('p',{text:'All matched steps identical.',style:{padding:'0 16px 12px',color:C.g,fontSize:'12px'}}));box.append(det);return}
    const tbl=h('table',{style:{width:'100%',borderCollapse:'collapse',fontSize:'14px'}});
    tbl.append(h('thead',{},[h('tr',{},[h('th',{text:'Step',style:ts()}),h('th',{text:'Similarity',style:ts('center')})])]));
    const tb=h('tbody');
    const sc={green:C.g,yellow:C.y,orange:C.o,red:C.r};
    for(const m of divergent) {
      const tr=h('tr',{style:{cursor:m.amd_commands?'pointer':'default'}});
      tr.append(h('td',{text:m.normalized,style:td()}));
      tr.append(h('td',{html:`<span style="color:${sc[m.color]||C.m};font-weight:600">${(m.command_similarity*100).toFixed(0)}%</span>`,style:td('center')}));
      tb.append(tr);
      // Expandable diff row with highlighted differences
      if(m.amd_commands && m.nvidia_commands) {
        const diffRow=h('tr',{style:{display:'none'}});
        const diffCell=h('td',{colspan:'2',style:{padding:'12px 16px',background:C.bg2,borderBottom:`1px solid ${C.bd}`}});

        // Compute which commands are unique to each side
        const amdSet=new Set(m.amd_commands);
        const upstreamSet=new Set(m.nvidia_commands);
        const common=new Set([...amdSet].filter(c=>upstreamSet.has(c)));

        diffCell.append(h('div',{style:{display:'grid',gridTemplateColumns:'1fr 1fr',gap:'16px',fontSize:'13px',fontFamily:'monospace'}},[
          h('div',{},[
            h('div',{text:'AMD Commands',style:{color:C.r,fontWeight:'700',marginBottom:'6px',fontSize:'13px',fontFamily:'inherit'}}),
            ...m.amd_commands.map(c=>{
              const isUnique=!upstreamSet.has(c);
              return h('div',{text:c,style:{
                color:isUnique?C.t:C.m,padding:'3px 6px',wordBreak:'break-all',
                background:isUnique?'rgba(218,54,51,0.15)':'transparent',
                borderLeft:isUnique?`3px solid ${C.r}`:'3px solid transparent',
                borderRadius:'2px',marginBottom:'2px',
              }})
            })
          ]),
          h('div',{},[
            h('div',{text:'Upstream Commands',style:{color:C.b,fontWeight:'700',marginBottom:'6px',fontSize:'13px',fontFamily:'inherit'}}),
            ...m.nvidia_commands.map(c=>{
              const isUnique=!amdSet.has(c);
              return h('div',{text:c,style:{
                color:isUnique?C.t:C.m,padding:'3px 6px',wordBreak:'break-all',
                background:isUnique?'rgba(31,111,235,0.15)':'transparent',
                borderLeft:isUnique?`3px solid ${C.b}`:'3px solid transparent',
                borderRadius:'2px',marginBottom:'2px',
              }})
            })
          ]),
        ]));
        diffRow.append(diffCell);
        tb.append(diffRow);
        tr.onclick=()=>{diffRow.style.display=diffRow.style.display==='none'?'':'none'};
      }
    }
    tbl.append(tb);
    det.append(h('div',{style:{padding:'0 16px 12px'}},[tbl]));
    box.append(det);
  }

  // ═══════════════════════ STYLE HELPERS ═══════════════════════
  function ts(a){return{textAlign:a||'left',padding:'8px 12px',borderBottom:`2px solid ${C.bd}`,color:C.m,fontSize:'12px',textTransform:'uppercase',fontWeight:'600'}}
  function td(a){return{textAlign:a||'left',padding:'8px 12px',borderBottom:`1px solid ${C.bd}`,color:C.t,fontSize:'14px'}}
  function tdo(a){return{textAlign:a||'left',padding:'8px 12px',borderBottom:`1px solid ${C.bd}`,fontSize:'14px'}}

  function renderHealthSubviewTabs(box, data) {
    const tabs = [
      {id:'gating', label:'Gating Signal'},
      {id:'external', label:'Experimental Signal'},
    ];
    const tabBar = h('div',{style:{display:'flex',gap:'4px',flexWrap:'wrap',margin:'12px 0 16px'}});
    const viewHost = h('div');
    const buttons = {};

    function button(label) {
      return h('button',{text:label,style:{background:C.bd,border:'none',color:C.t,padding:'6px 12px',borderRadius:'5px',cursor:'pointer',fontSize:'13px',fontWeight:'400',fontFamily:'inherit'}});
    }

    function setActive(name) {
      for (const t of tabs) {
        const active = t.id === name;
        buttons[t.id].style.background = active ? C.b : C.bd;
        buttons[t.id].style.color = active ? '#fff' : C.t;
        buttons[t.id].style.fontWeight = active ? '600' : '400';
      }
    }

    for (const t of tabs) {
      buttons[t.id] = button(t.label);
      tabBar.append(buttons[t.id]);
    }

    let externalPromise = null;

    function loadExternalSignalData() {
      if (data.parity || data.cp || data.flaky || data.trends) {
        return Promise.resolve(data);
      }
      if (!externalPromise) {
        externalPromise = Promise.all([
          J(`${CI}/parity_report.json`),J(`${CI}/config_parity.json`),J(`${CI}/flaky_tests.json`),
          J(`${CI}/failure_trends.json`)
        ]).then(([parity, cp, flaky, trends]) => {
          Object.assign(data, {parity, cp, flaky, trends});
          return data;
        });
      }
      return externalPromise;
    }

    function renderSubview(name) {
      viewHost.innerHTML = '';
      setActive(name);
      try {
        if (name === 'external') {
          viewHost.append(h('p',{text:'Loading experimental signal...',style:{color:C.m}}));
          loadExternalSignalData().then(() => {
            viewHost.innerHTML = '';
            renderExternalSignal(viewHost, data);
          }).catch(e => {
            console.error('CI Health external:', e);
            viewHost.innerHTML = '';
            viewHost.append(h('div',{text:`CI Health could not load experimental signal: ${e.message}`,style:{color:C.r,background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'8px',padding:'12px 14px'}}));
          });
        } else {
          renderGatingExecutive(viewHost, data.capacity, data.matrix, data.health, data.analytics, data.proposals, data.targets, data.targetCandidates);
        }
      } catch (e) {
        console.error(`CI Health ${name}:`, e);
        viewHost.append(h('div',{text:`CI Health could not render ${name}: ${e.message}`,style:{color:C.r,background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'8px',padding:'12px 14px'}}));
      }
    }

    buttons.gating.onclick = () => renderSubview('gating');
    buttons.external.onclick = () => renderSubview('external');
    box.append(tabBar, viewHost);
    renderSubview('gating');
  }

  function renderExternalSignal(box, data) {
    const {health, parity, cp, flaky, trends} = data;
    box.append(h('h3',{text:'Internal CI signal',style:{fontSize:'18px',margin:'0 0 6px'}}));
    box.append(h('p',{text:'Secondary view: this compares vllm/amd-ci with the upstream vllm/ci runtime signal. Treat it as diagnostic context; upstream names can include CPU, AMD-like, and NVIDIA hardware labels.',style:{color:C.m,fontSize:'13px',margin:'0 0 14px',maxWidth:'980px'}}));

    // Running build banner
    const ab=health?.amd?.latest_build;
    if(ab?.is_running){
      const jr=ab.jobs_running||0,jw=ab.jobs_waiting||0,jp=ab.jobs_passed||0,jf=ab.jobs_failed||0,jt=ab.job_count||0;
      const done=jp+jf,prog=jt>0?Math.round(done/jt*100):0;
      const sf=ab.jobs_soft_failed||0;
      const banner=h('div',{style:{background:'#d2992215',border:'1px solid #d29922',borderRadius:'8px',padding:'12px 16px',marginBottom:'16px',display:'flex',alignItems:'center',gap:'10px'}});
      banner.append(h('span',{html:'&#9888;',style:{fontSize:'18px'}}));
      banner.append(h('span',{html:`<strong>Build #${ab.build_number} is still running</strong> — ${done}/${jt} jobs complete (${prog}%)` +
        (jr>0?` &bull; <span style="color:${C.y}">${jr} running</span>`:'') +
        (jw>0?` &bull; <span style="color:${C.m}">${jw} waiting</span>`:'') +
        (sf>0?` &bull; <span style="color:${C.r}">${sf} soft-failed</span>`:''),
        style:{color:C.t,fontSize:'13px'}}));
      box.append(banner);
    }

    // Update build URLs in the link registry
    LinkRegistry.bk.updateBuildUrls(health);

    for(const[n,fn]of[['Metrics',()=>renderMetrics(box,health,parity)],['Hardware',()=>renderHardware(box,health,parity)],['Trend',()=>renderTrend(box,health)],['Heatmap',()=>renderHeatmap(box,parity)],['Groups',()=>renderGroups(box,parity)],['Flaky',()=>renderFlaky(box,flaky)],['Offenders',()=>renderOffenders(box,trends)],['ConfigParity',()=>renderConfigParity(box,cp)]]){try{fn()}catch(e){console.error(`CI Health ${n}:`,e);box.append(h('div',{text:`[${n} error: ${e.message}]`,style:{color:C.r,padding:'8px',fontSize:'13px'}}))}}
  }

  // ═══════════════════════ MAIN ═══════════════════════
  async function render() {
    const box=document.getElementById('ci-health-view');
    if(!box)return;
    box.innerHTML='<p style="color:#8b949e">Loading...</p>';

    const analyticsPromise = J(`${CI}/gating_nightlies.json`);
    const[health,capacity,proposals,targets,targetCandidates]=await Promise.all([
      J(`${CI}/ci_health.json`),
      J(`${CI}/capacity_monitor.json`),
      J(`${CI}/gating_proposals.json`),J(`${CI}/gating_targets.json`),J(`${CI}/gating_target_candidates.json`)
    ]);

    if(!health&&!capacity&&!proposals&&!targets&&!targetCandidates){
      const analytics=await analyticsPromise;
      if(!analytics){box.innerHTML='<p style="color:#8b949e">No data. Run collect_ci.py.</p>';return}
    }
    box.innerHTML='';

    box.append(h('h2',{text:'AMD CI',style:{marginBottom:'4px'}}));

    if(health?.generated_at) {
      // Show last updated + next expected nightly
      const updP=h('p',{style:{color:C.m,fontSize:'12px',marginBottom:'4px'}});
      updP.textContent=`Last updated: ${new Date(health.generated_at).toLocaleString()}`;
      box.append(updP);

      // Calculate next nightly times. These are UTC schedule slots; the browser
      // localizes the displayed time.
      // AMD nightly: ~09:00 UTC daily (4 AM Central during daylight time)
      // Upstream nightly: ~06:00 UTC daily (1 AM Central during daylight time)
      const now=new Date();
      const todayAmd=new Date(Date.UTC(now.getUTCFullYear(),now.getUTCMonth(),now.getUTCDate(),9,0));
      const todayUp=new Date(Date.UTC(now.getUTCFullYear(),now.getUTCMonth(),now.getUTCDate(),6,0));
      let nextAmd=todayAmd>now?todayAmd:new Date(todayAmd.getTime()+86400000);
      let nextUp=todayUp>now?todayUp:new Date(todayUp.getTime()+86400000);
      const next=nextUp<nextAmd?nextUp:nextAmd;
      const nextLabel=nextUp<nextAmd?'Upstream':'AMD';
      const diffMs=next-now;
      const diffH=Math.floor(diffMs/3600000);
      const diffM=Math.floor((diffMs%3600000)/60000);
      const timeStr=diffH>0?`${diffH}h ${diffM}m`:`${diffM}m`;

      // Latest nightly date shown in data
      const latestDate=health.amd?.builds?.[0]?.created_at?.slice(0,10)||'';
      function nightlyDateJS(iso){if(!iso)return'';const d=new Date(iso);if(d.getUTCHours()>=12)d.setUTCDate(d.getUTCDate()+1);return d.toISOString().slice(0,10);}
      const latestNightly=nightlyDateJS(health.amd?.builds?.[0]?.created_at);

      const nextP=h('p',{style:{color:C.m,fontSize:'12px',marginBottom:'16px'}});
      nextP.innerHTML=`Data through: <strong>${latestNightly||latestDate}</strong> &bull; Next nightly (${nextLabel}): <strong>${next.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})}</strong> (in ${timeStr})`;
      box.append(nextP);
    }

    const viewHost = h('div',{},[
      h('p',{text:'Loading gating history...',style:{color:C.m,fontSize:'13px',margin:'8px 0 0'}})
    ]);
    box.append(viewHost);
    const analytics=await analyticsPromise;
    viewHost.innerHTML='';
    renderHealthSubviewTabs(viewHost, {health, capacity, analytics, proposals, targets, targetCandidates});

    // Auto-refresh: poll every 5 min, re-render if data changed
    if(!window._ciHealthPoll){
      let lastGen=health?.generated_at||'';
      window._ciHealthPoll=setInterval(async()=>{
        try{
          const fresh=await J(`${CI}/ci_health.json`, {forceRefresh:true});
          if(fresh?.generated_at&&fresh.generated_at!==lastGen){
            lastGen=fresh.generated_at;
            if(window._updateSidebarTs) window._updateSidebarTs(lastGen);
            render();
          }
        }catch{}
      },5*60*1000);
    }
  }

  // Overlay for CI health cards
  // ═══════════════════════ GROUP OVERLAY (with links) ═══════════════════════
  function buildGroupTable(groups, showBoth) {
    const hasAnyAmd=groups.some(g=>!!g.amd), hasAnyUp=groups.some(g=>!!g.upstream);
    let tbl='<table style="width:100%;border-collapse:collapse;font-size:15px">';
    tbl+='<thead><tr>';
    tbl+='<th style="text-align:center;padding:10px 8px;border-bottom:2px solid var(--border,#30363d);color:var(--text-muted,#8b949e);font-size:13px;font-weight:600;width:36px">#</th>';
    tbl+='<th style="text-align:left;padding:10px 14px;border-bottom:2px solid var(--border,#30363d);color:var(--text-muted,#8b949e);font-size:14px;font-weight:600">Test Group</th>';
    if(showBoth||hasAnyAmd){
      tbl+='<th style="text-align:center;padding:10px 14px;border-bottom:2px solid var(--border,#30363d);color:#da3633;font-size:14px;font-weight:600">AMD Tests P/F/S</th>';
    }
    if(showBoth||hasAnyUp){
      tbl+='<th style="text-align:center;padding:10px 14px;border-bottom:2px solid var(--border,#30363d);color:#1f6feb;font-size:14px;font-weight:600">Upstream Tests P/F/S</th>';
    }
    tbl+='</tr></thead><tbody>';

    const sorted=[...groups].sort((a,b)=>(a.name||'').localeCompare(b.name||''));
    let rowNum=0;
    for(const g of sorted){
      const hasAmd=!!g.amd, hasUp=!!g.upstream;
      let rowBg='';
      if(showBoth&&!hasAmd) rowBg='background:rgba(218,54,51,0.08);';
      if(showBoth&&!hasUp) rowBg='background:rgba(31,111,235,0.08);';
      tbl+='<tr style="border-bottom:1px solid var(--border,#30363d);'+rowBg+'">';
      tbl+='<td style="text-align:center;padding:8px 8px;color:var(--text-muted,#8b949e);font-size:13px;width:36px">'+(++rowNum)+'</td>';

      // Name cell with red/blue link icons for ALL groups
      let nameHtml=escapeHtml(g.name);
      nameHtml+=' ';
      if(hasAmd) nameHtml+=LinkRegistry.bk.iconLink(g.name, 'amd') + ' ';
      if(hasUp) nameHtml+=LinkRegistry.bk.iconLink(g.name, 'upstream');
      tbl+='<td style="padding:8px 14px">'+nameHtml+'</td>';

      if(showBoth||hasAnyAmd){
        if(hasAmd){
          const ap=g.amd.passed||0,af=g.amd.failed||0,ak=g.amd.skipped||0;
          tbl+='<td style="text-align:center;padding:8px 14px"><span style="color:#238636;font-weight:600">'+ap.toLocaleString()+'</span>/<span style="color:'+(af>0?'#da3633':'var(--text-muted,#8b949e)')+';font-weight:600">'+af+'</span>/<span style="color:var(--text-muted,#8b949e)">'+ak.toLocaleString()+'</span></td>';
        } else {
          tbl+='<td style="text-align:center;padding:8px 14px"><span style="color:#da3633;font-weight:600">not in AMD CI</span></td>';
        }
      }
      if(showBoth||hasAnyUp){
        if(hasUp){
          const up=g.upstream.passed||0,uf=g.upstream.failed||0,us=g.upstream.skipped||0;
          tbl+='<td style="text-align:center;padding:8px 14px"><span style="color:#238636;font-weight:600">'+up.toLocaleString()+'</span>/<span style="color:'+(uf>0?'#da3633':'var(--text-muted,#8b949e)')+';font-weight:600">'+uf+'</span>/<span style="color:var(--text-muted,#8b949e)">'+us.toLocaleString()+'</span></td>';
        } else {
          tbl+='<td style="text-align:center;padding:8px 14px"><span style="color:#1f6feb;font-weight:600">not in Upstream</span></td>';
        }
      }
      tbl+='</tr>';
    }
    tbl+='</tbody></table>';
    return tbl;
  }

  function showOverlayPanel(titleHtml, bodyHtml) {
    const backdrop=document.createElement('div');
    backdrop.className='overlay-backdrop';
    backdrop.onclick=e=>{if(e.target===backdrop)backdrop.remove()};

    const panel=document.createElement('div');
    panel.className='overlay-panel';

    const header=document.createElement('div');
    header.className='overlay-header';
    header.innerHTML='<h3>'+titleHtml+'</h3>';
    const closeBtn=document.createElement('button');
    closeBtn.className='overlay-close';
    closeBtn.innerHTML='&times;';
    closeBtn.onclick=()=>backdrop.remove();
    header.appendChild(closeBtn);

    const body=document.createElement('div');
    body.className='overlay-body';
    body.innerHTML=bodyHtml;

    panel.append(header,body);
    backdrop.appendChild(panel);
    document.body.appendChild(backdrop);
    document.addEventListener('keydown',function esc(e){if(e.key==='Escape'){backdrop.remove();document.removeEventListener('keydown',esc)}});
  }

  function showGroupOverlay_health(title, groups, color, totalFail, totalUpFail, showAll) {
    let countHtml;
    if(totalFail!=null){
      countHtml=`<span style="color:${C.r}">${totalFail.toLocaleString()}</span>`;
      if(totalUpFail) countHtml+=` / <span style="color:${C.b}">${totalUpFail.toLocaleString()}</span>`;
      countHtml+=` tests across ${groups.length} groups`;
    } else if(showAll) {
      const failCount=groups.filter(g=>(g.amd?.failed||0)>0).length;
      const passCount=groups.length-failCount;
      countHtml=`<span style="color:${C.g}">${passCount} passing</span>, <span style="color:${C.r}">${failCount} failing</span> of ${groups.length}`;
    } else {
      countHtml=`${groups.length}`;
    }
    // Sort: failing first, then passing, then alphabetical within each
    const sorted=[...groups].sort((a,b)=>{
      const af=(a.amd?.failed||0)>0?0:1, bf=(b.amd?.failed||0)>0?0:1;
      if(af!==bf) return af-bf;
      return (a.name||'').localeCompare(b.name||'');
    });
    const titleHtml=`<span style="color:${color}">${title}</span> <span style="color:var(--text-muted);font-weight:400">(${countHtml})</span>`;
    showOverlayPanel(titleHtml, buildGroupTable(sorted, true));
  }

  function showParityOverlay(both, amdOnly, upOnly) {
    const tabs=[
      {label:`Common (${both.length})`,color:C.p,groups:both,showBoth:true},
      {label:`AMD-only (${amdOnly.length})`,color:C.r,groups:amdOnly,showBoth:false},
      {label:`Upstream-only (${upOnly.length})`,color:C.b,groups:upOnly,showBoth:false},
    ];
    let tabBar='<div style="display:flex;gap:8px;margin-bottom:16px">';
    tabs.forEach((t,i)=>{
      tabBar+=`<button onclick="document.querySelectorAll('._parity-tab-body').forEach((e,j)=>{e.style.display=j===${i}?'':'none'});this.parentNode.querySelectorAll('button').forEach((b,j)=>{b.style.background=j===${i}?'var(--bg2,#0d1117)':'';b.style.borderColor=j===${i}?'${t.color}':'var(--border,#30363d)'})" style="padding:6px 14px;border-radius:6px;border:1px solid ${i===0?t.color:'var(--border,#30363d)'};background:${i===0?'var(--bg2,#0d1117)':''};color:${t.color};cursor:pointer;font-size:13px;font-weight:600">${t.label}</button>`;
    });
    tabBar+='</div>';
    let bodies='';
    tabs.forEach((t,i)=>{
      bodies+=`<div class="_parity-tab-body" style="${i>0?'display:none':''}">`;
      bodies+=buildGroupTable(t.groups,t.showBoth);
      bodies+='</div>';
    });
    showOverlayPanel(
      `<span style="color:${C.p}">Coverage Parity</span> <span style="color:var(--text-muted);font-weight:400">(${both.length+amdOnly.length+upOnly.length} groups)</span>`,
      tabBar+bodies
    );
  }

  const obs=new MutationObserver(()=>{
    const p=document.getElementById('tab-ci-health');
    if(p?.classList.contains('active')&&!p.dataset.loaded){p.dataset.loaded='1';render()}
  });
  document.addEventListener('DOMContentLoaded',()=>{
    if(window.__DASHBOARD_V2__) return;
    const p=document.getElementById('tab-ci-health');
    if(p){obs.observe(p,{attributes:true,attributeFilter:['class']});
      if(p.classList.contains('active')&&!p.dataset.loaded){p.dataset.loaded='1';render()}}
  });
})();
