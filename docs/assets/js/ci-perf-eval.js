/**
 * Perf Eval — AMD nightly performance + accuracy from the vllm/perf-eval pipeline.
 *
 * Data source: data/vllm/perf_eval/perf_eval.json, produced by
 * scripts/vllm/collect_perf_eval.py from webhook-fed events (no Buildkite API
 * polling). AMD-only, nightly-only. Every metric is data-driven: its
 * "higher/lower is better" direction and red/green status come straight from
 * the payload, so workloads/metrics added or removed upstream render correctly
 * without touching this file.
 *
 * The view is built for executives: each model headlines its latest nightly's
 * vLLM commit/image, KPI tiles call out wins (green) and regressions (red) vs
 * the previous nightly with an explicit "higher/lower is better" hint, and a
 * sparkline shows the trend across nightlies.
 */
(function() {
  if (window.__DASHBOARD_V2__) return;
  const _s = getComputedStyle(document.documentElement);
  const C = {
    g: _s.getPropertyValue('--accent-green').trim() || '#238636',
    y: _s.getPropertyValue('--accent-orange').trim() || '#d29922',
    r: _s.getPropertyValue('--badge-closed').trim() || '#da3633',
    b: _s.getPropertyValue('--accent-blue').trim() || '#1f6feb',
    p: _s.getPropertyValue('--accent-purple').trim() || '#8957e5',
    m: _s.getPropertyValue('--text-muted').trim() || '#8b949e',
    t: _s.getPropertyValue('--text').trim() || '#e6edf3',
    bg: _s.getPropertyValue('--card-bg').trim() || '#161b22',
    bg2: _s.getPropertyValue('--bg').trim() || '#0d1117',
    bd: _s.getPropertyValue('--border').trim() || '#30363d',
  };

  const h = el; // shared element factory from utils.js
  const DATA_URL = 'data/vllm/perf_eval/perf_eval.json';

  function statusColor(status) {
    if (status === 'good') return C.g;
    if (status === 'bad') return C.r;
    return C.m;
  }

  function directionHint(direction) {
    return direction === 'lower' ? 'Lower is better' : 'Higher is better';
  }

  function fmtValue(value, unit) {
    if (value == null || !Number.isFinite(value)) return '\u2014';
    let text;
    if (unit === 's') {
      // Latency: show ms for sub-second values so execs read "420 ms" not "0.42 s".
      text = value < 1 ? Math.round(value * 1000) + ' ms' : value.toFixed(2) + ' s';
      return text;
    }
    if (Math.abs(value) >= 1000) text = Math.round(value).toLocaleString();
    else if (Math.abs(value) >= 1) text = value.toFixed(1);
    else text = value.toFixed(3);
    return unit ? text + ' ' + unit : text;
  }

  function fmtPct(pct) {
    if (pct == null || !Number.isFinite(pct)) return '';
    const sign = pct > 0 ? '+' : '';
    return sign + pct.toFixed(1) + '%';
  }

  function shortCommit(commit) {
    return commit ? String(commit).slice(0, 7) : '\u2014';
  }

  // A delta badge: arrow shows the raw direction of movement, color shows
  // whether that movement is good or bad for this metric.
  function deltaBadge(block, opts) {
    opts = opts || {};
    const color = statusColor(block.status);
    let arrow = '';
    const moved = block.delta != null && block.delta !== 0;
    if (moved) arrow = block.delta > 0 ? '\u25B2' : '\u25BC';
    let text;
    if (block.previous == null) {
      text = 'first nightly';
    } else if (opts.absolute) {
      const sign = block.delta > 0 ? '+' : '';
      text = arrow + ' ' + sign + (block.delta != null ? block.delta.toFixed(3) : '');
    } else {
      text = arrow + ' ' + fmtPct(block.delta_pct);
    }
    return h('span', {
      text: text,
      title: 'vs previous nightly',
      style: {
        color: color, fontWeight: '700', fontSize: '12px', whiteSpace: 'nowrap',
      },
    });
  }

  function sparkline(host, series, direction, unit) {
    if (typeof Chart === 'undefined' || !series || series.length < 2) return;
    const wrap = h('div', { style: { position: 'relative', height: '46px', marginTop: '8px' } });
    const cv = h('canvas');
    wrap.append(cv);
    host.append(wrap);
    const values = series.map(p => p.value);
    const labels = series.map(p => shortCommit(p.vllm_commit));
    // Color the line by whether the latest point is an improvement.
    const last = values[values.length - 1];
    const prev = values[values.length - 2];
    const improved = direction === 'lower' ? last < prev : last > prev;
    const flat = last === prev;
    const lineColor = flat ? C.m : improved ? C.g : C.r;
    new Chart(cv, {
      type: 'line',
      data: {
        labels: labels,
        datasets: [{
          data: values,
          borderColor: lineColor,
          backgroundColor: lineColor + '22',
          borderWidth: 2,
          pointRadius: 2,
          pointBackgroundColor: lineColor,
          tension: 0.25,
          fill: true,
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: (items) => 'commit ' + (items[0].label || ''),
              label: (item) => fmtValue(item.parsed.y, unit),
            },
          },
        },
        scales: { x: { display: false }, y: { display: false } },
      },
    });
  }

  function metricTile(metric, block) {
    const color = statusColor(block.status);
    const tile = h('div', {
      style: {
        background: C.bg2, border: `1px solid ${C.bd}`, borderRadius: '8px',
        padding: '12px 14px', borderTop: `3px solid ${color}`,
        display: 'flex', flexDirection: 'column', gap: '2px',
      },
    });
    tile.append(h('div', {
      text: block.label || metric,
      style: { fontSize: '11px', color: C.m, textTransform: 'uppercase', letterSpacing: '.4px' },
    }));
    const valRow = h('div', { style: { display: 'flex', alignItems: 'baseline', gap: '8px', flexWrap: 'wrap' } });
    valRow.append(h('div', {
      text: fmtValue(block.latest, block.unit),
      style: { fontSize: '22px', fontWeight: '800', color: C.t, lineHeight: '1.1' },
    }));
    valRow.append(deltaBadge(block));
    tile.append(valRow);
    tile.append(h('div', {
      text: directionHint(block.direction),
      style: { fontSize: '11px', color: C.m },
    }));
    sparkline(tile, block.series, block.direction, block.unit);
    return tile;
  }

  function perfConfigBlock(cfg) {
    const wrap = h('div', { style: { marginTop: '14px' } });
    const head = h('div', { style: { display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '8px', flexWrap: 'wrap' } });
    head.append(h('span', { text: cfg.label, style: { fontSize: '14px', fontWeight: '700', color: C.t } }));
    const meta = [];
    if (cfg.tp) meta.push('TP ' + cfg.tp);
    if (cfg.precision) meta.push(cfg.precision);
    if (meta.length) head.append(h('span', { text: meta.join(' \u00b7 '), style: { fontSize: '12px', color: C.m } }));
    wrap.append(head);

    // Order tiles so the headline throughput leads, latencies follow.
    const order = ['tput_per_gpu', 'output_tput_per_gpu', 'input_tput_per_gpu',
      'mean_ttft', 'p99_ttft', 'mean_tpot', 'mean_intvty'];
    const keys = Object.keys(cfg.metrics).sort((a, b) => {
      const ia = order.indexOf(a), ib = order.indexOf(b);
      return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
    });
    const grid = h('div', {
      style: {
        display: 'grid', gap: '10px',
        gridTemplateColumns: 'repeat(auto-fill, minmax(190px, 1fr))',
      },
    });
    for (const k of keys) grid.append(metricTile(k, cfg.metrics[k]));
    wrap.append(grid);
    return wrap;
  }

  function accuracyBlock(model) {
    const tasks = model.accuracy_tasks || [];
    if (!tasks.length) return null;
    const wrap = h('div', { style: { marginTop: '18px' } });
    wrap.append(h('div', {
      text: 'Accuracy', style: { fontSize: '14px', fontWeight: '700', color: C.t, marginBottom: '4px' },
    }));
    wrap.append(h('div', {
      text: 'lm-eval scores per task \u00b7 ' + directionHint('higher'),
      style: { fontSize: '11px', color: C.m, marginBottom: '8px' },
    }));
    const tbl = h('table', { style: { width: '100%', borderCollapse: 'collapse', fontSize: '13px' } });
    const th = a => ({ textAlign: a || 'left', padding: '6px 10px', borderBottom: `2px solid ${C.bd}`, color: C.m, fontSize: '11px', textTransform: 'uppercase' });
    tbl.append(h('thead', {}, [h('tr', {}, [
      h('th', { text: 'Task', style: th() }),
      h('th', { text: 'Metric', style: th() }),
      h('th', { text: 'Latest', style: th('right') }),
      h('th', { text: 'vs prev', style: th('right') }),
      h('th', { text: 'Trend', style: th('center') }),
    ])]));
    const tb = h('tbody');
    const td = a => ({ textAlign: a || 'left', padding: '6px 10px', borderBottom: `1px solid ${C.bd}55`, color: C.t });
    for (const task of tasks) {
      const tr = h('tr');
      tr.append(h('td', { text: task.task, style: { ...td(), fontWeight: '600' } }));
      tr.append(h('td', { text: task.metric, style: { ...td(), color: C.m, fontSize: '12px' } }));
      tr.append(h('td', {
        html: '<span style="font-weight:700">' + (task.latest != null ? (task.latest * 100).toFixed(1) + '%' : '\u2014') + '</span>',
        style: { ...td('right'), color: statusColor(task.status) },
      }));
      const deltaCell = h('td', { style: td('right') });
      deltaCell.append(deltaBadge(task, { absolute: false }));
      tr.append(deltaCell);
      const trendCell = h('td', { style: { ...td('center'), width: '140px' } });
      sparkline(trendCell, task.series, 'higher', '');
      tr.append(trendCell);
      tb.append(tr);
    }
    tbl.append(tb);
    wrap.append(tbl);
    return wrap;
  }

  function provenanceLine(latest) {
    const wrap = h('div', { style: { display: 'flex', gap: '14px', flexWrap: 'wrap', alignItems: 'center', marginTop: '4px', fontSize: '12px', color: C.m } });
    if (latest.vllm_commit) {
      const commitEl = latest.build_url
        ? h('a', { text: 'commit ' + shortCommit(latest.vllm_commit), href: latest.build_url, target: '_blank', rel: 'noopener', style: { color: C.b, textDecoration: 'none', fontFamily: 'monospace' } })
        : h('span', { text: 'commit ' + shortCommit(latest.vllm_commit), style: { fontFamily: 'monospace' } });
      wrap.append(commitEl);
    }
    if (latest.image) wrap.append(h('span', { text: latest.image, style: { fontFamily: 'monospace' } }));
    if (latest.build_number != null && latest.build_url) {
      wrap.append(h('a', { text: 'build #' + latest.build_number, href: latest.build_url, target: '_blank', rel: 'noopener', style: { color: C.b, textDecoration: 'none' } }));
    }
    if (latest.date) wrap.append(h('span', { text: 'latest nightly ' + relativeTime(latest.date) }));
    return wrap;
  }

  function modelCard(model) {
    const card = h('div', {
      style: {
        background: C.bg, border: `1px solid ${C.bd}`, borderRadius: '10px',
        padding: '18px 20px', marginBottom: '18px',
      },
    });
    const head = h('div', { style: { borderBottom: `1px solid ${C.bd}`, paddingBottom: '10px', marginBottom: '6px' } });
    const titleRow = h('div', { style: { display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' } });
    titleRow.append(h('span', { text: model.model, style: { fontSize: '18px', fontWeight: '800', color: C.t } }));
    for (const dev of (model.devices || [])) {
      titleRow.append(h('span', {
        text: String(dev).toUpperCase(),
        style: { background: C.r + '22', color: C.r, border: `1px solid ${C.r}55`, borderRadius: '20px', padding: '2px 10px', fontSize: '11px', fontWeight: '700' },
      }));
    }
    titleRow.append(h('span', { text: model.nightly_count + ' nightlies', style: { fontSize: '12px', color: C.m, marginLeft: 'auto' } }));
    head.append(titleRow);
    head.append(provenanceLine(model.latest || {}));
    card.append(head);

    const perfConfigs = model.perf_configs || [];
    if (perfConfigs.length) {
      card.append(h('div', { text: 'Performance', style: { fontSize: '14px', fontWeight: '700', color: C.t, marginTop: '12px' } }));
      for (const cfg of perfConfigs) card.append(perfConfigBlock(cfg));
    }
    const acc = accuracyBlock(model);
    if (acc) card.append(acc);
    return card;
  }

  function legend() {
    const wrap = h('div', {
      style: {
        display: 'flex', gap: '18px', flexWrap: 'wrap', alignItems: 'center',
        background: C.bg, border: `1px solid ${C.bd}`, borderRadius: '8px',
        padding: '10px 14px', margin: '12px 0 20px', fontSize: '12px', color: C.m,
      },
    });
    const item = (color, label) => {
      const i = h('span', { style: { display: 'inline-flex', alignItems: 'center', gap: '6px' } });
      i.append(h('span', { style: { width: '12px', height: '12px', borderRadius: '3px', background: color, display: 'inline-block' } }));
      i.append(h('span', { text: label }));
      return i;
    };
    wrap.append(h('strong', { text: 'How to read:', style: { color: C.t } }));
    wrap.append(item(C.g, 'Improved vs previous nightly'));
    wrap.append(item(C.r, 'Regressed vs previous nightly'));
    wrap.append(item(C.m, 'Within noise band (no change)'));
    wrap.append(h('span', { text: '\u25B2/\u25BC = direction of change; each tile states whether higher or lower is better.' }));
    return wrap;
  }

  function summaryRow(data) {
    const s = data.summary || {};
    const row = h('div', { style: { display: 'flex', gap: '12px', flexWrap: 'wrap', marginBottom: '6px' } });
    const box = (num, label, color) => h('div', {
      style: {
        background: C.bg, border: `1px solid ${C.bd}`, borderTop: `3px solid ${color || C.b}`,
        borderRadius: '8px', padding: '12px 18px', minWidth: '120px',
      },
    }, [
      h('div', { text: String(num), style: { fontSize: '24px', fontWeight: '800', color: C.t } }),
      h('div', { text: label, style: { fontSize: '12px', color: C.m } }),
    ]);
    row.append(box(s.models || 0, 'AMD models', C.b));
    row.append(box(s.nightlies || 0, 'Nightlies tracked', C.p));
    row.append(box((s.amd_devices || []).map(d => d.toUpperCase()).join(', ') || '\u2014', 'AMD hardware', C.r));
    return row;
  }

  async function render() {
    const host = document.getElementById('ci-perf-eval-view');
    if (!host) return;
    host.innerHTML = '';

    const header = h('div', { style: { marginBottom: '6px' } });
    header.append(h('h2', { text: 'AMD Perf Eval \u2014 Nightly', style: { margin: '0 0 4px', color: C.t } }));
    header.append(h('p', {
      html: 'Performance and accuracy for AMD (ROCm) nightly runs from the '
        + '<a href="https://buildkite.com/vllm/perf-eval" target="_blank" rel="noopener" style="color:' + C.b + '">vllm/perf-eval</a> '
        + 'pipeline. Fed by Buildkite webhooks \u2014 no API polling. NVIDIA workloads are excluded.',
      style: { margin: '0', color: C.m, fontSize: '13px' },
    }));
    host.append(header);

    const data = await fetchJSON(DATA_URL, { timeoutMs: 8000, forceRefresh: true });
    if (!data || !Array.isArray(data.models)) {
      host.append(h('div', {
        text: 'No perf-eval data available yet. The first nightly webhook has not been ingested.',
        style: { padding: '24px', color: C.m, border: `1px dashed ${C.bd}`, borderRadius: '8px', marginTop: '14px' },
      }));
      return;
    }

    host.append(summaryRow(data));
    host.append(legend());

    if (data.generated_at) {
      host.append(h('div', {
        text: 'Data generated ' + relativeTime(data.generated_at),
        style: { fontSize: '11px', color: C.m, marginBottom: '14px' },
      }));
    }

    if (!data.models.length) {
      host.append(h('div', {
        text: 'No AMD nightly results yet.',
        style: { padding: '24px', color: C.m, border: `1px dashed ${C.bd}`, borderRadius: '8px' },
      }));
      return;
    }

    for (const model of data.models) host.append(modelCard(model));
  }

  const obs = new MutationObserver(() => {
    const p = document.getElementById('tab-ci-perf-eval');
    if (p && p.classList.contains('active') && !p.dataset.loaded) {
      p.dataset.loaded = '1';
      render();
    }
  });
  document.addEventListener('DOMContentLoaded', () => {
    const p = document.getElementById('tab-ci-perf-eval');
    if (p) {
      obs.observe(p, { attributes: true, attributeFilter: ['class'] });
      if (p.classList.contains('active') && !p.dataset.loaded) {
        p.dataset.loaded = '1';
        render();
      }
    }
  });
})();
