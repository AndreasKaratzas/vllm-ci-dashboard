import { onBeforeUnmount, ref } from 'vue'

export interface PlotPoint {
  /** Position in the scale's range, clamped to the plot. */
  local: number
  /** Horizontal offset from the host element's left edge, in CSS pixels. */
  hostPx: number
}

interface DragRangeOptions {
  /** Maps a pointer x to the plot, or null when the plot is not rendered. */
  toPlot: (clientX: number) => PlotPoint | null
  invert: (local: number) => number
  onRange: () => ((min: number, max: number) => void) | undefined
}

const DRAG_THRESHOLD_PX = 4

/** Drag across a plot to pick a range; a plain click still reaches the marks underneath. */
export function useDragRange({ toPlot, invert, onRange }: DragRangeOptions) {
  const selection = ref<{ left: number; width: number } | null>(null)
  let dragStart: number | null = null
  let dragMoved = false
  let suppressClick = false

  function onMove(e: MouseEvent) {
    if (dragStart === null) return
    const start = toPlot(dragStart)
    const end = toPlot(e.clientX)
    if (!start || !end) return
    if (Math.abs(e.clientX - dragStart) > DRAG_THRESHOLD_PX) dragMoved = true
    if (dragMoved) selection.value = { left: Math.min(start.hostPx, end.hostPx), width: Math.abs(end.hostPx - start.hostPx) }
  }
  function stop() {
    selection.value = null
    window.removeEventListener('mousemove', onMove)
    window.removeEventListener('mouseup', onUp)
  }
  function onUp(e: MouseEvent) {
    if (dragStart === null) return
    const start = toPlot(dragStart)
    const end = toPlot(e.clientX)
    dragStart = null
    stop()
    if (!dragMoved || !start || !end) return
    suppressClick = true
    const a = invert(Math.min(start.local, end.local))
    const b = invert(Math.max(start.local, end.local))
    if (Number.isFinite(a) && Number.isFinite(b) && b > a) onRange()?.(a, b)
  }
  function onDown(e: MouseEvent) {
    if (!onRange() || e.button !== 0 || !toPlot(e.clientX)) return
    dragStart = e.clientX
    dragMoved = false
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }
  function onClickCapture(e: MouseEvent) {
    if (!suppressClick) return
    suppressClick = false
    e.stopPropagation()
    e.preventDefault()
  }

  onBeforeUnmount(stop)
  return { selection, onDown, onClickCapture }
}
