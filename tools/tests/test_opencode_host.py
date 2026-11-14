"""Адаптер OpenCode (121-567, H3): сборка `build-host.py opencode`, агенты ролей с правами, установщик, проба.

Весь набор лежит внутри навыка `skills/supertick-team-feature/`: файлы вне проекта OpenCode в `opencode run` не
даёт читать (`external_directory` — отказ), а каталоги навыков открыты ведущему и субагентам (проверено 14.11.2026).
Ведущего в пробе играет tools/tests/fake_lead_host.py под именем `opencode` (вывод в формате `opencode run --format
json`). Живая проба на настоящем OpenCode — только при SUPERTICK_LIVE=1, на бесплатной модели:
  SUPERTICK_LIVE=1 python3 -m unittest discover -s tools/tests -p test_opencode_host.py -k LiveOpencode
Запуск: python3 -m unittest discover -s tools/tests   (из корня репозитория)
"""
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_lead_probe import LIVE, Bench, load, lp, ss
from test_scripts import tmpdir

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BUILD = ROOT / "tools" / "build-host.py"
INSTALL = ROOT / "tools" / "install-opencode.sh"
SKILL = "skills/supertick-team-feature"
ec = load("engine_config_h3", "engine-config.py")
ROLE_AGENTS = ("coder", "unified-reviewer", "acceptance-checker", "spec-verifier", "ci-verifier", "tech-lead",
               "architect", "risk-tester", "codebase-researcher", "reference-researcher", "legacy-scanner")


def scalar(v):
    try:
        return json.loads(v)
    except ValueError:
        return v   # шапка навыка пишется без кавычек


def frontmatter(path):
    head = path.read_text(encoding="utf-8").split("---\n")[1]
    out, sub = {}, None
    for line in head.splitlines():
        if line.startswith("  ") and sub is not None:
            k, v = line.strip().split(": ", 1)
            out[sub][k] = scalar(v)
        else:
            k, v = line.split(":", 1)
            sub = k if not v.strip() else None
            out[k] = {} if sub else scalar(v.strip())
    return out


class OpencodeBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name) / "opencode"
        res = subprocess.run([sys.executable, str(BUILD), "opencode", "--out", str(cls.out)], capture_output=True, text=True)
        assert res.returncode == 0, res.stderr

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_bundle_lives_inside_the_team_feature_skill(self):
        root = self.out / SKILL
        for rel in ("SKILL.md", "LEAD.md", "primitives.md", "scripts/supervisor-tick.py", "scripts/run-engine.sh",
                    "scripts/lead-probe.py", "engines/opencode-supertick.json", "roles/coder.md"):
            self.assertTrue((root / rel).is_file(), rel)
        self.assertTrue((self.out / "skills" / "supertick-setup" / "SKILL.md").is_file())
        self.assertFalse((root / "scripts" / "tests").exists())
        for script in ("supervisor-wait.sh", "git-guard.py", "run-engine.sh"):
            self.assertTrue(os.access(root / "scripts" / script, os.X_OK), script)
        skills = sorted(str(p.relative_to(self.out)) for p in self.out.rglob("SKILL.md"))
        self.assertEqual(skills, sorted([f"{SKILL}/SKILL.md", "skills/supertick-setup/SKILL.md"]), "других SKILL.md в наборе нет")

    def test_version_is_the_same_as_other_hosts_and_doctor_sees_it(self):
        kimi = json.loads((ROOT / "hosts" / "kimi" / "kimi.plugin.json").read_text(encoding="utf-8"))
        manifest = json.loads((self.out / SKILL / "supertick.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], kimi["version"], "одна версия набора на все среды")
        doctor = load("doctor_h3", "supertick-doctor.py")
        self.assertEqual(doctor.bundle_version(self.out / SKILL), (kimi["version"], "supertick.json"))

    def test_skill_names_match_their_folders_and_root_comes_from_base_directory(self):
        for name in ("supertick-team-feature", "supertick-setup"):
            fm = frontmatter(self.out / "skills" / name / "SKILL.md")
            self.assertEqual(fm["name"], name)
            self.assertTrue(fm["description"])
        team = (self.out / SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn('cd "${SUPERTICK_ROOT:-<SKILL_DIR>}" && pwd', team)
        self.assertIn("Base directory for this skill", team)
        setup = (self.out / "skills" / "supertick-setup" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("<SKILL_DIR>/../supertick-team-feature", setup)

    def test_role_agents_carry_the_same_access_as_kimi_types(self):
        kimi = {Path(e["to"]).stem: e["frontmatter"]["tools"]
                for e in json.loads((ROOT / "hosts" / "kimi" / "build.json").read_text(encoding="utf-8"))["include"]
                if "frontmatter" in e}
        agents = sorted(p.stem for p in (self.out / "agents").glob("*.md"))
        self.assertEqual(agents, sorted(f"supertick-{r}" for r in ROLE_AGENTS))
        for name in agents:
            with self.subTest(agent=name):
                fm = frontmatter(self.out / "agents" / f"{name}.md")
                tools = set(kimi[name])
                self.assertEqual(fm["mode"], "subagent")
                self.assertEqual(fm["permission"], {
                    "edit": "allow" if {"Write", "Edit"} & tools else "deny",
                    "bash": "allow" if "Bash" in tools else "deny", "webfetch": "deny", "task": "deny"})
                brief = (ROOT / "core" / "roles" / f"{name.removeprefix('supertick-')}.md").read_text(encoding="utf-8")
                self.assertTrue((self.out / "agents" / f"{name}.md").read_text(encoding="utf-8").endswith(brief))

    def test_primitives_are_l1_with_task_and_a_timeout(self):
        text = (self.out / SKILL / "primitives.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(text.splitlines()), 80)
        for needle in ("L1", "task_id", "subagent_type", "<task_result>", "timeout: 1800000", "--mode shared",
                       "external_directory", "supertick-coder"):
            self.assertIn(needle, text)
        for op in ("ЗАПУСТИТЬ", "ПРОДОЛЖИТЬ", "ОТВЕТ", "ЖДАТЬ", "СПРОСИТЬ", "ОСТАНОВИТЬ"):
            self.assertRegex(text, rf"\| {op} \|")


class OpencodeHostTable(unittest.TestCase):
    def test_level_l1_with_native_subagents(self):
        self.assertEqual(ec.HOSTS["opencode"], {"level": "L1", "native": "native", "subagents": True, "engine": "opencode"})
        res = ec.resolve({"roles": {"spec-verifier": "cursor"}}, "opencode", {"opencode", "cursor-agent"})
        self.assertNotIn("coder", res["table"], "неназначенная роль — родной агент supertick-coder")
        self.assertEqual(res["table"]["spec-verifier"], "cursor")

    def test_opencode_can_lead(self):
        self.assertTrue(ss.LEAD_CAPABLE["opencode"])
        self.assertEqual(lp.HOST_ARGV["opencode"]("/b/opencode", "m/x", "P"),
                         ["/b/opencode", "run", "--format", "json", "-m", "m/x", "--", "P"])

    def test_probe_reads_opencode_shell_commands(self):
        cmds = lp.shell_commands(ROOT / "docs" / "spikes" / "fixtures" / "opencode" / "lead-probe-glm.jsonl")
        self.assertTrue(any("mock-engine.sh" in c for c in cmds), cmds[:3])


class OpencodeInstall(unittest.TestCase):
    def test_install_replaces_only_ours_and_uninstall_removes_only_ours(self):
        cfg = tmpdir(self) / "oc"
        (cfg / "skills" / "mine").mkdir(parents=True)
        (cfg / "skills" / "mine" / "SKILL.md").write_text("x")
        (cfg / "agents").mkdir()
        (cfg / "agents" / "mine.md").write_text("x")
        run = lambda *a: subprocess.run(["bash", str(INSTALL), "--dir", str(cfg), *a], capture_output=True, text=True)
        for _ in range(2):   # повтор безвреден
            res = run()
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertTrue((cfg / SKILL / "LEAD.md").is_file())
        self.assertEqual(len(list((cfg / "agents").glob("supertick-*.md"))), len(ROLE_AGENTS))
        self.assertFalse(list((cfg / "skills").glob("*.new")))
        res = run("--uninstall")
        self.assertEqual(res.returncode, 0)
        self.assertEqual(sorted(p.name for p in (cfg / "skills").iterdir()), ["mine"])
        self.assertEqual(sorted(p.name for p in (cfg / "agents").iterdir()), ["mine.md"])

    def test_project_install_goes_to_dot_opencode(self):
        repo = tmpdir(self)
        res = subprocess.run(["bash", str(INSTALL), "--project", str(repo)], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue((repo / ".opencode" / SKILL / "SKILL.md").is_file())


@unittest.skipUnless(shutil.which("setsid") and shutil.which("git"), "нужны setsid и git")
class OpencodeProbe(unittest.TestCase):
    """Проба ведущего OpenCode на поддельной среде: вывод в формате opencode run --format json, уровень L1."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.bench = Bench(cls._tmp.name, host="opencode")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_good_and_broken_leads(self):
        cases = {"none": None, "poll": "wait", "engine": "engine", "hook": "hands", "tests": None}
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(cases)) as pool:
            got = dict(zip(cases, pool.map(self.bench.probe, cases)))
        for broken, check in cases.items():
            with self.subTest(broken=broken):
                r = got[broken]
                self.assertEqual(r["result"], f"failed: {check}" if check else "passed", lp.text(r))
                self.assertEqual((r["host"], r["model"]), ("opencode", "opencode/deepseek-v4.1-flash-free"))
        self.assertEqual(len(got["tests"]["notes"]), 1, "команды тестов распознаны и в выводе OpenCode")

    def test_setup_writes_the_opencode_probe_into_the_profile(self):
        cfg_file = tmpdir(self) / "config.json"
        model = "opencode/deepseek-v4.1-flash-free"
        cfg_file.write_text(json.dumps({"active_profile": "o", "profiles": {"o": {
            "lead": {"host": "opencode", "model": model}, "roles": {}, "sources": ["opencode"], "installed": ["opencode"]}}}))
        env = dict(self.bench.env, SUPERTICK_CONFIG=str(cfg_file), HOME=str(cfg_file.parent), FAKE_LEAD_BREAK="none")
        res = subprocess.run([sys.executable, str(ROOT / "core" / "scripts" / "supertick-setup.py"), "lead-probe", "o",
                              "--root", str(self.bench.root), "--json"], capture_output=True, text=True, env=env, timeout=300)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        cfg = json.loads(cfg_file.read_text())
        self.assertEqual(cfg["profiles"]["o"]["lead_probe"]["result"], "passed")
        self.assertTrue(ss.probe_mark(cfg, "opencode", model).startswith("проверена как ведущий"))


@unittest.skipUnless(LIVE and shutil.which("opencode"), "живая проба: SUPERTICK_LIVE=1 и установленный opencode")
class LiveOpencode(unittest.TestCase):
    def test_real_opencode_lead_passes(self):
        model = os.environ.get("SUPERTICK_LIVE_LEAD_MODEL", "opencode/deepseek-v4.1-flash-free")
        keep = os.environ.get("SUPERTICK_LIVE_KEEP")
        bundle = Path(tempfile.mkdtemp(prefix="supertick-opencode-bundle-"))
        subprocess.run([sys.executable, str(BUILD), "opencode", "--out", str(bundle)], check=True, capture_output=True)
        r = lp.probe("opencode", model, root=bundle / SKILL, timeout=int(os.environ.get("SUPERTICK_LIVE_TIMEOUT", "2400")),
                     keep=keep)
        print("\n" + lp.text(r))
        self.assertEqual(r["result"], "passed", lp.text(r))


if __name__ == "__main__":
    unittest.main()
