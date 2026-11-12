"""Новые пресеты и мок-движок (121-567, F4): kimi под Kimi Code CLI 2.x, claude, mock.

- Разбор вывода Kimi 2.x — на фикстурах спайка `docs/spikes/fixtures/kimi/` через run-engine.sh
  (подставной бинарь `kimi` печатает фикстуру).
- argv новых пресетов — как их печатает `engine-config.py argv`, ожидания записаны здесь руками
  по живым проверкам 12.11.2026.
- Мок-движок проходит весь путь «engine-config argv → run-engine.sh → .done» во всех режимах.
- Живые вызовы настоящих CLI — только при SUPERTICK_LIVE=1 (в CI их нет, ТЗ п. 7): ответ, продолжение
  помнит слово, режим чтения не меняет файлы, дочерний Claude не видит навыков supertick.

Запуск: python3 -m unittest discover -s tools/tests -p test_engine_presets_f4.py
        SUPERTICK_LIVE=1 python3 -m unittest discover -s tools/tests -p test_engine_presets_f4.py
"""
import importlib.util
import json
import os
import shutil
import subprocess
import time
import unittest
from pathlib import Path

from test_scripts import executable, tmpdir

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FIXTURES = ROOT / "docs" / "spikes" / "fixtures" / "kimi"
CORE = ROOT / "core" / "scripts"
spec = importlib.util.spec_from_file_location("engine_config_f4", CORE / "engine-config.py")
ec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ec)
LIVE = os.environ.get("SUPERTICK_LIVE") == "1"
DONE_WAIT_SEC = 30

FIXTURE_KIMI = r'''#!/usr/bin/env python3
# подставной kimi: печатает фикстуру спайка; у .txt — stdout, затем stderr и код выхода 1
import os, sys
text = open(os.environ["KIMI_FIXTURE"], encoding="utf-8").read()
if "--- stderr (exit 1):" in text:
    out, err = text.split("--- stderr (exit 1):", 1)
    sys.stdout.write(out); sys.stderr.write(err.lstrip("\n")); sys.exit(1)
sys.stdout.write(text)
'''


def wait_done(case, launched, timeout=DONE_WAIT_SEC):
    case.assertEqual(launched.returncode, 0, launched.stderr)
    info = dict(l.split("=", 1) for l in launched.stdout.splitlines() if l.startswith(("out=", "done=")))
    done = Path(info["done"])
    deadline = time.monotonic() + timeout
    while not done.exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    case.assertTrue(done.exists(), f".done не появился: {done}")
    fields = dict(l.split("=", 1) for l in done.read_text(encoding="utf-8").splitlines() if "=" in l)
    result = Path(fields["result"]).read_text(encoding="utf-8") if "result" in fields else ""
    return fields, result


@unittest.skipUnless(shutil.which("setsid"), "нет setsid")
class KimiStreamJsonParsing(unittest.TestCase):
    """run-engine.sh достаёт ответ и сессию из JSONL Kimi 2.x."""

    def run_fixture(self, name):
        root = tmpdir(self)
        (root / "bin").mkdir()
        executable(root / "bin" / "kimi", FIXTURE_KIMI)
        prompt = root / "p.md"
        prompt.write_text("hi\n", encoding="utf-8")
        env = {**os.environ, "HOME": str(root), "PATH": f"{root / 'bin'}:{os.environ['PATH']}", "KIMI_FIXTURE": str(FIXTURES / name)}
        # исходник из core/: совпадение сборки с ним держит test_build_host
        launched = subprocess.run(["bash", str(CORE / "run-engine.sh"), "--role", "r", "--run-dir", str(root / "run"),
                                   "--prompt", str(prompt), "--engine", "kimi", "--timeout", "10", "--", "kimi", "-p", "{prompt}"],
                                  capture_output=True, text=True, timeout=60, env=env)
        return wait_done(self, launched)

    def test_reply_and_session_of_a_resumed_call(self):
        fields, result = self.run_fixture("resume-ok.jsonl")
        self.assertEqual(fields["status"], "done")
        self.assertEqual(fields["session"], "session_ef497a8e-1ab5-4d06-b5ea-16ce15e1a48d")
        self.assertEqual(result, "АРБУЗ\n")

    def test_the_reply_is_the_last_assistant_text_not_tool_calls_or_tool_output(self):
        fields, result = self.run_fixture("write-and-shell-ok.jsonl")
        self.assertEqual(fields["session"], "session_2bd45a6b-d8a5-4dd8-85c2-608438dd1275")
        self.assertTrue(result.startswith("1) Файл `spike-write.txt` создан"), result[:80])
        self.assertNotIn("tool_call_id", result)

    def test_read_only_profile_output(self):
        _, result = self.run_fixture("agent-file-readonly.jsonl")
        self.assertTrue(result.startswith("**Результаты:**"), result[:80])

    def test_errors_fail_with_the_exit_code(self):
        for name in ("error-model.txt", "error-sess.txt"):
            with self.subTest(fixture=name):
                fields, _ = self.run_fixture(name)
                self.assertEqual((fields["status"], fields["reason"]), ("failed", "exit 1"))


