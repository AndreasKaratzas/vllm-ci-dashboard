// The main-pulse-data branch overlay; the suite never reads the real branch.
export const MAIN_PULSE_LIVE = 'https://raw.githubusercontent.com/AndreasKaratzas/vllm-ci-dashboard/main-pulse-data/main_pulse_state.json';

/** Serves a main pulse overlay holding `groups` and `timeline` (default: none) on the branch URL. */
export async function routeMainPulse(page, { groups = {}, timeline = [], updatedAt = '2026-10-08T20:00:00Z' } = {}) {
  const overlay = { schema_version: 1, updated_at: updatedAt, cursor: updatedAt, groups, issue: null, issue_basis: '', timeline, processed_builds: [], last_run: {} };
  await page.route(`${MAIN_PULSE_LIVE}*`, (route) =>
    route.fulfill({ body: JSON.stringify(overlay), contentType: 'application/json', headers: { 'access-control-allow-origin': '*' } }),
  );
}
