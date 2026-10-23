import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { CHAR_W, endName } from "./chartText";
import { fmtNum } from "./ui";

/* Charts for the live reports. One measure at a time (never two scales on one chart); the report table below
 * is the data view, so nothing here is the only way to read a value. Series colors come from a validated
 * categorical palette (--series-1…8), assigned in a fixed order by series name. */

export type ChartData = {
  kind: "line" | "bar" | "hbar";
  x: string;
  x_label: string;
  x_kind: string;
  series: string | null;
  series_label: string | null;
  ref: boolean;
  ref_label: string | null;
  measures: { key: string; label: string; unit: string }[];
  points: Record<string, string | number | null>[];
};

const MAX_SERIES = 8;
const H = 240;
const M = { top: 14, bottom: 30, left: 52 };


function fmtValue(v: number | null | undefined, unit: string) {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  if (unit === "%") return `${v.toFixed(v < 10 ? 1 : 0)}%`;
  if (unit === "$") return `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  return fmtNum(Math.round(v * 10) / 10);
}

function fmtX(iso: string, period: string) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  if (period === "month") return d.toLocaleDateString(undefined, { month: "short", year: "numeric", timeZone: "UTC" });
  if (period === "hour") return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", timeZone: "UTC" });
  // Monitoring's charts (#397): the viewer's own time, to the minute
  if (period === "time") return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
}

const lowerFirst = (s: string) => s.charAt(0).toLowerCase() + s.slice(1);

/** Every period from the first to the last (UTC), so gaps show as gaps. Capped to keep the chart readable. */
function fillPeriods(present: string[], period: string): string[] {
  if (present.length < 2) return present;
  const step = (d: Date) => {
    const n = new Date(d);
    if (period === "month") n.setUTCMonth(n.getUTCMonth() + 1);
    else if (period === "hour") n.setUTCHours(n.getUTCHours() + 1);
    else n.setUTCDate(n.getUTCDate() + (period === "week" ? 7 : 1));
    return n;
  };
  const byTime = new Map(present.map((x) => [new Date(x).getTime(), x]));
  const out: string[] = [];
  const end = new Date(present[present.length - 1]).getTime();
  for (let d = new Date(present[0]); d.getTime() <= end && out.length < 400; d = step(d)) {
    out.push(byTime.get(d.getTime()) ?? d.toISOString());
  }
  return out.length >= present.length ? out : present;
}

/** Axis labels are whole where the step allows: 0%, 25%, $0, $0.50, 1,000. */
function fmtTick(v: number, unit: string) {
  if (unit === "%") return `${Math.round(v)}%`;
  if (unit === "$") return `$${v.toLocaleString(undefined, { maximumFractionDigits: v < 10 ? 2 : 0 })}`;
  return fmtNum(v);
}

/** Clean axis ticks: 0, then round steps up to just past the largest value. */
function ticks(max: number, unit: string): number[] {
  if (unit === "%") return [0, 25, 50, 75, 100];
  if (max <= 0) return [0, 1];
  const raw = max / 4;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out = [];
  for (let v = 0; v <= max + step * 0.999; v += step) out.push(Number(v.toFixed(6)));
  return out.length > 1 ? out : [0, step];
}

function useWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(Math.max(280, el.clientWidth)));
    ro.observe(el);
    setWidth(Math.max(280, el.clientWidth));
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}

export function ReportChart({ chart, period, title, busy, note = "The table below has every number." }:
  { chart: ChartData; period: string; title: string; busy?: boolean; note?: string }) {
  const [measureKey, setMeasureKey] = useState(chart.measures[0]?.key);
  const measure = chart.measures.find((m) => m.key === measureKey) ?? chart.measures[0];
  useEffect(() => { if (!chart.measures.some((m) => m.key === measureKey)) setMeasureKey(chart.measures[0]?.key); }, [chart, measureKey]);
  if (!measure || chart.points.length === 0) return null;
  return (
    <figure className={`report-chart${busy ? " busy" : ""}`} aria-label={`${title}: ${measure.label}`}>
      <div className="chart-head">
        <figcaption><strong>{measure.label}</strong>{chart.kind !== "hbar" && chart.x_kind !== "time" && <span className="muted"> by {lowerFirst(chart.x_label)}</span>}</figcaption>
        {chart.measures.length > 1 && (
          <div className="chart-measures" role="group" aria-label="Show">
            {chart.measures.map((m) => (
              <button key={m.key} type="button" className="btn small secondary" aria-pressed={m.key === measure.key}
                onClick={() => setMeasureKey(m.key)}>{m.label}</button>
            ))}
          </div>
        )}
      </div>
      {chart.kind === "hbar"
        ? <HBars chart={chart} measure={measure} />
        : <Plot chart={chart} measure={measure} period={period} />}
      {note && <p className="muted small chart-note">{note}</p>}
    </figure>
  );
}

/** Top categories as horizontal bars, labeled at the tip (HTML: no hover needed, every value is shown). */
function HBars({ chart, measure }: { chart: ChartData; measure: ChartData["measures"][number] }) {
  const max = Math.max(1, ...chart.points.map((p) => Number(p[measure.key] ?? 0)));
  return (
    <ol className="hbars">
      {chart.points.map((p, i) => {
        const v = Number(p[measure.key] ?? 0);
        return (
          <li key={i}>
            <span className="hbar-label" title={String(p[chart.x])}>{String(p[chart.x] ?? "–")}</span>
            <span className="hbar-track"><span className="hbar" style={{ width: `${(100 * v) / max}%` }} /></span>
            <span className="hbar-value">{fmtValue(v, measure.unit)}</span>
          </li>
        );
      })}
    </ol>
  );
}

function Plot({ chart, measure, period }: { chart: ChartData; measure: ChartData["measures"][number]; period: string }) {
  const [boxRef, width] = useWidth();
  const [active, setActive] = useState<number | null>(null);

  const { xs, series, values, refValue } = useMemo(() => {
    const present = [...new Set(chart.points.map((p) => String(p[chart.x])))].sort();
    // Time runs evenly: periods with nothing in them still get their place on the axis
    const xs = chart.x_kind === "period" ? fillPeriods(present, period) : present;
    // Color follows the entity: series keep their slot by name, not by rank
    const names = chart.series ? [...new Set(chart.points.map((p) => String(p[chart.series!] ?? "–")))].sort() : [measure.label];
    const series = names.slice(0, MAX_SERIES);
    const values = new Map<string, Map<string, number | null>>();
    for (const p of chart.points) {
      const name = chart.series ? String(p[chart.series] ?? "–") : measure.label;
      if (!values.has(name)) values.set(name, new Map());
      const v = p[measure.key];
      values.get(name)!.set(String(p[chart.x]), v === null || v === undefined ? null : Number(v));
    }
    const refs = chart.ref && series.length === 1 ? chart.points.map((p) => Number(p._ref ?? 0)).filter((v) => v > 0) : [];
    return { xs, series, values, refValue: refs.length ? Math.max(...refs) : null };
  }, [chart, measure]);

  const isLine = chart.kind === "line";
  // Room at the right for each line's name (12px text, about 6.6px a character), up to 30% of the chart: a longer
  // name is shortened, as the legend above names every line in full (#545)
  const endNames = isLine && series.length > 1 && series.length <= 4;
  const labelRoom = endNames
    ? Math.min(Math.round(width * 0.3), 20 + Math.ceil(Math.max(...series.map((s) => s.length)) * CHAR_W)) : 16;
  const fits = Math.max(4, Math.floor((labelRoom - 12) / CHAR_W));
  const innerW = Math.max(120, width - M.left - labelRoom);
  const innerH = H - M.top - M.bottom;
  const all = series.flatMap((s) => xs.map((x) => values.get(s)?.get(x) ?? null)).filter((v): v is number => v !== null);
  const yTicks = ticks(Math.max(refValue ?? 0, ...all, 0), measure.unit);
  const yMax = yTicks[yTicks.length - 1] || 1;
  const y = (v: number) => M.top + innerH - (v / yMax) * innerH;
  const band = innerW / Math.max(1, xs.length);
  const xCenter = (i: number) => M.left + band * i + band / 2;
  const labelW = period === "time" ? 110 : 70;  // "Oct 17, 7:15 PM" needs more room than a date
  const labelEvery = Math.max(1, Math.ceil(xs.length / Math.max(1, Math.floor(innerW / labelW))));
  const barW = Math.min(24, Math.max(3, (band * 0.75 - 2 * (series.length - 1)) / series.length));
  const slot = (name: string) => `var(--series-${(series.indexOf(name) % MAX_SERIES) + 1})`;

  /** Each series' name at the right edge, level with its last value, nudged apart so names never overlap. */
  function endLabels() {
    const placed = series.flatMap((s) => {
      const last = [...xs].reverse().map((x) => values.get(s)?.get(x)).find((v) => v !== null && v !== undefined);
      return last === undefined || last === null ? [] : [{ s, at: y(last) }];
    }).sort((a, b) => a.at - b.at);
    const gap = 15;
    for (let i = 1; i < placed.length; i++) placed[i].at = Math.max(placed[i].at, placed[i - 1].at + gap);
    // Keep the last one inside the plot, pushing the others up as needed
    const bottom = M.top + innerH;
    for (let i = placed.length - 1; i >= 0; i--) {
      const limit = i === placed.length - 1 ? bottom : placed[i + 1].at - gap;
      placed[i].at = Math.max(M.top, Math.min(placed[i].at, limit));
    }
    return placed;
  }

  function onKey(e: KeyboardEvent) {
    if (e.key === "ArrowRight") { setActive((a) => Math.min(xs.length - 1, (a ?? -1) + 1)); e.preventDefault(); }
    else if (e.key === "ArrowLeft") { setActive((a) => Math.max(0, (a ?? xs.length) - 1)); e.preventDefault(); }
    else if (e.key === "Escape") setActive(null);
  }

  const tip = active !== null ? xs[active] : null;
  return (
    <div className="plot" ref={boxRef}>
      {series.length > 1 && (
        <ul className="chart-legend" aria-label={chart.series_label ?? "Series"}>
          {series.map((s) => (
            <li key={s}><span className={isLine ? "key-line" : "key-box"} style={{ background: slot(s) }} aria-hidden="true" />{s}</li>
          ))}
        </ul>
      )}
      <svg width={width} height={H} role="img" tabIndex={0} onKeyDown={onKey} onBlur={() => setActive(null)}
        aria-label={`${measure.label} by ${lowerFirst(chart.x_label)}, ${xs.length} points. Use the left and right arrow keys to read values.`}
        onMouseLeave={() => setActive(null)}>
        {yTicks.map((t) => (
          <g key={t}>
            <line x1={M.left} x2={M.left + innerW} y1={y(t)} y2={y(t)} className="grid" />
            <text x={M.left - 8} y={y(t)} className="tick" textAnchor="end" dominantBaseline="middle">{fmtTick(t, measure.unit)}</text>
          </g>
        ))}
        {xs.map((x, i) => i % labelEvery === 0 && (
          <text key={x} x={xCenter(i)} y={H - 10} className="tick" textAnchor="middle">{fmtX(x, period)}</text>
        ))}
        {endNames && endLabels().map(({ s, at }) => (
          <text key={s} x={M.left + innerW + 8} y={at} className="end-label" dominantBaseline="middle">
            {endName(s, fits)}{endName(s, fits) !== s && <title>{s}</title>}</text>
        ))}
        {refValue !== null && (
          <g>
            <line x1={M.left} x2={M.left + innerW} y1={y(refValue)} y2={y(refValue)} className="ref-line" />
            <text x={M.left + innerW} y={y(refValue) - 5} className="tick" textAnchor="end">{chart.ref_label}: {fmtNum(refValue)}</text>
          </g>
        )}
        {isLine ? series.map((s) => {
          const pts = xs.map((x, i) => [i, values.get(s)?.get(x) ?? null] as const);
          let d = "";
          let open = false;
          for (const [i, v] of pts) {
            if (v === null) { open = false; continue; }
            d += `${open ? "L" : "M"}${xCenter(i)},${y(v)}`;
            open = true;
          }
          return (
            <g key={s}>
              <path d={d} fill="none" stroke={slot(s)} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
              {pts.map(([i, v]) => v !== null && (pts.length <= 40 || (pts[i - 1]?.[1] == null && pts[i + 1]?.[1] == null)) && (
                // Every point when there are few; otherwise only points with no neighbor, which a line can't show
                <circle key={i} cx={xCenter(i)} cy={y(v)} r={4} fill={slot(s)} className="ring" />
              ))}
            </g>
          );
        }) : xs.map((x, i) => series.map((s, k) => {
          const v = values.get(s)?.get(x);
          if (v === null || v === undefined || v <= 0) return null;
          const left = xCenter(i) - (series.length * barW + (series.length - 1) * 2) / 2 + k * (barW + 2);
          const top = y(v);
          const h = M.top + innerH - top;
          const r = Math.min(4, barW / 2, h);
          return (
            <path key={`${x}-${s}`} fill={slot(s)} opacity={active !== null && active !== i ? 0.55 : 1}
              d={`M${left},${M.top + innerH}V${top + r}Q${left},${top} ${left + r},${top}H${left + barW - r}Q${left + barW},${top} ${left + barW},${top + r}V${M.top + innerH}Z`} />
          );
        }))}
        {isLine && active !== null && <line x1={xCenter(active)} x2={xCenter(active)} y1={M.top} y2={M.top + innerH} className="crosshair" />}
        {xs.map((x, i) => (
          <rect key={x} x={M.left + band * i} y={M.top} width={band} height={innerH} fill="transparent"
            onMouseMove={() => setActive(i)} />
        ))}
      </svg>
      {tip !== null && active !== null && (
        <div className="chart-tip" role="status" style={{ left: Math.min(Math.max(0, xCenter(active) - 80), width - 180) }}>
          <div className="muted small">{fmtX(tip, period)}</div>
          {series.map((s) => (
            <div key={s} className="tip-row">
              <span className="key-line" style={{ background: slot(s) }} aria-hidden="true" />
              <strong>{fmtValue(values.get(s)?.get(tip) ?? null, measure.unit)}</strong>
              {series.length > 1 && <span className="muted small"> {s}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
