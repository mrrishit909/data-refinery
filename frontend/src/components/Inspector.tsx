import { useEffect, useRef } from "react";
import type { ColumnProfile, PlanStep, QuarantinedRow, Repair, Row, RunModel, Sample } from "../data/model";
import { reasonLabel } from "../data/model";
import { Icon } from "./Icon";

export type Selection =
  | { kind: "row"; row: Row | QuarantinedRow; sample?: Sample }
  | { kind: "repair"; repair: Repair }
  | { kind: "column"; profile: ColumnProfile }
  | null;

function isQuarantined(row: Row | QuarantinedRow): row is QuarantinedRow {
  return "reasons" in row;
}

function Steps({ steps }: { steps: PlanStep[] }) {
  if (!steps.length) return <p className="dr-small">No transformation steps recorded for this batch.</p>;
  return (
    <ol className="dr-steps">
      {steps.map((s) => (
        <li key={`${s.batch}-${s.order}`}>
          <span>{s.name}</span>
          <span className="dr-small dr-mono">{s.rowsOut ?? ""}</span>
        </li>
      ))}
    </ol>
  );
}

export function Inspector({ selection, run, onClose }: { selection: Selection; run: RunModel; onClose: () => void }) {
  const ref = useRef<HTMLElement>(null);
  const open = selection !== null;
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    const el = ref.current;
    el?.addEventListener("keydown", onKey);
    // On narrow screens the inspector is a sheet; move focus into it so keyboard users land there.
    if (el && getComputedStyle(el).position === "fixed") el.focus();
    return () => el?.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  let body: React.ReactNode = (
    <p className="dr-inspector-empty">
      Select a row, a repair or a column to see where it came from, what was done to it and why.
    </p>
  );
  if (selection?.kind === "row") {
    const { row, sample } = selection;
    const steps = run.plan.filter((s) => s.batch === row.batch);
    const cleaned = sample?.cleaned ?? (isQuarantined(row) ? null : row.values);
    const raw = sample?.raw ?? (isQuarantined(row) ? row.values : null);
    const keys = [...new Set([...Object.keys(raw ?? {}), ...Object.keys(cleaned ?? {})])].filter((k) => !k.startsWith("_"));
    body = (
      <>
        <div style={{ display: "grid", gap: 4 }}>
          <p className="dr-h3">
            Row {row.sourceRow} of <span className="dr-mono">{row.sourceFile}</span>
          </p>
          <p className="dr-small">Batch {row.batch}</p>
        </div>
        {isQuarantined(row) ? (
          <div style={{ display: "grid", gap: 6 }}>
            <p><span className="dr-tag dr-tone-danger"><Icon name="cross" /> Quarantined</span></p>
            <ul style={{ listStyle: "disc", paddingLeft: 18 }}>
              {row.reasons.map((r) => (
                <li key={r}>
                  {reasonLabel(r)} <span className="dr-small dr-mono">{r}</span>
                  {run.reasonText[r] && <span className="dr-small" style={{ display: "block" }}>{run.reasonText[r]}</span>}
                </li>
              ))}
              {row.values._reason_detail && <li className="dr-small" style={{ listStyle: "none" }}>{row.values._reason_detail}</li>}
            </ul>
          </div>
        ) : (
          <p><span className="dr-tag dr-tone-success"><Icon name="check" /> Accepted</span></p>
        )}
        {sample?.note && <p className="dr-small">{sample.note}</p>}
        {cleaned === null ? (
          <div className="dr-table-wrap" tabIndex={0} role="region" aria-label="Values as received">
            <table className="dr-table">
              <thead><tr><th scope="col">Column</th><th scope="col">Value as received</th></tr></thead>
              <tbody>
                {keys.map((k) => (
                  <tr key={k}>
                    <th scope="row" style={{ fontWeight: 500 }}>{k}</th>
                    <td className="dr-mono">{raw?.[k] || <span className="dr-small">(empty)</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
        <div className="dr-table-wrap" tabIndex={0} role="region" aria-label="Values before and after">
          <table className="dr-table">
            <thead><tr><th scope="col">Column</th><th scope="col">Source value</th><th scope="col">Output value</th></tr></thead>
            <tbody>
              {keys.map((k) => {
                const before = raw?.[k];
                const after = cleaned?.[k];
                const changed = raw && cleaned && before !== undefined && after !== undefined && before !== after;
                return (
                  <tr key={k}>
                    <th scope="row" style={{ fontWeight: 500 }}>{k}</th>
                    <td className={`dr-mono${changed ? " dr-cell-raw" : ""}`}>{before ?? (raw ? "" : "not in sample")}</td>
                    <td className={`dr-mono${changed ? " dr-cell-changed" : ""}`}>{after ?? ""}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        )}
        <div style={{ display: "grid", gap: 6 }}>
          <p className="dr-h3">Steps applied to batch {row.batch}</p>
          <Steps steps={steps} />
        </div>
      </>
    );
  } else if (selection?.kind === "repair") {
    const r = selection.repair;
    body = (
      <>
        <p className="dr-h3">{r.kind.replace(/_/g, " ")} in batch {r.batch}</p>
        <p>{r.detail}</p>
        <p>
          {r.applied ? (
            <span className="dr-tag dr-tone-success"><Icon name="check" /> Applied</span>
          ) : r.ambiguity ? (
            <span className="dr-tag dr-tone-warning"><Icon name="warn" /> Needs a human</span>
          ) : (
            <span className="dr-tag dr-tone-muted">Proposed, not applied</span>
          )}
        </p>
        {r.ambiguity && <p className="dr-small">{r.ambiguity}</p>}
        <div style={{ display: "grid", gap: 6 }}>
          <p className="dr-small">Confidence <span className="dr-mono">{r.confidence.toFixed(2)}</span></p>
          <div className="dr-meter" role="img" aria-label={`Confidence ${Math.round(r.confidence * 100)} percent`}>
            <span style={{ width: `${Math.round(r.confidence * 100)}%` }} />
          </div>
        </div>
        <dl className="dr-kv">
          {Object.entries(r.evidence).map(([k, v]) => (
            <div key={k} style={{ display: "contents" }}>
              <dt>{k.replace(/_/g, " ")}</dt>
              <dd className="dr-mono">{v === null || v === undefined ? "none" : typeof v === "object" ? JSON.stringify(v) : String(v)}</dd>
            </div>
          ))}
        </dl>
        <p className="dr-small">
          Only unambiguous repairs are ever applied automatically, and only in apply-safe mode. Every applied repair is
          a recorded step in the plan.
        </p>
      </>
    );
  } else if (selection?.kind === "column") {
    const p = selection.profile;
    const rule = run.contractColumns.find((c) => c.name === p.column);
    body = (
      <>
        <p className="dr-h3"><span className="dr-mono">{p.column}</span> in batch {p.batch}</p>
        <dl className="dr-kv">
          <dt>Contract</dt><dd>{rule ? rule.rule : "not in the contract"}</dd>
          <dt>Observed type</dt><dd>{p.inferredType}</dd>
          <dt>Nulls</dt><dd className="dr-mono">{p.nulls} ({(p.nullRate * 100).toFixed(1)}%)</dd>
          <dt>Distinct</dt><dd className="dr-mono">{p.distinct}</dd>
          {p.min !== null && <><dt>Range</dt><dd className="dr-mono">{p.min} to {p.max}</dd></>}
          {p.dateFormats.length > 0 && <><dt>Date formats</dt><dd className="dr-mono">{p.dateFormats.join(", ")}</dd></>}
        </dl>
        {p.notes.length > 0 && (
          <ul style={{ listStyle: "disc", paddingLeft: 18 }} className="dr-small">
            {p.notes.map((n) => <li key={n}>{n}</li>)}
          </ul>
        )}
        {p.topValues.length > 0 && (
          <div className="dr-table-wrap" tabIndex={0} role="region" aria-label="Most common values">
            <table className="dr-table">
              <thead><tr><th scope="col">Most common values</th><th scope="col" className="dr-num">Rows</th></tr></thead>
              <tbody>
                {p.topValues.map(([v, n]) => (
                  <tr key={v}><td className="dr-mono">{v === "" ? "(empty)" : v}</td><td className="dr-num dr-mono">{n}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </>
    );
  }

  return (
    <>
      <div className="dr-scrim" data-open={open} onClick={onClose} aria-hidden />
      <aside className="dr-inspector" data-open={open} aria-label="Inspector" tabIndex={-1} ref={ref}>
        <button type="button" className="dr-btn dr-inspector-close" onClick={onClose}>
          <Icon name="cross" /> Close
        </button>
        {body}
      </aside>
    </>
  );
}