class NewPresetsArgv(unittest.TestCase):
    def argv(self, engine, role, resume=None, model=None, cfg_extra=None, host="kimi"):
        roles = {role: {"engine": engine, **({"model": model} if model else {})}}
        cfg = {"roles": roles, **(cfg_extra or {})}
        return ec.build_argv(cfg, host, role, None, "/run", "/p.md", resume=resume,
                             available={"kimi", "claude", "cursor-agent", "codex"})

    def test_kimi_read_uses_the_read_only_profile_and_resume_drops_it(self):
        first = self.argv("kimi", "unified-reviewer")
        profile = str(ec.ENGINES_DIR / "kimi-read.md")
        self.assertEqual(first["argv"], ["kimi", "-m", "kimi-code/k3", "--output-format", "stream-json",
                                         "--agent-file", profile, "-p", "{prompt}"])
        self.assertTrue(Path(profile).is_file(), "профиль чтения лежит в наборе")
        again = self.argv("kimi", "unified-reviewer", resume="session_x")
        self.assertEqual(again["argv"], ["kimi", "-S", "session_x", "-m", "kimi-code/k3", "--output-format", "stream-json",
                                         "-p", "{prompt}"], "--agent-file с -S Kimi не принимает: профиль восстанавливается сам")

    def test_kimi_write_is_the_default_mode_and_a_coder_may_go_there(self):
        self.assertEqual(self.argv("kimi", "coder")["argv"],
                         ["kimi", "-m", "kimi-code/k3", "--output-format", "stream-json", "-p", "{prompt}"])
        self.assertEqual(ec.validate({"roles": {"coder": "kimi"}})[0], [])

    def test_kimi_role_with_commands_takes_write_because_the_read_profile_has_no_shell(self):
        built = self.argv("kimi", "spec-verifier")
        self.assertEqual(built["access"], "write")
        self.assertNotIn("--agent-file", built["argv"])

    def test_claude_is_isolated_and_maps_access_to_tools(self):
        iso = ["--disable-slash-commands", "--strict-mcp-config", "--setting-sources", "project,local"]
        head = ["env", "-u", "CLAUDECODE", "claude", "-p", "--output-format", "json", "--model", "haiku"] + iso
        read = self.argv("claude", "unified-reviewer", model="haiku")
        self.assertEqual(read["argv"], head + ["--tools", "Read,Grep,Glob", "--", "{prompt}"])
        write = self.argv("claude", "coder", model="haiku")
        self.assertEqual(write["argv"], head + ["--permission-mode", "acceptEdits", "--allowedTools",
                                                "Bash,Edit,Write,Read,Grep,Glob", "--", "{prompt}"])
        again = self.argv("claude", "unified-reviewer", resume="sid-1", model="haiku")
        self.assertEqual(again["argv"], head + ["--tools", "Read,Grep,Glob", "--resume", "sid-1", "--", "{prompt}"])

    def test_claude_on_claude_code_is_the_native_executor_not_a_process(self):
        self.assertIsNone(self.argv("claude", "unified-reviewer", host="claude-code"))

    def test_claude_on_another_host_needs_the_binary(self):
        res = ec.resolve({"roles": {"unified-reviewer": "claude"}}, "kimi", set())
        self.assertEqual(res["table"]["unified-reviewer"], "native")
        self.assertEqual(res["warnings"], ["engine_missing:unified-reviewer:claude"])
        built = ec.build_argv({"roles": {"unified-reviewer": "claude"}}, "kimi", "unified-reviewer", None, "/r", "/p.md",
                              available={"claude"})
        self.assertEqual(built["argv"][3], "claude")

    def test_mock_is_always_available_and_lives_next_to_the_scripts(self):
        built = ec.build_argv({"roles": {"coder": "mock"}}, "kimi", "coder", None, "/r", "/p.md", available=set())
        self.assertEqual(built["argv"], [str(ec.SCRIPTS_DIR / "mock-engine.py"), "--model", "mock-1", "-p", "{prompt}"])
        read = ec.build_argv({"roles": {"unified-reviewer": "mock"}}, "kimi", "unified-reviewer", None, "/r", "/p.md", available=set())
        self.assertIn("--read-only", read["argv"])


