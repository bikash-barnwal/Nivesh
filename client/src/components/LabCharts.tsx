import { ColorType, createChart, type IChartApi, type LineStyle, type SeriesMarker, type Time } from 'lightweight-charts'
import { useEffect, useRef } from 'react'
import type { EquityData, TradeDetail } from '../api/types'
import { REGIME_COLORS } from '../lib/colors'

/** 12,37,00,000 -> "₹1.24Cr" so the equity axis stays readable. */
export function compactRupees(v: number): string {
  const abs = Math.abs(v)
  if (abs >= 1e7) return `₹${(v / 1e7).toFixed(2)}Cr`
  if (abs >= 1e5) return `₹${(v / 1e5).toFixed(1)}L`
  return `₹${v.toFixed(0)}`
}

const BASE = {
  layout: { background: { type: ColorType.Solid, color: '#111827' }, textColor: '#8793aa' },
  grid: { vertLines: { color: '#1a2438' }, horzLines: { color: '#1a2438' } },
  rightPriceScale: { borderColor: '#243049' },
  timeScale: { borderColor: '#243049' },
} as const

/** Equity vs a rebased Nifty 500 line, with the drawdown underneath. */
export function EquityChart({ data }: { data: EquityData }) {
  const top = useRef<HTMLDivElement>(null)
  const bottom = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!top.current || !bottom.current) return
    const equityChart = createChart(top.current, {
      ...BASE,
      autoSize: true,
      localization: { priceFormatter: compactRupees },
    })
    const equity = equityChart.addAreaSeries({ lineColor: '#7aa2ff', topColor: 'rgba(122,162,255,0.25)', bottomColor: 'rgba(122,162,255,0)', lineWidth: 2 })
    equity.setData(data.curve.map((p) => ({ time: p.date as Time, value: p.equity })))
    if (data.benchmark.length) {
      const bench = equityChart.addLineSeries({ color: '#fbbf24', lineWidth: 1, priceLineVisible: false, lastValueVisible: false })
      bench.setData(data.benchmark.map((p) => ({ time: p.date as Time, value: p.value })))
    }
    equityChart.timeScale().fitContent()

    const ddChart: IChartApi = createChart(bottom.current, { ...BASE, autoSize: true })
    const dd = ddChart.addAreaSeries({ lineColor: '#f87171', topColor: 'rgba(248,113,113,0)', bottomColor: 'rgba(248,113,113,0.35)', lineWidth: 1, invertFilledArea: true })
    dd.setData(data.curve.map((p) => ({ time: p.date as Time, value: p.drawdown * 100 })))
    ddChart.timeScale().fitContent()

    // keep the two panes scrolled/zoomed together
    equityChart.timeScale().subscribeVisibleLogicalRangeChange((r) => r && ddChart.timeScale().setVisibleLogicalRange(r))
    ddChart.timeScale().subscribeVisibleLogicalRangeChange((r) => r && equityChart.timeScale().setVisibleLogicalRange(r))
    return () => {
      equityChart.remove()
      ddChart.remove()
    }
  }, [data])

  return (
    <div>
      <div className="mb-2 flex gap-4 text-xs text-muted">
        <span><span className="mr-1.5 inline-block size-2 rounded-full bg-accent align-middle" />Strategy equity</span>
        {data.benchmark.length > 0 && <span><span className="mr-1.5 inline-block size-2 rounded-full bg-warn align-middle" />Nifty 500 (rebased)</span>}
        <span className="ml-auto">Drawdown % below</span>
      </div>
      <div ref={top} className="h-72 w-full overflow-hidden rounded" />
      <div ref={bottom} className="mt-1 h-28 w-full overflow-hidden rounded" />
    </div>
  )
}

/** One trade in context: candles, SMA20/50, entry/exit markers, stop lines and the market-regime strip. */
export function TradeChart({ detail }: { detail: TradeDetail }) {
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!box.current) return
    const { trade, window } = detail
    const chart = createChart(box.current, { ...BASE, autoSize: true })
    const candles = chart.addCandlestickSeries({
      upColor: '#34d399', downColor: '#f87171', borderVisible: false, wickUpColor: '#34d399', wickDownColor: '#f87171',
    })
    candles.setData(window.map((w) => ({ time: w.date as Time, open: w.open, high: w.high, low: w.low, close: w.close })))
    for (const [key, color] of [['sma20', '#fbbf24'], ['sma50', '#34d399']] as const) {
      const line = chart.addLineSeries({ color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false })
      line.setData(window.filter((w) => w[key] != null).map((w) => ({ time: w.date as Time, value: w[key] as number })))
    }
    const volume = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol', color: '#3b4a6b' })
    volume.setData(window.map((w) => ({ time: w.date as Time, value: w.volume, color: w.close >= w.open ? '#1f6f55' : '#7f3535' })))
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } })

    const markers: SeriesMarker<Time>[] = [
      { time: trade.entry_date as Time, position: 'belowBar', color: '#34d399', shape: 'arrowUp', text: `E ₹${trade.entry_price.toFixed(2)}` },
      { time: trade.exit_date as Time, position: 'aboveBar', color: '#f87171', shape: 'arrowDown', text: `X ₹${trade.exit_price.toFixed(2)}` },
    ]
    candles.setMarkers(markers.sort((a, b) => String(a.time).localeCompare(String(b.time))))
    const dashed = 2 as LineStyle
    candles.createPriceLine({ price: trade.initial_stop, color: '#f87171', lineWidth: 1, lineStyle: dashed, title: 'initial stop' })
    if (trade.be_activated) candles.createPriceLine({ price: trade.final_stop, color: '#fbbf24', lineWidth: 1, lineStyle: dashed, title: 'breakeven stop' })
    chart.timeScale().fitContent()
    return () => chart.remove()
  }, [detail])

  const { regimes, window } = detail
  const byDate = new Map(regimes.map((r) => [r.date, r.regime]))
  return (
    <div>
      <div ref={box} className="h-96 w-full overflow-hidden rounded" />
      <div className="mt-1 flex h-2 gap-px overflow-hidden rounded" title="Market regime across this window" aria-label="Market regime context">
        {window.map((w) => (
          <span key={w.date} className="flex-1" style={{ background: REGIME_COLORS[byDate.get(w.date) ?? ''] ?? '#334155' }} title={`${w.date} · ${byDate.get(w.date) ?? 'n/a'}`} />
        ))}
      </div>
    </div>
  )
}
