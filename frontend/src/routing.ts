import { useCallback, useEffect, useState } from "react";
import type { Routing } from "./config";
import { STAGES, type StageId } from "./data/model";

export type Route =
  | { view: "run"; runId?: string; stage: StageId }
  | { view: "history" }
  | { view: "preview" }
  | { view: "request" }
  | { view: "about" };

const STAGE_IDS = new Set<string>(STAGES.map((s) => s.id));

export function parseRoute(hash: string): Route {
  const [head, a, b, c] = hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  if (head === "runs" && a && b === "stage" && c && STAGE_IDS.has(c)) return { view: "run", runId: a, stage: c as StageId };
  if (head === "runs" && a) return { view: "run", runId: a, stage: "reconcile" };
  if (head === "runs") return { view: "history" };
  if (head === "stage" && a && STAGE_IDS.has(a)) return { view: "run", stage: a as StageId };
  if (head === "preview") return { view: "preview" };
  if (head === "request") return { view: "request" };
  if (head === "about") return { view: "about" };
  return { view: "run", stage: "reconcile" };
}

export function routeHref(route: Route): string {
  const enc = encodeURIComponent;
  switch (route.view) {
    case "run":
      if (route.runId) return `#/runs/${enc(route.runId)}/stage/${route.stage}`;
      return route.stage === "reconcile" ? "#/" : `#/stage/${route.stage}`;
    case "history":
      return "#/runs";
    case "preview":
      return "#/preview";
    case "request":
      return "#/request";
    case "about":
      return "#/about";
  }
}

export function useRoute(mode: Routing): [Route, (r: Route) => void] {
  const [route, setRoute] = useState<Route>(() => (mode === "hash" ? parseRoute(window.location.hash) : { view: "run", stage: "reconcile" }));
  useEffect(() => {
    if (mode !== "hash") return;
    const onHash = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, [mode]);
  const navigate = useCallback(
    (r: Route) => {
      if (mode === "hash") window.location.hash = routeHref(r).slice(1);
      else setRoute(r);
    },
    [mode],
  );
  return [route, navigate];
}
