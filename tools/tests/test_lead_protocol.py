"""Общий протокол ведущего (121-567, P2): hosts/shared/LEAD.md и брифы core/roles/.

Приёмка P2: две задачи на мок-движках проходят только через shell по тексту LEAD.md —
FAIL → правка → PASS → приёмка → DONE; текст не длиннее 600 строк и без имён инструментов среды.

Тест играет ведущего: каждая команда — блок `# lead:<id>` из LEAD.md, взятый как есть, с подставленными
метками. Сам тест только пишет промпт-файлы и строки Status в PLAN.md (это файлы ведущего). Исполнители —
mock-engine.py со сценарием (SUPERTICK_MOCK_ACT): кодер пишет файл и коммитит, рецензент выносит
вердикт по диффу, приёмщик — по диффу коммитов и отчёту ревью. Роли назначены движку `mock` профилем,
среда — kimi.

Запуск: python3 -m unittest discover -s tools/tests   (из корня репозитория)
"""
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

from test_scripts import tmpdir

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
LEAD = REPO_ROOT / "hosts" / "shared" / "LEAD.md"
ROLES = REPO_ROOT / "core" / "roles"
SCRIPTS = REPO_ROOT / "core" / "scripts"
NEUTRALITY = json.loads((REPO_ROOT / "tools" / "core-neutrality.json").read_text(encoding="utf-8"))["patterns"]

BLOCK = re.compile(r"```bash\n\s*# lead:(?P<id>[\w-]+)\n\s*(?P<cmd>.+?)\n\s*```", re.S)
PLACEHOLDER = re.compile(r"<[^\s<>][^<>]*>")


def lead_blocks():
    return {m["id"]: m["cmd"].strip() for m in BLOCK.finditer(LEAD.read_text(encoding="utf-8"))}


ACT = r'''
import json, os, re, subprocess, sys
from pathlib import Path
p = os.environ["MOCK_PROMPT"]; access = os.environ["MOCK_ACCESS"]; sess = os.environ["MOCK_SESSION"]
state_dir = Path(os.environ["SUPERTICK_MOCK_STATE"]); log = state_dir / "calls.jsonl"
brief = re.search(r"ROLE BRIEF FILE: .*/(\S+)\.md", p)
mem_file = state_dir / f"act-{sess}.json"
mem = json.loads(mem_file.read_text()) if mem_file.exists() else {}
role = brief.group(1) if brief else mem.get("role", "?")
mem["role"] = role
with log.open("a") as f:
    f.write(json.dumps({"role": role, "access": access, "session": sess, "first": p.strip().splitlines()[0][:80]}) + "\n")
def out(text):
    mem_file.write_text(json.dumps(mem)); print(text); sys.exit(0)
if role == "coder":
    m = re.search(r"Files to create/edit: (\S+)", p)
    if m:
        mem["file"] = m.group(1); mem["task"] = re.search(r"## Task (\d+)", p).group(1)
    path, task = Path(mem["file"]), mem["task"]
    if "Commit now." in p:
        subprocess.run(["git", "add", "--", str(path)], check=True)
        subprocess.run(["git", "commit", "-q", "-m", f"task {task}", "--", str(path)], check=True)
        sha = subprocess.run(["git", "log", "-1", "--format=%h"], capture_output=True, text=True, check=True).stdout.strip()
        out(f"COMMITTED: task #{task}\nCOMMITS: {sha}\nSUMMARY: done")
    path.parent.mkdir(parents=True, exist_ok=True)
    buggy = task == "1" and "CHANGES REQUESTED" not in p
    path.write_text("value = 1  # BUG\n" if buggy else "value = 2\n")
    out(f"READY FOR REVIEW: task #{task}\nFILES: {path}")
if role == "unified-reviewer":
    if access != "read": out("WRONG ACCESS")
    task = re.search(r"REVIEW: task #(\d+)", p).group(1)
    diff = Path(re.search(r"DIFF: (\S+)", p).group(1)).read_text()
    if "BUG" in diff:
        out(f"VERDICT: task #{task} — CHANGES REQUESTED\nCRITICAL 0 · MAJOR 1 · MINOR 0\n\n### MAJOR\n- [logic] BUG")
    out(f"VERDICT: task #{task} — APPROVED\nCRITICAL 0 · MAJOR 0 · MINOR 0")
if role == "acceptance-checker":
    if access != "read": out("WRONG ACCESS")
    task = re.search(r"ACCEPTANCE of task #(\d+)", p).group(1)
    diff = Path(re.search(r"DIFF FILE: (\S+)", p).group(1)).read_text()
    review = Path(re.search(r"REVIEW REPORT: (\S+)", p).group(1)).read_text().splitlines()[0]
    ok = "value = 2" in diff and "BUG" not in diff and review.endswith("APPROVED")
    out(f"ACCEPT: task #{task} — {'PASS' if ok else 'FAIL'}")
out("UNKNOWN ROLE " + role)
'''


