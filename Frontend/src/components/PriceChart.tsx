import {
  ColorType,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type Time,
} from 'lightweight-charts'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { FeatureRow, OhlcvRow } from '../api/types'

const SMAS = [
  { key: 'sma20', label: 'SMA 20', color: '#fbbf24' },
  { key: 'sma50', label: 'SMA 50', color: '#34d399' },
  { key: 'sma200', label: 'SMA 200', color: '#f87171' },
] as const

type SmaKey = (typeof SMAS)[number]['key']

/** Candlesticks + volume with toggleable moving averages (TradingView lightweight-charts). */
export function PriceChart({ ohlcv, features }: { ohlcv: OhlcvRow[]; features: FeatureRow[] }) {
  const container = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candles = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volume = useRef<ISeriesApi<'Histogram'> | null>(null)
  const lines = useRef<Partial<Record<SmaKey, ISeriesApi<'Line'>>>>({})
  const [visible, setVisible] = useState<Record<SmaKey, boolean>>({ sma20: true, sma50: true, sma200: false })

  const asc = useMemo(() => [...ohlcv].sort((a, b) => a.date.localeCompare(b.date)), [ohlcv])
  const smaByDate = useMemo(() => new Map(features.map((f) => [f.date, f])), [features])

  useEffect(() => {
    if (!container.current) return
    const chart = createChart(container.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: '#111827' }, textColor: '#8793aa' },
      grid: { vertLines: { color: '#1a2438' }, horzLines: { color: '#1a2438' } },
      rightPriceScale: { borderColor: '#243049' },
      timeScale: { borderColor: '#243049' },
      crosshair: { mode: 1 },
    })
    const c = chart.addCandlestickSeries({
      upColor: '#34d399',
      downColor: '#f87171',
      borderVisible: false,
      wickUpColor: '#34d399',
      wickDownColor: '#f87171',
    })
    const v = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol', color: '#3b4a6b' })
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } })
    for (const sma of SMAS) {
      lines.current[sma.key] = chart.addLineSeries({ color: sma.color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false })
    }
    chartRef.current = chart
    candles.current = c
    volume.current = v
    return () => {
      chart.remove()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    candles.current?.setData(asc.map((r) => ({ time: r.date as Time, open: r.open, high: r.high, low: r.low, close: r.close })))
    volume.current?.setData(
      asc.map((r) => ({ time: r.date as Time, value: r.volume, color: r.close >= r.open ? '#1f6f55' : '#7f3535' })),
    )
    for (const sma of SMAS) {
      const data: LineData[] = []
      for (const r of asc) {
        const value = smaByDate.get(r.date)?.[sma.key]
        if (typeof value === 'number') data.push({ time: r.date as Time, value })
      }
      lines.current[sma.key]?.setData(data)
      lines.current[sma.key]?.applyOptions({ visible: visible[sma.key] })
    }
    chartRef.current?.timeScale().fitContent()
  }, [asc, smaByDate, visible])

  return (
    <div>
      <div className="mb-2 flex gap-2">
        {SMAS.map((sma) => (
          <button
            key={sma.key}
            type="button"
            aria-pressed={visible[sma.key]}
            onClick={() => setVisible((v) => ({ ...v, [sma.key]: !v[sma.key] }))}
            className={`rounded border px-2 py-0.5 text-xs ${visible[sma.key] ? 'border-line bg-panel-2' : 'border-line/50 text-muted'}`}
          >
            <span className="mr-1.5 inline-block size-2 rounded-full align-middle" style={{ background: sma.color }} />
            {sma.label}
          </button>
        ))}
      </div>
      <div ref={container} className="h-[420px] w-full overflow-hidden rounded" />
    </div>
  )
}
