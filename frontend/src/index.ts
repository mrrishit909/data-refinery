import { createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { DataRefineryApp } from "./App";
import type { AppConfig } from "./config";

export { DataRefineryApp } from "./App";
export type { AppConfig, Routing, Theme } from "./config";

/**
 * Mount the app into any element. Returns an unmount function that removes every listener the
 * app registered (hashchange, media queries and observers are all tied to React effects).
 */
export function mount(element: HTMLElement, config: Partial<AppConfig> = {}): () => void {
  const root: Root = createRoot(element);
  root.render(createElement(DataRefineryApp, config));
  return () => root.unmount();
}
