"""Real immutable-ledger gates compose with collector recovery planning."""
from dataclasses import asdict
from datetime import timedelta
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from vllm import observe_watchdog_collection_admission as admission
from vllm import plan_publication_watchdog as watchdog
from vllm import request_bearing_attempt_budget as budget
from tests.vllm import test_request_bearing_attempt_budget as ledger_fixtures
from tests.vllm.test_publication_watchdog import write_operations_manifest
from tests.vllm.test_request_bearing_attempt_budget import BASE, initialize, seed, remote_sha

repo = ledger_fixtures.repo
policy = ledger_fixtures.policy


class Clock:
    def __init__(self):
        self.now = BASE
        self.elapsed = 0.0
        self.sleeps = []

    def sleep(self, seconds):
        assert 0 < seconds <= 30
        self.sleeps.append(seconds)
        self.now += timedelta(seconds=seconds)
        self.elapsed += seconds


def observe(root, policy, clock, max_wait_seconds=300):
    return admission.observe_admission(root, policy, clock=lambda: clock.now,
                                       monotonic=lambda: clock.elapsed, sleep=clock.sleep,
                                       observe=budget.observe, max_wait_seconds=max_wait_seconds)


@pytest.mark.parametrize(("success", "age", "expected_mode"), [
    (True, 117, "success_gated"), (False, 27, "retry_gated"),
])
def test_near_real_gate_waits_then_observes_open_without_accounting_write(repo, policy, success, age, expected_mode):
    root, _ = repo
    initial = initialize(root, policy, [seed(1, BASE - timedelta(minutes=age), success=success)])
    clock = Clock()
    before = budget.observe(root, policy, now=BASE, remote="origin")
    assert before["request_mode"] == expected_mode
    result = observe(root, policy, clock)
    assert result.required is True and result.request_mode == "reserved"
    assert result.available_at == "" and result.waited_seconds == 180
    assert sum(clock.sleeps) == 180 and len(clock.sleeps) == 6
    assert remote_sha(root, policy) == initial
    assert result.budget_sha == initial


def test_far_success_gate_suppresses_stale_collector_without_sleep_or_accounting_write(repo, policy):
    root, _ = repo
    initial = initialize(root, policy, [seed(1, BASE - timedelta(minutes=90))])
    clock = Clock()
    result = observe(root, policy, clock)
    assert result.required is False and result.reason == "collection-success-gated"
    assert result.available_at == budget._iso(BASE + timedelta(minutes=30))
    assert not clock.sleeps and remote_sha(root, policy) == initial


def test_capacity_gate_releases_exactly_at_25_hour_boundary_without_policy_change(repo, policy):
    root, _ = repo
    oldest = BASE - timedelta(hours=25) + timedelta(seconds=142)
    attempts = [budget._attempt_row(attempt_id=f"runtime-{i}", reserved_at=oldest + timedelta(minutes=85*i),
                                   policy=policy, source="runtime", bound_proven=True, workflow_run_id=str(100+i),
                                   workflow_run_attempt=1, event_name="schedule") for i in range(16)]
    ledger = budget._create_commit(root, budget._new_ledger(attempts, now=BASE, policy=policy), policy)
    budget._push(root, "origin", ledger.commit_sha, None, policy)
    clock = Clock()
    result = observe(root, policy, clock)
    assert result.required is True and sum(clock.sleeps) == 142
    assert remote_sha(root, policy) == ledger.commit_sha
    assert policy.max_request_bearing_attempts == 16 and policy.request_start_allowance == 800
    assert len(budget.validate_ledger_ref(root, ledger.commit_sha, policy).ledger["attempts"]) == 16


def test_concurrent_reservation_moves_gate_beyond_original_deadline_and_abandons_wait(repo, policy):
    root, _ = repo
    initialize(root, policy, [seed(1, BASE - timedelta(minutes=117))])
    clock = Clock()
    def sleep(seconds):
        clock.sleep(seconds)
        # A real concurrent producer charges an attempt. The watchdog only observes it.
        if clock.elapsed == 180:
            budget.reserve(root, policy, attempt_id="concurrent-2-1", workflow_run_id="2", workflow_run_attempt=1,
                           event_name="schedule", now=clock.now, remote="origin")
    result = admission.observe_admission(root, policy, clock=lambda: clock.now, monotonic=lambda: clock.elapsed,
                                        sleep=sleep, observe=budget.observe, max_wait_seconds=300)
    assert result.required is False and result.reason == "collection-admission-shifted"
    assert result.available_at == budget._iso(BASE + timedelta(minutes=33))
    assert clock.sleeps == [30] * 6


