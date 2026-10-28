# Changelog

## 1.1.0 — 2026-10-28

- Team gate: after rating complexity the lead checks that the work is COMPLEX and touches money or
  security; otherwise it asks once whether to go on as a single session. `--force-team` skips the
  question.
- Step 0a: a run does not start while another run's supervisor loop is alive on the same machine.
- The full test suite runs once per run, in phase 3. Coders and acceptance checkers run only the
  task's own tests; a task criterion may not demand the whole suite.
- `--full-run=deferred` and `FULL RUN: deferred to release` in VERIFICATION_PLAN.md: for a feature
  that ships with other branches, the full suite moves to one final run after the merge.
- ci-verifier runs full suites under `flock /tmp/supertick-heavy.lock` when `flock` exists.

## 1.0.0 — 2026-10-22

First public release.
