#!/usr/bin/env bash
# Replay one hourly refresh-only publication (queue or dns_health) of the real
# dashboard state with this checkout's code and no Buildkite access. Fails when
# the code would leave the currently published state unpublishable.
set -euo pipefail

SURFACE="$1"
CODE_SHA=$(git rev-parse --verify 'HEAD^{commit}')
WORK=$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/published-state-replay.XXXXXX")
export PYTHONPATH="$PWD/scripts" CI=1 MAX_DATA_AGE_HOURS=36
unset BUILDKITE_TOKEN BUILDKITE_API_TOKEN
python scripts/vllm/buildkite_request_guard.py initialize \
  --file "$WORK/buildkite-guard.json" --attempt-id published-state-replay --allowance 0
export BUILDKITE_REQUEST_GUARD_FILE="$WORK/buildkite-guard.json" \
  BUILDKITE_REQUEST_GUARD_ATTEMPT_ID=published-state-replay \
  BUILDKITE_REQUEST_GUARD_ALLOWANCE=0

if [ -z "${REPLAY_STATE_REF:-}" ]; then
  for branch in dashboard-state queue-data dns-health-data; do
    git fetch --no-tags --depth=1 origin "+refs/heads/$branch:refs/remotes/origin/$branch"
  done
fi
STATE_SHA=$(git rev-parse --verify "${REPLAY_STATE_REF:-origin/dashboard-state}^{commit}")
echo "Replaying $SURFACE refresh of state $STATE_SHA with code $CODE_SHA."

GIT_NO_LAZY_FETCH=1 python scripts/vllm/dashboard_state.py materialize --ref "$STATE_SHA"
git restore --source="$CODE_SHA" --worktree -- \
  dashboards/dashboard-audit.md dashboards/dashboard-cleanup.md
if [ ! -f data/vllm/ci/docker_builds.json ]; then
  git restore --source="$CODE_SHA" --worktree -- data/vllm/ci/docker_builds.json
fi
if [ ! -f data/vllm/ci/amd_queue_registry.json ]; then
  git restore --source="$CODE_SHA" --worktree -- data/vllm/ci/amd_queue_registry.json
fi

case "$SURFACE" in
  queue)
    git archive origin/queue-data | tar -x
    python scripts/vllm/build_queue_section.py --input-dir data/vllm/ci
    python scripts/vllm/audit_dashboard_data.py --queue-only
    python -S scripts/vllm/restore_queue_projections.py --baseline-ref "$STATE_SHA"
    ;;
  dns_health)
    git show origin/dns-health-data:data/vllm/ci/dns_failures.json > "$WORK/dns_failures.json"
    python -S scripts/vllm/audit_dashboard_data.py --dns-only --dns-path "$WORK/dns_failures.json"
    install -m 0644 "$WORK/dns_failures.json" data/vllm/ci/dns_failures.json
    ;;
  *)
    echo "usage: $0 queue|dns_health" >&2
    exit 2
    ;;
esac

GITHUB_OUTPUT="$WORK/selector.out" python scripts/vllm/select_publication_surfaces.py \
  --baseline-ref "$STATE_SHA" \
  --candidate-code-ref "$CODE_SHA" \
  --refresh-only-surface "$SURFACE"
python - <<'PY'
from pathlib import Path
from vllm.publication_surfaces import PRE_VIEW_RETIREMENT_SURFACE_SPECS, RETIRED_SURFACES, RETIRED_RUNTIME_PARITY_PATHS, RETIRED_PARITY_OVERRIDE_PATHS, RETIRED_TICKETING_PATHS
for surface in sorted(RETIRED_SURFACES):
    for relative in PRE_VIEW_RETIREMENT_SURFACE_SPECS[surface].required_paths:
        Path(relative).unlink(missing_ok=True)
for relative in RETIRED_RUNTIME_PARITY_PATHS | RETIRED_PARITY_OVERRIDE_PATHS | RETIRED_TICKETING_PATHS:
    Path(relative).unlink(missing_ok=True)
for name in ("gating", "trajectory", "comparison_retry_evidence", "ownership"):
    Path(f"data/vllm/ci/operations_v2/{name}.json").unlink(missing_ok=True)
PY
python scripts/vllm/build_operations_snapshot.py \
  --input-dir data/vllm/ci --output data/vllm/ci/operations_v2.json.gz
python scripts/render.py

git add -A -- data/ dashboards/ README.md
python scripts/vllm/check_git_blob_sizes.py
python scripts/vllm/check_dashboard_state_storage_budget.py
SOURCE_REFS=()
while IFS= read -r source_ref; do
  SOURCE_REFS+=(--source-ref "$source_ref")
done < <(python - "$SURFACE" <<'PY'
import json
import subprocess
import sys

refs = json.load(open("data/vllm/ci/dashboard_state.json")).get("source_refs", {})
branch = {"queue": "queue-data", "dns_health": "dns-health-data"}[sys.argv[1]]
refs[branch] = subprocess.check_output(
    ["git", "rev-parse", f"refs/remotes/origin/{branch}^{{commit}}"], text=True
).strip()
for item in sorted(refs.items()):
    print("=".join(item))
PY
)
python scripts/vllm/dashboard_state.py prepare \
  --code-sha "$CODE_SHA" \
  --generation-id "replay-$SURFACE" \
  --generated-at "$(sed -n 's/^generated_at=//p' "$WORK/selector.out")" \
  "${SOURCE_REFS[@]}"

python scripts/vllm/audit_dashboard_data.py
pytest tests/ -m live_data --tb=short
env -u BUILDKITE_REQUEST_GUARD_FILE -u BUILDKITE_REQUEST_GUARD_ATTEMPT_ID \
  -u BUILDKITE_REQUEST_GUARD_ALLOWANCE \
  pytest tests/ -m 'not live_data' -n auto -q --tb=short -o faulthandler_timeout=60

python scripts/build_site.py --cache-bust-index
python scripts/vllm/public_projection.py create \
  --site-root _site \
  --manifest _site/publication_manifest.json \
  --attestation data/vllm/ci/public_projection_attestation.json
git add -- data/vllm/ci/public_projection_attestation.json
python scripts/vllm/dashboard_state.py refresh-manifest --expected-code-sha "$CODE_SHA"
python scripts/vllm/check_dashboard_state_storage_budget.py
