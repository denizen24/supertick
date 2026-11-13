"""Проба пригодности ведущего (121-567, H2, п. 4.5 ТЗ): core/scripts/lead-probe.py и `supertick-setup.py lead-probe`.

Приёмка H2: на рабочем ведущем проба проходит; на намеренно испорченном (мок) — нет, с названием
проверки; итог виден в диалоге. Ведущего играет tools/tests/fake_lead_host.py, поставленный в PATH
под именем `kimi`: он исполняет блоки LEAD.md из собранного набора, а FAKE_LEAD_BREAK портит ровно один
шаг. Варианты гоняются параллельно — у каждого свой стенд.

Живая проба на настоящем Kimi — только при SUPERTICK_LIVE=1 (несколько минут вызовов модели):
  SUPERTICK_LIVE=1 python3 -m unittest discover -s tools/tests -p test_lead_probe.py
Запуск: python3 -m unittest discover -s tools/tests   (из корня репозитория)
"""
import concurrent.futures
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_scripts import executable, tmpdir

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
SCRIPTS = REPO_ROOT / "core" / "scripts"
FAKE = HERE / "fake_lead_host.py"
LIVE = os.environ.get("SUPERTICK_LIVE") == "1"


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lp = load("lead_probe", "lead-probe.py")
ss = load("supertick_setup_h2", "supertick-setup.py")


class Bench:
    """Собранный набор Kimi и каталог PATH с поддельным `kimi`."""

    def __init__(self, home):
        self.home = Path(home)
        self.bundle = self.home / "bundle"
        subprocess.run([sys.executable, str(REPO_ROOT / "tools" / "build-host.py"), "kimi", "--out", str(self.bundle)],
                       capture_output=True, text=True, check=True)
        self.bin = self.home / "bin"
        self.bin.mkdir()
        executable(self.bin / "kimi", f'#!/bin/sh\nexec {sys.executable} {FAKE} "$@"\n')
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ.get('PATH', '')}", TZ="UTC")
        self.env.pop("CLAUDECODE", None)

    def probe(self, broken="none", timeout=120, keep=None):
        env = dict(self.env, FAKE_LEAD_BREAK=broken)
        return lp.probe("kimi", "kimi-code/k3", root=self.bundle, timeout=timeout, env=env, keep=keep)


@unittest.skipUnless(shutil.which("setsid") and shutil.which("git"), "нужны setsid и git")
class ProbeVerdicts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.bench = Bench(cls._tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_each_broken_lead_fails_on_its_check(self):
        cases = {"root": "root", "state": "state", "plan": "plan", "engine": "engine", "wait": "wait", "poll": "wait",
                 "resume": "cycle", "hands": "hands", "exit": "finish"}
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(cases)) as pool:
            got = dict(zip(cases, pool.map(self.bench.probe, cases)))
        for broken, check in cases.items():
            with self.subTest(broken=broken):
                r = got[broken]
                self.assertEqual(r["result"], f"failed: {check}", lp.text(r))
                self.assertTrue(r["checks"][check]["detail"], "у проваленной проверки есть объяснение")

    def test_lead_running_tests_is_a_note_not_a_failure(self):
        r = self.bench.probe("tests")
        self.assertEqual(r["result"], "passed", lp.text(r))
        self.assertEqual(len(r["notes"]), 1)
        self.assertIn("python3 -m unittest", r["notes"][0])
        self.assertIn("⚠ tests:", lp.text(r))

    def test_hanging_lead_is_cut_by_the_deadline(self):
        r = self.bench.probe("hang", timeout=3)
        self.assertEqual(r["result"], "failed: finish")
        self.assertIn("не закончил за 3 с", r["checks"]["finish"]["detail"])
        self.assertLess(r["seconds"], 30)

    def test_working_lead_passes_and_the_kept_stand_holds_the_evidence(self):
        keep = tmpdir(self) / "stand"
        r = self.bench.probe(keep=keep)
        self.assertEqual(r["result"], "passed", lp.text(r))
        self.assertIsNone(r["failed"])
        self.assertTrue(all(c["ok"] for c in r["checks"].values()))
        self.assertEqual(r["notes"], [])
        self.assertEqual(r["version"], json.loads((REPO_ROOT / "hosts" / "kimi" / "kimi.plugin.json").read_text())["version"])
        self.assertEqual((r["host"], r["model"], r["stand"]), ("kimi", "kimi-code/k3", str(keep)))
        self.assertTrue((keep / "probe" / "wait.log").read_text().strip())
        self.assertTrue((keep / "probe" / "mock" / "calls.jsonl").is_file())
        log = subprocess.run(["git", "-C", str(keep / "repo"), "log", "--format=%an %s"], capture_output=True, text=True).stdout
        self.assertIn(f"{lp.MOCK_AUTHOR} task 1: lead-probe", log)
        alive = subprocess.run(["pgrep", "-f", str(keep) + "/"], capture_output=True, text=True).stdout.split()
        self.assertEqual(alive, [], "после пробы процессы стенда погашены")


