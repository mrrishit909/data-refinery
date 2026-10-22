# State model

GitHub is the only durable infrastructure. Nothing authoritative lives on a laptop, in a
database, in an Actions cache or only in an Actions artifact.

| Store | Holds | Authority |
|---|---|---|
| `state` branch (JSON/CSV files) | run records, results, request idempotency records, release records, evidence summaries | **source of truth** |
| GitHub Issues | run requests, run summaries, repair assignments, memory proposals | human-readable ledger (a view of state) |
| `main` branch | code, schemas, rubric, accepted memory (`memory/lessons.json`) | reviewed configuration |
| Actions artifacts (14 days) | raw compute output and browser evidence before publication | temporary only |
| Pages site | sanitized snapshot of the state branch | read-only copy, timestamped |

## Layout of the `state` branch

```
index.json                     newest-first run list (bounded to limits.max_index_entries; overflow -> index-archive.json)
latest.json                    pointer to the newest completed, validated run (moved only after validation)
requests/<request_id>.json     idempotency record: params hash + every attempt and its status
runs/<run_id>/run.json         run record (schemas/run.schema.json), immutable once terminal
runs/<run_id>/request.json     the validated parameters
runs/<run_id>/<result files>   immutable results (project output contract); failure.json for failed runs
outcomes/<release_id>/         Outcome Verification Loop findings + small evidence files
outcomes/repairs.json          defect batches and repair rounds
releases/current.json          THE current release record (one designated record; only `released` ones)
releases/history.json          last 100 release attempts (released and blocked)
```

## Identifiers

- `request_id` = first 16 hex of sha256(canonical JSON of {project, params}). Identical parameters
  are the same request (idempotency key), whoever asks.
- `run_id` = `<project>-<created UTC YYYYmmddTHHMMSSZ>-<request_id[:8]>-a<attempt>`.
- `input_manifest_hash` = sha256 of canonical {params, source file sha256 list, contract sha256,
  package version}, written by the compute step into `manifest.json` and copied into the run record.

## State machine

```
queued -> running -> completed
queued -> running -> failed
queued -> blocked
running -> cancelled
```

`queued` exists between dispatch and admission (shown by the issue label `job:requested`);
admission writes the record directly as `running` or `blocked`. Terminal records never change.
A retry is a new attempt (`-a2`, `-a3`, max 3) whose `parent_run_id` points at the failed one;
the failed attempt's record and `failure.json` stay in place.

## Concurrency

- Every state write is a pure mutation applied to a fresh copy of the branch tip and pushed
  without force. A rejected push (someone else moved the branch) resets to the new tip,
  re-applies the mutation and retries with exponential backoff (6 attempts). This is
  compare-and-swap on the branch ref; related files always change in one commit.
- State-writing jobs share the concurrency group `state-writer` with `queue: max`, so bursts
  wait instead of cancelling each other. The compare-and-swap loop is still the real guarantee.
- Result paths are immutable: rewriting an existing file with different bytes raises
  `ImmutableConflict` and nothing is written.

## Partial-write recovery

| Interrupted after | Visible symptom | Recovery |
|---|---|---|
| issue opened, dispatch lost | issue stays `job:requested`, no run record | reconciler marks it `job:blocked` after 2 h with an explanation; owner edits or re-opens |
| admission committed `running`, compute or publish died | run stuck `running` past 90 min | reconciler checks the workflow run; re-publishes from the retained `run-<run_id>` artifact if present, else marks `failed`/`cancelled` |
| same workflow re-run before publishing | second admission sees its own `workflow_run_id` | earlier attempt marked `failed` ("superseded"), new attempt links `parent_run_id` |
| results committed, issue not updated | issue label disagrees with state | reconciler re-syncs labels from state (Issues are views, state wins) |
| publish job re-run after success | none | no-op: terminal records are immutable |
| outputs invalid or over the size ceiling | | run marked `failed`; outputs are not copied; `failure.json` lists the problems |
| Pages deploy failed after publish | site shows the older snapshot with its timestamp | next deploy (push or run) publishes the newer snapshot |

## Size and lifecycle

- Per-run output ceiling `limits.max_run_bytes` (2 MB). Result directories are retained for the
  newest `limits.max_retained_run_dirs` (30) runs plus the runs referenced by `latest.json` and
  `releases/current.json`; older directories keep only `run.json` (results remain in git history).
- The Pages snapshot includes at most `limits.max_snapshot_runs` (8) runs.
- If the branch ever approaches 500 MB, the owner squashes it (documented in OPERATIONS.md); no
  automated job rewrites history.
