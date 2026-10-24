# Contributing

This repository builds the vLLM AMD CI dashboard: collectors that read
Buildkite and GitHub, the publication workflows that assemble the Pages site,
and the Vue frontend.

## Layout

- `scripts/` and `scripts/vllm/`: collectors, watchers, snapshot builders, and
  publication tooling. `scripts/vllm/README.md` describes each data product.
- `web/`: the Vue 3 frontend. `npm run build` writes `docs/index.html` and
  `docs/v2/`, which are committed (see `web/README.md`).
- `docs/`: static site shell, including the classic renderer.
- `config/`: CI ownership, parity policy, request and storage budgets, and
  tracked projects.
- `tests/`: pytest suites; `tests/browser/` holds the Playwright suite.
- `.github/workflows/`: CI, the hourly publication lane, and the monitors.

## Workflow

1. Branch from `main` and open a pull request against `main`.
2. Keep each pull request focused; describe the motivation and the tests you ran.
3. Sign off every commit (`git commit -s`) to certify the
   [Developer Certificate of Origin](https://developercertificate.org/).

## Running the checks

Python is pinned to 3.12.13 and installs use `constraints.txt`:

```bash
uv venv --python 3.12.13 .venv
uv pip install --python .venv/bin/python -c constraints.txt -e '.[dev]'
.venv/bin/python -m pytest tests/ -m 'not live_data' -q
.venv/bin/ruff check scripts tests
.venv/bin/mypy scripts/vllm/ci
```

Unset `BUILDKITE_TOKEN` and `BUILDKITE_API_TOKEN` when running tests or local
tools; the Buildkite request guard exits with status 78 when it sees a token
outside a guarded workflow. Live Buildkite collection runs only through the
workflows.

Frontend (Node 22):

```bash
cd web
npm ci
npm run typecheck
npm test
npm run build   # commit the regenerated docs/index.html and docs/v2
```

Browser suite:

```bash
cd tests/browser
npm ci
npx playwright install chromium
npm test
```

Spelling is checked with cspell from `tools/spellcheck`; see the `lint` job in
`.github/workflows/ci.yml` for the exact file list.
