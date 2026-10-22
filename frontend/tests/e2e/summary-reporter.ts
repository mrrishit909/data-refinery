import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { FullResult, Reporter, TestCase, TestResult } from "@playwright/test/reporter";

/** Writes evidence/e2e/summary.json, the file the Outcome Verification Loop's browser gate reads. */
export default class SummaryReporter implements Reporter {
  private tests: { title: string; project: string; status: string; duration: number }[] = [];

  onTestEnd(test: TestCase, result: TestResult) {
    const project = test.parent.project()?.name ?? "unknown";
    const existing = this.tests.findIndex((t) => t.title === test.title && t.project === project);
    const entry = { title: test.title, project, status: result.status, duration: result.duration };
    if (existing >= 0) this.tests[existing] = entry; // keep the final retry's outcome
    else this.tests.push(entry);
  }

  onEnd(result: FullResult) {
    const dir = process.env.E2E_EVIDENCE_DIR ?? "../evidence/e2e";
    mkdirSync(dir, { recursive: true });
    const count = (s: string) => this.tests.filter((t) => t.status === s).length;
    const summary = {
      target: process.env.E2E_BASE_URL ?? "local production preview",
      status: result.status,
      passed: count("passed"),
      failed: count("failed") + count("timedOut") + count("interrupted"),
      skipped: count("skipped"),
      tests: this.tests,
    };
    writeFileSync(join(dir, "summary.json"), JSON.stringify(summary, null, 2));
  }
}
