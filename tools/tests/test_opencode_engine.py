"""Пресет движка `opencode` (121-567, хвост F4 после спайка S0-OC, OpenCode 1.19.5).

- Разбор `opencode run --format json` в run-engine.sh — на образцах спайка `docs/spikes/fixtures/opencode/`
  (подставной бинарь `opencode` печатает образец): ответ — тексты последнего сообщения с текстом, сессия —
  `sessionID` ведущего (события субагентов в вывод не попадают), строка `type: error` — провал.
- argv пресета — как его печатает `engine-config.py argv`, ожидания записаны по живым проверкам 14.11.2026:
  `--` перед промптом (бриф начинается с `---`), агент чтения из своего конфига, `--agent` повторяется при
  продолжении (без него сессия идёт агентом build и пишет — проверено живым вызовом).
- Живые вызовы — только при SUPERTICK_LIVE=1 и на бесплатной модели: продолжение помнит слово, роль чтения
  не создаёт файл.

Запуск: python3 -m unittest discover -s tools/tests -p test_opencode_engine.py
        SUPERTICK_LIVE=1 python3 -m unittest discover -s tools/tests -p test_opencode_engine.py
"""
import json
import os
import shutil
import subprocess
import unittest

from test_engine_presets_f4 import CORE, ROOT, LIVE, ec, wait_done
from test_scripts import executable, tmpdir

FIXTURES = ROOT / "docs" / "spikes" / "fixtures" / "opencode"
FREE_MODEL = "opencode/deepseek-v4.1-flash-free"

FIXTURE_OPENCODE = r'''#!/usr/bin/env python3
# подставной opencode: печатает образец спайка; .txt — текст ошибки в stderr и код 1; error-model — код 1
import os, sys
path = os.environ["OPENCODE_FIXTURE"]
text = open(path, encoding="utf-8").read()
if path.endswith(".txt"):
    sys.stderr.write(text); sys.exit(1)
sys.stdout.write(text)
if "error-" in os.path.basename(path):
    sys.exit(1)
'''


@unittest.skipUnless(shutil.which("setsid"), "нет setsid")
class OpencodeJsonParsing(unittest.TestCase):
    def run_fixture(self, name):
        root = tmpdir(self)
        (root / "bin").mkdir()
        executable(root / "bin" / "opencode", FIXTURE_OPENCODE)
        prompt = root / "p.md"
        prompt.write_text("hi\n", encoding="utf-8")
        env = {**os.environ, "HOME": str(root), "PATH": f"{root / 'bin'}:{os.environ['PATH']}",
               "OPENCODE_FIXTURE": str(FIXTURES / name)}
        launched = subprocess.run(["bash", str(CORE / "run-engine.sh"), "--role", "r", "--run-dir", str(root / "run"),
                                   "--prompt", str(prompt), "--engine", "opencode", "--timeout", "10", "--",
                                   "opencode", "run", "--format", "json", "--", "{prompt}"],
                                  capture_output=True, text=True, timeout=60, env=env)
        return wait_done(self, launched)

    def test_reply_and_session_of_a_first_call(self):
        fields, result = self.run_fixture("ok-first.jsonl")
        self.assertEqual((fields["status"], fields["session"], result), ("done", "ses_e27cc8813ffe5iKWtOWmvLVThp", "ok\n"))

    def test_resumed_call_keeps_the_session_and_remembers(self):
        fields, result = self.run_fixture("ok-resume.jsonl")
        self.assertEqual((fields["session"], result), ("ses_e27cc8813ffe5iKWtOWmvLVThp", "PELICAN\n"))

    def test_the_reply_is_the_last_message_not_tool_output(self):
        _, result = self.run_fixture("write-default.jsonl")
        self.assertEqual(result, "done\n")
        _, result = self.run_fixture("lead-probe-glm.jsonl")
        self.assertEqual(result, "ГОТОВО\n")

    def test_session_is_the_lead_one_when_subagents_ran(self):
        fields, result = self.run_fixture("subagents.jsonl")
        self.assertEqual(fields["session"], "ses_e27c27eb3ffeYN3P0FwEpxviep")
        self.assertIn("ZEBRA", result)

    def test_errors_fail_with_the_exit_code(self):
        fields, _ = self.run_fixture("error-model.jsonl")
        self.assertEqual((fields["status"], fields["failure_kind"]), ("failed", "provider_error"))
        self.assertEqual(fields["session"], "ses_e27c1c4beffe4PibAxPjKgl1Cd", "сессия заведена и у провалившегося вызова")
        fields, _ = self.run_fixture("error-session.txt")
        self.assertEqual((fields["status"], fields["failure_kind"]), ("failed", "engine_exit"))


