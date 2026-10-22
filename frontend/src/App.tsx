import { type MouseEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ErrorState, Segmented, useIndicator } from "./components/Controls";
import { Equation } from "./components/Flow";
import { Icon } from "./components/Icon";
import { Inspector, type Selection } from "./components/Inspector";
import { type AppConfig, resolveConfig, type Theme } from "./config";
import { STAGES, type StageId } from "./data/model";
import { useRun, useSiteData } from "./data/site";
import { ago } from "./lib/format";
import { animate } from "./lib/motion";
import { parseRoute, type Route, routeHref, useRoute } from "./routing";
import { AboutView, HistoryView, PreviewView, RequestView } from "./views/Pages";
import {
  DriftStage, IntakeStage, LineageStage, ProfileStage, QuarantineStage, ReconcileStage, ReplayStage, TransformStage,
} from "./views/Stages";
import "./styles/app.css";

const STAGE_VIEWS: Record<StageId, typeof IntakeStage> = {
  intake: IntakeStage, profile: ProfileStage, drift: DriftStage, transform: TransformStage,
  quarantine: QuarantineStage, reconcile: ReconcileStage, lineage: LineageStage, replay: ReplayStage,
};

function useResolvedTheme(theme: Theme): "light" | "dark" {
  const q = "(prefers-color-scheme: dark)";
  const [system, setSystem] = useState<"light" | "dark">(() => (typeof window !== "undefined" && window.matchMedia?.(q).matches ? "dark" : "light"));
  useEffect(() => {
    const mq = window.matchMedia?.(q);
    if (!mq) return;
    const on = () => setSystem(mq.matches ? "dark" : "light");
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return theme === "auto" ? system : theme;
}

function Skeleton() {
  return (
    <div className="dr-skeleton" role="status" aria-live="polite">
      <p className="dr-small">Loading the pipeline snapshot.</p>
      <div className="dr-skel" style={{ height: 40, width: "70%" }} />
      <div className="dr-skel" style={{ height: 120 }} />
      <div style={{ display: "grid", gridTemplateColumns: "200px 1fr", gap: 20 }}>
        <div className="dr-skel" style={{ height: 220 }} />
        <div className="dr-skel" style={{ height: 220 }} />
      </div>
    </div>
  );
}

export function DataRefineryApp(props: Partial<AppConfig>) {
  const config = useMemo(() => resolveConfig(props), [props.basePath, props.dataUrl, props.theme, props.showShell, props.routing, props.repoUrl, props.siteUrl]); // eslint-disable-line react-hooks/exhaustive-deps
  const [themeChoice, setThemeChoice] = useState<Theme>(config.theme);
  const theme = useResolvedTheme(themeChoice);
  const [route, navigate] = useRoute(config.routing);
  const { state, reload } = useSiteData(config.dataUrl);
  const [tick, setTick] = useState(0);
  const [batch, setBatch] = useState<string | null>(null);
  const [selection, setSelection] = useState<Selection>(null);
  const surface = useRef<HTMLDivElement>(null);
  const prevStage = useRef<StageId | null>(null);

  const latest = state.status === "ready" ? state.data.snapshot.latest_run_id : null;
  const runId = route.view === "run" ? route.runId ?? latest : null;
  const run = useRun(config.dataUrl, state.status === "ready" ? runId : null, tick);
  const stage: StageId = route.view === "run" ? route.stage : "reconcile";

  const refresh = () => { reload(); setTick((t) => t + 1); };
  const close = useCallback(() => setSelection(null), []);

  // Stage change: slide in the pipeline direction, then move focus to the stage heading.
  useEffect(() => {
    if (route.view !== "run") { prevStage.current = null; return; }
    const before = prevStage.current;
    prevStage.current = stage;
    setSelection(null);
    if (before === null || before === stage) return;
    const dir = STAGES.findIndex((s) => s.id === stage) > STAGES.findIndex((s) => s.id === before) ? 1 : -1;
    animate(surface.current, [{ opacity: 0, transform: `translateX(${dir * 24}px)` }, { opacity: 1, transform: "none" }], { duration: 240 });
    surface.current?.querySelector<HTMLElement>("[data-stage-heading]")?.focus({ preventScroll: true });
  }, [stage, route.view]);

  const onClick = (e: MouseEvent<HTMLDivElement>) => {
    const a = (e.target as HTMLElement).closest("a");
    const href = a?.getAttribute("href");
    if (!a || !href?.startsWith("#/") || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
    if (config.routing === "memory") { e.preventDefault(); navigate(parseRoute(href)); }
  };

  const stageHref = (s: StageId) => routeHref({ view: "run", runId: route.view === "run" ? route.runId : undefined, stage: s });
  const stageIndex = STAGES.findIndex((s) => s.id === stage);
  const { container: railRef, style: railStyle } = useIndicator<HTMLElement>('a[aria-current="step"]', `${route.view}-${stage}`);
  const goStage = (s: StageId) => navigate({ view: "run", runId: route.view === "run" ? route.runId : undefined, stage: s });

  let body: React.ReactNode;
  if (state.status === "loading" || (route.view === "run" && state.status === "ready" && run.status === "loading")) body = <Skeleton />;
  else if (state.status === "error") body = <div className="dr-state"><ErrorState title="The pipeline snapshot did not load" error={state.error} onRetry={refresh} /></div>;
  else if (route.view === "request") body = <RequestView config={config} />;
  else if (route.view === "about") body = <AboutView config={config} />;
  else if (route.view === "preview") body = <PreviewView rules={run.status === "ready" ? run.data.rules : state.status === "ready" ? state.data.rules : []} />;
  else if (state.status === "empty") body = (
    <div className="dr-state">
      <h1 className="dr-h1">No completed runs yet.</h1>
      <p className="dr-prose">A run is published only after its outputs pass validation. The first one will show every row it read and where each one went.</p>
      <p><a className="dr-btn dr-btn-primary" href="#/request">How to request a run</a></p>
    </div>
  );
  else if (state.status !== "ready") body = <Skeleton />;
  else if (route.view === "history") body = <HistoryView site={state.data} config={config} />;
  else if (run.status === "error") body = <div className="dr-state"><ErrorState title={runId && !state.data.snapshot.runs_included.includes(runId) ? `Run ${runId} is not in this snapshot` : "This run did not load"} error={run.error} onRetry={refresh} /></div>;
  else if (run.status === "ready") {
    const model = run.data.model;
    const View = STAGE_VIEWS[stage];
    const meter = (b: string) => model.accounting.find((a) => a.batch === b);
    body = (
      <>
        <div className="dr-runhead">
          <p className="dr-meta dr-wrap-anywhere">
            Run <span className="dr-mono">{model.record.runId}</span>, batches {model.params.batches.join(", ")}, {model.params.repairMode} mode, contract {model.params.contract}.
          </p>
          <Equation rows={batch ? model.accounting.filter((a) => a.batch === batch) : model.accounting} label={batch ? `Batch ${batch}` : "All batches"} />
        </div>
        <div className="dr-bench">
          <nav className="dr-rack" aria-label="Batches">
            <p className="dr-rack-title">Batches</p>
            <button type="button" className="dr-tray" aria-pressed={batch === null} onClick={() => setBatch(null)}>
              <span className="dr-h3">All batches</span>
              <span className="dr-small dr-mono">{model.accounting.reduce((s, a) => s + a.rowsIn, 0).toLocaleString("en-US")} rows</span>
            </button>
            {model.params.batches.map((b) => {
              const a = meter(b);
              const total = a?.rowsIn || 1;
              return (
                <button key={b} type="button" className="dr-tray" aria-pressed={batch === b} onClick={() => setBatch(b)}>
                  <span className="dr-h3 dr-mono">{b}</span>
                  <span className="dr-small">{model.sources.find((s) => s.batch === b)?.format.toUpperCase()} {a ? `${a.rowsIn.toLocaleString("en-US")} rows` : ""}</span>
                  {a && (
                    <span className="dr-tray-meter" role="img" aria-label={`${a.accepted} accepted, ${a.quarantined} quarantined, ${a.merged} merged`}>
                      <span style={{ width: `${(a.accepted / total) * 100}%`, background: "var(--dr-accepted-fill)" }} />
                      <span style={{ width: `${(a.quarantined / total) * 100}%`, background: "var(--dr-quarantined-fill)" }} />
                      <span style={{ width: `${(a.merged / total) * 100}%`, background: "var(--dr-merged-fill)" }} />
                    </span>
                  )}
                </button>
              );
            })}
          </nav>
          <div className="dr-surface" ref={surface}>
            <View run={model} batch={batch} select={setSelection} selection={selection} config={config} />
            <div className="dr-toolbar" role="navigation" aria-label="Previous and next stage">
              {stageIndex > 0 && <a className="dr-btn" href={stageHref(STAGES[stageIndex - 1]!.id)}>Back to {STAGES[stageIndex - 1]!.label}</a>}
              {stageIndex < STAGES.length - 1 && <a className="dr-btn dr-btn-primary" href={stageHref(STAGES[stageIndex + 1]!.id)}>Next: {STAGES[stageIndex + 1]!.label}</a>}
            </div>
          </div>
          <Inspector selection={selection} run={model} onClose={close} />
        </div>
      </>
    );
  } else body = <Skeleton />;

  const snap = state.status === "ready" ? state.data.snapshot : null;
  const release = state.status === "ready" ? state.data.release : null;
  const topLink = (r: Route, label: string, extra = "") => (
    <a className={extra} href={routeHref(r)} aria-current={route.view === r.view ? "page" : undefined}>{label}</a>
  );

  return (
    <div className="dr-root" data-theme={theme} onClick={onClick}>
      <a className="dr-skip" href="#dr-main" onClick={(e) => { e.preventDefault(); document.getElementById("dr-main")?.focus(); }}>Skip to content</a>
      {config.showShell && (
        <header className="dr-housing">
          <div className="dr-housing-top">
            <a className="dr-wordmark" href="#/">Data Refinery</a>
            <nav className="dr-topnav" aria-label="Primary">
              {topLink({ view: "history" }, "Runs")}
              {topLink({ view: "preview" }, "Preview a CSV", "dr-hide-sm")}
              {topLink({ view: "request" }, "Request a run")}
              {topLink({ view: "about" }, "About", "dr-hide-sm")}
              <button type="button" onClick={refresh} aria-label="Refresh data from the published snapshot"><Icon name="refresh" /></button>
            </nav>
          </div>
          {route.view === "run" && (
            <>
              <nav className="dr-rail" aria-label="Pipeline stages" ref={railRef}>
                {railStyle && <span className="dr-rail-indicator" style={railStyle} aria-hidden />}
                <ol>
                  {STAGES.map((s, i) => (
                    <li key={s.id}>
                      <a href={stageHref(s.id)} aria-current={s.id === stage ? "step" : undefined}>
                        <span className="dr-step-no">{String(i + 1).padStart(2, "0")}</span>
                        {s.label}
                      </a>
                    </li>
                  ))}
                </ol>
              </nav>
              <div className="dr-stepper">
                <button type="button" className="dr-btn" aria-label="Previous stage" disabled={stageIndex === 0} onClick={() => goStage(STAGES[stageIndex - 1]!.id)}>
                  <span aria-hidden>&lsaquo;</span>
                </button>
                <label className="dr-sr-only" htmlFor="dr-stage-select">Pipeline stage</label>
                <select id="dr-stage-select" value={stage} onChange={(e) => goStage(e.target.value as StageId)}>
                  {STAGES.map((s, i) => <option key={s.id} value={s.id}>Stage {i + 1} of {STAGES.length}: {s.label}</option>)}
                </select>
                <button type="button" className="dr-btn" aria-label="Next stage" disabled={stageIndex === STAGES.length - 1} onClick={() => goStage(STAGES[stageIndex + 1]!.id)}>
                  <span aria-hidden>&rsaquo;</span>
                </button>
              </div>
            </>
          )}
        </header>
      )}
      {snap?.mode === "fixture" && (
        <div className="dr-banner" role="note"><Icon name="info" /> Fixture data: a real pipeline run built for automated browser tests, not a published run.</div>
      )}
      {state.status === "ready" && state.stale && snap && (
        <div className="dr-banner" role="status">
          <span className="dr-tone-warning"><Icon name="warn" /></span>
          <span>This snapshot is {ago(snap.generated_at)} old, older than its 14-day freshness window. The results shown are the last ones published.</span>
          <button type="button" className="dr-btn" onClick={refresh}><Icon name="refresh" /> Refresh</button>
        </div>
      )}
      {release?.status === "blocked" && (
        <div className="dr-banner" role="status"><span className="dr-tone-danger"><Icon name="cross" /></span> The latest release check is blocked.</div>
      )}
      <main id="dr-main" tabIndex={-1} style={{ outline: "none" }}>{body}</main>
      {config.showShell && (
        <footer className="dr-footer">
          <div style={{ display: "grid", gap: 10 }}>
            <p>Synthetic order data processed by a deterministic pipeline.{snap ? ` Snapshot generated ${snap.generated_at.replace("T", " ").replace("Z", " UTC")}.` : ""}{release ? ` Latest release check: ${release.status}.` : ""}</p>
            <Segmented label="Colour theme" value={themeChoice} options={[{ value: "auto", label: "System" }, { value: "light", label: "Light" }, { value: "dark", label: "Dark" }]} onChange={setThemeChoice} />
          </div>
          <nav aria-label="Project links">
            <a href={config.repoUrl}>Source</a>
            <a href={`${config.repoUrl}/tree/state`}>State branch</a>
            <a href={`${config.repoUrl}/blob/main/docs/ARCHITECTURE.md`}>Architecture</a>
          </nav>
        </footer>
      )}
    </div>
  );
}
