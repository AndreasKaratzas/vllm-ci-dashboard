# Independent dashboard recovery clock

<!-- cspell:ignore sudoedit journalctl -->

The primary clock is the in-repository **Dashboard Clock** workflow
(`.github/workflows/dashboard-clock.yml`). In October 2026 GitHub delivered only
4-8 of the watchdog's 96 daily scheduled ticks, but `workflow_dispatch` requests
made with `GITHUB_TOKEN` reliably start runs. Each clock run executes
`external_recovery_tick.py --github-actions-loop` for 5.5 hours, then an
`if: always()` step dispatches its successor on `main`. The loop keeps its
schedule in memory and wakes these profiles every minute they are due:

| Workflow | In-repository dispatch interval |
| --- | --- |
| Publication Watchdog | Twenty minutes |
| Queue Monitor | Ten minutes |
| Queue Lifecycle Monitor | Thirty minutes |
| Docker Build Monitor | Thirty minutes |
| DNS Health Monitor | One hour |
| Site Health Check | One hour |

Deployment retention and the scheduler keepalive keep their own schedules. A
failed dispatch is logged as a warning and retried on the next pass. The
`dashboard-clock` concurrency group allows one running and one pending run, so
duplicate dispatches collapse instead of multiplying chains. When every successor
dispatch attempt fails, the run fails visibly and the hourly schedule revives the
chain on the next delivered GitHub event. A run whose loop fails or is
cancelled hands off no sooner than 20 minutes after it started, so a persistent
early failure cannot hot-loop. Disable the workflow to stop the chain;
cancelling a run still dispatches its successor.

`workflow_run` events never follow runs started with `GITHUB_TOKEN`, so the
chain relies on `workflow_dispatch` only. Clock-dispatched runs are attributed
to `github-actions[bot]`. Site Health's clock liveness probe counts only those
watchdog dispatches, so external revivers cannot mask a broken chain.

The external clocks below are now optional revivers. They may keep their
current profiles or dispatch only `dashboard-clock.yml` (the Linux client's
`dashboard-clock` profile, at most hourly). Every target applies its own gates,
so overlap with the in-repository clock is harmless.

The owner-private
[hosted recovery service](https://vllm-dashboard-recovery-clock.truejoy5.chatgpt.site)
runs an hourly cloud task, **Keep the AMD CI dashboard fresh**, while the
development workspace is closed. Before the in-repository clock it was the
primary trigger in practice: the hosted task dispatched the watchdog 23-24 times
a day. Buildkite
`repository_dispatch` events add no redundancy because no relay is deployed
(the last one arrived on 2026-03-26). The hosted task provider permits
one run per hour; the workflow profiles below still enforce their own minimum
dispatch intervals and every producer retains its collection admission gates.

Site Health raises its incident when the newest watchdog run dispatched by the
Dashboard Clock is older than 2.5 hours, so a stopped chain reaches the owner
through the assigned site-health issue on the next delivered GitHub schedule.

The hosted task admits DNS and health once in each UTC hour, with at least ten
minutes between acknowledged dispatches. A task arriving earlier than the prior
hour's execution can still wake both profiles. Repeated requests within the same
hour and unresolved acknowledgements remain gated. Durable acknowledgements
retain their actual timestamps. The Linux client's one-hour intervals remain
unchanged.

DNS checks the recent workflow history before reserving provider requests. A
failed or cancelled scan on the same code revision starts a 50-minute retry
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
wakes the existing guarded workflows. GitHub schedules remain best-effort
additional triggers. The fixed profiles preserve each producer's normal
collection gates:

| Workflow | External dispatch interval |
| --- | --- |
| Publication Watchdog | Ten minutes |
| Queue Monitor | Ten minutes |
| Queue Lifecycle Monitor | Thirty minutes |
| Docker Build Monitor | Thirty minutes |
| DNS Health Monitor | One hour |
| Site Health Check | One hour |
| Deployment Record Retention | One day, applying normal retention |
| Scheduler Activity Keepalive | One week |
| Dashboard Clock (reviver only) | One hour |

The client uses Python 3.10 or newer and only its standard library. It dispatches
only these allowlisted workflows on `main`; it never contacts Buildkite or
changes budget records. Each workflow decides whether work is due using its
normal gates. The watchdog deduplicates active recovery, and the canonical
collector still reserves its normal allowance and enforces every existing quota
and validation gate. Lifecycle, docker builds and DNS need their own wakeups: full collection
imports their durable observations but cannot advance their source clocks.

Use a repository-scoped GitHub token with **Actions: write** for
`AndreasKaratzas/vllm-ci-dashboard`. The external host needs no Buildkite token,
repository write access, private collector caches or full repository checkout.
Do not drive this client from a GitHub cron alone: dropped schedule events
would share the failure it is meant to detect. The Dashboard Clock avoids that
by chaining `workflow_dispatch` runs and using its cron only as a reviver.

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
for workflow in publication-watchdog queue-monitor queue-lifecycle docker-builds dns-health health-check deployment-retention scheduler-activity; do
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
