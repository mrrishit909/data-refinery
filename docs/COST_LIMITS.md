# Cost and resource limits

## Basis (checked against GitHub documentation on 2026-10-21)

- GitHub Actions is free for **public** repositories that use **standard GitHub-hosted runners**.
  Larger runners are always billed, so every job here uses `ubuntu-24.04` (standard).
- GitHub Pages on GitHub Free requires a public repository. Published sites may be up to 1 GB,
  with a soft 100 GB/month bandwidth limit; deployments time out after 10 minutes.
- Artifact retention on public repositories can be 1 to 90 days; this project uses 3 to 14.
- `GITHUB_TOKEN` is limited to 1,000 REST requests per hour per repository; content-creating
  requests are limited to 80 per minute and 500 per hour.
- Scheduled workflows run only on the default branch, can be delayed under load, and are
  disabled in public repositories after 60 days without activity.

**Unverified:** the account's billing plan could not be read with the available token, so the
zero-cost claim rests on the public-repository and standard-runner rules above, not on a plan
check. No paid billing was enabled and no plan was changed.

## Ceilings (enforced in code from `project.json` -> `limits`)

| Limit | Value | Enforced by |
|---|---|---|
| Runs per calendar month | 40 | admit step blocks with `job:blocked` |
| Concurrent running runs | 5 | admit step |
| Output per run | 2 MB | publish step marks the run failed |
| Retained result directories | 30 newest + pinned | publish step prunes |
| Index entries | 500 (overflow archived) | state writer |
| Runs in the Pages snapshot | 8 | snapshot builder |
| Compute time per run | 20 min | job `timeout-minutes` |
| Input size | at most 4 batches, 5,000 rows and 2 MB per file, 1 XLSX sheet | engine |
| Retry attempts per request | 3 | admit step |
| Reconciler actions per invocation | 5 | reconciler |
| Automatic repair rounds per defect batch | 3 | Outcome Verification Loop |
| Grader inference per run | USD 1.00 (`--max-budget-usd`) | grader job; skipped without a secret |

## Scheduled work

- Reconciler: daily (cheap: reads state, at most 5 corrective actions).
- Pipeline refresh: weekly, the full four-batch apply-safe request. The fixtures are fixed, so after
  the first run each refresh is a duplicate and does nothing unless the engine code, fixtures,
  contracts or lockfile changed (they are part of the request id).

## Not used

No external databases, hosting, object storage, paid APIs, paid marketplace actions, Git LFS,
or Actions caches as storage. LLM calls are never used for transformation or repair decisions.
