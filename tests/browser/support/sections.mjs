import { createHash } from 'node:crypto';

// Serve a substitute body for one operations section and publish its digest in
// the manifest, as the collector does; the app rejects sections whose sha256
// does not match the manifest.
export async function routeSection(page, name, body) {
  const text = typeof body === 'string' ? body : JSON.stringify(body);
  const sha256 = createHash('sha256').update(text).digest('hex');
  await page.route('**/operations_v2_manifest.json*', async (route) => {
    const response = await route.fetch();
    const manifest = await response.json();
    const section = manifest.sections?.[name];
    if (section) {
      section.sha256 = sha256;
      delete section.generation;
    }
    await route.fulfill({ response, json: manifest });
  });
  await page.route(`**/operations_v2/${name}.json*`, (route) =>
    route.fulfill({ body: text, contentType: 'application/json' }),
  );
}
