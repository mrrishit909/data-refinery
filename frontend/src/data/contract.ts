import type { ContractRule } from "../lib/preview";

export interface ContractColumn {
  name: string;
  type: string;
  required: boolean;
  unique?: boolean;
  pattern?: string;
  format?: string;
  values?: string[];
  min?: string;
  exclusive_min?: string;
  scale?: number;
  unit?: string;
}

export interface ContractFile {
  contract: { contract: string; columns: ContractColumn[]; derived?: { name: string; rule: string }[] };
  fx: { fx_table: string; rates: Record<string, string>; synthetic: boolean };
}

/** Plain-language rule text for one contract column. */
export function describe(c: ContractColumn): string {
  const parts = [c.required ? "required" : "optional"];
  if (c.type === "enum" && c.values) parts.push(`one of ${c.values.join(", ")}`);
  else if (c.type === "date") parts.push("date as YYYY-MM-DD");
  else if (c.type === "decimal") parts.push(`decimal, ${c.scale ?? 2} places${c.min !== undefined ? `, at least ${c.min}` : ""}`);
  else if (c.type === "integer") parts.push(`whole number${c.min !== undefined ? `, at least ${c.min}` : ""}`);
  else if (c.type === "float") parts.push(`number${c.exclusive_min !== undefined ? ` above ${c.exclusive_min}` : ""}${c.unit ? ` in ${c.unit}` : ""}`);
  else parts.push(c.type);
  if (c.pattern) parts.push(`pattern ${c.pattern}`);
  if (c.unique) parts.push("unique");
  return parts.join(", ");
}

function checker(c: ContractColumn): (v: string) => boolean {
  const pattern = c.pattern ? new RegExp(c.pattern) : null;
  switch (c.type) {
    case "enum":
      return (v) => (c.values ?? []).includes(v);
    case "date":
      return (v) => /^\d{4}-\d{2}-\d{2}$/.test(v) && !Number.isNaN(Date.parse(`${v}T00:00:00Z`)) && new Date(`${v}T00:00:00Z`).toISOString().startsWith(v);
    case "decimal":
      return (v) => new RegExp(`^\\d+(\\.\\d{1,${c.scale ?? 2}})?$`).test(v) && Number(v) >= Number(c.min ?? "-Infinity");
    case "integer":
      return (v) => /^-?\d+$/.test(v) && Number(v) >= Number(c.min ?? "-Infinity");
    case "float":
      return (v) => /^-?\d+(\.\d+)?$/.test(v) && (c.exclusive_min === undefined || Number(v) > Number(c.exclusive_min));
    default:
      return (v) => (pattern ? pattern.test(v) : true);
  }
}

export function rulesFromContract(file: ContractFile | null): ContractRule[] {
  if (!file) return [];
  return file.contract.columns.map((c) => ({ name: c.name, rule: describe(c), required: c.required, check: checker(c) }));
}