class Lead:
    """Ведущий из теста: подставляет метки в блоки LEAD.md и запускает их через bash."""

    def __init__(self, case):
        self.case = case
        self.blocks = lead_blocks()
        self.home = tmpdir(case)
        self.repo = self.home / "repo"
        self.repo.mkdir()
        self.run_dir = self.repo / ".supertick" / "teams" / "feature-demo"
        state = self.home / "mock"
        state.mkdir()
        act = self.home / "act.py"
        act.write_text(ACT, encoding="utf-8")
        cfg = self.home / "config.json"
        roles = {r: {"engine": "mock"} for r in ("coder", "unified-reviewer", "acceptance-checker")}
        cfg.write_text(json.dumps({"active_profile": "demo", "profiles": {"demo": {
            "lead": {"host": "kimi"}, "roles": roles, "sources": ["kimi"], "installed": ["kimi"]}}}), encoding="utf-8")
        self.env = dict(os.environ, HOME=str(self.home), SUPERTICK_CONFIG=str(cfg), SUPERTICK_MOCK_STATE=str(state),
                        SUPERTICK_MOCK_ACT=f"{sys.executable} {act}", TZ="UTC")
        self.env.pop("CLAUDECODE", None)
        self.state = state
        git = ["git", "-C", str(self.repo)]
        subprocess.run(git + ["init", "-q"], check=True)
        subprocess.run(git + ["config", "user.name", "t"], check=True)
        subprocess.run(git + ["config", "user.email", "t@example.invalid"], check=True)
        (self.repo / "README").write_text("x\n")
        (self.repo / "src").mkdir()
        (self.repo / "src" / "t2.py").write_text("value = 0\n")   # задача 2 правит отслеживаемый файл, задача 1 создаёт новый
        subprocess.run(git + ["add", "README", "src/t2.py"], check=True)
        subprocess.run(git + ["commit", "-qm", "base"], check=True)

    def sh(self, block, extra="", **subs):
        cmd = self.blocks[block]
        subs = {"S": str(SCRIPTS), "RUN": str(self.run_dir), "REPO": str(self.repo), "HOST": "kimi", **subs}
        for key, value in subs.items():
            cmd = cmd.replace(f"<{key}>", str(value))
        left = PLACEHOLDER.findall(cmd)
        self.case.assertEqual(left, [], f"lead:{block}: не подставлено {left}")
        res = subprocess.run(["bash", "-c", cmd + extra], capture_output=True, text=True, env=self.env,
                             cwd=str(self.repo), timeout=120)
        return res

    def plan(self, statuses):
        text = "# Plan — feature-demo\n\n"
        for tid, status in statuses.items():
            text += (f"## Task {tid}: demo {tid}\nStatus: {status}\nBlocked by: none\n"
                     f"Files to create/edit: src/t{tid}.py\n\nAcceptance criteria:\n- src/t{tid}.py has value = 2\n"
                     f"Tooling: python3 -c 'import src.t{tid}'\n\n")
        (self.run_dir / "PLAN.md").write_text(text, encoding="utf-8")

    def task_section(self, tid):
        text = (self.run_dir / "PLAN.md").read_text(encoding="utf-8")
        return re.search(rf"(## Task {tid}:.*?)(?=\n## Task |\Z)", text, re.S).group(1)

    def call(self, role, name, n, report, task, body, resume=False):
        """Карточка → промпт → argv (или resume) → запуск → ожидание → взять; возвращает первые строки отчёта."""
        rc = self.sh("card", NAME=name, **{"роль": role, "id": task, "engine|native": "engine", "срок": "600",
                                           "отчёт": report})
        self.case.assertEqual(rc.returncode, 0, rc.stderr)
        prompt = self.run_dir / "engine" / f"{name}-{n}.prompt.md"
        head = "" if resume else (f"ROLE BRIEF FILE: {ROLES}/{'unified-reviewer' if role == 'second-reviewer' else role}.md\n"
                                  f"RUN DIR: {self.run_dir}\nREPO: {self.repo}\nNAME: {name}\n")
        prompt.write_text(head + f"REPORT: {self.run_dir}/reports/{report}.md\n" + body, encoding="utf-8")
        argv = self.sh("resume" if resume else "argv", NAME=name, n=n, **{"роль": role, "отчёт": report, "id": task})
        self.case.assertIn("argv rc=0", argv.stdout, argv.stderr)
        launch = self.sh("launch", NAME=name, n=n)
        self.case.assertEqual(launch.returncode, 0, launch.stderr)
        ref = None
        for _ in range(10):
            woke = self.sh("wait", extra=" --interval 1 --max 40")
            m = re.search(rf"^P1 engine_result_ready {re.escape(name)}: .*\[(\S+)\.result\.md\]", woke.stdout, re.M)
            if m:
                ref = m.group(1)
                break
            self.case.assertNotIn("P0 ", woke.stdout)
        self.case.assertIsNotNone(ref, f"{name}: результат не пришёл")
        taken = self.sh("take", NAME=name, **{"отчёт": report, "путь .out из ref тика": ref})
        self.case.assertEqual(taken.returncode, 0, taken.stderr)
        return taken.stdout.strip().splitlines()


