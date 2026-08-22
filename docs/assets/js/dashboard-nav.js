/**
 * Lightweight dashboard navigation shell for the v2 operations UI.
 *
 * The former dashboard.js bundle also contains the retired v1 Home renderer.
 * Keeping navigation separate lets every v2 route become interactive without
 * downloading and parsing that unrelated renderer first.
 */
(function () {
  'use strict';

  function hasTab(id) {
    return Boolean(id && document.getElementById('tab-' + id));
  }

  function reapplyVisibility() {
    if (window.__authGate && typeof window.__authGate.applyTabVisibility === 'function') {
      window.__authGate.applyTabVisibility();
    }
  }

  function resetRouteScroll(panel) {
    const main = document.getElementById('main-content');
    const reset = function () {
      window.scrollTo(0, 0);
      document.documentElement.scrollTop = 0;
      document.body.scrollTop = 0;
      if (main) {
        main.scrollTop = 0;
        main.scrollLeft = 0;
      }
      if (panel) {
        panel.scrollTop = 0;
        panel.scrollLeft = 0;
      }
    };
    reset();
    if (window.requestAnimationFrame) window.requestAnimationFrame(reset);
  }

  function switchTab(target) {
    if (!hasTab(target)) target = 'projects';
    document.querySelectorAll('.nav-btn').forEach(function (button) {
      button.classList.remove('active');
      button.removeAttribute('aria-current');
    });
    document.querySelectorAll('.tab-panel').forEach(function (panel) {
      panel.classList.remove('active');
    });
    const button = document.querySelector('.nav-btn[data-tab="' + target + '"]');
    if (button) {
      button.classList.add('active');
      button.setAttribute('aria-current', 'page');
    }
    const panel = document.getElementById('tab-' + target);
    if (panel) panel.classList.add('active');
    resetRouteScroll(panel);
    if (window.OpsV2 && typeof window.OpsV2.render === 'function') {
      window.OpsV2.render(target);
    }
    reapplyVisibility();
    return target;
  }

  window.__dashboardNav = {
    switchTab: function (target, options) {
      const next = switchTab(target);
      if (!options || options.updateHash !== false) {
        const method = options && options.history === 'replace' ? 'replaceState' : 'pushState';
        history[method](null, '', location.pathname + location.search + '#' + next);
      }
      return next;
    },
  };

  const sidebarNav = document.getElementById('sidebar-nav');
  if (sidebarNav) {
    sidebarNav.addEventListener('click', function (event) {
      const button = event.target.closest && event.target.closest('.nav-btn');
      const target = button && button.getAttribute('data-tab');
      if (target) window.__dashboardNav.switchTab(target);
    });
  }

  const hash = location.hash.replace('#', '');
  if (hash && hasTab(hash)) switchTab(hash);
  let routeSyncPending = false;
  function syncLocationRoute() {
    if (routeSyncPending) return;
    routeSyncPending = true;
    setTimeout(function () {
      routeSyncPending = false;
      const target = location.hash.replace('#', '');
      if (target && hasTab(target)) switchTab(target);
    }, 0);
  }
  window.addEventListener('hashchange', syncLocationRoute);
  window.addEventListener('popstate', syncLocationRoute);
  document.addEventListener('auth:changed', reapplyVisibility);

  const sidebar = document.getElementById('sidebar');
  const menuToggle = document.getElementById('ops-menu-toggle');
  const navBackdrop = document.getElementById('ops-nav-backdrop');
  function setDrawer(open) {
    if (!sidebar || !menuToggle) return;
    sidebar.classList.toggle('open', Boolean(open));
    document.body.classList.toggle('ops-drawer-open', Boolean(open));
    menuToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    menuToggle.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
    if (open) {
      const active = sidebar.querySelector('.nav-btn.active') || sidebar.querySelector('.nav-btn');
      if (active) active.focus();
    } else {
      menuToggle.focus({preventScroll: true});
    }
  }
  if (menuToggle) {
    menuToggle.addEventListener('click', function () {
      setDrawer(!document.body.classList.contains('ops-drawer-open'));
    });
  }
  if (navBackdrop) navBackdrop.addEventListener('click', function () { setDrawer(false); });
  if (sidebarNav) {
    sidebarNav.addEventListener('click', function (event) {
      if (event.target.closest && event.target.closest('.nav-btn')
        && window.matchMedia('(max-width: 767px)').matches) {
        setDrawer(false);
      }
    });
  }
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && document.body.classList.contains('ops-drawer-open')) {
      setDrawer(false);
    }
  });
})();