def test_health_completion_observes_near_gate_once_without_deliberate_wait(repo, policy):
    root, _ = repo
    initialize(root, policy, [seed(1, BASE - timedelta(minutes=119))])
    clock = Clock()
    result = observe(root, policy, clock, max_wait_seconds=0)
    assert result.required is False and result.reason == "collection-success-gated"
    assert result.available_at == budget._iso(BASE + timedelta(minutes=1))
    assert result.waited_seconds == 0 and clock.sleeps == []


def test_zero_wait_still_admits_a_due_real_ledger(repo, policy):
    root, _ = repo
    initialize(root, policy, [seed(1, BASE - timedelta(minutes=121))])
    result = observe(root, policy, Clock(), max_wait_seconds=0)
    assert result.required is True and result.waited_seconds == 0


def _value(**updates):
    value = {"observation_valid": "true", "required": "false", "request_mode": "retry_gated",
             "available_at": budget._iso(BASE + timedelta(seconds=30)), "budget_sha": "a" * 40}
    value.update(updates)
    return value


@pytest.mark.parametrize("updates", [
    {"observation_valid": "false"}, {"required": True}, {"required": "true"},
    {"request_mode": "arbitrary"}, {"budget_sha": "bad"},
    {"available_at": budget._iso(BASE)}, {"available_at": budget._iso(BASE - timedelta(seconds=1))},
    {"available_at": "2026-09-01T12:00:30+00:00"}, {"available_at": "2026-09-01T12:00:30.1Z"},
    {"available_at": budget._iso(BASE + timedelta(minutes=121))},
])
def test_unverified_inconsistent_or_noncanonical_observation_fails_closed(policy, updates):
    clock = Clock()
    with pytest.raises((ValueError, budget.AttemptBudgetError)):
        admission.observe_admission(Path('.'), policy, clock=lambda: clock.now, monotonic=lambda: clock.elapsed,
                                    sleep=clock.sleep, observe=lambda *a, **k: _value(**updates),
                                    max_wait_seconds=300)
    assert clock.sleeps == []


@pytest.mark.parametrize("bound", [-1, 1801, float('nan'), float('inf'), True])
def test_wait_bound_cannot_be_extended(policy, bound):
    with pytest.raises(ValueError, match="bounded"):
        admission.observe_admission(Path('.'), policy, max_wait_seconds=bound)


def test_unmoving_provider_gate_stops_at_initial_deadline(policy):
    clock = Clock()
    def provider(*args, **kwargs):
        # A changing future gate stays within the original deadline, then is
        # moved by a late concurrent reservation: it cannot renew that deadline.
        value = _value(available_at=budget._iso(BASE + timedelta(seconds=300)))
        if clock.elapsed >= 300:
            value['available_at'] = budget._iso(BASE + timedelta(seconds=301))
        return value
    result = admission.observe_admission(Path('.'), policy, clock=lambda: clock.now, monotonic=lambda: clock.elapsed,
                                        sleep=clock.sleep, observe=provider, max_wait_seconds=300)
    assert not result.required and result.reason == 'collection-admission-shifted'
    assert sum(clock.sleeps) == 300 and result.waited_seconds == 300


def _proof(tmp_path, **updates):
    payload = asdict(admission.AdmissionDecision(1, False, 'collection-success-gated', 'success_gated',
                                               budget._iso(BASE + timedelta(minutes=30)), 'a'*40, budget._iso(BASE), 0))
    payload.update(updates)
    path = tmp_path / 'admission.json'; path.write_text(json.dumps(payload)); return path


