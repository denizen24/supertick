"""Язык ленты и отчётов (121-604): выбор ru | en и откуда он взят, профиль, диалог, doctor и строки ⚙️.

Цепочка: флаг → SUPERTICK_LANG → профиль → конфиг → язык запроса → локаль → en. Промпты ролей и метки
протокола от языка не зависят; здесь проверяются только тексты для человека.
"""
import json
import subprocess
import sys
import unittest

from test_setup import OK_STUB, Env, answers_file, ss

ec = ss.ec
SCRIPT = ss.SCRIPTS_DIR / "engine-config.py"
DOCTOR = ss.SCRIPTS_DIR / "supertick-doctor.py"
C_LOCALE = {"LANG": "C.UTF-8"}


def lang(cfg=None, **kw):
    kw.setdefault("env", C_LOCALE)
    return ec.resolve_language(cfg or {}, **kw)


class Resolve(unittest.TestCase):
    def test_chain_order(self):
        cfg = {"language": "ru", "active_profile": "p", "profiles": {"p": {"language": "en"}}}
        self.assertEqual(lang(cfg, flag="ru"), {"lang": "ru", "source": "flag", "warnings": []})
        self.assertEqual(lang(cfg, env={"SUPERTICK_LANG": "ru"})["source"], "env:SUPERTICK_LANG")
        self.assertEqual(lang(cfg, request="ru")["source"], "profile:p")          # профиль главнее запроса
        self.assertEqual(lang({"language": "ru"}, request="en")["source"], "config")
        self.assertEqual(lang({}, request="ru"), {"lang": "ru", "source": "request", "warnings": []})
        self.assertEqual(lang({}, env={"LANG": "ru_RU.UTF-8"})["source"], "locale")
        self.assertEqual(lang({}), {"lang": "en", "source": "default", "warnings": []})

    def test_locale_follows_posix_order_and_ignores_c(self):
        self.assertEqual(ec.locale_language({"LC_ALL": "en_US.UTF-8", "LANG": "ru_RU.UTF-8"}), "en")
        self.assertEqual(ec.locale_language({"LC_MESSAGES": "ru_RU", "LANG": "en_US"}), "ru")
        self.assertIsNone(ec.locale_language({"LANG": "C.UTF-8"}))
        self.assertIsNone(ec.locale_language({"LC_ALL": "C", "LANG": "ru_RU.UTF-8"}))   # LC_ALL=C решает: сведений нет
        self.assertIsNone(ec.locale_language({"LANG": "de_DE.UTF-8"}))

    def test_bad_flag_is_an_error_bad_config_is_skipped(self):
        with self.assertRaises(ValueError):
            lang(flag="de")
        got = lang({"language": "Deutsch"}, request="ru")
        self.assertEqual(got["lang"], "ru")
        self.assertEqual(len(got["warnings"]), 1)
        self.assertEqual(lang(env={"SUPERTICK_LANG": "xx", "LANG": "ru_RU"})["source"], "locale")

    def test_case_and_spaces_do_not_matter(self):
        self.assertEqual(lang(flag=" EN ")["lang"], "en")

    def test_validate_checks_language(self):
        errors, _ = ec.validate({"language": "de", "profiles": {"p": {"language": 1}}})
        self.assertEqual(sorted(e["path"] for e in errors), ["language", "profiles.p.language"])
        errors, warnings = ec.validate({"language": "en", "profiles": {"p": {"language": "ru"}}})
        self.assertEqual((errors, warnings), ([], []))   # ключ известен: не unknown_key


class Cli(unittest.TestCase):
    def run_ec(self, *args, env=None):
        e = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C.UTF-8", **(env or {})}
        return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=e, timeout=60)

    def test_lang_command(self):
        res = self.run_ec("lang", "--request-lang", "ru", "--json")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(json.loads(res.stdout)["lang"], "ru")
        self.assertEqual(self.run_ec("lang").stdout.strip(), "LANG: en (default)")
        res = self.run_ec("lang", "--lang", "fr")
        self.assertEqual(res.returncode, 2)

    def test_feed_lines_follow_the_language(self):
        w = {"code": "engine_missing", "role": "coder", "engine": "codex"}
        try:
            ec.OUTPUT_LANG = "en"
            self.assertEqual(ec.feed_line(w, "claude"), "⚙️ role coder: engine codex not found, role runs on Claude")
            ec.OUTPUT_LANG = "ru"
            self.assertEqual(ec.feed_line(w, "claude"), "⚙️ роль coder: движок codex не найден, роль на Claude")
        finally:
            ec.OUTPUT_LANG = "ru"
        self.assertEqual(set(ec.FEED), set(ec.FEED_EN), "у каждой строки ⚙️ есть английская пара")


