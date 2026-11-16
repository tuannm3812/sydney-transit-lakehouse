# 0. Coding standards (project deltas)

The master standard at `~/Documents/GitHub/coding-standards/coding_standards.md` applies in full. It is not copied here. This file lists only what this project adds or overrides.

## Shape

- **Package-first** (§1) and **Shape B** docs (§2). The pipeline is the centre of gravity. Notebooks, if any are added later, read Iceberg/dbt outputs and never hold pipeline logic.
- Root `STATUS.md` is the running checklist. `docs/3_decisions.md` uses `D-NNN` ids, and code comments cite them (e.g. "see D-006").

## Deltas and project rules

- `data/` exists at runtime on the collector host and is gitignored. Raw snapshots are never committed, not even samples. Tests build protobufs in code.
- Lint and format with `ruff` at 79 columns, and test with `pytest`. CI runs both plus `collector --check-config` and a Docker build (§14).
- All partitions and stored timestamps are UTC (D-006). The single exception is TfNSW's quota reset, which is midnight **AEST** (fixed UTC+10), not Sydney civil time.
- Any change to `config/feeds.toml` must keep `collector --check-config` under the quota budget, and it gets a decisions-log row if it changes the feed set or cadence.
- Every poll attempt is written to the manifest. Do not add a code path that skips recording a failure: capture rate is computed from the manifest (D-007).
- The TfNSW key is read from `TFNSW_API_KEY` only. Never print it, never include it in a report, never send it in a URL query string.
