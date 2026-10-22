import { describe, type ContractFile } from "./contract";
import type { ColumnProfile, LineageModel, RunModel } from "./model";

/** Raw published files of one run (JSON parsed, CSV parsed to header-keyed rows). */
export interface RawRun {
  run: Record<string, unknown>;
  manifest: any; // eslint-disable-line @typescript-eslint/no-explicit-any
  reconciliation: any; // eslint-disable-line @typescript-eslint/no-explicit-any
  repairs: any; // eslint-disable-line @typescript-eslint/no-explicit-any
  ambiguities: any; // eslint-disable-line @typescript-eslint/no-explicit-any
  drift: any; // eslint-disable-line @typescript-eslint/no-explicit-any
  profiles: any; // eslint-disable-line @typescript-eslint/no-explicit-any
  plan: any; // eslint-disable-line @typescript-eslint/no-explicit-any
  samples: any; // eslint-disable-line @typescript-eslint/no-explicit-any
  quality: any; // eslint-disable-line @typescript-eslint/no-explicit-any
  lineage: any; // eslint-disable-line @typescript-eslint/no-explicit-any
  checks: any; // eslint-disable-line @typescript-eslint/no-explicit-any
  contract: ContractFile;
  cleaned: Record<string, string>[];
  quarantine: Record<string, string>[];
}

const entries = <T,>(o: Record<string, T> | undefined | null): [string, T][] => Object.entries(o ?? {});

function need<T>(value: T | undefined | null, what: string): T {
  if (value === undefined || value === null) throw new Error(`The run's published files are missing ${what}.`);
  return value;
}

function text(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const d = detail as Record<string, unknown>;
    if ("from" in d && "to" in d) return `${String(d.from)} to ${String(d.to)}`;
    return Object.entries(d).map(([k, v]) => `${k.replace(/_/g, " ")}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`).join("; ");
  }
  return "";
}

/** A repair's detail as one plain sentence; unknown kinds fall back to key: value pairs. */
export function repairText(kind: string, detail: unknown): string {
  const d = (detail && typeof detail === "object" ? detail : {}) as Record<string, unknown>;
  const rows = d.rows_affected === undefined ? "" : ` (${d.rows_affected} rows)`;
  if (kind === "rename") return `Rename column ${d.from} to ${d.to}.`;
  if (kind === "date_format") return `Read ${d.column} as ${d.format} and write ${d.to_format}${rows}.`;
  if (kind === "category_map") {
    const why = `${d.rule}${rows ? `, ${d.rows_affected} rows` : ""}`;
    return d.to === null || d.to === undefined ? `"${d.from}" has no safe mapping (${why}).` : `"${d.from}" becomes "${d.to}" (${why}).`;
  }
  if (kind === "unit_scale") {
    const g = (d.guard ?? {}) as Record<string, number>;
    const band = g.median_min !== undefined ? `; only when the batch median is between ${g.median_min} and ${g.median_max}` : "";
    return `Convert ${d.column} from ${d.from_unit} to ${d.to_unit}, multiplying by ${d.factor}${band}.`;
  }
  return text(detail);
}

function profileList(byBatch: Record<string, Record<string, Record<string, Record<string, unknown>>>>, stage: "raw" | "accepted"): ColumnProfile[] {
  const out: ColumnProfile[] = [];
  for (const [batch, stages] of entries(byBatch)) {
    for (const [column, p] of entries(stages[stage])) {
      const formats = entries(p.date_formats as Record<string, number>).filter(([k, n]) => n > 0 && k !== "unparsed").map(([k]) => k);
      const unparsed = (p.date_formats as Record<string, number> | undefined)?.unparsed ?? 0;
      const notes: string[] = [];
      if (unparsed) notes.push(`${unparsed} values did not parse as any known date format`);
      if (p.currencies) notes.push(`Currencies: ${entries(p.currencies as Record<string, number>).map(([c, n]) => `${c} ${n}`).join(", ")}`);
      if (p.scale) {
        const s = p.scale as Record<string, number>;
        notes.push(`Typical values: p05 ${s.p05}, median ${s.median}, p95 ${s.p95}`);
      }
      out.push({
        batch,
        column,
        inferredType: String(p.inferred_type ?? "unknown"),
        nulls: Number(p.null_count ?? 0),
        nullRate: Number(p.null_rate ?? 0),
        distinct: Number(p.distinct_count ?? 0),
        min: p.min === undefined || p.min === null ? null : String(p.min),
        max: p.max === undefined || p.max === null ? null : String(p.max),
        topValues: ((p.top_values as [unknown, number][]) ?? []).map(([v, n]) => [String(v ?? ""), Number(n)] as [string, number]),
        dateFormats: formats,
        notes,
      });
    }
  }
  return out;
}

