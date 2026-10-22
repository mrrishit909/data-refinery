import { useMemo, useState } from "react";
import { Disclosure, Segmented } from "../components/Controls";
import { FlowRibbons, totals } from "../components/Flow";
import { Icon } from "../components/Icon";
import type { Selection } from "../components/Inspector";
import type { AppConfig } from "../config";
import type { QuarantinedRow, Repair, RunModel, Sample } from "../data/model";
import { reasonLabel } from "../data/model";

type Props = { run: RunModel; batch: string | null; select: (s: Selection) => void; selection: Selection; config: AppConfig };

const fmt = (n: number) => n.toLocaleString("en-US");
const pct = (x: number) => `${(x * 100).toFixed(1)}%`;
const usd = (n: number) => n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const inBatch = <T extends { batch: string }>(items: T[], batch: string | null) => (batch ? items.filter((i) => i.batch === batch) : items);

function Head({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="dr-section-head">
      <h2 className="dr-h2" tabIndex={-1} data-stage-heading>{title}</h2>
      {children && <p className="dr-prose">{children}</p>}
    </div>
  );
}

const Region = ({ label, children }: { label: string; children: React.ReactNode }) => (
  <div className="dr-table-wrap" tabIndex={0} role="region" aria-label={label}>{children}</div>
);

