// Seeded LCG for generated test inputs. Golden digests depend on this exact
// sequence: changing it invalidates every recorded golden.
export function rng(seed: number): () => number {
  let s = seed >>> 0
  return () => (s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32
}