export function adapt(raw: RawRun, base: string): RunModel {
  const m = need(raw.manifest, "manifest.json");
  const recBatches = need(raw.reconciliation?.batches, "the per-batch accounting in reconciliation.json") as Record<string, Record<string, unknown>>;
  const run = raw.run as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

  const batchOf = (file: string) => /(\d{4}-\d{2})/.exec(file)?.[1] ?? file;
  const lineageNodes = ((raw.lineage?.nodes ?? []) as Record<string, any>[]).map((n) => ({ // eslint-disable-line @typescript-eslint/no-explicit-any
    id: String(n.id),
    kind: (n.type === "source" || n.type === "output" ? n.type : "step") as "source" | "step" | "output",
    label: String(n.name ?? n.file ?? String(n.id).replace(/^output:/, "")),
    batch: n.batch ? String(n.batch) : null,
  }));
  const lineage: LineageModel = {
    nodes: lineageNodes,
    edges: ((raw.lineage?.edges ?? []) as Record<string, unknown>[]).map((e) => ({ from: String(e.from), to: String(e.to), label: e.rows === undefined ? null : String(e.rows) })),
    columns: entries(raw.lineage?.columns as Record<string, any>) // eslint-disable-line @typescript-eslint/no-explicit-any
      .filter(([, c]) => c.kind !== "lineage")
      .map(([name, c]) => {
        const byBatch = entries(c.by_batch as Record<string, { source_column?: string; steps?: string[] }>);
        return {
          output: name,
          sources: [...new Set([...byBatch.map(([, b]) => b.source_column).filter(Boolean) as string[], ...((c.inputs as string[]) ?? [])])],
          steps: byBatch.flatMap(([, b]) => b.steps ?? []),
        };
      }),
  };

  return {
    record: {
      runId: String(run.run_id),
      status: String(run.status),
      attempt: Number(run.attempt ?? 1),
      createdAt: String(run.created_at),
      completedAt: run.completed_at ? String(run.completed_at) : null,
      sourceCommit: String(run.source_commit ?? ""),
      workflowRunId: run.workflow_run_id ?? null,
      inputManifestHash: run.input_manifest_hash ?? m.input_manifest_hash ?? null,
      requestSource: { type: String(run.request_source?.type ?? "manual"), issue: run.request_source?.issue_number ?? null, actor: run.request_source?.actor ?? null },
    },
    params: { batches: m.params.batches, repairMode: m.params.repair_mode, contract: m.params.contract, fxTable: m.params.fx_table },
    sources: (m.sources as Record<string, any>[]).map((s) => ({ // eslint-disable-line @typescript-eslint/no-explicit-any
      batch: String(s.batch ?? batchOf(String(s.file))), file: String(s.file), format: String(s.format), sha256: String(s.sha256),
      bytes: Number(s.bytes), rows: Number(s.rows), columns: (s.columns as string[]) ?? [],
    })),
    accounting: entries(recBatches).map(([batch, b]) => ({
      batch,
      rowsIn: Number(need(b.rows_in, `rows_in for ${batch}`)),
      accepted: Number(need(b.rows_accepted, `rows_accepted for ${batch}`)),
      quarantined: Number(need(b.rows_quarantined, `rows_quarantined for ${batch}`)),
      merged: Number(need(b.rows_merged_duplicates, `rows_merged_duplicates for ${batch}`)),
      byReason: (b.quarantine_reasons as Record<string, number>) ?? {},
      identityHolds: Boolean(b.identity_holds),
    })),
    repairs: ((raw.repairs?.repairs ?? []) as Record<string, any>[]).map((r) => ({ // eslint-disable-line @typescript-eslint/no-explicit-any
      id: String(r.id), batch: String(r.batch), kind: String(r.kind), detail: repairText(String(r.kind), r.detail),
      evidence: { ...(r.evidence ?? {}), ...(r.confidence_basis ? { confidence_basis: r.confidence_basis } : {}) },
      confidence: Number(r.confidence ?? 0), autoApplicable: Boolean(r.auto_applicable), applied: Boolean(r.applied),
      ambiguity: r.ambiguity ? String(r.ambiguity) : null,
    })),
    ambiguities: ((raw.ambiguities?.ambiguities ?? []) as Record<string, any>[]).map((a) => ({ // eslint-disable-line @typescript-eslint/no-explicit-any
      id: String(a.id), batch: String(a.batch), column: String(a.column), value: String(a.value),
      reason: [a.reason, a.resolution].filter(Boolean).join(" "), rows: Number(a.rows ?? 0),
    })),
    drift: entries(raw.drift?.batches as Record<string, { findings?: Record<string, unknown>[] }>).flatMap(([batch, d]) =>
      (d.findings ?? []).map((f) => ({ batch, kind: String(f.kind), column: f.column ? String(f.column) : null, detail: String(f.detail ?? "") })),
    ),
    profiles: profileList(raw.profiles?.batches ?? {}, "raw"),
    profilesAccepted: profileList(raw.profiles?.batches ?? {}, "accepted"),
    plan: entries(raw.plan?.batches as Record<string, { steps?: Record<string, any>[] }>).flatMap(([batch, p]) => // eslint-disable-line @typescript-eslint/no-explicit-any
      (p.steps ?? []).map((s) => ({
        batch, order: Number(s.order), name: String(s.name),
        detail: [s.kind === "repair" ? `repair ${s.repair}` : s.kind, s.rows_changed ? `${s.rows_changed} rows changed` : "", text(s.detail)].filter(Boolean).join("; "),
        rowsIn: s.rows_in ?? null, rowsOut: s.rows_out ?? null,
      })),
    ),
    quarantine: raw.quarantine.map((q) => {
      let values: Record<string, string> = {};
      try { values = JSON.parse(q.raw ?? "{}"); } catch { values = { raw: q.raw ?? "" }; }
      return {
        batch: q._batch ?? "", sourceFile: q._source_file ?? "", sourceRow: Number(q._source_row),
        reasons: (q.reason_codes ?? "").split(/[;|,\s]+/).filter(Boolean),
        values: { ...Object.fromEntries(Object.entries(values).map(([k, v]) => [k, v === null ? "" : String(v)])), ...(q.reason_detail ? { _reason_detail: q.reason_detail } : {}) },
      };
    }),
    cleaned: raw.cleaned.map(({ _batch, _source_file, _source_row, ...values }) => ({
      batch: _batch ?? "", sourceFile: _source_file ?? "", sourceRow: Number(_source_row), values,
    })),
    samples: entries(raw.samples?.batches as Record<string, Record<string, any>[]>).flatMap(([batch, list]) => // eslint-disable-line @typescript-eslint/no-explicit-any
      list.map((s) => ({
        batch, sourceRow: Number(s._source_row),
        raw: Object.fromEntries(entries(s.raw as Record<string, unknown>).map(([k, v]) => [k, v === null ? "" : String(v)])),
        cleaned: s.outcome === "accepted" && s.cleaned ? Object.fromEntries(entries(s.cleaned as Record<string, unknown>).map(([k, v]) => [k, v === null ? "" : String(v)])) : null,
        reasons: ((s.reasons ?? []) as (string | { code: string })[]).map((r) => (typeof r === "string" ? r : String(r.code))),
        note: [
          ...((s.reasons ?? []) as (string | { message?: string })[]).map((r) => (typeof r === "string" ? "" : r.message ? `${r.message}.` : "")),
          s.outcome === "merged" || s.duplicate_of ? `Merged as a duplicate of row ${s.duplicate_of}.` : "",
          (s.repairs as string[] | undefined)?.length ? `Repairs applied: ${(s.repairs as string[]).join(", ")}.` : "",
        ].filter(Boolean).join(" "),
      })),
    ),
    quality: entries(raw.quality?.batches as Record<string, Record<string, Record<string, number | null>>>).map(([batch, q]) => {
      const pick = (o: Record<string, number | null> | undefined) => ({
        completeness: Number(o?.completeness ?? NaN), validity: Number(o?.validity ?? NaN),
        uniqueness: Number(o?.uniqueness ?? NaN), timeliness: Number(o?.timeliness ?? NaN),
      });
      return { batch, before: pick(q.before_repair), after: pick(q.after_repair) };
    }),
    lineage,
    restatements: entries(raw.reconciliation?.periods as Record<string, { restatements?: Record<string, unknown>[] }>).flatMap(([period, p]) =>
      (p.restatements ?? []).map((r) => ({
        period, batch: String(r.batch ?? ""), rows: Number(r.rows ?? 0),
        beforeUsd: Number(r.before_usd), lateUsd: Number(r.late_usd), afterUsd: Number(r.after_usd),
      })),
    ),
    fx: Object.fromEntries(entries(raw.contract?.fx?.rates).map(([c, r]) => [c, Number(r)])),
    checks: ((raw.checks?.checks ?? []) as Record<string, unknown>[]).map((c) => ({ name: String(c.id), passed: Boolean(c.passed), detail: String(c.detail ?? "") })),
    contractColumns: (raw.contract?.contract?.columns ?? []).map((c) => ({ name: c.name, rule: describe(c) })),
    reasonText: (raw.checks?.reason_codes as Record<string, string>) ?? {},
    reportUrl: `${base}report.md`,
    cleanedUrl: `${base}cleaned.csv`,
    quarantineUrl: `${base}quarantine.csv`,
    packageVersion: String(m.package_version ?? ""),
  };
}
