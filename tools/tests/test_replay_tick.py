"""replay_tick.py (121-567, V1, приёмка п. 3): сравнение тика 2.0 с 1.1.1 на срезах прошлых прогонов.

Проверяется сам инструмент: одинаковые тики дают ноль расхождений и непустой счёт событий (иначе
сравнение пустое — так было с первой версией на конечных каталогах), подложенное событие ловится,
а срез по времени не показывает письма и строки ledger из будущего.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
TOOL = REPO / "tools" / "replay_tick.py"
NEW_TICK = REPO / "core" / "scripts" / "supervisor-tick.py"

_spec = importlib.util.spec_from_file_location("replay_tick", TOOL)
rt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rt)

T0 = datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)


def at(path, when):
    os.utime(path, (when.timestamp(), when.timestamp()))


def build_run(root):
    """Прогон: задача IN_PROGRESS, письмо ведущему через 10 минут, движок с .done через 20."""
    run = root / ".claude" / "teams" / "feature-r"
    for d in ("runs", "mail/lead", "engine/researcher", "state"):
        (run / d).mkdir(parents=True)
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    (run / "PLAN.md").write_text("# Plan\n\n## Task 1: x\nStatus: IN_PROGRESS(coder-1)\nBlocked by: none\n"
                                 "Files to create/edit: src/a.py\n", encoding="utf-8")
    (run / "state.md").write_text("## Phase: IMPLEMENTATION\n", encoding="utf-8")
    letter = run / "mail" / "lead" / "20261101T101000_coder-1_QUESTION_task1.md"
    letter.write_text(f"from: coder-1\nkind: QUESTION\ntask: 1\nts: {(T0 + timedelta(minutes=10)).isoformat()}\n\nкак?\n",
                      encoding="utf-8")
    at(letter, T0 + timedelta(minutes=10))
    out = run / "engine" / "researcher" / "001.out"
    out.write_text("ответ\n", encoding="utf-8")
    at(out, T0 + timedelta(minutes=20))
    done = run / "engine" / "researcher" / "001.done"
    done.write_text("0\n", encoding="utf-8")
    at(done, T0 + timedelta(minutes=20))
    ledger = run / "ledger.jsonl"
    ledger.write_text(json.dumps({"ts": T0.isoformat(), "event": "launch", "role": "researcher"}) + "\n" +
                      json.dumps({"ts": (T0 + timedelta(minutes=20)).isoformat(), "event": "done", "role": "researcher"}) + "\n",
                      encoding="utf-8")
    at(ledger, T0 + timedelta(minutes=20))
    for f in (run / "PLAN.md", run / "state.md"):
        at(f, T0)
    return run


class Slice(unittest.TestCase):
    def test_future_files_and_ledger_lines_are_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = build_run(Path(tmp) / "repo")
            dest = Path(tmp) / "slice"
            (dest / "state").mkdir(parents=True)
            rt.slice_at(run, dest, T0 + timedelta(minutes=5))
            self.assertTrue((dest / "PLAN.md").is_file())                 # изменяемые — всегда
            self.assertFalse(any((dest / "mail" / "lead").glob("*.md")) if (dest / "mail" / "lead").exists() else False)
            self.assertFalse((dest / "engine" / "researcher" / "001.done").exists())
            self.assertEqual(len((dest / "ledger.jsonl").read_text(encoding="utf-8").splitlines()), 1)
            rt.slice_at(run, dest, T0 + timedelta(minutes=25))
            self.assertTrue((dest / "engine" / "researcher" / "001.done").exists())
            self.assertEqual(len((dest / "ledger.jsonl").read_text(encoding="utf-8").splitlines()), 2)


class Compare(unittest.TestCase):
    def run_tool(self, tmp, new):
        run = build_run(Path(tmp) / "repo")
        report = Path(tmp) / "report.md"
        proc = subprocess.run([sys.executable, str(TOOL), str(run), "--old", str(NEW_TICK), "--new", str(new),
                               "--step", "300", "--tail", "30", "--report", str(report)],
                              capture_output=True, text=True, timeout=300)
        return proc, report.read_text(encoding="utf-8")

    def test_same_tick_gives_zero_new_and_nonempty_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc, report = self.run_tool(tmp, NEW_TICK)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("новых у 2.0: 0", report)
            events = int(report.split("Событий P0–P2 у 1.1.1 всего ")[1].split(",")[0])
            self.assertGreater(events, 0, "сравнение пустое: тики не выдали ни одного события")

    def test_extra_event_in_new_tick_is_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            patched = Path(tmp) / "tick-patched.py"
            src = NEW_TICK.read_text(encoding="utf-8")
            marker = "    calls = engine_calls(run_dir, now)\n"
            self.assertIn(marker, src)
            patched.write_text(src.replace(marker, marker + '    add("P1", "planted", detail="подложено")\n', 1),
                               encoding="utf-8")
            proc, report = self.run_tool(tmp, patched)
            self.assertEqual(proc.returncode, 1, proc.stdout)
            self.assertIn("planted", report)


if __name__ == "__main__":
    unittest.main()
