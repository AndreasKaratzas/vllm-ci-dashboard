// ═══════════════════════ TAB REGISTRY ═══════════════════════
// One source of truth for the public dashboard shell. Navigation and tests
// read this metadata instead of hard-coding parallel lists.
var DashboardTabs = (function() {
  var _tabs = [
    { id: 'projects', label: 'Home', section: 'core', family: 'static' },
    { id: 'ci-health', label: 'CI Health', section: 'vLLM', family: 'ci' },
    { id: 'ci-analytics', label: 'CI Analytics', section: 'vLLM', family: 'ci' },
    {
      id: 'ci-perf-eval',
      label: 'Perf Eval',
      section: 'vLLM',
      family: 'ci',
      description: 'AMD nightly performance + accuracy from the vllm/perf-eval pipeline',
    },
    { id: 'ci-omni', label: 'Omni CI', section: 'vLLM', family: 'ci' },
  ];
  var _byId = {};
  for (var i = 0; i < _tabs.length; i++) {
    _byId[_tabs[i].id] = _tabs[i];
  }

  function _clone(tab) {
    return tab ? Object.assign({}, tab) : null;
  }

  function list() {
    return _tabs.map(_clone);
  }

  function get(id) {
    return _clone(_byId[id]);
  }

  function getSectionTabs(section, family) {
    return _tabs.filter(function(tab) {
      return tab.section === section && (!family || tab.family === family);
    }).map(_clone);
  }

  return {
    list: list,
    get: get,
    getSectionTabs: getSectionTabs,
  };
})();
window.__dashboardTabs = DashboardTabs;

var _ciSections = [];

function setCISectionExpanded(section, expanded) {
  if (!section) return;
  section.expanded = Boolean(expanded);
  section.container.style.maxHeight = section.expanded ? (section.tabs.length * 40 + 10) + 'px' : '0';
  section.container.style.opacity = section.expanded ? '1' : '0';
  section.header.classList.toggle('ci-fw-expanded', section.expanded);
  section.header.setAttribute('aria-expanded', section.expanded ? 'true' : 'false');
}

function registerCISection(frameworkName, tabs) {
  var nav = document.querySelector('#sidebar-nav') || document.querySelector('nav');
  if (!nav) return;

  var toolsLabel = null;
  var labels = nav.querySelectorAll('.nav-section-label');
  for (var i = 0; i < labels.length; i++) {
    if (labels[i].textContent.trim().toLowerCase() === 'tools') {
      toolsLabel = labels[i];
      break;
    }
  }

  // Create clickable framework header
  var header = document.createElement('div');
  header.className = 'ci-framework-header';
  header.setAttribute('data-framework', frameworkName);
  var hasTabs = tabs && tabs.length > 0;
  var sectionId = 'ci-section-' + String(frameworkName || 'framework').toLowerCase().replace(/[^a-z0-9]+/g, '-');
  if (hasTabs) {
    header.setAttribute('role', 'button');
    header.setAttribute('tabindex', '0');
    header.setAttribute('aria-expanded', 'false');
    header.setAttribute('aria-controls', sectionId);
  }
  header.innerHTML = '<span class="ci-fw-name">' + frameworkName + ' CI</span>' +
    (hasTabs ? '<span class="ci-fw-arrow">&#9656;</span>' : '<span class="ci-fw-empty">—</span>');
  if (toolsLabel) nav.insertBefore(header, toolsLabel);
  else nav.appendChild(header);

  // Create tab container (hidden by default)
  var tabContainer = document.createElement('div');
  tabContainer.className = 'ci-tab-group';
  tabContainer.id = sectionId;
  tabContainer.style.maxHeight = '0';
  tabContainer.style.overflow = 'hidden';
  tabContainer.style.transition = 'max-height 0.3s ease, opacity 0.3s ease';
  tabContainer.style.opacity = '0';
  if (toolsLabel) nav.insertBefore(tabContainer, toolsLabel);
  else nav.appendChild(tabContainer);

  var sectionInfo = { name: frameworkName, header: header, container: tabContainer, tabs: tabs || [], expanded: false };
  _ciSections.push(sectionInfo);

  var main = document.getElementById('main-content');

  if (hasTabs) {
    for (var t = 0; t < tabs.length; t++) {
      var tab = tabs[t];
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'nav-btn ci-sub-btn';
      btn.setAttribute('data-tab', tab.id);
      if (tab.description) btn.setAttribute('data-tab-description', tab.description);
      var label = document.createElement('span');
      label.className = 'nav-btn-label';
      label.textContent = tab.label;
      btn.appendChild(label);
      tabContainer.appendChild(btn);

      // Create tab panel
      var panel = document.createElement('div');
      panel.id = 'tab-' + tab.id;
      panel.className = 'tab-panel';
      var section = document.createElement('section');
      section.id = tab.id + '-view';
      panel.appendChild(section);
      if (main) main.appendChild(panel);
    }
  }

  // Header click: expand this, collapse others
  function toggleSection() {
    if (!hasTabs) return;
    var isExpanded = sectionInfo.expanded;
    // Collapse all
    for (var i = 0; i < _ciSections.length; i++) {
      setCISectionExpanded(_ciSections[i], false);
    }
    // Toggle this one
    if (!isExpanded) setCISectionExpanded(sectionInfo, true);
  }
  header.addEventListener('click', toggleSection);
  header.addEventListener('keydown', function(event) {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    toggleSection();
  });
}

// Register all framework CI sections from the shared tab registry.
registerCISection('vLLM', DashboardTabs.getSectionTabs('vLLM', 'ci'));
// Other framework CI sections removed — vLLM only

// Auto-expand vLLM on load (it has tabs)
(function() {
  for (var i = 0; i < _ciSections.length; i++) {
    var s = _ciSections[i];
    if (s.name === 'vLLM' && s.tabs.length) {
      setCISectionExpanded(s, true);
      break;
    }
  }
})();
