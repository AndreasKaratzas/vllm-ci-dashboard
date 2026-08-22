/**
 * Lightweight dashboard navigation shell for the v2 operations UI.
 *
 * Navigation stays separate so every Operations route becomes interactive
 * before its data shard is loaded.
 */
(function () {
  'use strict';

  function hasTab(id) {
    return Boolean(id && document.getElementById('tab-' + id));
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
  const sidebar = document.getElementById('sidebar');
  const menuToggle = document.getElementById('ops-menu-toggle');
  const navBackdrop = document.getElementById('ops-nav-backdrop');
  const mainContent = document.getElementById('main-content');
  const pageFooter = document.querySelector('body > footer');
  const themeToggle = document.getElementById('theme-toggle');
  function setDrawer(open) {
    if (!sidebar || !menuToggle) return;
    sidebar.classList.toggle('open', Boolean(open));
    document.body.classList.toggle('ops-drawer-open', Boolean(open));
    menuToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    menuToggle.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
    [mainContent, pageFooter, themeToggle].forEach(function (element) {
      if (!element) return;
      element.inert = Boolean(open);
    });
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
    if (!document.body.classList.contains('ops-drawer-open')) return;
    if (event.key === 'Escape') {
      setDrawer(false);
      return;
    }
    if (event.key === 'Tab') {
      const controls = [menuToggle].concat(Array.from(sidebar.querySelectorAll(
        'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])'
      ))).filter(function (control, index, all) {
        return control && all.indexOf(control) === index && control.offsetParent !== null;
      });
      if (!controls.length) return;
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  });
})();
