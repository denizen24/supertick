"""Сборка для хоста (121-567, F2): тождество с 1.1.1, синхронность плагина и нейтральность core/.

Три вещи, на которых держится D23:
1. `build-host.py claude-code` даёт набор, побайтно равный 1.1.1: хеши и бит x по манифесту
   `snapshots/plugin-1.1.1.manifest` (снят с коммита e9736d5:
   `git ls-tree -r e9736d5 plugins/supertick` + sha256 содержимого каждого блоба). Осознанные
   расхождения перечислены в `snapshots/plugin-1.1.1.changes`; устаревшая запись — тоже ошибка;
2. `plugins/supertick/` в git равен свежей сборке: правка в обход `core/` и `hosts/` не проходит;
3. в `core/` нет переменных, путей и инструментов Claude Code, кроме исключений
   `tools/core-neutrality.json` с точными счётчиками строк.

Запуск: python3 -m unittest discover -s tools/tests   (из корня репозитория)
"""
import hashlib
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

from test_scripts import tmpdir

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BUILD = ROOT / "tools" / "build-host.py"
MANIFEST = HERE / "snapshots" / "plugin-1.1.1.manifest"
CHANGES = HERE / "snapshots" / "plugin-1.1.1.changes"
NEUTRALITY = json.loads((ROOT / "tools" / "core-neutrality.json").read_text(encoding="utf-8"))


def build(*args, root=None):
    cmd = [sys.executable, "-I", str(BUILD), *args]
    if root:
        cmd += ["--root", str(root)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60)


def tree_manifest(directory):
    rows = {}
    for p in sorted(Path(directory).rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
            mode = "100755" if p.stat().st_mode & 0o111 else "100644"
            rows[p.relative_to(directory).as_posix()] = (mode, hashlib.sha256(p.read_bytes()).hexdigest())
    return rows


def read_manifest():
    rows = {}
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if line.strip():
            mode, digest, path = line.split(" ", 2)
            rows[path] = (mode, digest)
    return rows


def read_changes():
    return {l.split(None, 1)[0] for l in CHANGES.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.lstrip().startswith("#")}


class IdentityWith111(unittest.TestCase):
    def test_build_equals_1_1_1_except_the_declared_changes(self):
        out = tmpdir(self) / "plugin"
        done = build("claude-code", "--out", str(out))
        self.assertEqual(done.returncode, 0, done.stderr)
        got, want, declared = tree_manifest(out), read_manifest(), read_changes()
        self.assertEqual(len(want), 34, "манифест 1.1.1 — 34 файла")
        differing = {p for p in set(got) | set(want) if got.get(p) != want.get(p)}
        self.assertEqual(differing - declared, set(), "сборка разошлась с 1.1.1 без записи в plugin-1.1.1.changes")
        self.assertEqual(declared - differing, set(), "в plugin-1.1.1.changes есть путь, который с 1.1.1 не расходится")

    def test_committed_plugin_is_the_fresh_build(self):
        checked = build("claude-code", "--check")
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_scripts_keep_the_executable_bit_and_tests_do_not_have_it(self):
        rows = tree_manifest(ROOT / "plugins" / "supertick")
        for path, (mode, _) in rows.items():
            if path.startswith("scripts/") and path.count("/") == 1:
                self.assertEqual(mode, "100755", path)
            elif path.startswith("scripts/tests/"):
                self.assertEqual(mode, "100644", path)


class BuilderBehaviour(unittest.TestCase):
    def fake_repo(self, manifest=None, files=None):
        root = tmpdir(self)
        for rel, text in (files or {"core/a.sh": "echo a\n", "hosts/h/b.md": "b\n"}).items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, encoding="utf-8")
        (root / "core" / "a.sh").chmod(0o755)
        manifest = manifest or {"host": "h", "output": "out/h", "include": [
            {"from": "core", "to": "scripts"}, {"from": "hosts/h/b.md", "to": "b.md"}]}
        (root / "hosts" / "h").mkdir(parents=True, exist_ok=True)
        (root / "hosts" / "h" / "build.json").write_text(json.dumps(manifest), encoding="utf-8")
        return root

    def test_build_is_idempotent_and_sets_modes(self):
        root = self.fake_repo()
        first = build("h", root=root)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("записано 2", first.stdout)
        self.assertTrue((root / "out/h/scripts/a.sh").stat().st_mode & 0o111)
        self.assertFalse((root / "out/h/b.md").stat().st_mode & 0o111)
        self.assertIn("записано 0", build("h", root=root).stdout)

    def test_check_reports_every_kind_of_drift_and_writes_nothing(self):
        root = self.fake_repo()
        build("h", root=root)
        out = root / "out" / "h"
        (out / "b.md").write_text("changed\n", encoding="utf-8")            # отличается
        (out / "scripts" / "a.sh").chmod(0o644)                             # режим
        (out / "stray.txt").write_text("x", encoding="utf-8")               # лишний
        checked = build("h", "--check", root=root)
        self.assertEqual(checked.returncode, 1)
        for line in ("отличается: b.md", "режим (бит x): scripts/a.sh", "лишний: stray.txt"):
            self.assertIn(line, checked.stdout)
        (out / "scripts" / "a.sh").unlink()                                 # нет в выходе
        self.assertIn("нет в выходе: scripts/a.sh", build("h", "--check", root=root).stdout)
        self.assertTrue((out / "stray.txt").exists(), "--check ничего не удаляет")
        fixed = build("h", root=root)
        self.assertEqual(fixed.returncode, 0, fixed.stderr)
        self.assertEqual(build("h", "--check", root=root).returncode, 0)
        self.assertFalse((out / "stray.txt").exists())

    def test_caches_are_neither_built_nor_compared(self):
        root = self.fake_repo(files={"core/a.sh": "a\n", "core/__pycache__/x.pyc": "p", "core/y.pyc": "p", "hosts/h/b.md": "b\n"})
        build("h", root=root)
        self.assertFalse((root / "out/h/scripts/__pycache__").exists())
        (root / "out/h/scripts/__pycache__").mkdir()
        (root / "out/h/scripts/__pycache__/z.pyc").write_text("p", encoding="utf-8")
        self.assertEqual(build("h", "--check", root=root).returncode, 0)

    def test_bad_manifests_are_refused_with_code_2(self):
        cases = {
            "нет источника": {"output": "out/h", "include": [{"from": "core/missing", "to": "x"}]},
            "без `..`": {"output": "out/h", "include": [{"from": "../core", "to": "x"}]},
            "должен быть относительным": {"output": "out/h", "include": [{"from": "/etc", "to": "x"}]},
            "два источника на один": {"output": "out/h", "include": [{"from": "core", "to": "s"}, {"from": "core/a.sh", "to": "s/a.sh"}]},
            "нужны `output`": {"include": [{"from": "core", "to": "x"}]},
        }
        for needle, manifest in cases.items():
            with self.subTest(case=needle):
                done = build("h", root=self.fake_repo(manifest=manifest))
                self.assertEqual(done.returncode, 2, done.stdout)
                self.assertIn(needle, done.stderr)

    def test_a_foreign_nonempty_out_is_refused_and_sources_are_never_the_output(self):
        root = self.fake_repo()
        foreign = tmpdir(self)
        (foreign / "precious.txt").write_text("keep", encoding="utf-8")
        refused = build("h", "--out", str(foreign), root=root)
        self.assertEqual(refused.returncode, 2)
        self.assertTrue((foreign / "precious.txt").exists())
        into_core = build("h", "--out", str(root / "core"), root=root)
        self.assertEqual(into_core.returncode, 2)
        self.assertIn("совпадает с исходниками", into_core.stderr)

    def test_unknown_host_is_a_clear_error(self):
        done = build("nope")
        self.assertEqual(done.returncode, 2)
        self.assertIn("нет манифеста", done.stderr)


