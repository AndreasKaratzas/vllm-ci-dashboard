import { ref } from 'vue'

const dark = ref(document.documentElement.classList.contains('dark'))

export function useTheme() {
  function toggle() {
    dark.value = !dark.value
    document.documentElement.classList.toggle('dark', dark.value)
    try {
      localStorage.setItem('theme', dark.value ? 'dark' : 'light')
    } catch {
      // Private browsing can reject storage; the toggle still applies to this page.
    }
  }
  return { dark, toggle }
}
