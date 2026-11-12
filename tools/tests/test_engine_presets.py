"""Четыре старых пресета через run-engine.sh на мок-движках (121-567, F1).

Мок-бинарь называется как настоящий CLI (`cursor-agent`, `codex`, `kimi`, `grok`) и печатает вывод
в его формате. Фиксируем две вещи 1.1.1, на которые опирается 2.0:
- argv пресета (эталон `snapshots/presets-1.1.1.json`) доходит до бинаря без искажений, `{prompt}`
  раскрывается в текст промпта одним аргументом;
- из вывода достаются сессия, ответ и причина провала; resume идёт в ту же сессию.

Сети нет, настоящие модели не вызываются. Запуск: python3 -m unittest discover -s tools/tests
"""
import json
import os
import shutil
import subprocess
import time
import unittest
from pathlib import Path

from test_scripts import SCRIPTS, executable, tmpdir

HERE = Path(__file__).resolve().parent
GOLDEN = json.loads((HERE / "snapshots" / "presets-1.1.1.json").read_text(encoding="utf-8"))
PRESETS = GOLDEN["presets"]
DONE_WAIT_SEC = 20

CURSOR_SESSION = "aaaaaaaa-0000-4000-8000-000000000001"
CODEX_SESSION = "bbbbbbbb-0000-4000-8000-000000000002"
KIMI_SESSION = "session_0123456789abcdef"
GROK_SESSION = "cccccccc-0000-4000-8000-000000000003"

MOCK_ENGINE = r'''#!/usr/bin/env python3
# мок CLI-движка: имя файла = имя настоящего CLI, вывод в его формате
import json, os, sys, time
name = os.path.basename(sys.argv[0])
argv = sys.argv[1:]
with open(os.environ["MOCK_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({"bin": name, "argv": argv}, ensure_ascii=False) + "\n")
mode = os.environ.get("MOCK_MODE", "ok")
if mode == "hang":
    time.sleep(60)
if mode == "fail":
    print("mock engine: boom", file=sys.stderr)
    sys.exit(3)
if mode == "empty":
    sys.exit(0)

def after(flag):
    return argv[argv.index(flag) + 1] if flag in argv else None

if name == "cursor-agent":
    session = after("--resume") or "%(cursor)s"
elif name == "codex":
    session = argv[2] if argv[:2] == ["exec", "resume"] else "%(codex)s"
elif name == "kimi":
    session = after("-r") or "%(kimi)s"
else:
    session = after("--session-id") or after("-r")
state = os.path.join(os.environ["MOCK_STATE"], (session or "none").replace("/", "_"))
turn = (int(open(state).read()) if os.path.exists(state) else 0) + 1
open(state, "w").write(str(turn))
reply = "%%s turn %%d saw: %%s" %% (name, turn, argv[-1][:30])

if name == "cursor-agent":
    if mode == "no-json":
        print(reply)
    else:
        print(json.dumps({"type": "result", "subtype": "success", "is_error": mode == "is_error",
                          "result": reply, "session_id": session}))
elif name == "codex":
    print("OpenAI Codex v0.146.0 (research preview)\n--------\nsession id: " + session + "\n--------\n" + reply)
elif name == "kimi":
    print(reply + "\n\nTo resume this session: kimi -r " + session)
else:
    print(reply)
''' % {"cursor": CURSOR_SESSION, "codex": CODEX_SESSION, "kimi": KIMI_SESSION}

PRESET_SESSION = {"cursor": CURSOR_SESSION, "codex": CODEX_SESSION, "kimi": KIMI_SESSION, "grok": GROK_SESSION}


def render(preset, which, access="read", session=""):
    """argv пресета из эталона: раскрыты все токены, кроме {prompt}."""
    spec = PRESETS[preset]
    acc = GOLDEN["access"][access]
    out = []
    for tok in spec[which]:
        if tok == "{mode_flags}":
            out += acc["mode_flags_cursor"]
            continue
        tok = tok.replace("{model}", spec["defaults"].get("model", "")).replace("{effort}", spec["defaults"].get("effort", ""))
        tok = tok.replace("{sandbox}", acc["sandbox"]).replace("{session}", session)
        out.append(tok)
    return out


