// react-plotly.js publishes minimal types; re-export the factory shape we use.
declare module 'react-plotly.js' {
  import type { ComponentType, CSSProperties } from 'react'
  export interface PlotParams {
    data: any[]
    layout?: any
    config?: any
    frames?: any[]
    style?: CSSProperties
    className?: string
    onClick?: (event: any) => void
    onSelected?: (event: any) => void
    onRelayout?: (event: any) => void
    useResizeHandler?: boolean
    revision?: number
  }
  const Plot: ComponentType<PlotParams>
  export default Plot
}
