const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function pct(x: number, digits = 1): string {
  return Number.isFinite(x) ? `${(x * 100).toFixed(digits)}%` : "n/a";
}

export function signedPct(x: number, digits = 1): string {
  if (!Number.isFinite(x)) return "n/a";
  const v = (x * 100).toFixed(digits);
  return x > 0 ? `+${v}%` : `${v}%`;
}

export function fixed(x: number, digits = 2): string {
  return Number.isFinite(x) ? x.toFixed(digits) : "n/a";
}

export function money(x: number): string {
  return Number.isFinite(x) ? Math.round(x).toLocaleString("en-US") : "n/a";
}

/** "2019-08-23" -> "23 Aug 2019" (dates are calendar dates, never shifted by time zone). */
export function day(iso: string): string {
  const [y, m, d] = iso.slice(0, 10).split("-");
  if (!y || !m || !d) return iso;
  return `${Number(d)} ${MONTHS[Number(m) - 1]} ${y}`;
}

export function monthYear(iso: string): string {
  const [y, m] = iso.split("-");
  return `${MONTHS[Number(m) - 1]} ${y}`;
}

export function timestamp(iso: string): string {
  const t = new Date(iso);
  if (Number.isNaN(t.getTime())) return iso;
  return `${day(iso.slice(0, 10))}, ${iso.slice(11, 16)} UTC`;
}

export function ago(iso: string, now = Date.now()): string {
  const ms = now - Date.parse(iso);
  if (!Number.isFinite(ms)) return "unknown time";
  const min = Math.round(ms / 60000);
  if (min < 2) return "just now";
  if (min < 90) return `${min} minutes ago`;
  const h = Math.round(min / 60);
  if (h < 36) return `${h} hours ago`;
  return `${Math.round(h / 24)} days ago`;
}

export function paramsText(params: Record<string, number>): string {
  return Object.entries(params)
    .map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`)
    .join(", ");
}

export function shortHash(h: string | null | undefined, n = 10): string {
  if (!h) return "n/a";
  return h.replace(/^sha256:/, "").slice(0, n);
}

const NUMBER_WORDS = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten"];
export function countWord(n: number): string {
  return NUMBER_WORDS[n] ?? String(n);
}
