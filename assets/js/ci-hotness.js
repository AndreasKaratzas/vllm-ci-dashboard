/**
 * CI Hotness — windowed view of AMD queue workload.
 * Shows test-group frequency + time-to-completion, plus per-branch hotness.
 * Window controls (1h / 3h / 24h / 72h) let the user switch timeframes
 * without refetching — the collector pre-aggregates every window.
 */
(function() {
  const _s = getComputedStyle(document.documentElement);
  const C = {
    g:_s.getPropertyValue('--accent-green').trim()||'#238636',
    y:_s.getPropertyValue('--accent-orange').trim()||'#d29922',
    r:_s.getPropertyValue('--badge-closed').trim()||'#da3633',
    b:_s.getPropertyValue('--accent-blue').trim()||'#1f6feb',
    p:_s.getPropertyValue('--accent-purple').trim()||'#8957e5',
    m:_s.getPropertyValue('--text-muted').trim()||'#8b949e',
    t:_s.getPropertyValue('--text').trim()||'#e6edf3',
    bg:_s.getPropertyValue('--card-bg').trim()||'#161b22',
    bg2:_s.getPropertyValue('--bg').trim()||'#0d1117',
    bd:_s.getPropertyValue('--border').trim()||'#30363d',
  };

  const h = el;  // shared element factory defined in utils.js

  const WINDOW_ORDER = ['1h', '3h', '24h', '72h'];
  const WINDOW_LABEL = {
    '1h': 'Last hour',
    '3h': 'Last 3h',
    '24h': 'Last 24h',
    '72h': 'Last 3 days',
  };

  async function loadHotness() {
    try {
      const r = await fetch('data/vllm/ci/hotness.json?_='+Math.floor(Date.now()/1000));
      if (!r.ok) return null;
      return await r.json();
    } catch(e) { return null; }
  }

  async function loadQueueTimeseries() {
    // JSONL: last ~72h of 30-min snapshots is the slice we plot.
    try {
      const r = await fetch('data/vllm/ci/queue_timeseries.jsonl?_='+Math.floor(Date.now()/1000));
      if (!r.ok) return [];
      const txt = await r.text();
      const out = [];
      const cutoff = Date.now() - 72*3600*1000;
      for (const line of txt.split('\n')) {
        if (!line.trim()) continue;
        try {
          const row = JSON.parse(line);
          const t = Date.parse(row.ts);
          if (!isNaN(t) && t >= cutoff) out.push(row);
        } catch(_) { /* skip malformed */ }
      }
      return out;
    } catch(e) { return []; }
  }

  function resolveBranchUrl(row) {
    const base = row.fork_url && row.fork_url.startsWith('http') ? row.fork_url :
                 row.fork_url ? 'https://github.com/' + row.fork_url.replace(/^git@github\.com:/,'').replace(/\.git$/,'') :
                 'https://github.com/vllm-project/vllm';
    if (row.commit) return base + '/commit/' + row.commit;
    if (row.branch) return base + '/tree/' + encodeURIComponent(row.branch);
    return base;
  }

  function formatRelative(iso) {
    if (!iso) return '\u2014';
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    const diffMin = (Date.now() - d.getTime()) / 60000;
    if (diffMin < 60) return `${Math.round(diffMin)}m ago`;
    if (diffMin < 1440) return `${Math.round(diffMin/60)}h ago`;
    return `${Math.round(diffMin/1440)}d ago`;
  }

  function scoreBranch(row) {
    // Hotness: build count is the main signal, amplified by how recently the
    // branch fired (logistic decay over the 72h window).
    if (!row.last_seen) return row.builds || 0;
    const ageHours = (Date.now() - new Date(row.last_seen).getTime()) / 3600000;
    const recency = Math.max(0.2, Math.exp(-ageHours / 48));
    return (row.builds || 0) * recency + (row.count || 0) * 0.05 * recency;
  }

  function buildWindowsFromLegacy(data) {
    // Old hotness.json shape: top-level test_groups/branches/queues only.
    // Wrap it so the rest of the renderer can assume data.windows exists.
    const key = `${data.window_hours || 72}h`;
    return {
      [key]: {
        test_groups: data.test_groups || [],
        branches: data.branches || [],
        queues: data.queues || [],
        window_hours: data.window_hours || 72,
      },
    };
  }

  async function render() {
    const host = document.getElementById('ci-hotness-view');
    if (!host) return;
    host.innerHTML = '<p style="color:#8b949e">Loading hotness data...</p>';

    const data = await loadHotness();
    if (!data) {
      host.innerHTML = '<p style="color:#8b949e">No hotness data yet. <code>scripts/vllm/collect_hotness.py</code> runs hourly once the workflow is enabled.</p>';
      return;
    }

    const windows = (data.windows && Object.keys(data.windows).length) ? data.windows : buildWindowsFromLegacy(data);
    const available = WINDOW_ORDER.filter(k => windows[k]);
    const initialWindow = available.includes('24h') ? '24h'
                         : available.includes('72h') ? '72h'
                         : available[0] || Object.keys(windows)[0];

    const state = {
      view: 'groups',
      window: initialWindow,
      workload: 'all',
      hw: 'all',
      q: '',
    };

    host.innerHTML = '';
    host.append(h('h2',{text:'CI Workload Trajectory',style:{marginBottom:'6px'}}));
    const subtitle = h('p',{style:{color:C.m,marginBottom:'14px',fontSize:'13px'}});
    host.append(subtitle);
    host.append(h('div',{style:{
      padding:'10px 14px',background:C.b+'12',border:`1px solid ${C.b}33`,
      borderRadius:'8px',marginBottom:'14px',fontSize:'13px',color:C.t
    }},[
      h('span',{text:'Hotness is already windowed over 1h / 3h / 24h / 72h, so older hardware naturally ages out without a manual reset.'})
    ]));

    // Window pills — drives every downstream render.
    const windowRow = h('div',{style:{display:'flex',gap:'8px',alignItems:'center',flexWrap:'wrap',marginBottom:'14px'}});
    windowRow.append(h('span',{text:'Timeframe:',style:{color:C.m,fontSize:'12px',fontWeight:'600',textTransform:'uppercase',letterSpacing:'.5px'}}));
    const windowBtns = {};
    for (const w of available) {
      const btn = h('button',{text:WINDOW_LABEL[w]||w,style:{
        background:C.bd,border:'none',color:C.t,
        padding:'6px 14px',borderRadius:'3px',cursor:'pointer',
        fontSize:'13px',fontFamily:'inherit',fontWeight:'400',
      }});
      btn.onclick = () => { state.window = w; state.hw = 'all'; state.workload = 'all'; renderAll(); };
      windowBtns[w] = btn;
      windowRow.append(btn);
    }
    host.append(windowRow);

    const cardsHost = h('div',{style:{marginBottom:'20px'}});
    host.append(cardsHost);

    const chartHost = h('div',{style:{marginBottom:'20px'}});
    host.append(chartHost);
    renderQueueChart(chartHost).catch(()=>{});

    // Tabs
    const tabBar = h('div',{style:{display:'flex',gap:'2px',marginBottom:'12px'}});
    const TABS = [
      {k:'groups',label:'Test groups'},
      {k:'branches',label:'Branches'},
      {k:'queues',label:'Queues'},
    ];
    const tabBtns = {};
    for (const t of TABS) {
      const btn = h('button',{text:t.label,style:{
        background:C.bd,border:'none',color:C.t,
        padding:'6px 14px',borderRadius:'3px',cursor:'pointer',
        fontSize:'13px',fontFamily:'inherit',fontWeight:'400',
      }});
      btn.onclick = () => { state.view = t.k; renderAll(); };
      tabBtns[t.k] = btn;
      tabBar.append(btn);
    }
    host.append(tabBar);

    const body = h('div');
    host.append(body);

    function currentWindow() { return windows[state.window] || {test_groups:[],branches:[],queues:[]}; }

    function renderAll() {
      // Window button styling
      for (const [k, btn] of Object.entries(windowBtns)) {
        const active = k === state.window;
        btn.style.background = active ? C.p : C.bd;
        btn.style.fontWeight = active ? '600' : '400';
      }
      // Tab button styling
      for (const [k, btn] of Object.entries(tabBtns)) {
        const active = k === state.view;
        btn.style.background = active ? C.b : C.bd;
        btn.style.fontWeight = active ? '600' : '400';
      }
      subtitle.textContent = `Showing ${WINDOW_LABEL[state.window] || state.window} \u2014 ${data.builds_examined || 0} builds walked overall. Generated ${formatRelative(data.generated_at)}.`;
      renderCards();
      renderBody();
    }

    function renderCards() {
      cardsHost.innerHTML = '';
      const w = currentWindow();
      const groups = w.test_groups || [];
      const branches = w.branches || [];
      const totalRuns = groups.reduce((s,g)=>s+(g.count||0),0);
      const totalFails = groups.reduce((s,g)=>s+(g.failures||0),0);
      const failRate = totalRuns ? (totalFails / totalRuns * 100) : 0;
      const hotGroup = groups[0];
      const hotBranch = branches.slice().sort((a,b)=>scoreBranch(b)-scoreBranch(a))[0];
      // Slowest (highest p90) group with at least 3 runs — a single slow outlier is noise.
      const slowest = groups.slice().filter(g=>(g.count||0)>=3).sort((a,b)=>(b.p90_min||0)-(a.p90_min||0))[0];

      const cards = h('div',{style:{display:'grid',gridTemplateColumns:'repeat(4,1fr)',gap:'12px'}});
      const card = (label, value, sub, color) => h('div',{style:{background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'8px',padding:'14px 18px',borderTop:`3px solid ${color}`}},[
        h('div',{text:label,style:{fontSize:'12px',color:C.m,textTransform:'uppercase',letterSpacing:'.5px',marginBottom:'4px'}}),
        h('div',{text:String(value),style:{fontSize:'22px',fontWeight:'800',color,lineHeight:'1.1'}}),
        sub?h('div',{text:sub,style:{fontSize:'12px',color:C.m,marginTop:'4px'}}):null,
      ]);
      cards.append(card(`Runs (${state.window})`, totalRuns.toLocaleString(), `${groups.length} groups`, C.b));
      cards.append(card('Failure rate', failRate.toFixed(1)+'%', `${totalFails} failing jobs`, failRate > 10 ? C.r : C.g));
      cards.append(card('Busiest group', hotGroup ? hotGroup.group.slice(0,28) : '\u2014', hotGroup ? `${hotGroup.count} runs \u2022 p90 ${hotGroup.p90_min}m` : 'no runs in window', C.y));
      cards.append(card('Slowest group (p90)', slowest ? slowest.group.slice(0,28) : '\u2014', slowest ? `${slowest.p90_min}m \u2022 ${slowest.count} runs` : 'insufficient data', C.r));
      cardsHost.append(cards);
    }

    function renderBody() {
      body.innerHTML = '';
      const w = currentWindow();
      if (state.view === 'groups') return renderGroups(w);
      if (state.view === 'branches') return renderBranches(w);
      return renderQueues(w);
    }

    function renderGroups(w) {
      const groups = w.test_groups || [];
      const filterRow = h('div',{style:{display:'flex',gap:'10px',alignItems:'center',flexWrap:'wrap',marginBottom:'10px'}});
      const mkPill = (label, active, onclick) => {
        const b = h('button',{text:label,style:{
          background:active?C.p:C.bd,border:'none',color:C.t,
          padding:'4px 10px',borderRadius:'3px',cursor:'pointer',
          fontSize:'12px',fontFamily:'inherit',fontWeight:active?'600':'400',
        }});
        b.onclick = onclick;
        return b;
      };
      filterRow.append(h('span',{text:'Workload:',style:{color:C.m,fontSize:'12px'}}));
      for (const wk of ['all','vllm','omni']) {
        filterRow.append(mkPill(wk, state.workload===wk, ()=>{state.workload=wk; renderBody();}));
      }
      filterRow.append(h('span',{text:'Hardware:',style:{color:C.m,fontSize:'12px',marginLeft:'10px'}}));
      const allHw = Array.from(new Set(groups.map(g=>g.hw))).sort();
      for (const hw of ['all', ...allHw]) {
        filterRow.append(mkPill(hw, state.hw===hw, ()=>{state.hw=hw; renderBody();}));
      }
      const search = h('input',{type:'search',placeholder:'Filter groups\u2026',value:state.q,autocomplete:'off',autocorrect:'off',autocapitalize:'off',spellcheck:'false',name:'ci-trajectory-group-filter',style:{marginLeft:'auto',background:C.bg2,border:`1px solid ${C.bd}`,color:C.t,padding:'4px 10px',borderRadius:'3px',fontSize:'13px',minWidth:'200px'}});
      search.oninput = () => { state.q = search.value; renderGroupTable(); };
      filterRow.append(search);
      body.append(filterRow);

      const tblHost = h('div');
      body.append(tblHost);

      function renderGroupTable() {
        tblHost.innerHTML = '';
        let rows = groups.slice();
        if (state.workload !== 'all') rows = rows.filter(r=>r.workload===state.workload);
        if (state.hw !== 'all') rows = rows.filter(r=>r.hw===state.hw);
        if (state.q) {
          const q = state.q.toLowerCase();
          rows = rows.filter(r => (r.group||'').toLowerCase().includes(q));
        }
        if (!rows.length) {
          tblHost.append(h('p',{text:'No matching groups in this window.',style:{color:C.m,fontSize:'13px',padding:'20px 0'}}));
          return;
        }
        const tbl = h('table',{style:{width:'100%',borderCollapse:'collapse',fontSize:'13px'}});
        const th = s => ({textAlign:s||'left',padding:'6px 8px',borderBottom:`1px solid ${C.bd}`,color:C.m,fontSize:'12px',textTransform:'uppercase',letterSpacing:'.3px'});
        tbl.append(h('thead',{},[h('tr',{},[
          h('th',{text:'Test group',style:th()}),
          h('th',{text:'HW',style:th('center')}),
          h('th',{text:'Workload',style:th('center')}),
          h('th',{text:'Runs',style:th('center')}),
          h('th',{text:'Avg min',style:th('center')}),
          h('th',{text:'p50 min',style:th('center')}),
          h('th',{text:'p90 min',style:th('center')}),
          h('th',{text:'Max min',style:th('center')}),
          h('th',{text:'Fail rate',style:th('center')}),
          h('th',{text:'Last seen',style:th('center')}),
        ])]));
        const tb = h('tbody');
        const td = s => ({padding:'6px 8px',textAlign:s||'left',fontSize:'13px'});
        for (const r of rows.slice(0,200)) {
          const tr = h('tr',{style:{borderBottom:`1px solid ${C.bd}22`}});
          tr.append(h('td',{text:r.group,style:{...td(),fontWeight:'600'}}));
          tr.append(h('td',{text:(r.hw||'?').toUpperCase(),style:{...td('center'),color:C.m}}));
          tr.append(h('td',{text:r.workload||'vllm',style:{...td('center'),color:r.workload==='omni'?C.p:C.m}}));
          tr.append(h('td',{text:String(r.count),style:{...td('center'),fontWeight:'600'}}));
          tr.append(h('td',{text:r.avg_min.toFixed(1),style:{...td('center'),color:C.m}}));
          tr.append(h('td',{text:(r.p50_min||0).toFixed(1),style:{...td('center'),color:C.m}}));
          tr.append(h('td',{text:r.p90_min.toFixed(1),style:{...td('center'),color:r.p90_min>30?C.r:C.t}}));
          tr.append(h('td',{text:r.max_min.toFixed(1),style:{...td('center'),color:C.m}}));
          const fp = (r.fail_rate||0)*100;
          tr.append(h('td',{text:fp.toFixed(1)+'%',style:{...td('center'),color:fp>10?C.r:fp>0?C.y:C.g,fontWeight:fp>0?'600':'400'}}));
          tr.append(h('td',{text:formatRelative(r.last_seen),style:{...td('center'),color:C.m,fontSize:'12px'}}));
          tb.append(tr);
        }
        tbl.append(tb);
        tblHost.append(tbl);
        if (rows.length > 200) {
          tblHost.append(h('p',{text:`Showing 200 of ${rows.length}. Use the search box to narrow.`,style:{color:C.m,fontSize:'12px',margin:'8px 0'}}));
        }
      }
      renderGroupTable();
    }

    function renderBranches(w) {
      const rows = (w.branches||[]).slice().sort((a,b)=>scoreBranch(b)-scoreBranch(a));
      if (!rows.length) { body.append(h('p',{text:'No branch activity in this window.',style:{color:C.m,padding:'20px 0'}})); return; }
      const tbl = h('table',{style:{width:'100%',borderCollapse:'collapse',fontSize:'13px'}});
      const th = s => ({textAlign:s||'left',padding:'6px 8px',borderBottom:`1px solid ${C.bd}`,color:C.m,fontSize:'12px',textTransform:'uppercase',letterSpacing:'.3px'});
      tbl.append(h('thead',{},[h('tr',{},[
        h('th',{text:'Branch',style:th()}),
        h('th',{text:'Commit',style:th('center')}),
        h('th',{text:'Builds',style:th('center')}),
        h('th',{text:'Jobs',style:th('center')}),
        h('th',{text:'Avg min',style:th('center')}),
        h('th',{text:'p90 min',style:th('center')}),
        h('th',{text:'Workload',style:th('center')}),
        h('th',{text:'Source',style:th('center')}),
        h('th',{text:'Last seen',style:th('center')}),
      ])]));
      const tb = h('tbody');
      const td = s => ({padding:'6px 8px',textAlign:s||'left',fontSize:'13px'});
      for (const r of rows.slice(0,100)) {
        const tr = h('tr',{style:{borderBottom:`1px solid ${C.bd}22`}});
        const url = resolveBranchUrl(r);
        tr.append(h('td',{style:td()},[
          h('a',{text:r.branch||'(none)',href:url,target:'_blank',style:{color:C.b,textDecoration:'none',fontWeight:'600'}})
        ]));
        tr.append(h('td',{style:{...td('center'),fontFamily:'monospace',color:C.m,fontSize:'12px'}},[
          r.commit ? h('a',{text:r.commit.slice(0,7),href:url,target:'_blank',style:{color:C.m,textDecoration:'none'}}) : h('span',{text:'\u2014'})
        ]));
        tr.append(h('td',{text:String(r.builds),style:{...td('center'),fontWeight:'600'}}));
        tr.append(h('td',{text:String(r.count),style:{...td('center'),color:C.m}}));
        tr.append(h('td',{text:r.avg_min.toFixed(1),style:{...td('center'),color:C.m}}));
        tr.append(h('td',{text:r.p90_min.toFixed(1),style:{...td('center'),color:r.p90_min>30?C.r:C.t}}));
        tr.append(h('td',{text:r.workload||'vllm',style:{...td('center'),color:r.workload==='omni'?C.p:C.m}}));
        tr.append(h('td',{text:r.source||'\u2014',style:{...td('center'),color:C.m,fontSize:'12px'}}));
        tr.append(h('td',{text:formatRelative(r.last_seen),style:{...td('center'),color:C.m,fontSize:'12px'}}));
        tb.append(tr);
      }
      tbl.append(tb);
      body.append(tbl);
    }

    function renderQueues(w) {
      const rows = (w.queues||[]).slice();
      if (!rows.length) { body.append(h('p',{text:'No queue activity in this window.',style:{color:C.m,padding:'20px 0'}})); return; }
      const tbl = h('table',{style:{width:'100%',borderCollapse:'collapse',fontSize:'13px'}});
      const th = s => ({textAlign:s||'left',padding:'6px 8px',borderBottom:`1px solid ${C.bd}`,color:C.m,fontSize:'12px',textTransform:'uppercase',letterSpacing:'.3px'});
      tbl.append(h('thead',{},[h('tr',{},[
        h('th',{text:'Queue',style:th()}),
        h('th',{text:'Jobs',style:th('center')}),
        h('th',{text:'Avg min',style:th('center')}),
        h('th',{text:'p50 min',style:th('center')}),
        h('th',{text:'p90 min',style:th('center')}),
        h('th',{text:'Max min',style:th('center')}),
      ])]));
      const tb = h('tbody');
      const td = s => ({padding:'6px 8px',textAlign:s||'left',fontSize:'13px'});
      for (const r of rows) {
        const tr = h('tr',{style:{borderBottom:`1px solid ${C.bd}22`}});
        tr.append(h('td',{text:r.queue,style:{...td(),fontWeight:'600'}}));
        tr.append(h('td',{text:String(r.count),style:{...td('center'),fontWeight:'600'}}));
        tr.append(h('td',{text:r.avg_min.toFixed(1),style:{...td('center'),color:C.m}}));
        tr.append(h('td',{text:r.p50_min.toFixed(1),style:{...td('center'),color:C.m}}));
        tr.append(h('td',{text:r.p90_min.toFixed(1),style:{...td('center'),color:r.p90_min>30?C.r:C.t}}));
        tr.append(h('td',{text:r.max_min.toFixed(1),style:{...td('center'),color:C.m}}));
        tb.append(tr);
      }
      tbl.append(tb);
      body.append(tbl);
    }

    renderAll();
  }

  async function renderQueueChart(host) {
    if (typeof Chart === 'undefined') return;
    const card = h('div',{style:{background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'8px',padding:'12px 14px'}});
    card.append(h('div',{text:'AMD queue load (72h)',style:{fontSize:'13px',fontWeight:'600',color:C.t,marginBottom:'2px'}}));
    card.append(h('div',{text:'Waiting vs running jobs, 30-min snapshots',style:{fontSize:'11px',color:C.m,marginBottom:'8px'}}));
    const canvasWrap = h('div',{style:{position:'relative',height:'180px'}});
    const cv = h('canvas');
    canvasWrap.append(cv);
    card.append(canvasWrap);
    host.append(card);

    const rows = await loadQueueTimeseries();
    if (!rows.length) {
      card.append(h('p',{text:'No queue timeseries data.',style:{color:C.m,fontSize:'12px'}}));
      return;
    }
    const axisGrid = { color: C.bd+'55', drawBorder:false };
    const axisTick = { color: C.m, font:{size:10} };
    const labels = [], waiting = [], running = [];
    for (const row of rows) {
      const qs = row.queues || {};
      let w = 0, r = 0;
      for (const qname of Object.keys(qs)) {
        if (!qname.toLowerCase().startsWith('amd')) continue;
        w += (qs[qname].waiting||0);
        r += (qs[qname].running||0);
      }
      const t = new Date(row.ts);
      labels.push(`${String(t.getUTCMonth()+1).padStart(2,'0')}/${String(t.getUTCDate()).padStart(2,'0')} ${String(t.getUTCHours()).padStart(2,'0')}:${String(t.getUTCMinutes()).padStart(2,'0')}`);
      waiting.push(w);
      running.push(r);
    }
    new Chart(cv, {
      type:'line',
      data:{labels, datasets:[
        {label:'Running', data:running, borderColor:C.b, backgroundColor:C.b+'22', borderWidth:2, pointRadius:0, tension:0.25, fill:true},
        {label:'Waiting', data:waiting, borderColor:C.y, backgroundColor:C.y+'22', borderWidth:2, pointRadius:0, tension:0.25, fill:true},
      ]},
      options:{
        responsive:true, maintainAspectRatio:false,
        interaction:{mode:'index',intersect:false},
        plugins:{legend:{labels:{color:C.m,font:{size:11},boxWidth:10}}},
        scales:{
          x:{grid:axisGrid, ticks:{...axisTick, maxTicksLimit:8, autoSkip:true}},
          y:{grid:axisGrid, ticks:axisTick, beginAtZero:true},
        },
      },
    });
  }

  const obs = new MutationObserver(() => {
    const p = document.getElementById('tab-ci-hotness');
    if (p?.classList.contains('active') && !p.dataset.loaded) { p.dataset.loaded='1'; render(); }
  });
  document.addEventListener('DOMContentLoaded', () => {
    const p = document.getElementById('tab-ci-hotness');
    if (p) {
      obs.observe(p, {attributes:true, attributeFilter:['class']});
      if (p.classList.contains('active') && !p.dataset.loaded) { p.dataset.loaded='1'; render(); }
    }
  });
})();