export function IntakeStage({ run, batch, config }: Props) {
  const sources = inBatch(run.sources, batch);
  return (
    <>
      <Head title="Intake: what arrived, exactly as it arrived">
        Each batch is read as text with no type coercion, bounded in size, and fingerprinted. The original files are never
        modified; every later step points back to a row in one of these files.
      </Head>
      <div className="dr-panel">
        <Region label="Source files">
          <table className="dr-table">
            <thead><tr><th scope="col">Batch</th><th scope="col">File</th><th scope="col">Format</th><th scope="col" className="dr-num">Rows</th><th scope="col" className="dr-num">Bytes</th><th scope="col">Columns</th><th scope="col">sha256</th></tr></thead>
            <tbody>
              {sources.map((s) => (
                <tr key={s.batch}>
                  <th scope="row" className="dr-mono">{s.batch}</th>
                  <td><a href={`${config.repoUrl}/blob/main/fixtures/${s.file}`} className="dr-mono">{s.file}</a></td>
                  <td>{s.format.toUpperCase()}</td>
                  <td className="dr-num dr-mono">{fmt(s.rows)}</td>
                  <td className="dr-num dr-mono">{fmt(s.bytes)}</td>
                  <td className="dr-small">{s.columns.join(", ")}</td>
                  <td className="dr-mono dr-wrap-anywhere">{s.sha256.replace(/^sha256:/, "").slice(0, 12)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Region>
      </div>
      <div className="dr-toolbar">
        <a className="dr-btn" href="#/preview"><Icon name="info" /> Preview your own CSV in the browser</a>
        <span className="dr-small">The preview runs in your browser only; nothing is uploaded or saved.</span>
      </div>
    </>
  );
}

export function ProfileStage({ run, batch, select, selection }: Props) {
  const current = batch ?? run.params.batches[run.params.batches.length - 1] ?? null;
  const [view, setView] = useState<"raw" | "accepted">("raw");
  const profiles = (view === "raw" ? run.profiles : run.profilesAccepted).filter((p) => p.batch === current);
  const contract = new Map(run.contractColumns.map((c) => [c.name, c.rule]));
  const drifted = new Set(run.drift.filter((d) => d.batch === current && d.column).map((d) => d.column));
  return (
    <>
      <Head title={`Profile: the shape of batch ${current}`}>
        Observed columns compared with the contract. A column the contract does not know, or one with drift, is marked.
        Select a column to see its most common values.
      </Head>
      <Segmented label="Profile" value={view} options={[{ value: "raw", label: "As received" }, { value: "accepted", label: "After refining" }]} onChange={setView} />
      <div className="dr-panel">
        <Region label={`Column profiles for batch ${current}`}>
          <table className="dr-table">
            <thead><tr><th scope="col">Column</th><th scope="col">Contract</th><th scope="col">Observed</th><th scope="col">Nulls</th><th scope="col" className="dr-num">Distinct</th><th scope="col">Status</th></tr></thead>
            <tbody>
              {profiles.map((p) => {
                const selected = selection?.kind === "column" && selection.profile.column === p.column && selection.profile.batch === p.batch;
                const known = contract.has(p.column);
                return (
                  <tr key={p.column} data-selectable aria-selected={selected} onClick={() => select({ kind: "column", profile: p })}>
                    <th scope="row" style={{ fontWeight: 500 }}>
                      <button type="button" className="dr-mono" style={{ all: "unset", cursor: "pointer" }} onClick={(e) => { e.stopPropagation(); select({ kind: "column", profile: p }); }}>{p.column}</button>
                    </th>
                    <td className="dr-small">{contract.get(p.column) ?? "not in contract"}</td>
                    <td>{p.inferredType}</td>
                    <td style={{ minWidth: 110 }}>
                      <div className="dr-meter" role="img" aria-label={`${pct(p.nullRate)} null`}><span style={{ width: `${Math.max(p.nullRate * 100, p.nulls ? 2 : 0)}%`, background: "var(--dr-quarantined-fill)" }} /></div>
                      <span className="dr-small dr-mono">{p.nulls}</span>
                    </td>
                    <td className="dr-num dr-mono">{fmt(p.distinct)}</td>
                    <td>
                      {!known ? <span className="dr-tag dr-tone-warning"><Icon name="warn" /> Unknown</span>
                        : drifted.has(p.column) ? <span className="dr-tag dr-tone-warning"><Icon name="warn" /> Drift</span>
                        : <span className="dr-tag dr-tone-success"><Icon name="check" /> Matches</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Region>
      </div>
    </>
  );
}

function RepairCard({ r, active, onSelect }: { r: Repair; active: boolean; onSelect: () => void }) {
  return (
    <li className="dr-card" style={active ? { borderColor: "var(--dr-action)" } : undefined}>
      <div className="dr-toolbar" style={{ justifyContent: "space-between" }}>
        <p className="dr-h3">{r.kind.replace(/_/g, " ")} <span className="dr-small dr-mono">{r.batch}</span></p>
        {r.applied ? <span className="dr-tag dr-tone-success"><Icon name="check" /> Applied</span>
          : r.ambiguity ? <span className="dr-tag dr-tone-warning"><Icon name="warn" /> Needs a human</span>
          : <span className="dr-tag dr-tone-muted">Proposed</span>}
      </div>
      <p>{r.detail}</p>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) auto", gap: 10, alignItems: "center" }}>
        <div className="dr-meter" role="img" aria-label={`Confidence ${Math.round(r.confidence * 100)} percent`}><span style={{ width: `${Math.round(r.confidence * 100)}%` }} /></div>
        <span className="dr-small dr-mono">{r.confidence.toFixed(2)}</span>
      </div>
      <div><button type="button" className="dr-btn" onClick={onSelect} aria-pressed={active}>Inspect the evidence</button></div>
    </li>
  );
}

function BeforeAfter({ samples, select }: { samples: Sample[]; select: (s: Selection) => void }) {
  const [mode, setMode] = useState<"after" | "before">("after");
  const [all, setAll] = useState(false);
  const cols = useMemo(() => [...new Set(samples.flatMap((s) => Object.keys(s.raw)))].filter((c) => !c.startsWith("_")).slice(0, 8), [samples]);
  if (!samples.length) return null;
  const shown = all ? samples : samples.slice(0, SAMPLE_PREVIEW);
  return (
    <div className="dr-panel" style={{ display: "grid", gap: 12 }}>
      <div className="dr-toolbar" style={{ justifyContent: "space-between" }}>
        <p className="dr-h3">Sample rows, before and after</p>
        <Segmented label="Show values" value={mode} options={[{ value: "after", label: "After" }, { value: "before", label: "Before" }]} onChange={setMode} />
      </div>
      <Region label="Sample rows">
        <table className="dr-table">
          <thead><tr><th scope="col">Row</th>{cols.map((c) => <th key={c} scope="col">{c}</th>)}<th scope="col">Outcome</th></tr></thead>
          <tbody>
            {shown.map((s) => (
              <tr key={`${s.batch}-${s.sourceRow}`} data-selectable onClick={() => select({ kind: "row", row: s.cleaned ? { batch: s.batch, sourceFile: "", sourceRow: s.sourceRow, values: s.cleaned } : { batch: s.batch, sourceFile: "", sourceRow: s.sourceRow, values: s.raw, reasons: s.reasons }, sample: s })}>
                <th scope="row" className="dr-mono">{s.sourceRow}</th>
                {cols.map((c) => {
                  const before = s.raw[c] ?? "";
                  const after = s.cleaned?.[c];
                  const changed = after !== undefined && after !== before;
                  const shown = mode === "before" || after === undefined ? before : after;
                  return <td key={c} className={`dr-mono${changed && mode === "after" ? " dr-cell-changed" : ""}`} style={{ transition: "background-color 220ms" }}>{shown}</td>;
                })}
                <td>{s.cleaned ? <span className="dr-tag dr-tone-success">Accepted</span> : <span className="dr-tag dr-tone-danger">{s.reasons.map(reasonLabel).join(", ")}</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Region>
      <div className="dr-toolbar">
        {samples.length > SAMPLE_PREVIEW && (
          <button type="button" className="dr-btn" aria-expanded={all} onClick={() => setAll((v) => !v)}>
            {all ? `Show the first ${SAMPLE_PREVIEW}` : `Show all ${samples.length} sample rows`}
          </button>
        )}
        <p className="dr-small">Underlined cells changed between the source and the output. Select a row for its full lineage.</p>
      </div>
    </div>
  );
}

const SAMPLE_PREVIEW = 12;

export function DriftStage({ run, batch, select, selection }: Props) {
  const drift = inBatch(run.drift, batch);
  const repairs = inBatch(run.repairs, batch);
  const ambiguities = inBatch(run.ambiguities, batch);
  const samples = inBatch(run.samples, batch);
  const applied = repairs.filter((r) => r.applied).length;
  return (
    <>
      <Head title="Drift and repairs: what changed, and what was done about it">
        {repairs.length} repairs proposed, {applied} applied in {run.params.repairMode} mode, {ambiguities.length} questions that need a person.
        Only unambiguous repairs are applied automatically.
      </Head>
      {drift.length > 0 && (
        <div className="dr-panel">
          <p className="dr-h3" style={{ marginBottom: 8 }}>Drift found</p>
          <ul style={{ listStyle: "none", display: "grid", gap: 6 }}>
            {drift.map((d, i) => (
              <li key={i} style={{ display: "grid", gridTemplateColumns: "auto minmax(0, 1fr)", gap: 10 }}>
                <span className="dr-tone-warning"><Icon name="warn" /></span>
                <span><span className="dr-mono dr-small">{d.batch}</span> {d.detail}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <ul className="dr-cards" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))" }}>
        {repairs.map((r) => (
          <RepairCard key={r.id} r={r} active={selection?.kind === "repair" && selection.repair.id === r.id} onSelect={() => select({ kind: "repair", repair: r })} />
        ))}
      </ul>
      {ambiguities.length > 0 && (
        <div className="dr-panel">
          <p className="dr-h3" style={{ marginBottom: 8 }}>Needs a human decision</p>
          <Region label="Ambiguities">
            <table className="dr-table">
              <thead><tr><th scope="col">Batch</th><th scope="col">Column</th><th scope="col">Value</th><th scope="col" className="dr-num">Rows</th><th scope="col">Why it was not decided</th></tr></thead>
              <tbody>
                {ambiguities.map((a) => (
                  <tr key={a.id}><td className="dr-mono">{a.batch}</td><td className="dr-mono">{a.column}</td><td className="dr-mono">{a.value}</td><td className="dr-num dr-mono">{a.rows}</td><td>{a.reason}</td></tr>
                ))}
              </tbody>
            </table>
          </Region>
        </div>
      )}
      <BeforeAfter samples={samples} select={select} />
    </>
  );
}

export function TransformStage({ run, batch }: Props) {
  const steps = inBatch(run.plan, batch);
  const quality = inBatch(run.quality, batch);
  const byBatch = useMemo(() => {
    const m = new Map<string, typeof steps>();
    for (const s of steps) m.set(s.batch, [...(m.get(s.batch) ?? []), s]);
    return [...m.entries()];
  }, [steps]);
  const metrics = ["completeness", "validity", "uniqueness", "timeliness"] as const;
  return (
    <>
      <Head title="Transform: the exact plan, step by step">
        Every operation applied to each batch, in order, with row counts. Nothing happens to the data that is not a line here.
      </Head>
      {byBatch.map(([b, list]) => (
        <div key={b} className="dr-panel" style={{ display: "grid", gap: 8 }}>
          <p className="dr-h3">Batch <span className="dr-mono">{b}</span></p>
          <ol className="dr-steps">
            {list.map((s) => (
              <li key={s.order}>
                <div><p>{s.name}</p><p className="dr-small">{s.detail}</p></div>
                <span className="dr-small dr-mono">{s.rowsIn !== null && s.rowsOut !== null ? `${fmt(s.rowsIn)} to ${fmt(s.rowsOut)}` : ""}</span>
              </li>
            ))}
          </ol>
        </div>
      ))}
      {quality.length > 0 && (
        <div className="dr-panel">
          <p className="dr-h3" style={{ marginBottom: 8 }}>Quality before and after</p>
          <Region label="Quality metrics before and after">
            <table className="dr-table">
              <thead><tr><th scope="col">Batch</th>{metrics.map((m) => <th key={m} scope="col">{m}</th>)}</tr></thead>
              <tbody>
                {quality.map((q) => (
                  <tr key={q.batch}>
                    <th scope="row" className="dr-mono">{q.batch}</th>
                    {metrics.map((m) => (
                      <td key={m} className="dr-mono">{pct(q.before[m])} to <strong>{pct(q.after[m])}</strong></td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </Region>
        </div>
      )}
    </>
  );
}

const PAGE = 25;

export function QuarantineStage({ run, batch, select, selection }: Props) {
  const rows = inBatch(run.quarantine, batch);
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const r of rows) for (const reason of r.reasons) c[reason] = (c[reason] ?? 0) + 1;
    return Object.entries(c).sort((a, b) => b[1] - a[1]);
  }, [rows]);
  const [reason, setReason] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const filtered = reason ? rows.filter((r) => r.reasons.includes(reason)) : rows;
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE));
  const current = Math.min(page, pages - 1);
  const shown = filtered.slice(current * PAGE, current * PAGE + PAGE);
  const cols = ["order_id", "customer_id", "order_date", "amount", "currency", "category"];
  const isSel = (r: QuarantinedRow) => selection?.kind === "row" && selection.row.batch === r.batch && selection.row.sourceRow === r.sourceRow;
  return (
    <>
      <Head title="Quarantine: set aside, never dropped">
        {fmt(rows.length)} rows did not meet the contract after repair. Each keeps its original values, its source row and the
        reasons it was held back.
      </Head>
      <div className="dr-chips" role="group" aria-label="Filter by reason">
        <button type="button" className="dr-chip" aria-pressed={reason === null} onClick={() => { setReason(null); setPage(0); }}>
          All <span className="dr-mono">{fmt(rows.length)}</span>
        </button>
        {counts.map(([code, n]) => (
          <button key={code} type="button" className="dr-chip" aria-pressed={reason === code} onClick={() => { setReason(code); setPage(0); }}>
            {reasonLabel(code)} <span className="dr-mono">{fmt(n)}</span>
          </button>
        ))}
      </div>
      {filtered.length === 0 ? (
        <p className="dr-prose">No quarantined rows {batch ? `in batch ${batch}` : "in this run"}{reason ? " for this reason" : ""}.</p>
      ) : (
        <div className="dr-panel" style={{ display: "grid", gap: 10 }}>
          <div className="dr-wide-only">
            <Region label={`Quarantined rows, page ${current + 1} of ${pages}`}>
              <table className="dr-table">
                <thead><tr><th scope="col">Batch</th><th scope="col">Row</th>{cols.map((c) => <th key={c} scope="col">{c}</th>)}<th scope="col">Reasons</th></tr></thead>
                <tbody>
                  {shown.map((r) => (
                    <tr key={`${r.batch}-${r.sourceRow}`} data-selectable aria-selected={isSel(r)} onClick={() => select({ kind: "row", row: r })}>
                      <td className="dr-mono">{r.batch}</td>
                      <th scope="row"><button type="button" className="dr-mono" style={{ all: "unset", cursor: "pointer" }} onClick={(e) => { e.stopPropagation(); select({ kind: "row", row: r }); }}>{r.sourceRow}</button></th>
                      {cols.map((c) => (
                        <td key={c} className="dr-mono">{r.values[c] ?? (c === "customer_id" ? r.values.cust_id : undefined) ?? ""}</td>
                      ))}
                      <td>{r.reasons.map((x) => <span key={x} className="dr-tag dr-tone-danger" style={{ marginRight: 4 }}>{reasonLabel(x)}</span>)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Region>
          </div>
          <ul className="dr-cards" aria-label={`Quarantined rows, page ${current + 1} of ${pages}`}>
            {shown.map((r) => (
              <li key={`${r.batch}-${r.sourceRow}`} className="dr-card">
                <p><span className="dr-mono">{r.batch}</span>, row <span className="dr-mono">{r.sourceRow}</span></p>
                <p>{r.reasons.map((x) => <span key={x} className="dr-tag dr-tone-danger" style={{ marginRight: 4 }}>{reasonLabel(x)}</span>)}</p>
                <p className="dr-small dr-mono dr-wrap-anywhere">{r.values.order_id || "(no order id)"} {r.values.order_date ?? ""} {r.values.amount ?? ""} {r.values.currency ?? ""}</p>
                <div><button type="button" className="dr-btn" onClick={() => select({ kind: "row", row: r })}>Inspect row</button></div>
              </li>
            ))}
          </ul>
          <div className="dr-toolbar" role="navigation" aria-label="Quarantine pages">
            <button type="button" className="dr-btn" disabled={current === 0} onClick={() => setPage(current - 1)}>Previous page</button>
            <span className="dr-small dr-mono">Page {current + 1} of {pages}</span>
            <button type="button" className="dr-btn" disabled={current >= pages - 1} onClick={() => setPage(current + 1)}>Next page</button>
            <a className="dr-btn" href={run.quarantineUrl} download>Download quarantine.csv</a>
          </div>
        </div>
      )}
    </>
  );
}

export function ReconcileStage({ run, batch, select }: Props) {
  const rows = inBatch(run.accounting, batch);
  const t = totals(rows);
  const [showReasons, setShowReasons] = useState(false);
  return (
    <>
      <Head title={batch ? `Reconcile: batch ${batch}` : "Reconcile: every row, accounted for"}>
        Rows in must equal rows accepted, quarantined and merged as duplicates. The identity is checked per batch by the
        pipeline and again by the release gates.
      </Head>
      <div className="dr-panel" style={{ display: "grid", gap: 14 }}>
        <FlowRibbons rows={rows} onPick={(dest) => dest === "quarantined" && setShowReasons(true)} />
        <div className="dr-toolbar">
          <button type="button" className="dr-btn" aria-expanded={showReasons} onClick={() => setShowReasons((v) => !v)}>
            {showReasons ? "Hide" : "Show"} quarantine reasons
          </button>
          <a className="dr-btn" href={run.cleanedUrl} download>Download cleaned.csv</a>
        </div>
        {showReasons && (
          <ul style={{ listStyle: "none", display: "grid", gap: 4 }}>
            {Object.entries(t.byReason).sort((a, b) => b[1] - a[1]).map(([code, n]) => (
              <li key={code} className="dr-small"><span className="dr-mono">{fmt(n)}</span> {reasonLabel(code)}</li>
            ))}
          </ul>
        )}
      </div>
      {run.restatements.length > 0 && (
        <div className="dr-panel">
          <p className="dr-h3" style={{ marginBottom: 6 }}>Late arrivals restate closed periods</p>
          <p className="dr-small" style={{ marginBottom: 10 }}>Rows dated in a month an earlier batch already closed are accepted and flagged, and that month's totals are restated, never silently changed.</p>
          <Region label="Restated periods">
            <table className="dr-table">
              <thead><tr><th scope="col">Period</th><th scope="col">Restated by</th><th scope="col" className="dr-num">Late rows</th><th scope="col" className="dr-num">Before, USD</th><th scope="col" className="dr-num">Late, USD</th><th scope="col" className="dr-num">After, USD</th></tr></thead>
              <tbody>
                {run.restatements.map((r) => (
                  <tr key={`${r.period}-${r.batch}`}>
                    <th scope="row" className="dr-mono">{r.period}</th>
                    <td className="dr-mono">{r.batch}</td>
                    <td className="dr-num dr-mono">{fmt(r.rows)}</td>
                    <td className="dr-num dr-mono">{usd(r.beforeUsd)}</td>
                    <td className="dr-num dr-mono">+{usd(r.lateUsd)}</td>
                    <td className="dr-num dr-mono"><strong>{usd(r.afterUsd)}</strong></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Region>
        </div>
      )}
      <div className="dr-panel">
        <p className="dr-h3" style={{ marginBottom: 6 }}>Currency conversion</p>
        <p className="dr-small">
          Amounts are converted to <span className="dr-mono">amount_usd</span> with the declared synthetic table {run.params.fxTable}:{" "}
          {Object.entries(run.fx).map(([c, r]) => <span key={c} className="dr-mono">{c} {r} </span>)}. The original amount and currency are kept.
        </p>
      </div>
      <p className="dr-small">
        Need a specific row? <button type="button" className="dr-btn" onClick={() => run.quarantine[0] && select({ kind: "row", row: run.quarantine[0] })}>Inspect the first quarantined row</button>
      </p>
    </>
  );
}

/** Source file, the batch's step line and the output files; the steps that touch the chosen column are lit. */
function LineageTrace({ run, active, batch }: { run: RunModel; active: string | null; batch: string }) {
  const { nodes, edges } = run.lineage;
  const sources = nodes.filter((n) => n.kind === "source" && n.batch === batch);
  const steps = nodes.filter((n) => n.kind === "step" && n.batch === batch);
  const outputs = nodes.filter((n) => n.kind === "output");
  const path = active ? run.lineage.columns.find((c) => c.output === active) : null;
  const lit = new Set(path?.steps ?? []);
  const label = new Map(nodes.map((n) => [n.id, n.label]));
  const writes = (id: string) => edges.filter((e) => e.from === id && outputs.some((o) => o.id === e.to)).map((e) => label.get(e.to));
  return (
    <div className="dr-trace">
      <div className="dr-trace-end">
        <p className="dr-small">Read from</p>
        {sources.map((n) => <span key={n.id} className="dr-trace-file dr-mono" data-active={Boolean(path)}>{n.label}</span>)}
      </div>
      <ol className="dr-trace-line" aria-label={`Steps in batch ${batch}${active ? `; steps that touch ${active} are marked` : ""}`}>
        {steps.map((s) => {
          const on = lit.has(s.id);
          const w = writes(s.id);
          return (
            <li key={s.id} data-active={on}>
              <span className="dr-mono">{s.label}</span>
              {on && <span className="dr-sr-only"> (touches {active})</span>}
              {w.length > 0 && <span className="dr-small">writes {w.join(", ")}</span>}
            </li>
          );
        })}
      </ol>
      <div className="dr-trace-end">
        <p className="dr-small">Written to</p>
        {outputs.map((n) => <span key={n.id} className="dr-trace-file dr-mono" data-active={Boolean(path) && n.label === "cleaned.csv"}>{n.label}</span>)}
      </div>
    </div>
  );
}

export function LineageStage({ run, batch }: Props) {
  const shown = batch ?? run.params.batches[0] ?? "";
  const outputs = run.lineage.columns.map((c) => c.output);
  const [active, setActive] = useState<string | null>(outputs[0] ?? null);
  const path = run.lineage.columns.find((c) => c.output === active);
  return (
    <>
      <Head title="Lineage: from a source cell to an output column">
        Pick an output column to trace the source columns and steps it came from in batch {shown} (choose another batch in
        the rack). Every row also carries its batch, source file and source row number.
      </Head>
      <div className="dr-chips" role="group" aria-label="Output column">
        {outputs.map((o) => (
          <button key={o} type="button" className="dr-chip" aria-pressed={o === active} onClick={() => setActive(o)}><span className="dr-mono">{o}</span></button>
        ))}
      </div>
      <div className="dr-panel" style={{ display: "grid", gap: 12 }}>
        <LineageTrace run={run} active={active} batch={shown} />
        {path && (
          <dl className="dr-kv">
            <dt>Output</dt><dd className="dr-mono">{path.output}</dd>
            <dt>From source columns</dt><dd className="dr-mono">{path.sources.join(", ") || "derived"}</dd>
            <dt>Through steps in {shown}</dt>
            <dd>{path.steps.filter((id) => id.includes(`:${shown}:`)).map((id) => run.lineage.nodes.find((n) => n.id === id)?.label ?? id).join(", ") || "identity"}</dd>
          </dl>
        )}
      </div>
    </>
  );
}

export function ReplayStage({ run, config }: Props) {
  const r = run.record;
  return (
    <>
      <Head title="Replay: the same inputs give the same outputs">
        The run's inputs are fingerprinted together; re-running the same request reproduces byte-identical files, and the
        self-checks below were computed inside the run.
      </Head>
      <div className="dr-panel">
        <dl className="dr-kv">
          <dt>Run</dt><dd className="dr-mono dr-wrap-anywhere">{r.runId} (attempt {r.attempt})</dd>
          <dt>Request</dt><dd className="dr-mono">batches {run.params.batches.join(", ")}; {run.params.repairMode}; contract {run.params.contract}</dd>
          <dt>Input manifest</dt><dd className="dr-mono dr-wrap-anywhere">{r.inputManifestHash?.replace(/^sha256:/, "").slice(0, 24) ?? "n/a"}</dd>
          <dt>Source commit</dt><dd>{/^0+$/.test(r.sourceCommit) ? "fixture" : <a className="dr-mono" href={`${config.repoUrl}/commit/${r.sourceCommit}`}>{r.sourceCommit.slice(0, 10)}</a>}</dd>
          <dt>Workflow run</dt><dd>{r.workflowRunId ? <a href={`${config.repoUrl}/actions/runs/${r.workflowRunId}`}>#{r.workflowRunId}</a> : "none (fixture)"}</dd>
          <dt>Requested by</dt><dd>{r.requestSource.type}{r.requestSource.issue ? <> via <a href={`${config.repoUrl}/issues/${r.requestSource.issue}`}>issue #{r.requestSource.issue}</a></> : null}</dd>
          <dt>Engine</dt><dd className="dr-mono">datarefinery {run.packageVersion}</dd>
        </dl>
      </div>
      <div className="dr-panel">
        <p className="dr-h3" style={{ marginBottom: 6 }}>Self-checks recorded by the run</p>
        <ul style={{ listStyle: "none", display: "grid", gap: 6 }}>
          {run.checks.map((c) => (
            <li key={c.name} style={{ display: "grid", gridTemplateColumns: "auto minmax(0, 1fr)", gap: 10 }}>
              <span className={c.passed ? "dr-tone-success" : "dr-tone-danger"}><Icon name={c.passed ? "check" : "cross"} label={c.passed ? "Passed" : "Failed"} /></span>
              <span><strong style={{ fontWeight: 500 }}>{c.name}</strong> <span className="dr-small">{c.detail}</span></span>
            </li>
          ))}
        </ul>
      </div>
      <div className="dr-toolbar">
        <a className="dr-btn" href={run.reportUrl} download>Download the run report</a>
        <a className="dr-btn" href={`${config.repoUrl}/tree/state/runs/${r.runId}`}><Icon name="github" /> Run record on the state branch</a>
        <a className="dr-btn" href="#/runs">All runs</a>
      </div>
      <Disclosure summary="Why replay matters here">
        <p className="dr-prose">A pipeline that repairs data must be repeatable: if the same files and the same request could produce different
          outputs, no repair could be audited. The release gates re-run a request and compare hashes.</p>
      </Disclosure>
    </>
  );
}
