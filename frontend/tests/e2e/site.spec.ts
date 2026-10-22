import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { join } from "node:path";

const EVIDENCE = process.env.E2E_EVIDENCE_DIR ?? "../evidence/e2e";
const SCREENS = join(EVIDENCE, "screens");
mkdirSync(SCREENS, { recursive: true });

// Let finite animations and transitions finish so evidence and audits see settled states, not mid-frame ones.
async function settle(page: Page) {
  await page.waitForFunction(() => document.getAnimations().every((a) => a.playState !== "running" || a.effect?.getTiming().iterations === Infinity), null, { timeout: 3000 }).catch(() => {});
}

async function shot(page: Page, name: string, fullPage = true) {
  await settle(page);
  await page.screenshot({ path: join(SCREENS, `${test.info().project.name}-${name}.jpg`), type: "jpeg", quality: 70, fullPage });
}

function watch(page: Page) {
  const problems: string[] = [];
  page.on("console", (m) => m.type() === "error" && problems.push(`console: ${m.text()}`));
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("response", (r) => {
    const url = new URL(r.url());
    if (r.status() >= 400 && url.origin === new URL(page.url() || r.url()).origin) problems.push(`HTTP ${r.status()} ${url.pathname}`);
  });
  return problems;
}

async function noOverflow(page: Page) {
  const [scroll, inner] = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
  expect(scroll, "page must not scroll horizontally").toBeLessThanOrEqual(inner + 1);
}

async function axe(page: Page) {
  await settle(page);
  // @axe-core/playwright is typed against its own Playwright Page version; the runtime object is the same.
  const results = await new AxeBuilder({ page: page as never }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(serious.map((v) => `${v.id}: ${v.nodes.length} node(s)`)).toEqual([]);
}

const narrow = (page: Page) => (page.viewportSize()?.width ?? 0) <= 720;

async function goStage(page: Page, label: string, id: string) {
  if (narrow(page)) await page.locator("#dr-stage-select").selectOption(id);
  else await page.getByRole("navigation", { name: "Pipeline stages" }).getByRole("link", { name: new RegExp(label) }).click();
  await expect(page).toHaveURL(new RegExp(`stage/${id}$`));
}

test("overview: accounting equation, flow, batch rack; accessible, no overflow", async ({ page }) => {
  const problems = watch(page);
  await page.goto("./");
  const eq = page.locator(".dr-equation");
  await expect(eq).toHaveAttribute("aria-label", /Every row is accounted for/);
  await expect(page.getByRole("navigation", { name: "Batches" })).toBeVisible();
  await page.waitForTimeout(1100);
  await noOverflow(page);
  await axe(page);
  await shot(page, "overview");
  await shot(page, "overview-first-viewport", false);
  expect(problems).toEqual([]);
});

test("batch rack filters the equation to one batch", async ({ page }) => {
  await page.goto("./");
  const eq = page.locator(".dr-equation");
  const all = await eq.getAttribute("aria-label");
  const tray = page.getByRole("navigation", { name: "Batches" }).getByRole("button").nth(1);
  await tray.click();
  await expect(tray).toHaveAttribute("aria-pressed", "true");
  await expect(eq).not.toHaveAttribute("aria-label", all ?? "");
  await expect(eq).toHaveAttribute("aria-label", /^Batch /);
});

test("stations: navigate the rail or stepper, back button restores the previous station", async ({ page }) => {
  const problems = watch(page);
  await page.goto("./");
  await goStage(page, "Intake", "intake");
  await expect(page.getByRole("heading", { level: 2 }).first()).toContainText("Intake");
  await goStage(page, "Lineage", "lineage");
  await expect(page.getByRole("heading", { level: 2 }).first()).toContainText("Lineage");
  await page.goBack();
  await expect(page).toHaveURL(/stage\/intake$/);
  await noOverflow(page);
  expect(problems).toEqual([]);
});

test("quarantine: reason filter, pagination, inspector opens and closes", async ({ page }) => {
  const problems = watch(page);
  await page.goto("./#/stage/quarantine");
  const chips = page.getByRole("group", { name: "Filter by reason" }).getByRole("button");
  await expect(chips.first()).toHaveAttribute("aria-pressed", "true");
  if ((await chips.count()) > 1) {
    await chips.nth(1).click();
    await expect(chips.nth(1)).toHaveAttribute("aria-pressed", "true");
  }
  const inspect = narrow(page)
    ? page.getByRole("button", { name: "Inspect row" }).first()
    : page.getByRole("region", { name: /Quarantined rows/ }).locator("tbody tr").first();
  await inspect.click();
  const inspector = page.getByRole("complementary", { name: "Inspector" });
  await expect(inspector).toContainText(/Row \d+ of/);
  await expect(inspector).toContainText("Quarantined");
  await noOverflow(page);
  await axe(page);
  await shot(page, "quarantine", false);
  if ((page.viewportSize()?.width ?? 0) <= 1180) {
    await page.keyboard.press("Escape");
    await expect(inspector).toBeHidden();
  } else {
    await expect(inspector.getByRole("button", { name: "Close" })).toBeHidden();
  }
  expect(problems).toEqual([]);
});

test("drift: repair evidence in the inspector, before/after toggle", async ({ page }) => {
  await page.goto("./#/stage/drift");
  const evidence = page.getByRole("button", { name: "Inspect the evidence" }).first();
  await evidence.click();
  const inspector = page.getByRole("complementary", { name: "Inspector" });
  await expect(inspector).toContainText(/Confidence/);
  if ((page.viewportSize()?.width ?? 0) <= 1180) await inspector.getByRole("button", { name: "Close" }).click();
  const before = page.getByRole("group", { name: "Show values" }).getByRole("button", { name: "Before" });
  if (await before.count()) {
    await before.click();
    await expect(before).toHaveAttribute("aria-pressed", "true");
  }
  await noOverflow(page);
  await shot(page, "drift");
});

test("lineage: tracing an output column highlights its path", async ({ page }) => {
  await page.goto("./#/stage/lineage");
  const chips = page.getByRole("group", { name: "Output column" }).getByRole("button");
  await expect(chips.first()).toBeVisible();
  const last = chips.last();
  const name = (await last.textContent())?.trim() ?? "";
  await last.click();
  await expect(last).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".dr-kv").first()).toContainText(name);
  await expect(page.locator(".dr-trace-line > li[data-active='true']").first()).toBeVisible();
  await noOverflow(page);
  await shot(page, "lineage", false);
});

