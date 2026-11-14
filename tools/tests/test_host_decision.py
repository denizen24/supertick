"""engine-config.py host (121-567, F6): когда спрашивать — восемь случаев п. 4.4 ТЗ по одному.

Решение принимает `host_decision` по профилю, среде сеанса и найденным CLI; через командную строку проверяется
то же на конфиге-файле. Claude Code без настроек не получает ни вопроса, ни строки (случай 1).

Запуск: python3 -m unittest discover -s tools/tests   (из корня репозитория)
"""
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

from test_scripts import tmpdir

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parents[1] / "core" / "scripts" / "engine-config.py"
spec = importlib.util.spec_from_file_location("engine_config", SCRIPT)
ec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ec)

PROFILE = {"lead": {"host": "kimi", "model": "kimi-code/k3"}, "roles": {}, "sources": ["codex", "kimi"],
           "installed": ["codex", "kimi"]}
BOTH = {"codex", "kimi"}


def decide(host="kimi", profile=PROFILE, available=BOTH, model="kimi-code/k3", **kw):
    return ec.host_decision({}, host, "team" if profile else None, profile, model, set(available), **kw)


class WhenToAsk(unittest.TestCase):
    def test_1_claude_code_without_profile_is_silent(self):
        got = decide(host="claude-code", profile=None, available={"claude", "kimi", "codex"})
        self.assertEqual((got["action"], got["lines"]), ("none", []))

    def test_1b_claude_code_without_profile_is_silent_in_non_interactive_session_too(self):
        got = decide(host="claude-code", profile=None, interactive=False)
        self.assertEqual((got["action"], got["lines"]), ("none", []))

    def test_2_other_host_without_profile_runs_the_dialog_once(self):
        got = decide(host="kimi", profile=None)
        self.assertEqual(got["action"], "setup")
        self.assertIn("supertick-setup.py", got["command"])

    def test_3_same_sources_and_lead_gives_one_profile_line(self):
        got = decide()
        self.assertEqual(got["action"], "line")
        self.assertEqual(got["lines"], ["⚙️ профиль team: ведущий kimi/kimi-code/k3"])

    def test_4_new_cli_is_one_line_without_a_question(self):
        got = decide(available=BOTH | {"opencode"})
        self.assertEqual(got["action"], "line")
        self.assertEqual(got["new"], ["opencode"])
        self.assertIn("⚙️ появился opencode, запустите setup, чтобы включить", got["lines"])
        self.assertEqual(len(got["lines"]), 2)

    def test_4b_installed_but_not_logged_in_at_setup_time_is_not_new(self):
        prof = dict(PROFILE, sources=["kimi"], installed=["codex", "kimi"])
        got = decide(profile=prof)
        self.assertEqual(got["new"], [])
        self.assertEqual(len(got["lines"]), 1)

    def test_4c_foreign_binaries_are_not_clis(self):
        got = decide(available=BOTH | {"mock-engine.py", "git"})
        self.assertEqual(got["new"], [])

    def test_5_vanished_source_is_reported_and_left_to_resolve(self):
        got = decide(available={"kimi"})
        self.assertEqual(got["action"], "line")
        self.assertEqual(got["vanished"], ["codex"])
        self.assertEqual(len(got["lines"]), 1)

    def test_6_lead_mismatch_asks_once_with_the_launch_command(self):
        got = decide(host="codex")
        self.assertEqual(got["action"], "ask_restart")
        self.assertEqual(got["launch"], "kimi -m kimi-code/k3")

    def test_6b_same_host_other_model_is_a_mismatch_but_unknown_model_is_not(self):
        self.assertEqual(decide(model="kimi-code/k3.1-pro")["action"], "ask_restart")
        self.assertEqual(decide(model=None)["action"], "line")

    def test_6c_mismatch_in_a_non_interactive_session_does_not_ask(self):
        got = decide(host="codex", interactive=False)
        self.assertEqual(got["action"], "line")
        self.assertIn("не совпал с сессией", got["lines"][0])

    def test_7_non_interactive_session_without_profile_stops_with_a_command(self):
        got = decide(host="codex", profile=None, interactive=False)
        self.assertEqual(got["action"], "stop")
        self.assertIn("supertick-setup.py", got["command"])

    def test_8_setup_flag_runs_the_dialog_always(self):
        for profile, host in ((None, "claude-code"), (PROFILE, "kimi"), (PROFILE, "codex")):
            self.assertEqual(decide(host=host, profile=profile, setup_flag=True)["action"], "setup")

    def test_profile_written_by_hand_without_sources_skips_set_checks(self):
        prof = {"lead": {"host": "kimi"}, "roles": {}}
        got = decide(profile=prof, available={"kimi", "codex", "opencode"})
        self.assertEqual((got["new"], got["vanished"]), ([], []))
        self.assertEqual(got["lines"], ["⚙️ профиль team: ведущий kimi"])

    def test_claude_code_with_profile_follows_the_same_rules(self):
        prof = {"lead": {"host": "claude-code", "model": "opus"}, "roles": {}, "sources": ["claude"], "installed": ["claude"]}
        got = decide(host="claude-code", profile=prof, available={"claude"}, model="opus")
        self.assertEqual((got["action"], len(got["lines"])), ("line", 1))


