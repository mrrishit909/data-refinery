# Security

## Trust boundaries

| Input | Trust | Handling |
|---|---|---|
| Issue title and body | untrusted | read from the event JSON file by Python; coerced field by field (`harness/issueform.py`) and validated against `schemas/request.schema.json`; never interpolated into shell, never echoed back verbatim |
| Workflow dispatch inputs | untrusted | passed through `env:` only; re-validated by the admit step |
| Labels | untrusted (issue-form labels are applied for any author) | authorization never depends on labels; it checks `issue.user.login` / `sender.login` against the repository owner |
| Repository code on `main` | trusted after review | the only code executed by workflows |
| Grader output | untrusted | schema-validated; can add subjective scores only; cannot change hard-gate results |
| Memory proposals | untrusted until accepted | schema + guard (no security, cost, threshold, publication or requirement subjects); accepted only by the owner's `memory:accept` label |

## Workflow rules

- Top-level `permissions: {}`; each job requests only what it needs:
  intake `issues: write, actions: write`; compute `contents: read`; publish/admit
  `contents: write, issues: write`; Pages deploy `pages: write, id-token: write`.
- Compute (which runs project code over requests) has no write token and no secrets.
- No `pull_request_target`. Pull requests run CI with read-only permissions and no secrets.
- Third-party actions are pinned to full commit SHAs (tag noted in a comment).
- Dependencies install from lockfiles (`uv.lock` with `uv sync --frozen`, `package-lock.json`
  with `npm ci`). The one exception is the optional Claude Code CLI, installed globally by exact version
  (`@anthropic-ai/claude-code@2.1.292`) in a step without secrets, only when an inference secret exists.
- Every job has `timeout-minutes`; state writers serialize through `state-writer`.

## Credentials

- No credential is stored in this repository or shipped to the browser. CI and the Outcome
  Verification Loop run `scripts/secret_scan.py` over tracked files and the live site's HTML,
  JS and CSS.
- The only secret-bearing job is the optional grader in `outcome.yml`. It runs only when the
  repository has `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`, uses `claude -p --bare` with
  only the `Read` and `Glob` tools (`--tools`), Read allowed only under `evidence/`, `rubric/` and the
  design contract, a `--max-budget-usd` cap, over screenshots and the rubric; the pinned CLI is installed
  in a separate step that has no secret in its environment;
  it never executes repository or generated code. Without the secret it is skipped and the
  release record says so.
- Visitors are never asked for tokens. The site has no write path; durable requests go through
  GitHub Issues, which require a GitHub sign-in, and only the owner's requests run unattended.

## Publication

- Only allow-listed result files of completed runs are copied into the Pages snapshot
  (`project_hooks.PUBLIC_FILES`). All data is synthetic and labelled as such.
- Reports contain no logs, environment dumps or personal data. Run records name the GitHub
  account that requested the run (a public login).

## Reporting

Open an issue without sensitive details, or use GitHub's private vulnerability reporting.
