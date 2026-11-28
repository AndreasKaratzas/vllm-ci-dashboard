// Apply the saved or preferred theme before first paint (no inline script under CSP).
(function () {
  var saved = null
  try { saved = localStorage.getItem('theme') } catch (_) {}
  var dark = saved ? saved === 'dark' : !window.matchMedia('(prefers-color-scheme: light)').matches
  document.documentElement.classList.toggle('dark', dark)
})()
