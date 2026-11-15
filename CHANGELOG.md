# Changelog

## 2.0.0 — 2026-11-15

First stable 2.0: the four-host team plugin from the `universal` branch (121-567) plus the language setting
(121-604). Versions of all four bundles are `2.0.0`; no beta suffix from now on.

### Language of the human-facing output (121-604)

- One setting picks the language (`ru` | `en`) of what a person reads: the lead's feed, questions, `PLAN.md`,
  `VERIFICATION_PLAN.md`, `DECISIONS.md`, `LEGACY_REPORT.md`, the summary, and the `supertick-doctor.py` /
  `supertick-setup.py` output. Role prompts stay Russian (D9); protocol tokens (`DONE:`, `VERDICT:` …), template
  headings, paths, commands and commit messages do not change.
- Resolution order: `--lang` → `$SUPERTICK_LANG` → active profile `language` → top-level `language` → language of the
  user's request (the lead passes `--request-lang`) → locale (`LC_ALL` → `LC_MESSAGES` → `LANG`; `C`/`POSIX` give
  nothing) → `en`. New `engine-config.py lang` prints `LANG: <lang> (<source>)`; `validate` checks the key.
- `supertick-setup.py`: the first dialog question is the language (asked in both languages, the resolved one
  first); `--answers` accepts `"language"`, which is written to the profile; `--lang` on every command.
- `supertick-doctor.py`: a `language:` line with its source, `--lang`. `engine-config.py resolve --lang` gives the
  ⚙️ feed lines in that language.
- Prompts: a fourth header line `LANG:`; the lead resolves it at step 0c (Claude Code) or `lead:lang` (shared
  protocol) and keeps it in `state.md` as `Language:`; coder, tech lead and architect write their human-facing files
  in it. `/supertick:team-feature --lang=ru|en`; `/supertick:interviewed-team-feature` runs the interview in the
  resolved language and passes `--lang` on.
- Without any setting the feed follows the language of the request, so a Russian request still gets a Russian
  feed; scripts with no profile and a `C` locale now print English.
- The language never changes commit messages: the plan's `Commit:` line keeps the project's `git log` style; template
  explanations follow `LANG`, while tick fields (`## Task <id>:`, `Status:`, statuses) stay as they are. Found in the
  live SIMPLE run with `--lang=en` (ALL_PASS 11/11, English feed), where the lead wrote "message in English".

### Four hosts — branch `universal` (121-567)

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

- New `scripts/supertick-doctor.py` (F7, read-only): bundle version from the manifest in the root,
  host and its level, root (`$SUPERTICK_ROOT` or the folder above `scripts/`), config files with the
  `validate` result and where profiles are written, the CLIs with versions (`--probe` adds one probe call
  each), the active profile with its lead, lead-probe result and whether the CLI set still matches the
  fingerprint, and the step 0 decision of `engine-config.py host`. Exit code 1 on validate errors or a
  broken config file.

