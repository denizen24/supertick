"""Адаптер Codex CLI (121-567, H4): сборка `build-host.py codex`, роли на движке codex, сторож .git, установщик, проба.

Живой прогон из Codex — в docs/spikes/h4-codex-live.md; здесь то, что проверяется без модели. Ведущего в пробе
играет tools/tests/fake_lead_host.py под именем `codex` (вывод в формате `codex exec --json`).

Живая проба на настоящем Codex — только при SUPERTICK_LIVE=1 (15–20 минут вызовов модели):
  SUPERTICK_LIVE=1 python3 -m unittest discover -s tools/tests -p test_codex_host.py -k LiveCodex
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

from test_engine_presets_f4 import wait_done
from test_lead_probe import LIVE, Bench, load, lp, ss
from test_scripts import tmpdir

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BUILD = ROOT / "tools" / "build-host.py"
INSTALL = ROOT / "tools" / "install-codex.sh"
GUARD = ROOT / "core" / "scripts" / "git-guard.py"
ec = load("engine_config_h4", "engine-config.py")


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def new_repo(base):
    repo = Path(base) / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "t")
    git(repo, "config", "user.email", "t@t.invalid")
    (repo / "a.txt").write_text("a\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    return repo


class CodexBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name) / "codex"
        res = subprocess.run([sys.executable, str(BUILD), "codex", "--out", str(cls.out)], capture_output=True, text=True)
        assert res.returncode == 0, res.stderr

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_manifest_and_layout(self):
        manifest = json.loads((self.out / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        kimi = json.loads((ROOT / "hosts" / "kimi" / "kimi.plugin.json").read_text(encoding="utf-8"))
        self.assertEqual((manifest["name"], manifest["skills"]), ("supertick", "./skills/"))
        self.assertEqual(manifest["version"], kimi["version"], "одна версия набора на все среды")
        for rel in ("LEAD.md", "primitives.md", "skills/team-feature/SKILL.md", "skills/setup/SKILL.md",
                    "scripts/supervisor-tick.py", "scripts/git-guard.py", "scripts/lead-probe.py", "roles/coder.md"):
            self.assertTrue((self.out / rel).is_file(), rel)
        self.assertFalse((self.out / "scripts" / "tests").exists())
        self.assertFalse((self.out / "agents").exists(), "типов исполнителей у Codex нет")
        for script in ("supervisor-wait.sh", "git-guard.py", "run-engine.sh"):
            self.assertTrue(os.access(self.out / "scripts" / script, os.X_OK), script)

    def test_primitives_are_l1_and_short(self):
        text = (self.out / "primitives.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(text.splitlines()), 80)
        for needle in ("L1", "--max", "write_stdin", "danger-full-access", "lead:git-guard", "lead:git-check",
                       "spawn_agent", "send_input", "wait_agent", "close_agent"):
            self.assertIn(needle, text)
        for op in ("ЗАПУСТИТЬ", "ПРОДОЛЖИТЬ", "ОТВЕТ", "ЖДАТЬ", "СПРОСИТЬ", "ОСТАНОВИТЬ"):
            self.assertIn(f"| {op} |", text)

    def test_skill_names_host_and_level(self):
        text = (self.out / "skills" / "team-feature" / "SKILL.md").read_text(encoding="utf-8")
        for needle in ("LEAD.md", "primitives.md", "SUPERTICK_ROOT", "`codex`", "L1"):
            self.assertIn(needle, text)
        self.assertNotIn("KIMI_SKILL_DIR", text)

    def test_root_block_prefers_supertick_root_over_the_skill_dir(self):
        block = re.compile(r"```bash\n\s*(cd .+?)\n\s*```", re.S)
        other = tmpdir(self)
        for skill in ("team-feature", "setup"):
            skill_dir = self.out / "skills" / skill
            text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
            m = block.search(text) or re.search(r"`(cd \"\$\{SUPERTICK_ROOT[^`]+)`", text)
            self.assertIsNotNone(m, f"{skill}: нет команды корня")
            cmd = m.group(1).replace("<SKILL_DIR>", str(skill_dir))   # путь подставляет модель: Codex его показывает
            env = {k: v for k, v in os.environ.items() if k != "SUPERTICK_ROOT"}
            for given, want in ((None, self.out), (other, other)):
                with self.subTest(skill=skill, root=given):
                    run_env = dict(env, **({"SUPERTICK_ROOT": str(given)} if given else {}))
                    res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, env=run_env)
                    self.assertEqual(Path(res.stdout.strip()).resolve(), Path(want).resolve(), res.stderr)

    def test_lead_md_guards_git_at_start_and_end(self):
        text = (self.out / "LEAD.md").read_text(encoding="utf-8")
        self.assertLess(text.index("# lead:run-dir"), text.index("# lead:git-guard"))
        self.assertLess(text.index("# lead:git-guard"), text.index("# lead:git-check"))

    def test_claude_code_build_is_untouched(self):
        res = subprocess.run([sys.executable, str(BUILD), "claude-code", "--check"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)


class RolesOnEngine(unittest.TestCase):
    """На Codex роль без назначения идёт движком codex с моделью ведущего: у spawn_agent нет своей песочницы."""

    CFG = {"active_profile": "c", "profiles": {"c": {"lead": {"host": "codex", "model": "gpt-5.6-luna"}, "roles": {}}}}

    def test_unassigned_roles_go_to_codex_with_the_lead_model(self):
        res = ec.resolve(self.CFG, "codex", {"codex"})
        self.assertFalse(res["stop"])
        for role in ("coder", "unified-reviewer", "acceptance-checker", "spec-verifier", "ci-verifier", "tech-lead",
                     "codebase-researcher", "legacy-scanner"):
            with self.subTest(role=role):
                self.assertEqual(res["detail"][role], {"engine": "codex", "model": "gpt-5.6-luna"})
        self.assertNotIn(res["table"].get("browser-verifier"), ("codex", "native"), "браузерная проверка только в Claude Code")
        self.assertEqual(res["table"]["second-reviewer"], "none", "второе мнение — только явным назначением")

    def test_read_roles_sandboxed_and_only_coder_writes_git(self):
        repo = new_repo(tmpdir(self))
        review = ec.build_argv(self.CFG, "codex", "unified-reviewer", None, "/r", "/p.md", available={"codex"}, cwd=repo)
        coder = ec.build_argv(self.CFG, "codex", "coder", None, "/r", "/p.md", available={"codex"}, cwd=repo)
        self.assertEqual(review["argv"][review["argv"].index("--sandbox") + 1], "read-only")
        self.assertEqual(review["argv"][review["argv"].index("-m") + 1], "gpt-5.6-luna")
        self.assertFalse(any("writable_roots" in a for a in review["argv"]))
        self.assertEqual(coder["argv"][coder["argv"].index("--sandbox") + 1], "workspace-write")
        roots = next(a for a in coder["argv"] if a.startswith("sandbox_workspace_write.writable_roots="))
        self.assertIn(str((repo / ".git").resolve()), roots)
        resumed = ec.build_argv(self.CFG, "codex", "coder", None, "/r", "/p.md", resume="s-1", available={"codex"}, cwd=repo)
        self.assertEqual(resumed["argv"][resumed["argv"].index("-m") + 1], "gpt-5.6-luna", "продолжение не теряет модель")

    def test_explicit_assignment_wins(self):
        cfg = json.loads(json.dumps(self.CFG))
        cfg["profiles"]["c"]["roles"] = {"unified-reviewer": {"engine": "cursor"}, "coder": {"engine": "codex", "model": "gpt-5.6-sol"}}
        res = ec.resolve(cfg, "codex", {"codex", "cursor-agent"})
        self.assertEqual(res["table"]["unified-reviewer"], "cursor")
        self.assertEqual(res["detail"]["coder"]["model"], "gpt-5.6-sol")
        self.assertEqual(res["detail"]["tech-lead"]["model"], "gpt-5.6-luna")

    def test_without_codex_binary_roles_stay_native(self):
        res = ec.resolve(self.CFG, "codex", set())
        self.assertIn(res["table"].get("coder", "native"), ("native",))
        self.assertIsNone(ec.build_argv(self.CFG, "codex", "coder", None, "/r", "/p.md", available=set()))

    def test_other_hosts_keep_native_roles(self):
        cfg = {"active_profile": "k", "profiles": {"k": {"lead": {"host": "kimi", "model": "kimi-code/k3"}, "roles": {}}}}
        self.assertNotIn("coder", ec.resolve(cfg, "kimi", {"kimi", "codex"})["table"])

    def test_launch_runs_the_lead_without_sandbox(self):
        self.assertEqual(ec.launch_command({"host": "codex", "model": "gpt-5.6-sol"}),
                         "codex -m gpt-5.6-sol --sandbox danger-full-access")


class GitGuard(unittest.TestCase):
    def run_guard(self, *args):
        return subprocess.run([sys.executable, str(GUARD), *args], capture_output=True, text=True)

    def setUp(self):
        base = tmpdir(self)
        self.repo = new_repo(base)
        self.run = base / "run"
        self.run.mkdir()

    def test_untouched_run_is_ok_and_commits_do_not_count(self):
        self.assertEqual(self.run_guard("snapshot", str(self.run), "--root", str(self.repo)).returncode, 0)
        (self.repo / "b.txt").write_text("b\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "b")
        res = self.run_guard("check", str(self.run), "--root", str(self.repo))
        self.assertEqual((res.returncode, res.stdout.strip()), (0, "GIT GUARD OK"))

    def test_new_hook_changed_config_and_exec_bit_are_reported(self):
        sample = next((self.repo / ".git" / "hooks").glob("*.sample"), None)
        self.run_guard("snapshot", str(self.run), "--root", str(self.repo))
        (self.repo / ".git" / "hooks" / "post-checkout").write_text("#!/bin/sh\necho owned\n")
        git(self.repo, "config", "core.hooksPath", "/tmp/evil")
        if sample:
            sample.chmod(0o644 if os.access(sample, os.X_OK) else 0o755)
        res = self.run_guard("check", str(self.run), "--root", str(self.repo))
        self.assertEqual(res.returncode, 1)
        first = res.stdout.splitlines()[0]
        self.assertTrue(first.startswith("GIT GUARD CHANGED: "), first)
        self.assertIn("hooks/post-checkout (новый)", first)
        self.assertIn("config (изменён)", first)
        if sample:
            self.assertIn(f"hooks/{sample.name} (сменилось право на исполнение)", first)

    def test_snapshot_is_kept_on_resume_and_worktree_sees_common_hooks(self):
        self.run_guard("snapshot", str(self.run), "--root", str(self.repo))
        (self.repo / ".git" / "hooks" / "pre-push").write_text("#!/bin/sh\n")
        again = self.run_guard("snapshot", str(self.run), "--root", str(self.repo))
        self.assertIn("уже есть", again.stdout)
        wt = self.repo.parent / "wt"
        git(self.repo, "worktree", "add", "-q", str(wt))
        res = self.run_guard("check", str(self.run), "--root", str(wt))
        self.assertEqual(res.returncode, 1)
        self.assertIn("hooks/pre-push (новый)", res.stdout)

    def test_missing_snapshot_and_not_a_repo(self):
        self.assertEqual(self.run_guard("check", str(self.run), "--root", str(self.repo)).returncode, 2)
        self.assertEqual(self.run_guard("snapshot", str(self.run), "--root", str(self.run)).returncode, 2)


@unittest.skipUnless(shutil.which("setsid"), "нет setsid")
class ReportWrittenByEngine(unittest.TestCase):
    """Живой прогон H4: исполнитель с записью сам кладёт файл REPORT, а run-engine.sh копировал ответ рядом в
    `-001` — два отчёта, у одного первая строка не та. Теперь файл исполнителя и есть отчёт."""

    def launch(self, root, writes):
        act = root / "act.py"
        act.write_text("import os, pathlib, re\n"
                       "p = os.environ['MOCK_PROMPT']\n"
                       "rep = re.search(r'REPORT: (\\S+)', p).group(1)\n"
                       + ("pathlib.Path(rep).write_text('COMMITTED: task #1\\nCOMMITS: abc\\n')\n" if writes else "")
                       + "print('Делаю задачу, затем отчитаюсь.')\n", encoding="utf-8")
        run = root / "run"
        prompt = root / "p.md"
        prompt.write_text(f"REPORT: {run}/reports/task1-coder-1.md\nработа\n", encoding="utf-8")
        built = ec.build_argv({"roles": {"coder": "mock"}}, "kimi", "coder", "task1-coder", str(run), str(prompt),
                              report="task1-coder-1.md", available=set())
        env = {**os.environ, "HOME": str(root), "SUPERTICK_MOCK_STATE": str(root / "mock"),
               "SUPERTICK_MOCK_ACT": f"{sys.executable} {act}"}
        env.pop("SUPERTICK_MOCK_MODE", None)
        launched = subprocess.run(["bash", *built["launcher"], "--", *built["argv"]], capture_output=True, text=True,
                                  timeout=60, env=env)
        fields, reply = wait_done(self, launched)
        return run, fields, reply

    def test_engine_file_is_the_report_and_no_copy_is_made(self):
        run, fields, reply = self.launch(tmpdir(self), writes=True)
        self.assertEqual((fields["status"], fields.get("report_by")), ("done", "engine_file"))
        self.assertEqual(Path(fields["report"]), run / "reports" / "task1-coder-1.md")
        self.assertEqual(sorted(p.name for p in (run / "reports").iterdir()), ["task1-coder-1.md"])
        self.assertTrue((run / "reports" / "task1-coder-1.md").read_text().startswith("COMMITTED: task #1"))
        self.assertIn("Делаю задачу", reply, "ответ движка остался в .out.result.md")
        ledger = [json.loads(l) for l in (run / "ledger.jsonl").read_text().splitlines()]
        self.assertEqual(ledger[-1]["report_by"], "engine_file")

    def test_reply_is_the_report_when_the_engine_writes_nothing(self):
        run, fields, _ = self.launch(tmpdir(self), writes=False)
        self.assertNotIn("report_by", fields)
        self.assertTrue((run / "reports" / "task1-coder-1.md").read_text().startswith("Делаю задачу"))

    def test_a_report_existing_before_the_call_is_never_overwritten(self):
        root = tmpdir(self)
        (root / "run" / "reports").mkdir(parents=True)
        (root / "run" / "reports" / "task1-coder-1.md").write_text("старый\n")
        run, fields, _ = self.launch(root, writes=True)
        self.assertNotIn("report_by", fields)
        self.assertEqual(Path(fields["report"]).name, "task1-coder-1-001.md")


@unittest.skipUnless(shutil.which("codex"), "нужен codex")
class Installer(unittest.TestCase):
    def test_install_reinstall_and_uninstall_in_a_private_home(self):
        base = tmpdir(self)
        home, market = base / "home", base / "market"
        home.mkdir()

        def run(*extra):
            return subprocess.run(["bash", str(INSTALL), "--codex-home", str(home), "--market", str(market), *extra],
                                  capture_output=True, text=True, timeout=300)

        res = run()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        root = Path(re.search(r"^установлен: supertick, (.+)$", res.stdout, re.M).group(1))
        self.assertTrue(root.is_relative_to(home))
        self.assertTrue((root / "skills" / "team-feature" / "SKILL.md").is_file())
        self.assertTrue(os.access(root / "scripts" / "supervisor-wait.sh", os.X_OK))
        self.assertIn('[plugins."supertick@supertick-local"]', (home / "config.toml").read_text())
        old = dict(os.environ)
        try:   # проба находит установленный набор там же
            os.environ["CODEX_HOME"] = str(home)
            self.assertEqual(lp.installed_root("codex"), root)
        finally:
            os.environ.clear()
            os.environ.update(old)
        self.assertEqual(run().returncode, 0, "повтор безвреден")
        res = run("--uninstall")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertNotIn("supertick", (home / "config.toml").read_text())
        self.assertFalse(market.exists())


@unittest.skipUnless(shutil.which("setsid") and shutil.which("git"), "нужны setsid и git")
class CodexProbe(unittest.TestCase):
    """Проба ведущего Codex на поддельной среде: вывод в формате codex exec --json, уровень L1."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.bench = Bench(cls._tmp.name, host="codex")

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
                self.assertEqual((r["host"], r["model"]), ("codex", "gpt-5.6-sol"))
        self.assertIn(".git тронут: hooks/post-checkout (новый)", got["hook"]["checks"]["hands"]["detail"])
        self.assertEqual(len(got["tests"]["notes"]), 1, "команды тестов распознаны и в выводе Codex")
        self.assertIn("ожиданий", got["none"]["checks"]["wait"]["detail"])

    def test_setup_writes_the_codex_probe_into_the_profile(self):
        cfg_file = tmpdir(self) / "config.json"
        cfg_file.write_text(json.dumps({"active_profile": "c", "profiles": {"c": {
            "lead": {"host": "codex", "model": "gpt-5.6-sol"}, "roles": {}, "sources": ["codex"], "installed": ["codex"]}}}))
        env = dict(self.bench.env, SUPERTICK_CONFIG=str(cfg_file), HOME=str(cfg_file.parent), FAKE_LEAD_BREAK="none")
        res = subprocess.run([sys.executable, str(ROOT / "core" / "scripts" / "supertick-setup.py"), "lead-probe", "c",
                              "--root", str(self.bench.bundle), "--json"], capture_output=True, text=True, env=env, timeout=300)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        cfg = json.loads(cfg_file.read_text())
        self.assertEqual(cfg["profiles"]["c"]["lead_probe"]["result"], "passed")
        self.assertTrue(ss.probe_mark(cfg, "codex", "gpt-5.6-sol").startswith("проверена как ведущий"))


@unittest.skipUnless(LIVE and shutil.which("codex"), "живая проба: SUPERTICK_LIVE=1 и установленный codex")
class LiveCodex(unittest.TestCase):
    def test_real_codex_lead_passes(self):
        model = os.environ.get("SUPERTICK_LIVE_LEAD_MODEL", "gpt-5.6-sol")
        keep = os.environ.get("SUPERTICK_LIVE_KEEP")
        bundle = Path(tempfile.mkdtemp(prefix="supertick-codex-bundle-"))
        subprocess.run([sys.executable, str(BUILD), "codex", "--out", str(bundle)], check=True, capture_output=True)
        r = lp.probe("codex", model, root=bundle, timeout=int(os.environ.get("SUPERTICK_LIVE_TIMEOUT", "2400")), keep=keep)
        print("\n" + lp.text(r))
        self.assertEqual(r["result"], "passed", lp.text(r))


if __name__ == "__main__":
    unittest.main()
