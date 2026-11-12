"""Адаптер Kimi (121-567, H1): сборка `build-host.py kimi`, типы агентов из брифов, установщик.

Живой прогон из Kimi — в docs/spikes/h1-kimi-live.md; здесь то, что проверяется без модели.

Запуск: python3 -m unittest discover -s tools/tests   (из корня репозитория)
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_scripts import tmpdir

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BUILD = ROOT / "tools" / "build-host.py"
INSTALL = ROOT / "tools" / "install-kimi.sh"
MANIFEST = json.loads((ROOT / "hosts" / "kimi" / "build.json").read_text(encoding="utf-8"))
WRITE_TOOLS = {"Write", "Edit"}


def front(text):
    m = re.match(r"---\n(.*?)\n---\n\n", text, re.S)
    assert m, "нет YAML-шапки"
    head, tools, key = {}, [], None
    for line in m.group(1).splitlines():
        if line.startswith("  - "):
            tools.append(line[4:])
        else:
            key, _, value = line.partition(": ")
            head[key] = json.loads(value) if value else None
    head["tools"] = tools
    return head, text[m.end():]


class KimiBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name) / "kimi"
        res = subprocess.run([sys.executable, str(BUILD), "kimi", "--out", str(cls.out)], capture_output=True, text=True)
        assert res.returncode == 0, res.stderr

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_manifest_and_layout(self):
        manifest = json.loads((self.out / "kimi.plugin.json").read_text(encoding="utf-8"))
        self.assertEqual((manifest["name"], manifest["skills"], manifest["agents"]), ("supertick", "./skills/", "./agents/"))
        for rel in ("LEAD.md", "primitives.md", "skills/team-feature/SKILL.md", "skills/setup/SKILL.md",
                    "scripts/supervisor-tick.py", "scripts/engine-config.py", "roles/coder.md"):
            self.assertTrue((self.out / rel).is_file(), rel)

    def test_script_tests_are_not_shipped_and_bits_kept(self):
        self.assertFalse((self.out / "scripts" / "tests").exists())
        self.assertTrue(os.access(self.out / "scripts" / "supervisor-tick.py", os.X_OK))
        self.assertTrue(os.access(self.out / "scripts" / "supervisor-wait.sh", os.X_OK))

    def test_every_agent_is_its_role_brief_with_a_header(self):
        agents = sorted((self.out / "agents").glob("*.md"))
        self.assertEqual(len(agents), 11)
        for path in agents:
            with self.subTest(agent=path.name):
                head, body = front(path.read_text(encoding="utf-8"))
                role = path.stem.removeprefix("supertick-")
                self.assertEqual(head["name"], path.stem)
                self.assertIn("Запускает только ведущий supertick", head["description"])
                self.assertEqual(body, (ROOT / "core" / "roles" / f"{role}.md").read_text(encoding="utf-8"))

    def test_read_roles_cannot_write(self):
        for role in ("unified-reviewer", "tech-lead", "architect", "codebase-researcher", "reference-researcher",
                     "legacy-scanner", "acceptance-checker", "spec-verifier"):
            head, _ = front((self.out / "agents" / f"supertick-{role}.md").read_text(encoding="utf-8"))
            self.assertFalse(WRITE_TOOLS & set(head["tools"]), role)
        head, _ = front((self.out / "agents" / "supertick-coder.md").read_text(encoding="utf-8"))
        self.assertTrue({"Bash", "Write", "Edit"} <= set(head["tools"]))

    def test_primitives_name_every_agent_type_and_fit_80_lines(self):
        text = (self.out / "primitives.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(text.splitlines()), 80)
        for path in (self.out / "agents").glob("*.md"):
            self.assertIn(path.stem, text)

    def test_skill_points_to_lead_and_primitives(self):
        text = (self.out / "skills" / "team-feature" / "SKILL.md").read_text(encoding="utf-8")
        for needle in ("LEAD.md", "primitives.md", "KIMI_SKILL_DIR", "SUPERTICK_ROOT", "kimi", "$ARGUMENTS"):
            self.assertIn(needle, text)

    def test_claude_code_build_is_untouched(self):
        res = subprocess.run([sys.executable, str(BUILD), "claude-code", "--check"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)


class Frontmatter(unittest.TestCase):
    def manifest(self, root, entry):
        (root / "hosts" / "x").mkdir(parents=True)
        (root / "src.md").write_text("тело\n", encoding="utf-8")
        (root / "hosts" / "x" / "build.json").write_text(json.dumps({"output": "out", "include": [entry]}), encoding="utf-8")

    def run_build(self, root):
        return subprocess.run([sys.executable, str(BUILD), "x", "--root", str(root)], capture_output=True, text=True)

    def test_header_and_check(self):
        root = tmpdir(self)
        self.manifest(root, {"from": "src.md", "to": "a.md", "frontmatter": {"name": "n", "tools": ["Read"]}})
        self.assertEqual(self.run_build(root).returncode, 0)
        self.assertEqual((root / "out" / "a.md").read_text(encoding="utf-8"), '---\nname: "n"\ntools:\n  - Read\n---\n\nтело\n')
        check = subprocess.run([sys.executable, str(BUILD), "x", "--root", str(root), "--check"], capture_output=True, text=True)
        self.assertEqual(check.returncode, 0, check.stdout)
        (root / "out" / "a.md").write_text("тело\n", encoding="utf-8")   # шапку потеряли — check видит
        check = subprocess.run([sys.executable, str(BUILD), "x", "--root", str(root), "--check"], capture_output=True, text=True)
        self.assertEqual(check.returncode, 1)

    def test_bad_frontmatter_is_refused(self):
        root = tmpdir(self)
        self.manifest(root, {"from": "src.md", "to": "a.md", "frontmatter": {"tools": [1]}})
        self.assertEqual(self.run_build(root).returncode, 2)

    def test_exclude_skips_named_parts(self):
        root = tmpdir(self)
        (root / "hosts" / "x").mkdir(parents=True)
        (root / "d" / "tests").mkdir(parents=True)
        (root / "d" / "a.py").write_text("a")
        (root / "d" / "tests" / "t.py").write_text("t")
        (root / "hosts" / "x" / "build.json").write_text(json.dumps(
            {"output": "out", "include": [{"from": "d", "to": "d", "exclude": ["tests"]}]}), encoding="utf-8")
        self.assertEqual(self.run_build(root).returncode, 0)
        self.assertEqual(sorted(p.name for p in (root / "out").rglob("*") if p.is_file()), ["a.py"])


class Installer(unittest.TestCase):
    def run_install(self, home, *args):
        return subprocess.run(["bash", str(INSTALL), "--kimi-home", str(home), *args], capture_output=True, text=True, timeout=120)

    def test_install_keeps_other_plugins_backs_up_and_uninstalls(self):
        home = tmpdir(self)
        (home / "plugins").mkdir()
        other = {"id": "other", "root": "/x", "enabled": True}
        (home / "plugins" / "installed.json").write_text(json.dumps({"version": 1, "plugins": [other]}), encoding="utf-8")
        res = self.run_install(home)
        self.assertEqual(res.returncode, 0, res.stderr)
        reg = json.loads((home / "plugins" / "installed.json").read_text(encoding="utf-8"))
        self.assertEqual([p["id"] for p in reg["plugins"]], ["other", "supertick"])
        mine = reg["plugins"][1]
        self.assertEqual((mine["root"], mine["enabled"], mine["source"]), (str(home / "plugins" / "managed" / "supertick"), True, "local-path"))
        self.assertTrue((home / "plugins" / "managed" / "supertick" / "kimi.plugin.json").is_file())
        self.assertEqual(len(list((home / "plugins").glob("installed.json.bak-*"))), 1)
        first = mine["installedAt"]
        self.assertEqual(self.run_install(home).returncode, 0)   # повтор: одна запись, дата установки та же
        reg = json.loads((home / "plugins" / "installed.json").read_text(encoding="utf-8"))
        self.assertEqual([p["id"] for p in reg["plugins"]].count("supertick"), 1)
        self.assertEqual(reg["plugins"][-1]["installedAt"], first)
        self.assertEqual(self.run_install(home, "--uninstall").returncode, 0)
        reg = json.loads((home / "plugins" / "installed.json").read_text(encoding="utf-8"))
        self.assertEqual(reg["plugins"], [other])
        self.assertFalse((home / "plugins" / "managed" / "supertick").exists())

    def test_install_into_empty_home(self):
        home = tmpdir(self)
        self.assertEqual(self.run_install(home).returncode, 0)
        reg = json.loads((home / "plugins" / "installed.json").read_text(encoding="utf-8"))
        self.assertEqual([p["id"] for p in reg["plugins"]], ["supertick"])


if __name__ == "__main__":
    unittest.main()