class ProbeLimits(unittest.TestCase):
    def test_hosts_without_a_probe_say_why(self):
        for host, word in (("claude-code", "1.1.x"), ("codex", "H4"), ("opencode", "H3")):
            with self.subTest(host=host):
                with self.assertRaises(lp.ProbeError) as ctx:
                    lp.probe(host)
                self.assertIn(word, str(ctx.exception))

    def test_root_without_lead_md_is_refused(self):
        with self.assertRaises(lp.ProbeError) as ctx:
            lp.probe("kimi", root=REPO_ROOT / "core", timeout=5)
        self.assertIn("LEAD.md", str(ctx.exception))

    def test_user_config_is_not_touched_by_the_stand(self):
        stand = tmpdir(self) / "s"
        bundle = tmpdir(self) / "b"
        subprocess.run([sys.executable, str(REPO_ROOT / "tools" / "build-host.py"), "kimi", "--out", str(bundle)], check=True,
                       capture_output=True)
        paths = lp.build_stand(stand, bundle, "kimi", "m", {"sources": ["kimi"], "installed": ["kimi"]})
        env = lp.probe_env(paths, {"SUPERTICK_CONFIG": "/user/config.json", "SUPERTICK_MOCK_MODE": "fail"})
        self.assertEqual(env["SUPERTICK_CONFIG"], str(paths["config"]))
        self.assertNotIn("SUPERTICK_MOCK_MODE", env)
        cfg = json.loads(paths["config"].read_text())
        prof = cfg["profiles"][cfg["active_profile"]]
        self.assertEqual(prof["lead"], {"host": "kimi", "model": "m"})
        self.assertEqual({s["engine"] for s in prof["roles"].values()}, {"mock"})
        self.assertEqual(prof["sources"], ["kimi"])