@pytest.mark.parametrize("updates", [
    {'required': 'false'}, {'schema_version': True}, {'waited_seconds': 1801}, {'waited_seconds': True},
    {'observed_at': budget._iso(BASE + timedelta(seconds=1))},
    {'observed_at': budget._iso(BASE - timedelta(minutes=7))}, {'reason': 'unknown\nrequired=true'},
    {'budget_sha': 'bad'}, {'available_at': budget._iso(BASE)}, {'request_mode': 'reserved'},
    {'extra': 'untrusted'},
])
def test_planner_refuses_corrupt_future_or_expired_admission_proof(tmp_path, updates):
    with pytest.raises((ValueError, budget.AttemptBudgetError)):
        admission.load_admission(_proof(tmp_path, **updates), now=BASE)


def _plan(tmp_path, proof, *, now=BASE, active=False, target='collector', force='site-health-stale',
          publication_age=timedelta(hours=4), core_age=timedelta(minutes=110)):
    status = tmp_path/'status.json'; status.write_text(json.dumps({
        'schema_version': 1, 'mode':'current','status':'healthy','generated_at':budget._iso(now-publication_age),
        'degraded_since':None,'publication_blocked':False,'uses_fallback':False,'affected_surfaces':[],
        'affected_surface_count':0,'fallback_surface_count':0,'fresh_degraded_surface_count':0,
    }))
    runs = tmp_path/'runs.json'; runs.write_text(json.dumps({'workflow_runs': [{
        'id':1,'head_branch':'main','head_sha':'c'*40,'status':'in_progress','created_at':budget._iso(BASE),
        'run_started_at':budget._iso(BASE),'updated_at':budget._iso(BASE),'conclusion':None,'display_title':'Data Collection',
    }] if active else []}))
    output = tmp_path/'outputs'; output.unlink(missing_ok=True)
    argv = ['--status',str(status),'--workflow-runs',str(runs),'--recovery-target',target,
            '--now',budget._iso(now),'--github-output',str(output)]
    if force:
        argv += ['--force-recovery-reason',force]
    if proof is not None:
        clock = budget._iso(now - core_age)
        manifest = write_operations_manifest(
            tmp_path/'operations.json', dict.fromkeys(watchdog.load_core_targets().sources, clock))
        argv += ['--collector-admission',str(proof),'--operations-manifest',str(manifest)]
    if target == 'dns-health':
        argv += ['--current-code-sha','c'*40]
    assert watchdog.main(argv) == 0
    return dict(line.split('=',1) for line in output.read_text().splitlines())


def _write_proof(result, tmp_path):
    proof = tmp_path/'proof.json'; proof.write_text(json.dumps(asdict(result))); return proof


def test_actual_gate_proof_blocks_stale_and_forced_collector_plan(repo, policy, tmp_path):
    root, _ = repo
    initialize(root, policy, [seed(1, BASE-timedelta(minutes=90))])
    decision = _plan(tmp_path, _write_proof(observe(root, policy, Clock()), tmp_path))
    assert decision['required'] == 'false' and decision['reason'] == 'collection-success-gated'


@pytest.mark.parametrize('active', [False, True])
def test_admitted_collector_still_uses_fresh_active_run_deduplication(repo, policy, tmp_path, active):
    root, _ = repo
    initialize(root, policy, [seed(1, BASE-timedelta(minutes=121))])
    decision = _plan(tmp_path, _write_proof(observe(root, policy, Clock()), tmp_path), active=active)
    assert decision['required'] == ('false' if active else 'true')
    assert decision['reason'] == ('collection-active' if active else 'site-health-stale')


@pytest.mark.parametrize(('core_age', 'reason'), [
    (timedelta(minutes=110), 'buildkite-collection-due'), (timedelta(minutes=135), 'core-data-stale'),
])
def test_near_gate_opening_promotes_a_current_publication(repo, policy, tmp_path, core_age, reason):
    root, _ = repo
    initialize(root, policy, [seed(1, BASE-timedelta(minutes=118))])
    clock = Clock()
    result = observe(root, policy, clock)
    assert result.required is True and sum(clock.sleeps) == 120
    decision = _plan(tmp_path, _write_proof(result, tmp_path), now=clock.now, force='',
                     publication_age=timedelta(minutes=10), core_age=core_age)
    assert decision['required'] == 'true' and decision['reason'] == reason


