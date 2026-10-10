# Browser smoke tests

Run `npm ci` and `npx playwright install chromium`, then `npm test` here.
The pretest builds `_site` through the production Operations snapshot writer and
site assembler using a temporary data copy. It never modifies `data/` or calls
Buildkite/GitHub.

Current-CI fixtures are synthetic: source SHA `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`,
ten inline AMD mirrors (required, optional, and soft-fail), one missing workload,
one CUDA-only workload, and five main-CI nightlies #30005–#30001 on October 8–4,
2026. The production source parser, parity builder, matrix builder, analytics
summarizer, and latency builder derive the browser contracts. Runtime results
include passing, partial hardware coverage, failed groups, and parallel shards.
Frozen repository data seeds retained infrastructure/performance views only.

These fixtures verify rendering and evidence contracts; release acceptance checks
the deployed source pins, cohort dates, counts, status, and exact job links
independently against canonical collected data.