class OpencodePresetArgv(unittest.TestCase):
    CONFIG = f"OPENCODE_CONFIG={ec.ENGINES_DIR / 'opencode-supertick.json'}"

    def argv(self, role, resume=None, model=None):
        cfg = {"roles": {role: {"engine": "opencode", **({"model": model} if model else {})}}}
        return ec.build_argv(cfg, "kimi", role, None, "/run", "/p.md", resume=resume, available={"opencode"})["argv"]

    def test_read_role_gets_the_read_agent_from_our_config_and_dashes(self):
        self.assertEqual(self.argv("unified-reviewer"),
                         ["env", self.CONFIG, "opencode", "run", "--format", "json", "-m", FREE_MODEL,
                          "--agent", "supertick-read", "--", "{prompt}"],
                         "-- нужен: бриф начинается с ---, без него opencode печатает справку и выходит с кодом 1")

    def test_resume_repeats_the_agent_because_a_resumed_session_falls_back_to_build(self):
        self.assertEqual(self.argv("unified-reviewer", resume="ses_1"),
                         ["env", self.CONFIG, "opencode", "run", "--format", "json", "-m", FREE_MODEL,
                          "--agent", "supertick-read", "-s", "ses_1", "--", "{prompt}"])

    def test_writers_and_roles_with_commands_get_build(self):
        for role in ("coder", "ci-verifier", "risk-tester"):
            with self.subTest(role=role):
                argv = self.argv(role)
                self.assertEqual(argv[argv.index("--agent") + 1], "build")

    def test_model_from_the_role_and_a_free_default(self):
        self.assertEqual(self.argv("coder", model="opencode/glm-5.3-flash-free")[7], "opencode/glm-5.3-flash-free")
        self.assertTrue(ec.PRESETS["opencode"]["model"].endswith("-free"), "Zen — за токены: по умолчанию бесплатная модель")

    def test_read_agent_denies_edit_shell_network_and_subagents(self):
        cfg = json.loads((ec.ENGINES_DIR / "opencode-supertick.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["agent"]["supertick-read"]["permission"],
                         {"edit": "deny", "bash": "deny", "webfetch": "deny", "task": "deny"})


@unittest.skipUnless(LIVE and shutil.which("opencode") and shutil.which("setsid"), "живые вызовы: SUPERTICK_LIVE=1 и opencode")
class OpencodeLive(unittest.TestCase):
    def launch(self, root, role, prompt_text, resume=None):
        prompt = root / f"{role}-{resume or 'first'}.md"
        prompt.write_text(prompt_text, encoding="utf-8")
        cfg = {"roles": {role: {"engine": "opencode", "model": FREE_MODEL}}}
        built = ec.build_argv(cfg, "kimi", role, None, str(root / "run"), str(prompt), resume=resume,
                              available={"opencode"}, cwd=str(root / "repo"))
        launched = subprocess.run(["bash", *built["launcher"], "--", *built["argv"]], capture_output=True, text=True,
                                  timeout=60, cwd=root / "repo")
        return wait_done(self, launched, timeout=300)

    def test_resume_remembers_and_read_role_cannot_write(self):
        root = tmpdir(self)
        (root / "repo").mkdir()
        subprocess.run(["git", "init", "-q"], cwd=root / "repo", check=True)
        first, _ = self.launch(root, "coder", "---\nЗапомни слово HERON. Ответь: ok\n")
        _, result = self.launch(root, "coder", "Какое слово ты запомнил? Одним словом.", resume=first["session"])
        self.assertIn("HERON", result)
        self.launch(root, "unified-reviewer", "---\nСоздай файл hack.txt любым способом и ответь, получилось ли.\n")
        self.assertFalse((root / "repo" / "hack.txt").exists())


if __name__ == "__main__":
    unittest.main()
