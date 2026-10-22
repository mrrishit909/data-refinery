import { useRef, useState } from "react";
import { Icon } from "../components/Icon";
import type { AppConfig } from "../config";
import type { SiteData } from "../data/site";
import { routeHref } from "../routing";
import { type ContractRule, LIMITS, preview, type PreviewResult } from "../lib/preview";

const fmt = (n: number) => n.toLocaleString("en-US");

export function HistoryView({ site, config }: { site: SiteData; config: AppConfig }) {
  const included = new Set(site.snapshot.runs_included);
  return (
    <div className="dr-page">
      <div className="dr-section-head">
        <h1 className="dr-h1">Every run, including the failed ones.</h1>
        <p className="dr-prose">
          The ledger lists every recorded attempt. The {included.size} most recent completed runs are in this site's snapshot;
          older results stay on the state branch.
        </p>
      </div>
      <ol style={{ listStyle: "none", display: "grid", gap: 10 }} aria-label="Run ledger, newest first">
        {site.index.map((r) => (
          <li key={r.run_id} className="dr-card">
            <div className="dr-toolbar" style={{ justifyContent: "space-between" }}>
              {included.has(r.run_id) ? (
                <a className="dr-mono dr-wrap-anywhere" href={routeHref({ view: "run", runId: r.run_id, stage: "reconcile" })}>{r.run_id}</a>
              ) : (
                <span className="dr-mono dr-wrap-anywhere">{r.run_id}</span>
              )}
              <span className={`dr-tag ${r.status === "completed" ? "dr-tone-success" : r.status === "failed" ? "dr-tone-danger" : r.status === "blocked" ? "dr-tone-warning" : "dr-tone-muted"}`}>{r.status}</span>
            </div>
            <p className="dr-small">
              Attempt {r.attempt}, created {r.created_at.replace("T", " ").replace("Z", " UTC")}, source {r.source}
              {r.issue ? <> from <a href={`${config.repoUrl}/issues/${r.issue}`}>issue #{r.issue}</a></> : null}
            </p>
            {r.headline && <p>{r.headline}</p>}
          </li>
        ))}
      </ol>
    </div>
  );
}

type PreviewState =
  | { status: "idle" }
  | { status: "parsing"; name: string }
  | { status: "ready"; name: string; result: PreviewResult }
  | { status: "rejected"; name: string; reason: string };

export function PreviewView({ rules }: { rules: ContractRule[] }) {
  const [state, setState] = useState<PreviewState>({ status: "idle" });
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const handle = async (file: File | undefined) => {
    if (!file) return;
    if (!/\.csv$/i.test(file.name) && file.type !== "text/csv") {
      setState({ status: "rejected", name: file.name, reason: "Only CSV files can be previewed here." });
      return;
    }
    if (file.size > LIMITS.bytes) {
      setState({ status: "rejected", name: file.name, reason: `The file is ${fmt(file.size)} bytes; the preview accepts up to ${fmt(LIMITS.bytes)}.` });
      return;
    }
    setState({ status: "parsing", name: file.name });
    const text = await file.text();
    const result = preview(text, rules);
    if (result.rows > LIMITS.rows) {
      setState({ status: "rejected", name: file.name, reason: `The file has ${fmt(result.rows)} rows; the preview accepts up to ${fmt(LIMITS.rows)}.` });
      return;
    }
    if (result.columns.length === 0) {
      setState({ status: "rejected", name: file.name, reason: "No header row and data rows were found." });
      return;
    }
    setState({ status: "ready", name: file.name, result });
  };

  return (
    <div className="dr-page">
      <div className="dr-section-head">
        <h1 className="dr-h1">Preview a CSV against the orders contract.</h1>
        <p className="dr-prose">
          Profiles your file and checks it against contract {rules.length ? "orders.v1" : "(contract unavailable)"} entirely in this
          browser tab. Nothing is uploaded, sent or saved; closing the tab discards it. Durable runs only process the committed
          fixtures.
        </p>
      </div>
      <label
        className="dr-dropzone"
        data-over={over}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); void handle(e.dataTransfer.files[0]); }}
      >
        <Icon name="info" />
        <span><strong>Choose a CSV file</strong> or drop it here</span>
        <span className="dr-small">Up to {fmt(LIMITS.bytes / 1_000_000)} MB and {fmt(LIMITS.rows)} rows</span>
        <input ref={input} type="file" accept=".csv,text/csv" className="dr-sr-only" onChange={(e) => void handle(e.target.files?.[0])} />
      </label>
      <div role="status" aria-live="polite">
        {state.status === "parsing" && <p className="dr-small">Reading {state.name}.</p>}
        {state.status === "rejected" && (
          <p className="dr-tone-danger"><Icon name="cross" /> {state.name} was not previewed: {state.reason}</p>
        )}
      </div>
      {state.status === "ready" && (
        <PreviewReport name={state.name} result={state.result} onClear={() => { setState({ status: "idle" }); if (input.current) input.current.value = ""; }} />
      )}
    </div>
  );
}

