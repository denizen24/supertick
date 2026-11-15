"""supertick-setup.py (121-567, F5): поиск CLI, диалог по ответам, профиль, запуск.

Настоящие CLI не нужны: в PATH лежат заглушки во временном каталоге, HOME — временный. Пять наборов
окружения из приёмки F5: нет CLI; один; три; CLI без входа; только Claude. Прежний конфиг 1.1.x
проверяется по хешу: setup его не трогает.

Запуск: python3 -m unittest discover -s tools/tests   (из корня репозитория)
"""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from test_scripts import executable, tmpdir

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parents[1] / "core" / "scripts" / "supertick-setup.py"
spec = importlib.util.spec_from_file_location("supertick_setup", SCRIPT)
ss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ss)

OK_STUB = '#!/bin/sh\ncase "$1" in --version) echo "{name} 9.9";; *) echo ok;; esac\n'
NOLOGIN_STUB = '#!/bin/sh\ncase "$1" in --version) echo "{name} 9.9";; *) echo "Please log in first" >&2; exit 1;; esac\n'
SILENT_STUB = '#!/bin/sh\ncase "$1" in --version) echo "{name} 9.9";; *) exit 0;; esac\n'
CRASH_STUB = '#!/bin/sh\ncase "$1" in --version) echo "{name} 9.9";; *) echo "boom" >&2; exit 3;; esac\n'


class Env:
    """Каталог HOME и каталог PATH с заглушками; в PATH только они плюс env и sh."""

    def __init__(self, case, **clis):
        self.home = tmpdir(case)
        self.bin = self.home / "bin"
        self.bin.mkdir()
        for tool in ("env", "sh"):
            (self.bin / tool).symlink_to(Path("/usr/bin") / tool if Path("/usr/bin", tool).exists() else Path("/bin") / tool)
        for name, stub in clis.items():
            executable(self.bin / name, stub.format(name=name))
        self.legacy = self.home / ".claude" / "agent-teams.json"
        # SUPERTICK_LANG=ru: тексты сверяются по-русски; по локали C.UTF-8 вывод был бы на английском (121-604)
        self.env = {"PATH": str(self.bin), "HOME": str(self.home), "LANG": "C.UTF-8", "SUPERTICK_LANG": "ru"}

    def run(self, *args, extra_env=None):
        env = dict(self.env, **(extra_env or {}))
        return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=60)

    def discover(self, *args):
        res = self.run("discover", "--json", *args)
        self.assert_ok = res.returncode == 0
        return json.loads(res.stdout)

    def config(self):
        return self.home / ".config" / "supertick" / "config.json"


def states(d):
    return {e["cli"]: e["state"] for e in d["clis"]}