test("preview: a renamed column is detected in the browser; non-CSV is rejected", async ({ page }) => {
  await page.goto("./#/preview");
  const csv = "order_id,cust_id,order_date,amount,currency,category,quantity,weight_kg,region\nORD-000001,CUS-00001,2026-01-05,12.50,USD,Home,1,1.2,North\nORD-000002,CUS-00002,05/01/2026,8.00,EUR,Toys,2,,East\n";
  await page.locator('input[type="file"]').setInputFiles({ name: "orders.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
  await expect(page.getByText(/2 rows, 9 columns/)).toBeVisible();
  await expect(page.getByText(/possibly renamed to/)).toContainText("cust_id");
  await noOverflow(page);
  await axe(page);
  await shot(page, "preview");
  await page.locator('input[type="file"]').setInputFiles({ name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("hello") });
  await expect(page.getByText(/was not previewed/)).toBeVisible();
});

test("keyboard: skip link and visible focus", async ({ page }) => {
  await page.goto("./");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
  await page.keyboard.press("Tab");
  const outline = await page.evaluate(() => getComputedStyle(document.activeElement as Element).outlineStyle);
  expect(outline).not.toBe("none");
  await shot(page, "keyboard-focus", false);
});

test("dark theme: the theme control applies and stays accessible", async ({ page }) => {
  await page.goto("./#/stage/quarantine");
  await page.getByRole("group", { name: "Colour theme" }).getByRole("button", { name: "Dark" }).click();
  await expect(page.locator(".dr-root")).toHaveAttribute("data-theme", "dark");
  await axe(page);
  // axe reports identical text and background colours as incomplete, not as a violation; check the primary action directly.
  const [fg, bg] = await page.locator(".dr-btn-primary").first().evaluate((el) => [getComputedStyle(el).color, getComputedStyle(el).backgroundColor]);
  expect(fg).not.toBe(bg);
  await shot(page, "dark", false);
});

test("reduced motion: no running animations, equation complete", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("./");
  await expect(page.locator(".dr-equation")).toHaveAttribute("aria-label", /accounted for/);
  const running = await page.evaluate(() => document.getAnimations().filter((a) => a.playState === "running").length);
  expect(running).toBe(0);
  await shot(page, "reduced-motion", false);
});

test("states: loading, empty, error with retry, stale", async ({ page }) => {
  let release: () => void = () => {};
  const gate = new Promise<void>((r) => (release = r));
  await page.route("**/data/snapshot.json", async (route) => { await gate; await route.continue(); });
  await page.goto("./");
  await expect(page.getByRole("status").filter({ hasText: "Loading" })).toBeVisible();
  await shot(page, "state-loading", false);
  release();
  await expect(page.locator(".dr-equation")).toBeVisible();
  await page.unroute("**/data/snapshot.json");

  await page.route("**/data/snapshot.json", async (route) => {
    const res = await route.fetch();
    await route.fulfill({ response: res, json: { ...(await res.json()), latest_run_id: null, runs_included: [] } });
  });
  await page.goto("./?empty");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("No completed runs yet.");
  await shot(page, "state-empty", false);
  await page.unroute("**/data/snapshot.json");

  let fail = true;
  await page.route("**/data/snapshot.json", async (route) => {
    if (fail) await route.fulfill({ status: 503, body: "unavailable" });
    else await route.continue();
  });
  await page.goto("./?error");
  await expect(page.getByRole("alert")).toContainText("The server answered 503");
  await shot(page, "state-error", false);
  fail = false;
  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.locator(".dr-equation")).toBeVisible();
  await page.unroute("**/data/snapshot.json");

  await page.route("**/data/snapshot.json", async (route) => {
    const res = await route.fetch();
    await route.fulfill({ response: res, json: { ...(await res.json()), generated_at: "2025-01-01T00:00:00Z" } });
  });
  await page.goto("./?stale");
  await expect(page.getByText(/older than its 14-day freshness window/)).toBeVisible();
  await shot(page, "state-stale", false);
});

test("pages: runs ledger, request form link, about", async ({ page }) => {
  const preview = await page.request.get("./preview.jpg");
  expect(preview.status(), "portfolio preview image (portfolio-manifest.json preview_image)").toBe(200);
  const problems = watch(page);
  await page.goto("./#/runs");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Every run");
  await noOverflow(page);
  await page.goto("./#/request");
  await expect(page.getByRole("link", { name: /Open the run request form/ })).toHaveAttribute("href", /\/issues\/new\?template=run-request\.yml$/);
  await axe(page);
  await page.goto("./#/about");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("How the refinery works");
  await noOverflow(page);
  expect(problems).toEqual([]);
});
