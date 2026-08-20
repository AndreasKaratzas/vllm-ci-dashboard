(function () {
  'use strict';

  const STATUS_URL = 'data/vllm/ci/publication_status.json';
  const HEALTHY_MAX_AGE_MS = 3 * 60 * 60 * 1000;
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
    if (payload.mode === 'current' && payload.status === 'healthy') {
      const generatedAt = safeTimestamp(payload.generated_at);
      if (!generatedAt) return MODE_VIEWS.unavailable;
      const current = Number.isFinite(Number(nowMs)) ? Number(nowMs) : Date.now();
      if (current - Date.parse(generatedAt) > HEALTHY_MAX_AGE_MS) return MODE_VIEWS.stale;
      return null;
    }
    if (payload.mode === 'current' && payload.status === 'degraded') {
      return MODE_VIEWS.degraded;
    }
    return MODE_VIEWS[payload.mode] || MODE_VIEWS.unavailable;
  }

  function safeTimestamp(value) {
    if (typeof value !== 'string' || !value || value.length > 64) return '';
    return Number.isNaN(Date.parse(value)) ? '' : value;
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

  async function load() {
    try {
      const response = await fetch(STATUS_URL, {cache: 'no-store'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      render(await response.json());
    } catch (_error) {
      render({mode: 'unavailable', status: 'unknown'});
    }
  }

  window.PublicationStatusBanner = Object.freeze({load, render, viewFor});
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', load, {once: true});
  } else {
    load();
  }
})();
