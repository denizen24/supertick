#!/usr/bin/env python3
"""replay_tick.py — тик 2.0 против тика 1.1.1 на копиях каталогов прошлых прогонов (121-567, приёмка п. 3).

У каждого тика своя копия-срез (жёсткие ссылки, mtime сохраняются): тик пишет свою память в
`state/supervisor.json`, и у двух версий она своя. Оба тика вызываются подряд с одними и теми же `--now`: от самого раннего mtime в каталоге до самого позднего
плюс хвост, с шагом `--step`. Файлы при этом не меняются: история прогона не восстанавливается,
зато вход у двух тиков побайтно одинаковый. Это сравнение поведения, а не реконструкция прогона.

На каждый момент тики видят срез: письма, отчёты и маркеры движков с mtime не позже момента, строки
ledger.jsonl с ts не позже момента; изменяемые файлы (PLAN.md, state.md, runs/*.json) — в конечном
виде; отметок «прочитано» нет. Так тики проходят по настоящим письмам и вызовам движков, а не по
конечному каталогу, где всё DONE и тик молчит.

Событие — тройка (приоритет, вид, участник). «Новое» — есть у 2.0 и нет у 1.1.1 в тот же момент.
Приёмка требует ноль новых событий P0–P2. Расхождения только в тексте detail считаются отдельно.

    python3 tools/replay_tick.py --scan /var/www/121ai --scan /var/www/supertick --report docs/trials/2.0-replay.md
    python3 tools/replay_tick.py <run-dir> [<run-dir> …] [--old <tick.py>] [--new <tick.py>]

`--old` по умолчанию — `plugins/supertick/scripts/supervisor-tick.py` из тега v1.1.1 (через `git show`),
`--new` — `core/scripts/supervisor-tick.py`. Код выхода 1, если есть новые P0–P2.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NEW_TICK = REPO / "core" / "scripts" / "supervisor-tick.py"
OLD_REF = "v1.1.1:plugins/supertick/scripts/supervisor-tick.py"
GATED = ("P0", "P1", "P2")


def old_tick_file(tmp):
    path = Path(tmp) / "tick-1.1.1.py"
    path.write_bytes(subprocess.run(["git", "-C", str(REPO), "show", OLD_REF],
                                    check=True, capture_output=True).stdout)
    return path


def scan(base):
    """Каталоги прогонов под base: <repo>/.claude/teams/<name> со state/supervisor.json.
    Копии одного прогона в worktree (то же имя и тот же размер PLAN.md) берутся один раз."""
    found, seen = [], set()
    for memory in sorted(Path(base).rglob("state/supervisor.json")):
        run = memory.parents[1]
        if run.parent.name != "teams" or run.parents[1].name != ".claude":
            continue
        plan = run / "PLAN.md"
        key = (run.name, plan.stat().st_size if plan.is_file() else -1)
        if key in seen:
            continue
        seen.add(key)
        found.append(run)
    return found


def time_span(run, tail_min):
    stamps = [p.stat().st_mtime for p in run.rglob("*") if p.is_file() and "state" not in p.relative_to(run).parts[:1]]
    if not stamps:
        return None
    start = datetime.fromtimestamp(min(stamps), tz=timezone.utc)
    end = datetime.fromtimestamp(max(stamps), tz=timezone.utc) + timedelta(minutes=tail_min)
    return start, end


MUTABLE = {"PLAN.md", "state.md", "VERIFICATION_PLAN.md", "DECISIONS.md"}
SKIP_TOP = {"state", "tmp"}


def slice_at(run, dest, now):
    """Срез прогона на момент `now` в dest (state/ в dest не трогается — там память тика).

    Письма, отчёты, маркеры движков появляются один раз — берутся, если mtime ≤ now (жёсткая ссылка,
    mtime тот же). ledger.jsonl обрезается по полю ts. Изменяемые файлы (PLAN.md, state.md, runs/*.json)
    истории не хранят — берутся в конечном виде всегда. Отметки «прочитано» (state/acked.log) не берутся."""
    for child in dest.iterdir():
        if child.name != "state":
            shutil.rmtree(child) if child.is_dir() and not child.is_symlink() else child.unlink()
    stamp = now.timestamp()
    for src in run.rglob("*"):
        rel = src.relative_to(run)
        if rel.parts[0] in SKIP_TOP or not src.is_file() or src.is_symlink():
            continue
        mutable = rel.name in MUTABLE and len(rel.parts) == 1 or rel.parts[0] == "runs" \
            or rel.as_posix() == "ledger.jsonl"   # дописывается весь прогон: берётся всегда, обрезанным по ts
        if not mutable and src.stat().st_mtime > stamp:
            continue
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        if rel.as_posix() == "ledger.jsonl":
            keep = []
            for line in src.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True):
                try:
                    ts = json.loads(line).get("ts")
                    at = datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None
                except (ValueError, AttributeError):
                    at = None
                if at is None or at.timestamp() <= stamp:
                    keep.append(line)
            out.write_text("".join(keep), encoding="utf-8")
            os.utime(out, (stamp, min(stamp, src.stat().st_mtime)))
        else:
            os.link(src, out)


def call(tick, run, root, now, home):
    env = {**os.environ, "TZ": "UTC", "HOME": str(home)}
    proc = subprocess.run([sys.executable, "-I", str(tick), str(run), "--root", str(root), "--json",
                           "--now", now.isoformat()], capture_output=True, text=True, timeout=120, env=env)
    if proc.returncode != 0:
        return None, proc.stderr.strip().splitlines()[-1:] or ["код " + str(proc.returncode)]
    text = proc.stdout
    for path in {str(run), str(Path(run).resolve())}:   # у двух тиков разные копии — путь не в счёт
        text = text.replace(path, "<RUN>")
    return json.loads(text)["actions"], None


def replay(run, old, new, step, tail_min, tmp):
    span = time_span(run, tail_min)
    root = run.parents[2]
    out = {"run": str(run), "ticks": 0, "events": 0, "new": Counter(), "gone": Counter(), "detail_only": 0,
           "errors": [], "examples": []}
    if not span:
        return out
    work = Path(tempfile.mkdtemp(prefix=run.name + "-", dir=tmp))
    a, b, home = work / "old", work / "new", work / "home"
    for d in (a / "state", b / "state", home):
        d.mkdir(parents=True)
    now = span[0]
    while now <= span[1]:
        slice_at(run, a, now)
        slice_at(run, b, now)
        acts_old, err_old = call(old, a, root, now, home)
        acts_new, err_new = call(new, b, root, now, home)
        out["ticks"] += 1
        if err_old or err_new:
            out["errors"].append(f"{now.isoformat()}: old={err_old} new={err_new}")
        else:
            key = lambda x: (x["priority"], x["kind"], x["run"])
            so = Counter(key(x) for x in acts_old)
            sn = Counter(key(x) for x in acts_new)
            out["events"] += gated(so)
            for k, n in (sn - so).items():
                out["new"][k] += n
                if k[0] in GATED and len(out["examples"]) < 5:
                    out["examples"].append((now.isoformat(), k, [x["detail"] for x in acts_new if key(x) == k][:1]))
            for k, n in (so - sn).items():
                out["gone"][k] += n
            if so == sn and sorted(map(json.dumps, acts_old)) != sorted(map(json.dumps, acts_new)):
                out["detail_only"] += 1
        now += timedelta(seconds=step)
    shutil.rmtree(work, ignore_errors=True)
    return out


def gated(counter):
    return sum(n for k, n in counter.items() if k[0] in GATED)


def report(results, old, new, step, tail_min):
    total_new = sum(gated(r["new"]) for r in results)
    lines = ["# Тик 2.0 против 1.1.1 на прошлых прогонах", "",
             f"Сделано `tools/replay_tick.py` {datetime.now().strftime('%d.%m.%Y %H:%M')}: "
             f"{len(results)} каталогов, шаг {step // 60} мин, хвост {tail_min} мин после последней правки, "
             f"режим `claude` (все прогоны сделаны в Claude Code). Старый тик: `{old}`, новый: `{new}`.", "",
             f"**Событий P0–P2 у 1.1.1 всего {sum(r['events'] for r in results)}, новых у 2.0: {total_new}.** " + ("Приёмка п. 3 выполнена." if total_new == 0 else "Приёмка п. 3 НЕ выполнена."),
             "", "| Прогон | Тиков | События P0–P2 у 1.1.1 | Новые P0–P2 | Пропавшие P0–P2 | Только текст | Ошибки |",
             "|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| `{Path(r['run']).relative_to(Path(r['run']).parents[3])}` | {r['ticks']} | {r['events']} | {gated(r['new'])} | "
                     f"{gated(r['gone'])} | {r['detail_only']} | {len(r['errors'])} |")
    lines += ["", "Срез на момент — файлы с mtime не позже момента и ledger по ts; PLAN.md, state.md и карточки —",
              "в конечном виде, отметок «прочитано» нет. Поэтому события искусственные (например, каждое письмо",
              "ведущему висит без ответа): важно только то, что у двух версий они совпадают."]
    extra = [(r, k, n) for r in results for k, n in r["new"].items()] + \
            [(r, k, -n) for r in results for k, n in r["gone"].items()]
    if extra:
        lines += ["", "## Расхождения", "", "| Прогон | Событие | Разница (2.0 − 1.1.1) |", "|---|---|---|"]
        for r, k, n in extra:
            lines.append(f"| `{Path(r['run']).name}` | {' '.join(x for x in k if x)} | {n:+d} |")
    examples = [(r, e) for r in results for e in r["examples"]]
    if examples:
        lines += ["", "## Примеры новых событий", ""]
        lines += [f"- `{Path(r['run']).name}` {e[0]}: {' '.join(e[1])} — {e[2][0] if e[2] else ''}" for r, e in examples]
    errors = [(r, e) for r in results for e in r["errors"][:3]]
    if errors:
        lines += ["", "## Ошибки вызова", ""] + [f"- `{Path(r['run']).name}` {e}" for r, e in errors]
    return "\n".join(lines) + "\n", total_new


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("runs", nargs="*", type=Path)
    ap.add_argument("--scan", type=Path, action="append", default=[], help="найти каталоги прогонов под этим путём (можно несколько)")
    ap.add_argument("--old", type=Path)
    ap.add_argument("--new", type=Path, default=NEW_TICK)
    ap.add_argument("--step", type=int, default=600, help="шаг по времени, с (600)")
    ap.add_argument("--tail", type=int, default=30, help="минут после последней правки (30)")
    ap.add_argument("--report", type=Path, help="записать отчёт Markdown сюда")
    args = ap.parse_args(argv)
    runs = list(args.runs) + [r for base in args.scan for r in scan(base)]
    if not runs:
        ap.error("нужен каталог прогона или --scan")
    with tempfile.TemporaryDirectory(prefix="replay-tick-") as tmp:
        old = args.old or old_tick_file(tmp)
        results = []
        for run in runs:
            r = replay(run.resolve(), old, args.new, args.step, args.tail, tmp)
            print(f"{Path(r['run']).name}: тиков {r['ticks']}, событий P0–P2 у 1.1.1 {r['events']}, новых P0–P2 {gated(r['new'])}, "
                  f"пропавших {gated(r['gone'])}, только текст {r['detail_only']}, ошибок {len(r['errors'])}", flush=True)
            results.append(r)
        text, total = report(results, args.old or OLD_REF, args.new.relative_to(REPO) if args.new.is_relative_to(REPO) else args.new,
                             args.step, args.tail)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8")
    print(f"ИТОГ: новых событий P0–P2 — {total}")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
