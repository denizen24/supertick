"""Снапшоты поведения тика 1.1.1 (121-567, F1): тот же вывод CLI на тех же каталогах — до байта.

Зачем: тесты `plugins/supertick/scripts/tests` проверяют правила по одному, а 2.0 переложит тик в
`core/` и добавит режим общего протокола. Эти снапшоты ловят любое расхождение вывода без флага:
текст строк, порядок, приоритеты и то, что тик запоминает между вызовами (`firstMinute`, `checkpoints`).

Каждый сценарий — один или несколько вызовов `supervisor-tick.py <run> --root <root> --now <ISO>`
подряд над одним каталогом прогона; в снапшот идёт вывод каждого вызова и память тика после него.
Каталоги строит тот же `Fixture`, что и в тестах плагина. Эталоны — `snapshots/tick/<сценарий>.txt`.
Обновить после осознанной правки поведения (из корня репозитория):

    SNAPSHOT_UPDATE=1 python3 -m unittest discover -s tools/tests -p test_tick_snapshots.py

В CI переменной нет: отсутствующий эталон — это провал, а не молчаливое создание.
"""
import importlib.util
import json
import os
import subprocess
import sys
import unittest
from datetime import timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parents[1] / "plugins" / "supertick" / "scripts"
TICK = PLUGIN / "supervisor-tick.py"
SNAPSHOTS = HERE / "snapshots" / "tick"
UPDATE = os.environ.get("SNAPSHOT_UPDATE") == "1"
MEMORY_KEYS = ("phase", "tasks", "runs", "firstMinute", "checkpoints")

_spec = importlib.util.spec_from_file_location("plugin_tick_tests", PLUGIN / "tests" / "test_supervisor_tick.py")
_tests = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_tests)
NOW, Fixture, iso = _tests.NOW, _tests.Fixture, _tests.iso


def run_tick(f, at=NOW):
    """Один вызов CLI в зафиксированном времени и поясе; возвращает текст для снапшота."""
    env = {**os.environ, "TZ": "UTC", "HOME": str(f.root)}
    proc = subprocess.run(
        [sys.executable, "-I", str(TICK), str(f.run), "--root", str(f.root), "--now", iso(at)],
        capture_output=True, text=True, timeout=60, env=env)
    assert proc.returncode == 0, proc.stderr
    memory = json.loads((f.run / "state" / "supervisor.json").read_text(encoding="utf-8"))
    kept = {k: memory[k] for k in MEMORY_KEYS}
    text = f"$ tick --now {iso(at)}\n{proc.stdout}--- memory\n{json.dumps(kept, ensure_ascii=False, sort_keys=True, indent=1)}\n"
    # временный каталог каждый раз другой; длинный путь первым: на macOS /private/var/… содержит /var/…
    for root in sorted({str(f.root), str(f.root.resolve())}, key=len, reverse=True):
        text = text.replace(root, "<ROOT>")
    return text


