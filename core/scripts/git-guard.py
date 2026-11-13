#!/usr/bin/env python3
"""git-guard.py — сторож `.git/hooks` и `.git/config` на время прогона (121-567, H4, риск R11).

Кодеру на Codex (и любому движку, которому открыта запись в `.git` ради коммита) песочница не мешает
положить файл в `.git/hooks/` или поправить `.git/config`: хук потом исполнит владелец вне песочницы.
Ограничить запись подкаталогами `.git` нельзя (`index.lock` создаётся в самом каталоге), поэтому
ведущий снимает отпечаток в начале прогона и сверяет его в конце.

  git-guard.py snapshot <run-dir> --root <репозиторий>   → <run-dir>/state/git-guard.json, строка GIT GUARD SNAPSHOT
  git-guard.py check    <run-dir> --root <репозиторий> [--json]

Под отпечаток попадают все файлы `hooks/` общего каталога git, `config` общего каталога и, если это
git-worktree, `config.worktree` и `config` его каталога. Сравнение — по sha256 и праву на исполнение.

Коды выхода: 0 — без изменений (или снимок записан); 1 — есть изменения, первая строка
`GIT GUARD CHANGED: …`; 2 — неверный вызов, нет снимка или не репозиторий.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

SNAPSHOT = Path("state") / "git-guard.json"


def git_dirs(root):
    dirs = []
    for flag in ("--git-common-dir", "--absolute-git-dir"):
        out = subprocess.run(["git", "-C", str(root), "rev-parse", flag], capture_output=True, text=True)
        if out.returncode != 0 or not out.stdout.strip():
            raise SystemExit(f"git-guard: {root} — не репозиторий git")
        path = Path(out.stdout.strip())
        path = (path if path.is_absolute() else Path(root) / path).resolve()
        if path not in dirs:
            dirs.append(path)
    return dirs   # [общий, каталог рабочего дерева (если отличается)]


def fingerprint(root):
    dirs = git_dirs(root)
    common = dirs[0]
    files = {}

    def add(label, path):
        try:
            data = path.read_bytes()
        except OSError:
            return
        files[label] = {"sha256": hashlib.sha256(data).hexdigest(), "exec": os.access(path, os.X_OK)}

    hooks = common / "hooks"
    if hooks.is_dir():
        for path in sorted(hooks.rglob("*")):
            if path.is_file() or path.is_symlink():
                add("hooks/" + path.relative_to(hooks).as_posix(), path)
    add("config", common / "config")
    add("config.worktree", common / "config.worktree")
    for extra in dirs[1:]:
        add(f"worktree:{extra.name}/config.worktree", extra / "config.worktree")
        add(f"worktree:{extra.name}/config", extra / "config")
    return {"git_dirs": [str(d) for d in dirs], "files": files}


def diff(before, after):
    changes = []
    for label in sorted(set(before) | set(after)):
        if label not in before:
            changes.append(f"{label} (новый)")
        elif label not in after:
            changes.append(f"{label} (удалён)")
        elif before[label] != after[label]:
            what = "изменён" if before[label]["sha256"] != after[label]["sha256"] else "сменилось право на исполнение"
            changes.append(f"{label} ({what})")
    return changes


def main(argv=None):
    ap = argparse.ArgumentParser(description="сторож .git/hooks и .git/config на время прогона")
    ap.add_argument("action", choices=("snapshot", "check"))
    ap.add_argument("run_dir")
    ap.add_argument("--root", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    run, snap = Path(args.run_dir), Path(args.run_dir) / SNAPSHOT
    try:
        now = fingerprint(args.root)
    except SystemExit as e:
        print(e, file=sys.stderr)
        return 2
    if args.action == "snapshot":
        if not run.is_dir():
            print(f"git-guard: нет каталога прогона {run}", file=sys.stderr)
            return 2
        snap.parent.mkdir(parents=True, exist_ok=True)
        if snap.exists():   # прогон продолжают после потери контекста: снимок начала не перезаписывается
            print(f"GIT GUARD SNAPSHOT: уже есть, оставлен ({snap})")
            return 0
        tmp = snap.with_suffix(".tmp")
        tmp.write_text(json.dumps(now, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, snap)
        print(f"GIT GUARD SNAPSHOT: {len(now['files'])} файлов ({snap})")
        return 0
    try:
        before = json.loads(snap.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print(f"git-guard: нет снимка {snap} — сначала snapshot", file=sys.stderr)
        return 2
    changes = diff(before.get("files") or {}, now["files"])
    if args.json:
        print(json.dumps({"changed": changes}, ensure_ascii=False))
    elif changes:
        print("GIT GUARD CHANGED: " + ", ".join(changes))
        print("Проверь эти файлы руками до следующего git-действия вне песочницы: хук исполнится от твоего имени.")
    else:
        print("GIT GUARD OK")
    return 1 if changes else 0


if __name__ == "__main__":
    sys.exit(main())
