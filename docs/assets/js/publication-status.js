(function () {
  'use strict';

  const STATUS_URL = 'data/vllm/ci/publication_status.json';
  const HEALTHY_MAX_AGE_MS = 3 * 60 * 60 * 1000;
  const FUTURE_SKEW_MS = 5 * 60 * 1000;
  const REFRESH_INTERVAL_MS = 5 * 60 * 1000;
  const REQUEST_TIMEOUT_MS = 30 * 1000;
  let requestSerial = 0;
  let started = false;
  const MODE_VIEWS = Object.freeze({
    stale: Object.freeze({
      tone: 'is-critical',
      title: 'Dashboard snapshot is stale',
      message: 'No successful dashboard refresh has been published in the last three hours. Build and test states may have changed since this snapshot.',
    }),
    degraded: Object.freeze({
      tone: 'is-warning',
      title: 'Live data is degraded',
      message: 'One or more dashboard areas did not pass freshness or consistency checks. Current data remains visible while the issue is investigated.',
    }),
    fallback: Object.freeze({
      tone: 'is-warning',
      title: 'Using last-known-good data',
      message: 'Some dashboard areas failed validation, so a previously validated snapshot is being shown.',
    }),
    mixed: Object.freeze({
      tone: 'is-warning',
      title: 'Dashboard data is partially recovered',
      message: 'Some areas use last-known-good data while other areas are showing current degraded data.',
    }),
    blocked: Object.freeze({
      tone: 'is-critical',
      title: 'Latest dashboard refresh blocked',
      message: 'The latest data refresh did not pass publication checks. The dashboard remains on the last published snapshot.',
    }),
    unavailable: Object.freeze({
      tone: 'is-warning',
      title: 'Publication status unavailable',
      message: 'The dashboard could not verify whether this snapshot is current. Treat displayed data as potentially stale.',
    }),
  });

  function viewFor(payload, nowMs) {
    if (!payload || typeof payload !== 'object') return MODE_VIEWS.unavailable;
    // A blocked publication is the highest-priority state. Every other mode
    // must still obey the snapshot freshness SLO: an old degraded/fallback
    // record means reconciliation itself stopped running.
    if (payload.mode === 'blocked') return MODE_VIEWS.blocked;
    const generatedAt = safeTimestamp(payload.generated_at);
    if (!generatedAt) return MODE_VIEWS.unavailable;
    const current = Number.isFinite(Number(nowMs)) ? Number(nowMs) : Date.now();
    const generatedMs = Date.parse(generatedAt);
    if (generatedMs - current > FUTURE_SKEW_MS) return MODE_VIEWS.unavailable;
    if (current - generatedMs > HEALTHY_MAX_AGE_MS) {
      return MODE_VIEWS.stale;
    }
    if (payload.mode === 'current' && payload.status === 'healthy') {
      return hasHealthyCurrentContract(payload) ? null : MODE_VIEWS.unavailable;
    }
    if (payload.mode === 'current' && payload.status === 'degraded') {
      return MODE_VIEWS.degraded;
    }
    return MODE_VIEWS[payload.mode] || MODE_VIEWS.unavailable;
  }

  function safeTimestamp(value) {
    if (typeof value !== 'string' || !value || value.length > 64) return '';
    const zonedIso = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;
    if (!zonedIso.test(value) || Number.isNaN(Date.parse(value))) return '';
    return value;
  }

  function hasHealthyCurrentContract(payload) {
    return payload.schema_version === 1
      && payload.mode === 'current'
      && payload.status === 'healthy'
      && payload.degraded_since === null
      && payload.publication_blocked === false
      && payload.uses_fallback === false
      && Array.isArray(payload.affected_surfaces)
      && payload.affected_surfaces.length === 0
      && payload.affected_surface_count === 0
      && payload.fallback_surface_count === 0
      && payload.fresh_degraded_surface_count === 0;
  }

  function affectedAreas(payload) {
    if (!Array.isArray(payload.affected_surfaces)) return [];
    return [...new Set(payload.affected_surfaces
      .filter(value => typeof value === 'string' && value.length <= 64))]
      .slice(0, 8);
  }

  function render(payload) {
    const banner = document.getElementById('publication-status-banner');
    if (!banner) return;
    const view = viewFor(payload);
    if (!view) {
      banner.hidden = true;
      return;
    }

    banner.classList.remove('is-warning', 'is-critical');
    banner.classList.add(view.tone);
    banner.setAttribute('role', view.tone === 'is-critical' ? 'alert' : 'status');
    banner.querySelector('[data-publication-status-title]').textContent = view.title;
    banner.querySelector('[data-publication-status-message]').textContent = view.message;

    const details = [];
    const areas = affectedAreas(payload || {});
    if (areas.length) {
      details.push(`Affected areas: ${areas.join(', ')}.`);
    } else if (Number.isInteger(payload && payload.affected_surface_count)
               && payload.affected_surface_count > 0) {
      const count = Math.min(payload.affected_surface_count, 99);
      details.push(`${count} dashboard ${count === 1 ? 'area is' : 'areas are'} affected.`);
    }
    const degradedSince = safeTimestamp(payload && payload.degraded_since);
    const generatedAt = safeTimestamp(payload && payload.generated_at);
    if (degradedSince) details.push(`Degraded since: ${degradedSince}.`);
    if (generatedAt) details.push(`Status recorded: ${generatedAt}.`);

    const meta = banner.querySelector('[data-publication-status-meta]');
    meta.textContent = details.join(' ');
    meta.hidden = details.length === 0;
    banner.hidden = false;
  }

  function statusUrl(nowMs) {
    const current = Number.isFinite(Number(nowMs)) ? Number(nowMs) : Date.now();
    return `${STATUS_URL}?v=${Math.floor(current / (60 * 1000))}`;
  }

  async function load() {
    const request = ++requestSerial;
    const controller = typeof AbortController === 'function' ? new AbortController() : null;
    const timeout = controller && typeof window.setTimeout === 'function'
      ? window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
      : null;
    try {
      const options = {cache: 'no-store'};
      if (controller) options.signal = controller.signal;
      const response = await fetch(statusUrl(), options);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      if (request === requestSerial) render(payload);
    } catch (_error) {
      if (request === requestSerial) render({mode: 'unavailable', status: 'unknown'});
    } finally {
      if (timeout !== null && typeof window.clearTimeout === 'function') {
        window.clearTimeout(timeout);
      }
    }
  }

  function refreshWhenVisible() {
    if (document.visibilityState !== 'hidden') load();
  }

  function start() {
    if (started) return;
    started = true;
    load();
    if (typeof window.setInterval === 'function') {
      window.setInterval(load, REFRESH_INTERVAL_MS);
    }
    document.addEventListener('visibilitychange', refreshWhenVisible);
    if (typeof window.addEventListener === 'function') {
      window.addEventListener('pageshow', load);
    }
  }

  window.PublicationStatusBanner = Object.freeze({load, render, start, statusUrl, viewFor});
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, {once: true});
  } else {
    start();
  }
})();
