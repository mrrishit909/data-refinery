# Data Refinery

A self-repairing analytical pipeline workbench. It ingests batches of **synthetic** e-commerce
orders (CSV, JSON and XLSX), fingerprints and profiles them, checks them against a data
contract, detects schema drift, proposes repairs with evidence and confidence, applies only the
unambiguous ones, quarantines what still fails with reasons, and proves that every input row is
accounted for: `rows in = accepted + quarantined + merged duplicates`.

- **Live site:** https://mrrishit909.github.io/data-refinery/
- **Current release record:** [`releases/current.json` on the state branch](https://github.com/mrrishit909/data-refinery/blob/state/releases/current.json)
- **Run records and results:** the [`state` branch](https://github.com/mrrishit909/data-refinery/tree/state)

## What it does

| Part | Where |
|---|---|
| Fixture generator with documented, injected defects and ground truth | `src/datarefinery/` (`python -m datarefinery fixtures`), `fixtures/` |
| Contract and fixed synthetic FX table | `contracts/` |
| Intake, profiling, drift detection, repair proposals, transformation, quarantine, deduplication, reconciliation, lineage, quality | `src/datarefinery/` |
| GitHub-native harness: intake, admission, publication, reconciler, outcome loop, memory | `harness/` |
| Static, embeddable workbench with an in-browser CSV preview | `frontend/` |

## Use it

- Browse runs anonymously: the accounting equation, the eight pipeline stations, the quarantine,
  repair evidence, lineage and downloads.
- Preview your own CSV against the contract in the browser; nothing is uploaded or saved.
- Request a run: open the *Pipeline run request* issue form. The owner's requests run
  automatically; anyone else's wait for the owner's `job:approved` label. See
  [docs/OPERATIONS.md](docs/OPERATIONS.md).

## Documents

[Architecture](docs/ARCHITECTURE.md), [operations](docs/OPERATIONS.md),
[state model](docs/STATE_MODEL.md), [security](docs/SECURITY.md),
[cost limits](docs/COST_LIMITS.md), [verification](docs/VERIFICATION.md),
[portfolio integration](docs/PORTFOLIO_INTEGRATION.md), [design contract](docs/DESIGN_CONTRACT.md),
[portfolio manifest](portfolio-manifest.json).

## Assets and licences

| Font | Package | Licence |
|---|---|---|
| Archivo | `@fontsource-variable/archivo` 5.3.0 | SIL Open Font License 1.1, (c) The Archivo Project Authors |
| Martian Mono | `@fontsource-variable/martian-mono` 5.3.0 | SIL Open Font License 1.1, (c) The Martian Mono Project Authors |

The fonts are installed from npm (Fontsource packages, which carry the OFL text) and bundled into the
site's build; nothing loads from a third-party font service. Icons and illustrations are original inline
SVG and CSS; there are no photographs or third-party images. Every dataset is synthetic and committed; no real customer or company data. The code is published
in this public repository.

## Limitations

Synthetic fixtures only (durable runs never take uploads); one contract (`orders.v1`) and one
fixed, synthetic FX table; repairs cover renames, date formats, unit scale and unambiguous
category variants, and never guess business meaning (category rules are lexical only, and a one-letter substitution is never auto-applied); unit detection works per batch, so a minority of rows in another unit inside an otherwise consistent batch is not flagged; duplicates are detected within a run, not across runs; a JSON record that repeats a key rejects its file; intake is bounded to four batches, 5,000
rows and 2 MB per file and one XLSX sheet, and a request whose outputs would exceed 1.9 MB is rejected before anything is written. The optional AI grader runs only if the owner adds an
inference secret; release records state whether it ran.