class Discover(unittest.TestCase):
    def test_no_cli_at_all(self):
        d = Env(self).discover()
        self.assertEqual(set(states(d).values()), {"not_installed"})
        self.assertEqual(d["sources"], [])

    def test_one_cli(self):
        d = Env(self, kimi=OK_STUB).discover()
        self.assertEqual(states(d)["kimi"], "responds")
        self.assertEqual(d["sources"], ["kimi"])
        kimi = next(e for e in d["clis"] if e["cli"] == "kimi")
        self.assertEqual((kimi["version"], kimi["host"], kimi["lead_capable"]), ("kimi 9.9", "kimi", True))

    def test_three_clis(self):
        d = Env(self, kimi=OK_STUB, codex=OK_STUB, **{"cursor-agent": OK_STUB}).discover()
        self.assertEqual(d["sources"], ["codex", "cursor-agent", "kimi"])
        cursor = next(e for e in d["clis"] if e["cli"] == "cursor-agent")
        self.assertFalse(cursor["lead_capable"])
        self.assertEqual(cursor["engine"], "cursor")

    def test_cli_without_login_is_not_a_source(self):
        d = Env(self, kimi=OK_STUB, codex=NOLOGIN_STUB).discover()
        self.assertEqual(states(d)["codex"], "not_logged_in")
        self.assertEqual(d["sources"], ["kimi"])

    def test_crashing_cli_is_an_error_not_a_source(self):
        d = Env(self, grok=CRASH_STUB).discover()
        self.assertEqual(states(d)["grok"], "error")
        self.assertEqual(d["sources"], [])

    def test_silent_success_is_not_a_response(self):
        d = Env(self, kimi=SILENT_STUB).discover()
        self.assertEqual(states(d)["kimi"], "error")
        self.assertEqual(d["sources"], [])

    def test_only_claude(self):
        d = Env(self, claude=OK_STUB).discover()
        self.assertEqual(d["sources"], ["claude"])
        claude = next(e for e in d["clis"] if e["cli"] == "claude")
        self.assertEqual((claude["host"], claude["lead_capable"], claude["engine_preset"]), ("claude-code", True, True))

    def test_no_probe_trusts_installed_and_does_not_call(self):
        env = Env(self, codex=NOLOGIN_STUB)
        d = env.discover("--no-probe")
        self.assertEqual(states(d)["codex"], "unchecked")
        self.assertEqual(d["sources"], ["codex"])
        self.assertFalse(d["probed"])

    def test_fingerprint_follows_the_source_set(self):
        a = Env(self, kimi=OK_STUB).discover()
        b = Env(self, kimi=OK_STUB).discover()
        c = Env(self, kimi=OK_STUB, codex=OK_STUB).discover()
        self.assertEqual(a["fingerprint"], b["fingerprint"])
        self.assertNotEqual(a["fingerprint"], c["fingerprint"])

    def test_models_come_from_the_cli_own_config(self):
        env = Env(self, kimi=OK_STUB, codex=OK_STUB, grok=OK_STUB)
        (env.home / ".kimi-code").mkdir()
        (env.home / ".kimi-code" / "config.toml").write_text(
            'default_model = "kimi-code/k3.1-pro"\n[models."kimi-code/k3"]\nx=1\n[models."kimi-code/k3.1-pro"]\nx=1\n', encoding="utf-8")
        (env.home / ".codex").mkdir()
        (env.home / ".codex" / "models_cache.json").write_text(json.dumps({"models": [
            {"slug": "gpt-a", "visibility": "list"}, {"slug": "secret", "visibility": "hide"}]}), encoding="utf-8")
        got = {e["cli"]: e for e in env.discover()["clis"]}
        self.assertEqual(got["kimi"]["models"], ["kimi-code/k3.1-pro", "kimi-code/k3"])
        self.assertEqual(got["kimi"]["models_source"], "cli-config")
        self.assertEqual(got["codex"]["models"], ["gpt-a"])
        self.assertEqual(got["grok"]["models_source"], "catalog")
        self.assertIn("grok-4.6", got["grok"]["models"])

    def test_opencode_models_come_from_its_catalog_with_free_ones_first(self):
        stub = ('#!/bin/sh\ncase "$1" in --version) echo "1.19.5";; models) printf "opencode/claude-x\\n'
                'opencode/glm-free\\nanthropic/claude-y\\n\\nnot a model line\\n";; *) echo ok;; esac\n')
        got = {e["cli"]: e for e in Env(self, opencode=stub).discover("--no-probe")["clis"]}
        self.assertEqual(got["opencode"]["models"], ["opencode/glm-free", "opencode/claude-x", "anthropic/claude-y"])
        self.assertEqual(got["opencode"]["models_source"], "cli-config")
        broken = {e["cli"]: e for e in Env(self, opencode=CRASH_STUB).discover("--no-probe")["clis"]}
        self.assertEqual(broken["opencode"]["models_source"], "catalog")


def answers_file(env, data):
    path = env.home / "answers.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


KIMI_LEAD = {"host": "kimi", "model": "kimi-code/k3"}


