---
name: update-dummy-data
description: Use when changing the dummy data generator (src/data/generate.py), its tables, patterns, row counts or planted problems, so seed-42 reproducibility and the 5 planted problems stay intact.
---

# Update the dummy data

The generator is "connector #1" (plan section 5 and design rule 1). Tests and
the AI evaluation depend on its output being identical on every run and on the
5 planted problems being present.

## Checklist

1. **Read plan section 5** and `data/planted_issues.json` before changing
   anything. Table names and columns must stay the six standard tables.
2. **Keep seed 42 reproducible.** Use only the seeded generators (`SEED`
   constant, one NumPy `Generator` and a seeded Faker). No `random` module,
   no unseeded calls, no dependence on the current date or dict/set ordering.
3. **Protect the planted problems.** Keep the exact customer names, product
   names and SKUs (Harbor & Pine Co., Bluegate Supplies, Kestrel Home Stores,
   Classic Denim Jacket AP-1150, Smart Bottle Pro SB-1042). If a change affects
   them, update `planted_issues.json` and the tests in the same commit.
4. **Constants, not magic numbers.** New tuning values go in the named
   constants at the top of `generate.py`.
5. **Regenerate:** `python -m src.data.generate` and check the printed summary
   (row counts, date range, total revenue) against the plan's targets.
6. **Re-run tests:** `pytest`. `tests/test_data.py` checks row counts, foreign
   keys, date range, reproducibility and every planted problem.
7. If row counts or planted-problem details changed on purpose, say so in the
   commit message. Run `ruff format . && ruff check --fix .`, then commit.
