import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

// The synthetic docker_builds.json build_fixture_site.py published into _site.
const PAGES_FILE = resolve(dirname(fileURLToPath(import.meta.url)), '../../../_site/data/vllm/ci/docker_builds.json');
export const dockerPages = () => JSON.parse(readFileSync(PAGES_FILE, 'utf8'));

// The docker-builds-data branch candidate; the suite never reads the real branch.
export const DOCKER_LIVE = 'https://raw.githubusercontent.com/AndreasKaratzas/vllm-ci-dashboard/docker-builds-data/data/vllm/ci/docker_builds.json';

const fulfill = (body) => (route) =>
  route.fulfill({ body: typeof body === 'string' ? body : JSON.stringify(body), contentType: 'application/json', headers: { 'access-control-allow-origin': '*' } });

/** Serves `live` (default: the Pages copy) on the live branch URL and, when given, `pages` on the Pages path. */
export async function routeDocker(page, { live, pages } = {}) {
  await page.route(`${DOCKER_LIVE}*`, fulfill(live ?? readFileSync(PAGES_FILE, 'utf8')));
  if (pages) await page.route('**/data/vllm/ci/docker_builds.json*', fulfill(pages));
}

/** The committed not-collected seed shape, generated at `at`. */
export function dockerSeed(at) {
  const columns = ['id', 'build', 'pipeline', 'step', 'state', 'source', 'retried', 'created', 'wait_s', 'run_s', 'exit'];
  return {
    ...dockerPages(),
    generated_at: at,
    window: { days: 30, from: at, to: at },
    coverage: { status: 'not_collected', refetch_hours: 10, pipelines: {} },
    jobs: { n: 0, ...Object.fromEntries(columns.map((name) => [name, []])) },
  };
}