class Apply(unittest.TestCase):
    def setUp(self):
        self.env = Env(self, kimi=OK_STUB, codex=OK_STUB, **{"cursor-agent": OK_STUB})

    def apply(self, data, *extra):
        return self.env.run("apply", "--answers", answers_file(self.env, data), "--json", *extra)

    def test_writes_profile_and_it_passes_engine_config(self):
        res = self.apply({"profile": "kimi-team", "lead": KIMI_LEAD, "layout": "split",
                          "split": {"coder": {"engine": "codex", "model": "gpt-5.6-sol"}, "reviewer": {"engine": "cursor"}},
                          "second_opinion": {"engine": "cursor", "model": "grok-4.7-xhigh-fast"}})
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        cfg = json.loads(self.env.config().read_text(encoding="utf-8"))
        prof = cfg["profiles"]["kimi-team"]
        self.assertEqual(cfg["active_profile"], "kimi-team")
        self.assertEqual(cfg["fallback"], "host")
        self.assertEqual(prof["lead"], KIMI_LEAD)
        self.assertEqual(prof["roles"]["coder"], {"engine": "codex", "model": "gpt-5.6-sol"})
        self.assertEqual(prof["roles"]["unified-reviewer"]["engine"], "cursor")
        self.assertTrue(prof["fingerprint"].startswith("sha256:"))
        self.assertEqual(prof["sources"], ["codex", "cursor-agent", "kimi"])
        self.assertEqual(prof["installed"], ["codex", "cursor-agent", "kimi"])
        engine_config = HERE.parents[1] / "core" / "scripts" / "engine-config.py"
        run = subprocess.run([sys.executable, str(engine_config), "validate", "--config", str(self.env.config()), "--json"],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout)
        run = subprocess.run([sys.executable, str(engine_config), "host", "--host", "kimi", "--model", "kimi-code/k3",
                              "--config", str(self.env.config()), "--json"], capture_output=True, text=True)
        self.assertTrue(json.loads(run.stdout)["lead_matches"])

    def test_table_matches_runtime_over_legacy_roles(self):
        """V1 (R2): «всё на модели ведущего» при ролях на cursor в прежнем конфиге — таблица диалога и шаг 0b
        прогона говорят одно и то же: все роли родные, cursor не всплывает."""
        self.env.legacy.parent.mkdir(parents=True)
        self.env.legacy.write_text('{"enabled": true, "roles": {"coder": "cursor", "unified-reviewer": "cursor", '
                                   '"second-reviewer": "cursor"}}\n', encoding="utf-8")
        res = self.apply({"lead": KIMI_LEAD, "layout": "all-on-lead"})
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        rows = {r["role"]: r["engine"] for r in json.loads(res.stdout)["rows"]}
        self.assertEqual(set(rows.values()), {"native"}, rows)
        engine_config = HERE.parents[1] / "core" / "scripts" / "engine-config.py"
        run = subprocess.run([sys.executable, str(engine_config), "resolve", "--host", "kimi", "--json",
                              "--available", "cursor-agent,kimi,codex"],
                             capture_output=True, text=True, env=self.env.env)
        table = json.loads(run.stdout)["table"]
        self.assertNotIn("cursor", table.values(), table)
        self.assertEqual(table["second-reviewer"], "none")

    def test_codex_null_roles_go_to_the_codex_engine(self):
        """На Codex «родной» — движок codex с моделью ведущего (H4), и null в профиле этого не ломает."""
        res = self.apply({"lead": {"host": "codex", "model": "gpt-5.6-sol"}, "layout": "all-on-lead"})
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        rows = {r["role"]: (r["engine"], r["model"]) for r in json.loads(res.stdout)["rows"]}
        self.assertEqual(rows["coder"], ("codex", "gpt-5.6-sol"))
        self.assertEqual(rows["unified-reviewer"], ("codex", "gpt-5.6-sol"))

    def test_agent_teams_json_is_never_touched(self):
        self.env.legacy.parent.mkdir(parents=True)
        self.env.legacy.write_text('{"enabled": true, "roles": {"coder": "cursor"}}\n', encoding="utf-8")
        before = hashlib.sha256(self.env.legacy.read_bytes()).hexdigest()
        self.assertEqual(self.apply({"lead": KIMI_LEAD}).returncode, 0)
        self.assertEqual(hashlib.sha256(self.env.legacy.read_bytes()).hexdigest(), before)
        self.assertTrue(self.env.config().is_file())

    def test_existing_config_is_merged_and_backed_up(self):
        cfg = self.env.config()
        cfg.parent.mkdir(parents=True)
        cfg.write_text(json.dumps({"enabled": True, "fallback": "fail", "roles": {"coder": "codex"},
                                   "profiles": {"old": {"lead": {"host": "codex"}}}}), encoding="utf-8")
        res = self.apply({"profile": "new", "lead": KIMI_LEAD})
        self.assertEqual(res.returncode, 0, res.stderr)
        got = json.loads(cfg.read_text(encoding="utf-8"))
        self.assertEqual(got["fallback"], "fail")
        self.assertEqual(got["roles"], {"coder": "codex"})
        self.assertIn("old", got["profiles"])
        self.assertIn("new", got["profiles"])
        backups = list(cfg.parent.glob("config.json.bak-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text(encoding="utf-8"))["fallback"], "fail")
        self.assertEqual(sorted(p.name for p in cfg.parent.iterdir()), sorted(["config.json", backups[0].name]))

    def test_supertick_config_env_redirects_the_write(self):
        target = self.env.home / "elsewhere.json"
        res = self.env.run("apply", "--answers", answers_file(self.env, {"lead": KIMI_LEAD}), "--json",
                           extra_env={"SUPERTICK_CONFIG": str(target)})
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(target.is_file())
        self.assertFalse(self.env.config().exists())

    def test_unconnected_engine_blocks_the_write(self):
        env = Env(self, kimi=OK_STUB, codex=NOLOGIN_STUB, grok=OK_STUB)
        res = env.run("apply", "--json", "--answers", answers_file(env, {
            "lead": KIMI_LEAD, "layout": "groups", "groups": {"coders": {"engine": "codex"}}}))
        self.assertEqual(res.returncode, 1)
        self.assertIn("codex не подключён", json.loads(res.stdout)["problems"][0])
        self.assertFalse(env.config().exists())

    def test_force_writes_despite_problems(self):
        env = Env(self, kimi=OK_STUB, codex=NOLOGIN_STUB, grok=OK_STUB)
        res = env.run("apply", "--json", "--force", "--answers", answers_file(env, {
            "lead": KIMI_LEAD, "layout": "groups", "groups": {"coders": {"engine": "codex"}}}))
        self.assertEqual(res.returncode, 0)
        self.assertTrue(env.config().is_file())

    def test_lead_that_is_not_installed_blocks(self):
        res = self.apply({"lead": {"host": "opencode", "model": "x/y"}})
        self.assertEqual(res.returncode, 1)
        self.assertTrue(any("не установлена" in p for p in json.loads(res.stdout)["problems"]))

    def test_lead_that_cannot_lead_is_rejected(self):
        res = self.apply({"lead": {"host": "nowhere"}})
        self.assertEqual(res.returncode, 2)
        self.assertFalse(self.env.config().exists())

    def test_split_needs_two_sources(self):
        env = Env(self, kimi=OK_STUB)
        res = env.run("apply", "--answers", answers_file(env, {
            "lead": KIMI_LEAD, "layout": "split", "split": {"coder": {"engine": "kimi"}}}))
        self.assertEqual(res.returncode, 2)
        self.assertIn("двух и более", res.stderr)

    def test_second_opinion_needs_two_sources(self):
        env = Env(self, kimi=OK_STUB)
        res = env.run("apply", "--answers", answers_file(env, {"lead": KIMI_LEAD, "second_opinion": {"engine": "kimi"}}))
        self.assertEqual(res.returncode, 2)

    def test_second_opinion_off_is_written_as_null(self):
        """V1: «выключено» — явный null, иначе второе мнение из прежнего конфига включилось бы само."""
        self.assertEqual(self.apply({"lead": KIMI_LEAD, "second_opinion": "off"}).returncode, 0)
        cfg = json.loads(self.env.config().read_text(encoding="utf-8"))
        self.assertIsNone(cfg["profiles"]["default"]["roles"]["second-reviewer"])

    def test_groups_expand_to_roles(self):
        res = self.apply({"lead": KIMI_LEAD, "layout": "groups", "groups": {
            "coders": {"engine": "codex"}, "architects": {"engine": "cursor"}, "verifiers": None}})
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        roles = json.loads(self.env.config().read_text(encoding="utf-8"))["profiles"]["default"]["roles"]
        self.assertEqual(roles["coder"]["engine"], "codex")
        self.assertEqual({r for r, s in roles.items() if s and s["engine"] == "cursor"}, set(ss.GROUPS["architects"]))
        self.assertIsNone(roles["spec-verifier"])   # группа без значения — родной исполнитель, записан явно

    def test_unknown_group_is_rejected(self):
        self.assertEqual(self.apply({"lead": KIMI_LEAD, "layout": "groups", "groups": {"bosses": None}}).returncode, 2)

    def test_broken_target_config_is_not_overwritten(self):
        self.env.config().parent.mkdir(parents=True)
        self.env.config().write_text("{not json", encoding="utf-8")
        res = self.apply({"lead": KIMI_LEAD})
        self.assertEqual(res.returncode, 2)
        self.assertEqual(self.env.config().read_text(encoding="utf-8"), "{not json")

    def test_plan_prints_the_table_and_writes_nothing(self):
        res = self.env.run("plan", "--answers", answers_file(self.env, {"lead": KIMI_LEAD, "layout": "groups",
                                                                       "groups": {"coders": {"engine": "codex", "model": "m1"}}}))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("coder", res.stdout)
        self.assertRegex(res.stdout, r"coder\s+codex\s+m1\s+запись")
        self.assertRegex(res.stdout, r"unified-reviewer\s+native\s+—\s+чтение")
        self.assertFalse(self.env.config().exists())

    def test_apply_does_not_change_the_probe_set(self):
        d1 = self.env.discover()
        self.apply({"lead": KIMI_LEAD})
        self.assertEqual(self.env.discover()["fingerprint"], d1["fingerprint"])


class Launch(unittest.TestCase):
    def test_launch_prints_the_lead_command(self):
        env = Env(self, kimi=OK_STUB)
        self.assertEqual(env.run("apply", "--answers", answers_file(env, {"profile": "p", "lead": KIMI_LEAD})).returncode, 0)
        res = env.run("launch", "p", "--json")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(json.loads(res.stdout)["command"], "kimi -m kimi-code/k3")

    def test_launch_without_model_has_no_dangling_flag(self):
        # ведущий Codex — без песочницы (H4): иначе исполнители `codex exec` из его shell не стартуют
        self.assertEqual(ss.launch_command({"host": "codex"}), "codex --sandbox danger-full-access")
        self.assertEqual(ss.launch_command({"host": "codex", "model": "gpt-5.6-sol"}),
                         "codex -m gpt-5.6-sol --sandbox danger-full-access")
        self.assertEqual(ss.launch_command({"host": "claude-code", "model": "opus"}), "claude --model opus")

    def test_launch_unknown_profile(self):
        res = Env(self).run("launch", "ghost")
        self.assertEqual(res.returncode, 2)


class Dialog(unittest.TestCase):
    def setUp(self):
        self.d = Env(self, kimi=OK_STUB, codex=OK_STUB, **{"cursor-agent": OK_STUB}).discover()

    def run_dialog(self, replies, current=None):
        ss.ec.OUTPUT_LANG = "ru"   # «1» в вопросе о языке — предложенный язык; предлагаем русский (121-604)
        it = iter(replies)
        asked = []

        def ask(prompt):
            asked.append(prompt)
            return next(it)
        return ss.dialog(self.d, ask, lambda *_: None, current), asked

    def test_split_dialog_builds_answers_that_plan_accepts(self):
        # язык 1 (русский), ведущий 1 (codex первым в списке по порядку CLIS: kimi), имя, раскладка 2, кодер 1, рецензент 2,
        # второе мнение 3
        answers, asked = self.run_dialog(["1", "1", "team", "2", "1", "2", "3"], current="kimi")
        self.assertEqual(answers["lead"]["host"], "kimi")
        self.assertEqual(answers["layout"], "split")
        self.assertEqual(answers["profile"], "team")
        self.assertEqual(answers["language"], "ru")
        self.assertIn("текущий сеанс", asked[1])
        plan = ss.build_plan(answers, self.d, {})
        self.assertEqual(plan["problems"], [])

    def test_questions_have_at_most_four_options(self):
        _, asked = self.run_dialog(["1", "1", "", "1", "1"])
        for text in asked:
            self.assertLessEqual(sum(1 for line in text.splitlines() if line.strip()[:2].rstrip(".").isdigit() and line.strip()[0] != "0"), 4)

    def test_engine_question_offers_four_options_and_no_more(self):
        self.d = Env(self, kimi=OK_STUB, codex=OK_STUB, claude=OK_STUB, grok=OK_STUB, **{"cursor-agent": OK_STUB}).discover()
        _, asked = self.run_dialog(["1", "1", "", "2", "1", "1", "1"])
        engine_q = next(a for a in asked if a.startswith("Кто пишет код"))
        numbered = [l for l in engine_q.splitlines() if l.strip()[:1].isdigit() and not l.strip().startswith("0.")]
        self.assertEqual(len(numbered), 4)

    def test_single_source_skips_split_and_second_opinion(self):
        self.d = Env(self, kimi=OK_STUB).discover()
        answers, asked = self.run_dialog(["1", "1", "", "1"])
        self.assertEqual(answers["layout"], "all-on-lead")
        self.assertNotIn("second_opinion", answers)
        self.assertEqual(len(asked), 4)
        self.assertNotIn("разных моделях", asked[3])

    def test_no_lead_capable_cli_stops_with_a_hint(self):
        self.d = Env(self, grok=OK_STUB).discover()
        with self.assertRaises(ss.SetupError):
            self.run_dialog(["1"])

    def test_bad_answer_is_an_error_not_a_guess(self):
        with self.assertRaises(ss.SetupError):
            self.run_dialog(["1", "9"])

    def test_setup_without_terminal_refuses_and_points_to_apply(self):
        res = Env(self, kimi=OK_STUB).run("setup")
        self.assertEqual(res.returncode, 2)
        self.assertIn("apply --answers", res.stderr)


if __name__ == "__main__":
    unittest.main()
