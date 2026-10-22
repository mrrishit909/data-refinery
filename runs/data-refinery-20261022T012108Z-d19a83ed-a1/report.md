# Data Refinery run report

Batches 2026-01, 2026-02, 2026-03; repair mode `propose`; contract `orders.v1`; FX table `fixed-2026q1` (synthetic fixed rates).

## Accounting

| Batch | Rows in | Accepted | Quarantined | Merged duplicates | Identity holds |
|---|---:|---:|---:|---:|---|
| 2026-01 | 1000 | 1000 | 0 | 0 | yes |
| 2026-02 | 1206 | 0 | 1186 | 20 | yes |
| 2026-03 | 1100 | 51 | 1049 | 0 | yes |

## What was found

- 2026-01: quarantine reasons: none; merged exact duplicates: 0.
- 2026-02: quarantine reasons: BAD_DATE 182, DUPLICATE_CONFLICT 12, NEGATIVE_AMOUNT 3, UNKNOWN_COLUMN_SET 1186; merged exact duplicates: 20.
- 2026-03: quarantine reasons: AMBIGUOUS_CATEGORY 13, INVALID_CATEGORY 37, NULL_REQUIRED 12, UNRESOLVED_UNIT_SCALE 1047; merged exact duplicates: 0.

## What was repaired

- Nothing.

## Safe repairs proposed but not applied (propose mode)

- R-2026-02-01: rename column cust_id to customer_id, confidence 0.889.
- R-2026-02-02: read order_date as %d/%m/%Y (177 values), confidence 0.76.
- R-2026-03-01: map category 'Apparrel' to Apparel (typo, 4 rows), confidence 0.9.
- R-2026-03-03: map category 'Electronic' to Electronics (plural, 6 rows), confidence 0.97.
- R-2026-03-05: map category 'Grocey' to Grocery (typo, 4 rows), confidence 0.9.
- R-2026-03-06: map category 'HOME' to Home (case, 5 rows), confidence 0.99.
- R-2026-03-08: map category 'Toy' to Toys (plural, 6 rows), confidence 0.97.
- R-2026-03-09: map category 'electronics' to Electronics (case, 8 rows), confidence 0.99.
- R-2026-03-10: convert weight_kg from g to kg (x0.001), confidence 0.697.

## What needs a human

- R-2026-03-02 (2026-03): 'Elec' is only a prefix of Electronics; a truncated label can also abbreviate a category outside the contract, so the data does not determine its meaning.
- R-2026-03-04 (2026-03): 'Gift Cards' matches no contract category by case, plural, single-edit typo, prefix or token.
- R-2026-03-07 (2026-03): 'Home & Garden' combines Home with other terms (garden) that have no contract category; it is broader than Home and may belong elsewhere.

## Late arrivals and restatements

- None.

## Self-checks

- 9 of 9 passed.

## Limitations

- All data is synthetic, generated deterministically by `datarefinery.fixtures`; nothing here describes real orders or customers.
- `amount_usd` uses one fixed, synthetic FX table for every date; it is not market data.
- One contract (`orders.v1`); there is no schema evolution or contract negotiation.
- Category repairs are lexical rules (case, plural, single-edit typo). There is no semantic model, so anything else is left to a human.
- Unit-scale drift is judged against the previous accepted batch, or the contract's declared reference when no earlier batch is in the request.
- Duplicates are detected within a batch and against ids accepted earlier in the same run only.
