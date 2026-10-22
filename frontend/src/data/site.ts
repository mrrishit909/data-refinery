import { useCallback, useEffect, useRef, useState } from "react";
import type { ContractRule } from "../lib/preview";
import { adapt, type RawRun } from "./adapter";
import { type ContractFile, rulesFromContract } from "./contract";
import { parseCsv } from "./csv";
import { fetchJson, fetchText, LoadError } from "./load";
import type { RunModel } from "./model";

export interface Snapshot {
  project: string;
  mode: "state-snapshot" | "fixture";
  generated_at: string;
  source_commit: string;
  state_commit: string;
  latest_run_id: string | null;
  runs_included: string[];
  stale_after_hours: number;
  files?: { release: boolean; memory: boolean };
}

export interface IndexEntry {
  run_id: string;
  request_id: string;
  status: string;
  attempt: number;
  created_at: string;
  completed_at: string | null;
  source: string;
  issue: number | null;
  headline: string | null;
}

export interface Release {
  release_id: string;
  status: "released" | "blocked";
  created_at: string;
}

export interface SiteData {
  snapshot: Snapshot;
  index: IndexEntry[];
  release: Release | null;
  rules: ContractRule[];
}

export type LoadState<T> =
  | { status: "loading" }
  | { status: "error"; error: Error }
  | { status: "empty" }
  | { status: "ready"; data: T; stale: boolean };

export function isStale(s: Snapshot, now = Date.now()): boolean {
  const t = Date.parse(s.generated_at);
  return Number.isFinite(t) && now - t > s.stale_after_hours * 3600_000;
}

export function useSiteData(dataUrl: string): { state: LoadState<SiteData>; reload: () => void } {
  const [state, setState] = useState<LoadState<SiteData>>({ status: "loading" });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const ctrl = new AbortController();
    setState({ status: "loading" });
    (async () => {
      try {
        const snapshot = await fetchJson<Snapshot>(`${dataUrl}snapshot.json`, ctrl.signal);
        const index = await fetchJson<{ runs: IndexEntry[] }>(`${dataUrl}index.json`, ctrl.signal);
        const release = snapshot.files?.release ? await fetchJson<Release>(`${dataUrl}release.json`, ctrl.signal) : null;
        if (!snapshot.latest_run_id) {
          setState({ status: "empty" });
          return;
        }
        let rules: ContractRule[] = [];
        try {
          rules = rulesFromContract(await fetchJson<ContractFile>(`${dataUrl}runs/${encodeURIComponent(snapshot.latest_run_id)}/contract.json`, ctrl.signal));
        } catch (err) {
          if (!(err instanceof LoadError)) throw err; // a run without a contract snapshot still renders
        }
        setState({ status: "ready", data: { snapshot, index: index.runs, release, rules }, stale: isStale(snapshot) });
      } catch (err) {
        if (!ctrl.signal.aborted) setState({ status: "error", error: err as Error });
      }
    })();
    return () => ctrl.abort();
  }, [dataUrl, tick]);
  return { state, reload: useCallback(() => setTick((t) => t + 1), []) };
}

const JSON_FILES = ["run.json", "manifest.json", "reconciliation.json", "repairs.json", "ambiguities.json", "drift.json",
  "profiles.json", "plan.json", "samples.json", "quality.json", "lineage.json", "checks.json", "contract.json"] as const;

async function loadRun(dataUrl: string, runId: string, signal: AbortSignal): Promise<{ model: RunModel; rules: ContractRule[] }> {
  const base = `${dataUrl}runs/${encodeURIComponent(runId)}/`;
  const json = await Promise.all(JSON_FILES.map((f) => fetchJson<unknown>(base + f, signal)));
  const [cleaned, quarantine] = await Promise.all([fetchText(base + "cleaned.csv", signal), fetchText(base + "quarantine.csv", signal)]);
  const raw = Object.fromEntries(JSON_FILES.map((f, i) => [f.replace(".json", ""), json[i]])) as unknown as RawRun;
  raw.cleaned = parseCsv(cleaned);
  raw.quarantine = parseCsv(quarantine);
  const model = adapt(raw, base);
  return { model, rules: rulesFromContract(raw.contract as ContractFile) };
}

export function useRun(dataUrl: string, runId: string | null, tick: number): LoadState<{ model: RunModel; rules: ContractRule[] }> {
  const cache = useRef(new Map<string, { model: RunModel; rules: ContractRule[] }>());
  const [state, setState] = useState<LoadState<{ model: RunModel; rules: ContractRule[] }>>({ status: "loading" });
  useEffect(() => { cache.current.clear(); }, [tick]);
  useEffect(() => {
    if (!runId) {
      setState({ status: "empty" });
      return;
    }
    const hit = cache.current.get(runId);
    if (hit) {
      setState({ status: "ready", data: hit, stale: false });
      return;
    }
    const ctrl = new AbortController();
    setState({ status: "loading" });
    loadRun(dataUrl, runId, ctrl.signal).then(
      (data) => { cache.current.set(runId, data); setState({ status: "ready", data, stale: false }); },
      (error) => { if (!ctrl.signal.aborted) setState({ status: "error", error }); },
    );
    return () => ctrl.abort();
  }, [dataUrl, runId, tick]);
  return state;
}
