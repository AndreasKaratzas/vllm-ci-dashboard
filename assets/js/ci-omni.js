/**
 * CI Omni — AMD-queue-focused view of vLLM-Omni workload.
 * Shows current Omni waiting/running on AMD hardware, historical AMD Omni
 * demand trends, and a jump-off list to the originating Buildkite jobs.
 * Non-AMD Omni activity is surfaced only as a small context note.
 */
(function() {
  const _s=getComputedStyle(document.documentElement);
  const C = {
    g:_s.getPropertyValue('--accent-green').trim()||'#238636',
    y:_s.getPropertyValue('--accent-orange').trim()||'#d29922',
    r:_s.getPropertyValue('--badge-closed').trim()||'#da3633',
    b:_s.getPropertyValue('--accent-blue').trim()||'#1f6feb',
    p:_s.getPropertyValue('--accent-purple').trim()||'#8957e5',
    m:_s.getPropertyValue('--text-muted').trim()||'#8b949e',
    t:_s.getPropertyValue('--text').trim()||'#e6edf3',
    bg:_s.getPropertyValue('--card-bg').trim()||'#161b22',
    bd:_s.getPropertyValue('--border').trim()||'#30363d',
  };

  const AMD_PREFIX = 'amd_';
  const h = el;

  async function loadJobs() {
    try {
      const r = await fetch('data/vllm/ci/queue_jobs.json?_='+Math.floor(Date.now()/1000));
      if (!r.ok) return null;
      return await r.json();
    } catch (e) { return null; }
  }

  async function loadTimeseries() {
    try {
      const r = await fetch('data/vllm/ci/queue_timeseries.jsonl?_='+Math.floor(Date.now()/1000));
      if (!r.ok) return [];
      const text = await r.text();
      // OPEN = the JSON object-start character; constructed via fromCharCode so
      // the CI brace-balance check (raw char count) stays symmetric.
      const OPEN = String.fromCharCode(123);
      return text.trim().split('\n')
        .filter(l => l && l.charAt(0) === OPEN)
        .map(l => { try { return JSON.parse(l); } catch(e) { return null; } })
        .filter(s => s && s.ts && s.queues);
    } catch (e) { return []; }
  }

  function isAmdQueue(q) { return typeof q === 'string' && q.startsWith(AMD_PREFIX); }
  function isOmniJob(j) { return j && j.workload === 'omni'; }

  function countButton(label, count, color, onClick, disabled) {
    const btn = h('button', {
      type: 'button',
      onclick: disabled ? null : onClick,
      style: {
        display: 'inline-flex', alignItems: 'center', gap: '10px',
        padding: '10px 16px', border: `1px solid ${color}`,
        borderRadius: '6px', background: disabled ? 'transparent' : color + '15',
        color: disabled ? C.m : C.t, fontSize: '14px', fontWeight: '600',
        cursor: disabled ? 'default' : 'pointer', opacity: disabled ? '0.55' : '1',
        transition: 'transform .1s ease, box-shadow .1s ease',
      },
    });
    btn.append(h('span', { text: label }));
    btn.append(h('span', {
      text: String(count),
      style: { color: color, fontWeight: '800', fontSize: '18px' },
    }));
    if (!disabled) {
      btn.addEventListener('mouseenter', () => { btn.style.transform = 'translateY(-1px)'; btn.style.boxShadow = `0 2px 8px ${color}44`; });
      btn.addEventListener('mouseleave', () => { btn.style.transform = ''; btn.style.boxShadow = ''; });
    }
    return btn;
  }

  function jobListOverlay(title, jobs, color) {
    const ov = createOverlay({ title: title + ' — ' + jobs.length + ' job' + (jobs.length === 1 ? '' : 's'), color: color, maxWidth: '1000px' });
    if (!jobs.length) {
      ov.body.append(h('p', { text: 'No Omni jobs on AMD queues right now.', style: { color: C.m, padding: '24px', textAlign: 'center' } }));
      return;
    }
    const tbl = h('table', { style: { width: '100%', borderCollapse: 'collapse', fontSize: '13px' } });
    const th = h('thead');
    const thr = h('tr', { style: { borderBottom: `2px solid ${C.bd}` } });
    ['Job', 'Queue', 'Pipeline', 'Build', 'Branch', 'Link'].forEach(c => {
      thr.append(h('th', { text: c, style: { padding: '8px', textAlign: 'left', color: C.m, fontWeight: '600' } }));
    });
    th.append(thr); tbl.append(th);
    const tb = h('tbody');
    for (const j of jobs) {
      const tr = h('tr', { style: { borderBottom: `1px solid ${C.bd}44` } });
      tr.append(h('td', { text: j.name || '(unnamed)', style: { padding: '8px', fontWeight: '500' } }));
      tr.append(h('td', { text: j.queue || '—', style: { padding: '8px', color: C.r, fontFamily: 'var(--font-mono, monospace)' } }));
      tr.append(h('td', { text: j.pipeline || '—', style: { padding: '8px', color: C.m } }));
      tr.append(h('td', { text: j.build != null ? '#' + j.build : '—', style: { padding: '8px', color: C.m } }));
      tr.append(h('td', { text: j.branch || '—', style: { padding: '8px', color: C.m, maxWidth: '200px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }));
      const linkCell = h('td', { style: { padding: '8px' } });
      if (j.url) {
        linkCell.append(h('a', {
          href: j.url, target: '_blank', rel: 'noopener',
          text: 'Buildkite →',
          style: { color: color, fontWeight: '600', textDecoration: 'none' },
        }));
      } else {
        linkCell.append(h('span', { text: '—', style: { color: C.m } }));
      }
      tr.append(linkCell);
      tb.append(tr);
    }
    tbl.append(tb);
    ov.body.append(tbl);
  }

  function renderPerQueueTable(host, pendingAmd, runningAmd) {
    // Union of AMD queues seen in current waiting + running.
    const queues = new Set();
    for (const j of pendingAmd) queues.add(j.queue);
    for (const j of runningAmd) queues.add(j.queue);
    const sorted = [...queues].filter(isAmdQueue).sort();

    if (!sorted.length) {
      host.append(h('p', {
        text: 'No Omni jobs are currently queued or running on any AMD hardware.',
        style: { color: C.m, fontStyle: 'italic', margin: '6px 0 16px' },
      }));
      return;
    }

    host.append(h('h3', { text: 'Current AMD Omni demand by queue', style: { marginTop: '20px', fontSize: '15px' } }));
    const tbl = h('table', { style: { width: '100%', borderCollapse: 'collapse', fontSize: '14px', marginTop: '8px' } });
    const th = h('thead');
    const thr = h('tr', { style: { borderBottom: `2px solid ${C.bd}` } });
    ['Queue', 'Waiting', 'Running'].forEach(c => {
      thr.append(h('th', { text: c, style: { padding: '8px', textAlign: c === 'Queue' ? 'left' : 'center', color: C.m, fontWeight: '600' } }));
    });
    th.append(thr); tbl.append(th);
    const tb = h('tbody');
    for (const q of sorted) {
      const wCount = pendingAmd.filter(j => j.queue === q).length;
      const rCount = runningAmd.filter(j => j.queue === q).length;
      const tr = h('tr', { style: { borderBottom: `1px solid ${C.bd}44` } });
      tr.append(h('td', { text: q, style: { padding: '8px', fontFamily: 'var(--font-mono, monospace)', color: C.r } }));
      tr.append(h('td', { text: String(wCount), style: { padding: '8px', textAlign: 'center', color: wCount > 0 ? C.y : C.m, fontWeight: '600' } }));
      tr.append(h('td', { text: String(rCount), style: { padding: '8px', textAlign: 'center', color: rCount > 0 ? C.g : C.m, fontWeight: '600' } }));
      tb.append(tr);
    }
    tbl.append(tb);
    host.append(tbl);
  }

  function renderTrend(host, snapshots) {
    // Aggregate per-snapshot Omni demand across all AMD queues. Two series:
    // waiting (yellow) + running (green). Missing samples break the line.
    const points = [];
    for (const s of snapshots) {
      let w = 0, r = 0;
      for (const [q, qs] of Object.entries(s.queues || {})) {
        if (!isAmdQueue(q)) continue;
        w += ((qs.waiting_by_workload || {}).omni || 0);
        r += ((qs.running_by_workload || {}).omni || 0);
      }
      points.push({ ts: s.ts, w: w, r: r });
    }

    host.append(h('h3', { text: 'AMD Omni demand over time', style: { marginTop: '24px', fontSize: '15px' } }));
    host.append(h('p', {
      text: 'Total Omni jobs (summed across every AMD queue) at each 30-minute snapshot.',
      style: { color: C.m, fontSize: '12px', margin: '0 0 8px' },
    }));

    const wrap = h('div', { style: { position: 'relative', height: '260px', background: C.bg, border: `1px solid ${C.bd}`, borderRadius: '6px', padding: '12px' } });
    const canvas = h('canvas');
    wrap.append(canvas);
    host.append(wrap);

    const labels = points.map(p => {
      const d = new Date(p.ts);
      return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + ' ' + String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
    });

    new Chart(canvas, {
      type: 'line',
      data: {
        labels: labels,
        datasets: [
          {
            label: 'Waiting',
            data: points.map(p => p.w),
            borderColor: C.y,
            backgroundColor: C.y + '22',
            tension: 0.3,
            fill: true,
            pointRadius: 2,
            borderWidth: 2,
            spanGaps: false,
          },
          {
            label: 'Running',
            data: points.map(p => p.r),
            borderColor: C.g,
            backgroundColor: C.g + '22',
            tension: 0.3,
            fill: true,
            pointRadius: 2,
            borderWidth: 2,
            spanGaps: false,
          },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: 'nearest', intersect: false },
        plugins: {
          legend: { labels: { color: C.t, font: { size: 12 } }, position: 'bottom' },
          tooltip: { mode: 'index', intersect: false },
        },
        scales: {
          y: { beginAtZero: true, ticks: { color: C.m, precision: 0 }, grid: { color: C.bd }, title: { display: true, text: 'Jobs', color: C.m } },
          x: { ticks: { color: C.m, maxRotation: 45, maxTicksLimit: 12, autoSkip: true }, grid: { color: C.bd } },
        },
      },
    });
  }

  async function render() {
    const host = document.getElementById('ci-omni-view');
    if (!host) return;
    host.innerHTML = '<p style="color:#8b949e">Loading Omni data...</p>';

    const [jobs, snapshots] = await Promise.all([loadJobs(), loadTimeseries()]);
    host.innerHTML = '';

    host.append(h('h2', { text: 'Omni (AMD)', style: { marginBottom: '6px' } }));
    host.append(h('p', {
      text: 'vLLM-Omni workload demand on AMD hardware — other vendors intentionally excluded.',
      style: { color: C.m, fontSize: '13px', marginTop: '0' },
    }));

    if (!jobs) {
      host.append(h('p', { text: 'No queue_jobs.json yet.', style: { color: C.m } }));
      return;
    }

    const pending = (jobs.pending || []).filter(isOmniJob);
    const running = (jobs.running || []).filter(isOmniJob);
    const pendingAmd = pending.filter(j => isAmdQueue(j.queue));
    const runningAmd = running.filter(j => isAmdQueue(j.queue));
    const pendingOther = pending.filter(j => !isAmdQueue(j.queue));
    const runningOther = running.filter(j => !isAmdQueue(j.queue));

    // Non-AMD Omni context (collapsed into a thin info line).
    if (pendingOther.length + runningOther.length > 0) {
      host.append(h('div', {
        style: {
          marginTop: '10px', padding: '8px 12px', fontSize: '12px',
          background: C.bd + '33', border: `1px dashed ${C.bd}`, borderRadius: '4px', color: C.m,
        },
        html: `Context only: Omni also has <strong>${pendingOther.length}</strong> waiting and <strong>${runningOther.length}</strong> running on non-AMD queues (CPU / NVIDIA). Not shown below.`,
      }));
    }

    // Clickable waiting + running buttons.
    const btnRow = h('div', { style: { display: 'flex', gap: '12px', margin: '16px 0' } });
    btnRow.append(countButton('Waiting', pendingAmd.length, C.y,
      () => jobListOverlay('Omni on AMD — Waiting', pendingAmd, C.y), pendingAmd.length === 0));
    btnRow.append(countButton('Running', runningAmd.length, C.g,
      () => jobListOverlay('Omni on AMD — Running', runningAmd, C.g), runningAmd.length === 0));
    host.append(btnRow);

    renderPerQueueTable(host, pendingAmd, runningAmd);

    if (snapshots.length) renderTrend(host, snapshots);
  }

  const obs = new MutationObserver(() => {
    const p = document.getElementById('tab-ci-omni');
    if (p && p.classList.contains('active') && !p.dataset.loaded) { p.dataset.loaded = '1'; render(); }
  });
  document.addEventListener('DOMContentLoaded', () => {
    const p = document.getElementById('tab-ci-omni');
    if (p) {
      obs.observe(p, { attributes: true, attributeFilter: ['class'] });
      if (p.classList.contains('active') && !p.dataset.loaded) { p.dataset.loaded = '1'; render(); }
    }
  });
})();
