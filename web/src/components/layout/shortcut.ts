const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent)

/** The command palette shortcut as this platform writes it. */
export const SHORTCUT = isMac ? '⌘K' : 'Ctrl K'
