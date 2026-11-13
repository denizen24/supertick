"""supervisor-wait.sh в режиме shared: у прогона одно ожидание (121-567, V1, прогон R2).

В R2 ведущий Kimi оставил забытое фоновое ожидание, и `kimi -p` не вышел до таймаута: сводки не было.
Теперь новое ожидание того же прогона гасит прежнее, `--stop` гасит живое, ожидания других прогонов
не трогаются, а режим Claude Code (без --mode shared) ведёт себя как в 1.1.1. Ожидания ищутся по
командной строке, а не по pid-файлу: файл терялся в гонке, и во втором прогоне R2 ожидание снова
пережило `--stop`.
"""
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

WAIT = Path(__file__).resolve().parents[2] / "core" / "scripts" / "supervisor-wait.sh"


def start(run, *extra):
    return subprocess.Popen(["bash", str(WAIT), str(run), "--interval", "1", "--max", "120", *extra],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def gone(proc, timeout=10):
    try:
        proc.wait(timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        return False


class SingleWait(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run_dir = Path(self.tmp.name) / ".claude" / "teams" / "r"
        (self.run_dir / "state").mkdir(parents=True)
        (self.run_dir / "PLAN.md").write_text("# Plan\n", encoding="utf-8")
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
                p.wait()

    def spawn(self, *extra):
        p = start(self.run_dir, *extra)
        self.procs.append(p)
        time.sleep(1.5)
        return p

    def test_new_shared_wait_replaces_the_old_one(self):
        first = self.spawn("--mode", "shared")
        second = self.spawn("--mode", "shared")
        self.assertTrue(gone(first), "прежнее ожидание живо")
        self.assertEqual(first.returncode, 0)
        self.assertNotIn("WAKE", first.stdout.read(), "погашенное ожидание не должно будить строкой WAKE")
        self.assertIsNone(second.poll())

    def test_chain_of_replacements_leaves_exactly_one(self):
        """Гонка из R2: три ожидания подряд — живым остаётся только последнее, и `--stop` гасит его."""
        waits = [self.spawn("--mode", "shared") for _ in range(3)]
        self.assertTrue(all(gone(w) for w in waits[:2]))
        self.assertIsNone(waits[2].poll())
        subprocess.run(["bash", str(WAIT), str(self.run_dir), "--stop"], capture_output=True, text=True, timeout=30)
        self.assertTrue(gone(waits[2]))

    def test_wrapped_in_bash_c_like_a_host_does(self):
        """Среда запускает команду через `bash -c` — обёртку своего ожидания скрипт не гасит."""
        cmd = f"cd / && bash {WAIT} {self.run_dir} --mode shared --interval 1 --max 120"
        first = subprocess.Popen(["bash", "-c", cmd], stdout=subprocess.PIPE, text=True)
        self.procs.append(first)
        time.sleep(1.5)
        second = subprocess.Popen(["bash", "-c", cmd], stdout=subprocess.PIPE, text=True)
        self.procs.append(second)
        time.sleep(2)
        self.assertTrue(gone(first))
        self.assertIsNone(second.poll(), "новое ожидание погасило собственную обёртку")

    def test_stop_kills_the_live_wait_and_clears_pid(self):
        wait = self.spawn("--mode", "shared")
        out = subprocess.run(["bash", str(WAIT), str(self.run_dir), "--stop"], capture_output=True, text=True, timeout=30)
        self.assertIn(f"ожидание {wait.pid} остановлено", out.stdout)
        self.assertTrue(gone(wait))
        again = subprocess.run(["bash", str(WAIT), str(self.run_dir), "--stop"], capture_output=True, text=True, timeout=30)
        self.assertIn("живого ожидания нет", again.stdout)

    def test_claude_mode_keeps_1_1_1_behaviour(self):
        first = self.spawn()
        second = self.spawn()
        self.assertIsNone(first.poll(), "без --mode shared ожидания не гасят друг друга (как в 1.1.1)")
        self.assertIsNone(second.poll())

    def test_wait_of_another_run_is_not_touched(self):
        other = self.run_dir.parent / "r2"
        (other / "state").mkdir(parents=True)
        (other / "PLAN.md").write_text("# Plan\n", encoding="utf-8")
        theirs = start(other, "--mode", "shared")
        self.procs.append(theirs)
        time.sleep(1.5)
        self.spawn("--mode", "shared")
        subprocess.run(["bash", str(WAIT), str(self.run_dir), "--stop"], capture_output=True, text=True, timeout=30)
        self.assertIsNone(theirs.poll(), "ожидание другого прогона погашено")


if __name__ == "__main__":
    unittest.main()
