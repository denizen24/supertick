"""Правило busy на macOS (121-567, X3): процессы из `ps`, cwd из `lsof`; сравнение путей через resolve.

CI на macOS (14.11.2026) упал на busy-тесте: корень `/var/folders/…` после resolve — `/private/var/folders/…`, а cwd
процесса сравнивался как есть. Вывод `ps`/`lsof` здесь — образцы, команды не запускаются; на Linux правило по-прежнему
читает /proc (снапшоты тика не меняются).

Запуск: python3 -m unittest discover -s tools/tests -p test_busy_macos.py
"""
import importlib.util
import os
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from test_scripts import tmpdir

ROOT = Path(__file__).resolve().parents[1].parent
spec = importlib.util.spec_from_file_location("tick_x3", ROOT / "core" / "scripts" / "supervisor-tick.py")
st = importlib.util.module_from_spec(spec)
spec.loader.exec_module(st)

PS = """  101 Sat Nov 14 11:55:00 2026     /usr/local/bin/python3 -m pytest tests/test_pay.py
  102 Sat Nov 14 11:58:30 2026     node /repo/node_modules/.bin/jest --watch=false
  103 Sat Nov 14 09:00:00 2026     /bin/zsh -l
  104 Sat Nov 14 11:59:00 2026     vim notes.md
"""
LSOF = "p101\nfcwd\nn{cwd101}\np102\nfcwd\nn/elsewhere\n"


def runner(calls, cwd101):
    def run(argv):
        calls.append(argv)
        out = PS if argv[0] == "ps" else LSOF.format(cwd101=cwd101)
        return types.SimpleNamespace(stdout=out, returncode=0)
    return run


class MacProcesses(unittest.TestCase):
    def test_only_test_and_build_processes_get_a_cwd_lookup(self):
        calls = []
        procs = st.mac_processes(runner(calls, "/repo/backend"))
        self.assertEqual(calls[1][:5], ["lsof", "-a", "-d", "cwd", "-p"])
        self.assertEqual(sorted(calls[1][5].split(",")), ["101", "102"], "zsh и vim — не кандидаты")
        by_pid = {p["pid"]: p for p in procs}
        self.assertEqual(by_pid[101]["cwd"], "/repo/backend")
        self.assertEqual(by_pid[101]["argv"][:3], ["/usr/local/bin/python3", "-m", "pytest"])
        self.assertEqual(by_pid[101]["started"].replace(tzinfo=None), datetime(2026, 11, 14, 11, 55))
        self.assertEqual(by_pid[102]["cwd"], "/elsewhere")

    def test_busy_on_macos_uses_them(self):
        root = tmpdir(self)
        (root / "backend").mkdir()
        procs = st.mac_processes(runner([], str(root / "backend")))
        saved = st.list_processes
        st.list_processes = lambda: procs
        self.addCleanup(setattr, st, "list_processes", saved)
        now = datetime(2026, 11, 14, 12, 0).astimezone()
        self.assertIn("pytest", st.busy_command(root, now, 1800))
        self.assertIsNone(st.busy_command(root, now, 60), "тест старше окна не спасает")

    def test_no_lsof_turns_the_rule_off_with_a_note(self):
        saved_which = __import__("shutil").which
        import shutil
        shutil.which = lambda name, *a, **k: None if name == "lsof" else saved_which(name, *a, **k)
        self.addCleanup(setattr, shutil, "which", saved_which)
        st.BUSY_NOTE.clear()
        self.assertEqual(st.mac_processes(), [])
        self.assertIn("нет lsof", st.BUSY_NOTE["busy"])


class PathsResolve(unittest.TestCase):
    def test_cwd_through_a_symlink_matches_the_resolved_root(self):
        # /var → /private/var на macOS: корень и cwd процесса записаны разными путями к одному каталогу
        real = tmpdir(self) / "private-var"
        (real / "repo" / "backend").mkdir(parents=True)
        link = real.parent / "var"
        os.symlink(real, link)
        saved = st.list_processes
        now = datetime(2026, 11, 14, 12, 0).astimezone()
        st.list_processes = lambda: [{"pid": 1, "cwd": str(link / "repo" / "backend"), "argv": ["pytest"],
                                      "started": now - timedelta(minutes=2)}]
        self.addCleanup(setattr, st, "list_processes", saved)
        self.assertEqual(st.busy_command(real / "repo", now, 600), "pytest")


class DoctorLine(unittest.TestCase):
    def test_doctor_names_the_source_or_why_the_rule_is_off(self):
        spec = importlib.util.spec_from_file_location("doctor_x3", ROOT / "core" / "scripts" / "supertick-doctor.py")
        doc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(doc)
        nowhere = tmpdir(self) / "no-proc"
        self.assertEqual(doc.busy_rule("linux", proc=ROOT), "busy: /proc")
        self.assertEqual(doc.busy_rule("darwin", proc=nowhere, which=lambda n: "/usr/sbin/lsof"), "busy: ps и lsof")
        self.assertIn("нет lsof", doc.busy_rule("darwin", proc=nowhere, which=lambda n: None))
        self.assertIn("freebsd", doc.busy_rule("freebsd", proc=nowhere))


if __name__ == "__main__":
    unittest.main()
