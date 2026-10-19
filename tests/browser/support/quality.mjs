import AxeBuilder from '@axe-core/playwright';
import { expect } from '@playwright/test';

// Quality gates every page must pass: no page or console errors, a pointer
// cursor on every visible clickable control, no horizontal page overflow, and
// no serious or critical accessibility violations in the current theme.

const CLICKABLE = 'button:not(:disabled), [role="tab"], [role="button"], [role="option"], [role="menuitem"], a[href], summary, select';

export function collectErrors(page) {
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => message.type() === 'error' && errors.push(message.text()));
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
      // The sidebar rail is a resize handle and keeps its resize cursor.
      .filter((node) => !node.matches('[data-slot="sidebar-rail"]') && getComputedStyle(node).cursor !== 'pointer')
      .map((node) => `${node.tagName.toLowerCase()}[${node.getAttribute('role') ?? ''}] ${node.textContent.trim().slice(0, 40)}`),
  );
  expect(offenders, 'clickable controls without a pointer cursor').toEqual([]);
}

export async function expectNoHorizontalOverflow(page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow, 'page scrolls sideways').toBeLessThanOrEqual(0);
}

export async function expectAccessible(page) {
  // Entrance fades would otherwise be measured half transparent.
  await page.evaluate(() =>
    Promise.all(document.getAnimations().filter((a) => a.effect?.getComputedTiming().iterations !== Infinity).map((a) => a.finished.catch(() => {}))),
  );
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
  const serious = results.violations
    .filter((violation) => violation.impact === 'serious' || violation.impact === 'critical')
    .map((violation) => `${violation.id}: ${violation.nodes.map((node) => node.target.join(' ')).slice(0, 3).join(', ')}`);
  expect(serious, 'serious accessibility violations').toEqual([]);
}

/** Every gate on the current page, as rendered at the current viewport and theme. */
export async function expectQuality(page, errors) {
  await expectPointerCursors(page);
  await expectNoHorizontalOverflow(page);
  await expectAccessible(page);
  expect(errors, 'page or console errors').toEqual([]);
}