class Setup(unittest.TestCase):
    def setUp(self):
        self.env = Env(self, kimi=OK_STUB)

    def test_language_answer_lands_in_the_profile(self):
        target = self.env.home / "cfg.json"
        res = self.env.run("apply", "--no-probe", "--answers",
                           answers_file(self.env, {"lead": {"host": "kimi"}, "language": "en"}),
                           extra_env={"SUPERTICK_CONFIG": str(target)})
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        cfg = json.loads(target.read_text())
        self.assertEqual(cfg["profiles"]["default"]["language"], "en")
        self.assertIn("язык ленты и отчётов: en", res.stdout)
        # дальше скрипты берут язык из профиля, если SUPERTICK_LANG не задан
        env = dict(self.env.env, SUPERTICK_CONFIG=str(target))
        env.pop("SUPERTICK_LANG")
        out = subprocess.run([sys.executable, str(SCRIPT), "lang", "--json"], capture_output=True, text=True,
                             env=env, timeout=60)
        self.assertEqual(json.loads(out.stdout)["source"], "profile:default")

    def test_no_language_answer_writes_no_key(self):
        target = self.env.home / "cfg.json"
        self.env.run("apply", "--no-probe", "--answers", answers_file(self.env, {"lead": {"host": "kimi"}}),
                     extra_env={"SUPERTICK_CONFIG": str(target)})
        self.assertNotIn("language", json.loads(target.read_text())["profiles"]["default"])

    def test_bad_language_answer_is_refused(self):
        res = self.env.run("plan", "--no-probe", "--answers",
                           answers_file(self.env, {"lead": {"host": "kimi"}, "language": "de"}))
        self.assertEqual(res.returncode, 2)
        self.assertIn("language", res.stderr)

    def test_english_output_on_request(self):
        res = self.env.run("discover", "--no-probe", "--lang", "en")
        self.assertIn("kimi: not checked", res.stdout)
        self.assertIn("sources: kimi", res.stdout)

    def test_dialog_asks_language_first_and_switches(self):
        d = Env(self, kimi=OK_STUB).discover()
        replies = iter(["2", "1", "", "1"])   # язык 2 (English при русском по умолчанию), ведущий, имя, раскладка
        asked = []

        def ask(text):
            asked.append(text)
            return next(replies)
        try:
            ec.OUTPUT_LANG = "ru"
            answers = ss.dialog(d, ask, lambda *_: None, "kimi")
        finally:
            ec.OUTPUT_LANG = "ru"
        self.assertTrue(asked[0].startswith("Язык ленты и отчётов / Feed and report language:"))
        self.assertIn("1. русский", asked[0])
        self.assertEqual(answers["language"], "en")
        self.assertTrue(asked[1].startswith("Who leads?"), asked[1])

    def test_dialog_suggests_the_resolved_language_first(self):
        d = Env(self, kimi=OK_STUB).discover()
        asked = []

        def ask(text):
            asked.append(text)
            return {0: "1", 1: "1", 2: "", 3: "1"}[len(asked) - 1]
        try:
            ec.OUTPUT_LANG = "en"
            answers = ss.dialog(d, ask, lambda *_: None, "kimi")
        finally:
            ec.OUTPUT_LANG = "ru"
        self.assertIn("1. English", asked[0])
        self.assertEqual(answers["language"], "en")


class Doctor(unittest.TestCase):
    def test_doctor_names_the_language_and_speaks_it(self):
        env = Env(self)
        base = dict(env.env)
        base.pop("SUPERTICK_LANG")
        en = subprocess.run([sys.executable, str(DOCTOR)], capture_output=True, text=True, env=base, timeout=60)
        self.assertIn("language: en (default)", en.stdout)
        self.assertIn("profile: none active", en.stdout)
        ru = subprocess.run([sys.executable, str(DOCTOR), "--lang", "ru", "--json"], capture_output=True, text=True,
                            env=base, timeout=60)
        self.assertEqual(json.loads(ru.stdout)["language"]["source"], "flag")
        bad = subprocess.run([sys.executable, str(DOCTOR), "--lang", "xx"], capture_output=True, text=True,
                             env=base, timeout=60)
        self.assertEqual(bad.returncode, 2)


if __name__ == "__main__":
    unittest.main()