@pytest.mark.parametrize(('gate_minutes', 'budget_minutes', 'required'), [(20, 30, True), (31, 30, False)])
def test_gate_inside_run_budget_waits_then_dispatches(repo, policy, tmp_path,
                                                     gate_minutes, budget_minutes, required):
    root, _ = repo
    initialize(root, policy, [seed(1, BASE-timedelta(minutes=120-gate_minutes))])
    clock = Clock()
    result = observe(root, policy, clock, max_wait_seconds=budget_minutes*60)
    assert result.required is required
    assert sum(clock.sleeps) == (gate_minutes*60 if required else 0)
    decision = _plan(tmp_path, _write_proof(result, tmp_path), now=clock.now, force='', publication_age=timedelta(minutes=5))
    assert decision['required'] == str(required).lower()
    assert decision['reason'] == ('buildkite-collection-due' if required else 'collection-success-gated')


@pytest.mark.parametrize(('target','force'), [('deploy-pages','state-slot-repair'), ('dns-health','dns-only-degraded'), ('collector','state-uninitialized')])
def test_pages_dns_and_bootstrap_plans_remain_independent_of_collector_admission(tmp_path, target, force):
    result = _plan(tmp_path, None, target=target, force=force)
    assert result['required'] == 'true' and result['reason'] == force


def test_workflow_waits_for_a_gate_inside_the_remaining_run_budget():
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root/'.github/workflows/publication-watchdog.yml').read_text())
    job = workflow['jobs']['recover']; steps = job['steps']
    names = [step.get('name') for step in steps]
    clock = steps[1]
    assert clock['id'] == 'run-clock' and '+ 30 * 60' in clock['run']
    gate = steps[names.index('Observe bounded collector admission')]
    assert names.index('Inspect durable state and choose recovery target') < names.index(gate['name']) < names.index('Read selected recovery workflow state')
    assert job['timeout-minutes'] == 35 and gate['timeout-minutes'] == 31
    assert admission.MAX_WAIT_SECONDS == 30 * 60
    assert "state-uninitialized" in gate['if'] and "'collector'" in gate['if']
    assert gate['env']['WAIT_DEADLINE'] == '${{ steps.run-clock.outputs.wait_deadline }}'
    assert 'ADMISSION_MAX_WAIT_SECONDS=$(( WAIT_DEADLINE - $(date -u +%s) ))' in gate['run']
    assert '--max-wait-seconds "$ADMISSION_MAX_WAIT_SECONDS"' in gate['run']
    assert 'BUILDKITE_TOKEN' not in gate['env']
    planner = steps[names.index('Plan deduplicated targeted recovery')]
    assert '--collector-admission' in planner['run']
    assert '--operations-manifest "$RUNNER_TEMP/pages-operations-manifest.json"' in planner['run']


def test_workflow_hydrates_the_operations_manifest_from_its_projection_proof():
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root/'.github/workflows/publication-watchdog.yml').read_text())
    route = next(step for step in workflow['jobs']['recover']['steps'] if step.get('id') == 'recovery-route')['run']
    hydration = route[route.index('OPERATIONS_MANIFEST="$RUNNER_TEMP/pages-operations-manifest.json"'):]
    hydration = hydration[:hydration.index('\nfi\n')]
    assert route.index('public_projection.py verify-git') < route.index(hydration)
    assert 'GIT_NO_LAZY_FETCH=1 git show \\\n     origin/gh-pages:publication_manifest.json' in hydration
    assert '["files"]["data/vllm/ci/operations_v2_manifest.json"]' in hydration
    assert f'hydrate_proven_blob "$OPERATIONS_OID" "$OPERATIONS_BYTES" \\\n     {watchdog.OPERATIONS_MANIFEST_MAX_BYTES} ' in hydration
    assert 'GIT_NO_LAZY_FETCH=1 git cat-file blob "$OPERATIONS_OID" > "$OPERATIONS_MANIFEST"' in hydration
    assert '::warning::' in hydration and 'exit' not in hydration


def _subprocess_root(root, policy):
    project = Path(__file__).resolve().parents[2]
    (root / 'scripts').symlink_to(project / 'scripts', target_is_directory=True)
    (root / 'config').mkdir()
    (root / 'config/data_collection_attempt_budget.json').write_text(json.dumps({'schema_version': 1, **asdict(policy)}))


