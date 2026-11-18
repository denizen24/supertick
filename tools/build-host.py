#!/usr/bin/env python3
"""build-host.py — собирает устанавливаемый набор supertick для среды (121-567, F2, D23).

    tools/build-host.py <хост> [--out КАТАЛОГ] [--check] [--root КОРЕНЬ]

Источник истины — `core/` (общие скрипты) и `hosts/<хост>/` (тексты под среду). Что и куда класть,
говорит `hosts/<хост>/build.json`: список `include` из пар `from` → `to`, пути от корня репозитория.
Результат по умолчанию — `output` из манифеста (для Claude Code это `plugins/supertick/`, он лежит
в git для маркетплейса и руками не правится).

- Без флагов: выход приводится к сборке. Совпавшие файлы не переписываются, лишние удаляются.
  Бит исполнения берётся у источника, остальные права нормализуются (git хранит только его).
- `--check`: ничего не пишет; печатает расхождения (нет, лишний, отличается, режим) и выходит с 1.
  Так CI ловит правку `plugins/supertick/` в обход `core/` и `hosts/`.
- `--out`: собрать в другой каталог. Непустой чужой каталог не принимается: сборка стирает лишнее.
- `__pycache__`, `*.pyc`, `.pytest_cache` ни в сборку, ни в сравнение не входят.
- У пары в `include` могут быть ещё два ключа (121-567, H1):
  `exclude` — список имён каталогов или файлов, которые из источника-каталога не берутся;
  `frontmatter` — объект (строки, списки строк и объекты строк в один уровень), который ставится YAML-шапкой `---` перед текстом
  файла-источника. Так тип агента среды собирается из брифа роли `core/roles/` без копии текста.

Только stdlib. Код выхода: 0 — готово или совпало; 1 — расхождение в `--check`; 2 — ошибка манифеста.
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

SKIP_DIRS = {"__pycache__", ".pytest_cache"}
SKIP_SUFFIXES = {".pyc"}


class BuildError(Exception):
    pass


def skipped(path):
    return any(part in SKIP_DIRS for part in path.parts) or path.suffix in SKIP_SUFFIXES


def load_manifest(root, host):
    path = root / "hosts" / host / "build.json"
    if not path.is_file():
        raise BuildError(f"нет манифеста {path.relative_to(root)}")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise BuildError(f"{path.relative_to(root)}: не JSON ({exc})")
    if not manifest.get("output") or not isinstance(manifest.get("include"), list) or not manifest["include"]:
        raise BuildError(f"{path.relative_to(root)}: нужны `output` и непустой `include`")
    return manifest


def inside(root, rel, what):
    """Относительный путь без выхода из корня: никаких абсолютных и `..`."""
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts:
        raise BuildError(f"{what} `{rel}`: путь должен быть относительным и без `..`")
    return p


def frontmatter(data, where):
    """YAML-шапка из объекта со строками, списками строк и объектами строк; строки — в кавычках JSON (валидный YAML)."""
    if not isinstance(data, dict) or not data:
        raise BuildError(f"{where}: frontmatter должен быть непустым объектом")
    lines = ["---"]
    for key, value in data.items():
        if isinstance(value, str):
            lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
        elif isinstance(value, list) and all(isinstance(v, str) for v in value):
            lines.append(f"{key}:")
            lines += [f"  - {v}" for v in value]
        elif isinstance(value, dict) and value and all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
            lines.append(f"{key}:")   # один уровень вложенности: права агента OpenCode (permission: {edit: deny})
            lines += [f"  {k}: {json.dumps(v, ensure_ascii=False)}" for k, v in value.items()]
        else:
            raise BuildError(f"{where}: frontmatter.{key} — нужна строка, список строк или объект строк")
    return ("\n".join(lines) + "\n---\n\n").encode("utf-8")


def collect(root, manifest):
    """Плоский план сборки: выходной относительный путь -> (источник, исполняемый ли, шапка-байты)."""
    plan = {}
    for entry in manifest["include"]:
        src_rel = inside(root, entry.get("from", ""), "from")
        dst_rel = inside(root, entry.get("to", ""), "to")
        src = root / src_rel
        if src.resolve() != src:
            raise BuildError(f"символьная ссылка в источнике: {src_rel}")
        if not src.exists():
            raise BuildError(f"нет источника `{src_rel}`")
        head = b""
        if "frontmatter" in entry:
            if not src.is_file():
                raise BuildError(f"frontmatter только у файла, а `{src_rel}` — каталог")
            head = frontmatter(entry["frontmatter"], f"`{src_rel}`")
        exclude = set(entry.get("exclude") or [])
        files = [src] if src.is_file() else sorted(src.rglob("*"))
        for f in files:
            rel_in_src = f.relative_to(src) if src.is_dir() else Path()
            if skipped(rel_in_src) or skipped(Path(f.name)) or exclude & set(rel_in_src.parts):
                continue
            if f.is_symlink():
                raise BuildError(f"символьная ссылка в источнике: {f.relative_to(root)}")
            if not f.is_file():
                continue
            dest = dst_rel / rel_in_src if src.is_dir() else dst_rel
            key = dest.as_posix()
            if key in plan:
                raise BuildError(f"два источника на один выходной файл `{key}`: {plan[key][0].relative_to(root)} и {f.relative_to(root)}")
            plan[key] = (f, bool(f.stat().st_mode & 0o111), head)
    return plan


def existing(out):
    """Файлы выходного каталога: относительный путь -> исполняемый ли (мусор кэшей не считается)."""
    found = {}
    if out.is_dir():
        for p in sorted(out.rglob("*")):
            rel = p.relative_to(out)
            if p.is_file() and not skipped(rel):
                found[rel.as_posix()] = bool(p.stat().st_mode & 0o111)
    return found


def diff(plan, out):
    """Расхождения выхода со сборкой: (нет, отличается, режим, лишний)."""
    have = existing(out)
    missing = sorted(set(plan) - set(have))
    extra = sorted(set(have) - set(plan))
    differs, mode = [], []
    for key in sorted(set(plan) & set(have)):
        src, exe, head = plan[key]
        if (out / key).read_bytes() != head + src.read_bytes():
            differs.append(key)
        elif have[key] != exe:
            mode.append(key)
    return missing, differs, mode, extra


def write(plan, out):
    missing, differs, mode, extra = diff(plan, out)
    for key in missing + differs + mode:
        src, exe, head = plan[key]
        dest = out / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        if key not in mode:
            dest.write_bytes(head + src.read_bytes())
        dest.chmod(0o755 if exe else 0o644)
    for key in extra:
        (out / key).unlink()
    for d in sorted((p for p in out.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        if not any(d.iterdir()):
            d.rmdir()
    return len(missing) + len(differs) + len(mode), len(extra)


def main(argv):
    ap = argparse.ArgumentParser(description="Сборка набора supertick для среды")
    ap.add_argument("host")
    ap.add_argument("--out")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    try:
        manifest = load_manifest(root, args.host)
        plan = collect(root, manifest)
        target = root / inside(root, manifest["output"], "output")
        requested = Path(os.path.abspath(args.out)) if args.out else target
        if target.resolve() != target or requested.is_symlink():
            raise BuildError("выходной каталог не должен проходить через символьную ссылку")
        declared, out = target, requested.resolve()
        if out.is_dir() and any(p.is_symlink() for p in out.rglob("*")):
            raise BuildError("в выходном каталоге есть символьная ссылка")
        if out == root or root in out.parents and out.relative_to(root).parts[0] in ("core", "hosts", "tools", ".git"):
            raise BuildError(f"выходной каталог {out} совпадает с исходниками")
        if not args.check and out != declared and out.exists() and any(out.iterdir()):
            raise BuildError(f"--out {out}: каталог не пуст, сборка стёрла бы в нём лишнее")
    except BuildError as exc:
        print(f"build-host: {exc}", file=sys.stderr)
        return 2
    if args.check:
        missing, differs, mode, extra = diff(plan, out)
        for label, items in (("нет в выходе", missing), ("отличается", differs), ("режим (бит x)", mode), ("лишний", extra)):
            for key in items:
                print(f"{label}: {key}")
        if missing or differs or mode or extra:
            print(f"build-host: {out} расходится со сборкой {args.host}; пересобрать: tools/build-host.py {args.host}", file=sys.stderr)
            return 1
        print(f"build-host: {args.host} совпадает, файлов {len(plan)}")
        return 0
    out.mkdir(parents=True, exist_ok=True)
    changed, removed = write(plan, out)
    print(f"build-host: {args.host} -> {out}: файлов {len(plan)}, записано {changed}, удалено {removed}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
