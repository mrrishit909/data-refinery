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

## Limitations

Synthetic fixtures only (durable runs never take uploads); one contract (`orders.v1`) and one
fixed, synthetic FX table; repairs cover renames, date formats, unit scale and unambiguous
category variants, and never guess business meaning (category rules are lexical only); duplicates are detected within a run, not across runs; intake is bounded to four batches, 5,000
rows and 2 MB per file and one XLSX sheet. The optional AI grader runs only if the owner adds an
inference secret; release records state whether it ran.
