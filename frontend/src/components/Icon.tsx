const PATHS = {
  check: "M4 10.5 8.2 14.5 16 6",
  warn: "M10 3.5 17.5 16.5H2.5Z M10 8.5v3.5 M10 14.2v.3",
  cross: "M5 5l10 10M15 5 5 15",
  info: "M10 2.8a7.2 7.2 0 1 1 0 14.4 7.2 7.2 0 0 1 0-14.4Z M10 9v5 M10 6.3v.3",
  refresh: "M16 10a6 6 0 1 1-1.8-4.3 M16 3.5V7h-3.5",
  chevron: "M7.5 4.5 13 10l-5.5 5.5",
  menu: "M3.5 6h13M3.5 10h13M3.5 14h13",
  external: "M8 4.5H4.5v11h11V12 M11 3.5h5.5V9 M16.5 3.5 9 11",
  sort: "M7 8l3-3 3 3M7 12l3 3 3-3",
  seal: "M10 2.5 12 5l3-.4.4 3 2.6 1.4-1.4 2.6 1.4 2.6L15.4 15.6 15 18.5l-3-.4-2 2.4-2-2.4-3 .4-.4-3L2 14.2l1.4-2.6L2 9l2.6-1.4.4-3 3 .4Z",
  github:
    "M10 2a8 8 0 0 0-2.5 15.6c.4 0 .5-.2.5-.4v-1.5c-2.2.5-2.7-1-2.7-1-.4-.9-.9-1.2-.9-1.2-.7-.5.1-.5.1-.5.8.1 1.2.8 1.2.8.7 1.2 1.9.9 2.3.7.1-.5.3-.9.5-1.1-1.8-.2-3.6-.9-3.6-4 0-.9.3-1.6.8-2.1-.1-.2-.4-1 .1-2.1 0 0 .7-.2 2.2.8a7.6 7.6 0 0 1 4 0c1.5-1 2.2-.8 2.2-.8.4 1.1.2 1.9.1 2.1.5.6.8 1.3.8 2.1 0 3.1-1.9 3.8-3.6 4 .3.3.5.8.5 1.5v2.2c0 .2.1.5.6.4A8 8 0 0 0 10 2Z",
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, label }: { name: IconName; label?: string }) {
  const filled = name === "github";
  return (
    <svg
      viewBox="0 0 20 20"
      width="16"
      height="16"
      aria-hidden={label ? undefined : true}
      role={label ? "img" : undefined}
      aria-label={label}
      fill={filled ? "currentColor" : "none"}
      stroke={filled ? "none" : "currentColor"}
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d={PATHS[name]} />
    </svg>
  );
}

export function StatusIcon({ status }: { status: "pass" | "warn" | "fail" }) {
  const map = { pass: ["check", "dr-tone-success", "Pass"], warn: ["warn", "dr-tone-warning", "Warning"], fail: ["cross", "dr-tone-danger", "Fail"] } as const;
  const [icon, tone, label] = map[status];
  return (
    <span className={tone}>
      <Icon name={icon} label={label} />
    </span>
  );
}
