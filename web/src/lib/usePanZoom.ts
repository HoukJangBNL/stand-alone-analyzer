// web/src/lib/usePanZoom.ts
/**
 * Lightweight pan/zoom hook for a fixed-size <img> inside a relatively-positioned
 * wrapper. NOT OpenSeadragon (per Q-U3).
 *
 * The wheel listener is attached via native addEventListener with
 * { passive: false } because React attaches wheel handlers passively by
 * default — without this, e.preventDefault() is silently ignored and the
 * page scrolls while the user tries to zoom.
 *
 * Returns:
 *   wrapperRef   — attach to the wrapper element so the wheel listener binds.
 *   wrapperProps — onMouseDown + onMouseMove + onMouseUp + onMouseLeave.
 *   imgStyle     — { transform, cursor, transformOrigin, userSelect }.
 *   reset        — function that returns to scale=1, translate=0.
 *   state        — current { scale, tx, ty, dragging }.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import type { CSSProperties, MouseEvent as ReactMouseEvent } from 'react'

interface State {
  scale: number
  tx: number
  ty: number
  dragging: boolean
}

export function usePanZoom() {
  const [state, setState] = useState<State>({ scale: 1, tx: 0, ty: 0, dragging: false })
  const wrapperRef = useRef<HTMLDivElement | null>(null)
  const draggingRef = useRef(false)
  const last = useRef({ x: 0, y: 0 })

  useEffect(() => {
    const el = wrapperRef.current
    if (!el) return
    const handler = (e: WheelEvent) => {
      e.preventDefault()
      const delta = e.deltaY < 0 ? 1.1 : 1 / 1.1
      setState((s) => ({ ...s, scale: Math.max(0.25, Math.min(8, s.scale * delta)) }))
    }
    el.addEventListener('wheel', handler, { passive: false })
    return () => el.removeEventListener('wheel', handler)
  }, [])

  const onMouseDown = useCallback((e: ReactMouseEvent) => {
    draggingRef.current = true
    last.current = { x: e.clientX, y: e.clientY }
    setState((s) => ({ ...s, dragging: true }))
  }, [])

  const onMouseMove = useCallback((e: ReactMouseEvent) => {
    if (!draggingRef.current) return
    const dx = e.clientX - last.current.x
    const dy = e.clientY - last.current.y
    last.current = { x: e.clientX, y: e.clientY }
    setState((s) => ({ ...s, tx: s.tx + dx, ty: s.ty + dy }))
  }, [])

  const onMouseUp = useCallback(() => {
    if (!draggingRef.current) return
    draggingRef.current = false
    setState((s) => ({ ...s, dragging: false }))
  }, [])

  const reset = useCallback(() => {
    setState((s) => ({ ...s, scale: 1, tx: 0, ty: 0 }))
  }, [])

  return {
    wrapperRef,
    wrapperProps: { onMouseDown, onMouseMove, onMouseUp, onMouseLeave: onMouseUp },
    imgStyle: {
      transform: `translate(${state.tx}px, ${state.ty}px) scale(${state.scale})`,
      cursor: state.dragging ? 'grabbing' : 'grab',
      transformOrigin: '0 0',
      userSelect: 'none',
      WebkitUserSelect: 'none',
    } as CSSProperties,
    reset,
    state,
  }
}