class LeadText(unittest.TestCase):
    def test_lead_is_at_most_600_lines(self):
        self.assertLessEqual(len(LEAD.read_text(encoding="utf-8").splitlines()), 600)

    def test_no_host_tool_names_or_host_paths(self):
        texts = {LEAD: LEAD.read_text(encoding="utf-8")}
        texts.update({p: p.read_text(encoding="utf-8") for p in ROLES.glob("*.md")})
        for path, text in texts.items():
            for name, pattern in NEUTRALITY.items():
                with self.subTest(file=path.name, pattern=name):
                    self.assertIsNone(re.search(pattern, text), f"{path.name}: {name}")

    def test_every_command_block_is_tagged_and_known(self):
        text = LEAD.read_text(encoding="utf-8")
        self.assertEqual(text.count("```bash"), len(BLOCK.findall(text)), "блок bash без метки # lead:<id>")
        self.assertTrue({"root", "second-run", "host", "resolve", "run-dir", "card", "argv", "launch", "resume",
                         "take", "wait", "tick", "diff", "accept-diff"} <= set(lead_blocks()))

    def test_scripts_named_in_lead_exist(self):
        for script in set(re.findall(r"<S>/([\w.-]+)", LEAD.read_text(encoding="utf-8"))):
            self.assertTrue((SCRIPTS / script).is_file(), script)

    def test_every_role_brief_exists_and_states_its_first_line(self):
        for role in ("coder", "unified-reviewer", "acceptance-checker", "spec-verifier", "ci-verifier", "tech-lead",
                     "architect", "risk-tester", "codebase-researcher", "reference-researcher", "web-researcher",
                     "legacy-scanner"):
            text = (ROLES / f"{role}.md").read_text(encoding="utf-8")
            self.assertIn("ROLE BRIEF FILE:", text, role)
            self.assertLessEqual(len(text.splitlines()), 160, role)


