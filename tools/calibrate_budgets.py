#!/usr/bin/env python3
"""calibrate_budgets.py — пороги бюджетов по прошлым прогонам (121-567, X1).

    tools/calibrate_budgets.py [<каталог поиска> ...] [--json]

Ищет каталоги прогонов: `<repo>/.claude/teams/<имя>` (Claude Code, 1.x) и `<repo>/.supertick/teams/feature-<имя>`
(общий протокол, 2.0) — каталог с PLAN.md. По каждому считает:

  agents      участников: карточки runs/*.json плюс одноразовые роли, у которых карточки нет (имена вызовов
              движков из ledger.jsonl и каталоги engine/<имя>/) — без повторов имён;
  rounds      наибольшее число кругов ревью у одной задачи (отчёты reports/review-task<id>-r<n>*.md, иначе
              вердикты в mail/ — у Claude Code ревью идёт письмами);
  concurrent  наибольшее число участников, работавших одновременно (по интервалам startedAt/spawnedAt →
              lastEventAt карточек);
  complexity  из state.md / PLAN.md (SIMPLE | MEDIUM | COMPLEX), иначе `?`.

Печатает таблицу по прогонам и сводку по сложности (максимум, 90-й перцентиль), ниже — предложение порогов:
максимум по сложности с запасом в 25 % и не ниже значений ТЗ. Только stdlib, ничего не пишет.
"""
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_ROOTS = ("/var/www", "/root")
SPEC = {"SIMPLE": (8, 3, 4), "MEDIUM": (14, 3, 6), "COMPLEX": (30, 4, 8)}   # ТЗ п. 4.7, редакция 3
SKIP = {"node_modules", ".git", "proc", "build", "worktrees"}


def find_runs(roots):
    seen = set()
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for plan in root.rglob("PLAN.md"):
            parts = set(plan.parts)
            if parts & SKIP and "worktrees" not in str(root):
                continue
            run = plan.parent
            if run.parent.name == "teams" and run.parents[1].name in (".claude", ".supertick"):
                key = run.resolve()
                if key not in seen:
                    seen.add(key)
                    yield run


def ts(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def complexity(run):
    for name in ("state.md", "PLAN.md"):
        f = run / name
        if f.exists():
            m = re.search(r"\b(SIMPLE|MEDIUM|COMPLEX)\b", f.read_text(encoding="utf-8", errors="replace"))
            if m:
                return m.group(1)
    return "?"


def agents(run):
    names = {p.stem for p in (run / "runs").glob("*.json")} if (run / "runs").is_dir() else set()
    ledger = run / "ledger.jsonl"
    if ledger.exists():
        for line in ledger.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("role"):
                names.add(str(rec["role"]))
    if (run / "engine").is_dir():
        names |= {p.name for p in (run / "engine").iterdir() if p.is_dir()}
    return len(names)


def rounds(run):
    per_task = {}
    for f in (run / "reports").glob("review-task*-r*.md") if (run / "reports").is_dir() else []:
        m = re.match(r"review-task([^-]+)-r(\d+)", f.name)
        if m:
            per_task[m.group(1)] = max(per_task.get(m.group(1), 0), int(m.group(2)))
    if not per_task and (run / "mail").is_dir():
        for f in (run / "mail").rglob("*.md"):
            head = f.read_text(encoding="utf-8", errors="replace")[:400]
            if re.search(r"\b(VERDICT|REVIEW)\b", head):
                m = re.search(r"task:\s*#?(\w+)", head)
                if m:
                    per_task[m.group(1)] = per_task.get(m.group(1), 0) + (1 if "VERDICT" in head else 0)
    return max(per_task.values(), default=0)


def concurrent(run):
    spans = []
    for f in (run / "runs").glob("*.json") if (run / "runs").is_dir() else []:
        try:
            card = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        start = ts(card.get("startedAt") or card.get("spawnedAt") or "")
        end = ts(card.get("lastEventAt") or "")
        if start and end and end >= start:
            spans.append((start, end))
    best = 0
    for s, _ in spans:
        best = max(best, sum(1 for a, b in spans if a <= s <= b))
    return best


def percentile(values, p):
    if not values:
        return 0
    values = sorted(values)
    return values[max(0, math.ceil(p * len(values)) - 1)]


def main(argv):
    as_json = "--json" in argv
    roots = [a for a in argv if not a.startswith("--")] or list(DEFAULT_ROOTS)
    rows = []
    for run in find_runs(roots):
        rows.append({"run": str(run), "complexity": complexity(run), "agents": agents(run),
                     "rounds": rounds(run), "concurrent": concurrent(run)})
    summary = {}
    for level in ("SIMPLE", "MEDIUM", "COMPLEX", "?"):
        sub = [r for r in rows if r["complexity"] == level]
        if not sub:
            continue
        stat = {k: {"max": max(r[k] for r in sub), "p90": percentile([r[k] for r in sub], 0.9)}
                for k in ("agents", "rounds", "concurrent")}
        spec = SPEC.get(level)
        proposal = None
        if spec:
            proposal = tuple(max(s, math.ceil(stat[k]["max"] * 1.25)) for s, k in zip(spec, ("agents", "rounds", "concurrent")))
        summary[level] = {"runs": len(sub), **stat, "spec": spec, "proposal": proposal}
    if as_json:
        print(json.dumps({"runs": rows, "summary": summary}, ensure_ascii=False, indent=1))
        return 0
    for r in sorted(rows, key=lambda r: (r["complexity"], r["run"])):
        print(f"{r['complexity']:8} agents={r['agents']:3} rounds={r['rounds']:2} concurrent={r['concurrent']:2}  {r['run']}")
    print()
    for level, s in summary.items():
        print(f"{level}: {s['runs']} прогонов; agents max {s['agents']['max']} p90 {s['agents']['p90']}; "
              f"rounds max {s['rounds']['max']}; concurrent max {s['concurrent']['max']}; ТЗ {s['spec']} → {s['proposal']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
