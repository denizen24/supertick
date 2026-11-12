"""Тик в режиме общего протокола (121-567, P1): `supervisor-tick.py --mode shared` и `supervisor-wait.sh --mode shared`.

Приёмка P1: правила почты молчат; смерть движка, зависший исполнитель и невзятый результат видны. Поведение
без флага держат снапшоты F1 (test_tick_snapshots.py) — здесь только новый режим. Скрипты берутся из
core/scripts (исходник), каталоги строит тот же Fixture, что в тестах тика.

Запуск: python3 -m unittest discover -s tools/tests   (из корня репозитория)
"""
import importlib.util
import json
import os
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORE = HERE.parents[1] / "core" / "scripts"
TICK = CORE / "supervisor-tick.py"
WAIT = CORE / "supervisor-wait.sh"

_spec = importlib.util.spec_from_file_location("core_tick_tests", CORE / "tests" / "test_supervisor_tick.py")
_tests = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_tests)
NOW, Fixture, iso, st = _tests.NOW, _tests.Fixture, _tests.iso, _tests.st
DEAD_PID = 4194303   # выше pid_max по умолчанию: такого процесса нет


def kinds(f, mode="shared", at=NOW):
    return [(a["priority"], a["kind"], a["run"]) for a in st.tick(f.run, f.root, at, mode)["actions"]]


def engine(f, name, state, when=NOW - timedelta(seconds=30), seq="001"):
    """Маркеры run-engine.sh: running (живой pid), dead (pid нет, .done нет), ok/failed (.done), taken."""
    d = f.run / "engine" / name
    d.mkdir(parents=True, exist_ok=True)
    pid = d / f"{seq}.out.pid"
    pid.write_text(str(os.getpid() if state == "running" else DEAD_PID))
    t = when.timestamp()
    os.utime(pid, (t, t))
    if state in ("ok", "failed", "taken"):
        done = d / f"{seq}.out.done"
        done.write_text("status=failed\n" if state == "failed" else "status=ok\n")
        os.utime(done, (t, t))
        (d / f"{seq}.out.result.md").write_text("ok\n")
    if state == "taken":
        (d / f"{seq}.out.taken").write_text("")


def card(f, name, **fields):
    base = {"role": "coder", "task": "3", "startedAt": iso(NOW - timedelta(minutes=5)), "executor": "native"}
    base.update(fields)
    return f.run_card(name, **base)


class MailRulesAreSilent(unittest.TestCase):
    def build(self):
        f = Fixture(self)
        card(f, "task3-c1", startedAt=iso(NOW - timedelta(minutes=50)), lastEventAt=iso(NOW - timedelta(minutes=40)),
             checkAfterSec=300)
        f.mail("lead", "task3-c1", "DONE", "3", "DONE: task 3")
        f.mail("lead", "task3-c1", "QUESTION", "3", "QUESTION: which?")
        f.mail("unified-reviewer", "coder-2", "REVIEW", "2", "REVIEW: task #2")
        f.mail("task3-c1", "unified-reviewer", "VERDICT", "3", "FAIL: fix it")
        letter = next((f.run / "mail" / "task3-c1").iterdir())   # вердикт лежит 10 минут: без флага undelivered_mail
        letter.write_text(letter.read_text().replace(iso(NOW - timedelta(minutes=1)), iso(NOW - timedelta(minutes=10))))
        (f.run / "pending.log").write_text("11:50 → task3-c1: RESEND | OPEN\n", encoding="utf-8")
        return f

    def test_without_flag_the_same_run_is_loud(self):
        got = {k for _, k, _ in kinds(self.build(), mode="claude")}
        self.assertTrue({"deliver_pending", "done_without_review", "answer_needed", "silent_too_long", "undelivered_mail"} <= got, got)

    def test_shared_mode_says_nothing_about_mail_or_silence(self):
        self.assertEqual(kinds(self.build()), [("P4", "ok", "")])

    def test_first_minute_and_checkpoint_are_silent_too(self):
        f = Fixture(self)
        card(f, "task3-c1", startedAt=None, spawnedAt=iso(NOW - timedelta(minutes=10)),
             lastEventAt=iso(NOW - timedelta(minutes=10)))
        self.assertIn("first_minute_silent", {k for _, k, _ in kinds(f, mode="claude")})
        self.assertEqual(kinds(f), [("P4", "ok", "")])