- `supervisor-tick.py --mode shared` and `supervisor-wait.sh --mode shared` (P1) for hosts without
  messages between agents, where the lead runs the loop and executors do not keep their own cards.
  Mail rules (copies, letters to the lead, undelivered letters, DONE without REVIEW) and silence rules
  (first minute, checkpoint, silent too long, busy) are off. New: `engine_result_ready` (P1, a finished
  engine is the lead's next step, not a 15-minute wait), `executor_overdue` (P0, past `timeoutSec` of the
  card, one hour by default, with no live engine call of that name), `executor_report_ready` (P1, the
  card's `report` file is newer than the start), `engine_not_started` (P1). Dead, failed and unread
  engines and acceptance reports work as before. Without the flag the output is byte-identical (F1
  snapshots). `supervisor-wait.sh` runs in the background where the host wakes the lead, or in the
  foreground with `--max` below one shell call's limit where it does not.

- Shared lead protocol (P2) for hosts without messages between agents: `hosts/shared/LEAD.md` (545 lines,
  no host tool names) and role briefs `core/roles/*.md` (12 roles). The lead runs the loop: one mechanism
  for every role — card (`run-state.py new`), prompt file, `engine-config.py argv` (engine via
  `run-engine.sh`, or exit 3 for the host's own executor), wait with `supervisor-wait.sh --mode shared`
  (background on L2, foreground with `--max` on L1), take the report and decide by its first line
  (`READY FOR REVIEW`, `VERDICT: … — APPROVED | CHANGES REQUESTED`, `COMMITTED` + `COMMITS:`,
  `ACCEPT: … — PASS | FAIL`). The fix after review goes to the same executor by resume. Host differences
  live in `hosts/<host>/primitives.md` (H1, H4). Not part of the Claude Code build.
  `mock-engine.py` gets `SUPERTICK_MOCK_ACT`: a scenario command whose stdout becomes the reply, so a
  test can play coder, reviewer and acceptance without a model.

- Kimi Code CLI adapter (H1): `hosts/kimi/` — `kimi.plugin.json`, skills `team-feature` and `setup`,
  `primitives.md` (L2: background subagents of the plugin's `supertick-<role>` types, resume by `agent_id`,
  background `supervisor-wait.sh`). `tools/build-host.py kimi` builds the plugin into `build/kimi/supertick`
  (not in git); agent types are the `core/roles` briefs with a YAML header from the new `frontmatter` key
  of `build.json` (and `exclude` keeps `scripts/tests` out). `tools/install-kimi.sh` installs without the
  interactive `/plugins install` (copy into `plugins/managed/` plus a registry entry, registry backed up).
  Live SIMPLE from Kimi on 12.11.2026: three tasks DONE, coder resumed for the commit, the lead woke
  itself, a failing cursor engine fell back to the host (`docs/spikes/h1-kimi-live.md`).

- Codex CLI spike S0-CX (12.11.2026, `docs/spikes/s0-hosts.md`) and the `codex` preset fixed after it:
  `--` before the prompt (a brief starting with `---` was read as an option and the call died with code 2),
  `--json` (reply = last `agent_message`, session = `thread_id`; `run-engine.sh` parses it), `-m`, effort and
  `-c sandbox_mode` repeated on `resume` (it silently used the default model otherwise), `--skip-git-repo-check`
  on `resume`. A role that commits (`coder`) gets the git dirs of the project as writable roots
  (`{git_flags}`; `workspace-write` mounts `.git` read-only) — note this also opens `.git/hooks`. Codex is
  host level L1 with native sub-agents: no wake-up of the lead, waiting only inside a turn. The 1.1.1 `codex`
  argv is not kept. The lead probe stand gained `PROBE_WAIT=poll` for hosts without wake-up.

- Lead probe (H2, p. 4.5): new `scripts/lead-probe.py` and `supertick-setup.py lead-probe <profile>`. The probe
  starts the profile's lead host headless on its model with a tiny task in a throw-away stand (git repo,
  a copy of the bundle with `supervisor-wait.sh` wrapped by a call counter, every role on the mock engine
  with a script: the first coder round leaves a `PROBE-BUG` line, the reviewer asks for changes, then
  approves, acceptance passes). The verdict comes from the run files only, no model judges text: `finish`,
  `root`, `state`, `plan`, `engine` (mock calls ≤ `run-engine.sh` launches), `wait` (`supervisor-wait.sh
  --mode shared`, no `sleep` polling), `cycle` (review and acceptance before DONE; the coder is resumed,
  not replaced, after CHANGES REQUESTED), `taken` (final tick without P0/P1), `hands` (only the mock coder
  commits, tree clean). `passed` or `failed: <first failed check>` with date and bundle version is stored
  as the profile's `lead_probe` (atomic write with backup; `--no-write` to only look) and shown by the
  dialog ("проверена как ведущий <date>"), by `plan`, `discover` and `doctor`. It warns, never forbids;
  `apply` keeps the result while the lead host and model stay the same. Hosts: Kimi now; Codex with its
  adapter (H4), OpenCode with H3; the Claude Code lead keeps its 1.1.x protocol and is not probed.
  A lead that runs tests itself gets a note (`⚠ tests`, stored with the result), not a failure. Live on
  Kimi `kimi-code/k3`, 13.11.2026: four runs, the last `passed` in 995 s (`docs/spikes/h2-lead-probe-live.md`).

- Kimi skills: the bundle root is computed by one command, `cd "${SUPERTICK_ROOT:-${KIMI_SKILL_DIR}/../..}"`.
  The first live probe showed the lead running the old block literally and taking the installed plugin while
  `$SUPERTICK_ROOT` pointed elsewhere.

- Codex CLI adapter (H4): `hosts/codex/` (plugin `.codex-plugin/plugin.json`, skills `team-feature` and
  `setup`, `primitives.md` for level L1: the lead waits inside its turn with `supervisor-wait.sh --max 1500`
  in the foreground and `write_stdin` polls of up to 300 s, and never ends the turn to wait) and
  `tools/install-codex.sh` (local marketplace, then `codex plugin marketplace add` and `codex plugin add`;
  repeat refreshes the cache, `--uninstall`). Checked on 13.11.2026: inside a `workspace-write` lead a child
  `codex exec` does not start (read-only `~/.codex`), and with network and `~/.codex` writable it cannot
  build its own sandbox inside the lead's (bubblewrap). So the Codex lead runs without a sandbox
  (`launch` prints `codex -m <model> --sandbox danger-full-access`), and role limits are held by the
  executors' own sandboxes: under such a lead a `codex exec --sandbox read-only` child runs shell and is
  refused writes.
- `engine-config.py`: on Codex a role without an assignment runs on the `codex` engine with the lead's
  model instead of a native subagent (`spawn_agent` has neither type nor sandbox, so a read role could
  write); only the coder gets the `.git` directories writable. Without the `codex` binary the roles stay
  native. Explicit assignments win.
- New `scripts/git-guard.py` (risk R11): `snapshot` fingerprints `.git/hooks` and `.git/config` (and the
  worktree config) right after the run directory is made, `check` compares them at the end. LEAD.md got
  `lead:git-guard` and `lead:git-check`; a change goes to the feed verbatim and first into the manual
  checks, the lead does not revert it. The lead probe fails `hands` on such a change.
- Lead probe for Codex: `codex exec --sandbox danger-full-access --json`, shell commands read from
  `command_execution` items, the installed bundle found in `$CODEX_HOME/plugins/cache/supertick-local/`.
- `run-engine.sh`: when the report file did not exist at launch and the engine wrote it itself during the
  call (roles with write access do so despite the brief — seen in the H4 live run with a Codex coder and
  cursor in write mode), that file is the report (`report_by=engine_file` in `.done` and the ledger) and no
  `-<label>` copy of the reply is made; the reply stays in `.out.result.md`. A report that existed before
  the call is still never overwritten.

### Merge to `main`

- The Claude Code plugin and the marketplace entry carry `2.0.0-beta.1`, like the Kimi, Codex and OpenCode bundles:
  Claude Code caches plugins by version, so the new build (2.0 scripts under the unchanged 1.1.x texts) reaches it
  only with a new version. Claude Code behaviour is still that of 1.1.1.

### X2 — run mirror `state/run.json`

- In `--mode shared` the tick writes `state/run.json` on every pass, atomically and only when the content changes:
  phase, complexity, budgets, host, tasks (status, files, planned agents, coder, reviewer, commits), agents (role,
  access, engine, provider, model from the ledger), engine calls, `revision` (SHA-256 without itself). Nobody else
  writes it; the lead and the roles do not read it. Claude Code mode writes no mirror. Schema — `docs/run-json.md`.
- `supervisor-tick.py validate-run <run>` catches the typical corruptions (not JSON, keys and schema, a hand edit
  that breaks the revision, tasks out of step with PLAN.md, broken agents and engine calls, secret-like keys);
  `postrun-check` runs it when the mirror exists.

### X3 — `busy` on macOS

- The tick's `busy` rule (a test or build run under the repository root downgrades `silent_too_long` to P3) works on
  macOS: processes from `ps -axo pid=,lstart=,command=`, the working directory of test and build candidates from
  `lsof -a -d cwd -p … -Fn`. No `lsof` (or another OS without `/proc`) — the rule is off and `supertick-doctor.py`
  says why (`busy: /proc | ps и lsof | выключено — …`). Linux behaviour is unchanged (tick snapshots).
- The process working directory is compared after `resolve()`, like the root: on macOS `/var/folders/…` is
  `/private/var/folders/…`, and the first macOS CI run failed on exactly that.

### X1 — budgets, `plan`, `postrun-check`

- Budgets in `state.md` (`## Budgets`, printed by `supervisor-tick.py budgets <complexity> [--budget=…]`): `observe` by
  default (P3 `budget_note`, the lead is not woken), `enforce` (P1 `agent_budget_exceeded`, `concurrency_exceeded`,
  `review_rounds_exceeded`, `fanout_planned_exceeded`), `off`. Every excess is one event. Runs without the section
  (Claude Code, 1.1.x) are untouched — the tick snapshots and the replay against 1.1.1 stay the same.
- P0 `read_agent_owns_files` in any mode: an executor with `access: read` committed. `run-state.py` gives shared-protocol
  cards (`executor=`) the role's access from the engine-config registry and accepts `commits=`.
- `supervisor-tick.py plan <run>` (planned agents against `max_agents`; 2 only on excess in `enforce` or without the
  section) and `postrun-check <run>` (commits of DONE tasks exist, acceptance PASS, no OPEN copies, no unread or live
  engines, budget excesses written down, no commits from read roles; 1 on any finding). LEAD.md: budgets at step 0,
  `Planned agents:` in PLAN.md, `plan` before the first coder, `postrun-check` before the summary.
- Thresholds from `tools/calibrate_budgets.py` over 54 past runs (`docs/budgets.md`): 17/3/4, 27/4/9, 42/5/19 — the
  spec's 8/14/30 agents would fire on every shared-protocol SIMPLE. Idea from dsh-supertick, code written anew (`NOTICE`).

### V2 (2.0.0-beta acceptance)

- Version `2.0.0-beta.1` for the Kimi, Codex and OpenCode bundles (the Claude Code build stays 1.1.1 plus the declared
  changes); the OpenCode bundle carries `supertick.json`, which `supertick-doctor.py` reads.
- `docs/hosts.md` rewritten for beta: per-host recipes (install, run with and without a person, traps) for Kimi, Codex
  and OpenCode, macOS requirements (GNU tools from Homebrew), probes passed on all three hosts.
- CI runs on Linux and macOS (`macos-latest` installs `bash coreutils util-linux` from Homebrew, as the recipe says)
  and builds every host bundle.
- Acceptance: R4 (OpenCode) passed — `docs/trials/2.0-beta-runs.md`; R1–R3, R5, R6 carry over from alpha.

### H3 — OpenCode adapter

- `hosts/opencode` and `tools/install-opencode.sh`: skills `supertick-team-feature` and `supertick-setup`, role agents
  `supertick-<role>` with OpenCode permissions (the same access as the Kimi types), installed into
  `~/.config/opencode/` (or `<repo>/.opencode/` with `--project`). The whole bundle lives inside the team-feature
  skill directory: OpenCode refuses files outside the project in `opencode run` (`external_directory` ask →
  auto-reject, and a rejection ends the run), while skill directories are open to the lead and subagents.
- OpenCode is L1 with native subagents in the host table, can lead (setup), and has a lead probe (`opencode run
  --format json`, OpenCode shell commands parsed); the probe sets `PWD` (OpenCode takes the project from it) and
  allows the stand directory. `build-host.py` frontmatter accepts one-level string objects (agent permissions).
- Live checks (`docs/spikes/h3-opencode-live.md`): the lead probe passed on `opencode/glm-5.3-flash` (paid, cheap);
  the acceptance SIMPLE run from OpenCode reached ALL_PASS in 15 minutes, 3 tasks DONE, coder commits, GIT GUARD OK,
  all roles native `supertick-*` agents. Free models hit the Zen free usage limit, after which `opencode run` hangs.

### S0-OC and the `opencode` engine preset

- Spike S0-OC (`docs/spikes/s0-hosts.md`, fixtures in `docs/spikes/fixtures/opencode/`): OpenCode 1.19.5 is L1 with
  native subagents — own agent types with model and permissions, `task_id` resumes a subagent, parallel calls;
  no background and no wake-up (the lead waits within its turn; `bash` timeout 120 s by default, no upper limit).
  The 596-line lead probe passed 10/10 on two free models.
- New engine preset `opencode`: `env OPENCODE_CONFIG=<engines>/opencode-supertick.json opencode run --format json
  -m <model> --agent supertick-read|build -- <prompt>`, resume adds `-s <session>`. `--` is required (a brief starts
  with `---`); the read agent (`engines/opencode-supertick.json`: edit, bash, webfetch, task denied) comes from our
  own config file, nothing is written into the project; `--agent` is repeated on resume because a resumed session
  falls back to `build` and writes (live check). Default model is a free one (`opencode/deepseek-v4.1-flash-free`):
  Zen bills per token. Roles with commands take `build`.
- `run-engine.sh` parses `opencode run --format json`: session from `sessionID`, reply from the text parts of the
  last message that has text, a `type: error` line is a failure.
- Setup lists OpenCode models from `opencode models` (free ones first) instead of the two-model fallback catalog.

### V1 (2.0.0-alpha acceptance)

- Full suite before V1: 52 script tests and all tool tests green; the Claude Code build still matches
  the committed plugin.
- Acceptance runs R1, R2, R3, R5, R6 passed on the bench (`docs/trials/2.0-alpha-runs.md`); the final
  suite after the V1 fixes: 52 script tests, 368 tool tests (5 skipped) green.
- New `tools/replay_tick.py` and `docs/trials/2.0-replay.md` (acceptance item 3): the 2.0 and 1.1.1
  ticks run side by side on time slices of 22 past runs (files with mtime up to the moment, ledger lines
  up to the moment by `ts`, mutable files in their final form, no read marks). 2576 P0–P2 events from
  1.1.1, zero new ones from 2.0, zero lost, zero text-only differences.
- Setup writes a complete profile: every role without an assignment is written as `null` (the host's
  native executor), and "second opinion off" as `second-reviewer: null`. Before, an empty `roles` let
  the 1.1.x roles from `agent-teams.json` (the profile overlays them) take over at run time: the dialog
  showed "all native", the run used cursor. The `plan` table is now built from the same resolve as step
  0b, over the legacy file. `engine-config.py validate` accepts `null` roles; on Codex and on hosts
  without subagents a `null` role still goes to the host engine with the lead's model; a `null`
  `second-reviewer` is off without a warning.
- `supervisor-wait.sh`, `--mode shared` only: one wait per run. A new wait stops the other waits of the
  same run (found by command line, never its own `bash -c` wrapper or a wait of another run) and waits
  for them to exit; `--stop` stops them at the end (new `lead:wait-stop` in phase 3, step 8). In the R2
  live run the Kimi lead left a forgotten background wait, and `kimi -p` does not exit while a
  background task lives, so the run hung until the timeout with no summary. Claude Code mode unchanged.
- LEAD.md: phase 3 verifiers as a table (`## Build & Types` and `## Tests` only to `ci-verifier`, never
  to the read-only `acceptance-checker` — H4 got BROKEN on `.pyc`); a task's acceptance criteria are
  only its own checks; the lead may drop an out-of-scope finding (not CRITICAL, not `[security]`) before
  round 3 by a DECISIONS.md entry and a `DECISION: … Re-check.` continuation of the same reviewer
  (the reviewer brief knows it); the final summary takes the test result from the ci-verifier report
  instead of running tests itself.
- LEAD.md: the `Tooling:` line of a task is its own check command, not the whole suite (R5: `python3 -m
  unittest` with no tests yet exits 5, acceptance FAIL on correct code).
- `run-engine.sh`: the ledger names the model of each call (`model`, from `--model` / `-m` in the engine
  command; acceptance R3 asks for engine and model). The report in `reports/` starts at the reply's first
  protocol head line (`VERDICT:`, `ACCEPT:`, `READY FOR REVIEW:`, `CI:`… — not one quoted in backticks or
  quotes); a preamble glued in front is cut and the count lands in `report_trimmed`, the reply stays as is
  in `.out.result.md`. In R3 the cursor reviewer answered «Читаю бриф роли…VERDICT: task #1 — APPROVED»,
  and acceptance rejected the report by its first line: one extra review and acceptance round.
- `engine-config.py host` (step 0) finds CLIs the way setup does (every known CLI binary), not only the
  binaries of engine presets: `opencode` has no engine preset yet and was reported as vanished from every
  profile (R6).
- LEAD.md: each ⚙️ line of `resolve` goes to the feed verbatim, on its own line (it carries the reason of
  a substitution); the final summary lists engine substitutions (or says there were none). In R6 the lead
  folded «unified-reviewer: движок cursor не найден» into a generic «reviewer — native» and the summary
  did not mention the fallback.

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
