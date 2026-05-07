/**
 * CI Analytics — Streamlined comparison-first view.
 * Side-by-side is the default and only pipeline view.
 * Queue comparison: AMD queues vs Other agents (NVIDIA on top).
 */
(function() {
  const _s=getComputedStyle(document.documentElement);
  const C = {g:_s.getPropertyValue('--accent-green').trim()||'#238636',y:_s.getPropertyValue('--accent-orange').trim()||'#d29922',o:'#db6d28',r:_s.getPropertyValue('--badge-closed').trim()||'#da3633',b:_s.getPropertyValue('--accent-blue').trim()||'#1f6feb',m:_s.getPropertyValue('--text-muted').trim()||'#656d76',t:_s.getPropertyValue('--text').trim()||'#e6edf3',bg:_s.getPropertyValue('--card-bg').trim()||'#161b22',bg2:_s.getPropertyValue('--bg').trim()||'#0d1117',bd:_s.getPropertyValue('--border').trim()||'#30363d',sf:'#a371f7'};
  const _cb = () => '?_=' + Math.floor(Date.now()/1000);
  const J = async u => { try { const r = await fetch(u + _cb()); return r.ok ? r.json() : null } catch { return null } };

  const h = el;  // shared element factory defined in utils.js
  const ANALYTICS_WINDOW_ORDER = ['1d', '3d', '7d', '14d'];
  const ANALYTICS_WINDOW_LABEL = {
    '1d': 'Last 24h',
    '3d': 'Last 3d',
    '7d': 'Last 7d',
    '14d': 'Last 14d',
  };

  function fmtDur(mins) {
    if (mins == null) return '-';
    if (mins < 60) return `${Math.round(mins)}m`;
    const hr = Math.floor(mins/60), mn = Math.round(mins%60);
    return `${hr}h ${mn}m`;
  }

  function stateDot(state, size='10px') {
    const colors = {passed:C.g,failed:C.r,soft_fail:C.sf,canceled:C.m,running:C.b,scheduled:C.y,timed_out:C.o};
    return h('span',{style:{display:'inline-block',width:size,height:size,borderRadius:'50%',background:colors[state]||C.m,transition:'transform 0.2s'}});
  }

  function metricCard(label, value, sub, color) {
    return h('div',{style:{background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'8px',padding:'16px 20px',borderTop:`3px solid ${color||C.b}`,transition:'transform 0.2s, box-shadow 0.2s'}},[
      h('div',{text:label,style:{fontSize:'11px',color:C.m,textTransform:'uppercase',letterSpacing:'.5px',marginBottom:'4px'}}),
      h('div',{html:String(value),style:{fontSize:'28px',fontWeight:'800',color:color||C.t,lineHeight:'1.1'}}),
      sub?h('div',{html:sub,style:{fontSize:'12px',color:C.m,marginTop:'4px'}}):null,
    ]);
  }

  function fmtPct(value, digits=0) {
    if (value == null || !Number.isFinite(value)) return '-';
    return (value * 100).toFixed(digits) + '%';
  }

  function summarizeAmdMatrix(matrixData) {
    const rows = matrixData?.rows || [];
    const architectures = matrixData?.architectures || [];
    let hardwareCells = 0, matchedCells = 0, passingCells = 0, failingCells = 0;
    let attentionFamilies = 0;
    let waitingCells = 0, unknownCells = 0;
    for (const row of rows) {
      const cells = row.cells || {};
      let rowNeedsAttention = false;
      for (const arch of architectures.map(a => a.id)) {
        const cell = cells[arch] || {};
        if (!cell.exists) continue;
        hardwareCells += 1;
        if (cell.latest_matched) matchedCells += 1;
        const state = cell.latest_state || '';
        if (state === 'passed') passingCells += 1;
        else if (['failed', 'timed_out', 'broken', 'soft_fail'].includes(state)) {
          failingCells += 1;
          rowNeedsAttention = true;
        }
        else if (['running', 'scheduled', 'assigned'].includes(state)) {
          waitingCells += 1;
          rowNeedsAttention = true;
        }
        else unknownCells += 1;
      }
      if (rowNeedsAttention) attentionFamilies += 1;
    }
    const summary = matrixData?.summary || {};
    hardwareCells = summary.hardware_cells ?? hardwareCells;
    matchedCells = summary.latest_matched_cells ?? matchedCells;
    passingCells = summary.passing_cells ?? passingCells;
    failingCells = summary.failing_cells ?? failingCells;
    waitingCells = summary.waiting_cells ?? waitingCells;
    unknownCells = summary.unknown_cells ?? unknownCells;
    return {
      families: summary.unique_groups ?? rows.length,
      architectures: summary.architecture_count ?? architectures.length,
      hardwareCells,
      matchedCells,
      unmatchedCells: Math.max(0, hardwareCells - matchedCells),
      passingCells,
      failingCells,
      attentionFamilies,
      waitingCells,
      unknownCells,
      passRate: matchedCells > 0 ? passingCells / matchedCells : null,
      matchRate: hardwareCells > 0 ? matchedCells / hardwareCells : null,
    };
  }

  function progressBar(value, max, color, w='200px') {
    const pct = max>0 ? Math.round(value/max*100) : 0;
    return h('div',{style:{display:'inline-flex',alignItems:'center',gap:'6px'}},[
      h('div',{style:{width:w,height:'8px',background:C.bd,borderRadius:'4px',overflow:'hidden'}},[
        h('div',{style:{width:pct+'%',height:'100%',background:color,borderRadius:'4px',transition:'width .6s cubic-bezier(0.4,0,0.2,1)'}}),
      ]),
      h('span',{text:pct+'%',style:{fontSize:'12px',color,fontWeight:'600',minWidth:'36px'}}),
    ]);
  }

  const thS = a => ({textAlign:a||'left',padding:'8px 12px',borderBottom:`2px solid ${C.bd}`,color:C.m,fontSize:'10px',textTransform:'uppercase',fontWeight:'600',whiteSpace:'nowrap'});
  const tdS = a => ({textAlign:a||'left',padding:'6px 12px',borderBottom:`1px solid ${C.bd}`,color:C.t,fontSize:'13px'});

  // ═══════════ COMPARISON VIEW (default) ═══════════

  function analyticsWindowKeys(block) {
    const keys = Object.keys(block?.windows || {});
    const ordered = ANALYTICS_WINDOW_ORDER.filter(k => keys.includes(k));
    return ordered.length ? ordered : keys.sort((a, b) => parseInt(a, 10) - parseInt(b, 10));
  }

  function analyticsWindowData(block, windowKey) {
    return block?.windows?.[windowKey] || block || {};
  }

  function renderComparisonView(box, data, pipelines) {
    const firstBlock = pipelines.map(p => data[p]).find(Boolean) || {};
    const availableWindows = analyticsWindowKeys(firstBlock);
    let activeWindow = availableWindows.includes(firstBlock.default_window)
      ? firstBlock.default_window
      : (availableWindows.includes('7d') ? '7d' : availableWindows[0]);

    if (availableWindows.length) {
      const windowRow = h('div',{style:{display:'flex',gap:'8px',alignItems:'center',flexWrap:'wrap',marginBottom:'14px'}});
      windowRow.append(h('span',{text:'Comparison window:',style:{color:C.m,fontSize:'12px',fontWeight:'600',textTransform:'uppercase',letterSpacing:'.5px'}}));
      const windowBtns = {};
      for (const key of availableWindows) {
        const btn = h('button',{text:ANALYTICS_WINDOW_LABEL[key] || key,style:{
          background:key===activeWindow?C.b:C.bd,border:'none',color:C.t,
          padding:'6px 14px',borderRadius:'3px',cursor:'pointer',
          fontSize:'13px',fontFamily:'inherit',fontWeight:key===activeWindow?'600':'400',
        }});
        btn.onclick = () => {
          activeWindow = key;
          Object.entries(windowBtns).forEach(([btnKey, node]) => {
            node.style.background = btnKey === key ? C.b : C.bd;
            node.style.fontWeight = btnKey === key ? '600' : '400';
          });
          rebuild();
        };
        windowBtns[key] = btn;
        windowRow.append(btn);
      }
      box.append(windowRow);
    }

    const note = h('div',{style:{
      padding:'10px 14px',background:C.b+'12',border:`1px solid ${C.b}33`,
      borderRadius:'8px',marginBottom:'16px',fontSize:'13px',color:C.t
    }});
    box.append(note);

    const dyn = h('div');
    box.append(dyn);

    function rebuild() {
      dyn.innerHTML = '';
      const activeLabel = ANALYTICS_WINDOW_LABEL[activeWindow] || activeWindow || `${firstBlock.days || 14}d`;
      note.textContent = availableWindows.length
        ? `Rankings below use ${activeLabel}. Shorter windows forget older hardware automatically, so MI325-era behavior ages out as MI300 takes over. Recent Builds and Test Group Trends still keep the wider history.`
        : 'Rankings below use the collected analytics span. Once the hourly collector refreshes, this view will switch to shorter precomputed windows so older hardware ages out naturally.';

      const summaryRow = h('div',{style:{display:'grid',gridTemplateColumns:'repeat(4,1fr)',gap:'12px',marginBottom:'20px'}});
      let totalBuilds = 0, totalFailures = 0, totalJobs = 0;
      const topFails = [];
      const topDurs = [];

      for (const p of pipelines) {
        const d = data[p];
        if (!d) continue;
        const wd = analyticsWindowData(d, activeWindow);
        totalBuilds += wd.summary?.total_builds || 0;
        totalFailures += wd.summary?.jobs_with_failures || 0;
        totalJobs += wd.summary?.total_jobs_tracked || 0;
        if (wd.failure_ranking?.[0]) topFails.push({...wd.failure_ranking[0], pipeline: p});
        if (wd.duration_ranking?.[0]) topDurs.push({...wd.duration_ranking[0], pipeline: p});
      }

      topFails.sort((a,b) => b.fail_rate - a.fail_rate);
      topDurs.sort((a,b) => (b.median_dur||0) - (a.median_dur||0));

      summaryRow.append(metricCard('Nightly Builds', totalBuilds, `${activeLabel} across ${pipelines.length} pipelines`, C.b));
      summaryRow.append(metricCard('Jobs with Failures', totalFailures, `of ${totalJobs} tracked`, totalFailures > 0 ? C.r : C.g));
      summaryRow.append(metricCard('Worst Failure Rate', topFails[0] ? `${topFails[0].fail_rate}%` : '0%', topFails[0]?.name?.slice(0,30) || '', C.r));
      summaryRow.append(metricCard('Slowest Job (p50)', topDurs[0] ? fmtDur(topDurs[0].median_dur) : '-', topDurs[0]?.name?.slice(0,30) || '', C.o));
      dyn.append(summaryRow);

      const grid = h('div',{style:{display:'grid',gridTemplateColumns:`repeat(${pipelines.length},1fr)`,gap:'20px',marginBottom:'20px'}});
      for (const p of pipelines) {
        const d = data[p];
        if (!d) continue;
        const wd = analyticsWindowData(d, activeWindow);
        const col = h('div');
        col.append(h('h3',{text:d.display_name || p,style:{marginBottom:'12px',color:C.b,borderBottom:`2px solid ${C.b}`,paddingBottom:'6px',fontSize:'14px',fontWeight:'700'}}));

        const s = wd.summary || d.summary || {};
        const miniRow = h('div',{style:{display:'grid',gridTemplateColumns:'1fr 1fr',gap:'8px',marginBottom:'16px'}});
        miniRow.append(metricCard('Builds', s.total_builds || 0, activeLabel, C.b));
        miniRow.append(metricCard('Failures', s.jobs_with_failures || 0, `of ${s.total_jobs_tracked || 0}`, (s.jobs_with_failures || 0) > 0 ? C.r : C.g));
        col.append(miniRow);

        if (wd.failure_ranking?.length) {
          const sec = h('div',{style:{background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'8px',padding:'12px',marginBottom:'12px'}});
          sec.append(h('div',{text:'Top Failures',style:{fontSize:'12px',fontWeight:'700',color:C.m,textTransform:'uppercase',marginBottom:'8px'}}));
          for (const j of wd.failure_ranking.slice(0,5)) {
            const row = h('div',{style:{display:'flex',justifyContent:'space-between',alignItems:'center',padding:'4px 0',fontSize:'12px'}});
            row.append(h('span',{text:j.name, style:{flex:'1',marginRight:'8px',wordBreak:'break-word'}}));
            row.append(progressBar(j.fail_rate, 100, j.fail_rate >= 50 ? C.r : j.fail_rate >= 20 ? C.o : C.y, '80px'));
            sec.append(row);
          }
          col.append(sec);
        }

        if (wd.duration_ranking?.length) {
          const sec = h('div',{style:{background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'8px',padding:'12px'}});
          sec.append(h('div',{text:'Slowest Jobs',style:{fontSize:'12px',fontWeight:'700',color:C.m,textTransform:'uppercase',marginBottom:'8px'}}));
          for (const j of wd.duration_ranking.slice(0,5)) {
            const row = h('div',{style:{display:'flex',justifyContent:'space-between',alignItems:'center',padding:'4px 0',fontSize:'12px'}});
            row.append(h('span',{text:j.name, style:{flex:'1',marginRight:'8px',wordBreak:'break-word'}}));
            row.append(h('span',{text:fmtDur(j.median_dur),style:{color:C.o,fontWeight:'600',minWidth:'50px',textAlign:'right'}}));
            sec.append(row);
          }
          col.append(sec);
        }

        grid.append(col);
      }
      dyn.append(grid);

      const chartSection = h('div',{style:{display:'grid',gridTemplateColumns:`repeat(${pipelines.length},1fr)`,gap:'16px',marginBottom:'16px'}});
      for (const p of pipelines) {
        const d = data[p];
        if (!d) continue;
        const wd = analyticsWindowData(d, activeWindow);
        const builds = (wd.builds || d.builds || []).filter(b => (b.total_jobs||0) > 10).slice(0, 21).reverse();
        if (builds.length < 2) continue;

        const labels = builds.map(b => (b.date||'').slice(5));
        const rates = builds.map(b => {
          const total = (b.passed||0) + (b.failed||0) + (b.soft_failed||0);
          return total > 0 ? +((b.passed||0) / total * 100).toFixed(1) : null;
        });
        const allVals = rates.filter(v => v != null);
        const yMin = allVals.length ? Math.max(0, Math.floor(Math.min(...allVals) / 5) * 5 - 5) : 0;

        const section = h('div',{style:{background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'8px',padding:'20px'}});
        section.append(h('h3',{text:`${d.display_name || p} — Job Pass Rate`,style:{marginBottom:'6px',fontSize:'14px'}}));
        section.append(h('p',{text:activeLabel,style:{margin:'0 0 12px',fontSize:'12px',color:C.m}}));
        const canvas = h('canvas',{style:{maxHeight:'180px'}});
        section.append(canvas);
        chartSection.append(section);

        new Chart(canvas, {
          type: 'line',
          data: {
            labels,
            datasets: [{
              label: 'Job Pass Rate',
              data: rates,
              borderColor: p.includes('amd') ? C.r : C.b,
              backgroundColor: (p.includes('amd') ? C.r : C.b) + '15',
              tension: 0.3, fill: true, pointRadius: 3, spanGaps: true,
            }]
          },
          options: {
            responsive: true,
            plugins: {
              legend: { display: false },
              tooltip: { callbacks: { label: ctx => ctx.parsed.y != null ? ctx.parsed.y + '% jobs passed' : 'no data' } },
            },
            scales: {
              x: { ticks: { color: C.m, font: { size: 10 } }, grid: { color: C.bd } },
              y: { min: yMin, max: 100, ticks: { color: C.m, callback: v => v + '%' }, grid: { color: C.bd } },
            }
          }
        });
      }
      if (chartSection.childElementCount) dyn.append(chartSection);
    }

    rebuild();
  }

  // ═══════════ QUEUE COMPARISON VIEW ═══════════

  function isAmdQueue(name) {
    return name.startsWith('amd_') || name.startsWith('amd-');
  }

  function isNvidiaQueue(name) {
    return ['gpu_1_queue','gpu_4_queue','B200','H200','a100_queue','mithril-h100-pool','nebius-h200','perf-B200','perf-h200'].includes(name);
  }

  function renderQueueComparison(box, data, pipelines) {
    // Build job-name → {queues, median_wait, p90_wait} lookup from duration_ranking
    const jobQueueMap = {};
    for (const p of pipelines) {
      const d = data[p];
      if (!d) continue;
      for (const jr of (d.duration_ranking || [])) {
        if (jr.queues?.length && jr.name) {
          jobQueueMap[jr.name] = {queues: jr.queues, median_wait: jr.median_wait, p90_wait: jr.p90_wait, pipeline: p};
        }
      }
    }

    // Gather all builds with dates
    const allBuilds = [];
    for (const p of pipelines) {
      const d = data[p];
      if (!d) continue;
      for (const b of (d.builds || [])) {
        if (b.date) allBuilds.push({...b, pipeline: p});
      }
    }
    allBuilds.sort((a,b) => a.date.localeCompare(b.date));

    // Time segment options — 'Last build' uses days=0 as sentinel
    const segments = [
      {label:'Last build',days:0},{label:'3d',days:3},{label:'7d',days:7},{label:'14d',days:14},{label:'All',days:9999}
    ];
    let activeDays = 9999;

    const dynContainer = h('div');

    function computeQueueStats(filteredBuilds) {
      // Check if per-build jobs have queue data (new collector format)
      const hasQ = filteredBuilds.some(b => (b.jobs||[]).some(j => j.q));

      const qMap = {};
      for (const b of filteredBuilds) {
        for (const j of (b.jobs||[])) {
          // Determine queue: use per-job 'q' field if available, else look up from duration_ranking
          let queues, wait, pipeline = b.pipeline;
          if (j.q) {
            queues = [j.q];
            wait = j.wait;
          } else {
            const lookup = jobQueueMap[j.name];
            if (!lookup) continue;
            queues = lookup.queues;
            wait = lookup.median_wait; // best estimate from aggregated data
            pipeline = lookup.pipeline;
          }
          for (const q of queues) {
            if (!qMap[q]) qMap[q] = {queue:q, jobs:0, waits:[], pipeline};
            qMap[q].jobs++;
            if (wait != null) qMap[q].waits.push(wait);
          }
        }
      }

      const amdQueues = [], otherQueues = [];
      for (const d of Object.values(qMap)) {
        const w = d.waits.sort((a,b)=>a-b);
        const entry = {queue:d.queue, jobs:d.jobs, pipeline:d.pipeline,
          median_wait: w.length ? w[Math.floor(w.length/2)] : null,
          p90_wait: w.length>1 ? w[Math.floor(w.length*0.9)] : null,
        };
        if (isAmdQueue(d.queue) && d.pipeline.includes('amd')) amdQueues.push(entry);
        else if (!isAmdQueue(d.queue)) otherQueues.push(entry);
      }
      return {amdQueues, otherQueues, hasQ};
    }

    function rebuildView() {
      dynContainer.innerHTML = '';
      let filteredBuilds;
      if (activeDays === 0) {
        // Last build: pick the most recent build per pipeline
        const seen = {};
        const sorted = [...allBuilds].sort((a,b) => b.date.localeCompare(a.date));
        filteredBuilds = sorted.filter(b => { if (seen[b.pipeline]) return false; seen[b.pipeline]=true; return true; });
      } else {
        const cutoff = activeDays < 9999 ? new Date(Date.now() - activeDays * 86400000).toISOString().slice(0,10) : '';
        filteredBuilds = cutoff ? allBuilds.filter(b => b.date >= cutoff) : allBuilds;
      }
      const filteredDates = [...new Set(filteredBuilds.map(b=>b.date))].sort();
      const dateRange = filteredDates.length ? filteredDates[0] + ' \u2013 ' + filteredDates[filteredDates.length-1] : '';

      const {amdQueues, otherQueues, hasQ} = computeQueueStats(filteredBuilds);

      otherQueues.sort((a,b) => {
        const aNv = isNvidiaQueue(a.queue)?0:1, bNv = isNvidiaQueue(b.queue)?0:1;
        return aNv !== bNv ? aNv - bNv : (b.median_wait||0) - (a.median_wait||0);
      });
      amdQueues.sort((a,b) => (b.median_wait||0) - (a.median_wait||0));

      // Data source banner
      const bkQueuesUrl = LinkRegistry.bk.queues();
      const srcNote = h('div',{style:{padding:'10px 14px',background:C.b+'12',border:`1px solid ${C.b}33`,borderRadius:'6px',marginBottom:'16px',fontSize:'13px',color:C.t,display:'flex',justifyContent:'space-between',alignItems:'center',flexWrap:'wrap',gap:'8px'}});
      const waitNote = hasQ ? '' : ' Wait times are estimated from overall job averages.';
      srcNote.append(h('span',{html:`Aggregated from <strong>${filteredBuilds.length}</strong> nightly builds${dateRange?' ('+dateRange+')':''}.${waitNote}`}));
      srcNote.append(h('a',{text:'View live queues on Buildkite \u2197',href:bkQueuesUrl,target:'_blank',style:{color:C.b,fontSize:'13px',fontWeight:'600',textDecoration:'none',whiteSpace:'nowrap'}}));
      dynContainer.append(srcNote);

      const totalAmdJobs = amdQueues.reduce((a,q) => a + q.jobs, 0);
      const totalOtherJobs = otherQueues.reduce((a,q) => a + q.jobs, 0);
      const topAmdWait = amdQueues[0], topOtherWait = otherQueues[0];

      const summaryRow = h('div',{style:{display:'grid',gridTemplateColumns:'repeat(4,1fr)',gap:'12px',marginBottom:'20px'}});
      summaryRow.append(metricCard('AMD Queue Jobs', totalAmdJobs.toLocaleString(), `${amdQueues.length} queues`, C.r));
      summaryRow.append(metricCard('Other Agent Jobs', totalOtherJobs.toLocaleString(), `${otherQueues.length} queues`, C.b));
      summaryRow.append(metricCard('AMD Longest Wait', fmtDur(topAmdWait?.median_wait), topAmdWait?.queue||'', C.o));
      summaryRow.append(metricCard('Other Longest Wait', fmtDur(topOtherWait?.median_wait), topOtherWait?.queue||'', C.o));
      dynContainer.append(summaryRow);

      const grid = h('div',{style:{display:'grid',gridTemplateColumns:'1fr 1fr',gap:'20px'}});
      const amdCol = h('div');
      amdCol.append(h('h3',{text:'AMD Queues',style:{marginBottom:'12px',color:C.r,borderBottom:`2px solid ${C.r}`,paddingBottom:'6px',fontSize:'14px',fontWeight:'700'}}));
      renderQueueTable(amdCol, amdQueues);
      grid.append(amdCol);
      const otherCol = h('div');
      otherCol.append(h('h3',{text:'Other Agents',style:{marginBottom:'12px',color:C.b,borderBottom:`2px solid ${C.b}`,paddingBottom:'6px',fontSize:'14px',fontWeight:'700'}}));
      renderQueueTable(otherCol, otherQueues);
      grid.append(otherCol);
      dynContainer.append(grid);
    }

    // Segment selector bar
    const segBar = h('div',{style:{display:'flex',gap:'2px',marginBottom:'16px',flexWrap:'wrap'}});
    segBar.append(h('span',{text:'Time window:',style:{color:C.m,fontSize:'14px',marginRight:'6px',alignSelf:'center'}}));
    const segBtns = {};
    for (const s of segments) {
      const isActive = s.days === activeDays;
      const btn = h('button',{text:s.label,style:{
        background:isActive?C.b:C.bd, border:'none', color:C.t,
        padding:'4px 12px', borderRadius:'3px', cursor:'pointer',
        fontSize:'13px', fontFamily:'inherit', fontWeight:isActive?'600':'400'
      }});
      btn.onclick = () => {
        activeDays = s.days;
        for (const [,b] of Object.entries(segBtns)) { b.style.background=C.bd; b.style.fontWeight='400'; }
        btn.style.background=C.b; btn.style.fontWeight='600';
        rebuildView();
      };
      segBtns[s.days] = btn;
      segBar.append(btn);
    }
    box.append(segBar);
    box.append(dynContainer);
    rebuildView();
  }

  function renderQueueTable(box, queues) {
    if (!queues.length) { box.append(h('p',{text:'No queue data.',style:{color:C.m,fontSize:'13px'}})); return; }

    const BK_QUEUES_URL = LinkRegistry.bk.queues();

    const section = h('div',{style:{background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'8px',padding:'16px'}});
    const tbl = h('table',{style:{width:'100%',borderCollapse:'collapse',fontSize:'13px'}});
    const thead = h('thead');
    const hr = h('tr');
    hr.append(h('th',{text:'Queue',style:thS()}));
    hr.append(h('th',{text:'p50',style:thS('center')}));
    hr.append(h('th',{text:'p90',style:thS('center')}));
    hr.append(h('th',{text:'Jobs',style:thS('center')}));
    thead.append(hr);
    tbl.append(thead);

    const tbody = h('tbody');
    const maxWait = queues[0]?.median_wait || 1;
    for (const q of queues) {
      const tr = h('tr');
      const isNv = isNvidiaQueue(q.queue);
      const labelColor = isAmdQueue(q.queue) ? C.r : isNv ? C.b : C.m;
      // Queue name as clickable link to Buildkite
      const nameCell = h('td',{style:{...tdS(),fontWeight:'600'}});
      nameCell.append(h('span',{style:{width:'6px',height:'6px',borderRadius:'50%',background:labelColor,display:'inline-block',marginRight:'6px'}}));
      const qLink = h('a',{text:q.queue,href:BK_QUEUES_URL,target:'_blank',style:{color:C.t,textDecoration:'none'}});
      qLink.onmouseenter = () => { qLink.style.color = C.b; qLink.style.textDecoration = 'underline'; };
      qLink.onmouseleave = () => { qLink.style.color = C.t; qLink.style.textDecoration = 'none'; };
      nameCell.append(qLink);
      tr.append(nameCell);

      const wColor = (q.median_wait||0) > 10 ? C.r : (q.median_wait||0) > 5 ? C.o : (q.median_wait||0) > 2 ? C.y : C.g;
      tr.append(h('td',{text:fmtDur(q.median_wait),style:{...tdS('center'),color:wColor,fontWeight:'600'}}));
      tr.append(h('td',{text:fmtDur(q.p90_wait),style:tdS('center')}));
      tr.append(h('td',{text:q.jobs.toLocaleString(),style:tdS('center')}));
      tbody.append(tr);
    }
    tbl.append(tbody);
    section.append(tbl);
    box.append(section);
  }

  // ═══════════ RECENT BUILDS MATRIX ═══════════

  const _normCache = {};
  function normalizeJobName(name) {
    if (_normCache[name] !== undefined) return _normCache[name];
    var n = name;
    // Strip hardware prefixes: "mi250_1: ", "gpu_1: "
    n = n.replace(/^(mi\d+_\d+|gpu_\d+|amd_\w+):\s*/i, '');
    // Strip ALL hardware parenthesized suffixes: (B200-MI355), (H100-MI250), etc.
    n = n.replace(/\s*\([A-Za-z0-9]+-MI\d+\)\s*/g, '');
    // Strip standalone GPU suffixes: (B200), (H100), (2xH100)
    n = n.replace(/\s*\(\d*x?[A-Z]\d{2,4}\)\s*/g, '');
    // Strip (MI250), (MI325) etc
    n = n.replace(/\s*\(MI\d+\)\s*/g, '');
    // Strip GPU count parentheticals: (4 GPUs), (2 GPU)
    n = n.replace(/\s*\(\s*\d+\s+GPUs?\s*\)/gi, '');
    // Normalize version-like dots to hyphens (e.g., "Qwen3.5" → "Qwen3-5")
    n = n.replace(/(\d)\.(\d)/g, '$1-$2');
    // Strip parallelism marker: %1, %2
    n = n.replace(/\s+%\d+$/, '');
    // Only strip trailing shard index for known %N-expanded patterns
    n = n.trim();
    if (typeof _stripShardIndex === 'function') n = _stripShardIndex(n);
    // Lowercase to match backend normalization
    var result = n.trim().toLowerCase();
    _normCache[name] = result;
    return result;
  }

  // Area classification for test groups
  const AREA_PATTERNS = [
    ['Kernels', /^kernel/],['Attention', /attention/],['Distributed', /distributed|comm ops|torchrun/],
    ['Models', /models? test|models? \(/],['Multi-Modal', /multi-modal/],['Entrypoints', /entrypoint/],
    ['Compile', /compile|pytorch|fullgraph/],['Engine', /engine|e2e/],['LoRA', /lora/],
    ['Spec Decode', /spec.?dec/],['LM Eval', /lm.?eval|gpqa/],['Quantization', /quantiz/],
    ['V1', /^v1 /],['Benchmarks', /benchmark/],['Fusion', /fusion/],
  ];
  function getArea(name) {
    const l = name.toLowerCase();
    for (const [area, re] of AREA_PATTERNS) if (re.test(l)) return area;
    return 'Other';
  }

  function renderBuildsMatrix(box, data, pipelines) {
    const amdData = data['amd-ci'] || data[pipelines[0]];
    const upData = data['ci'] || data[pipelines[1]];
    if (!amdData?.builds?.length) { box.append(h('p',{text:'No build data.',style:{color:C.m}})); return; }

    const amdBuilds = amdData.builds.filter(b=>(b.jobs||[]).length>10).slice(0,21);
    const upBuilds = (upData?.builds||[]).filter(b=>(b.jobs||[]).length>10).slice(0,21);

    const dates = [];
    const amdByDate = {}, upByDate = {};
    for (const b of amdBuilds) { const d=b.date||b.created_at?.slice(0,10); if(!amdByDate[d]||(b.jobs||[]).length>(amdByDate[d].jobs||[]).length) amdByDate[d]=b; if(!dates.includes(d))dates.push(d); }
    for (const b of upBuilds) { const d=b.date||b.created_at?.slice(0,10); if(!upByDate[d]||(b.jobs||[]).length>(upByDate[d].jobs||[]).length) upByDate[d]=b; if(!dates.includes(d))dates.push(d); }
    dates.sort().reverse();

    const allGroups = new Set();
    for (const b of amdBuilds.slice(0,5)) (b.jobs||[]).forEach(j=>allGroups.add(normalizeJobName(j.name)));
    for (const b of upBuilds.slice(0,5)) (b.jobs||[]).forEach(j=>allGroups.add(normalizeJobName(j.name)));

    // Precompute job maps for all builds
    const _jobMapCache = new WeakMap();
    function buildJobMap(build) {
      if (!build) return {};
      if (_jobMapCache.has(build)) return _jobMapCache.get(build);
      const m={};
      (build?.jobs||[]).forEach(j=>{
        const n=normalizeJobName(j.name); const prev=m[n];
        const st=j.state==='soft_fail'?'failed':j.state;
        if(!prev||st==='failed'||(st==='passed'&&prev!=='failed'))
          m[n]=st;
      });
      _jobMapCache.set(build, m);
      return m;
    }

    // Group by area
    const byArea = {};
    for (const gn of allGroups) {
      const area = getArea(gn);
      (byArea[area] = byArea[area] || []).push(gn);
    }
    for (const a in byArea) byArea[a].sort();

    const stateColor = s => s==='passed'?C.g:(s==='failed'||s==='soft_fail')?C.r:C.bd;
    const useDates = dates.slice(0, 21).reverse();

    // Header + next nightly indicator
    box.append(h('h3',{text:'Test Group History',style:{marginBottom:'4px',fontSize:'18px'}}));
    const latestDate=useDates[useDates.length-1]||'';
    if(latestDate){
      const now=new Date();
      // AMD nightly runs ~06:00 UTC — column date = same calendar day
      const todayAmd=new Date(Date.UTC(now.getUTCFullYear(),now.getUTCMonth(),now.getUTCDate(),6,0));
      const nextUp=todayAmd>now?todayAmd:new Date(todayAmd.getTime()+86400000);
      const diffMs=nextUp-now;
      const diffH=Math.floor(diffMs/3600000);
      const diffM=Math.floor((diffMs%3600000)/60000);
      const timeStr=diffH>0?`${diffH}h ${diffM}m`:`${diffM}m`;
      box.append(h('p',{html:`Data through: <strong>${latestDate}</strong> &bull; Next column expected after AMD nightly (~${nextUp.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})} local, in ${timeStr})`,style:{fontSize:'12px',color:C.m,marginBottom:'8px'}}));
    }
    // Legend
    const legend = h('div',{style:{display:'flex',gap:'16px',marginBottom:'4px',fontSize:'14px',color:C.m,flexWrap:'wrap'}});
    for (const [label,color] of [['Passed',C.g],['Failed',C.r],['Not Run',C.bd]]) {
      legend.append(h('span',{style:{display:'flex',alignItems:'center',gap:'5px'}},[
        h('span',{style:{width:'12px',height:'12px',borderRadius:'2px',background:color,display:'inline-block'}}),label
      ]));
    }
    // Pipeline legend — compact colored rectangles matching heatmap dots
    for (const [label,color] of [['AMD (left)','#da3633'],['Upstream (right)','#1f6feb']]) {
      legend.append(h('span',{style:{display:'flex',alignItems:'center',gap:'5px'}},[
        h('span',{style:{width:'14px',height:'7px',borderRadius:'2px',background:color,display:'inline-block'}}),label
      ]));
    }
    box.append(legend);

    // Date header (shared) — offset by name column + links column
    const dateHeader = h('div',{style:{display:'flex',marginLeft:'calc(clamp(280px, 28vw, 500px) + 40px)',marginBottom:'4px'}});
    for (const d of useDates) {
      dateHeader.append(h('div',{text:d.slice(5),style:{width:'50px',textAlign:'center',fontSize:'15px',color:C.m,flexShrink:0}}));
    }
    box.append(dateHeader);

    // Render each area as a collapsible section
    const areaNames = Object.keys(byArea).sort();
    for (const area of areaNames) {
      const groups = byArea[area];
      // Count failures in latest build for this area
      const latestAmd = buildJobMap(amdByDate[useDates[useDates.length - 1]]);
      const areaFails = groups.filter(g => latestAmd[g] === 'failed').length;

      const det = h('details',{open: areaFails > 0, style:{marginBottom:'4px',background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'6px'}});
      const summ = h('summary',{style:{padding:'10px 14px',cursor:'pointer',display:'flex',justifyContent:'space-between',alignItems:'center',fontSize:'14px',fontWeight:'600'}});
      summ.append(h('span',{},[
        h('span',{style:{width:'8px',height:'8px',borderRadius:'50%',background:areaFails>0?C.r:C.g,display:'inline-block',marginRight:'8px'}}),
        `${area} `,
        h('span',{text:`(${groups.length} groups${areaFails?`, ${areaFails} failing`:''})`,style:{color:C.m,fontWeight:'400',fontSize:'13px'}})
      ]));
      det.append(summ);

      // Group rows inside
      const inner = h('div',{style:{padding:'4px 14px 10px'}});
      for (const gn of groups) {
        const row = h('div',{style:{display:'flex',alignItems:'center',marginBottom:'4px',minHeight:'20px'},title:gn});
        const nameDiv=h('div',{style:{width:'clamp(280px, 28vw, 500px)',fontSize:'clamp(12px, 0.85vw, 16px)',flexShrink:0,wordBreak:'break-word',lineHeight:'1.4'}});
        nameDiv.textContent=gn;
        row.append(nameDiv);
        const linksDiv=h('div',{style:{width:'40px',flexShrink:0,display:'flex',alignItems:'center',justifyContent:'center'}});
        if(typeof makeGroupLinksColumn==='function'){linksDiv.append(makeGroupLinksColumn(gn,true,true))}
        row.append(linksDiv);

        for (const d of useDates) {
          const amdMap = buildJobMap(amdByDate[d]);
          const upMap = buildJobMap(upByDate[d]);
          const cell = h('div',{style:{width:'50px',display:'flex',justifyContent:'center',gap:'2px',flexShrink:0},title:`${d}\nAMD: ${amdMap[gn]||'-'}\nUpstream: ${upMap[gn]||'-'}`});
          cell.append(h('span',{style:{width:'14px',height:'7px',borderRadius:'2px',background:stateColor(amdMap[gn]),display:'block'}}));
          cell.append(h('span',{style:{width:'14px',height:'7px',borderRadius:'2px',background:stateColor(upMap[gn]),display:'block'}}));
          row.append(cell);
        }
        inner.append(row);
      }
      det.append(inner);
      box.append(det);
    }
  }

  // ═══════════ TEST GROUP TRENDS ═══════════

  function renderGroupTrends(box, data, pipelines, matrixData) {
    const pipeRow = h('div',{style:{display:'flex',gap:'8px',alignItems:'center',marginBottom:'16px'}});
    pipeRow.append(h('span',{text:'Pipeline:',style:{color:C.m,fontSize:'13px'}}));
    let activePipe = pipelines[0] || 'amd-ci';
    const trendsBox = h('div');

    for (const p of pipelines) {
      const btn = h('button',{text:data[p]?.display_name || p,style:{
        background:p===activePipe?C.b:'transparent',border:'none',color:C.t,
        padding:'6px 14px',borderRadius:'4px',cursor:'pointer',fontSize:'13px',fontFamily:'inherit',
        fontWeight:p===activePipe?'700':'400'
      }});
      btn.onclick = () => {
        activePipe = p;
        pipeRow.querySelectorAll('button').forEach(b => { b.style.background='transparent'; b.style.fontWeight='400'; });
        btn.style.background = C.b; btn.style.fontWeight = '700';
        trendsBox.innerHTML = '';
        renderGroupTrendsChart(trendsBox, data[p], p === 'ci' ? 'upstream' : 'amd', matrixData);
      };
      pipeRow.append(btn);
    }
    box.append(pipeRow, trendsBox);
    renderGroupTrendsChart(trendsBox, data[activePipe], activePipe === 'ci' ? 'upstream' : 'amd', matrixData);
  }

  // Cache for group_changes.json
  let _groupChangesData = null;
  let _groupChangesLoaded = false;
  async function _loadGroupChanges() {
    if (_groupChangesLoaded) return _groupChangesData;
    _groupChangesLoaded = true;
    try {
      _groupChangesData = await J('data/vllm/ci/group_changes.json');
    } catch(e) {}
    return _groupChangesData;
  }

  function _showPROverlay(date, changes, color, pipeKey) {
    // pipeKey: 'amd' or 'upstream' — used to show per-pipeline changes
    if (!pipeKey) pipeKey = 'amd';
    const backdrop=h('div',{style:{position:'fixed',inset:'0',background:'rgba(0,0,0,.6)',zIndex:'1000',display:'flex',justifyContent:'center',alignItems:'flex-start',paddingTop:'60px',overflow:'auto'}});
    backdrop.onclick=e=>{if(e.target===backdrop)backdrop.remove()};
    const panel=h('div',{style:{background:'var(--bg,#0d1117)',border:'1px solid var(--border,#30363d)',borderRadius:'12px',width:'min(700px,90vw)',maxHeight:'80vh',overflow:'auto',padding:'24px'}});
    panel.append(h('div',{style:{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:'16px'}},[
      h('h3',{html:`Test Group Changes on <span style="color:${C.b}">${date}</span>`,style:{margin:'0'}}),
      h('button',{text:'\u2715',onclick:()=>backdrop.remove(),style:{background:'none',border:'none',color:C.m,fontSize:'20px',cursor:'pointer'}})
    ]));
    if(!changes.length){
      panel.append(h('p',{text:'No YAML changes found for this date.',style:{color:C.m}}));
    }
    for(const ch of changes){
      const sec=h('div',{style:{marginBottom:'16px',padding:'12px',background:'var(--card-bg,#161b22)',borderRadius:'8px',border:'1px solid var(--border,#30363d)'}});
      // PR link or commit info
      if(ch.pr){
        sec.append(h('div',{style:{marginBottom:'8px'}},[
          h('a',{href:ch.pr.url,target:'_blank',style:{color:C.b,fontWeight:'700',fontSize:'15px',textDecoration:'none'},text:`PR #${ch.pr.number}: ${ch.pr.title}`}),
          h('span',{text:` by ${ch.pr.author}`,style:{color:C.m,fontSize:'13px',marginLeft:'8px'}})
        ]));
      } else {
        sec.append(h('div',{text:`${ch.sha} — ${ch.message}`,style:{fontSize:'13px',color:C.t,marginBottom:'8px'}}));
        sec.append(h('div',{text:`by ${ch.author}`,style:{fontSize:'12px',color:C.m,marginBottom:'8px'}}));
      }
      // Show per-pipeline changes when available, otherwise combined
      const addedList = ch[pipeKey+'_added'] || ch.added || [];
      const removedList = ch[pipeKey+'_removed'] || ch.removed || [];
      if(addedList.length){
        sec.append(h('div',{style:{marginBottom:'6px'}},[
          h('span',{text:`+${addedList.length} added: `,style:{color:C.g,fontWeight:'600',fontSize:'13px'}}),
          ...addedList.map(g=>h('span',{text:g,style:{padding:'2px 8px',borderRadius:'3px',fontSize:'12px',background:C.g+'15',border:`1px solid ${C.g}33`,color:C.t,marginRight:'4px',display:'inline-block',marginBottom:'2px'}}))
        ]));
      }
      if(removedList.length){
        sec.append(h('div',{},[
          h('span',{text:`-${removedList.length} removed: `,style:{color:C.r,fontWeight:'600',fontSize:'13px'}}),
          ...removedList.map(g=>h('span',{text:g,style:{padding:'2px 8px',borderRadius:'3px',fontSize:'12px',background:C.r+'15',border:`1px solid ${C.r}33`,color:C.t,marginRight:'4px',display:'inline-block',marginBottom:'2px'}}))
        ]));
      }
      panel.append(sec);
    }
    backdrop.append(panel);
    document.body.append(backdrop);
    // Escape key closes
    var escHandler = function(e) { if (e.key === 'Escape') { backdrop.remove(); document.removeEventListener('keydown', escHandler); } };
    document.addEventListener('keydown', escHandler);
  }

  async function renderGroupTrendsChart(box, pipeData, pipeKey, matrixData) {
    if (!pipeData?.builds?.length) { box.append(h('p',{text:'No builds.',style:{color:C.m}})); return; }

    const builds = pipeData.builds.filter(b => (b.jobs||[]).length > 10).slice(0, 30).reverse();
    if (builds.length < 2) { box.append(h('p',{text:'Need at least 2 nightly builds for trends.',style:{color:C.m}})); return; }

    const buildGroups = builds.map(b => {
      const groups = new Set();
      (b.jobs||[]).forEach(j => groups.add(normalizeJobName(j.name)));
      return { date: b.date || b.created_at?.slice(0,10) || '?', groups, build: b };
    });

    const labels = [], totalCounts = [], newCounts = [], removedCounts = [];
    const allNew = new Set(), allRemoved = new Set();

    for (let i = 0; i < buildGroups.length; i++) {
      const curr = buildGroups[i];
      labels.push(curr.date.slice(5));
      totalCounts.push(curr.groups.size);
      if (i === 0) { newCounts.push(0); removedCounts.push(0); continue; }
      const prev = buildGroups[i-1];
      const added = [...curr.groups].filter(g => !prev.groups.has(g));
      const removed = [...prev.groups].filter(g => !curr.groups.has(g));
      newCounts.push(added.length);
      removedCounts.push(removed.length);
      added.forEach(g => { if (!buildGroups[0].groups.has(g)) allNew.add(g); });
      removed.forEach(g => { if (!buildGroups[buildGroups.length-1].groups.has(g)) allRemoved.add(g); });
    }

    const latest = buildGroups[buildGroups.length - 1];
    const earliest = buildGroups[0];

    function showListOverlay(title, items, color) {
      const backdrop = document.createElement('div');
      backdrop.className = 'overlay-backdrop';
      backdrop.onclick = e => { if(e.target===backdrop) backdrop.remove(); };
      const panel = document.createElement('div');
      panel.className = 'overlay-panel';
      const hdr = document.createElement('div');
      hdr.className = 'overlay-header';
      hdr.innerHTML = `<h3 style="color:${color}">${title} <span style="color:var(--text-muted);font-weight:400">(${items.length})</span></h3>`;
      const cls = document.createElement('button');
      cls.className = 'overlay-close';
      cls.innerHTML = '&times;';
      cls.onclick = () => backdrop.remove();
      hdr.appendChild(cls);
      const body = document.createElement('div');
      body.className = 'overlay-body';
      const list = items.sort();
      let html = '';
      for (const g of list) {
        html += `<div style="padding:5px 0;border-bottom:1px solid var(--border)">${LinkRegistry.aTag(LinkRegistry.bk.groupUrl(g, 'amd'), g, {style:'color:var(--text);text-decoration:none;transition:color .15s'})}</div>`;
      }
      body.innerHTML = html;
      panel.append(hdr, body);
      backdrop.append(panel);
      document.body.append(backdrop);
      document.addEventListener('keydown', function esc(e){if(e.key==='Escape'){backdrop.remove();document.removeEventListener('keydown',esc)}});
    }

    const matrixSummary = pipeKey === 'amd' && matrixData?.rows?.length ? summarizeAmdMatrix(matrixData) : null;
    const matrixGroups = matrixSummary
      ? (matrixData.rows || []).map(row => row.title).filter(Boolean)
      : null;

    const summRow = h('div',{style:{display:'grid',gridTemplateColumns:'repeat(4,1fr)',gap:'12px',marginBottom:'20px'}});
    const mc = (label,val,sub,color,items) => {
      const c = metricCard(label,val,sub,color);
      if (items && items.length) {
        c.style.cursor='pointer';
        c.onmouseenter=()=>{c.style.transform='translateY(-2px)';c.style.boxShadow='0 4px 12px rgba(0,0,0,.3)'};
        c.onmouseleave=()=>{c.style.transform='';c.style.boxShadow=''};
        c.onclick=()=>showListOverlay(label, items, color);
      }
      return c;
    };
    summRow.append(mc(
      matrixSummary ? 'Current YAML Groups' : 'Current Groups',
      matrixSummary ? matrixSummary.families : latest.groups.size,
      matrixSummary
        ? `${matrixSummary.hardwareCells} hardware jobs in AMD HW Matrix`
        : latest.date,
      C.b,
      matrixGroups || [...latest.groups]
    ));
    summRow.append(mc('Period Start', earliest.groups.size, earliest.date, C.m, [...earliest.groups]));
    summRow.append(mc('New Groups', allNew.size, `since ${earliest.date.slice(5)}`, C.g, [...allNew]));
    summRow.append(mc('Removed Groups', allRemoved.size, `since ${earliest.date.slice(5)}`, allRemoved.size > 0 ? C.r : C.m, [...allRemoved]));
    box.append(summRow);

    // Load group changes data for click-to-PR feature
    const gcData = await _loadGroupChanges();
    const changesByDate = {};
    if (gcData?.changes) {
      for (const ch of gcData.changes) {
        (changesByDate[ch.date] = changesByDate[ch.date] || []).push(ch);
      }
    }
    // Helper: find PR changes for a build date.
    // PRs committed on day N appear in the nightly build for day N (or N+1).
    // So for build date D, check D, D-1, and D+1.
    function changesForDate(d) {
      const result = [...(changesByDate[d] || [])];
      // Check adjacent days for PRs that were committed before/after the nightly
      const dt = new Date(d + 'T12:00:00Z');
      for (const offset of [-1, 1]) {
        const adj = new Date(dt);
        adj.setUTCDate(adj.getUTCDate() + offset);
        const adjStr = adj.toISOString().slice(0, 10);
        if (changesByDate[adjStr]) {
          for (const ch of changesByDate[adjStr]) {
            // Don't duplicate — check by SHA
            if (!result.some(r => r.sha === ch.sha)) result.push(ch);
          }
        }
      }
      return result;
    }
    // Map short labels back to full dates for lookup
    const fullDates = buildGroups.map(bg => bg.date);

    const chartSec = h('div',{style:{background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'8px',padding:'20px',marginBottom:'20px'}});
    chartSec.append(h('h3',{text:'Test Group Count Over Time',style:{marginBottom:'4px',fontSize:'15px'}}));
    chartSec.append(h('p',{text:'Click bars with changes to see the PRs responsible',style:{fontSize:'12px',color:C.m,marginBottom:'12px'}}));
    const canvas = h('canvas',{style:{maxHeight:'250px',cursor:'pointer'}});
    chartSec.append(canvas);
    box.append(chartSec);

    const chart = new Chart(canvas, {
      type:'bar', data:{ labels, datasets:[
        {type:'line',label:'Total Groups',data:totalCounts,borderColor:C.b,backgroundColor:C.b+'20',tension:.3,fill:true,pointRadius:4,borderWidth:2,yAxisID:'y'},
        {label:'New',data:newCounts,backgroundColor:C.g,borderRadius:2,yAxisID:'y1'},
        {label:'Removed',data:removedCounts.map(v=>-v),backgroundColor:C.r,borderRadius:2,yAxisID:'y1'},
      ]}, options:{ responsive:true,
        onClick:(evt, elements, chartInstance) => {
          // Get index from clicked elements, or from nearest X position
          let idx;
          if (elements && elements.length) {
            idx = elements[0].index;
          } else {
            // No element directly clicked — find nearest index from X position
            const xScale = chartInstance.scales.x;
            if (!xScale) return;
            const canvasPos = Chart.helpers.getRelativePosition(evt, chartInstance);
            idx = xScale.getValueForPixel(canvasPos.x);
            if (idx == null || idx < 0 || idx >= fullDates.length) return;
            idx = Math.round(idx);
          }
          const date = fullDates[idx];
          if (!date) return;
          if (idx === 0 && newCounts[idx] === 0 && removedCounts[idx] === 0) return;

          // Build-level group diff (what actually changed between nightly builds)
          const added = idx > 0 ? [...buildGroups[idx].groups].filter(g => !buildGroups[idx-1].groups.has(g)) : [];
          const removed = idx > 0 ? [...buildGroups[idx-1].groups].filter(g => !buildGroups[idx].groups.has(g)) : [];

          // YAML-level PR changes for this date (checks adjacent days too)
          // Filter to PRs that have changes for the active pipeline
          const dateChanges = changesForDate(date).filter(c => {
            const a = c[pipeKey+'_added'] || c.added || [];
            const r = c[pipeKey+'_removed'] || c.removed || [];
            return a.length > 0 || r.length > 0;
          });

          // Combine: PR changes first, then build-level diff showing ALL groups
          // Use per-pipeline fields (amd_added/amd_removed) when available,
          // falling back to combined (added/removed) for old cached entries
          // pipeKey is passed as a parameter to renderGroupTrendsChart
          const allChanges = [...dateChanges];
          // Normalize group_changes names the same way build names are normalized
          // Strip trailing shard index aggressively for matching (covers historical bases not in shard_bases.json)
          const stripTrailingShard = g => g.replace(/\s+\d+\s*$/, '');
          const prAdded = new Set(dateChanges.flatMap(c => (c[pipeKey+'_added'] || c.added || []).flatMap(g => {
            const n = normalizeJobName(g); return [n, stripTrailingShard(n)];
          })));
          const prRemoved = new Set(dateChanges.flatMap(c => (c[pipeKey+'_removed'] || c.removed || []).flatMap(g => {
            const n = normalizeJobName(g); return [n, stripTrailingShard(n)];
          })));
          const isAttributed = (g, prSet) => prSet.has(normalizeJobName(g)) || prSet.has(stripTrailingShard(normalizeJobName(g)));
          const unattributed_added = added.filter(g => !isAttributed(g, prAdded));
          const unattributed_removed = removed.filter(g => !isAttributed(g, prRemoved));
          if (unattributed_added.length || unattributed_removed.length) {
            allChanges.push({
              sha: '', author: '',
              message: 'Build-level changes (groups present in one nightly but not the other — infra, retries, or hardware availability)',
              added: unattributed_added,
              removed: unattributed_removed,
              pr: null,
            });
          }
          if (allChanges.length) _showPROverlay(date, allChanges, C.b, pipeKey);
        },
        plugins:{legend:{labels:{color:C.t,font:{size:12}}}},
        scales:{
          y:{position:'left',ticks:{color:C.m},grid:{color:C.bd},title:{display:true,text:'Total Groups',color:C.m}},
          y1:{position:'right',ticks:{color:C.m},grid:{display:false},title:{display:true,text:'Added / Removed',color:C.m}},
          x:{ticks:{color:C.m},grid:{color:C.bd}}
        }
      }
    });

    if (allNew.size > 0) {
      const sec = h('div',{style:{background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'8px',padding:'16px',marginBottom:'12px'}});
      sec.append(h('h4',{text:`New Groups (${allNew.size})`,style:{color:C.g,marginBottom:'8px',fontSize:'14px'}}));
      const chips = h('div',{style:{display:'flex',flexWrap:'wrap',gap:'6px'}});
      [...allNew].sort().forEach(g => chips.append(h('span',{text:g,style:{padding:'4px 10px',borderRadius:'4px',fontSize:'13px',background:C.g+'15',border:`1px solid ${C.g}33`,color:C.t}})));
      sec.append(chips);
      box.append(sec);
    }
    if (allRemoved.size > 0) {
      const sec = h('div',{style:{background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'8px',padding:'16px',marginBottom:'12px'}});
      sec.append(h('h4',{text:`Removed Groups (${allRemoved.size})`,style:{color:C.r,marginBottom:'8px',fontSize:'14px'}}));
      const chips = h('div',{style:{display:'flex',flexWrap:'wrap',gap:'6px'}});
      [...allRemoved].sort().forEach(g => chips.append(h('span',{text:g,style:{padding:'4px 10px',borderRadius:'4px',fontSize:'13px',background:C.r+'15',border:`1px solid ${C.r}33`,color:C.t}})));
      sec.append(chips);
      box.append(sec);
    }
  }

  function matrixStateColor(state, matched) {
    if (!matched) return C.b;
    if (state === 'passed') return C.g;
    if (state === 'failed' || state === 'timed_out' || state === 'broken' || state === 'soft_fail') return C.r;
    if (state === 'running') return C.b;
    if (state === 'scheduled' || state === 'assigned') return C.y;
    return C.m;
  }

  function matrixStateLabel(state, matched) {
    if (!matched) return 'in YAML, not matched in latest nightly';
    if (!state) return 'present';
    if (state === 'soft_fail') return 'soft fail';
    return state;
  }

  function matrixVariantTooltip(variant, source) {
    const parts = [variant.label];
    if ((variant.raw_variant_count || 1) > 1) parts.push(`${variant.raw_variant_count} YAML aliases`);
    if (variant.agent_pool) parts.push(`Pool: ${variant.agent_pool}`);
    parts.push(`Latest nightly: ${matrixStateLabel(variant.latest_state, !!variant.latest_matched)}`);
    if (source?.latest_build_number) parts.push(`Build #${source.latest_build_number}`);
    return parts.join('\n');
  }

  function matrixVariantEntries(variant) {
    if (Array.isArray(variant?.entries) && variant.entries.length) return variant.entries;
    return variant ? [variant] : [];
  }

  function matrixVariantTargetUrl(variant, source) {
    if (variant?.latest_url) return variant.latest_url;
    if (variant?.latest_matched) return source?.latest_build_url || LinkRegistry.bk.buildUrl('amd');
    return null;
  }

  function showMatrixVariantsOverlay(row, arch, variants, source) {
    const overlay = typeof createOverlay === 'function'
      ? createOverlay({ title: `${row.title} — ${arch.toUpperCase()}`, color: C.b, maxWidth: '760px' })
      : null;
    if (!overlay) return;

    const summary = h('div',{style:{
      padding:'12px 14px',border:`1px solid ${C.bd}`,borderRadius:'8px',
      background:C.bg,marginBottom:'14px',fontSize:'13px',color:C.m
    }});
    const rawVariantCount = variants.reduce((sum, variant) => sum + (variant.raw_variant_count || 1), 0);
    const rawVariantLabel = rawVariantCount === 1 ? 'YAML entry' : 'YAML entries';
    summary.append(h('div',{html:`<strong>${row.title}</strong> maps to <strong>${rawVariantCount}</strong> ${rawVariantLabel} on ${arch.toUpperCase()}.`}));
    if (source?.latest_build_number) {
      const meta = h('div',{style:{marginTop:'6px'}});
      meta.append(h('span',{text:'Latest AMD nightly: ',style:{color:C.m}}));
      meta.append(h('a',{
        text:`#${source.latest_build_number}${source.latest_build_date ? ` (${source.latest_build_date})` : ''}`,
        href:source.latest_build_url || LinkRegistry.bk.buildUrl('amd'),
        target:'_blank',
        rel:'noopener',
        style:{color:C.b,textDecoration:'none',fontWeight:'600'}
      }));
      summary.append(meta);
    }
    overlay.body.append(summary);

    const tableWrap = h('div',{style:{
      border:`1px solid ${C.bd}`,borderRadius:'8px',overflow:'hidden',background:C.bg
    }});
    const table = h('table',{style:{width:'100%',borderCollapse:'collapse'}});
    const thead = h('thead');
    const hr = h('tr');
    hr.append(h('th',{text:'Variant',style:thS()}));
    hr.append(h('th',{text:'Pool',style:thS()}));
    hr.append(h('th',{text:'Nightly',style:thS('center')}));
    hr.append(h('th',{text:'Link',style:thS('center')}));
    thead.append(hr);
    table.append(thead);
    const tbody = h('tbody');
    variants.forEach(v => {
      const tr = h('tr');
      const variantCell = h('td',{style:tdS()});
      variantCell.append(h('div',{text:v.label}));
      const aliases = (v.aliases || []).filter(label => label !== v.label);
      if (aliases.length) {
        variantCell.append(h('div',{text:`Aliases: ${aliases.join(' · ')}`,style:{
          marginTop:'4px',fontSize:'12px',color:C.m,lineHeight:'1.4'
        }}));
      }
      tr.append(variantCell);
      tr.append(h('td',{text:v.agent_pool || '-',style:tdS()}));
      const stateCell = h('td',{style:{...tdS('center'),whiteSpace:'nowrap'}});
      stateCell.append(h('span',{style:{
        display:'inline-block',width:'10px',height:'10px',borderRadius:'50%',
        background:matrixStateColor(v.latest_state, !!v.latest_matched),marginRight:'6px'
      }}));
      stateCell.append(h('span',{text:matrixStateLabel(v.latest_state, !!v.latest_matched)}));
      tr.append(stateCell);
      tr.append(h('td',{style:tdS('center')},[
        h('a',{
          text:'Open',
          href:matrixVariantTargetUrl(v, source) || LinkRegistry.bk.groupUrl(v.label, 'amd'),
          target:'_blank',
          rel:'noopener',
          style:{color:C.b,textDecoration:'none',fontWeight:'600'}
        })
      ]));
      tbody.append(tr);
    });
    table.append(tbody);
    tableWrap.append(table);
    overlay.body.append(tableWrap);
  }

  function renderAmdHardwareMatrix(box, matrixData) {
    if (!matrixData?.rows?.length) {
      box.append(h('div',{style:{textAlign:'center',padding:'40px 20px',color:C.m}},[
        h('h3',{text:'AMD HW Matrix',style:{marginBottom:'8px'}}),
        h('p',{text:'No AMD test matrix data available yet.'}),
      ]));
      return;
    }

    const architectures = matrixData.architectures || [];
    const archIds = architectures.map(a => a.id);
    const archCount = archIds.length || 1;
    const matrixSummary = summarizeAmdMatrix(matrixData);

    const header = h('div',{style:{marginBottom:'16px'}});
    header.append(h('h3',{text:'AMD Hardware Matrix',style:{marginBottom:'4px',fontSize:'18px'}}));
    const sub = h('p',{style:{color:C.m,fontSize:'13px',margin:'0'}});
    sub.append(h('span',{text:'YAML definitions from upstream '}));
    sub.append(h('a',{
      text:'test-amd.yaml',
      href:matrixData.source?.yaml_url || (LinkRegistry.github.repo('vllm-project/vllm') + '/blob/main/.buildkite/test-amd.yaml'),
      target:'_blank',
      rel:'noopener',
      style:{color:C.b,textDecoration:'none',fontWeight:'600'}
    }));
    if (matrixData.source?.latest_build_number) {
      sub.append(h('span',{text:' matched against AMD nightly '}));
      sub.append(h('a',{
        text:`#${matrixData.source.latest_build_number}`,
        href:matrixData.source.latest_build_url || LinkRegistry.bk.buildUrl('amd'),
        target:'_blank',
        rel:'noopener',
        style:{color:C.b,textDecoration:'none',fontWeight:'600'}
      }));
      if (matrixData.source?.latest_build_date) {
        sub.append(h('span',{text:` (${matrixData.source.latest_build_date})`}));
      }
    }
    header.append(sub);
    box.append(header);

    const summaryRow = h('div',{style:{display:'grid',gridTemplateColumns:'repeat(4,1fr)',gap:'12px',marginBottom:'16px'}});
    summaryRow.append(metricCard(
      'Test Families',
      matrixSummary.families,
      `${matrixSummary.architectures || archCount} architectures · ${matrixSummary.hardwareCells} hardware jobs`,
      C.b
    ));
    summaryRow.append(metricCard(
      'Nightly Matched',
      `${matrixSummary.matchedCells}/${matrixSummary.hardwareCells}`,
      `${fmtPct(matrixSummary.matchRate)} of YAML hardware jobs found in latest nightly`,
      matrixSummary.unmatchedCells ? C.o : C.g
    ));
    summaryRow.append(metricCard(
      'Passing HW Jobs',
      matrixSummary.passingCells,
      `${fmtPct(matrixSummary.passRate)} pass rate across matched hardware jobs`,
      matrixSummary.passRate == null ? C.m : matrixSummary.passRate >= 0.85 ? C.g : matrixSummary.passRate >= 0.7 ? C.o : C.r
    ));
    summaryRow.append(metricCard(
      'Needs Attention',
      matrixSummary.attentionFamilies,
      matrixSummary.waitingCells
        ? `${matrixSummary.failingCells} failing hardware jobs · ${matrixSummary.waitingCells} running or waiting`
        : `${matrixSummary.failingCells} failing hardware jobs in latest nightly`,
      matrixSummary.attentionFamilies ? C.r : C.g
    ));
    box.append(summaryRow);

    const note = h('div',{style:{
      padding:'10px 14px',background:C.b+'12',border:`1px solid ${C.b}33`,
      borderRadius:'8px',marginBottom:'16px',fontSize:'13px',color:C.t
    }});
    note.append(h('span',{text:'Rows are unique test families from test-amd.yaml. Cells are the hardware-specific jobs those families create. Needs Attention counts affected rows and shows the raw failing hardware-job count underneath.'}));
    box.append(note);

    const legend = h('div',{style:{
      display:'flex',flexWrap:'wrap',gap:'14px',alignItems:'center',
      padding:'10px 14px',background:C.bg,border:`1px solid ${C.bd}`,
      borderRadius:'8px',marginBottom:'16px',fontSize:'13px',color:C.t
    }});
    legend.append(h('strong',{text:'Legend',style:{marginRight:'4px'}}));

    function legendItem(color, label, outline) {
      const item = h('div',{style:{display:'inline-flex',alignItems:'center',gap:'8px'}});
      item.append(h('span',{style:{
        width:'11px',height:'11px',borderRadius:'50%',display:'inline-block',
        background:color,
        boxShadow:outline ? `inset 0 0 0 1px ${outline}` : '0 0 0 2px rgba(255,255,255,0.06)'
      }}));
      item.append(h('span',{text:label}));
      return item;
    }

    legend.append(legendItem(C.g, 'Passed in latest nightly'));
    legend.append(legendItem(C.r, 'Failed / timed out / soft-failed'));
    legend.append(legendItem(C.y, 'Scheduled or assigned'));
    legend.append(legendItem(C.b, 'Running, or present in YAML but not matched'));
    legend.append(legendItem(C.m, 'Unknown state', C.bd));
    box.append(legend);

    let search = '';
    let activeArea = 'All';
    let sortMode = 'coverage-desc';
    let attentionOnly = false;

    const controls = h('div',{style:{
      display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(180px,1fr))',gap:'12px',
      marginBottom:'16px',alignItems:'end'
    }});

    function buildSelect(labelText, options, value, onChange) {
      const wrap = h('label',{style:{display:'block'}});
      wrap.append(h('div',{text:labelText,style:{fontSize:'12px',color:C.m,marginBottom:'6px'}}));
      const sel = h('select',{style:{
        width:'100%',background:C.bg,border:`1px solid ${C.bd}`,color:C.t,
        borderRadius:'8px',padding:'10px 12px',fontFamily:'inherit'
      }});
      options.forEach(opt => {
        sel.append(h('option',{value:opt.value,text:opt.label,selected:opt.value===value?true:null}));
      });
      sel.onchange = onChange;
      wrap.append(sel);
      return wrap;
    }

    const searchWrap = h('label',{style:{display:'block'}});
    searchWrap.append(h('div',{text:'Search',style:{fontSize:'12px',color:C.m,marginBottom:'6px'}}));
    const searchInput = h('input',{
      type:'search',
      value:'',
      placeholder:'Filter by title, architecture, or variant label',
      style:{
        width:'100%',background:C.bg,border:`1px solid ${C.bd}`,color:C.t,
        borderRadius:'8px',padding:'10px 12px',fontFamily:'inherit'
      }
    });
    searchInput.oninput = () => { search = (searchInput.value || '').trim().toLowerCase(); redrawTable(); };
    searchWrap.append(searchInput);
    controls.append(searchWrap);

    controls.append(buildSelect('Area', [{value:'All',label:'All Areas'}].concat((matrixData.areas || []).map(a => ({value:a,label:a}))), 'All', e => {
      activeArea = e.target.value;
      redrawTable();
    }));

    controls.append(buildSelect('Sort Preference', [
      { value:'coverage-desc', label:'Most HW Presence' },
      { value:'coverage-asc', label:'Least HW Presence' },
      { value:'nightly-desc', label:'Most Nightly Matches' },
      { value:'area', label:'Area then Title' },
      { value:'title', label:'Title A-Z' },
      { value:'yaml', label:'YAML Order' },
    ], 'coverage-desc', e => {
      sortMode = e.target.value;
      redrawTable();
    }));

    const toggleWrap = h('label',{style:{
      display:'flex',alignItems:'center',gap:'8px',paddingBottom:'10px',
      fontSize:'13px',color:C.t,cursor:'pointer'
    }});
    const toggle = h('input',{type:'checkbox'});
    toggle.onchange = () => { attentionOnly = !!toggle.checked; redrawTable(); };
    toggleWrap.append(toggle, h('span',{text:'Needs attention only'}));
    controls.append(toggleWrap);
    box.append(controls);

    const tableWrap = h('div',{style:{
      border:`1px solid ${C.bd}`,borderRadius:'10px',overflow:'auto',background:C.bg
    }});
    box.append(tableWrap);

    function compareRows(a, b) {
      if (sortMode === 'coverage-asc') {
        return (a.coverage_count - b.coverage_count) || a.title.localeCompare(b.title);
      }
      if (sortMode === 'nightly-desc') {
        return (b.nightly_coverage_count - a.nightly_coverage_count)
          || (b.coverage_count - a.coverage_count)
          || a.title.localeCompare(b.title);
      }
      if (sortMode === 'area') {
        return a.area.localeCompare(b.area) || a.title.localeCompare(b.title);
      }
      if (sortMode === 'title') {
        return a.title.localeCompare(b.title);
      }
      if (sortMode === 'yaml') {
        return (a.yaml_order - b.yaml_order) || a.title.localeCompare(b.title);
      }
      return (b.coverage_count - a.coverage_count)
        || (b.nightly_coverage_count - a.nightly_coverage_count)
        || a.title.localeCompare(b.title);
    }

    function cellNeedsAttention(cell) {
      if (!cell?.exists) return false;
      if (!cell.latest_matched) return true;
      return ['failed', 'timed_out', 'broken', 'soft_fail', 'running', 'scheduled', 'assigned'].includes(cell.latest_state || '');
    }

    function rowNeedsAttention(row) {
      for (const arch of archIds) {
        if (cellNeedsAttention(row.cells?.[arch])) return true;
      }
      return false;
    }

    function filteredRows() {
      return (matrixData.rows || []).filter(row => {
        if (activeArea !== 'All' && row.area !== activeArea) return false;
        if (attentionOnly && !rowNeedsAttention(row)) return false;
        if (!search) return true;
        const hay = [
          row.title,
          row.area,
          row.signature || '',
        ].join(' ').toLowerCase();
        if (hay.includes(search)) return true;
        for (const arch of archIds) {
          const cell = row.cells?.[arch];
          if (!cell?.exists) continue;
          for (const variant of (cell.variants || [])) {
            if ((variant.label || '').toLowerCase().includes(search)) return true;
          }
        }
        return false;
      }).sort(compareRows);
    }

    function renderVariantLink(row, arch, variant, source) {
      const entries = matrixVariantEntries(variant);
      const matched = !!variant.latest_matched;
      const color = matrixStateColor(variant.latest_state, matched);
      const dot = h('span',{style:{
        width:'11px',height:'11px',borderRadius:'50%',display:'inline-block',
        background:color,
        boxShadow: matched ? '0 0 0 2px rgba(255,255,255,0.06)' : `inset 0 0 0 1px ${C.bd}`
      }});
      const tooltip = matrixVariantTooltip(variant, source);

      if (entries.length > 1) {
        const btn = h('button',{
          type:'button',
          title:tooltip,
          style:{
            display:'inline-flex',alignItems:'center',justifyContent:'center',gap:'5px',
            background:'transparent',border:'none',padding:'0',cursor:'pointer',color:C.t
          }
        });
        btn.onclick = () => showMatrixVariantsOverlay(row, arch, entries, source);
        btn.append(dot);
        btn.append(h('span',{text:String(entries.length),style:{fontSize:'10px',color:C.m}}));
        return btn;
      }

      const targetUrl = matrixVariantTargetUrl(entries[0], source);
      if (!targetUrl) {
        return h('span',{title:tooltip,style:{display:'inline-flex',alignItems:'center',justifyContent:'center'}},[dot]);
      }

      const link = h('a',{
        href:targetUrl,
        target:'_blank',
        rel:'noopener',
        title:tooltip,
        style:{display:'inline-flex',alignItems:'center',justifyContent:'center',textDecoration:'none'}
      });
      link.append(dot);
      return link;
    }

    function redrawTable() {
      tableWrap.innerHTML = '';
      const rows = filteredRows();
      if (!rows.length) {
        tableWrap.append(h('div',{style:{padding:'28px 20px',textAlign:'center',color:C.m}},[
          h('strong',{text:'No rows match this filter.'})
        ]));
        return;
      }

      const table = h('table',{style:{width:'100%',borderCollapse:'collapse',minWidth:`${420 + archIds.length * 80}px`}});
      const thead = h('thead');
      const hr = h('tr');
      hr.append(h('th',{text:'Test Group',style:{...thS(),position:'sticky',top:'0',background:C.bg,zIndex:'2',minWidth:'280px'}}));
      hr.append(h('th',{text:'Area',style:{...thS(),position:'sticky',top:'0',background:C.bg,zIndex:'2'}}));
      hr.append(h('th',{text:'HW Presence',style:{...thS('center'),position:'sticky',top:'0',background:C.bg,zIndex:'2'}}));
      architectures.forEach(arch => {
        hr.append(h('th',{text:arch.label,style:{...thS('center'),position:'sticky',top:'0',background:C.bg,zIndex:'2'}}));
      });
      thead.append(hr);
      table.append(thead);

      const tbody = h('tbody');
      rows.forEach(row => {
        const tr = h('tr',{style:{
          background:rowNeedsAttention(row) ? 'rgba(218,54,51,0.05)' : 'transparent'
        }});

        const titleCell = h('td',{style:tdS()});
        titleCell.append(h('div',{text:row.title,style:{fontWeight:'700',marginBottom:'2px'}}));
        titleCell.append(h('div',{text:row.signature || '-',style:{fontSize:'12px',color:C.m}}));
        tr.append(titleCell);

        tr.append(h('td',{text:row.area,style:tdS()}));
        tr.append(h('td',{html:`<strong>${row.coverage_count}</strong>/${archCount}`,style:tdS('center')}));

        archIds.forEach(arch => {
          const cell = row.cells?.[arch] || {};
          const td = h('td',{style:{...tdS('center'),whiteSpace:'nowrap'}});
          if (!cell.exists) {
            td.append(h('span',{text:'—',style:{color:C.m}}));
            tr.append(td);
            return;
          }
          const wrap = h('div',{style:{
            display:'inline-flex',alignItems:'center',justifyContent:'center',
            gap:'6px',flexWrap:'wrap',minHeight:'20px'
          }});
          const variants = cell.variants || [];
          variants.slice(0, 3).forEach(v => wrap.append(renderVariantLink(row, arch, v, matrixData.source)));
          td.append(wrap);
          tr.append(td);
        });
        tbody.append(tr);
      });
      table.append(tbody);
      tableWrap.append(table);
    }

    redrawTable();
  }

  // ═══════════ MAIN RENDER ═══════════

  async function render() {
    const container = document.getElementById('ci-analytics-view');
    if (!container) return;
    container.innerHTML = '<p style="color:#8b949e">Loading analytics...</p>';

    const [data, amdMatrixData] = await Promise.all([
      J('data/vllm/ci/analytics.json').then(d => d || {}),
      J('data/vllm/ci/amd_test_matrix.json').then(d => d || null),
      typeof _shardBasesReady !== 'undefined' ? _shardBasesReady : Promise.resolve(),
    ]);
    container.innerHTML = '';

    const pipelines = Object.keys(data);
    if (!pipelines.length) {
      container.append(h('div',{style:{textAlign:'center',padding:'60px 20px',color:C.m}},[
        h('h3',{text:'CI Analytics',style:{marginBottom:'8px'}}),
        h('p',{text:'No analytics data available yet.'}),
      ]));
      return;
    }

    let activeView = 'comparison';

    // Title
    container.append(h('h2',{text:'CI Analytics',style:{marginBottom:'4px'}}));
    container.append(h('p',{text:'Nightly builds only',style:{color:C.m,fontSize:'12px',marginBottom:'16px',fontStyle:'italic'}}));

    // View tabs: Comparison | Queue Comparison
    const viewTabs = h('div',{style:{display:'flex',gap:'0',marginBottom:'20px',borderBottom:`1px solid ${C.bd}`}});
    const views = [
      {id:'comparison',label:'Pipeline Comparison'},
      {id:'builds',label:'Recent Builds'},
      {id:'groups',label:'Test Group Trends'},
      {id:'coverage',label:'AMD HW Matrix'},
      {id:'queue',label:'Queue Comparison'}
    ];
    const tabBtns = {};
    for (const v of views) {
      const isActive = v.id === 'comparison';
      const btn = h('button',{text:v.label,style:{background:'transparent',border:'none',borderBottom:isActive?`2px solid ${C.b}`:'2px solid transparent',color:isActive?C.t:C.m,padding:'8px 16px',cursor:'pointer',fontSize:'13px',fontWeight:isActive?'600':'400',fontFamily:'inherit',transition:'all 0.2s'}});
      btn.dataset.view = v.id;
      tabBtns[v.id] = btn;
      viewTabs.append(btn);
    }
    container.append(viewTabs);

    const content = h('div');
    container.append(content);

    async function renderContent() {
      content.innerHTML = '';
      if (activeView === 'comparison') {
        renderComparisonView(content, data, pipelines);
      } else if (activeView === 'builds') {
        renderBuildsMatrix(content, data, pipelines);
      } else if (activeView === 'groups') {
        renderGroupTrends(content, data, pipelines, amdMatrixData);
      } else if (activeView === 'coverage') {
        renderAmdHardwareMatrix(content, amdMatrixData);
      } else {
        renderQueueComparison(content, data, pipelines);
      }
    }

    for (const v of views) {
      tabBtns[v.id].onclick = () => {
        activeView = v.id;
        for (const vv of views) {
          tabBtns[vv.id].style.borderBottomColor = vv.id === v.id ? C.b : 'transparent';
          tabBtns[vv.id].style.fontWeight = vv.id === v.id ? '600' : '400';
          tabBtns[vv.id].style.color = vv.id === v.id ? C.t : C.m;
        }
        renderContent();
      };
    }

    renderContent();
  }

  // Lazy load
  const obs = new MutationObserver(() => {
    const p = document.getElementById('tab-ci-analytics');
    if (p?.classList.contains('active') && !p.dataset.loaded) { p.dataset.loaded = '1'; render(); }
  });
  document.addEventListener('DOMContentLoaded', () => {
    const p = document.getElementById('tab-ci-analytics');
    if (p) {
      obs.observe(p, {attributes:true, attributeFilter:['class']});
      if (p.classList.contains('active') && !p.dataset.loaded) { p.dataset.loaded = '1'; render(); }
    }
  });
})();
