import AxeBuilder from '@axe-core/playwright';
import { expect } from '@playwright/test';

// Quality gates every page must pass: no page errors or console errors and
// warnings, a pointer cursor on every visible clickable control (clickable
// table rows and chart bars included), no horizontal page overflow, and
// no serious or critical accessibility violations in the current theme.

const CLICKABLE = [
  'button:not(:disabled), [role="tab"], [role="button"], [role="option"], [role="menuitem"], a[href], summary, select',
  'tr[data-clickable] td',
  '[data-slot="bar-plot"][data-clickable] path[class*="-bar"]',
].join(', ');

export function collectErrors(page) {
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => ['error', 'warning'].includes(message.type()) && errors.push(`${message.type()}: ${message.text()}`));
  return errors;
}

export async function setTheme(page, theme) {
  await page.addInitScript((value) => localStorage.setItem('theme', value), theme);
}

export async function expectPointerCursors(page) {
  const offenders = await page.locator(CLICKABLE).evaluateAll((nodes) =>
    nodes
      .filter((node) => {
        const box = node.getBoundingClientRect();
        return box.width > 0 && box.height > 0 && getComputedStyle(node).visibility !== 'hidden';
      })
      .filter((node) => getComputedStyle(node).cursor !== 'pointer')
      .map((node) => `${node.tagName.toLowerCase()}[${node.getAttribute('role') ?? ''}] ${node.textContent.trim().slice(0, 40)}`),
  );
  expect(offenders, 'clickable controls without a pointer cursor').toEqual([]);
}

export async function expectNoHorizontalOverflow(page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow, 'page scrolls sideways').toBeLessThanOrEqual(0);
}

export async function expectAccessible(page) {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
  const serious = results.violations
    .filter((violation) => violation.impact === 'serious' || violation.impact === 'critical')
    .map((violation) => `${violation.id}: ${violation.nodes.map((node) => node.target.join(' ')).slice(0, 3).join(', ')}`);
  expect(serious, 'serious accessibility violations').toEqual([]);
}

/** Waits for enter animations (fades, slides) so contrast is measured on the settled page. */
export async function settleAnimations(page) {
  await page.waitForFunction(() =>
    document.getAnimations().every((animation) => {
      const timing = animation.effect?.getComputedTiming();
      return timing?.iterations === Infinity || animation.playState !== 'running';
    }),
  );
}

/** Every gate on the current page, as rendered at the current viewport and theme. */
export async function expectQuality(page, errors) {
  await settleAnimations(page);
  await expectPointerCursors(page);
  await expectNoHorizontalOverflow(page);
  await expectAccessible(page);
  expect(errors, 'page or console errors').toEqual([]);
}
