# Porting a classic view to v2

The classic renderer (`docs/assets/js/ops-v2.js`, `amd-mirror-inventory.js`,
served by `docs/classic.html`) is the behavioral reference. A ported view must
show the same facts from the same snapshot, in the shadcn-vue style of Home and
CI Health.

## What to produce

1. **Domain logic** in `src/domain/<area>.ts`: the classic decision functions
   (filters, sorts, labels, tones, summaries) ported as pure functions with the
   same results. Keep classic names so they are easy to compare.
2. **Differential tests** in `tests/<area>.parity.test.ts`: load the classic
   functions with `loadClassic([...names], file?)` from `tests/classic.ts` and
   compare them with the ports on a real snapshot fixture plus generated
   variants (see `tests/parity.test.ts`, `tests/ciHealth.parity.test.ts`).
   Then plant at least 5 single-line bugs in your port, one at a time, and make
   sure the tests catch them; strengthen the generators until they do. Report
   how many were caught.
3. **Views** under your area's files (listed below), built from
   `src/components/ui` (shadcn-vue) and the shared ops components:
   `DataTable`, `KpiCard`, `RingCard`, `DistributionCard`, `TableSheet`,
   `ToneBadge`, `ViewState`, `BuildDetailSheet`. Charts use Unovis like
   `components/ops/NightlyChart.vue` and `components/health/MovementChart.vue`.
   Keep every drill-down the classic view offers (sheets instead of overlays).
4. **Browser tests** in `tests/browser/v2-<area>.spec.mjs`: read the same facts
   from `classic.html` and from v2 on the fixture site and require them to
   match (see the CI Health tests in `v2-smoke.spec.mjs`). Guard against both
   sides being empty.
5. **Legacy links**: translate the classic `?ops_*` params for your views in
   `src/legacy/<area>.ts`, with cases in `tests/legacyLinks.test.ts`.

## Data

- Operations sections: `useOperationSections([...names])` from
  `src/composables/useOperations.ts`; use the section names the classic
  `operationSectionNames()` uses for the view. Other files: `fetchJSON('data/...')`.
- Fixtures go in `tests/fixtures/<area>_*.json`, captured from
  `https://andreaskaratzas.github.io/vllm-ci-dashboard/data/...` and trimmed to
  the fields the ported code reads (stay under ~200 KB per area; the repo has a
  code-size budget).

## Rules

- Edit only your area's files plus new files under `src/components/<area>/`.
  Do not change shared components, `router.ts`, `legacyLinks.ts`, or another
  area's files; if a shared change is unavoidable, describe it in your report.
- Vue: an omitted optional boolean prop is `false`, not `undefined`; give it a
  default with `withDefaults` when absence should mean true.
- Do not commit `docs/` (the build output). Build to test, then restore it:
  `git checkout -- ../docs && git clean -fdq ../docs`.
- Never use `pkill -f`/`pgrep -f` with a pattern that also appears in your own
  command line; use a bracket like `"[h]ttp.server 4301"`.
- Commit with `git commit -s` and end the message with
  `Co-Authored-By: Claude <noreply@anthropic.com>`. Do not push.

## Commands

```bash
export PATH=/opt/node/bin:$PATH
export LD_LIBRARY_PATH=/tmp/akaratza_sp/chromedeps/root/usr/lib/x86_64-linux-gnu
export FONTCONFIG_FILE=/tmp/akaratza_sp/chromedeps/fonts.conf
(cd web && npm ci --no-audit --no-fund)
(cd tests/browser && npm ci --no-audit --no-fund)
(cd web && npx vue-tsc --noEmit && npx vitest run && npm run build)
env -u BUILDKITE_TOKEN -u BK_TOKEN python3 tests/browser/build_fixture_site.py
(cd tests/browser && PW_PORT=<your port> npx playwright test v2-)
```