class ThroughTheCommandLine(unittest.TestCase):
    def run_host(self, cfg, *args):
        path = tmpdir(self) / "c.json"
        if cfg is not None:
            path.write_text(json.dumps(cfg), encoding="utf-8")
        res = subprocess.run([sys.executable, str(SCRIPT), "host", "--json", "--config", str(path), *args],
                             capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        return json.loads(res.stdout)

    def test_legacy_config_in_claude_code_adds_nothing(self):
        got = self.run_host({"enabled": True, "roles": {"coder": "cursor"}}, "--host", "claude-code",
                            "--available", "claude,cursor-agent")
        self.assertEqual((got["action"], got["lines"], got["lead_matches"]), ("none", [], None))

    def test_profile_and_session_through_the_cli(self):
        cfg = {"active_profile": "team", "profiles": {"team": PROFILE}}
        got = self.run_host(cfg, "--host", "kimi", "--model", "kimi-code/k3", "--available", "kimi,codex,grok")
        self.assertEqual((got["action"], got["new"], got["lead_matches"]), ("line", ["grok"], True))
        got = self.run_host(cfg, "--host", "codex", "--available", "kimi,codex")
        self.assertEqual((got["action"], got["launch"]), ("ask_restart", "kimi -m kimi-code/k3"))
        got = self.run_host(cfg, "--host", "kimi", "--available", "kimi,codex", "--setup")
        self.assertEqual(got["action"], "setup")
        got = self.run_host(None, "--host", "codex", "--available", "codex", "--non-interactive")
        self.assertEqual(got["action"], "stop")

    def test_a_cli_without_an_engine_preset_is_found_like_setup_finds_it(self):
        """R6: у opencode нет пресета движка, и шаг 0 считал его исчезнувшим в каждом прогоне."""
        root = tmpdir(self)
        (root / "bin").mkdir()
        for cli in ("kimi", "opencode"):
            stub = root / "bin" / cli
            stub.write_text("#!/bin/sh\n", encoding="utf-8")
            stub.chmod(0o755)
        self.assertNotIn("opencode", ec.PRESETS, "у opencode появился пресет — тест больше ничего не проверяет")
        prof = {**PROFILE, "sources": ["kimi", "opencode"], "installed": ["kimi", "opencode"]}
        path = root / "c.json"
        path.write_text(json.dumps({"active_profile": "team", "profiles": {"team": prof}}), encoding="utf-8")
        res = subprocess.run([sys.executable, str(SCRIPT), "host", "--json", "--config", str(path), "--host", "kimi"],
                             capture_output=True, text=True, env={"PATH": str(root / 'bin'), "HOME": str(root)})
        self.assertEqual(res.returncode, 0, res.stderr)
        got = json.loads(res.stdout)
        self.assertEqual((got["vanished"], got["new"]), ([], []))

    def test_profile_with_new_keys_passes_validate(self):
        path = tmpdir(self) / "c.json"
        path.write_text(json.dumps({"active_profile": "team", "profiles": {"team": PROFILE}}), encoding="utf-8")
        res = subprocess.run([sys.executable, str(SCRIPT), "validate", "--json", "--config", str(path)], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stdout)
        self.assertEqual(json.loads(res.stdout)["warnings"], [])


if __name__ == "__main__":
    unittest.main()
