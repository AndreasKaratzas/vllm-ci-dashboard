# Independent dashboard recovery clock

<!-- cspell:ignore sudoedit journalctl -->

The dashboard now has an owner-private
[hosted recovery service](https://vllm-dashboard-recovery-clock.truejoy5.chatgpt.site)
and an enabled hourly cloud task, **Keep the AMD CI dashboard fresh**. This
independent backup runs while the development workspace is closed. GitHub's
normal schedules remain the primary triggers. The hosted task provider permits
one run per hour; the workflow profiles below still enforce their own minimum
dispatch intervals and every producer retains its collection admission gates.

The hosted hourly task admits DNS and health dispatches after 55 minutes. This
five-minute allowance accommodates early scheduler arrivals, so a small timing
change does not skip an entire hourly check. Durable acknowledgements retain
their actual timestamps. The Linux client's one-hour intervals remain unchanged.

DNS checks the recent workflow history before reserving provider requests. A
failed or cancelled scan on the same code revision starts a 70-minute retry
cooldown for both normal and watchdog triggers. A newer revision can retry an
older failure, subject to the existing rolling request budget. Successful DNS
generations still use the three-hour collection interval.

The hosted service stores acknowledgements and an expiring concurrency lease in
D1, and keeps its GitHub credential in a hosted secret. Its private `/status`
endpoint reports the last tick, profile acknowledgements and failures; `/instructions`
records the unattended update procedure. Successful dispatch is not proof of a
successful collection. Check the linked workflow and the dashboard's real
publication/core observation clocks before declaring recovery. Preserve the
owner-private audience and rotate the hosted credential through Sites settings.

The Linux client below provides a ten-minute clock when an always-on host is
available. It can replace the hourly hosted backup without depending on GitHub
scheduled events.

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
