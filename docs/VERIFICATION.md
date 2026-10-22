# Verification

**The single current release record is `releases/current.json` on the `state` branch.** It is
written only by the Outcome Verification Loop and only when every hard gate passed. Every attempt,
including blocked ones, is in `releases/history.json`, with findings and small evidence files in
`outcomes/<release_id>/`. This document explains the checks; it does not restate their status.

## Hard gates (objective; rubric locked by `rubric/rubric.lock`)

| Gate | What it proves | How |
|---|---|---|
| pipeline-gates | every input row accounted for; repeated runs idempotent; original data preserved; no silent row drops; no invented values; ambiguous business meaning flagged and never auto-applied; source-to-output lineage inspectable; repairs pass both the original and the drifted fixtures | `pytest -m gate tests/datarefinery` (8 tests) |
| harness | state machine, idempotency, duplicate detection, compare-and-swap concurrency, immutability, intake authorization by identity, injection-safe parsing, ceilings, repair-round bounds, memory guard | `pytest tests/harness` |
| secret-scan | no credential patterns in tracked files or in the live site's HTML, JS and CSS | `scripts/secret_scan.py --live` |
| live-site | the Pages URL serves the app and its snapshot points at the state branch's latest run | HTTP checks |
| published-matches-state | every published result file of the latest run is byte-identical to the state branch | `python -m harness verify-published` |
| browser | 12 checks x 3 widths (390, 768, 1440), all 36 must pass, against the live site: accounting equation and flow, every pipeline station by deep link, batch rack, quarantine reason filters and pagination, inspector open and close (sheet on narrow screens), before / after toggle, lineage column tracing, CSV preview with a generated file, navigation and back button, skip link and focus, reduced motion, dark theme (axe plus a direct check that the primary action's text and fill differ), loading / empty / error-retry / stale states; axe (WCAG 2.2 AA serious and critical), horizontal overflow, console errors and failed same-origin requests | Playwright, `frontend/tests/e2e/site.spec.ts` |

## Independent adversarial review

A separate data-quality adversary (an agent that did not build the engine) attacked the pipeline's
claims with crafted inputs and recorded each confirmed defect as a strict-xfail test in
`tests/datarefinery/test_dr_adversarial.py`. It confirmed 20 defects (DR-01 to DR-20), two of them
blockers: any non-automatic repair other than a category map crashed the run, and an XLSX file with a
stale dimension record silently dropped rows (the gates' own reader shared that blind spot). 19 are
fixed and their tests now pass as plain regression tests; DR-20a (a minority of rows in a different
unit inside one batch) remains a strict xfail and a documented limitation. Eight further tests guard
claims that held (byte-identical replay across hash seeds, no numeric coercion, half-up FX rounding,
first-period restatement, cross-batch duplicates, CSV edge cases).

## Subjective review (separate from gates)

The rubric's subjective criteria (hierarchy, typography, spacing, palette, motion, responsive
adaptation, distinctiveness, fidelity to `docs/DESIGN_CONTRACT.md`) are scored by an independent
grader only when the repository has an inference secret; otherwise the release record says the
grader did not run. Scores never change a gate result. Screenshots from every outcome run are kept
as Actions artifacts for 14 days and a small representative set is committed with the release
evidence.

## Repairs

A failing gate opens or updates one `type:repair` issue per gate. The same failing set on a new
commit counts as another repair round; after three rounds the release is marked blocked for human
review and its evidence is kept.

## Reproduce locally (optional)

```sh
uv sync --frozen && uv run pytest
cd frontend && npm ci && npm run build && npx playwright install chromium && npm run e2e
```
