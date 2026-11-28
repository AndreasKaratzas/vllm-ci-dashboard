# Deployment record retention

`deployment-retention.yml` runs daily on `main` and keeps the newest 20 deployment
records across the repository. It deletes older inactive records and retires only
superseded terminal native GitHub Pages records (`success`, `failure`, or `error`). Retirement requires a newer
protected success in the same environment, with the same site URL and a commit
that still matches the current `gh-pages` reference. The old record must have a
distinct deployment ID, be strictly older, non-transient, and non-production. Both statuses and the
source reference are checked again before changing only the old record to
`inactive` with `auto_inactive: false`.

Serving, queued, pending, in-progress, unknown, and other active records
remain visible even when that leaves more than 20 records. The workflow does not
publish the website, alter the serving deployment, or change dashboard data.

Manual runs default to a read-only preview. The Python command also defaults to a
preview and requires `--apply` to delete records. Each run fully paginates the
inventory, writes a private append-only journal outside the checkout, serializes
status writes and DELETE requests at least one second apart, and stops after 250
eligible deletions or its 20-minute time budget. The Actions job summary reports deletions and protected
exceptions; it does not create persistent artifacts.

This follows [GitHub's deployment deletion contract](https://docs.github.com/en/rest/deployments/deployments#delete-a-deployment),
which rejects deletion of active records when other deployments exist. A rejection
is preserved as an exception. The narrow old Pages retirement follows
[GitHub's status API](https://docs.github.com/en/rest/deployments/statuses#create-a-deployment-status),
with automatic status changes for other records explicitly disabled. Repeated daily
runs finish a larger backlog without widening permissions or bypassing protections.

For a local preview, supply a new journal path outside the repository:

```sh
python scripts/vllm/prune_deployment_history.py \
  --repository AndreasKaratzas/vllm-ci-dashboard \
  --journal /tmp/dashboard-deployment-retention-preview.jsonl
```
