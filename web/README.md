# Dashboard v2 (Vue 3 + shadcn-vue)

The v2 frontend is the site root: `web/` builds `docs/index.html` plus
`docs/v2/`. The classic dashboard stays at `docs/classic.html` for views not
ported yet, and old `?ops_*#<tab>` links are routed to whichever page serves
them (`src/app/oldLinks.ts`). The built bundle is committed so the hourly
publication lane never needs Node; CI fails if it differs from a fresh build.

```bash
cd web
npm ci
npm run dev        # proxies /data to the live Pages site
npm test           # parity tests against the classic renderer
npm run build      # typecheck, then write docs/index.html and docs/v2 (commit both)
```

Views are ported one at a time. Until a view is ported it links to the classic
page. Domain logic ported from `docs/assets/js/ops-v2.js` lives in
`src/domain/` and is checked by `tests/parity.test.ts`, which runs the classic
functions and the ports on the same real snapshot plus generated variants.

## View counter

`config/view_counter.json` turns on a [GoatCounter](https://www.goatcounter.com)
view count in the sidebar footer. With an empty `goatcounter_code` nothing is
sent and the policy in `index.html` is unchanged. With a code set, the build adds
`https://<code>.goatcounter.com` to `connect-src`, and the app counts the first
load and each route change only on `site_url`: PR previews, local dev, tests and
browser automation never count. GoatCounter sets no cookies. The sidebar shows views over the last 7 days,
from `/counter/TOTAL.json?start=week` (needs "Allow adding visitor counts on your website"
in the GoatCounter site settings, cached up to four hours); if the request is
blocked the count is just not shown.
