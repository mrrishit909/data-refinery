import { useEffect, useId, useRef } from "react";
import type { Accounting } from "../data/model";
import { animate, prefersReducedMotion } from "../lib/motion";
import { useWidth } from "../lib/useWidth";
import { Disclosure } from "./Controls";

let played = false; // the run-load sequence plays once per page load

export function totals(rows: Accounting[]): Omit<Accounting, "batch" | "byReason" | "identityHolds"> & { byReason: Record<string, number> } {
  const byReason: Record<string, number> = {};
  for (const a of rows) for (const [k, v] of Object.entries(a.byReason)) byReason[k] = (byReason[k] ?? 0) + v;
  return {
    rowsIn: rows.reduce((s, a) => s + a.rowsIn, 0),
    accepted: rows.reduce((s, a) => s + a.accepted, 0),
    quarantined: rows.reduce((s, a) => s + a.quarantined, 0),
    merged: rows.reduce((s, a) => s + a.merged, 0),
    byReason,
  };
}

const fmt = (n: number) => n.toLocaleString("en-US");

export function Equation({ rows, label }: { rows: Accounting[]; label: string }) {
  const t = totals(rows);
  const ref = useRef<HTMLParagraphElement>(null);
  const holds = t.rowsIn === t.accepted + t.quarantined + t.merged;
  useEffect(() => {
    if (played || prefersReducedMotion()) return;
    ref.current?.querySelectorAll("[data-term]").forEach((el, i) =>
      animate(el, [{ opacity: 0, transform: "translateY(6px)" }, { opacity: 1, transform: "none" }], { duration: 220, delay: i * 60 }),
    );
  }, []);
  return (
    <p className="dr-equation" ref={ref} role="img" aria-label={`${label}: ${fmt(t.rowsIn)} rows in equals ${fmt(t.accepted)} accepted plus ${fmt(t.quarantined)} quarantined plus ${fmt(t.merged)} merged duplicates. ${holds ? "Every row is accounted for." : "The accounting does not balance."}`}>
      <span className="dr-term" data-term aria-hidden>{fmt(t.rowsIn)}<small>in</small></span>
      <span className="dr-op" data-term aria-hidden>=</span>
      <span className="dr-term dr-term-accepted" data-term aria-hidden>{fmt(t.accepted)}<small>accepted</small></span>
      <span className="dr-op" data-term aria-hidden>+</span>
      <span className="dr-term dr-term-quarantined" data-term aria-hidden>{fmt(t.quarantined)}<small>quarantined</small></span>
      <span className="dr-op" data-term aria-hidden>+</span>
      <span className="dr-term dr-term-merged" data-term aria-hidden>{fmt(t.merged)}<small>merged</small></span>
    </p>
  );
}

