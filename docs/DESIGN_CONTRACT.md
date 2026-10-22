# Design contract: Data Refinery

The transferable design rules for this site. Graders and repair workers read this file; it
contains no private preferences or credentials.

## Character

A **precise transformation workbench**. The pipeline is a bench with stations: rows arrive,
are measured, repaired or set aside, and every one is counted out again. The site's one bold
element is the **accounting equation** that opens every run, set large in monospace
(`1,212 in = 1,050 accepted + 148 quarantined + 14 merged`), with **flow ribbons** beneath it
that carry each row group to its destination. It is the visual proof of the project's core
promise: no row disappears. Everything else is a quiet, gridded work surface.

Distinct from the sibling Quant Foundry (serif research note, red-pen strikes, light masthead):
Data Refinery uses a grotesk at expanded width, a monospace for every raw value, a dark
charcoal housing band for the masthead and stage rail, and a faint cutting-mat grid on the
canvas.

## Process record

| Directive stage | Playbook steps | Done here |
|---|---|---|
| 1 Discovery | 1 brief, 3 direction | Character given by the brief; direction delegated and chosen above |
| 2 Content | 2 audience and content | Audience: data and analytics engineers, technical reviewers. Their questions: did any row vanish, what changed, why was a row set aside, what needs a human, can I trust a replay. Primary action: open a quarantined row or a repair and see its evidence |
| 3 Staging | 4 references, 5 design plan, 6 assets | No external visual references adopted (the named motion reference could not be verified). Tokens below. No imagery: data is the material |
| 4 Component adaptation | 7 architecture | Hand-built SVG flow and lineage graph, no chart or UI library; the engineering kit (data loading, states, routing, embedding, e2e harness) is shared with Quant Foundry, the visual layer is not |
| (build) | 8 build and debug | Verified in a browser before any fix is claimed |
| 5 Batch polish | 9, 10 | One coordinated batch after first review; one restrained treatment per flat section |
| 6 Mobile pass | 11 | Deliberate 390 px composition below |
| 7 Browser evidence | 12 | Playwright at 390 / 768 / 1440 in CI and on the live site; screenshots read by a person |
| 8 Deployment | 13 | GitHub Pages from Actions; live URL verified |

Skills invoked for this design: `website-playbook`, `frontend-design:frontend-design`, `ui-ux-pro-max`
(design system: its Flat Design style kept in spirit, its Poppins/Open Sans pairing rejected as a
generic SaaS default; typography search pointed to a mono plus sans pairing; chart search recommended
a Sankey for flow data, adopted as the accounting ribbons with a table fallback).

## Tokens

| Token | Light | Dark | Role |
|---|---|---|---|
| canvas | #FFF6E8 | #1C1612 | work surface |
| grid | #EFE2CC (1 px, 24 px pitch) | #2A211C | cutting-mat lines, decorative only |
| raised | #FFFCF7 | #261E19 | panels, inspector |
| sunken | #F6EAD7 | #15100D | table headers, inputs, code |
| housing | #29201B, text #FFF6E8 | #110D0B, text #FFF6E8 | masthead and stage rail band |
| text | #29201B | #FFF6E8 | primary text |
| text-2 | #5F4F45 | #CFBDA8 | secondary text |
| border-strong | #8A7560 | #7D6A59 | control boundaries (>= 3:1) |
| action | #5A171B, ivory text | #E8D7BF, charcoal text | primary buttons, current stage |
| accepted | #4F5236 olive (text), fill #646548 | #B4B78A | accepted rows, passed checks |
| quarantined | #9A2E20 (text), fill #A6422A | #EC8F7B | quarantined rows, failed checks |
| merged | #6E5D4C (text), fill #8A7560 | #CDB79F | merged duplicates |
| flagged | #7A5410 | #E3B65E | ambiguities that need a human |
| focus | 2 px text-coloured outline + 2 px canvas offset | same | keyboard focus |

Every status appears with a word and an icon or pattern, never colour alone. The flow ribbons use
solid fill for accepted, diagonal hatch for quarantined and dots for merged, each labelled with its
count. Text pairs are >= 4.5:1 and boundaries >= 3:1 on every surface (checked in code review with a
contrast script).

## Type

- **Archivo Variable** (width axis): headings at width 116, weight 650, tight tracking; UI and body
  at width 100, weight 400/500, 15 px / 1.5.
- **Martian Mono Variable**: raw cell values, IDs, hashes, counts in the equation, reason codes.
  It never decorates labels: only real data is monospaced.
- Scale (px): 12, 13, 15, 18, 24, 32, 44 (equation 44 desktop, 26 mobile). Sentence case labels; no
  all-caps, no eyebrow labels, no middle-dot meta strings, no arrows on link text.

## Layout

- Desktop 1440: charcoal housing band (wordmark, stage rail with the eight numbered stations
  Intake, Profile, Drift and repairs, Transform, Quarantine, Reconcile, Lineage, Replay, and nav to
  Request and About); below it the run header with the equation and flow; then a three-pane bench:
  batch rack (left, 220 px), work surface (centre), inspector (right, 340 px, sticky) that shows the
  selected row, repair or column with its lineage.
- Tablet 768: the batch rack becomes a wrapping row of batch chips under the run header; the
  inspector becomes a sheet that slides up from the bottom when something is selected.
- Mobile 390: the stage rail becomes a compact stepper ("Stage 3 of 8, Drift and repairs" with
  previous and next buttons and a menu of all stages); wide tables become row cards with the
  cleaned value above the raw value; the inspector is a full-height sheet with a close button.
- Left aligned. Radius 3 px on controls, 6 px on panels. Panels have a 1 px border, no shadow; the
  inspector sheet has one soft shadow.

## Motion (durations in ms)

| Moment | Treatment | Duration |
|---|---|---|
| Run load (once) | equation terms appear left to right, then the ribbons extend from the input bar to their destinations (clip reveal) | 60 stagger per term; ribbons 520 |
| Stage change | work surface content slides 24 px in the pipeline direction (forward from the right, back from the left) and fades | 240 in, 160 out |
| Before / after toggle | changed cells cross-fade and get a one-time outline pulse | 220 |
| Inspector open / close | slides from the right (desktop) or bottom (sheet) | 220 open, 160 close |
| Filter chip toggle | chip fill transitions; counts update without animation | 120 |
| Controls | press scale 0.98 | 120 |

Easing: decelerate on arrival, accelerate on exit. Reduced motion: all durations 0, ribbons and
equation render complete. Nothing essential waits for animation; no scroll hijacking, no intro, no
perpetual motion, no hover-only controls.

## States

loading (skeleton shaped like the bench), empty (no runs yet + how to request one), error (which file
failed, Retry), stale (snapshot older than 14 days: banner with age + Refresh), fixture notice, success.
The upload preview states: idle, parsing, preview ready, file rejected (with the reason: size, rows,
format), and a permanent note that nothing leaves the browser and nothing is saved.
