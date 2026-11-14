import { readFileSync } from 'node:fs';

/** Facts captured from the retired classic page on the fixture site, keyed by case. */
export function readGolden(name) {
  return JSON.parse(readFileSync(new URL(`./${name}.json`, import.meta.url), 'utf8'));
}
