# Antigravity review, 2026-11-27

Antigravity (Gemini 4 Hydrogen, read-only) on `06059d32..c520327`, the gate-day backfill of PR #46 (D-056), run at Mike's request with `scripts/agents/antigravity_review.sh`. The review text is posted as written on [PR #46](https://github.com/tuannm3812/sydney-transit-lakehouse/pull/46#issuecomment-7220770340). PR #46 had already merged, so the fixes are in the follow-up PR.

Verdict, in short: D-056 fixes D-040's watermark reversal, and all six findings of the second 25 Nov review stay closed. Three resume edge cases in `replay` and two minor items remain.

## What happened to the findings

| Finding | Status |
|---|---|
| 1. A watermark in the hour before `start` was taken as a finished hour; a run cut mid-hour there lost the rest of that hour and ran its last file unseeded (B) | Fixed in the follow-up PR. Replay keeps a per-feed record in the scratch folder (`replay-state/<feed>.json`) of the last hour it finished and the watermark then. It carries on with the next hour only when the tables' lowest watermark matches that record and the file is still in the scratch folder. Otherwise it fetches the watermark's hour again. Test: `test_resume_after_a_cut_in_the_middle_of_an_hour`, which failed on `c520327`. |
| 2. A table with no watermark while the others have one (a cut in the very first hour with no seed hour) made the next run mark the seed in every table, dropping the lagging table's rows (B) | Fixed: the seed mark is written only to tables with no watermark, and such a feed restarts from the first hour in the scratch record. If there is no record, replay stops and says to rebuild. Test: `test_resume_when_one_table_is_behind`, which failed on `c520327`. |
| 3. Timetables did not move back to their watermark, so publishes between a cut run and a later start were skipped (B) | Fixed: the timetable watermark wins over `start`, as for the realtime feeds. `test_backfill_never_leaves_a_hole` now includes the timetable feed and checks the versions. |
| 4. `--maintain-every 0` turned off the end-of-run compaction and expiry (A) | Fixed: 0 means "only at the end"; `None` (code only) means never. Test: `test_maintain_every_zero_still_maintains_at_the_end`. |
| 5. `backfill.sh` aborted in `grep` before its own "OFFLOAD_BUCKET is not set" message (A) | Fixed: the `grep` no longer fails the script. |
| Decisions to revisit: nightly gold before I-21 is fixed | The runbook now says to build gold without a service-date window until I-21 is fixed, and that a full-history build under the 900 MB cap is not measured yet. |
| Decisions to revisit: D-050 gap, O-04 timing | Not changed here. |