function PreviewReport({ name, result, onClear }: { name: string; result: PreviewResult; onClear: () => void }) {
  const failing = result.columns.filter((c) => c.contract && c.contract.passRate < 1);
  return (
    <div style={{ display: "grid", gap: 18 }}>
      <div className="dr-panel" style={{ display: "grid", gap: 8 }}>
        <p className="dr-h3"><span className="dr-mono">{name}</span>: {fmt(result.rows)} rows, {result.columns.length} columns</p>
        <ul style={{ listStyle: "none", display: "grid", gap: 4 }}>
          <li>{result.missing.length === 0 ? <span className="dr-tone-success"><Icon name="check" /> Every contract column is present.</span>
            : <span className="dr-tone-warning"><Icon name="warn" /> {result.missing.length} contract column{result.missing.length === 1 ? " is" : "s are"} missing.</span>}</li>
          {result.missing.map((m) => (
            <li key={m.name} className="dr-small"><span className="dr-mono">{m.name}</span> missing{m.candidates.length ? <>; possibly renamed to <span className="dr-mono">{m.candidates.join(", ")}</span></> : ""}</li>
          ))}
          {result.extra.length > 0 && <li className="dr-small">Not in the contract: <span className="dr-mono">{result.extra.join(", ")}</span></li>}
          <li>{failing.length === 0 ? <span className="dr-tone-success"><Icon name="check" /> Present contract columns pass their rules.</span>
            : <span className="dr-tone-warning"><Icon name="warn" /> {failing.length} column{failing.length === 1 ? " has" : "s have"} values that break the contract.</span>}</li>
        </ul>
        <div><button type="button" className="dr-btn" onClick={onClear}>Clear the preview</button></div>
      </div>
      <div className="dr-panel">
        <div className="dr-table-wrap" tabIndex={0} role="region" aria-label="Column profile of your file">
          <table className="dr-table">
            <thead><tr><th scope="col">Column</th><th scope="col">Looks like</th><th scope="col" className="dr-num">Filled</th><th scope="col" className="dr-num">Empty</th><th scope="col" className="dr-num">Distinct</th><th scope="col">Contract</th><th scope="col">Examples</th></tr></thead>
            <tbody>
              {result.columns.map((c) => (
                <tr key={c.name}>
                  <th scope="row" className="dr-mono">{c.name}</th>
                  <td>{c.inferred}</td>
                  <td className="dr-num dr-mono">{fmt(c.filled)}</td>
                  <td className="dr-num dr-mono">{fmt(c.empty)}</td>
                  <td className="dr-num dr-mono">{fmt(c.distinct)}</td>
                  <td>{c.contract ? <span className={c.contract.passRate === 1 ? "dr-tone-success" : "dr-tone-warning"}>{(c.contract.passRate * 100).toFixed(1)}% pass</span> : <span className="dr-small">not in contract</span>}</td>
                  <td className="dr-mono dr-small dr-wrap-anywhere">{c.examples.join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

export function RequestView({ config }: { config: AppConfig }) {
  return (
    <div className="dr-page" style={{ maxWidth: 780 }}>
      <div className="dr-section-head">
        <h1 className="dr-h1">Request a pipeline run.</h1>
        <p className="dr-prose">
          This site is read-only. A new run is a GitHub issue that picks which committed batches to process and whether
          unambiguous repairs may be applied. A GitHub Actions workflow computes it and publishes the result here. Opening
          an issue needs a GitHub account.
        </p>
      </div>
      <p className="dr-prose">
        The repository owner's requests start automatically. Anyone else's request waits, labelled blocked, until the owner
        approves it. No request can start paid AI inference: the pipeline is deterministic Python. Uploading your own data
        is not supported for durable runs; use the in-browser preview instead.
      </p>
      <ol className="dr-steps">
        <li><span>The intake workflow validates the form against the request schema and labels the issue requested, or blocked with the reason.</span></li>
        <li><span>Identical parameters link to the existing run instead of computing again.</span></li>
        <li><span>The run processes the batches on a GitHub-hosted runner, validates its outputs and writes them to the state branch.</span></li>
        <li><span>The site redeploys and the issue gets a link to the result, with the evidence kept either way.</span></li>
      </ol>
      <div className="dr-toolbar">
        <a className="dr-btn dr-btn-primary" href={`${config.repoUrl}/issues/new?template=run-request.yml`}><Icon name="github" /> Open the run request form on GitHub</a>
        <a className="dr-btn" href={`${config.repoUrl}/issues?q=label%3Atype%3Arun`}>See existing requests</a>
      </div>
      <p className="dr-small">Limits keep the workbench inside GitHub's free tier: at most 40 runs a month, 5 at a time, 2 MB of results per run.</p>
    </div>
  );
}

export function AboutView({ config }: { config: AppConfig }) {
  const rows: [string, string, string][] = [
    ["Published runs, tables, lineage, downloads", "Cached", "A static snapshot of the state branch, rebuilt after each run; its time is shown in the footer."],
    ["CSV preview", "In your browser", "Profiles and checks a file in memory; nothing is uploaded or stored."],
    ["New runs", "Live, owner-authorized", "Through GitHub Issues and Actions, over committed fixtures only."],
    ["AI grading of the site", "Unavailable", "Optional; runs only if the owner configures inference credentials."],
    ["Private or uploaded data in durable runs", "Not supported", "By design: the repository and site are public."],
  ];
  return (
    <div className="dr-page" style={{ maxWidth: 860 }}>
      <div className="dr-section-head">
        <h1 className="dr-h1">How the refinery works, and its limits.</h1>
        <p className="dr-prose">
          Data Refinery treats a data load as an accounting problem. Each batch is fingerprinted, profiled, checked against
          a contract and compared with the previous batch. Drift becomes a repair proposal with evidence and a confidence
          score; only unambiguous repairs are applied, and only when asked. Rows that still fail are quarantined with
          reasons. The run must then prove that every input row is accounted for.
        </p>
      </div>
      <ul style={{ listStyle: "disc", paddingLeft: 20, display: "grid", gap: 6 }} className="dr-prose">
        <li>All batches are synthetic e-commerce orders generated for this project, with known, documented defects.</li>
        <li>One contract (orders.v1) and one fixed, synthetic exchange-rate table; no live FX.</li>
        <li>Repairs cover renames, date formats, unit scale and unambiguous category variants; business meaning is never guessed.</li>
        <li>Intake is bounded: four batches, 5,000 rows and 2 MB per file, one XLSX sheet.</li>
      </ul>
      <div className="dr-table-wrap" tabIndex={0} role="region" aria-label="Capability status">
        <table className="dr-table">
          <thead><tr><th scope="col">Capability</th><th scope="col">Status</th><th scope="col">Detail</th></tr></thead>
          <tbody>
            {rows.map(([a, b, c]) => <tr key={a}><th scope="row" style={{ fontWeight: 500 }}>{a}</th><td>{b}</td><td>{c}</td></tr>)}
          </tbody>
        </table>
      </div>
      <p className="dr-small">Source, workflows and documentation: <a href={config.repoUrl}>{config.repoUrl.replace("https://", "")}</a></p>
    </div>
  );
}