class TwoTasksThroughShell(unittest.TestCase):
    def test_fail_fix_pass_accept_done(self):
        lead = Lead(self)
        for block in ("root", "run-dir"):
            res = lead.sh(block)
            self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("ROOT OK", lead.sh("root").stdout)
        host = json.loads(lead.sh("host").stdout)
        self.assertEqual(host["action"], "line")
        self.assertIn("coder", lead.sh("resolve").stdout)

        lead.plan({"1": "TODO", "2": "TODO"})
        verdicts = {}
        for tid in ("1", "2"):
            coder = f"task{tid}-coder"
            lead.plan({**{t: "DONE" for t in ("1", "2") if t < tid}, tid: f"IN_PROGRESS({coder})",
                       **{t: "TODO" for t in ("1", "2") if t > tid}})
            n, rnd = 1, 1
            first = lead.call("coder", coder, n, f"task{tid}-coder-{n}", tid,
                              f"--- TASK ---\n{lead.task_section(tid)}\n--- END TASK ---\n")
            self.assertEqual(first[0], f"READY FOR REVIEW: task #{tid}")
            while True:
                diff = lead.sh("diff", **{"файлы задачи": f"src/t{tid}.py", "id": tid, "n": rnd})
                self.assertGreater(int(diff.stdout.strip() or 0), 0, diff.stderr)
                review = lead.call("unified-reviewer", f"task{tid}-review", rnd, f"review-task{tid}-r{rnd}", tid,
                                   f"REVIEW: task #{tid}, round {rnd}\n--- TASK ---\n{lead.task_section(tid)}\n--- END TASK ---\n"
                                   f"DIFF: {lead.run_dir}/reports/review-task{tid}-r{rnd}.diff\n", resume=rnd > 1)
                verdicts.setdefault(tid, []).append(review[0])
                if review[0].endswith("APPROVED"):
                    break
                self.assertEqual(review[0], f"VERDICT: task #{tid} — CHANGES REQUESTED")
                n += 1
                again = lead.call("coder", coder, n, f"task{tid}-coder-{n}", tid,
                                  f"CHANGES REQUESTED: task #{tid}. Report: {lead.run_dir}/reports/review-task{tid}-r{rnd}.md\n",
                                  resume=True)
                self.assertEqual(again[0], f"READY FOR REVIEW: task #{tid}")
                rnd += 1
            n += 1
            done = lead.call("coder", coder, n, f"task{tid}-coder-{n}", tid,
                             f"APPROVED: task #{tid}. Report: {lead.run_dir}/reports/review-task{tid}-r{rnd}.md. Commit now.\n",
                             resume=True)
            self.assertEqual(done[0], f"COMMITTED: task #{tid}")
            sha = done[1].split()[1]
            lead.plan({**{t: "DONE" for t in ("1", "2") if t < tid}, tid: f"ACCEPTING({coder})",
                       **{t: "TODO" for t in ("1", "2") if t > tid}})
            adiff = lead.sh("accept-diff", **{"sha …": sha, "файлы задачи": f"src/t{tid}.py", "id": tid})
            self.assertGreater(int(adiff.stdout.strip() or 0), 0, adiff.stderr)
            accept = lead.call("acceptance-checker", f"task{tid}-accept", 1, f"accept-task{tid}", tid,
                               f"ACCEPTANCE of task #{tid}, team feature-demo.\n--- TASK ---\n{lead.task_section(tid)}\n--- END TASK ---\n"
                               f"DIFF FILE: {lead.run_dir}/reports/accept-task{tid}.diff (commits {sha})\n"
                               f"REVIEW REPORT: {lead.run_dir}/reports/review-task{tid}-r{rnd}.md\nAPPROVED DEVIATIONS: none\n")
            self.assertEqual(accept[0], f"ACCEPT: task #{tid} — PASS")
            lead.plan({**{t: "DONE" for t in ("1", "2") if t <= tid}, **{t: "TODO" for t in ("1", "2") if t > tid}})

        # задача 1 прошла FAIL → правка → PASS, задача 2 — с первого круга
        self.assertEqual(verdicts["1"], ["VERDICT: task #1 — CHANGES REQUESTED", "VERDICT: task #1 — APPROVED"])
        self.assertEqual(verdicts["2"], ["VERDICT: task #2 — APPROVED"])
        log = subprocess.run(["git", "-C", str(lead.repo), "log", "--format=%s"], capture_output=True, text=True).stdout.split("\n")
        self.assertEqual(log[:2], ["task 2", "task 1"])
        self.assertEqual((lead.repo / "src" / "t1.py").read_text(), "value = 2\n")
        # продолжение — та же сессия исполнителя, а не новый запуск
        calls = [json.loads(l) for l in (lead.state / "calls.jsonl").read_text().splitlines()]
        self.assertEqual(len([c for c in calls if c["role"] == "coder"]), 5)   # 1: старт, правка, коммит; 2: старт, коммит
        self.assertEqual(len({c["session"] for c in calls if c["role"] == "coder"}), 2)   # по сессии на задачу
        self.assertEqual(len({c["session"] for c in calls if c["role"] == "unified-reviewer"}), 2)
        self.assertTrue({c["access"] for c in calls if c["role"] != "coder"} == {"read"})
        # в конце тик молчит: все карточки закрыты, все результаты взяты
        tick = lead.sh("tick")
        self.assertTrue(tick.stdout.startswith("P4 ok"), tick.stdout)
        for name in ("review-task1-r1.md", "review-task1-r2.md", "review-task2-r1.md", "accept-task1.md", "accept-task2.md"):
            self.assertTrue((lead.run_dir / "reports" / name).is_file(), name)


if __name__ == "__main__":
    unittest.main()
