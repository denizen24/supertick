"""Зеркало state/run.json и validate-run (121-567, X2).

- Тик в режиме общего протокола пишет зеркало атомарно и только при изменении содержимого: ревизия (SHA-256 без
  поля revision) меняется только вместе с содержимым. В режиме Claude Code зеркала нет.
- Валидатор ловит типовые порчи: не JSON, нет ключей и схемы, ревизия не сходится (правили руками), задачи
  расходятся с PLAN.md, исполнители и вызовы с битыми полями, ключи-секреты. postrun-check сообщает о битом зеркале.

Запуск: python3 -m unittest discover -s tools/tests -p test_run_json.py
"""
import json
import subprocess
import sys
import unittest

from test_budgets import CORE, NOW, Run, budgets, st


def mirror(r):
    return json.loads((r.run / "state" / "run.json").read_text(encoding="utf-8"))


def rewrite(r, data):
    (r.run / "state" / "run.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


class Mirror(unittest.TestCase):
    def test_written_in_shared_mode_with_tasks_agents_and_budgets(self):
        r = Run(self, budgets=budgets())
        r.card("task1-coder", task="1", status="done", role="coder", access="write", executor="native")
        r.tick()
        d = mirror(r)
        self.assertEqual((d["schema"], d["phase"], d["complexity"]), (1, "VERIFICATION", "SIMPLE"))
        self.assertEqual(d["budgets"]["max_agents"], 16)
        t1 = next(t for t in d["tasks"] if t["id"] == "1")
        self.assertEqual((t1["status"], t1["planned_agents"], t1["coder"], t1["commits"]), ("DONE", 3, "task1-coder", [r.shas["1"]]))
        coder = next(a for a in d["agents"] if a["name"] == "task1-coder")
        self.assertEqual((coder["access"], coder["engine"]), ("write", "native"))
        self.assertEqual(st.validate_run(r.run), [])

    def test_revision_changes_only_with_content(self):
        r = Run(self)
        r.tick()
        first = mirror(r)["revision"]
        stamp = (r.run / "state" / "run.json").stat().st_mtime_ns
        r.tick()
        self.assertEqual((r.run / "state" / "run.json").stat().st_mtime_ns, stamp, "то же содержимое — файл не переписан")
        r.plan({"1": ("DONE", 3), "2": ("REOPENED(task2-coder)", 3)})
        r.tick()
        self.assertNotEqual(mirror(r)["revision"], first)
        self.assertEqual(st.validate_run(r.run), [])

    def test_no_mirror_in_claude_mode(self):
        r = Run(self)
        r.tick("claude")
        self.assertFalse((r.run / "state" / "run.json").exists())

    def test_engines_and_models_come_from_the_ledger(self):
        r = Run(self)
        r.card("task1-review", task="1", status="done", role="unified-reviewer", executor="engine")
        with (r.run / "ledger.jsonl").open("w") as fh:
            for rec in ({"event": "launch", "role": "task1-review", "label": "001", "engine": "opencode",
                         "model": "opencode/glm-5.3-flash"},
                        {"event": "done", "role": "task1-review", "label": "001", "engine": "opencode"}):
                fh.write(json.dumps(rec) + "\n")
        r.tick()
        d = mirror(r)
        review = next(a for a in d["agents"] if a["name"] == "task1-review")
        self.assertEqual((review["engine"], review["provider"], review["model"]), ("opencode", "opencode", "opencode/glm-5.3-flash"))
        self.assertEqual(d["engines"], [{"role": "task1-review", "label": "001", "status": "done"}])
        self.assertEqual(next(t for t in d["tasks"] if t["id"] == "1")["reviewer"], "task1-review")


class Validator(unittest.TestCase):
    def setUp(self):
        self.r = Run(self)
        self.r.tick()

    def resign(self, data):
        data["revision"] = st.mirror_revision(data)
        return data

    def test_typical_corruptions(self):
        good = mirror(self.r)
        cases = {
            "не JSON": ("{битый", "не JSON"),
            "нет ключа": (self.resign({k: v for k, v in good.items() if k != "engines"}), "нет ключей: engines"),
            "схема": (self.resign({**good, "schema": 2}), "schema 2"),
            "правка руками": ({**good, "phase": "DONE"}, "revision не сходится"),
            "расхождение с PLAN": (self.resign({**good, "tasks": [{**t, "status": "TODO"} if t["id"] == "1" else t
                                                              for t in good["tasks"]]}), "в PLAN.md 'DONE'"),
            "задача пропала": (self.resign({**good, "tasks": [t for t in good["tasks"] if t["id"] != "2"]}), "task 2 есть в PLAN.md"),
            "исполнитель чужой задачи": (self.resign({**good, "agents": good["agents"] + [{"name": "x", "task": "9"}]}),
                                         "задача '9' не из tasks"),
            "битый вызов": (self.resign({**good, "engines": [{"role": "r", "label": "1", "status": "lost"}]}), "engines: битая запись"),
            "секрет": (self.resign({**good, "host": {"api_key": "sk-1"}}), "ключ host.api_key"),
        }
        for name, (data, needle) in cases.items():
            with self.subTest(case=name):
                if isinstance(data, str):
                    (self.r.run / "state" / "run.json").write_text(data, encoding="utf-8")
                else:
                    rewrite(self.r, data)
                problems = st.validate_run(self.r.run)
                self.assertTrue(any(needle in p for p in problems), problems)

    def test_cli_and_postrun_check(self):
        res = subprocess.run([sys.executable, str(CORE / "supervisor-tick.py"), "validate-run", str(self.r.run)],
                             capture_output=True, text=True)
        self.assertEqual((res.returncode, res.stdout.strip()), (0, "RUN JSON: валиден"))
        rewrite(self.r, {**mirror(self.r), "phase": "DONE"})
        res = subprocess.run([sys.executable, str(CORE / "supervisor-tick.py"), "validate-run", str(self.r.run)],
                             capture_output=True, text=True)
        self.assertEqual(res.returncode, 1)
        self.assertTrue(any("state/run.json: revision" in f for f in st.postrun_check(self.r.run, self.r.repo, NOW)))


if __name__ == "__main__":
    unittest.main()
