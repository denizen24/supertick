# Changelog

## 1.1.1 — 2026-10-28

Fixes from the live runs of 22.10.2026 (feature-tariff-links-506, feature-calendar-click-510).

- Tick: `done_without_review` (P1) when a DONE letter has no matching REVIEW in
  `mail/unified-reviewer/` — a proxy coder once committed and reported DONE without calling the
  reviewer, and the tick showed an ordinary `accept_needed`.
- Acceptance: a fourth general check — the coder's mailbox holds an `APPROVED` verdict file for
  the task (the lead passes `CODER:`); a criterion about file contents is proven by quoting the
  line, not by a test name or a commit message.
- Coder and proxy coder: the REVIEW copy goes to the reviewer's mailbox before the message, and the
  message carries `Mail: <file>`; the reviewer takes the time from that letter, re-lists the mailbox
  if it is not there yet, and on a batch of REVIEWs sets `lastEventAt` to the latest one.
- Runtime: the 2.1.308 replies `Message queued for delivery … it is resumed with the message` and
  `Message queued for the main conversation's next turn` are delivery, not `queued`.
- Step 0a: `ListAgents` — team names left by an earlier run in the same session mean a new session,
  since `SendMessage` still resolves those names to the old agents.
- Phase 3: re-verification reports go to `verify-<role>-r<N>.md` instead of overwriting round 1.
- state.md: `## Worktree:` and `## Tracker:` lines; late joiners go into `## Team Roster`.

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
