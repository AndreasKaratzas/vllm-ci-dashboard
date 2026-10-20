import { routeSection } from './sections.mjs';

const failed = (count, number) =>
  Array.from({ length: count }, (_, index) => ({
    name: `:amd: (MI300) Fixture job ${index}`,
    display_name: `Fixture job ${index}`,
    state: 'failed',
    queue: 'amd_mi300_1',
    build_number: number,
    url: `https://buildkite.com/vllm/ci/builds/${number}/steps/canvas?jid=fixture-${index}`,
  }));

/** The fixture nightly section with its newest nightly in one of the states the collector publishes. */
export function nightlyInState(nightly, kind) {
  const section = structuredClone(nightly);
  const pipeline = section.nightly.pipelines[0];
  const [head] = pipeline.builds;
  const number = head.number + 1;
  const next = (extra) => ({
    ...head,
    number,
    created_at: new Date(Date.parse(head.created_at) + 21 * 3600 * 1000).toISOString(),
    url: `https://buildkite.com/vllm/ci/builds/${number}`,
    total_groups: 0,
    has_test_results: false,
    transition_eligible: false,
    test_jobs_blocked: 0,
    failed_groups: [],
    soft_failed_groups: [],
    failure_movement: { ...head.failure_movement, available: false },
    transitions: undefined,
    amd_complete: false,
    non_amd_jobs_active: 0,
    active_retry: false,
    ...extra,
  });
  if (kind === 'amd-complete') Object.assign(head, { source_state: 'failing', amd_complete: true, non_amd_jobs_active: 1 });
  else if (kind === 'awaiting') pipeline.builds.unshift(next({ state: 'failing', source_state: 'failing', failed_groups: failed(2, number) }));
  else if (kind === 'retry') pipeline.builds.unshift(next({ state: 'failing', source_state: 'failing', active_retry: true }));
  else if (kind === 'blocked') pipeline.builds.unshift(next({ state: 'failed', source_state: 'failed', test_jobs_blocked: 6 }));
  else if (kind === 'no-results') pipeline.builds.unshift(next({ state: 'failed', source_state: 'failed', failed_groups: failed(1, number) }));
  else throw new Error(`unknown nightly state ${kind}`);
  return section;
}

/** Serve the fixture nightly section with its newest nightly in `kind`. */
export async function routeNightlyState(page, kind) {
  const response = await page.request.get('/data/vllm/ci/operations_v2/nightly.json');
  await routeSection(page, 'nightly', nightlyInState(await response.json(), kind));
}