def test_default_observer_executes_actual_read_only_budget_cli_in_bounded_subprocess(repo, policy):
    root, _ = repo
    initial = initialize(root, policy, [seed(1, BASE-timedelta(minutes=121))])
    _subprocess_root(root, policy)
    clock = Clock()
    result = admission.observe_admission(root, policy, clock=lambda: clock.now, monotonic=lambda: clock.elapsed,
                                        sleep=clock.sleep, max_wait_seconds=0)
    assert result.required is True and result.budget_sha == initial
    assert remote_sha(root, policy) == initial and clock.sleeps == []


def test_budget_read_timeout_is_fail_closed_and_does_not_sleep(repo, policy, monkeypatch):
    root, _ = repo
    _subprocess_root(root, policy)
    timeouts = []
    def child(args, **kwargs):
        timeouts.append(kwargs['timeout'])
        raise subprocess.TimeoutExpired(args, kwargs['timeout'])
    monkeypatch.setattr(admission, '_run_bounded_observation', child)
    clock = Clock()
    with pytest.raises(ValueError, match='deadline'):
        admission.observe_admission(root, policy, clock=lambda: clock.now, monotonic=lambda: clock.elapsed,
                                    sleep=clock.sleep, max_wait_seconds=0)
    assert timeouts == [55] and clock.sleeps == []


def test_slow_final_observation_is_bounded_by_remaining_thirty_seconds(repo, policy, monkeypatch):
    root, _ = repo
    _subprocess_root(root, policy)
    clock = Clock(); timeouts = []
    def child(args, **kwargs):
        timeouts.append(kwargs['timeout'])
        if clock.elapsed == 300:
            # The real subprocess boundary raises and kills/reaps a child after
            # this remaining timeout, rather than granting it another 60 seconds.
            clock.elapsed += kwargs['timeout']; clock.now += timedelta(seconds=kwargs['timeout'])
            raise subprocess.TimeoutExpired(args, kwargs['timeout'])
        value = _value(available_at=budget._iso(BASE + timedelta(seconds=300)))
        return subprocess.CompletedProcess(args, 0, '\n'.join(f'{k}={v}' for k,v in value.items()).encode(), b'')
    monkeypatch.setattr(admission, '_run_bounded_observation', child)
    with pytest.raises(ValueError, match='deadline'):
        admission.observe_admission(root, policy, clock=lambda: clock.now, monotonic=lambda: clock.elapsed,
                                    sleep=clock.sleep, max_wait_seconds=300)
    assert sum(clock.sleeps) == 300 and clock.elapsed == 325
    assert timeouts[-1] == 25 and all(timeout <= 60 for timeout in timeouts)


@pytest.mark.parametrize('updates', [
    {'request_mode':'retry_gated'}, {'available_at':budget._iso(BASE+timedelta(hours=3))},
    {'reason':'collection-retry-gated'},
])
def test_gated_proof_must_match_actual_producer_mode_reason_and_horizon(tmp_path, updates):
    with pytest.raises(ValueError, match='inconsistent'):
        admission.load_admission(_proof(tmp_path, **updates), now=BASE)


def test_malformed_or_duplicate_proof_json_fails_with_planner_error(tmp_path):
    proof = tmp_path/'proof.json'; proof.write_text('{"required":true,"required":false}')
    with pytest.raises(SystemExit) as error:
        _plan(tmp_path, proof)
    assert error.value.code == 2


def test_timeout_kills_the_entire_git_read_process_group(tmp_path):
    child_pid_path = tmp_path/'child.pid'
    script = ('import subprocess,sys,time;from pathlib import Path;'
              'child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]);'
              'Path(sys.argv[1]).write_text(str(child.pid));time.sleep(60)')
    with pytest.raises(ValueError, match='deadline'):
        admission._run_bounded_observation([sys.executable,'-c',script,str(child_pid_path)], timeout=0.2)
    child_pid = int(child_pid_path.read_text())
    state_path = Path(f'/proc/{child_pid}/stat')
    # An orphan can briefly remain a zombie until PID 1 reaps it; it cannot
    # continue performing the ledger read or holding its pipes open. Reaping
    # can also land between opening and reading its stat file (no such process).
    try:
        state = state_path.read_text().split()[2]
    except (FileNotFoundError, ProcessLookupError):
        state = None
    assert state in (None, 'Z')
