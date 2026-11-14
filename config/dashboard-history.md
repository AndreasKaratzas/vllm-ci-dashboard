# Fork history and bounded state

Generated data does not append to `main`. Canonical collection rotates two
parentless state commits, while Pages, queue, DNS, lifecycle and request-budget
branches also keep bounded snapshots. Code PRs use squash merges. These rules
prevent repeated large analytics snapshots from accumulating in code history.

For an authorized fork-history consolidation, first merge and validate the
functional changes. Require two real saved generations with the same executable
source as `main`; an ordinary auxiliary publication after full collection can
provide the second slot without repeating collection. Keep their actual source
timestamps and distinct data. Stop overlapping state/Pages writers for the short
publication window, fetch origin and upstream, and record all branch tips.

Run preparation with unused paths outside `.git`:

```sh
PYTHONPATH=scripts python -m vllm.flatten_dashboard_history prepare \
  --plan /path/outside/repository/rewrite.json \
  --backup /path/outside/repository/recovery.bundle
```

Review the plan and keep the recovery bundle. Preparation changes no refs or
checkout files. It builds a new `main` with the identical tree and exactly one
parent, upstream `main`; rebinds both source-equivalent state slots; and updates
only the Pages generation marker. Generated files, collection times, source refs,
public projection hashes and budget ledgers stay intact. A state with different
source, a mismatched Pages proof, or missing backup refuses publication.

```sh
PYTHONPATH=scripts python -m vllm.flatten_dashboard_history publish \
  --plan /path/outside/repository/rewrite.json \
  --backup /path/outside/repository/recovery.bundle
```

Publication leases all four changed refs and pushes them atomically. Changed
remote tips or upstream `main` require fresh preparation. Verify the four remote
tips, live generation marker, exact public projection and upstream ahead count
after Pages deployment. Resume ordinary writers after verification.

Remove only feature branches whose exact tip is proved to be a merged PR head.
Preserve unmerged work and its external archive. Old local branches, remote
tracking refs, worktrees and reflogs can retain the original history even after
the force push. A fresh single-branch clone is the simplest way to avoid fetching
it; explicitly fetch operational refs as needed. In an existing clone, archive
first, remove reviewed obsolete refs, then expire reflogs and run ordinary Git
garbage collection when no Git writer is running.

GitHub's read-only pull-request refs can retain old objects. Rewriting `main`
does not promise immediate server-side storage reclamation. Keep local recovery
bundles outside `.git` so the active object store can shrink without losing them.