/** Ribbons from the input bar to the three destinations; widths are proportional to row counts. */
export function FlowRibbons({ rows, onPick }: { rows: Accounting[]; onPick?: (dest: "accepted" | "quarantined" | "merged") => void }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const group = useRef<SVGGElement>(null);
  const uid = useId().replace(/:/g, "");
  const t = totals(rows);
  const compact = width < 560;
  const h = compact ? 150 : 170;
  const barW = 14;
  const inX = 0;
  const outX = Math.max(120, width - (compact ? 128 : 214));
  const scale = t.rowsIn ? (h - 24) / t.rowsIn : 0;
  const dests = [
    { key: "accepted" as const, label: "Accepted", count: t.accepted, fill: "var(--dr-accepted-fill)", pattern: "solid" },
    { key: "quarantined" as const, label: "Quarantined", count: t.quarantined, fill: "var(--dr-quarantined-fill)", pattern: "hatch" },
    { key: "merged" as const, label: "Merged duplicates", count: t.merged, fill: "var(--dr-merged-fill)", pattern: "dots" },
  ];
  let yIn = 0;
  let yOut = 0;
  const gap = 16;
  const minThick = 10; // small flows would be hairlines at true scale
  const enlarged = dests.some((d) => d.count > 0 && d.count * scale < minThick);
  const bands = dests.map((d) => {
    const thick = d.count ? Math.max(minThick, d.count * scale) : 0;
    const band = { ...d, thick, y0: yIn, y1: yOut };
    yIn += thick;
    yOut += thick + gap;
    return band;
  });

  useEffect(() => {
    if (!width || played) return;
    played = true;
    animate(group.current, [{ clipPath: "inset(0 100% 0 0)" }, { clipPath: "inset(0 0 0 0)" }], { duration: 520, delay: 300 });
  }, [width]);

  const fillFor = (b: (typeof bands)[number]) =>
    b.pattern === "solid" ? b.fill : `url(#${uid}-${b.pattern})`;

  return (
    <figure style={{ display: "grid", gap: 8 }}>
      <div ref={ref}>
        {width > 0 && (
          <svg className="dr-flow" width={width} height={yOut + 4} viewBox={`0 0 ${width} ${yOut + 4}`} aria-hidden>
            <defs>
              <pattern id={`${uid}-hatch`} width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                <rect width="6" height="6" fill="color-mix(in srgb, var(--dr-quarantined-fill) 30%, var(--dr-raised))" />
                <line x1="0" y1="0" x2="0" y2="6" stroke="var(--dr-quarantined-fill)" strokeWidth="2.2" />
              </pattern>
              <pattern id={`${uid}-dots`} width="6" height="6" patternUnits="userSpaceOnUse">
                <rect width="6" height="6" fill="color-mix(in srgb, var(--dr-merged-fill) 25%, var(--dr-raised))" />
                <circle cx="3" cy="3" r="1.4" fill="var(--dr-merged-fill)" />
              </pattern>
            </defs>
            <rect x={inX} y={0} width={barW} height={yIn} fill="var(--dr-text)" rx={2} />
            <g ref={group}>
              {bands.map((b) => {
                if (!b.thick) return null;
                const x0 = inX + barW;
                const x1 = outX;
                const mid = (x0 + x1) / 2;
                const d = `M${x0} ${b.y0} C ${mid} ${b.y0}, ${mid} ${b.y1}, ${x1} ${b.y1} L ${x1} ${b.y1 + b.thick} C ${mid} ${b.y1 + b.thick}, ${mid} ${b.y0 + b.thick}, ${x0} ${b.y0 + b.thick} Z`;
                return (
                  <g key={b.key} style={{ cursor: onPick ? "pointer" : undefined }} onClick={() => onPick?.(b.key)}>
                    <path d={d} fill={fillFor(b)} opacity={0.9} />
                    <rect x={x1} y={b.y1} width={6} height={b.thick} fill={b.fill} />
                    <text x={x1 + 12} y={b.y1 + Math.min(b.thick, 26) / 2 + 4}>
                      <tspan className="dr-flow-count">{fmt(b.count)}</tspan> {b.label}
                    </text>
                  </g>
                );
              })}
            </g>
          </svg>
        )}
      </div>
      {enlarged && <figcaption className="dr-small">Band widths follow row counts; bands for small counts are drawn at a minimum width so they stay visible.</figcaption>}
      <Disclosure summary={<span className="dr-small">Show the accounting as a table</span>}>
        <div className="dr-table-wrap" tabIndex={0} role="region" aria-label="Row accounting by batch">
          <table className="dr-table">
            <thead>
              <tr><th scope="col">Batch</th><th scope="col" className="dr-num">Rows in</th><th scope="col" className="dr-num">Accepted</th><th scope="col" className="dr-num">Quarantined</th><th scope="col" className="dr-num">Merged</th><th scope="col">Balances</th></tr>
            </thead>
            <tbody>
              {rows.map((a) => (
                <tr key={a.batch}>
                  <th scope="row" className="dr-mono">{a.batch}</th>
                  <td className="dr-num dr-mono">{fmt(a.rowsIn)}</td>
                  <td className="dr-num dr-mono">{fmt(a.accepted)}</td>
                  <td className="dr-num dr-mono">{fmt(a.quarantined)}</td>
                  <td className="dr-num dr-mono">{fmt(a.merged)}</td>
                  <td>{a.identityHolds ? "yes" : "no"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Disclosure>
    </figure>
  );
}
