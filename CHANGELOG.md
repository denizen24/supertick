# Changelog

## Unreleased — branch `universal` (121-567)

- Repository layout, no change to the plugin behaviour: shared scripts live in `core/scripts/`, Claude Code
  texts, agents and manifest in `hosts/claude-code/`, and `tools/build-host.py claude-code` builds
  `plugins/supertick/` from them. The build is byte-identical to 1.1.1 (hashes and executable bits
  pinned in `tools/tests/snapshots/plugin-1.1.1.manifest`); CI fails if the committed plugin differs
  from a fresh build or if `core/` mentions Claude Code beyond the declared exceptions. See
  `docs/build.md`.

- New `scripts/engine-config.py` (nothing calls it yet, the Claude Code texts are unchanged):
  engine config as a tool instead of the lead reading JSON — `resolve` (step 0b), `argv`
  (ready `run-engine.sh` command for a role), `validate`, `host`. Config search `$SUPERTICK_CONFIG` →
  `~/.config/supertick/config.json` → the 1.1.x file, merged by key (earlier wins); profiles overlay
  `roles`; `fallback` is `host | fail` (`claude` is a synonym). On the 1.1.x configs the resolution
  equals the hand-written step 0b tables. One deliberate refinement: `second-reviewer` is switched
  off only when engine **and model** match the reviewer (D7); 1.1.1 compared engines only.

- Engine presets (F4), all checked by live calls on 12.11.2026 (reply, resume remembers a word, read
  mode cannot write a file): `kimi` rewritten for Kimi Code CLI 2.x (`--output-format stream-json`,
  session from `session.resume_hint`, resume `-S`; read mode is `--agent-file engines/kimi-read.md`, which
  Kimi does not accept together with `-S` — the profile is restored with the session); new `claude`
  (child Claude Code without skills, commands, MCP and user settings, so it cannot start supertick
  again; read `--tools Read,Grep,Glob`, write `acceptEdits` with Bash); new `mock` with
  `scripts/mock-engine.py`. `run-engine.sh` takes the reply from the last assistant line when the JSON
  has no `result`. The 1.1.1 `kimi` argv is not kept (the old CLI is retired). The Claude Code texts
  still describe the 1.1.1 presets until the lead calls `engine-config.py argv` (P2/H1).

- New `scripts/supertick-setup.py` (F5, nothing calls it yet) and `references/models-catalog.json`: the
  connection step as a tool that runs from a plain terminal. `discover` finds `claude`, `kimi`, `opencode`,
  `codex`, `cursor-agent`, `grok`, reads their models from the CLI's own config (Kimi `config.toml`, Codex
  models cache) or the built-in catalog, and probes each with one headless call (`responds`,
  `not_logged_in`, `no_answer`, `error`); `plan` and `apply --answers FILE` turn the dialog answers (lead,
  layout all-on-lead / split / groups, second opinion) into a profile and write it atomically with a
  backup into `$SUPERTICK_CONFIG` or `~/.config/supertick/config.json`; the 1.1.x `agent-teams.json` is
  never written. `apply` refuses engines that are not connected and leads that cannot lead
  (`--force` overrides). `launch <profile>` prints the lead start command; `setup` is the same dialog in a
  terminal. The lead probe (`lead-probe`, p. 4.5) and the "when to ask" rules (F6) are separate tasks.

- `engine-config.py host` now also decides what the connection step must do (F6, the eight cases of
  p. 4.4; the Claude Code texts do not call it yet): `action` is `none` (Claude Code without a profile:
  no question, no line), `line`, `setup`, `ask_restart` (lead of the profile differs from the session:
  one question, `launch` gives the restart command) or `stop` (non-interactive session that needs a
  profile). New flags `--setup`, `--non-interactive`, `--available`. A profile written by `setup`
  now records `sources` and `installed`, so a new CLI and a vanished source are told apart; hand-written
  profiles without them skip those two checks.

- Codex CLI spike S0-CX (12.11.2026, `docs/spikes/s0-hosts.md`) and the `codex` preset fixed after it:
  `--` before the prompt (a brief starting with `---` was read as an option and the call died with code 2),
  `--json` (reply = last `agent_message`, session = `thread_id`; `run-engine.sh` parses it), `-m`, effort and
  `-c sandbox_mode` repeated on `resume` (it silently used the default model otherwise), `--skip-git-repo-check`
  on `resume`. A role that commits (`coder`) gets the git dirs of the project as writable roots
  (`{git_flags}`; `workspace-write` mounts `.git` read-only) — note this also opens `.git/hooks`. Codex is
  host level L1 with native sub-agents: no wake-up of the lead, waiting only inside a turn. The 1.1.1 `codex`
  argv is not kept. The lead probe stand gained `PROBE_WAIT=poll` for hosts without wake-up.

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