@unittest.skipUnless(shutil.which("setsid"), "нет setsid")
class MockEngineEndToEnd(unittest.TestCase):
    """engine-config argv → run-engine.sh → mock-engine.py → .done, все режимы мока."""

    def setUp(self):
        self.root = tmpdir(self)
        self.prompt = self.root / "p.md"
        self.prompt.write_text("---\nremember the word PELICAN\n", encoding="utf-8")

    def launch(self, role="coder", mode="ok", resume=None, timeout=None):
        built = ec.build_argv({"roles": {role: "mock"}}, "kimi", role, None, str(self.root / "run"), str(self.prompt),
                              resume=resume, timeout=timeout, available=set())
        env = {**os.environ, "HOME": str(self.root), "SUPERTICK_MOCK_MODE": mode, "SUPERTICK_MOCK_STATE": str(self.root / "mock")}
        launched = subprocess.run(["bash", *built["launcher"], "--", *built["argv"]], capture_output=True, text=True, timeout=60, env=env)
        return wait_done(self, launched)

    def test_ok_gives_a_reply_and_a_session_and_resume_remembers(self):
        fields, result = self.launch()
        self.assertEqual(fields["status"], "done")
        self.assertIn("mock turn 1 (write, mock-1): remember the word PELICAN", result)
        again, result2 = self.launch(resume=fields["session"])
        self.assertEqual(again["session"], fields["session"])
        self.assertIn("mock turn 2", result2)
        self.assertIn("remembered: remember the word PELICAN", result2)

    def test_read_access_reaches_the_mock(self):
        _, result = self.launch(role="unified-reviewer")
        self.assertIn("(read, mock-1)", result)

    def test_failure_modes(self):
        for mode, status, reason in (("fail", "failed", "exit 3"), ("is_error", "failed", "engine reported is_error"),
                                     ("no-session", "done", None)):
            with self.subTest(mode=mode):
                fields, _ = self.launch(mode=mode)
                self.assertEqual(fields["status"], status)
                self.assertEqual(fields.get("reason"), reason)
                if mode == "no-session":
                    self.assertEqual(fields["session"], "")

    def test_hang_is_cut_by_the_timeout(self):
        fields, _ = self.launch(mode="hang", timeout=1)
        self.assertEqual((fields["status"], fields["reason"]), ("failed", "timeout 1s"))


@unittest.skipUnless(LIVE, "живые вызовы — только при SUPERTICK_LIVE=1")
class LiveEngines(unittest.TestCase):
    """Настоящие CLI: ответ, продолжение помнит слово, чтение не пишет, дочерний Claude без навыков supertick."""

    MODELS = {"kimi": "kimi-code/kimi-for-coding-highspeed", "claude": "haiku"}

    def setUp(self):
        self.work = tmpdir(self)
        subprocess.run(["git", "init", "-q", str(self.work)], check=True)
        (self.work / "README.md").write_text("x\n", encoding="utf-8")

    def call(self, engine, role, text, resume=None, name=None):
        if not shutil.which({"kimi": "kimi", "claude": "claude"}[engine]) and engine == "claude":
            self.skipTest("нет claude")
        prompt = self.work / f"p-{time.monotonic_ns()}.md"
        prompt.write_text(text, encoding="utf-8")
        built = ec.build_argv({"roles": {role: {"engine": engine, "model": self.MODELS[engine]}}}, "kimi", role, name,
                              str(self.work / ".supertick" / "run"), str(prompt), resume=resume, timeout=300,
                              available={"kimi", "claude"})
        env = {**os.environ, "PATH": f"{Path.home() / '.kimi-code' / 'bin'}:{os.environ['PATH']}"}
        launched = subprocess.run(["bash", *built["launcher"], "--", *built["argv"]], capture_output=True, text=True,
                                  timeout=60, env=env, cwd=self.work)
        return wait_done(self, launched, timeout=330)

    def check_engine(self, engine):
        first, reply = self.call(engine, "coder", "Remember the code word ARBUZ. Reply with exactly: OK", name=f"{engine}-w")
        self.assertEqual(first["status"], "done", first)
        self.assertTrue(first["session"])
        again, reply2 = self.call(engine, "coder", "Which code word did I ask you to remember? One word.",
                                  resume=first["session"], name=f"{engine}-w")
        self.assertEqual(again["status"], "done", again)
        self.assertIn("ARBUZ", reply2.upper())
        ro, ro_reply = self.call(engine, "unified-reviewer", "Create file ro.txt with text X. Reply DONE or CANNOT.", name=f"{engine}-r")
        self.assertEqual(ro["status"], "done", ro)
        self.assertFalse((self.work / "ro.txt").exists(), f"{engine} в режиме чтения создал файл: {ro_reply}")

    def test_kimi(self):
        self.check_engine("kimi")

    def test_claude(self):
        self.check_engine("claude")
        _, reply = self.call("claude", "unified-reviewer",
                             "Look at the list of skills available to you. Reply with a comma-separated list of every skill "
                             "name that contains the word supertick, or exactly NONE.", name="claude-skills")
        self.assertNotIn("supertick:", reply)


if __name__ == "__main__":
    unittest.main()
