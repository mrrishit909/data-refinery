import { describe, expect, it } from "vitest";
import { repairText } from "../../src/data/adapter";
import { describe as describeRule, rulesFromContract, type ContractFile } from "../../src/data/contract";
import { isStale, type Snapshot } from "../../src/data/site";
import { infer, preview, similarity } from "../../src/lib/preview";
import { parseRoute, type Route, routeHref } from "../../src/routing";

const contract: ContractFile = {
  contract: {
    contract: "orders.v1",
    columns: [
      { name: "order_id", type: "string", required: true, unique: true, pattern: "^ORD-\\d{6}$" },
      { name: "customer_id", type: "string", required: true, pattern: "^CUS-\\d{5}$" },
      { name: "order_date", type: "date", required: true, format: "%Y-%m-%d" },
      { name: "amount", type: "decimal", required: true, scale: 2, min: "0" },
      { name: "currency", type: "enum", required: true, values: ["USD", "EUR", "GBP"] },
      { name: "quantity", type: "integer", required: true, min: "1" },
      { name: "weight_kg", type: "float", required: false, exclusive_min: "0" },
    ],
  },
  fx: { fx_table: "fixed-2026q1", rates: { USD: "1.00" }, synthetic: true },
};
const rules = Object.fromEntries(rulesFromContract(contract).map((r) => [r.name, r]));

describe("contract rules (browser preview)", () => {
  it("checks patterns, dates, decimals, enums and bounds like the contract says", () => {
    expect(rules.order_id!.check("ORD-000123")).toBe(true);
    expect(rules.order_id!.check("ORD-12")).toBe(false);
    expect(rules.order_date!.check("2026-02-28")).toBe(true);
    expect(rules.order_date!.check("2026-02-30")).toBe(false);
    expect(rules.order_date!.check("05/01/2026")).toBe(false);
    expect(rules.amount!.check("12.50")).toBe(true);
    expect(rules.amount!.check("12.505")).toBe(false);
    expect(rules.amount!.check("-1.00")).toBe(false);
    expect(rules.currency!.check("EUR")).toBe(true);
    expect(rules.currency!.check("JPY")).toBe(false);
    expect(rules.quantity!.check("0")).toBe(false);
    expect(rules.weight_kg!.check("0")).toBe(false);
  });
  it("describes rules in plain words", () => {
    expect(describeRule(contract.contract.columns[4]!)).toBe("required, one of USD, EUR, GBP");
  });
});

describe("preview", () => {
  it("finds a renamed column and reports pass rates", () => {
    const r = preview("order_id,cust_id,currency\nORD-000001,CUS-00001,USD\nORD-000002,CUS-00002,JPY\n", rulesFromContract(contract));
    expect(r.rows).toBe(2);
    expect(r.missing.find((m) => m.name === "customer_id")?.candidates).toEqual(["cust_id"]);
    expect(r.columns.find((c) => c.name === "currency")?.contract?.passRate).toBe(0.5);
  });
  it("infers types and scores name similarity", () => {
    expect(infer(["2026-01-01", "2026-01-02"])).toBe("date (YYYY-MM-DD)");
    expect(infer(["2026-01-01", "05/01/2026"])).toBe("date (mixed formats)");
    expect(infer(["1", "2", ""])).toBe("integer");
    expect(similarity("cust_id", "customer_id")).toBeGreaterThan(0.5);
    expect(similarity("region", "amount")).toBeLessThan(0.5);
  });
});

describe("routing", () => {
  const routes: Route[] = [
    { view: "run", stage: "reconcile" },
    { view: "run", stage: "quarantine" },
    { view: "run", runId: "data-refinery-1", stage: "lineage" },
    { view: "history" },
    { view: "preview" },
    { view: "request" },
    { view: "about" },
  ];
  it.each(routes)("round-trips %o", (r) => expect(parseRoute(routeHref(r))).toEqual(r));
  it("rejects unknown stages", () => expect(parseRoute("#/stage/nope")).toEqual({ view: "run", stage: "reconcile" }));
});

describe("staleness", () => {
  it("is stale after its window", () => {
    const s = { generated_at: "2026-10-01T00:00:00Z", stale_after_hours: 336 } as Snapshot;
    expect(isStale(s, Date.parse("2026-10-10T00:00:00Z"))).toBe(false);
    expect(isStale(s, Date.parse("2026-10-20T00:00:00Z"))).toBe(true);
  });
});

describe("repairText", () => {
  it("says when a category has no safe mapping instead of mapping to null", () => {
    expect(repairText("category_map", { from: "Elec", to: null, rule: "abbreviation", rows_affected: 7 })).toBe('"Elec" has no safe mapping (abbreviation, 7 rows).');
    expect(repairText("category_map", { from: "HOME", to: "Home", rule: "case", rows_affected: 5 })).toBe('"HOME" becomes "Home" (case, 5 rows).');
  });
  it("falls back to key: value pairs for unknown kinds", () => {
    expect(repairText("other", { a_b: 1 })).toBe("a b: 1");
  });
});
