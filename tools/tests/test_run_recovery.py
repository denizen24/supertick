import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "core" / "scripts"


class Recovery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "project"
        self.repo.mkdir()
        self.run_dir = self.root / "run"
        self.run_dir.mkdir()
        self.prompt = self.root / "prompt"
        self.prompt.write_text("synthetic prompt")
        self.plan = self.root / "plan.json"

    def git(self, *args):
        return subprocess.check_output(["git", "-C", self.repo, *args], stderr=subprocess.DEVNULL, text=True).strip()

    def init(self):
        self.git("init")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Tests")
        (self.repo / "source.txt").write_text("one")
        (self.repo / ".env.example").write_text("EXAMPLE=fixture")
        (self.repo / ".env").write_text("EXAMPLE=not-copied")
        self.git("add", "--", "source.txt", ".env", ".env.example")
        self.git("commit", "-m", "fixture")

    def command(self, script, *args):
        return [sys.executable, str(SCRIPTS / script), *map(str, args)]

    def call(self, script, *args, success=True):
        result = subprocess.run(self.command(script, *args), text=True, capture_output=True, timeout=15)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return result

    def launcher(self, name, *argv, timeout=None):
        command = ["bash", str(SCRIPTS / "run-engine.sh"), "--engine", "mock", "--role", name,
                   "--run-dir", str(self.run_dir), "--prompt", str(self.prompt), "--report", name + ".md"]
        if timeout is not None:
            command += ["--timeout", str(timeout)]
        return subprocess.run(command + ["--", *map(str, argv)], capture_output=True, text=True, timeout=8)

    def wait_file(self, path):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if path.is_file():
                return path.read_text()
            time.sleep(0.05)
        self.fail(f"missing {path}")

    def test_timeout_uses_card_and_partial_result_can_be_taken(self):
        self.call("run-state.py", "new", self.run_dir, "ci", "executor=engine", "timeoutSec=1")
        mismatch = self.launcher("ci", sys.executable, "-c", "pass", timeout=10)
        self.assertIn("differs from card", mismatch.stderr)
        launch = self.launcher("ci", sys.executable, "-u", "-c", "import time; print('partial'); time.sleep(15)")
        self.assertEqual(launch.returncode, 0, launch.stderr)
        self.assertIn("timeout_source=card", launch.stdout)
        denied = self.call("run-state.py", "set", self.run_dir, "ci", "timeoutSec=20", success=False)
        self.assertNotEqual(denied.returncode, 0)
        out = self.run_dir / "engine" / "ci" / "001.out"
        self.assertIn("failure_kind=timeout", self.wait_file(Path(f"{out}.done")))
        result = json.loads(self.call("run-state.py", "take", self.run_dir, out).stdout)
        self.assertEqual(result["failureKind"], "timeout")
        self.assertIsNone(result["report"])
        self.assertIn("partial", Path(result["artifact"]).read_text())

    def test_cli_exit_124_and_quota_are_distinct(self):
        out = self.run_dir / "engine" / "ci" / "001.out"
        self.assertEqual(self.launcher("ci", sys.executable, "-c", "raise SystemExit(124)").returncode, 0)
        self.assertIn("failure_kind=engine_exit", self.wait_file(Path(f"{out}.done")))
        quota = "print('{\"type\":\"provider.api_error\",\"message\":\"usage limit\"}'); raise SystemExit(1)"
        self.assertEqual(self.launcher("ci", sys.executable, "-c", quota).returncode, 0)
        marker = self.run_dir / "engine" / "ci" / "002.out.done"
        self.assertIn("failure_kind=quota", self.wait_file(marker))
        refused = self.launcher("ci", sys.executable, "-c", "print('would retry')")
        self.assertIn("quota exhausted", refused.stderr)

    def plan_args(self, name, *extra):
        return ["run", "--repo", self.repo, "--run-dir", self.run_dir, "--id", name, "--plan", self.plan, *extra]

    def test_timeout_resume_preserves_finished_test_and_binds_exact_commit(self):
        self.init()
        counter, gate = self.root / "count", self.root / "ready"
        first = f"""from pathlib import Path
import os, tempfile
assert not Path('.env').exists()
assert Path('.env.example').is_file()
assert tempfile.gettempdir() == os.environ['TMPDIR']
assert len(os.environ['DB_NAME']) < 45
with open({str(counter)!r}, 'a') as f: f.write('once\\n')
"""
        second = f"from pathlib import Path; import time; time.sleep(0 if Path({str(gate)!r}).exists() else 8)"
        self.plan.write_text(json.dumps({"env_templates": [".env.example"], "git_context": True, "steps": [
            {"id": "one", "argv": [sys.executable, "-c", first]}, {"id": "two", "argv": [sys.executable, "-c", second]}
        ]}))
        self.assertEqual(self.launcher("ci", *self.command("run-checks.py", *self.plan_args("ci")), timeout=2).returncode, 0)
        out = self.run_dir / "engine" / "ci" / "001.out.done"
        self.assertIn("timeout", self.wait_file(out))
        summary = self.run_dir / "tmp/checks/ci/results.json"
        previous = json.loads(summary.read_text())
        self.assertEqual(previous["steps"]["one"]["status"], "PASS")
        self.assertEqual(previous["steps"]["two"]["status"], "RUNNING")
        gate.touch()
        self.call("run-checks.py", *self.plan_args("ci", "--resume"))
        self.assertEqual(counter.read_text().splitlines(), ["once"])
        bind = ["bind", "--repo", self.repo, "--run-dir", self.run_dir, "--id", "ci"]
        self.assertEqual(json.loads(self.call("run-checks.py", *bind).stdout)["matchedFiles"], 2)
        (self.repo / "source.txt").write_text("two")
        self.git("commit", "-am", "change after tests")
        self.assertIn("differs from tested snapshot", self.call("run-checks.py", *bind, success=False).stderr)
        self.call("run-checks.py", *self.plan_args("ci", "--resume", "--only", "two"), success=False)
        latest = json.loads(summary.read_text())
        self.assertEqual(latest["changedSinceLastAttempt"], ["source.txt"])
        self.assertEqual(latest["steps"]["one"]["status"], "OUTDATED")

    def test_queue_is_visible_and_tests_start_only_when_lock_is_released(self):
        self.init()
        lock = self.root / "resource.lock"
        began = self.root / "began"
        # The first check holds the shared lock. Use distinct plans to ensure
        # this one cannot borrow an earlier result.
        first_plan = self.plan
        first_plan.write_text(json.dumps({"steps": [{"id": "active", "argv": [
            sys.executable, "-c", "import time; time.sleep(2)"]}]}))
        p = subprocess.Popen(self.command("run-checks.py", *self.plan_args("owner", "--lock", lock)),
                             stdout=subprocess.DEVNULL)
        try:
            active = self.run_dir / "state/checks/owner.json"
            self.wait_file(active)
            deadline = time.monotonic() + 6
            while json.loads(active.read_text())["status"] != "running" and time.monotonic() < deadline:
                time.sleep(.02)
            self.plan = self.root / "waiter.json"
            self.plan.write_text(json.dumps({"steps": [{"id": "check", "argv": [sys.executable, "-c",
                                            f"from pathlib import Path; Path({str(began)!r}).touch()"]}]}))
            launch = self.launcher("waiter", *self.command("run-checks.py", *self.plan_args("waiter", "--lock", lock)), timeout=1)
            self.assertEqual(launch.returncode, 0, launch.stderr)
            queued = json.loads(self.wait_file(self.run_dir / "state/checks/waiter.json"))
            self.assertEqual(queued["status"], "queued")
            self.assertEqual(queued["holder"]["check"], "owner")
            self.assertFalse(began.exists())
            done = self.run_dir / "engine/waiter/001.out.done"
            self.assertIn("status=done", self.wait_file(done))
            self.assertTrue(began.exists())
        finally:
            if p.poll() is None:
                p.kill()
            p.wait()

    def test_guard_rejects_untracked_and_linked_templates_without_running_steps(self):
        self.init()
        bad = self.repo / "backend"
        bad.mkdir()
        (bad / ".env.example").write_text("fixture")
        fake = {"env_templates": ["backend/.env.example"], "steps": [
            {"id": "unexpected", "argv": [sys.executable, "-c", "raise AssertionError('must not run')"]}]}
        self.plan.write_text(json.dumps(fake))
        result = self.call("run-checks.py", *self.plan_args("untracked"), success=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("not a tracked ordinary file", result.stderr)
        (bad / ".env.example").unlink()
        (bad / ".env.example").symlink_to("../.env")
        self.git("add", "backend/.env.example")
        result = self.call("run-checks.py", *self.plan_args("linked"), success=False)
        self.assertIn("not a tracked ordinary file", result.stderr)

    def test_copied_dependencies_cannot_add_environment_files(self):
        self.init()
        dependencies = self.root / "deps"
        dependencies.mkdir()
        (dependencies / ".env").write_text("fixture")
        self.plan.write_text(json.dumps({"copy_dependencies": {"node_modules": str(dependencies)}, "steps": [
            {"id": "unexpected", "argv": [sys.executable, "-c", "raise AssertionError('must not run')"]}]}))
        result = self.call("run-checks.py", *self.plan_args("deps"), success=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("unexpected environment file", result.stderr)
        self.assertFalse((self.run_dir / "tmp/checks/deps/results.json").exists())

    def test_status_counts_launches_separately_from_prepared_cards(self):
        self.call("run-state.py", "new", self.run_dir, "agent", "role=coder", "executor=native")
        args = ("status", self.run_dir)
        status = json.loads(self.call("supervisor-tick.py", *args).stdout)
        self.assertEqual(status["nativeCardsByRole"], {"coder": 1})
        self.assertEqual(status["launchesByRole"], {})
        self.call("run-state.py", "set", self.run_dir, "agent", "status=running")
        self.call("run-state.py", "set", self.run_dir, "agent", "status=running")
        status = json.loads(self.call("supervisor-tick.py", *args).stdout)
        self.assertEqual(status["launchesByRole"], {"coder": 1})

    def test_manual_acceptance_preserves_failure_and_checks_commit(self):
        self.init()
        sha = self.git("rev-parse", "HEAD")
        reports = self.run_dir / "reports"
        reports.mkdir()
        failure = reports / "accept-task4.md"
        failure.write_text("ACCEPT: task #4 — FAIL\n")
        (reports / "task4-coder-1.md").write_text(f"COMMITS: {sha}\n")
        (self.run_dir / "PLAN.md").write_text("## Task 4: Docs\nStatus: DONE\n")
        self.call("run-state.py", "manual-accept", self.run_dir, "4", f"commit={sha}",
                  "reason=Documentation tooling mismatch", "evidence=owner approved this commit")
        self.call("supervisor-tick.py", "postrun-check", self.run_dir, "--root", self.repo)
        self.assertEqual(failure.read_text(), "ACCEPT: task #4 — FAIL\n")
        result = self.call("supervisor-tick.py", self.repo, "--root", self.repo, success=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.repo / "state").exists())
