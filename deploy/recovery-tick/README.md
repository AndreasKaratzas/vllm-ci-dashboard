# Independent dashboard recovery clock

GitHub can delay or drop scheduled workflow events. A collector, monitor and
watchdog that all depend on that scheduler cannot guarantee fresh data when
events stop arriving. The independent clock runs on an always-on Linux host and
wakes the existing guarded workflows. GitHub schedules remain useful additional
triggers. The fixed profiles preserve each producer's normal collection gates:

| Workflow | External dispatch interval |
| --- | --- |
| Publication Watchdog | Ten minutes |
| Queue Monitor | Ten minutes |
| Queue Lifecycle Monitor | Thirty minutes |
| DNS Health Monitor | One hour |
| Site Health Check | One hour |
| Deployment Record Retention | One day, applying normal retention |
| Scheduler Activity Keepalive | One week |

The client uses Python 3.10 or newer and only its standard library. It dispatches
only these allowlisted workflows on `main`; it never contacts Buildkite or
changes budget records. Each workflow decides whether work is due using its
normal gates. The watchdog deduplicates active recovery, and the canonical
collector still reserves its normal allowance and enforces every existing quota
and validation gate. Lifecycle and DNS need their own wakeups: full collection
imports their durable observations but cannot advance their source clocks.

Use a repository-scoped GitHub token with **Actions: write** for
`AndreasKaratzas/vllm-ci-dashboard`. The external host needs no Buildkite token,
repository write access, private collector caches or full repository checkout.
Do not put this client inside another GitHub scheduled workflow: that would
share the failure it is meant to detect.

Install the reviewed client and units on the selected host:

```sh
sudo install -d -m 755 /opt/vllm-dashboard-recovery
sudo install -m 644 scripts/vllm/external_recovery_tick.py /opt/vllm-dashboard-recovery/
sudo install -m 644 deploy/recovery-tick/vllm-dashboard-recovery@.service /etc/systemd/system/
sudo install -m 644 deploy/recovery-tick/vllm-dashboard-recovery@.timer /etc/systemd/system/
sudo install -m 600 /dev/null /etc/vllm-dashboard-recovery.env
sudoedit /etc/vllm-dashboard-recovery.env
```

Set `GH_TOKEN=...` in that private environment file. Then activate and inspect:

```sh
sudo systemctl daemon-reload
for workflow in publication-watchdog queue-monitor queue-lifecycle dns-health health-check deployment-retention scheduler-activity; do
  sudo systemctl enable --now "vllm-dashboard-recovery@$workflow.timer"
done
sudo systemctl start vllm-dashboard-recovery@publication-watchdog.service
sudo systemctl list-timers 'vllm-dashboard-recovery@*.timer'
sudo journalctl -u 'vllm-dashboard-recovery@*.service' --since '30 minutes ago'
```

The journal should report `dispatched` with a GitHub run ID, or `cooldown` if
another activation already succeeded. Verify that the linked watchdog finishes
and that a due collection produces a genuinely newer public generation. The
local record contains only the last acknowledged dispatch and retains no token.
Each instance has isolated private state. The real `dispatch` child works with
systemd's private state-directory symlink; a final-component symlink is refused.
Timers check every ten minutes, while the client's durable interval gate limits
actual dispatches to the profile's cadence. Overlap is serialized, state is
bounded, and a failed request does not consume that interval. HTTP errors fail
the service visibly. Monitor timer
activation and service failures on the external host; rotate its token before
expiry. If GitHub itself is unavailable, the next tick retries when it returns.

The three-hour stale-data warning remains enforced. Neither a timer activation
nor successful dispatch is evidence that collection succeeded; only validated
new source data and a completed publication establish recovery.

Scheduling behavior: [GitHub troubleshooting documentation](https://docs.github.com/en/actions/how-tos/troubleshoot-workflows#scheduled-workflows-running-at-unexpected-times).
Token scope and dispatch API: [GitHub workflow documentation](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event).