class Engines(unittest.TestCase):
    def test_dead_engine_is_p0(self):
        f = Fixture(self)
        engine(f, "task3-c1", "dead")
        self.assertIn(("P0", "engine_dead", "task3-c1"), kinds(f))

    def test_fresh_result_wakes_the_lead_at_once(self):
        f = Fixture(self)
        engine(f, "task3-r1", "ok")
        self.assertEqual(kinds(f), [("P1", "engine_result_ready", "task3-r1")])
        self.assertEqual(kinds(f, mode="claude"), [("P4", "ok", "")])   # без флага результат ждёт прокси 15 мин

    def test_result_not_taken_for_15_minutes_is_p0(self):
        f = Fixture(self)
        engine(f, "task3-r1", "ok", when=NOW - timedelta(minutes=16))
        self.assertEqual(kinds(f), [("P0", "engine_result_unread", "task3-r1")])

    def test_failed_engine_is_p0(self):
        f = Fixture(self)
        engine(f, "task3-c1", "failed")
        self.assertEqual(kinds(f), [("P0", "engine_failed_unread", "task3-c1")])

    def test_taken_result_is_quiet(self):
        f = Fixture(self)
        engine(f, "task3-c1", "taken")
        self.assertEqual(kinds(f), [("P4", "ok", "")])


class Executors(unittest.TestCase):
    def test_native_executor_past_its_deadline_is_p0(self):
        f = Fixture(self)
        card(f, "task3-c1", startedAt=iso(NOW - timedelta(minutes=61)))
        self.assertEqual(kinds(f), [("P0", "executor_overdue", "task3-c1")])

    def test_card_timeout_overrides_the_default(self):
        f = Fixture(self)
        card(f, "task3-c1", startedAt=iso(NOW - timedelta(minutes=61)), timeoutSec=7200)
        self.assertEqual(kinds(f), [("P4", "ok", "")])
        card(f, "task3-c1", startedAt=iso(NOW - timedelta(minutes=11)), timeoutSec=600)
        self.assertEqual(kinds(f), [("P0", "executor_overdue", "task3-c1")])

    def test_spawned_at_counts_when_started_at_is_missing(self):
        f = Fixture(self)
        card(f, "task3-c1", startedAt=None, spawnedAt=iso(NOW - timedelta(minutes=70)))
        self.assertEqual(kinds(f), [("P0", "executor_overdue", "task3-c1")])

    def test_live_engine_call_keeps_the_executor_in_time(self):
        f = Fixture(self)
        card(f, "task3-c1", executor="engine", startedAt=iso(NOW - timedelta(minutes=70)))
        engine(f, "task3-c1", "running")
        self.assertEqual(kinds(f), [("P4", "ok", "")])

    def test_engine_executor_that_never_started_is_p1(self):
        f = Fixture(self)
        card(f, "task3-c1", executor="engine", startedAt=iso(NOW - timedelta(seconds=90)))
        self.assertEqual(kinds(f), [("P1", "engine_not_started", "task3-c1")])
        card(f, "task3-c1", executor="engine", startedAt=iso(NOW - timedelta(seconds=30)))
        self.assertEqual(kinds(f), [("P4", "ok", "")])

    def test_native_executor_without_engine_is_not_flagged_as_not_started(self):
        f = Fixture(self)
        card(f, "task3-c1", startedAt=iso(NOW - timedelta(minutes=5)))
        self.assertEqual(kinds(f), [("P4", "ok", "")])

    def test_fresh_report_is_p1_and_stale_one_is_ignored(self):
        f = Fixture(self)
        card(f, "task3-r1", role="unified-reviewer", report="reports/task3-review-r1.md")
        report = f.run / "reports" / "task3-review-r1.md"
        report.parent.mkdir()
        report.write_text("VERDICT: PASS\n")
        old = (NOW - timedelta(minutes=30)).timestamp()
        os.utime(report, (old, old))
        self.assertEqual(kinds(f), [("P4", "ok", "")])
        new = (NOW - timedelta(minutes=1)).timestamp()
        os.utime(report, (new, new))
        self.assertEqual(kinds(f), [("P1", "executor_report_ready", "task3-r1")])

    def test_stuck_card_is_p0_and_done_card_is_quiet(self):
        f = Fixture(self)
        card(f, "task3-c1", status="stuck", note="needs a decision")
        card(f, "task2-c1", status="done", startedAt=iso(NOW - timedelta(hours=3)))
        self.assertEqual(kinds(f), [("P0", "stuck", "task3-c1")])

    def test_acceptance_report_still_counts(self):
        f = Fixture(self)
        f.plan({"3": "ACCEPTING"})
        (f.run / "reports").mkdir()
        (f.run / "reports" / "accept-task3.md").write_text("ACCEPT: task 3 PASS\n")
        self.assertEqual(kinds(f), [("P1", "accept_result", "")])


