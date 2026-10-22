import { parseCsv } from "../data/csv";

export interface ContractRule {
  name: string;
  rule: string;
  required: boolean;
  check: (value: string) => boolean;
}

export interface ColumnPreview {
  name: string;
  filled: number;
  empty: number;
  distinct: number;
  inferred: string;
  examples: string[];
  contract: { rule: string; passRate: number } | null;
}

export interface PreviewResult {
  rows: number;
  columns: ColumnPreview[];
  missing: { name: string; candidates: string[] }[];
  extra: string[];
  head: Record<string, string>[];
}

export const LIMITS = { bytes: 2_000_000, rows: 5_000 };

const ISO = /^\d{4}-\d{2}-\d{2}$/;
const DMY = /^\d{2}\/\d{2}\/\d{4}$/;
const INT = /^-?\d+$/;
const DEC = /^-?\d+\.\d+$/;

export function infer(values: string[]): string {
  const v = values.filter((x) => x !== "");
  if (!v.length) return "empty";
  const share = (re: RegExp) => v.filter((x) => re.test(x)).length / v.length;
  if (share(ISO) > 0.95) return "date (YYYY-MM-DD)";
  if (share(ISO) + share(DMY) > 0.95) return "date (mixed formats)";
  if (share(INT) > 0.95) return "integer";
  if (share(INT) + share(DEC) > 0.95) return "decimal";
  return "text";
}

/** Normalized Levenshtein similarity in [0, 1]. */
export function similarity(a: string, b: string): number {
  const x = a.toLowerCase().replace(/[^a-z0-9]/g, "");
  const y = b.toLowerCase().replace(/[^a-z0-9]/g, "");
  if (!x.length || !y.length) return 0;
  const d: number[] = Array.from({ length: y.length + 1 }, (_, j) => j);
  for (let i = 1; i <= x.length; i++) {
    let prev = d[0]!;
    d[0] = i;
    for (let j = 1; j <= y.length; j++) {
      const tmp = d[j]!;
      d[j] = Math.min(d[j]! + 1, d[j - 1]! + 1, prev + (x[i - 1] === y[j - 1] ? 0 : 1));
      prev = tmp;
    }
  }
  return 1 - d[y.length]! / Math.max(x.length, y.length);
}

export function preview(text: string, rules: ContractRule[]): PreviewResult {
  const rows = parseCsv(text);
  const names = rows.length ? Object.keys(rows[0]!) : [];
  const byName = new Map(rules.map((r) => [r.name, r]));
  const columns: ColumnPreview[] = names.map((name) => {
    const values = rows.map((r) => (r[name] ?? "").trim());
    const filled = values.filter((v) => v !== "");
    const rule = byName.get(name);
    return {
      name,
      filled: filled.length,
      empty: values.length - filled.length,
      distinct: new Set(filled).size,
      inferred: infer(values),
      examples: [...new Set(filled)].slice(0, 3),
      contract: rule ? { rule: rule.rule, passRate: filled.length ? filled.filter(rule.check).length / filled.length : 0 } : null,
    };
  });
  const extra = names.filter((n) => !byName.has(n));
  const missing = rules
    .filter((r) => !names.includes(r.name))
    .map((r) => ({ name: r.name, candidates: extra.filter((e) => similarity(e, r.name) >= 0.5 || r.name.includes(e) || e.includes(r.name)) }));
  return { rows: rows.length, columns, missing, extra, head: rows.slice(0, 8) };
}
