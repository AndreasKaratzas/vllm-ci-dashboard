/**
 * CI Queue Monitor — Interactive time-series chart with queue selectors.
 * Loads queue_timeseries.jsonl, renders Chart.js line chart with toggleable queues.
 */
(function() {
  const _s=getComputedStyle(document.documentElement);
  const C = {g:_s.getPropertyValue('--accent-green').trim()||'#238636',y:_s.getPropertyValue('--accent-orange').trim()||'#d29922',o:'#db6d28',r:_s.getPropertyValue('--badge-closed').trim()||'#da3633',b:_s.getPropertyValue('--accent-blue').trim()||'#1f6feb',p:_s.getPropertyValue('--accent-purple').trim()||'#8957e5',m:_s.getPropertyValue('--text-muted').trim()||'#8b949e',t:_s.getPropertyValue('--text').trim()||'#e6edf3',bg:_s.getPropertyValue('--card-bg').trim()||'#161b22',bg2:_s.getPropertyValue('--bg').trim()||'#0d1117',bd:_s.getPropertyValue('--border').trim()||'#30363d'};

  // Queue color function — AMD red gradient, NVIDIA blue gradient, CPU green, other purple
  const AMD_GRADIENT = ['#ff6b6b','#ee5a5a','#da3633','#c92a2a','#b71c1c','#a51515','#8b0000','#ff8a80','#ef5350','#e53935','#d32f2f','#c62828'];
  const NV_GRADIENT = ['#64b5f6','#42a5f5','#2196f3','#1e88e5','#1976d2','#1565c0','#0d47a1','#82b1ff','#448aff','#2979ff','#2962ff','#1a237e'];
  const CPU_GRADIENT = ['#66bb6a','#4caf50','#43a047','#388e3c','#2e7d32','#1b5e20'];
  const OTHER_COLORS = ['#ab47bc','#9c27b0','#8e24aa','#7b1fa2','#6a1b9a','#4a148c','#ce93d8','#ba68c8'];

  let amdIdx=0, nvIdx=0, cpuIdx=0, otherIdx=0;
  function queueColor(queueName) {
    if (queueName.startsWith('amd_') || queueName.startsWith('amd-')) return AMD_GRADIENT[(amdIdx++) % AMD_GRADIENT.length];
    if (['gpu_1_queue','gpu_4_queue','B200','H200','a100_queue','mithril-h100-pool','nebius-h200','perf-B200','perf-h200'].includes(queueName)) return NV_GRADIENT[(nvIdx++) % NV_GRADIENT.length];
    if (queueName.includes('cpu') || queueName.includes('arm')) return CPU_GRADIENT[(cpuIdx++) % CPU_GRADIENT.length];
    return OTHER_COLORS[(otherIdx++) % OTHER_COLORS.length];
  }

  // Queue grouping — mi355B is split out because it's a testing fleet, so we
  // want to surface (and default-uncheck) it separately from production mi355.
  const Q_GROUPS = {
    'AMD MI250': q => q.startsWith('amd_mi250'),
    'AMD MI300': q => q.startsWith('amd_mi300'),
    'AMD MI325': q => q.startsWith('amd_mi325'),
    'AMD MI355B (testing)': q => q.startsWith('amd_mi355B') || q.startsWith('amd_mi355b'),
    'AMD MI355': q => q.startsWith('amd_mi355'),
    'NVIDIA GPU': q => ['gpu_1_queue','gpu_4_queue','B200','H200','a100_queue','mithril-h100-pool'].includes(q),
    'CPU': q => q.includes('cpu'),
    'Other': q => true,
  };
  const isMi355B = q => q.startsWith('amd_mi355B') || q.startsWith('amd_mi355b');
  const NVIDIA_QUEUES = ['gpu_1_queue','gpu_4_queue','B200','H200','a100_queue','mithril-h100-pool','nebius-h200','perf-B200','perf-h200'];

  const INTERVALS = [
    {label:'1h',hours:1},{label:'3h',hours:3},{label:'6h',hours:6},
    {label:'12h',hours:12},{label:'24h',hours:24},{label:'2d',hours:48},
    {label:'3d',hours:72},{label:'5d',hours:120},{label:'7d',hours:168},
    {label:'14d',hours:336},{label:'1m',hours:720},{label:'3m',hours:2160},
  ];

  const h = el;  // shared element factory defined in utils.js

  // ── Stuck-job kill flow (admin) ────────────────────────────────────
  // A job is "stuck" if it has been waiting in the queue longer than 4h.
  // The admin can click Kill → modal → paste Buildkite token → decrypt
  // the pre-committed proof sentinel → we call Buildkite's build-cancel
  // REST API with their token. Everything below except STUCK_MIN is
  // plumbing for that flow.
  const STUCK_MIN = 240;
  const AMD_TRIAGE_PREFIX = 'amd_';
  const KILL_AUTH_SALT = 'vllm-ci-dashboard|kill-auth';
  const KILL_AUTH_KDF_ITERATIONS = 200000;
  const KILL_AUTH_SENTINEL = '1';
  const BK_API = 'https://api.buildkite.com';

  function isAdminUser() {
    const gate = window.__authGate;
    return !!(gate && gate.isAdmin && gate.isAdmin());
  }
  function promptAdminSignIn() {
    const gate = window.__authGate;
    if (gate && gate.promptSignIn) gate.promptSignIn();
  }
  function _fmtMinutes(mins) {
    if (mins == null || !isFinite(mins)) return '\u2014';
    if (mins >= 1440) return (mins / 1440).toFixed(mins >= 2880 ? 0 : 1) + 'd';
    if (mins >= 60) return (mins / 60).toFixed(mins >= 600 ? 0 : 1) + 'h';
    return mins.toFixed(1) + 'm';
  }
  function _cancelCurl(org, pipeline, buildNumber) {
    return `curl -H "Authorization: Bearer $BUILDKITE_TOKEN" -X PUT "https://api.buildkite.com/v2/organizations/${org}/pipelines/${pipeline}/builds/${buildNumber}/cancel"`;
  }
  async function _copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (e) {
      return false;
    }
  }

  function _hexToBytes(hex) {
    const out = new Uint8Array(hex.length / 2);
    for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.substr(i*2, 2), 16);
    return out;
  }
  function _b64ToBytes(b64) {
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }

  // Pull the ``{org, pipeline, build}`` triple out of a job URL of the
  // shape produced by Buildkite (an /<org>/<pipeline>/builds/<n> path on
  // the buildkite host). Returns null for anything that doesn't fit the
  // pattern so the kill modal can error out cleanly instead of PUTing at
  // a wrong path.
  function _parseBkUrl(url) {
    if (!url) return null;
    const m = url.match(/^https:\/\/buildkite\.com\/([^/]+)\/([^/]+)\/builds\/(\d+)/);
    if (!m) return null;
    return { org: m[1], pipeline: m[2], build: parseInt(m[3], 10) };
  }

  // Derive the AES-GCM unwrap key from the admin's Buildkite token.
  // Must match scripts/vllm/encrypt_kill_auth.py exactly — salt bytes,
  // iteration count, hash, and derived-key length all contribute to the
  // key; any drift here makes every committed ciphertext undecryptable.
  async function _deriveKillAuthKey(bkToken) {
    const enc = new TextEncoder();
    const material = await crypto.subtle.importKey(
      'raw', enc.encode(bkToken),
      { name: 'PBKDF2' }, false, ['deriveKey']
    );
    return crypto.subtle.deriveKey(
      { name: 'PBKDF2',
        salt: enc.encode(KILL_AUTH_SALT),
        iterations: KILL_AUTH_KDF_ITERATIONS,
        hash: 'SHA-256' },
      material,
      { name: 'AES-GCM', length: 256 },
      false, ['decrypt']
    );
  }

  // Fetch the pre-committed ciphertext + decrypt it with the admin's
  // Buildkite token. Returns an {ok, reason} discriminated result:
  //
  //   {ok: true}                       — decrypt succeeded and plaintext is
  //                                      the sentinel "1", i.e. the token
  //                                      matches the one used during the
  //                                      offline encrypt_kill_auth.py run.
  //   {ok: false, reason: 'no-auth'}   — kill_auth.enc.json is not
  //                                      deployed; admin must run the CLI
  //                                      tool to seed it.
  //   {ok: false, reason: 'bad-token'} — decrypt threw (wrong token) or
  //                                      the plaintext isn't "1" (wrong
  //                                      token that happens to decrypt).
  //   {ok: false, reason: 'bad-envelope'} — record was malformed JSON.
  async function _verifyKillToken(bkToken) {
    let rec;
    try {
      const r = await fetch('data/vllm/ci/kill_auth.enc.json?_=' + Math.floor(Date.now()/1000));
      if (!r.ok) return { ok: false, reason: 'no-auth' };
      rec = await r.json();
    } catch (e) { return { ok: false, reason: 'no-auth' }; }
    if (!rec || rec.v !== 1 || !rec.iv || !rec.ct) {
      return { ok: false, reason: 'bad-envelope' };
    }
    try {
      const key = await _deriveKillAuthKey(bkToken);
      const pt = await crypto.subtle.decrypt(
        { name: 'AES-GCM', iv: _hexToBytes(rec.iv) },
        key, _b64ToBytes(rec.ct)
      );
      const plaintext = new TextDecoder('utf-8').decode(pt);
      if (plaintext === KILL_AUTH_SENTINEL) return { ok: true };
      return { ok: false, reason: 'bad-token' };
    } catch (e) { return { ok: false, reason: 'bad-token' }; }
  }

  // Fire the actual Buildkite build-cancel. Note: Buildkite's REST API
  // cancels the whole build (all jobs in it), not just the single job
  // the admin clicked on — the stuck-job modal communicates this so no
  // one is surprised. That's still the desired behavior here: a stuck
  // queued job means the build is backed up, and any sibling jobs are
  // just as blocked, so cancel-the-whole-build matches intent.
  async function _cancelBkBuild(token, org, pipeline, buildNumber) {
    const url = BK_API + '/v2/organizations/' + encodeURIComponent(org)
      + '/pipelines/' + encodeURIComponent(pipeline)
      + '/builds/' + buildNumber + '/cancel';
    const resp = await fetch(url, {
      method: 'PUT',
      headers: {
        'Authorization': 'Bearer ' + token,
        'Accept': 'application/json',
      },
    });
    if (!resp.ok) {
      let body = '';
      try { body = await resp.text(); } catch (_) {}
      throw new Error('HTTP ' + resp.status + ' — ' + (body.slice(0, 280) || resp.statusText));
    }
    return resp.json();
  }

  function renderLockedTriageSection(container, queuedCount, runningCount) {
    if (!queuedCount && !runningCount) return;
    const section = h('div',{style:{
      background:C.b+'10', border:`1px solid ${C.b}44`, borderRadius:'8px',
      padding:'14px 18px', marginBottom:'16px',
    }});
    const header = h('div',{style:{display:'flex',alignItems:'center',justifyContent:'space-between',gap:'12px',flexWrap:'wrap'}});
    header.append(h('div',{},[
      h('div',{text:'AMD Queue Triage',style:{fontSize:'15px',fontWeight:'700',color:C.t,marginBottom:'4px'}}),
      h('div',{text:`${queuedCount} queued > 4h, ${runningCount} running > 4h. Sign in as an admin to inspect and cancel builds.`,style:{fontSize:'13px',color:C.m}}),
    ]));
    const btn = h('button',{text:'Sign In',style:{background:C.b,border:'none',color:'#fff',padding:'6px 12px',borderRadius:'5px',cursor:'pointer',fontSize:'12px',fontWeight:'600',fontFamily:'inherit'}});
    btn.onclick = () => promptAdminSignIn();
    header.append(btn);
    section.append(header);
    container.append(section);
  }

  function makeQueueBadge(queue, qColorMap) {
    const qc = qColorMap[queue]||C.m;
    return h('span',{style:{display:'inline-flex',alignItems:'center',gap:'4px'}},[
      h('span',{style:{width:'6px',height:'6px',borderRadius:'50%',background:qc,display:'inline-block'}}),
      h('span',{text:queue||'?',style:{fontSize:'12px'}}),
    ]);
  }

  function renderTriageTable(title, jobs, kind, qColorMap) {
    const card = h('div',{style:{background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'8px',padding:'12px 14px'}});
    card.append(h('div',{style:{display:'flex',justifyContent:'space-between',alignItems:'center',gap:'8px',marginBottom:'10px',flexWrap:'wrap'}},[
      h('h4',{text:title,style:{margin:'0',fontSize:'14px',color:C.t}}),
      h('span',{text:jobs.length ? `${jobs.length} job${jobs.length===1?'':'s'}` : 'None',style:{fontSize:'12px',color:C.m}}),
    ]));
    if (!jobs.length) {
      card.append(h('p',{text:'No AMD jobs in this category right now.',style:{margin:'0',fontSize:'13px',color:C.m}}));
      return card;
    }

    const tbl = h('table',{style:{width:'100%',borderCollapse:'collapse',fontSize:'13px'}});
    const hdr = h('tr');
    for (const col of ['Job','Queue','Build','Age','Before Start','Review','Actions']) {
      hdr.append(h('th',{text:col,style:{textAlign:'left',padding:'6px 8px',borderBottom:`1px solid ${C.bd}`,color:C.m,fontSize:'12px'}}));
    }
    tbl.append(h('thead',{},[hdr]));
    const tb = h('tbody');
    for (const j of jobs) {
      const tr = h('tr',{style:{borderBottom:`1px solid ${C.bd}22`}});
      tr.append(h('td',{text:(j.name||'').slice(0,70),style:{padding:'6px 8px',wordBreak:'break-word'}}));
      tr.append(h('td',{style:{padding:'6px 8px'}},[makeQueueBadge(j.queue, qColorMap)]));
      tr.append(h('td',{text:(j.pipeline||'?')+' #'+(j.build||'?'),style:{padding:'6px 8px',color:C.m,fontSize:'12px',whiteSpace:'nowrap'}}));
      const age = kind === 'queued' ? j.wait_min : j.run_min;
      tr.append(h('td',{text:_fmtMinutes(age),style:{padding:'6px 8px',color:'#ff6b6b',fontWeight:'600',whiteSpace:'nowrap'}}));
      tr.append(h('td',{text:j.queue_wait_before_start_min!=null?_fmtMinutes(j.queue_wait_before_start_min):'\u2014',style:{padding:'6px 8px',color:C.m,whiteSpace:'nowrap'}}));
      const review = j.url
        ? h('a',{text:'BK \u2197',href:j.url,target:'_blank',style:{color:C.b,fontSize:'12px',textDecoration:'none',padding:'3px 8px',background:C.b+'15',borderRadius:'3px',border:`1px solid ${C.b}33`,whiteSpace:'nowrap'}})
        : h('span',{text:'\u2014',style:{color:C.m}});
      tr.append(h('td',{style:{padding:'6px 8px'}},[review]));
      const actions = h('div',{style:{display:'flex',gap:'6px',flexWrap:'wrap'}});
      const inspectBtn = h('button',{text:'Inspect',style:{background:C.bd,border:'none',color:C.t,padding:'3px 10px',borderRadius:'3px',cursor:'pointer',fontSize:'12px',fontWeight:'600',fontFamily:'inherit'}});
      inspectBtn.onclick = () => showKillModal(j);
      const curlBtn = h('button',{text:'Copy curl',style:{background:C.b,border:'none',color:'#fff',padding:'3px 10px',borderRadius:'3px',cursor:'pointer',fontSize:'12px',fontWeight:'600',fontFamily:'inherit'}});
      curlBtn.onclick = async () => {
        const parsed = j.url ? _parseBkUrl(j.url) : null;
        if (!parsed) return;
        const copied = await _copyText(_cancelCurl(parsed.org, parsed.pipeline, parsed.build));
        curlBtn.textContent = copied ? 'Copied' : 'Copy failed';
        setTimeout(() => { curlBtn.textContent = 'Copy curl'; }, 1200);
      };
      const parsed = j.url ? _parseBkUrl(j.url) : null;
      if (!parsed) {
        inspectBtn.disabled = true;
        curlBtn.disabled = true;
        inspectBtn.style.opacity = curlBtn.style.opacity = '0.4';
        inspectBtn.style.cursor = curlBtn.style.cursor = 'not-allowed';
      }
      actions.append(inspectBtn, curlBtn);
      tr.append(h('td',{style:{padding:'6px 8px'}},[actions]));
      tb.append(tr);
    }
    tbl.append(tb);
    card.append(tbl);
    return card;
  }

  function renderAmdTriageSection(container, queuedJobs, runningJobs, qColorMap) {
    const section = h('div', {style:{
      background:'rgba(218,54,51,0.08)', border:'1px solid rgba(218,54,51,0.6)',
      borderRadius:'8px', padding:'14px 18px', marginBottom:'16px',
    }});
    const hrs = (STUCK_MIN / 60).toFixed(0);
    const header = h('div',{style:{display:'flex',alignItems:'center',gap:'8px',marginBottom:'12px',flexWrap:'wrap'}});
    header.append(h('span',{text:'\u26A0',style:{fontSize:'18px',color:'#ff6b6b'}}));
    header.append(h('h3',{text:'AMD Queue Triage',style:{margin:'0',fontSize:'15px',color:'#ff6b6b'}}));
    header.append(h('span',{text:`queued or running > ${hrs}h`,style:{color:C.m,fontSize:'12px'}}));
    section.append(header);
    section.append(h('p',{text:'Inspect the job context first, then either copy the Buildkite cancel command or cancel the whole build after confirmation.',style:{margin:'0 0 12px 0',fontSize:'13px',color:C.m,lineHeight:'1.5'}}));
    const grid = h('div',{style:{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(320px,1fr))',gap:'12px'}});
    grid.append(
      renderTriageTable('Queued > 4h', queuedJobs, 'queued', qColorMap),
      renderTriageTable('Running > 4h', runningJobs, 'running', qColorMap),
    );
    section.append(grid);
    container.append(section);
  }

  // Three-step confirmation modal for killing a stuck build:
  //   1. Review link + masked token input → Verify
  //   2. "Type KILL to confirm" once the proof-of-possession check passes
  //   3. Success / error screen after the Buildkite PUT resolves
  //
  // The token is never stored past the modal's lifetime — it lives in the
  // ``token`` closure until the user closes the panel. We deliberately do
  // not read/write localStorage so a casual dashboard visitor can't pull
  // a cached admin token back out.
  async function showKillModal(job) {
    if (!isAdminUser()) {
      promptAdminSignIn();
      return;
    }
    const target = _parseBkUrl(job.url);
    if (!target) return;
    const curlCmd = _cancelCurl(target.org, target.pipeline, target.build);

    const backdrop = h('div',{style:{position:'fixed',inset:'0',background:'rgba(0,0,0,.7)',zIndex:'1001',display:'flex',justifyContent:'center',alignItems:'flex-start',paddingTop:'40px',overflow:'auto'}});
    backdrop.onclick = e => { if (e.target === backdrop) backdrop.remove(); };
    const panel = h('div',{style:{background:C.bg2||C.bg,border:'1px solid #da3633',borderRadius:'12px',width:'min(560px,90vw)',padding:'24px'}});

    const headerRow = (title, titleColor) => h('div',{style:{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:'12px'}},[
      h('h3',{text:title,style:{margin:'0',fontSize:'18px',color:titleColor}}),
      h('button',{text:'\u2715',onclick:()=>backdrop.remove(),style:{background:'none',border:'none',color:C.m,fontSize:'20px',cursor:'pointer',padding:'4px 8px'}}),
    ]);

    function renderStep1() {
      panel.innerHTML = '';
      panel.append(headerRow('Kill Stuck Build','#ff6b6b'));
      panel.append(h('p',{style:{margin:'0 0 12px 0',fontSize:'14px',color:C.t,lineHeight:'1.5'}},[
        h('span',{text:'This cancels the '}),
        h('strong',{text:'entire build'}),
        h('span',{text:' on Buildkite \u2014 every job in build #' + target.build + ' of '}),
        h('code',{text:target.org+'/'+target.pipeline,style:{background:C.bd,padding:'2px 6px',borderRadius:'3px',fontSize:'12px'}}),
        h('span',{text:', not just this one stuck job.'}),
      ]));
      panel.append(h('div',{style:{marginBottom:'14px',padding:'10px 12px',background:C.b+'15',border:`1px solid ${C.b}33`,borderRadius:'6px'}},[
        h('div',{text:'Review before killing',style:{fontSize:'11px',color:C.m,textTransform:'uppercase',letterSpacing:'.5px',marginBottom:'4px'}}),
        h('div',{text:job.name||'(unnamed job)',style:{fontSize:'13px',marginBottom:'6px',wordBreak:'break-word'}}),
        h('a',{text:'Open build #'+target.build+' on Buildkite \u2197',href:job.url,target:'_blank',style:{color:C.b,fontSize:'13px',textDecoration:'none',fontWeight:'600'}}),
      ]));
      const facts = h('table',{style:{width:'100%',borderCollapse:'collapse',fontSize:'12px',marginBottom:'12px'}});
      const factRows = [
        ['Queue', job.queue || '\u2014'],
        ['State', job.state || '\u2014'],
        ['Pipeline / build', target.pipeline + ' #' + target.build],
        ['Age', job.wait_min != null ? _fmtMinutes(job.wait_min) : _fmtMinutes(job.run_min)],
        ['Queued before start', job.queue_wait_before_start_min != null ? _fmtMinutes(job.queue_wait_before_start_min) : '\u2014'],
        ['Branch', job.branch || '\u2014'],
        ['Commit', job.commit || '\u2014'],
        ['Source', job.source || '\u2014'],
        ['Workload', job.workload || '\u2014'],
      ];
      for (const row of factRows) {
        facts.append(h('tr',{},[
          h('th',{text:row[0],style:{textAlign:'left',padding:'4px 6px',color:C.m,fontWeight:'600',borderBottom:`1px solid ${C.bd}55`,whiteSpace:'nowrap'}}),
          h('td',{text:row[1],style:{padding:'4px 6px',color:C.t,borderBottom:`1px solid ${C.bd}22`,wordBreak:'break-word'}}),
        ]));
      }
      panel.append(facts);
      panel.append(h('div',{style:{marginBottom:'12px'}},[
        h('div',{text:'Cancel command',style:{fontSize:'11px',color:C.m,textTransform:'uppercase',letterSpacing:'.5px',marginBottom:'4px'}}),
        h('pre',{text:curlCmd,style:{margin:'0',background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'6px',padding:'10px',fontSize:'12px',color:C.t,whiteSpace:'pre-wrap',wordBreak:'break-word'}}),
      ]));
      panel.append(h('label',{text:'Buildkite API token (needs write_builds scope):',style:{fontSize:'13px',color:C.t,display:'block',marginBottom:'4px'}}));
      const tokenInput = h('input',{type:'password',placeholder:'bkua_...',autocomplete:'off',style:{width:'100%',padding:'8px 10px',background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'4px',color:C.t,fontFamily:'monospace',fontSize:'13px',boxSizing:'border-box'}});
      panel.append(tokenInput);
      const errBox = h('div',{style:{marginTop:'8px',fontSize:'13px',color:'#ff6b6b',minHeight:'20px'}});
      panel.append(errBox);
      const actions = h('div',{style:{display:'flex',justifyContent:'flex-end',gap:'8px',marginTop:'14px'}});
      const cancelBtn = h('button',{text:'Cancel',onclick:()=>backdrop.remove(),style:{background:C.bd,border:'none',color:C.t,padding:'6px 14px',borderRadius:'4px',cursor:'pointer',fontSize:'13px',fontFamily:'inherit'}});
      const copyBtn = h('button',{text:'Copy curl',style:{background:C.bd,border:'1px solid '+C.b,color:C.t,padding:'6px 14px',borderRadius:'4px',cursor:'pointer',fontSize:'13px',fontFamily:'inherit'}});
      const verifyBtn = h('button',{text:'Verify token',style:{background:C.b,border:'none',color:'#fff',padding:'6px 14px',borderRadius:'4px',cursor:'pointer',fontSize:'13px',fontWeight:'600',fontFamily:'inherit'}});
      copyBtn.onclick = async () => {
        const copied = await _copyText(curlCmd);
        errBox.style.color = copied ? C.g : '#ff6b6b';
        errBox.textContent = copied ? 'Cancel command copied to clipboard.' : 'Could not copy the cancel command.';
      };
      const runVerify = async () => {
        errBox.textContent = '';
        errBox.style.color = '#ff6b6b';
        const tok = tokenInput.value.trim();
        if (!tok) { errBox.textContent = 'Enter a Buildkite token.'; return; }
        verifyBtn.disabled = true; verifyBtn.textContent = 'Verifying...';
        const res = await _verifyKillToken(tok);
        verifyBtn.disabled = false; verifyBtn.textContent = 'Verify token';
        if (!res.ok) {
          if (res.reason === 'no-auth') errBox.textContent = 'kill_auth.enc.json is missing \u2014 run scripts/vllm/encrypt_kill_auth.py to seed the proof first.';
          else if (res.reason === 'bad-envelope') errBox.textContent = 'kill_auth.enc.json is malformed; re-run encrypt_kill_auth.py.';
          else errBox.textContent = 'Token did not decrypt the authorization proof. Use the token that was encrypted by the admin.';
          return;
        }
        renderStep2(tok);
      };
      verifyBtn.onclick = runVerify;
      tokenInput.onkeydown = e => { if (e.key === 'Enter') runVerify(); };
      actions.append(cancelBtn, copyBtn, verifyBtn);
      panel.append(actions);
      setTimeout(() => tokenInput.focus(), 10);
    }

    function renderStep2(token) {
      panel.innerHTML = '';
      panel.append(headerRow('Confirm kill','#ff6b6b'));
      panel.append(h('div',{style:{padding:'10px 12px',background:C.g+'15',border:`1px solid ${C.g}44`,borderRadius:'6px',marginBottom:'12px',fontSize:'13px',color:C.g}},[
        h('span',{text:'\u2713 Token authorized.'}),
      ]));
      panel.append(h('p',{style:{margin:'0 0 8px 0',fontSize:'14px',color:C.t,lineHeight:'1.5'}},[
        h('span',{text:'About to cancel '}),
        h('strong',{text:'build #'+target.build}),
        h('span',{text:' in '+target.org+'/'+target.pipeline+'. Type '}),
        h('code',{text:'KILL',style:{background:C.bd,padding:'2px 6px',borderRadius:'3px',color:'#ff6b6b',fontWeight:'700'}}),
        h('span',{text:' below to confirm.'}),
      ]));
      const confirmInput = h('input',{type:'text',placeholder:'Type KILL',autocomplete:'off',style:{width:'100%',padding:'8px 10px',background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'4px',color:C.t,fontFamily:'monospace',fontSize:'13px',boxSizing:'border-box'}});
      panel.append(confirmInput);
      const errBox = h('div',{style:{marginTop:'8px',fontSize:'13px',color:'#ff6b6b',minHeight:'20px'}});
      panel.append(errBox);
      const actions = h('div',{style:{display:'flex',justifyContent:'flex-end',gap:'8px',marginTop:'14px'}});
      const backBtn = h('button',{text:'Back',onclick:renderStep1,style:{background:C.bd,border:'none',color:C.t,padding:'6px 14px',borderRadius:'4px',cursor:'pointer',fontSize:'13px',fontFamily:'inherit'}});
      const killBtn = h('button',{text:'Kill build #'+target.build,style:{background:'#da3633',border:'none',color:'#fff',padding:'6px 14px',borderRadius:'4px',cursor:'pointer',fontSize:'13px',fontWeight:'700',fontFamily:'inherit'}});
      const fire = async () => {
        errBox.textContent = '';
        if (confirmInput.value !== 'KILL') { errBox.textContent = 'Type KILL exactly (uppercase) to confirm.'; return; }
        killBtn.disabled = true; killBtn.textContent = 'Cancelling...';
        try {
          await _cancelBkBuild(token, target.org, target.pipeline, target.build);
          renderStep3({ok:true});
        } catch (e) {
          renderStep3({ok:false, err: (e && e.message) || String(e)});
        }
      };
      killBtn.onclick = fire;
      confirmInput.onkeydown = e => { if (e.key === 'Enter') fire(); };
      actions.append(backBtn, killBtn);
      panel.append(actions);
      setTimeout(() => confirmInput.focus(), 10);
    }

    function renderStep3(res) {
      panel.innerHTML = '';
      panel.append(headerRow(res.ok?'Build cancelled':'Cancel failed', res.ok?C.g:'#ff6b6b'));
      if (res.ok) {
        panel.append(h('p',{text:'Buildkite accepted the cancel for build #'+target.build+'. It can take a few seconds for the queue numbers to update.',style:{margin:'0 0 10px 0',fontSize:'14px',color:C.t,lineHeight:'1.5'}}));
        panel.append(h('a',{text:'Open build #'+target.build+' \u2197',href:job.url,target:'_blank',style:{color:C.b,fontSize:'13px',textDecoration:'none'}}));
      } else {
        panel.append(h('p',{text:'Buildkite rejected the cancel request. Response:',style:{margin:'0 0 6px 0',fontSize:'14px',color:C.t}}));
        panel.append(h('pre',{text:res.err,style:{background:C.bg,border:`1px solid ${C.bd}`,padding:'8px 10px',borderRadius:'4px',fontSize:'12px',color:'#ff6b6b',overflow:'auto',margin:'0',whiteSpace:'pre-wrap',wordBreak:'break-word'}}));
      }
      const actions = h('div',{style:{display:'flex',justifyContent:'flex-end',gap:'8px',marginTop:'14px'}});
      const closeBtn = h('button',{text:'Close',onclick:()=>backdrop.remove(),style:{background:C.bd,border:'none',color:C.t,padding:'6px 14px',borderRadius:'4px',cursor:'pointer',fontSize:'13px',fontFamily:'inherit'}});
      actions.append(closeBtn);
      panel.append(actions);
    }

    renderStep1();
    backdrop.append(panel);
    document.body.append(backdrop);
  }

  async function loadTimeseries() {
    try {
      const resp = await fetch('data/vllm/ci/queue_timeseries.jsonl?_='+Math.floor(Date.now()/1000));
      if (!resp.ok) return [];
      const text = await resp.text();
      var lines=text.trim().split('\n').filter(function(l){return l&&l.charAt(0)===String.fromCharCode(123)});
      return lines.map(function(l){try{return JSON.parse(l)}catch(e){return null}}).filter(function(s){return s&&s.ts&&s.queues&&typeof s.queues==='object'});
    } catch(e) { return []; }
  }

  async function render() {
    const container = document.getElementById('ci-queue-view');
    if (!container) return;
    container.innerHTML = '<p style="color:#8b949e">Loading queue data...</p>';

    const snapshots = await loadTimeseries();
    if (!snapshots.length) {
      container.innerHTML = '<p style="color:#8b949e">No queue data yet. The hourly monitor will start collecting data automatically.</p>';
      return;
    }
    container.innerHTML = '';

    // Extract all queue names
    const allQueues = new Set();
    for (const snap of snapshots) {
      for (const q of Object.keys(snap.queues || {})) allQueues.add(q);
    }
    const queueList = [...allQueues].sort();

    // State — default to AMD-only (no NVIDIA) and drop mi355B (testing fleet).
    let selectedQueues = new Set(queueList.filter(q => q.startsWith('amd_') && !isMi355B(q)));
    let intervalHours = 72; // default 3 days
    let metric = 'running'; // or 'waiting'
    let chart = null;

    // Title (project selector removed — handled by sidebar)
    container.append(h('h2',{text:'Queue Monitor',style:{marginBottom:'16px'}}));

    await renderQueueContent(container, snapshots);
  }

  async function renderQueueContent(container, snapshots) {
    const allQueues = new Set();
    for (const snap of snapshots) {
      for (const q of Object.keys(snap.queues || {})) allQueues.add(q);
    }
    const queueList = [...allQueues].sort();

    // Pre-compute colors per queue (reset gradient indices)
    amdIdx=0; nvIdx=0; cpuIdx=0; otherIdx=0;
    const qColorMap = {};
    for (const q of queueList) qColorMap[q] = queueColor(q);

    let selectedQueues = new Set(queueList.filter(q => q.startsWith('amd_') && !isMi355B(q)));
    let intervalHours = 168;
    let metric = 'running';
    let chart = null;
    // Scrub spike-like outlier points at render time. A point is treated as a
    // spike if it's both (a) > SPIKE_ABS_MIN and (b) > SPIKE_RATIO × the
    // local median of a ±SPIKE_WINDOW snapshot window. Replaced with the
    // local median so the line stays continuous without fake peaks.
    let spikeFilterOn = true;
    let workloadSplit = 'all'; // 'all' | 'vllm' | 'omni'
    const SPIKE_WINDOW = 3;
    const SPIKE_RATIO = 4;
    const SPIKE_ABS_MIN = 5;

    function median(xs) {
      if (!xs.length) return 0;
      const s = [...xs].sort((a,b)=>a-b);
      const m = Math.floor(s.length/2);
      return s.length%2 ? s[m] : (s[m-1]+s[m])/2;
    }

    function scrubSpikes(values) {
      if (!spikeFilterOn || values.length < 2*SPIKE_WINDOW+1) return values;
      const out = values.slice();
      for (let i=0;i<values.length;i++) {
        const v = values[i];
        if (v == null || v < SPIKE_ABS_MIN) continue;
        const lo = Math.max(0, i-SPIKE_WINDOW);
        const hi = Math.min(values.length, i+SPIKE_WINDOW+1);
        const ctx = [];
        for (let j=lo;j<hi;j++) if (j!==i && values[j]!=null) ctx.push(values[j]);
        if (ctx.length < 2) continue;
        const med = median(ctx);
        if (med >= SPIKE_ABS_MIN/2 && v > SPIKE_RATIO * Math.max(med, 1)) {
          out[i] = med;
        }
      }
      return out;
    }

    function queueValue(qd, key) {
      // Apply workload split if present on the snapshot entry; fall back to
      // the plain key when no breakdown is available.
      if (!qd) return null;
      if (workloadSplit === 'all') return qd[key];
      if (key === 'waiting' || key === 'running') {
        const bk = key + '_by_workload';
        const split = qd[bk];
        if (split && typeof split === 'object') return split[workloadSplit] || 0;
        return workloadSplit === 'vllm' ? (qd[key] || 0) : 0;
      }
      return qd[key];
    }

    const DEFAULT_WAIT_METRIC = 'p90_wait';
    function queueWaitValue(qd, key) {
      if (!qd) return 0;
      if (qd[key] != null) return qd[key];
      return 0;
    }

    // Current snapshot summary — clickable cards with overlays
    const latest = snapshots[snapshots.length - 1];
    const latestQueues = latest.queues || {};
    const BK_QUEUES_URL = typeof LinkRegistry.bk.queues === 'function' ? LinkRegistry.bk.queues() : LinkRegistry.bk.queues;

    function showQueueOverlay(title, color, filterFn, sortKey) {
      const sk = sortKey || 'waiting';
      const entries = Object.entries(latestQueues)
        .map(([name, d]) => ({name, ...d}))
        .filter(filterFn)
        .sort((a, b) => (b[sk]||0) - (a[sk]||0));

      const backdrop = h('div',{style:{position:'fixed',inset:'0',background:'rgba(0,0,0,.6)',zIndex:'1000',display:'flex',justifyContent:'center',alignItems:'flex-start',paddingTop:'40px',overflow:'auto'}});
      backdrop.onclick = e => { if (e.target === backdrop) backdrop.remove(); };
      const panel = h('div',{style:{background:C.bg2||C.bg,border:`1px solid ${C.bd}`,borderRadius:'12px',width:'min(700px,90vw)',maxHeight:'85vh',overflow:'auto',padding:'24px'}});
      panel.append(h('div',{style:{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:'16px'}},[
        h('h3',{text:`${title} (${entries.length} queues)`,style:{margin:'0',fontSize:'18px'}}),
        h('button',{text:'\u2715',onclick:()=>backdrop.remove(),style:{background:'none',border:'none',color:C.m,fontSize:'20px',cursor:'pointer',padding:'4px 8px'}})
      ]));
      // Link to Buildkite queues master page
      panel.append(h('div',{style:{marginBottom:'16px'}},[
        h('a',{text:'View all queues on Buildkite \u2192',href:BK_QUEUES_URL,target:'_blank',style:{color:C.b,fontSize:'13px',textDecoration:'none'}})
      ]));
      const tbl = h('table',{style:{width:'100%',borderCollapse:'collapse',fontSize:'14px'}});
      tbl.append(h('thead',{},[h('tr',{},[
        h('th',{text:'Queue',style:{textAlign:'left',padding:'8px',borderBottom:`1px solid ${C.bd}`,color:C.m}}),
        h('th',{text:'Agents',style:{textAlign:'center',padding:'8px',borderBottom:`1px solid ${C.bd}`,color:C.m}}),
        h('th',{text:'Waiting',style:{textAlign:'center',padding:'8px',borderBottom:`1px solid ${C.bd}`,color:C.m}}),
        h('th',{text:'Running',style:{textAlign:'center',padding:'8px',borderBottom:`1px solid ${C.bd}`,color:C.m}}),
        h('th',{text:'Avg Wait',style:{textAlign:'center',padding:'8px',borderBottom:`1px solid ${C.bd}`,color:C.m}}),
        h('th',{text:'Max Wait',style:{textAlign:'center',padding:'8px',borderBottom:`1px solid ${C.bd}`,color:C.m}}),
      ])]));
      const tb = h('tbody');
      for (const q of entries) {
        const tr = h('tr',{style:{borderBottom:`1px solid ${C.bd}`}});
        const qc = qColorMap[q.name] || C.m;
        const queueLabel = q.queue_url
          ? h('a',{text:q.name,href:q.queue_url,target:'_blank',style:{fontWeight:'600',color:C.b,textDecoration:'none'}})
          : h('span',{text:q.name,style:{fontWeight:'600'}});
        tr.append(h('td',{style:{padding:'8px'}},[
          h('span',{style:{width:'8px',height:'8px',borderRadius:'50%',background:qc,display:'inline-block',marginRight:'6px'}}),
          queueLabel
        ]));
        tr.append(h('td',{text:q.connected_agents!=null?String(q.connected_agents):'\u2014',style:{textAlign:'center',padding:'8px',color:C.m}}));
        tr.append(h('td',{text:String(q.waiting||0),style:{textAlign:'center',padding:'8px',color:q.waiting>0?C.r:C.m,fontWeight:q.waiting>0?'600':'400'}}));
        tr.append(h('td',{text:String(q.running||0),style:{textAlign:'center',padding:'8px',color:q.running>0?C.g:C.m,fontWeight:q.running>0?'600':'400'}}));
        tr.append(h('td',{text:q.avg_wait!=null?q.avg_wait.toFixed(1)+'m':'\u2014',style:{textAlign:'center',padding:'8px',color:C.m}}));
        tr.append(h('td',{text:q.max_wait!=null?q.max_wait.toFixed(1)+'m':'\u2014',style:{textAlign:'center',padding:'8px',color:q.max_wait>30?C.r:C.m}}));
        tb.append(tr);
      }
      tbl.append(tb);
      panel.append(tbl);
      backdrop.append(panel);
      document.body.append(backdrop);
    }

    function makeClickableCard(label, value, sub, color, onclick) {
      const card = h('div',{style:{background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'8px',padding:'16px 20px',borderTop:`3px solid ${color}`,cursor:'pointer',transition:'transform .15s,box-shadow .15s'}},[
        h('div',{text:label,style:{fontSize:'13px',color:C.m,textTransform:'uppercase',letterSpacing:'.5px',marginBottom:'4px'}}),
        h('div',{text:String(value),style:{fontSize:'28px',fontWeight:'800',color,lineHeight:'1.1'}}),
        sub?h('div',{text:sub,style:{fontSize:'14px',color:C.m,marginTop:'4px'}}):null,
      ]);
      card.onmouseenter = () => { card.style.transform='translateY(-2px)'; card.style.boxShadow='0 4px 12px rgba(0,0,0,.3)'; };
      card.onmouseleave = () => { card.style.transform=''; card.style.boxShadow=''; };
      card.onclick = onclick;
      return card;
    }

    // Load per-job data (latest snapshot jobs)
    let jobsData = null;
    try {
      const r = await fetch('data/vllm/ci/queue_jobs.json?_='+Math.floor(Date.now()/1000));
      if (r.ok) jobsData = await r.json();
    } catch(e) { /* queue_jobs.json not available yet */ }
    const pendingJobs = jobsData?.pending || [];
    const runningJobs = jobsData?.running || [];

    // Split queues into AMD / NVIDIA
    const isAmd = q => q.startsWith('amd_') || q === 'amd-cpu';
    const amdQueues = Object.entries(latestQueues).filter(([q])=>isAmd(q));
    const nvQueues = Object.entries(latestQueues).filter(([q])=>!isAmd(q));
    const amdWaiting = amdQueues.reduce((s,[,d])=>s+(d.waiting||0),0);
    const amdRunning = amdQueues.reduce((s,[,d])=>s+(d.running||0),0);
    const nvWaiting = nvQueues.reduce((s,[,d])=>s+(d.waiting||0),0);
    const nvRunning = nvQueues.reduce((s,[,d])=>s+(d.running||0),0);

    function showJobOverlay(title, jobs, color) {
      const backdrop = h('div',{style:{position:'fixed',inset:'0',background:'rgba(0,0,0,.6)',zIndex:'1000',display:'flex',justifyContent:'center',alignItems:'flex-start',paddingTop:'40px',overflow:'auto'}});
      backdrop.onclick = e => { if(e.target===backdrop) backdrop.remove(); };
      const panel = h('div',{style:{background:C.bg2||C.bg,border:`1px solid ${C.bd}`,borderRadius:'12px',width:'min(800px,90vw)',maxHeight:'85vh',overflow:'auto',padding:'24px'}});
      panel.append(h('div',{style:{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:'16px'}},[
        h('h3',{text:`${title} (${jobs.length})`,style:{margin:'0',fontSize:'18px'}}),
        h('button',{text:'\u2715',onclick:()=>backdrop.remove(),style:{background:'none',border:'none',color:C.m,fontSize:'20px',cursor:'pointer',padding:'4px 8px'}})
      ]));
      if(!jobs.length){ panel.append(h('p',{text:'No jobs.',style:{color:C.m}})); }
      else {
        const tbl=h('table',{style:{width:'100%',borderCollapse:'collapse',fontSize:'13px'}});
        const hdr=h('tr');
        for(const col of ['#','Job','Queue','Build',title.includes('Waiting')?'Wait':'','Link'])
          hdr.append(h('th',{text:col,style:{textAlign:col==='#'?'center':'left',padding:'6px 8px',borderBottom:`1px solid ${C.bd}`,color:C.m,fontSize:'12px'}}));
        tbl.append(h('thead',{},[hdr]));
        const tb=h('tbody');
        for(let i=0;i<jobs.length;i++){
          const j=jobs[i];
          const tr=h('tr',{style:{borderBottom:`1px solid ${C.bd}22`}});
          tr.append(h('td',{text:String(i+1),style:{textAlign:'center',padding:'4px 8px',color:C.m,fontSize:'12px'}}));
          tr.append(h('td',{text:(j.name||'').slice(0,60),style:{padding:'4px 8px',wordBreak:'break-word'}}));
          const qc=qColorMap[j.queue]||C.m;
          tr.append(h('td',{style:{padding:'4px 8px'}},[h('span',{style:{width:'6px',height:'6px',borderRadius:'50%',background:qc,display:'inline-block',marginRight:'4px'}}),h('span',{text:j.queue||'?',style:{fontSize:'12px'}})]));
          tr.append(h('td',{text:'#'+(j.build||'?'),style:{padding:'4px 8px',color:C.m,fontSize:'12px'}}));
          if(title.includes('Waiting')) tr.append(h('td',{text:j.wait_min!=null?j.wait_min+'m':'',style:{padding:'4px 8px',color:j.wait_min>30?C.r:C.m,fontSize:'12px'}}));
          else tr.append(h('td',{text:'',style:{padding:'4px 8px'}}));
          const link=j.url?h('a',{text:'BK',href:j.url,target:'_blank',style:{color:C.b,fontSize:'11px',textDecoration:'none',padding:'2px 6px',background:C.b+'15',borderRadius:'3px',border:`1px solid ${C.b}33`}}):h('span',{text:'\u2014',style:{color:C.m}});
          tr.append(h('td',{style:{padding:'4px 8px'}},[ link ]));
          tb.append(tr);
        }
        tbl.append(tb);
        panel.append(tbl);
      }
      backdrop.append(panel);
      document.body.append(backdrop);
    }

    const amdQueuedJobs = pendingJobs
      .filter(j => (j.queue || '').startsWith(AMD_TRIAGE_PREFIX) && (j.wait_min || 0) > STUCK_MIN)
      .sort((a, b) => (b.wait_min||0) - (a.wait_min||0));
    const amdRunningLongJobs = runningJobs
      .filter(j => (j.queue || '').startsWith(AMD_TRIAGE_PREFIX) && (j.run_min || 0) > STUCK_MIN)
      .sort((a, b) => (b.run_min||0) - (a.run_min||0));
    if (isAdminUser()) {
      renderAmdTriageSection(container, amdQueuedJobs, amdRunningLongJobs, qColorMap);
    } else {
      renderLockedTriageSection(container, amdQueuedJobs.length, amdRunningLongJobs.length);
    }

    // AMD row
    const amdLabel = h('div',{text:'AMD Queues',style:{fontSize:'13px',fontWeight:'700',color:'#da3633',marginBottom:'6px',textTransform:'uppercase',letterSpacing:'.5px'}});
    container.append(amdLabel);
    const amdRow = h('div',{style:{display:'grid',gridTemplateColumns:'repeat(3,1fr)',gap:'12px',marginBottom:'16px'}});
    amdRow.append(makeClickableCard('Waiting', amdWaiting, '', C.r,
      () => showJobOverlay('AMD Waiting Jobs', pendingJobs.filter(j=>isAmd(j.queue)), C.r)));
    amdRow.append(makeClickableCard('Running', amdRunning, '', C.g,
      () => showJobOverlay('AMD Running Jobs', runningJobs.filter(j=>isAmd(j.queue)), C.g)));
    amdRow.append(makeClickableCard('Active Queues', amdQueues.length, '', C.b,
      () => showQueueOverlay('AMD Active Queues', C.b, q => isAmd(q.name), 'total')));
    container.append(amdRow);

    // NVIDIA row
    const nvLabel = h('div',{text:'NVIDIA Queues',style:{fontSize:'13px',fontWeight:'700',color:'#1f6feb',marginBottom:'6px',textTransform:'uppercase',letterSpacing:'.5px'}});
    container.append(nvLabel);
    const nvRow = h('div',{style:{display:'grid',gridTemplateColumns:'repeat(3,1fr)',gap:'12px',marginBottom:'16px'}});
    nvRow.append(makeClickableCard('Waiting', nvWaiting, '', C.r,
      () => showJobOverlay('NVIDIA Waiting Jobs', pendingJobs.filter(j=>!isAmd(j.queue)), C.r)));
    nvRow.append(makeClickableCard('Running', nvRunning, '', C.g,
      () => showJobOverlay('NVIDIA Running Jobs', runningJobs.filter(j=>!isAmd(j.queue)), C.g)));
    nvRow.append(makeClickableCard('Active Queues', nvQueues.length, '', C.b,
      () => showQueueOverlay('NVIDIA Active Queues', C.b, q => !isAmd(q.name), 'total')));
    container.append(nvRow);

    // Snapshots row
    const REPO_URL = LinkRegistry.github.repo('AndreasKaratzas/vllm-ci-dashboard');
    const snapRow = h('div',{style:{display:'grid',gridTemplateColumns:'1fr',gap:'12px',marginBottom:'20px'}});
    snapRow.append(makeClickableCard('Snapshots', snapshots.length, `Since ${snapshots[0]?.ts?.slice(0,16)||'?'}`, C.m, () => {
      const backdrop = h('div',{style:{position:'fixed',inset:'0',background:'rgba(0,0,0,.6)',zIndex:'1000',display:'flex',justifyContent:'center',alignItems:'flex-start',paddingTop:'40px',overflow:'auto'}});
      backdrop.onclick = e => { if(e.target===backdrop) backdrop.remove(); };
      const panel = h('div',{style:{background:C.bg2||C.bg,border:`1px solid ${C.bd}`,borderRadius:'12px',width:'min(600px,90vw)',maxHeight:'85vh',overflow:'auto',padding:'24px'}});
      panel.append(h('div',{style:{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:'16px'}},[
        h('h3',{text:`Snapshots (${snapshots.length})`,style:{margin:'0',fontSize:'18px'}}),
        h('button',{text:'\u2715',onclick:()=>backdrop.remove(),style:{background:'none',border:'none',color:C.m,fontSize:'20px',cursor:'pointer',padding:'4px 8px'}})
      ]));
      panel.append(h('div',{style:{marginBottom:'16px'}},[
        h('a',{text:'View all runs \u2192',href:REPO_URL+'/actions/workflows/hourly-master.yml',target:'_blank',style:{color:C.b,fontSize:'13px',textDecoration:'none'}})
      ]));
      const list = h('div',{style:{display:'flex',flexDirection:'column',gap:'2px'}});
      const reversed = [...snapshots].reverse();
      for (let i = 0; i < reversed.length; i++) {
        const s = reversed[i];
        const d = new Date(s.ts||'');
        const dateStr = d.toLocaleDateString('en-US',{month:'short',day:'numeric'});
        const timeStr = d.toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit',hour12:false});
        const row = h('div',{style:{display:'flex',justifyContent:'space-between',alignItems:'center',padding:'6px 10px',borderRadius:'4px',background:i%2===0?(C.bd+'22'):'transparent',fontSize:'13px'}});
        row.append(h('span',{text:`#${snapshots.length - i}`,style:{color:C.m,width:'40px',flexShrink:'0'}}));
        row.append(h('span',{text:`${dateStr}, ${timeStr}`,style:{flex:'1',fontWeight:'500'}}));
        row.append(h('span',{text:`W:${s.total_waiting||0} R:${s.total_running||0}`,style:{color:C.m,marginRight:'8px',fontSize:'12px'}}));
        // Link to specific GitHub Actions run if run_id is available
        if(s.run_id){
          row.append(h('a',{text:'run',href:REPO_URL+'/actions/runs/'+s.run_id,target:'_blank',style:{color:C.b,fontSize:'12px',textDecoration:'none',padding:'2px 8px',background:C.b+'15',borderRadius:'3px',border:`1px solid ${C.b}33`}}));
        } else {
          row.append(h('span',{text:'\u2014',style:{color:C.m,fontSize:'12px'}}));
        }
        list.append(row);
      }
      panel.append(list);
      backdrop.append(panel);
      document.body.append(backdrop);
    }));
    container.append(snapRow);

    // Controls: interval selector + metric toggle
    const controlsRow = h('div',{style:{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:'16px',flexWrap:'wrap',gap:'8px'}});

    // Helper: count how many snapshots fall within an interval (relative to last snapshot)
    function snapshotsInInterval(hours) {
      const lastTs = new Date(snapshots[snapshots.length - 1].ts).getTime();
      const cutoff = new Date(lastTs - hours * 3600000);
      return snapshots.filter(s => new Date(s.ts) >= cutoff).length;
    }

    // Auto-select default interval: 3 days, or largest available if less data
    var best = INTERVALS.filter(iv => snapshotsInInterval(iv.hours) >= 2 && iv.hours <= 72).pop();
    if (best) intervalHours = best.hours;

    // Interval selector
    const intervalBar = h('div',{style:{display:'flex',gap:'2px',flexWrap:'wrap'}});
    intervalBar.append(h('span',{text:'Interval:',style:{color:C.m,fontSize:'14px',marginRight:'4px',alignSelf:'center'}}));
    for (const iv of INTERVALS) {
      const hasData = snapshotsInInterval(iv.hours) >= 2;
      const btn = h('button',{text:iv.label,style:{
        background:iv.hours===intervalHours?C.b:C.bd, border:'none', color:hasData?C.t:C.m+'66',
        padding:'4px 10px', borderRadius:'3px', cursor:hasData?'pointer':'not-allowed',
        fontSize:'13px', fontFamily:'inherit', opacity:hasData?'1':'0.4'
      }});
      if (hasData) {
        btn.onclick = () => {
          intervalHours = iv.hours;
          intervalBar.querySelectorAll('button').forEach(b=>{if(b.style.opacity!=='0.4')b.style.background=C.bd});
          btn.style.background = C.b;
          updateChart();
        };
      }
      intervalBar.append(btn);
    }
    controlsRow.append(intervalBar);

    // Metric toggle
    const metricBar = h('div',{style:{display:'flex',gap:'2px'}});
    const waitBtn = h('button',{text:'Waiting',style:{background:C.bd,border:'none',color:C.t,padding:'4px 12px',borderRadius:'3px',cursor:'pointer',fontSize:'13px',fontFamily:'inherit'}});
    const runBtn = h('button',{text:'Running',style:{background:C.g,border:'none',color:C.t,padding:'4px 12px',borderRadius:'3px',cursor:'pointer',fontSize:'13px',fontFamily:'inherit',fontWeight:'600'}});
    waitBtn.onclick = () => { metric='waiting'; waitBtn.style.background=C.r; waitBtn.style.fontWeight='600'; runBtn.style.background=C.bd; runBtn.style.fontWeight='400'; updateChart(); };
    runBtn.onclick = () => { metric='running'; runBtn.style.background=C.g; runBtn.style.fontWeight='600'; waitBtn.style.background=C.bd; waitBtn.style.fontWeight='400'; updateChart(); };
    metricBar.append(waitBtn, runBtn);
    controlsRow.append(metricBar);

    // Data-quality + workload controls
    const qualityRow = h('div',{style:{display:'flex',gap:'12px',flexWrap:'wrap',alignItems:'center',marginLeft:'auto'}});
    const spikeLabel = h('label',{style:{display:'inline-flex',alignItems:'center',gap:'6px',cursor:'pointer',fontSize:'13px',color:C.m}});
    const spikeCb = h('input',{type:'checkbox',style:{cursor:'pointer'}});
    spikeCb.checked = spikeFilterOn;
    spikeCb.onchange = () => { spikeFilterOn = spikeCb.checked; updateChart(); };
    spikeLabel.append(spikeCb, h('span',{text:'Scrub spikes',style:{color:C.t}}));
    spikeLabel.title = 'Replaces isolated outlier points (≥4× the local median) with the local median so legitimate gaps are preserved but transient spikes are flattened.';
    qualityRow.append(spikeLabel);

    const workloadBar = h('div',{style:{display:'flex',gap:'2px'}});
    workloadBar.append(h('span',{text:'Workload:',style:{color:C.m,fontSize:'13px',marginRight:'4px',alignSelf:'center'}}));
    const wlBtns = {};
    for (const w of [{k:'all',label:'All'},{k:'vllm',label:'vLLM'},{k:'omni',label:'Omni'}]) {
      const btn = h('button',{text:w.label,style:{
        background:w.k===workloadSplit?C.p:C.bd, border:'none', color:C.t,
        padding:'4px 10px', borderRadius:'3px', cursor:'pointer',
        fontSize:'13px', fontFamily:'inherit', fontWeight:w.k===workloadSplit?'600':'400',
      }});
      btn.onclick = () => {
        workloadSplit = w.k;
        for (const [k,b] of Object.entries(wlBtns)) { b.style.background=C.bd; b.style.fontWeight='400'; }
        btn.style.background = C.p; btn.style.fontWeight = '600';
        updateChart();
      };
      wlBtns[w.k] = btn;
      workloadBar.append(btn);
    }
    qualityRow.append(workloadBar);
    controlsRow.append(qualityRow);

    container.append(controlsRow);

    // Data availability info (updated dynamically in updateChart)
    const infoBanner = h('div',{style:{padding:'8px 14px',background:C.b+'15',border:`1px solid ${C.b}33`,borderRadius:'6px',marginBottom:'12px',fontSize:'13px',color:C.t}});
    container.append(infoBanner);

    // Busy Queues leaderboard — aggregates over the currently selected interval.
    const busySection = h('div',{style:{background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'8px',padding:'16px 20px',marginBottom:'12px'}});
    const busyHeader = h('div',{style:{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:'8px',gap:'8px',flexWrap:'wrap'}});
    busyHeader.append(h('h3',{text:'Busiest Queues',style:{fontSize:'15px'}}));
    const busyControls = h('div',{style:{display:'flex',gap:'10px',flexWrap:'wrap',alignItems:'center'}});
    const busySortBar = h('div',{style:{display:'flex',gap:'2px'}});
    const BUSY_SORTS = [
      {k:'busy_score',label:'Busy score'},
      {k:'peak_wait',label:'Peak wait'},
      {k:'peak_waiting',label:'Peak backlog'},
      {k:'active_share',label:'Active time'},
    ];
    let busySort = 'busy_score';
    const busySortBtns = {};
    for (const s of BUSY_SORTS) {
      const btn = h('button',{text:s.label,style:{
        background:s.k===busySort?C.b:C.bd, border:'none', color:C.t,
        padding:'4px 10px', borderRadius:'3px', cursor:'pointer',
        fontSize:'12px', fontFamily:'inherit', fontWeight:s.k===busySort?'600':'400',
      }});
      btn.onclick = () => {
        busySort = s.k;
        for (const [k,b] of Object.entries(busySortBtns)) { b.style.background=C.bd; b.style.fontWeight='400'; }
        btn.style.background = C.b; btn.style.fontWeight = '600';
        updateChart();
      };
      busySortBtns[s.k] = btn;
      busySortBar.append(btn);
    }
    busyControls.append(busySortBar);

    // Metric selector: which wait statistic the Peak/Avg columns reflect.
    // "Latest" pulls from the most recent snapshot in the interval rather than
    // aggregating, giving a current-state view instead of a historical one.
    const BUSY_METRICS = [
      {k:'latest',label:'Latest'},
      {k:'p50_wait',label:'p50'},
      {k:'p90_wait',label:'p90'},
      {k:'p99_wait',label:'p99'},
    ];
    let busyMetric = DEFAULT_WAIT_METRIC;
    const busyMetricBtns = {};
    const busyMetricBar = h('div',{style:{display:'flex',gap:'2px'}});
    busyMetricBar.append(h('span',{text:'Wait metric:',style:{color:C.m,fontSize:'12px',marginRight:'4px',alignSelf:'center'}}));
    for (const m of BUSY_METRICS) {
      const btn = h('button',{text:m.label,style:{
        background:m.k===busyMetric?C.b:C.bd, border:'none', color:C.t,
        padding:'4px 10px', borderRadius:'3px', cursor:'pointer',
        fontSize:'12px', fontFamily:'inherit', fontWeight:m.k===busyMetric?'600':'400',
      }});
      btn.onclick = () => {
        busyMetric = m.k;
        for (const [k,b] of Object.entries(busyMetricBtns)) { b.style.background=C.bd; b.style.fontWeight='400'; }
        btn.style.background = C.b; btn.style.fontWeight = '600';
        updateChart();
      };
      busyMetricBtns[m.k] = btn;
      busyMetricBar.append(btn);
    }
    busyControls.append(busyMetricBar);
    busyHeader.append(busyControls);
    busySection.append(busyHeader);
    const busyTableHost = h('div');
    busySection.append(busyTableHost);
    container.append(busySection);

    function renderBusyTable(filteredSnaps) {
      busyTableHost.innerHTML = '';
      // "Latest" mode inspects only the most recent snapshot's value per queue
      // so you see current state; every other mode aggregates the whole window.
      const isLatest = busyMetric === 'latest';
      const sampleKey = isLatest ? DEFAULT_WAIT_METRIC : busyMetric;
      const metricLabel = (BUSY_METRICS.find(m => m.k === busyMetric) || {}).label || '';
      const lastSnap = filteredSnaps[filteredSnaps.length - 1];

      const agg = {};
      for (const s of filteredSnaps) {
        for (const [q, d] of Object.entries(s.queues||{})) {
          if (!agg[q]) agg[q] = {peak_waiting:0,peak_running:0,peak_wait:0,sum_wait:0,wait_n:0,active:0,snapshots:0,latest_wait:0,latest_waiting:0,latest_running:0};
          const a = agg[q];
          a.snapshots++;
          const w = queueValue(d,'waiting') || 0;
          const r = queueValue(d,'running') || 0;
          if (w > a.peak_waiting) a.peak_waiting = w;
          if (r > a.peak_running) a.peak_running = r;
          if (w > 0 || r > 0) a.active++;
          const pw = queueWaitValue(d, sampleKey);
          if (pw > a.peak_wait) a.peak_wait = pw;
          if (pw > 0) { a.sum_wait += pw; a.wait_n++; }
        }
      }
      if (isLatest && lastSnap) {
        for (const [q, d] of Object.entries(lastSnap.queues||{})) {
          if (!agg[q]) continue;
          agg[q].latest_wait = queueWaitValue(d, DEFAULT_WAIT_METRIC);
          agg[q].latest_waiting = queueValue(d,'waiting') || 0;
          agg[q].latest_running = queueValue(d,'running') || 0;
        }
      }
      const rows = Object.entries(agg).map(([q,a]) => ({
        q,
        peak_waiting: isLatest ? a.latest_waiting : a.peak_waiting,
        peak_running: isLatest ? a.latest_running : a.peak_running,
        peak_wait: isLatest ? a.latest_wait : a.peak_wait,
        avg_wait: a.wait_n ? a.sum_wait / a.wait_n : 0,
        active_share: a.snapshots ? a.active / a.snapshots : 0,
        // Composite: log-scale backlog × active share × wait penalty.
        busy_score: (Math.log2(1 + a.peak_waiting) * (a.snapshots ? a.active/a.snapshots : 0) * (1 + (a.wait_n ? a.sum_wait/a.wait_n : 0)/30)),
      }));
      if (!rows.length) { busyTableHost.append(h('p',{text:'No queue activity in this interval.',style:{color:C.m,fontSize:'13px',margin:'8px 0'}})); return; }
      rows.sort((a,b)=>(b[busySort]||0)-(a[busySort]||0));
      const top = rows.slice(0, 12);

      const peakHeader = isLatest ? 'Latest wait' : `Peak ${metricLabel} wait`;
      const waitingHeader = isLatest ? 'Waiting' : 'Peak waiting';
      const runningHeader = isLatest ? 'Running' : 'Peak running';

      const tbl = h('table',{style:{width:'100%',borderCollapse:'collapse',fontSize:'13px'}});
      tbl.append(h('thead',{},[h('tr',{},[
        h('th',{text:'Queue',style:{textAlign:'left',padding:'6px 8px',borderBottom:`1px solid ${C.bd}`,color:C.m,fontSize:'12px'}}),
        h('th',{text:waitingHeader,style:{textAlign:'center',padding:'6px 8px',borderBottom:`1px solid ${C.bd}`,color:C.m,fontSize:'12px'}}),
        h('th',{text:runningHeader,style:{textAlign:'center',padding:'6px 8px',borderBottom:`1px solid ${C.bd}`,color:C.m,fontSize:'12px'}}),
        h('th',{text:peakHeader,style:{textAlign:'center',padding:'6px 8px',borderBottom:`1px solid ${C.bd}`,color:C.m,fontSize:'12px'}}),
        h('th',{text:'Active time',style:{textAlign:'center',padding:'6px 8px',borderBottom:`1px solid ${C.bd}`,color:C.m,fontSize:'12px'}}),
      ])]));
      const tb = h('tbody');
      for (const r of top) {
        const qc = qColorMap[r.q] || C.m;
        const tr = h('tr',{style:{borderBottom:`1px solid ${C.bd}22`}});
        tr.append(h('td',{style:{padding:'6px 8px'}},[
          h('span',{style:{width:'8px',height:'8px',borderRadius:'50%',background:qc,display:'inline-block',marginRight:'6px'}}),
          h('span',{text:r.q,style:{fontWeight:'600'}})
        ]));
        tr.append(h('td',{text:String(r.peak_waiting),style:{textAlign:'center',padding:'6px 8px',color:r.peak_waiting>0?C.r:C.m,fontWeight:r.peak_waiting>0?'600':'400'}}));
        tr.append(h('td',{text:String(r.peak_running),style:{textAlign:'center',padding:'6px 8px',color:r.peak_running>0?C.g:C.m,fontWeight:r.peak_running>0?'600':'400'}}));
        tr.append(h('td',{text:r.peak_wait?r.peak_wait.toFixed(1)+'m':'\u2014',style:{textAlign:'center',padding:'6px 8px',color:r.peak_wait>30?C.r:C.m}}));
        tr.append(h('td',{text:(r.active_share*100).toFixed(0)+'%',style:{textAlign:'center',padding:'6px 8px',color:C.m}}));
        tb.append(tr);
      }
      tbl.append(tb);
      busyTableHost.append(tbl);
    }

    // Jobs chart
    const chartSection = h('div',{style:{background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'8px',padding:'20px',marginBottom:'12px'}});
    chartSection.append(h('h3',{text:'Jobs Over Time',style:{marginBottom:'8px',fontSize:'15px'}}));
    const canvas = h('canvas',{style:{maxHeight:'300px'}});
    chartSection.append(canvas);
    container.append(chartSection);

    // Wait time chart with percentile selector
    const PERCENTILES = [
      {key:'p50_wait',label:'p50'},
      {key:'p90_wait',label:'p90'},
      {key:'p99_wait',label:'p99'},
    ];
    let selectedPercentile = DEFAULT_WAIT_METRIC;

    const waitSection = h('div',{style:{background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'8px',padding:'20px',marginBottom:'20px'}});
    const waitHeader = h('div',{style:{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:'8px',flexWrap:'wrap',gap:'8px'}});
    waitHeader.append(h('h3',{text:'Current Wait (derived from scheduled jobs, minutes)',style:{fontSize:'15px'}}));
    const pctBar = h('div',{style:{display:'flex',gap:'2px'}});
    const pctBtns = {};
    for (const p of PERCENTILES) {
      const btn = h('button',{text:p.label,style:{
        background:p.key===selectedPercentile?C.b:C.bd, border:'none', color:C.t,
        padding:'4px 10px', borderRadius:'3px', cursor:'pointer',
        fontSize:'13px', fontFamily:'inherit', fontWeight:p.key===selectedPercentile?'600':'400'
      }});
      btn.onclick = () => {
        selectedPercentile = p.key;
        for (const [k,b] of Object.entries(pctBtns)) { b.style.background=C.bd; b.style.fontWeight='400'; }
        btn.style.background = C.b; btn.style.fontWeight = '600';
        updateChart();
      };
      pctBtns[p.key] = btn;
      pctBar.append(btn);
    }
    waitHeader.append(pctBar);
    waitSection.append(waitHeader);
    const waitCanvas = h('canvas',{style:{maxHeight:'300px'}});
    waitSection.append(waitCanvas);
    container.append(waitSection);
    let waitChart = null;

    // Queue selector — grouped checkboxes
    const queueSection = h('div',{style:{background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'8px',padding:'16px',marginBottom:'20px'}});
    queueSection.append(h('div',{style:{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:'8px'}},[
      h('h3',{text:'Queues',style:{fontSize:'14px'}}),
      h('div',{style:{display:'flex',gap:'6px',flexWrap:'wrap'}},[
        makeBtn('Select All', () => { queueList.forEach(q=>selectedQueues.add(q)); updateCheckboxes(); updateChart(); }),
        makeBtn('AMD Only', () => { selectedQueues.clear(); queueList.filter(q=>q.startsWith('amd_') && !isMi355B(q)).forEach(q=>selectedQueues.add(q)); updateCheckboxes(); updateChart(); }),
        makeBtn('+ NVIDIA', () => { NVIDIA_QUEUES.forEach(q=>{if(allQueues.has(q))selectedQueues.add(q)}); updateCheckboxes(); updateChart(); }),
        makeBtn('NVIDIA Only', () => { selectedQueues.clear(); NVIDIA_QUEUES.forEach(q=>{if(allQueues.has(q))selectedQueues.add(q)}); updateCheckboxes(); updateChart(); }),
        makeBtn('+ MI355B', () => { queueList.filter(isMi355B).forEach(q=>selectedQueues.add(q)); updateCheckboxes(); updateChart(); }),
        makeBtn('Clear', () => { selectedQueues.clear(); updateCheckboxes(); updateChart(); }),
      ]),
    ]));

    const checkboxes = {};
    // Group queues
    const grouped = {};
    for (const q of queueList) {
      let grp = 'Other';
      for (const [name, fn] of Object.entries(Q_GROUPS)) {
        if (fn(q)) { grp = name; break; }
      }
      (grouped[grp] = grouped[grp] || []).push(q);
    }

    for (const [group, queues] of Object.entries(grouped)) {
      const row = h('div',{style:{marginBottom:'6px'}});
      row.append(h('div',{text:group,style:{fontSize:'13px',color:C.m,fontWeight:'600',textTransform:'uppercase',marginBottom:'2px'}}));
      const chips = h('div',{style:{display:'flex',flexWrap:'wrap',gap:'4px'}});
      for (const q of queues) {
        const qc = qColorMap[q] || '#8b949e';
        const chip = h('label',{style:{display:'inline-flex',alignItems:'center',gap:'4px',fontSize:'13px',cursor:'pointer',padding:'3px 8px',borderRadius:'3px',border:`1px solid ${C.bd}`,background:selectedQueues.has(q)?qc+'22':'transparent'}});
        const cb = h('input',{type:'checkbox',style:{width:'12px',height:'12px',cursor:'pointer'}});
        cb.checked = selectedQueues.has(q);
        cb.onchange = () => {
          if (cb.checked) selectedQueues.add(q); else selectedQueues.delete(q);
          chip.style.background = cb.checked ? qc+'22' : 'transparent';
          updateChart();
        };
        checkboxes[q] = { cb, chip };
        chip.append(cb, h('span',{style:{width:'8px',height:'8px',borderRadius:'50%',background:qc,display:'inline-block'}}), q);
        chips.append(chip);
      }
      row.append(chips);
      queueSection.append(row);
    }
    container.append(queueSection);

    function updateCheckboxes() {
      for (const [q, { cb, chip }] of Object.entries(checkboxes)) {
        cb.checked = selectedQueues.has(q);
        chip.style.background = selectedQueues.has(q) ? (qColorMap[q]||'#8b949e')+'22' : 'transparent';
      }
    }

    function updateChart() {
      // Use the last snapshot timestamp as the reference point, NOT Date.now().
      // Date.now() drifts away from the data window as time passes, causing
      // intervals shorter than the data span to show zero results.
      const lastSnapshotTs = new Date(snapshots[snapshots.length - 1].ts).getTime();
      const cutoff = new Date(lastSnapshotTs - intervalHours * 3600000);
      let filtered = snapshots.filter(s => new Date(s.ts) >= cutoff);

      // Update info banner to reflect the currently displayed data
      const filteredFirst = filtered.length ? new Date(filtered[0].ts) : lastSnapshotTs;
      const filteredLast = filtered.length ? new Date(filtered[filtered.length - 1].ts) : lastSnapshotTs;
      const filteredSpanMs = filteredLast - filteredFirst;
      const filteredHours = Math.round(filteredSpanMs / 3600000);
      const filteredDurText = filteredSpanMs < 3600000 ? `${Math.max(1, Math.round(filteredSpanMs / 60000))} minutes` :
                              filteredHours < 24 ? `${filteredHours} hours` :
                              `${Math.round(filteredHours / 24)} days`;
      const countsSource = latest.sources?.counts === 'cluster_metrics' ? 'Buildkite queue metrics' : 'fallback active job scan';
      const waitsSource = latest.sources?.waits === 'scheduled_jobs'
        ? 'derived from waiting jobs via Buildkite job data'
        : (latest.sources?.waits || 'active jobs');
      const zombieWait = latest.total_zombie_waiting || 0;
      const zombieRun = latest.total_zombie_running || 0;
      const zombieNote = (zombieWait || zombieRun)
        ? ` Excluding <strong>${zombieWait}</strong> queued and <strong>${zombieRun}</strong> running zombie jobs (&gt;4h) from queue stats.`
        : ' Queue stats exclude zombie jobs older than 4 hours.';
      infoBanner.innerHTML = `<strong>${filtered.length}</strong> snapshots over <strong>${filteredDurText}</strong> of data collected. Counts use <strong>${countsSource}</strong>; current waits use <strong>${waitsSource}</strong> rather than the Buildkite queue page's native Current Wait panel.${zombieNote}`;

      renderBusyTable(filtered);

      const labels = filtered.map(s => {
        const d = new Date(s.ts);
        const mon = d.toLocaleDateString('en-US', {month:'short'});
        const day = d.getDate();
        const hh = String(d.getHours()).padStart(2,'0');
        const mm = String(d.getMinutes()).padStart(2,'0');
        return intervalHours <= 24 ? `${hh}:${mm}` : `${mon} ${day}, ${hh}:${mm}`;
      });

      const datasets = [];
      for (const q of [...selectedQueues].sort()) {
        const qc = qColorMap[q] || '#8b949e';
        const raw = filtered.map(s => queueValue(s.queues?.[q], metric) || 0);
        datasets.push({
          label: q,
          data: scrubSpikes(raw),
          borderColor: qc,
          backgroundColor: qc + '15',
          tension: 0.3,
          fill: false,
          pointRadius: Math.max(4, Math.min(8, 200 / (filtered.length || 1))),
          pointHoverRadius: 6,
          borderWidth: 2,
        });
      }

      if (chart) chart.destroy();
      chart = new Chart(canvas, {
        type: 'line',
        data: { labels, datasets },
        options: {
          responsive: true,
          interaction: { mode: 'nearest', intersect: true },
          plugins: {
            legend: { labels: { color: C.t, font: {size:12} }, position: 'bottom' },
            tooltip: { mode: 'nearest', intersect: true, titleFont:{size:24}, bodyFont:{size:24}, padding:14, boxPadding:6,
              callbacks: { label: ctx => `${ctx.dataset.label}: ${ctx.parsed.y}` } },
          },
          scales: {
            y: { beginAtZero: true, ticks: { color: C.m }, grid: { color: C.bd }, title: { display: true, text: metric === 'waiting' ? 'Jobs Waiting' : 'Jobs Running', color: C.m, font:{size:13} } },
            x: { ticks: { color: C.m, maxRotation: 45, maxTicksLimit: 15, autoSkip: true }, grid: { color: C.bd } },
          },
        },
      });

      // Wait time chart: selected percentile per queue
      const waitDatasets = [];
      for (const q of [...selectedQueues].sort()) {
        const qc = qColorMap[q] || '#8b949e';
        // Treat missing samples as 0-wait rather than null. The user prefers a
        // continuous line over honest breaks: an empty queue has 0m wait by
        // definition, so filling with 0 doesn't falsify anything.
        const rawWait = filtered.map(s => {
          const qd = s.queues?.[q];
          if (!qd) return 0;
          return queueWaitValue(qd, selectedPercentile);
        });
        waitDatasets.push({
          label: q,
          data: scrubSpikes(rawWait),
          borderColor: qc,
          backgroundColor: qc + '15',
          tension: 0.3,
          fill: false,
          pointRadius: Math.max(4, Math.min(8, 200 / (filtered.length || 1))),
          borderWidth: 2,
        });
      }

      if (waitChart) waitChart.destroy();
      waitChart = new Chart(waitCanvas, {
        type: 'line',
        data: { labels, datasets: waitDatasets },
        options: {
          responsive: true,
          interaction: { mode: 'nearest', intersect: true },
          plugins: {
            legend: { labels: { color: C.t, font: {size:12} }, position: 'bottom' },
            tooltip: { mode: 'nearest', intersect: true, titleFont:{size:24}, bodyFont:{size:24}, padding:14, boxPadding:6,
              callbacks: { label: ctx => ctx.parsed.y != null ? `${ctx.dataset.label}: ${ctx.parsed.y}m` : `${ctx.dataset.label}: no data` } },
          },
          scales: {
            y: { beginAtZero: true, ticks: { color: C.m, callback: v => v + 'm' }, grid: { color: C.bd }, title: { display: true, text: (PERCENTILES.find(p=>p.key===selectedPercentile)?.label||'p50') + ' Wait Time (minutes)', color: C.m, font:{size:13} } },
            x: { ticks: { color: C.m, maxRotation: 45, maxTicksLimit: 15, autoSkip: true }, grid: { color: C.bd } },
          },
        },
      });
    }

    // Defer initial chart render so the browser completes layout after the
    // tab panel switches from display:none → display:block.  Without this,
    // Chart.js reads a zero-width canvas on first load via URL hash.
    // Double-render: first pass sets up the chart, second pass after a short
    // delay forces a resize to fix incorrect dimensions on first navigation.
    requestAnimationFrame(() => {
      updateChart();
      setTimeout(() => {
        if (chart) chart.resize();
        if (waitChart) waitChart.resize();
      }, 150);
    });
  }

  function makeCard(label, value, sub, color) {
    return h('div',{style:{background:C.bg,border:`1px solid ${C.bd}`,borderRadius:'8px',padding:'16px 20px',borderTop:`3px solid ${color}`}},[
      h('div',{text:label,style:{fontSize:'13px',color:C.m,textTransform:'uppercase',letterSpacing:'.5px',marginBottom:'4px'}}),
      h('div',{text:String(value),style:{fontSize:'28px',fontWeight:'800',color,lineHeight:'1.1'}}),
      sub?h('div',{text:sub,style:{fontSize:'14px',color:C.m,marginTop:'4px'}}):null,
    ]);
  }

  function makeBtn(text, onclick) {
    const btn = h('button',{text,style:{background:C.bd,border:'none',color:C.t,padding:'4px 10px',borderRadius:'3px',cursor:'pointer',fontSize:'13px',fontFamily:'inherit'}});
    btn.onclick = onclick;
    return btn;
  }

  // Lazy load
  const obs = new MutationObserver(() => {
    const p = document.getElementById('tab-ci-queue');
    if (p?.classList.contains('active') && !p.dataset.loaded) { p.dataset.loaded = '1'; render(); }
  });
  document.addEventListener('DOMContentLoaded', () => {
    const p = document.getElementById('tab-ci-queue');
    if (p) {
      obs.observe(p, {attributes:true, attributeFilter:['class']});
      // If the tab is already active (e.g. navigated via URL hash), render immediately
      if (p.classList.contains('active') && !p.dataset.loaded) { p.dataset.loaded = '1'; render(); }
    }
  });
  document.addEventListener('auth:changed', () => {
    const p = document.getElementById('tab-ci-queue');
    if (p?.classList.contains('active') && p.dataset.loaded) render();
  });
})();