@unittest.skipUnless(shutil.which("setsid") and shutil.which("git"), "нужны setsid и git")
class SetupIntegration(unittest.TestCase):
    """`supertick-setup.py lead-probe` пишет итог в профиль; диалог и таблица его показывают."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.bench = Bench(cls._tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self.cfg = tmpdir(self) / "config.json"
        self.cfg.write_text(json.dumps({"active_profile": "k", "profiles": {"k": {
            "lead": {"host": "kimi", "model": "kimi-code/k3"}, "roles": {}, "sources": ["kimi"], "installed": ["kimi"]}}}))
        self.env = dict(self.bench.env, SUPERTICK_CONFIG=str(self.cfg), HOME=str(self.cfg.parent))

    def setup_cli(self, *args, broken="none"):
        return subprocess.run([sys.executable, str(SCRIPTS / "supertick-setup.py"), *args], capture_output=True, text=True,
                              env=dict(self.env, FAKE_LEAD_BREAK=broken), timeout=300)

    def test_passed_probe_lands_in_the_profile_and_the_dialog(self):
        res = self.setup_cli("lead-probe", "k", "--root", str(self.bench.bundle), "--json")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        out = json.loads(res.stdout)
        self.assertEqual((out["result"], out["written"]), ("passed", str(self.cfg)))
        stored = json.loads(self.cfg.read_text())["profiles"]["k"]["lead_probe"]
        self.assertEqual(stored["result"], "passed")
        self.assertEqual(stored["model"], "kimi-code/k3")
        self.assertTrue(list(self.cfg.parent.glob("config.json.bak-*")), "прежний конфиг — копией")
        cfg = json.loads(self.cfg.read_text())
        self.assertEqual(ss.probe_mark(cfg, "kimi", "kimi-code/k3"), f"проверена как ведущий {stored['date']}")
        self.assertEqual(ss.probe_mark(cfg, "kimi", "kimi-code/k3.1-pro"), "не проверена как ведущий")
        # диалог: вариант ведущего несёт пометку
        d = {"clis": [{"cli": "kimi", "host": "kimi", "lead_capable": True, "state": "responds", "models": ["kimi-code/k3"],
                       "installed": True, "engine": "kimi", "engine_preset": True}], "sources": ["kimi"]}
        asked = []

        def ask(text):
            asked.append(text)
            return {0: "1", 1: "", 2: "1"}.get(len(asked) - 1, "")
        ss.dialog(d, ask, print, "kimi", cfg)
        self.assertIn(f"проверена как ведущий {stored['date']}", asked[0])
        # discover показывает итоги проб
        disc = self.setup_cli("discover", "--no-probe", "--json")
        self.assertEqual(json.loads(disc.stdout)["lead_probes"][0]["result"], "passed")

    def test_failed_probe_names_the_check_and_warns_in_the_plan(self):
        res = self.setup_cli("lead-probe", "k", "--root", str(self.bench.bundle), broken="hands")
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("failed: hands", res.stdout)
        self.assertIn("✗ hands", res.stdout)
        stored = json.loads(self.cfg.read_text())["profiles"]["k"]["lead_probe"]
        self.assertEqual(stored["result"], "failed: hands")
        self.assertIn("не мок-кодером", stored["detail"])
        answers = self.cfg.parent / "a.json"
        answers.write_text(json.dumps({"profile": "k", "lead": {"host": "kimi", "model": "kimi-code/k3"}}))
        plan = self.setup_cli("plan", "--answers", str(answers), "--no-probe")
        self.assertIn("проба ведущего", plan.stdout)
        self.assertIn("failed: hands", plan.stdout)

    def test_no_write_leaves_the_config_alone(self):
        before = self.cfg.read_bytes()
        res = self.setup_cli("lead-probe", "k", "--root", str(self.bench.bundle), "--no-write", broken="exit")
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("failed: finish", res.stdout)
        self.assertEqual(self.cfg.read_bytes(), before)

    def test_unknown_profile_is_an_error(self):
        res = self.setup_cli("lead-probe", "nope", "--root", str(self.bench.bundle))
        self.assertEqual(res.returncode, 2)
        self.assertIn("nope", res.stderr)


class ApplyKeepsProbe(unittest.TestCase):
    def test_same_lead_keeps_the_probe_other_model_drops_it(self):
        probe = {"result": "passed", "date": "2026-11-13", "version": "2.0.0-alpha.0"}
        existing = {"profiles": {"k": {"lead": {"host": "kimi", "model": "a"}, "roles": {}, "lead_probe": probe}}}
        same = ss.candidate_config(existing, "k", {"lead": {"host": "kimi", "model": "a"}, "roles": {}}, True)
        self.assertEqual(same["profiles"]["k"]["lead_probe"], probe)
        other = ss.candidate_config(existing, "k", {"lead": {"host": "kimi", "model": "b"}, "roles": {}}, True)
        self.assertNotIn("lead_probe", other["profiles"]["k"])

    def test_plan_without_probe_suggests_the_command(self):
        d = {"clis": [{"cli": "kimi", "host": "kimi", "lead_capable": True, "state": "responds", "models": ["m"],
                       "installed": True, "engine": "kimi", "engine_preset": True}], "sources": ["kimi"],
             "fingerprint": ss.fingerprint(["kimi"])}
        plan = ss.build_plan({"profile": "k", "lead": {"host": "kimi", "model": "m"}}, d, {})
        self.assertTrue(any("lead-probe k" in n for n in plan["notes"]), plan["notes"])

    def test_claude_code_lead_gets_no_probe_note(self):
        self.assertEqual(ss.probe_mark({}, "claude-code", "opus"), "свой протокол 1.1.x")


@unittest.skipUnless(LIVE and shutil.which("kimi"), "живая проба — только при SUPERTICK_LIVE=1 и установленном kimi")
class LiveKimi(unittest.TestCase):
    def test_real_kimi_lead_passes(self):
        bundle = tmpdir(self) / "bundle"
        subprocess.run([sys.executable, str(REPO_ROOT / "tools" / "build-host.py"), "kimi", "--out", str(bundle)], check=True,
                       capture_output=True)
        model = os.environ.get("SUPERTICK_LIVE_LEAD_MODEL", "kimi-code/k3")
        keep = os.environ.get("SUPERTICK_LIVE_KEEP")
        r = lp.probe("kimi", model, root=bundle, timeout=int(os.environ.get("SUPERTICK_LIVE_TIMEOUT", "1800")), keep=keep)
        print("\n" + lp.text(r))
        self.assertEqual(r["result"], "passed", lp.text(r))


if __name__ == "__main__":
    unittest.main()
