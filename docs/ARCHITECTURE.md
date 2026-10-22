# Architecture

```
 GitHub Issue (form)          workflow_dispatch / weekly schedule
        |                                  |
   intake.yml  -- validate, authorize -->  run.yml
                                           |
          admit (state: running|blocked, idempotency, ceilings)
                                           |
          compute (read-only token, no secrets): python -m harness compute -> datarefinery run
                                           |  artifact run-<run_id> (14 days)
          publish (validate outputs, immutable runs/<run_id>/, latest.json)
                                           |
          pages.yml (workflow_call): snapshot state -> build -> deploy -> smoke
                                           |
          outcome.yml: hard gates + live browser evidence + optional grader -> release record
                                           |
          memory.yml: deterministic lesson proposals -> type:memory issue
```

## Pipeline (`src/datarefinery`)

1. **Intake**: raw bytes are fingerprinted (sha256) and parsed as text, with no type coercion, within bounds.
2. **Profiling**: per batch and column (types, nulls, distinct values, formats, scale).
3. **Drift detection**: against the contract and the previous accepted batch (renames, formats,
   unit shifts, unknown categories, currency mix, new nulls).
4. **Repair proposals**: evidence, confidence and an `auto_applicable` flag. Ambiguous business
   meaning is never auto-applied; it goes to `ambiguities.json`.
5. **Plan and transform**: the exact ordered steps per batch, including applied repairs.
6. **Validation and quarantine**: pandera/pydantic checks; failing rows keep their raw values and
   carry reason codes.
7. **Deduplication**: exact duplicates are merged; conflicting duplicates are all quarantined.
8. **Currency normalization**: uses only the declared fixed table; originals are kept.
9. **Late arrivals**: accepted and flagged; closed periods are restated, never silently changed.
10. **Reconciliation, lineage, quality**: the accounting identity per batch, a lineage graph with
    per-column derivations, and quality before and after.

## Decisions

| Decision | Why |
|---|---|
| Synthetic fixtures with a ground-truth defect list | Defects are known, so recall can be tested; nothing private is ever published |
| Parse as text at intake | Coercion at intake is where silent data loss starts; every conversion is a recorded step |
| Auto-apply only unambiguous repairs | A pipeline must not invent business meaning; ambiguity is surfaced for a person |
| Quarantine instead of drop | "No silent row drops" is provable only if every set-aside row stays inspectable |
| Contract recorded per run | Each run's `contract.json` shows exactly which rules and rates produced it |
| Shared harness, distinct site | Operations are identical across the five projects; the visual layer is per product |

The harness, state model and workflows are documented in [STATE_MODEL.md](STATE_MODEL.md),
[SECURITY.md](SECURITY.md) and [OPERATIONS.md](OPERATIONS.md).
