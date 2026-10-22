# Portfolio integration

Three ways to show Data Refinery inside another site. All read the same public snapshot; none
needs a token.

## 1. Link (simplest)

Use `portfolio-manifest.json` for the card: name, description, live URL, preview image, tags,
capabilities and limitations. Deep links work: `#/runs/<run_id>/stage/quarantine`, `#/stage/lineage`,
`#/runs`, `#/preview`, `#/request`, `#/about`.

## 2. Iframe

```html
<iframe src="https://mrrishit909.github.io/data-refinery/#/" title="Data Refinery workbench"
        style="width:100%;height:80vh;border:0" loading="lazy"></iframe>
```

The app is fully self-contained; its theme follows the visitor's system setting.

## 3. Embedded component

The Pages site also serves an ES module build with React kept external:

- `https://mrrishit909.github.io/data-refinery/lib/data-refinery.js`
- `https://mrrishit909.github.io/data-refinery/lib/data-refinery.css`

```tsx
import "https://mrrishit909.github.io/data-refinery/lib/data-refinery.css";
import { DataRefineryApp } from "data-refinery"; // or the URL above, with react/react-dom provided by the host

<DataRefineryApp
  dataUrl="https://mrrishit909.github.io/data-refinery/data/"
  showShell={false}       // host supplies header and footer
  routing="memory"        // never reads or writes the host's URL
  theme="auto"            // "light" | "dark" | "auto"
/>;
```

Or without React in the host page's own code: `mount(element, config)` returns an unmount
function.

| Config | Default | Meaning |
|---|---|---|
| `basePath` | build base | where the standalone site lives |
| `dataUrl` | `<basePath>data/` | snapshot directory (must allow CORS; GitHub Pages does) |
| `theme` | `auto` | colour theme |
| `showShell` | `true` | render the app's own masthead and footer |
| `routing` | `hash` | `memory` for embedding |

Isolation: every CSS rule is scoped under `.dr-root` and tokens are defined on that element, not
`:root`. Listeners (hashchange in hash mode, media queries, resize and intersection observers) are
registered in React effects and removed on unmount. Fonts are loaded only by the standalone site;
an embedded copy uses the host's fonts or the stylesheet's system sans and monospace fallbacks.
