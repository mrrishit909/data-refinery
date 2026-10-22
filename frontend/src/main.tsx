// Fonts load only in the standalone site; an embedding host keeps its own fonts and the
// stylesheet falls back to system sans and monospace stacks.
import "@fontsource-variable/archivo/wdth.css";
import "@fontsource-variable/martian-mono/wdth.css";
import { mount } from "./index";

const el = document.getElementById("app");
if (el) {
  el.removeAttribute("data-boot");
  mount(el, { basePath: import.meta.env.BASE_URL });
}
