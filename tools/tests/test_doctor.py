"""supertick-doctor.py (121-567, F7): вывод п. 4.7 на трёх наборах окружения.

Наборы: Claude Code с прежним конфигом 1.1.x; Kimi с профилем от setup; голое окружение с битым конфигом
и без среды. CLI — заглушки в PATH, HOME временный (как в test_setup).

Запуск: python3 -m unittest discover -s tools/tests   (из корня репозитория)
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

from test_setup import NOLOGIN_STUB, OK_STUB, Env, answers_file

HERE = Path(__file__).resolve().parent
DOCTOR = HERE.parents[1] / "core" / "scripts" / "supertick-doctor.py"


def doctor(env, *args, extra=None):
    e = dict(env.env, **(extra or {}))
    return subprocess.run([sys.executable, str(DOCTOR), *args], capture_output=True, text=True, env=e, timeout=60)


class ClaudeCodeWithLegacyConfig(unittest.TestCase):
    def setUp(self):
        self.env = Env(self, claude=OK_STUB, **{"cursor-agent": OK_STUB})
        self.env.legacy.parent.mkdir(parents=True)
        self.env.legacy.write_text('{"enabled": true, "roles": {"coder": "cursor"}}', encoding="utf-8")
        self.root = self.env.home / "bundle"
        (self.root / ".claude-plugin").mkdir(parents=True)
        (self.root / ".claude-plugin" / "plugin.json").write_text('{"name": "supertick", "version": "2.0.0-alpha.1"}')
        self.extra = {"CLAUDECODE": "1", "SUPERTICK_ROOT": str(self.root)}

    def test_json(self):
        res = doctor(self.env, "--json", extra=self.extra)
        self.assertEqual(res.returncode, 0, res.stderr)
        r = json.loads(res.stdout)
        self.assertEqual((r["version"], r["root"]), ("2.0.0-alpha.1", str(self.root)))
        self.assertEqual((r["host"], r["level"], r["host_source"]), ("claude-code", "L3", "env"))
        self.assertEqual(r["config"]["used"], [str(self.env.legacy)])
        self.assertTrue(r["config"]["ok"])
        self.assertEqual(r["config"]["write_target"], str(self.env.config()))
        self.assertEqual(r["sources"], ["claude", "cursor-agent"])
        self.assertIsNone(r["profile"])
        self.assertEqual((r["step0"]["action"], r["step0"]["lines"]), ("none", []))
        self.assertFalse(r["probed"])

    def test_text(self):
        out = doctor(self.env, extra=self.extra).stdout
        for needle in ("supertick 2.0.0-alpha.1", "среда: claude-code (L3", "validate: в порядке",
                       "claude: установлен (проба не запускалась)", "grok: не установлен", "профиль: нет активного",
                       "шаг 0: none"):
            self.assertIn(needle, out)

    def test_doctor_writes_nothing(self):
        before = sorted(p for p in self.env.home.rglob("*"))
        doctor(self.env, extra=self.extra)
        self.assertEqual(sorted(p for p in self.env.home.rglob("*")), before)


class KimiWithProfile(unittest.TestCase):
    def setUp(self):
        self.env = Env(self, kimi=OK_STUB, codex=NOLOGIN_STUB)
        res = self.env.run("apply", "--no-probe", "--answers", answers_file(self.env, {
            "profile": "kimi-team", "lead": {"host": "kimi", "model": "kimi-code/k3"}}))
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_probe_profile_and_step0(self):
        res = doctor(self.env, "--host", "kimi", "--probe", "--json")
        self.assertEqual(res.returncode, 0, res.stderr)
        r = json.loads(res.stdout)
        self.assertEqual((r["host"], r["level"], r["host_source"]), ("kimi", "L2", "flag"))
        self.assertEqual(r["version"], "")
        states = {c["cli"]: c["state"] for c in r["clis"]}
        self.assertEqual((states["kimi"], states["codex"]), ("responds", "not_logged_in"))
        p = r["profile"]
        self.assertEqual((p["name"], p["lead"]["host"], p["lead_probe"]), ("kimi-team", "kimi", None))
        # профиль записан без пробы (обе CLI — источники), а сейчас codex без входа: отпечаток другой
        self.assertFalse(p["fingerprint_matches"])
        self.assertEqual(r["step0"]["action"], "line")

    def test_text_shows_profile_and_lead_probe(self):
        out = doctor(self.env, "--host", "kimi").stdout
        self.assertIn("профиль: kimi-team — ведущий kimi/kimi-code/k3", out)
        self.assertIn("проба ведущего: не запускалась", out)
        self.assertIn("набор CLI тот же", out)
        self.assertIn("версия неизвестна", out)

    def test_lead_probe_result_is_shown(self):
        cfg = json.loads(self.env.config().read_text(encoding="utf-8"))
        cfg["profiles"]["kimi-team"]["lead_probe"] = {"result": "passed", "date": "2026-11-12", "version": "2.0.0"}
        self.env.config().write_text(json.dumps(cfg), encoding="utf-8")
        self.assertIn("проба ведущего: passed 2026-11-12 2.0.0", doctor(self.env, "--host", "kimi").stdout)

    def test_lead_mismatch_shows_restart_command(self):
        out = doctor(self.env, "--host", "codex").stdout
        self.assertIn("шаг 0: ask_restart — kimi -m kimi-code/k3", out)


class BareAndBroken(unittest.TestCase):
    def setUp(self):
        self.env = Env(self)
        self.env.config().parent.mkdir(parents=True)
        self.env.config().write_text("{broken", encoding="utf-8")

    def test_broken_config_and_no_host(self):
        res = doctor(self.env, "--json")
        self.assertEqual(res.returncode, 1)
        r = json.loads(res.stdout)
        self.assertIsNone(r["host"])
        self.assertIsNone(r["step0"])
        self.assertEqual(r["config"]["broken"], [str(self.env.config())])
        self.assertEqual(r["sources"], [])
        self.assertTrue(all(c["state"] == "not_installed" for c in r["clis"]))

    def test_text(self):
        out = doctor(self.env).stdout
        self.assertIn("среда: не определена", out)
        self.assertIn("не читается (битый JSON)", out)
        self.assertNotIn("validate: в порядке", out)

    def test_validate_errors_fail_the_exit_code(self):
        self.env.config().write_text('{"fallback": "nowhere", "active_profile": "ghost"}', encoding="utf-8")
        res = doctor(self.env, "--host", "kimi")
        self.assertEqual(res.returncode, 1)
        self.assertIn("ОШИБКА: fallback", res.stdout)
        self.assertIn("профиль: ghost указан, но в profiles его нет", res.stdout)


if __name__ == "__main__":
    unittest.main()