class TickSnapshots(unittest.TestCase):
    def check(self, name, text):
        path = SNAPSHOTS / f"{name}.txt"
        if UPDATE:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            return
        self.assertTrue(path.exists(), f"нет эталона {path.name}: SNAPSHOT_UPDATE=1 создаст его осознанно")
        self.assertEqual(text, path.read_text(encoding="utf-8"), f"вывод тика разошёлся с эталоном {path.name}")

    def test_quiet_run(self):
        f = Fixture(self)
        f.run_card("coder-3")
        self.check("quiet_run", run_tick(f))

    def test_done_waiting_for_acceptance(self):
        f = Fixture(self)
        f.run_card("coder-3", status="done")
        f.mail("unified-reviewer", "coder-3", "REVIEW", "#3", "REVIEW: task #3. Files: src/t3.py")
        f.mail("lead", "coder-3", "DONE", "3", "DONE: task 3\nSUMMARY: works")
        first = run_tick(f)
        f.plan({"3": "DONE"})
        self.check("done_waiting_for_acceptance", first + run_tick(f))

    def test_done_without_review_letter(self):
        f = Fixture(self)
        f.run_card("coder-3", status="done")
        f.mail("unified-reviewer", "coder-2", "REVIEW", "2", "REVIEW: task #2. Files: src/t2.py")
        f.mail("lead", "coder-3", "DONE", "3", "DONE: task 3")
        self.check("done_without_review_letter", run_tick(f))

    def test_stuck_letter_then_ack(self):
        f = Fixture(self)
        f.run_card("coder-3")
        name = f.mail("lead", "coder-3", "STUCK", "3", "STUCK: task 3. Commit failed")
        first = run_tick(f)
        subprocess.run([sys.executable, "-I", str(TICK), "ack", str(f.run), name], check=True, timeout=60)
        self.check("stuck_letter_then_ack", first + run_tick(f))

    def test_question_needs_an_answer(self):
        f = Fixture(self)
        f.run_card("coder-3")
        f.mail("lead", "coder-3", "QUESTION", "3", "QUESTION: task 3. Which endpoint?")
        self.check("question_needs_an_answer", run_tick(f))

    def test_first_minute_alarms_sound_once(self):
        f = Fixture(self)
        f.run_card("coder-3", spawnedAt=iso(NOW - timedelta(seconds=90)), lastEventAt=iso(NOW - timedelta(seconds=90)))
        out = run_tick(f) + run_tick(f)
        later = NOW + timedelta(seconds=110)
        out += run_tick(f, later) + run_tick(f, later)
        self.check("first_minute_alarms_sound_once", out)

    def test_checkpoint_then_silence_too_long(self):
        f = Fixture(self)
        f.run_card("coder-3", spawnedAt=iso(NOW - timedelta(minutes=50)), lastEventAt=iso(NOW - timedelta(minutes=20)))
        out = run_tick(f) + run_tick(f)
        f.run_card("coder-3", spawnedAt=iso(NOW - timedelta(minutes=50)), lastEventAt=iso(NOW - timedelta(minutes=40)))
        self.check("checkpoint_then_silence_too_long", out + run_tick(f))

    def test_stuck_status_in_the_run_card(self):
        f = Fixture(self)
        f.run_card("coder-3", status="stuck", note="commit hook")
        self.check("stuck_status_in_the_run_card", run_tick(f))

    def test_engine_calls_dead_failed_unread(self):
        f = Fixture(self)
        f.run_card("coder-3")
        base = f.run / "engine" / "coder-3"
        base.mkdir(parents=True)
        (base / "001.out.pid").write_text("999999999")                       # процесс исчез, маркера нет
        (base / "002.out.pid").write_text("999999999")                       # упал, отчёт не забран
        (base / "002.out.done").write_text("status=failed\nexit=1\n")
        (base / "003.out.pid").write_text("999999999")                       # готов давно, никто не взял
        (base / "003.out.done").write_text("status=done\n")
        old = (NOW - timedelta(minutes=30)).timestamp()
        os.utime(base / "003.out.done", (old, old))
        (base / "004.out.pid").write_text("999999999")                       # готов и забран: тишина
        (base / "004.out.done").write_text("status=done\n")
        (base / "004.out.taken").write_text("")
        self.check("engine_calls_dead_failed_unread", run_tick(f))

    def test_open_pending_copy(self):
        f = Fixture(self)
        f.run_card("coder-3")
        stamp = (NOW - timedelta(minutes=10)).strftime("%H:%M")
        (f.run / "pending.log").write_text(
            f"{stamp} coder-3 -> unified-reviewer | REVIEW: task 3 | OPEN\n11:50 coder-3 -> unified-reviewer | REVIEW: task 2 | DELIVERED\n",
            encoding="utf-8")
        self.check("open_pending_copy", run_tick(f))

    def test_verdict_the_coder_never_took(self):
        f = Fixture(self)
        f.run_card("coder-3", status="in_review", startedAt=iso(NOW - timedelta(minutes=9)),
                   lastEventAt=iso(NOW - timedelta(minutes=6)))
        (f.run / "mail" / "coder-3").mkdir(parents=True, exist_ok=True)
        (f.run / "mail" / "coder-3" / "v.md").write_text(
            f"from: unified-reviewer\nkind: VERDICT\ntask: 3\nts: {iso(NOW - timedelta(minutes=3))}\n\nAPPROVED, 0 issues\n",
            encoding="utf-8")
        self.check("verdict_the_coder_never_took", run_tick(f))


if __name__ == "__main__":
    unittest.main()