class CommandLine(unittest.TestCase):
    def tick(self, f, *args):
        env = {**os.environ, "TZ": "UTC", "HOME": str(f.root)}
        return subprocess.run([sys.executable, "-I", str(TICK), str(f.run), "--root", str(f.root), "--now", iso(NOW), *args],
                              capture_output=True, text=True, env=env, timeout=60)

    def test_mode_flag_and_memory(self):
        f = Fixture(self)
        engine(f, "task3-r1", "ok")
        res = self.tick(f, "--mode", "shared")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(res.stdout.startswith("P1 engine_result_ready task3-r1: "))
        snap = json.loads((f.run / "state" / "supervisor.json").read_text(encoding="utf-8"))
        self.assertEqual(snap["mode"], "shared")
        self.tick(f)
        snap = json.loads((f.run / "state" / "supervisor.json").read_text(encoding="utf-8"))
        self.assertNotIn("mode", snap)

    def test_unknown_mode_is_refused(self):
        res = self.tick(Fixture(self), "--mode", "chat")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("--mode", res.stderr)


class Wait(unittest.TestCase):
    """supervisor-wait.sh тикает в настоящем времени: маркеры и карточки ставятся от «сейчас»."""

    def wait(self, f, *args):
        return subprocess.run(["bash", str(WAIT), str(f.run), "--root", str(f.root), "--interval", "1", *args],
                              capture_output=True, text=True, timeout=60)

    def test_background_style_wakes_on_a_ready_result(self):
        f = Fixture(self)
        engine(f, "task3-r1", "ok", when=datetime.now(timezone.utc))
        res = self.wait(f, "--mode", "shared", "--max", "30")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("WAKE: есть действие уровня P1 или выше", res.stdout)
        self.assertIn("engine_result_ready task3-r1", res.stdout)

    def test_foreground_call_ends_by_max_with_a_planned_check(self):
        f = Fixture(self)
        now = datetime.now(timezone.utc)
        f.run_card("task3-c1", startedAt=iso(now), lastEventAt=iso(now), executor="native")   # свежий исполнитель, тихо
        res = self.wait(f, "--mode", "shared", "--max", "1")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("WAKE: истёк максимум ожидания 1 с — плановая проверка", res.stdout)

    def test_same_run_without_mode_does_not_wake_on_the_result(self):
        f = Fixture(self)
        engine(f, "task3-r1", "ok", when=datetime.now(timezone.utc))
        res = self.wait(f, "--max", "1")
        self.assertIn("истёк максимум", res.stdout)

    def test_bad_mode_is_refused(self):
        res = self.wait(Fixture(self), "--mode", "chat")
        self.assertEqual(res.returncode, 2)


if __name__ == "__main__":
    unittest.main()
