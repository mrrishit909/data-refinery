/**
 * Frontend-owned model. `adapter.ts` maps the engine's published JSON/CSV onto these types, so
 * components never depend on file layouts directly.
 */

export type ReasonCode = string;

export interface SourceFile {
  batch: string;
  file: string;
  format: string;
  sha256: string;
  bytes: number;
  rows: number;
  columns: string[];
}

export interface Accounting {
  batch: string;
  rowsIn: number;
  accepted: number;
  quarantined: number;
  merged: number;
  byReason: Record<ReasonCode, number>;
  identityHolds: boolean;
}

export interface Repair {
  id: string;
  batch: string;
  kind: string;
  detail: string;
  evidence: Record<string, unknown>;
  confidence: number;
  autoApplicable: boolean;
  applied: boolean;
  ambiguity: string | null;
}

export interface Ambiguity {
  id: string;
  batch: string;
  column: string;
  value: string;
  reason: string;
  rows: number;
}

export interface DriftFinding {
  batch: string;
  kind: string;
  column: string | null;
  detail: string;
}

export interface ColumnProfile {
  batch: string;
  column: string;
  inferredType: string;
  nulls: number;
  nullRate: number;
  distinct: number;
  min: string | null;
  max: string | null;
  topValues: [string, number][];
  dateFormats: string[];
  notes: string[];
}

export interface PlanStep {
  batch: string;
  order: number;
  name: string;
  detail: string;
  rowsIn: number | null;
  rowsOut: number | null;
}

export interface Row {
  batch: string;
  sourceFile: string;
  sourceRow: number;
  values: Record<string, string>;
}

export interface QuarantinedRow extends Row {
  reasons: ReasonCode[];
}

export interface Sample {
  batch: string;
  sourceRow: number;
  raw: Record<string, string>;
  cleaned: Record<string, string> | null;
  reasons: ReasonCode[];
  note: string;
}

export interface QualityScore {
  completeness: number;
  validity: number;
  uniqueness: number;
  timeliness: number;
}

export interface Quality {
  batch: string;
  before: QualityScore;
  after: QualityScore;
}

export interface LineageNode {
  id: string;
  kind: "source" | "step" | "output";
  label: string;
  batch: string | null;
}

export interface LineageModel {
  nodes: LineageNode[];
  edges: { from: string; to: string; label: string | null }[];
  columns: { output: string; sources: string[]; steps: string[] }[];
}

export interface Restatement {
  period: string;
  batch: string;
  rows: number;
  beforeUsd: number;
  lateUsd: number;
  afterUsd: number;
}

export interface RunRecordLite {
  runId: string;
  status: string;
  attempt: number;
  createdAt: string;
  completedAt: string | null;
  sourceCommit: string;
  workflowRunId: number | null;
  inputManifestHash: string | null;
  requestSource: { type: string; issue: number | null; actor: string | null };
}

export interface RunModel {
  record: RunRecordLite;
  params: { batches: string[]; repairMode: string; contract: string; fxTable: string };
  sources: SourceFile[];
  accounting: Accounting[];
  repairs: Repair[];
  ambiguities: Ambiguity[];
  drift: DriftFinding[];
  profiles: ColumnProfile[];
  profilesAccepted: ColumnProfile[];
  reasonText: Record<string, string>;
  plan: PlanStep[];
  quarantine: QuarantinedRow[];
  cleaned: Row[];
  samples: Sample[];
  quality: Quality[];
  lineage: LineageModel;
  restatements: Restatement[];
  fx: Record<string, number>;
  checks: { name: string; passed: boolean; detail: string }[];
  contractColumns: { name: string; rule: string }[];
  reportUrl: string;
  cleanedUrl: string;
  quarantineUrl: string;
  packageVersion: string;
}

export const STAGES = [
  { id: "intake", label: "Intake" },
  { id: "profile", label: "Profile" },
  { id: "drift", label: "Drift and repairs" },
  { id: "transform", label: "Transform" },
  { id: "quarantine", label: "Quarantine" },
  { id: "reconcile", label: "Reconcile" },
  { id: "lineage", label: "Lineage" },
  { id: "replay", label: "Replay" },
] as const;

export type StageId = (typeof STAGES)[number]["id"];

export const REASON_LABELS: Record<string, string> = {
  MISSING_ID: "Missing identifier",
  BAD_PATTERN: "Identifier pattern",
  BAD_DATE: "Unparseable date",
  NULL_REQUIRED: "Required value missing",
  INVALID_CATEGORY: "Invalid category",
  AMBIGUOUS_CATEGORY: "Ambiguous category",
  NEGATIVE_AMOUNT: "Negative amount",
  DUPLICATE_CONFLICT: "Conflicting duplicate",
  UNKNOWN_COLUMN_SET: "Unknown columns",
};

export function reasonLabel(code: string): string {
  return REASON_LABELS[code] ?? code.toLowerCase().replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}
