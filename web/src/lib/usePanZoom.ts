// web/src/lib/usePanZoom.ts
/**
 * Lightweight pan/zoom hook for a fixed-size <img> inside a relatively-positioned
 * wrapper. NOT OpenSeadragon (per Q-U3).
 *
 * Returns:
 *   wrapperProps — onWheel + onMouseDown + onMouseMove + onMouseUp.
 *   imgStyle     — { transform, cursor }.
 *   reset        — function that returns to scale=1, translate=0.
 */
import { useCallback, useRef, useState } from 'react'
import type { CSSProperties, MouseEvent as ReactMouseEvent, WheelEvent as ReactWheelEvent } from 'react'

interface State {
  scale: number
  tx: number
  ty: number
}

export function usePanZoom() {
  const [state, setState] = useState<State>({ scale: 1, tx: 0, ty: 0 })
  const dragging = useRef(false)
  const last = useRef({ x: 0, y: 0 })

  const onWheel = useCallback((e: ReactWheelEvent) => {
    e.preventDefault()
    const delta = e.deltaY < 0 ? 1.1 : 1 / 1.1
    setState((s) => ({ ...s, scale: Math.max(0.25, Math.min(8, s.scale * delta)) }))
  }, [])

  const onMouseDown = useCallback((e: ReactMouseEvent) => {
    dragging.current = true
    last.current = { x: e.clientX, y: e.clientY }
  }, [])

  const onMouseMove = useCallback((e: ReactMouseEvent) => {
    if (!dragging.current) return
    const dx = e.clientX - last.current.x
    const dy = e.clientY - last.current.y
    last.current = { x: e.clientX, y: e.clientY }
    setState((s) => ({ ...s, tx: s.tx + dx, ty: s.ty + dy }))
  }, [])

  const onMouseUp = useCallback(() => {
    dragging.current = false
  }, [])

  const reset = useCallback(() => {
    setState({ scale: 1, tx: 0, ty: 0 })
  }, [])

  return {
    wrapperProps: { onWheel, onMouseDown, onMouseMove, onMouseUp, onMouseLeave: onMouseUp },
    imgStyle: {
      transform: `translate(${state.tx}px, ${state.ty}px) scale(${state.scale})`,
      cursor: dragging.current ? 'grabbing' : 'grab',
      transformOrigin: '0 0',
    } as CSSProperties,
    reset,
    state,
  }
}