@unittest.skipUnless(shutil.which("setsid"), "нет setsid: run-engine.sh отцепляет движок через него")
class OldPresetsOnMockEngines(unittest.TestCase):
    def setUp(self):
        self.root = tmpdir(self)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for spec in PRESETS.values():
            executable(self.bin / spec["binary"], MOCK_ENGINE)
        (self.root / "state").mkdir()
        self.log = self.root / "mock.log"
        self.run_dir = self.root / "run"
        self.prompt = self.root / "p.md"
        self.prompt.write_text("remember the word PELICAN, then answer briefly\n", encoding="utf-8")

    def env(self, mode="ok"):
        return {**os.environ, "HOME": str(self.root), "PATH": f"{self.bin}:{os.environ['PATH']}",
                "MOCK_LOG": str(self.log), "MOCK_STATE": str(self.root / "state"), "MOCK_MODE": mode}

    def launch(self, preset, argv, role="second-reviewer-1", mode="ok", timeout=10, extra=()):
        launched = subprocess.run(
            ["bash", str(SCRIPTS / "run-engine.sh"), "--role", role, "--run-dir", str(self.run_dir), "--prompt",
             str(self.prompt), "--engine", preset, "--task", "3", "--timeout", str(timeout), *extra, "--", *argv],
            capture_output=True, text=True, timeout=60, env=self.env(mode))
        self.assertEqual(launched.returncode, 0, launched.stderr)
        info = dict(l.split("=", 1) for l in launched.stdout.splitlines() if l.startswith(("out=", "done=")))
        done = Path(info["done"])
        deadline = time.monotonic() + DONE_WAIT_SEC
        while not done.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertTrue(done.exists(), f".done не появился: {done}")
        fields = dict(l.split("=", 1) for l in done.read_text(encoding="utf-8").splitlines() if "=" in l)
        result = Path(fields["result"]).read_text(encoding="utf-8") if "result" in fields else ""
        return fields, result

    def last_call(self):
        return json.loads(self.log.read_text(encoding="utf-8").splitlines()[-1])

    def expect_prompt_in(self, argv):
        # run-engine.sh берёт текст через $(cat …): завершающие переводы строки теряются (так в 1.1.1)
        text = self.prompt.read_text(encoding="utf-8").rstrip("\n")
        return [text if a == "{prompt}" else a for a in argv]

    def session_txt(self, role="second-reviewer-1"):
        return (self.run_dir / "engine" / role / "session.txt").read_text(encoding="utf-8").strip()

    def start(self, preset, access="read", role="second-reviewer-1"):
        """Первый вызов роли; у grok сессию заводит вызывающий и отдаёт скрипту."""
        if preset == "grok":
            argv = render("grok", "cmd", access, session=GROK_SESSION)
            return self.launch("grok", argv, role=role, extra=("--session", GROK_SESSION))
        return self.launch(preset, render(preset, "cmd", access), role=role)

    def test_first_call_passes_the_golden_argv_and_extracts_session_and_reply(self):
        for preset in PRESETS:
            with self.subTest(preset=preset):
                fields, result = self.start(preset, role=f"{preset}-role")
                self.assertEqual(fields["status"], "done", fields)
                self.assertEqual(fields["session"], PRESET_SESSION[preset])
                self.assertEqual(self.session_txt(f"{preset}-role"), PRESET_SESSION[preset])
                self.assertIn(f"turn 1 saw: remember the word PELICAN", result)
                call = self.last_call()
                self.assertEqual(call["bin"], PRESETS[preset]["binary"])
                argv = render(preset, "cmd", session=PRESET_SESSION[preset] if preset == "grok" else "")
                self.assertEqual([PRESETS[preset]["binary"]] + call["argv"], self.expect_prompt_in(argv))

    def test_write_access_changes_only_the_mode_flags(self):
        for preset, spec in PRESETS.items():
            if "write" not in spec["access"]:
                continue
            with self.subTest(preset=preset):
                fields, _ = self.start(preset, access="write", role=f"{preset}-writer")
                self.assertEqual(fields["status"], "done", fields)
                argv = render(preset, "cmd", "write", session=PRESET_SESSION[preset] if preset == "grok" else "")
                self.assertEqual([spec["binary"]] + self.last_call()["argv"], self.expect_prompt_in(argv))

    def test_resume_goes_to_the_same_session_and_remembers(self):
        for preset in PRESETS:
            with self.subTest(preset=preset):
                role = f"{preset}-resume"
                self.start(preset, role=role)
                session = self.session_txt(role)
                # grok сессию не печатает: без --session у вызова в .done её нет (так в 1.1.1)
                extra = ("--session", session) if preset == "grok" else ()
                fields, result = self.launch(preset, render(preset, "resume", session=session), role=role, extra=extra)
                self.assertEqual(fields["status"], "done", fields)
                self.assertEqual(self.session_txt(role), session)
                if preset in ("cursor", "grok"):   # у cursor session_id в каждом JSON, у grok — от вызывающего
                    self.assertEqual(fields["session"], session)
                self.assertIn("turn 2", result, "resume должен прийти в ту же сессию мока")
                self.assertEqual([PRESETS[preset]["binary"]] + self.last_call()["argv"],
                                 self.expect_prompt_in(render(preset, "resume", session=session)))
                labels = sorted(p.name for p in (self.run_dir / "engine" / role).glob("*.out.done"))
                self.assertEqual(labels, ["001.out.done", "002.out.done"])

    def test_ledger_has_launch_and_done_with_engine_and_session(self):
        self.start("kimi", role="kimi-ledger")
        rows = [json.loads(l) for l in (self.run_dir / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["event"] for r in rows], ["launch", "done"])
        self.assertEqual({r["engine"] for r in rows}, {"kimi"})
        self.assertEqual(rows[1]["session"], KIMI_SESSION)
        self.assertEqual(rows[1]["exit"], 0)

    def test_report_is_copied_only_for_a_successful_call(self):
        fields, _ = self.launch("codex", render("codex", "cmd"), role="codex-report", extra=("--report", "r.md"))
        self.assertEqual(Path(fields["report"]).read_text(encoding="utf-8").count("turn 1"), 1)
        self.launch("codex", render("codex", "cmd"), role="codex-report-fail", mode="fail", extra=("--report", "r2.md"))
        self.assertFalse((self.run_dir / "reports" / "r2.md").exists())

    def test_failures_get_a_reason_and_a_failed_marker(self):
        for preset in PRESETS:
            for mode, reason in (("fail", "exit 3"), ("empty", "no reply in output")):
                with self.subTest(preset=preset, mode=mode):
                    argv = render(preset, "cmd", session=GROK_SESSION if preset == "grok" else "")
                    fields, _ = self.launch(preset, argv, role=f"{preset}-{mode}", mode=mode)
                    self.assertEqual((fields["status"], fields["reason"]), ("failed", reason))

    def test_cursor_specific_failures(self):
        for mode, reason in (("is_error", "engine reported is_error"), ("no-json", "no JSON reply (no session_id)")):
            with self.subTest(mode=mode):
                fields, _ = self.launch("cursor", render("cursor", "cmd"), role=f"cursor-{mode}", mode=mode)
                self.assertEqual((fields["status"], fields["reason"]), ("failed", reason))

    def test_a_hung_engine_is_cut_by_the_timeout(self):
        fields, _ = self.launch("cursor", render("cursor", "cmd"), role="cursor-hang", mode="hang", timeout=1)
        self.assertEqual((fields["status"], fields["reason"]), ("failed", "timeout 1s"))

    def test_the_golden_file_lists_exactly_the_four_old_presets(self):
        self.assertEqual(sorted(PRESETS), ["codex", "cursor", "grok", "kimi"])
        for spec in PRESETS.values():
            self.assertEqual(spec["cmd"][0], spec["binary"])
            self.assertEqual(spec["resume"][0], spec["binary"])
            self.assertEqual(spec["cmd"][-1], "{prompt}")
            self.assertEqual(spec["resume"][-1], "{prompt}")
            self.assertIn("{session}", spec["resume"])


if __name__ == "__main__":
    unittest.main()
