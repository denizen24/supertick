"""Бюджеты, plan и postrun-check (121-567, X1; идея — dsh-supertick, код и тесты свои, см. NOTICE).

- Бюджетные правила тика работают только при разделе `## Budgets` в state.md: прогоны 1.1.x (раздела нет) — до
  байта прежние, снимок тика без ключа `budget`.
- observe — превышения P3 `budget_note`, enforce — P1 по видам, off — молчат; каждое превышение — одно событие.
- P0 `read_agent_owns_files` — в любом режиме и без раздела: нарушение контракта доступа.
- `plan` — 2 только при превышении в enforce или без раздела; `postrun-check` — 0 на чистом прогоне и находка на
  каждую внесённую неисправность.

Запуск: python3 -m unittest discover -s tools/tests -p test_budgets.py
"""
import importlib.util
import json
import subprocess
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

from test_scripts import tmpdir

ROOT = Path(__file__).resolve().parents[1].parent
CORE = ROOT / "core" / "scripts"
spec = importlib.util.spec_from_file_location("tick_x1", CORE / "supervisor-tick.py")
st = importlib.util.module_from_spec(spec)
spec.loader.exec_module(st)
NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


class Run:
    """Репозиторий с каталогом прогона общего протокола: две задачи DONE, коммиты, отчёты кодера и приёмки."""

    def __init__(self, case, budgets=None):
        self.repo = tmpdir(case) / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "t")
        git(self.repo, "config", "user.email", "t@t.invalid")
        self.run = self.repo / ".supertick" / "teams" / "feature-x"
        for d in ("runs", "reports", "engine", "state"):
            (self.run / d).mkdir(parents=True)
        self.shas = {}
        for task in ("1", "2"):
            (self.repo / f"f{task}.py").write_text(f"x = {task}\n")
            git(self.repo, "add", "-A")
            git(self.repo, "commit", "-qm", f"task {task}")
            self.shas[task] = git(self.repo, "rev-parse", "--short", "HEAD")
            self.report(f"task{task}-coder-2.md", f"COMMITTED: task #{task}\nCOMMITS: {self.shas[task]}\n")
            self.report(f"accept-task{task}.md", f"ACCEPT: task #{task} — PASS\n")
            self.report(f"review-task{task}-r1.md", f"VERDICT: task #{task} — APPROVED\n")
            for role in ("coder", "review", "accept"):
                self.card(f"task{task}-{role}", task=task, status="done")
        self.plan({"1": ("DONE", 3), "2": ("DONE", 3)})
        state = "# Team State — feature-x\n\n## Phase: VERIFICATION\n## Complexity: SIMPLE\n"
        if budgets:
            state += budgets
        (self.run / "state.md").write_text(state, encoding="utf-8")

    def plan(self, tasks):
        text = "# Plan — feature-x\n\n"
        for task, (status, planned) in tasks.items():
            text += f"## Task {task}: t{task}\nStatus: {status}\nFiles to create/edit: f{task}.py\n"
            text += (f"Planned agents: {planned}\n" if planned is not None else "") + "\n"
        (self.run / "PLAN.md").write_text(text, encoding="utf-8")

    def report(self, name, text):
        (self.run / "reports" / name).write_text(text, encoding="utf-8")

    def card(self, name, **fields):
        data = {"name": name, "role": name.split("-")[-1], "spawnedAt": "2026-11-14T11:00:00+00:00",
                "lastEventAt": "2026-11-14T11:30:00+00:00", **fields}
        (self.run / "runs" / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")

    def tick(self, mode="shared"):
        return st.tick(self.run, root=self.repo, now=NOW, mode=mode)

    def kinds(self, snap):
        return [(a["priority"], a["kind"]) for a in snap["actions"]]


def budgets(mode="observe", a=16, r=3, c=4):
    return f"\n## Budgets\n- mode: {mode}\n- max_agents: {a}  max_review_rounds: {r}  max_concurrent_agents: {c}\n"


class Section(unittest.TestCase):
    def test_defaults_by_complexity_and_flags(self):
        self.assertEqual(st.budgets_section("simple"),
                         "## Budgets\n- mode: observe\n- max_agents: 17  max_review_rounds: 3  max_concurrent_agents: 4\n")
        self.assertIn("mode: enforce", st.budgets_section("COMPLEX", ["--budget=enforce"]))
        self.assertIn("max_agents: 5  max_review_rounds: 2  max_concurrent_agents: 1",
                      st.budgets_section("MEDIUM", ["--budget=enforce,5/2/1"]))
        for bad in (("HUGE", []), ("SIMPLE", ["--budget=loud"])):
            with self.assertRaises(SystemExit):
                st.budgets_section(*bad)

    def test_state_section_round_trip(self):
        r = Run(self, budgets="\n" + st.budgets_section("MEDIUM", ["--budget=off"]))
        self.assertEqual(st.state_budgets(r.run), {"mode": "off", "max_agents": 27, "max_review_rounds": 4,
                                                   "max_concurrent_agents": 9})
        self.assertIsNone(st.state_budgets(Run(self).run))


class TickRules(unittest.TestCase):
    def test_without_the_section_nothing_changes_in_either_mode(self):
        r = Run(self)
        for i in range(20):
            r.card(f"extra-{i}", status="running")
        for mode in ("claude", "shared"):
            snap = r.tick(mode)
            self.assertFalse([k for _, k in r.kinds(snap) if "budget" in k or "exceeded" in k], mode)
            self.assertNotIn("budget", snap, "снимок прогона 1.1.x без бюджетного ключа")

    def test_checkpoint_memory_does_not_leak_into_the_budget_key(self):
        # контрольная точка Claude Code держит своё время в переменной `fired` — бюджет её не трогает
        r = Run(self)
        r.card("worker", task="1", status="running", startedAt="2026-11-14T11:40:00+00:00",
               lastEventAt="2026-11-14T11:44:00+00:00")
        snap = r.tick("claude")
        self.assertIn(("P3", "checkpoint"), r.kinds(snap))
        self.assertNotIn("budget", snap)

    def test_observe_notes_once_and_never_wakes(self):
        r = Run(self, budgets=budgets("observe", a=5))
        first = r.tick()
        self.assertIn(("P3", "budget_note"), r.kinds(first))
        self.assertTrue(any("agent_budget_exceeded" in a["detail"] for a in first["actions"]))
        self.assertFalse([p for p, _ in r.kinds(first) if p in ("P0", "P1", "P2")])
        self.assertNotIn(("P3", "budget_note"), r.kinds(r.tick()), "одно превышение — одно событие")

    def test_enforce_raises_each_kind_as_p1(self):
        r = Run(self, budgets=budgets("enforce", a=5, r=1, c=1))
        r.plan({"1": ("DONE", 2), "2": ("IN_REVIEW(task2-coder)", 3)})
        r.report("review-task2-r1.md", "VERDICT: task #2 — CHANGES REQUESTED\n")
        r.report("review-task2-r2.md", "VERDICT: task #2 — CHANGES REQUESTED\n")
        r.card("task2-coder", task="2", status="running")
        r.card("task2-review", task="2", status="reviewing")
        got = r.kinds(r.tick())
        for kind in ("agent_budget_exceeded", "concurrency_exceeded", "review_rounds_exceeded", "fanout_planned_exceeded"):
            self.assertIn(("P1", kind), got, kind)

    def test_off_is_silent(self):
        r = Run(self, budgets=budgets("off", a=1, r=0, c=0))
        self.assertFalse([k for _, k in r.kinds(r.tick()) if "budget" in k or "exceeded" in k])

    def test_read_agent_with_commits_is_p0_in_any_mode_once(self):
        for state in (None, budgets("off")):
            with self.subTest(budgets=bool(state)):
                r = Run(self, budgets=state)
                r.card("task1-review", task="1", status="done", access="read", report="reports/review-task1-r1.md")
                r.report("review-task1-r1.md", "COMMITTED: task #1\nCOMMITS: abc1234\n")
                self.assertIn(("P0", "read_agent_owns_files"), r.kinds(r.tick()))
                self.assertNotIn(("P0", "read_agent_owns_files"), r.kinds(r.tick()))
        r = Run(self)
        r.card("task1-coder", task="1", status="done", access="write", commits=["abc1234"])
        self.assertNotIn(("P0", "read_agent_owns_files"), r.kinds(r.tick()), "писателю коммиты положены")


class PlanCommand(unittest.TestCase):
    def test_codes_by_mode(self):
        self.assertEqual(st.plan_check(Run(self).run)[0], 2, "нет раздела")
        self.assertEqual(st.plan_check(Run(self, budgets=budgets("observe", a=16)).run)[0], 0)
        code, lines = st.plan_check(Run(self, budgets=budgets("observe", a=5)).run)
        self.assertEqual(code, 0)
        self.assertIn("⚠️", lines[-1])
        self.assertEqual(st.plan_check(Run(self, budgets=budgets("enforce", a=5)).run)[0], 2)
        self.assertEqual(st.plan_check(Run(self, budgets=budgets("off", a=1)).run)[0], 0)

    def test_tasks_without_a_plan_are_listed(self):
        r = Run(self, budgets=budgets())
        r.plan({"1": ("DONE", 3), "2": ("TODO", None)})
        self.assertIn("без planned_agents: task 2", st.plan_check(r.run)[1])

    def test_cli(self):
        r = Run(self, budgets=budgets("enforce", a=5))
        res = subprocess.run([sys.executable, str(CORE / "supervisor-tick.py"), "plan", str(r.run)], capture_output=True, text=True)
        self.assertEqual(res.returncode, 2)
        self.assertIn("PLAN BUDGET: planned_agents 6 из max_agents 5 (enforce)", res.stdout)


class PostrunCheck(unittest.TestCase):
    def test_clean_run_has_no_findings(self):
        r = Run(self, budgets=budgets())
        self.assertEqual(st.postrun_check(r.run, r.repo, NOW), [])
        res = subprocess.run([sys.executable, str(CORE / "supervisor-tick.py"), "postrun-check", str(r.run), "--root", str(r.repo)],
                             capture_output=True, text=True)
        self.assertEqual((res.returncode, res.stdout.strip()), (0, "POSTRUN CHECK: находок нет"))

    def test_each_fault_gives_its_finding(self):
        faults = {
            "no commits line": (lambda r: r.report("task1-coder-2.md", "READY FOR REVIEW: task #1\n"), "DONE без строки COMMITS"),
            "commit missing": (lambda r: r.report("task1-coder-2.md", "COMMITS: deadbeef1\n"), "коммита deadbeef1 нет"),
            "no accept pass": (lambda r: r.report("accept-task2.md", "ACCEPT: task #2 — FAIL\n"), "accept-task2.md с ACCEPT … PASS"),
            "pending open": (lambda r: (r.run / "pending.log").write_text("2026-11-14T11:00:00+00:00 | to x | OPEN\n"), "pending.log: OPEN"),
            "engine unread": (lambda r: [(r.run / "engine" / "task1-review").mkdir(),
                                         (r.run / "engine" / "task1-review" / "001.out").write_text("x"),
                                         (r.run / "engine" / "task1-review" / "001.out.pid").write_text("1"),
                                         (r.run / "engine" / "task1-review" / "001.out.done").write_text("status=done\n")],
                              "результат не забран"),
            "budget not reported": (lambda r: (r.run / "state.md").write_text(
                "## Phase: VERIFICATION\n" + budgets("observe", a=2)), "не записан в VERIFICATION_REPORT.md"),
            "read agent commits": (lambda r: r.card("task1-review", task="1", status="done", access="read", commits=["abc1234"]),
                                   "read_agent_owns_files"),
        }
        for name, (inject, needle) in faults.items():
            with self.subTest(fault=name):
                r = Run(self, budgets=budgets())
                inject(r)
                found = st.postrun_check(r.run, r.repo, NOW)
                self.assertTrue(any(needle in f for f in found), found)

    def test_budget_written_to_the_report_is_not_a_finding(self):
        r = Run(self, budgets=budgets("observe", a=2))
        (r.run / "VERIFICATION_REPORT.md").write_text("Статус: ALL_PASS\nBudgets: agent_budget_exceeded — 6 из 2\n")
        self.assertEqual(st.postrun_check(r.run, r.repo, NOW), [])


class CardAccess(unittest.TestCase):
    def test_shared_cards_get_access_from_the_registry_and_claude_cards_do_not(self):
        run = tmpdir(self)
        script = CORE / "run-state.py"
        for name, role, extra in (("r", "unified-reviewer", ["executor=native"]), ("c", "coder", ["executor=engine"]),
                                  ("cc", "coder", [])):
            subprocess.run([sys.executable, str(script), "new", str(run), name, f"role={role}", "task=1", *extra],
                           check=True, capture_output=True)
        access = {n: json.loads((run / "runs" / f"{n}.json").read_text()).get("access") for n in ("r", "c", "cc")}
        self.assertEqual(access, {"r": "read", "c": "write", "cc": None})
        subprocess.run([sys.executable, str(script), "set", str(run), "c", "commits=abc1234,def5678"], check=True, capture_output=True)
        self.assertEqual(json.loads((run / "runs" / "c.json").read_text())["commits"], ["abc1234", "def5678"])


if __name__ == "__main__":
    unittest.main()
