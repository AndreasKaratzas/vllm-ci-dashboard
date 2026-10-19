# Dashboard v2 (Vue 3 + shadcn-vue)

The v2 frontend is built from `web/` into `docs/v2/`, which Pages publishes at
`/v2/` beside the classic dashboard. The built bundle is committed so the hourly
publication lane never needs Node; CI fails if it differs from a fresh build.

```bash
cd web
npm ci
npm run dev        # proxies /data to the live Pages site
npm test           # parity tests against the classic renderer
npm run build      # typecheck, then write docs/v2 (commit the result)
```

Views are ported one at a time. Until a view is ported it links to the classic
dashboard. Domain logic ported from `docs/assets/js/ops-v2.js` lives in
`src/domain/` and is checked by `tests/parity.test.ts`, which runs the classic
functions and the ports on the same real snapshot plus generated variants.