class CoreIsHostNeutral(unittest.TestCase):
    def scan(self, base=ROOT):
        patterns = {k: re.compile(v) for k, v in NEUTRALITY["patterns"].items()}
        hits = {}
        for f in sorted((base / "core").rglob("*")):
            if not f.is_file() or "__pycache__" in f.parts or f.suffix == ".pyc":
                continue
            rel = f.relative_to(base).as_posix()
            for line in f.read_text(encoding="utf-8").splitlines():
                for name, rx in patterns.items():
                    if rx.search(line):
                        hits[(rel, name)] = hits.get((rel, name), 0) + 1
        return hits

    def test_core_has_no_claude_code_specifics_beyond_the_declared_exceptions(self):
        allowed = {(e["path"], e["pattern"]): e["lines"] for e in NEUTRALITY["exceptions"]}
        hits = self.scan()
        new = {k: v for k, v in hits.items() if allowed.get(k) != v}
        self.assertEqual(new, {}, "в core/ появилось (или стало иначе) то, чего нет в tools/core-neutrality.json")
        stale = {k: v for k, v in allowed.items() if hits.get(k) != v}
        self.assertEqual(stale, {}, "исключение в core-neutrality.json больше не совпадает с файлом: снизить счётчик или убрать")

    def test_the_two_hard_patterns_have_no_exceptions_at_all(self):
        hard = [e for e in NEUTRALITY["exceptions"] if e["pattern"] in ("claude-env", "claude-home")]
        self.assertEqual(hard, [], "CLAUDE_*/CLAUDECODE и ~/.claude в core/ не допускаются вовсе")

    def test_every_exception_has_a_reason_and_points_at_a_real_file(self):
        for e in NEUTRALITY["exceptions"]:
            self.assertTrue(e["why"].strip(), e)
            self.assertTrue((ROOT / e["path"]).is_file(), e["path"])
            self.assertIn(e["pattern"], NEUTRALITY["patterns"])

    def test_the_scan_really_catches_a_violation(self):
        base = tmpdir(self)
        (base / "core" / "scripts").mkdir(parents=True)
        (base / "core" / "scripts" / "probe.sh").write_text('echo "$CLAUDE_PLUGIN_ROOT" ~/.claude SendMessage\n', encoding="utf-8")
        hits = self.scan(base)
        for name in ("claude-env", "claude-home", "tool-names"):
            self.assertIn(("core/scripts/probe.sh", name), hits)


if __name__ == "__main__":
    unittest.main()
