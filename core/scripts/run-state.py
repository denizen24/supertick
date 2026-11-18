#!/usr/bin/env python3
"""run-state.py — карточка участника runs/<name>.json: создать при спавне, обновить по ходу.

  run-state.py new <run-dir> <name> role=<роль> task=<id> [files=a.py,b.py] [checkAfterSec=900] \
                   [acceptance=...] [nonGoals=...]          — ведущий, прямо перед спавном
                   (роль без задачи — рецензент, техлид — рождается `idle`: ждёт первый запрос)
  run-state.py set <run-dir> <name> status=<running|in_review|fixing|reviewing|done|stuck|idle|stopped> \
                   [note=...] [task=<id>] [commits=sha1,sha2]  — сам участник, при каждом событии
Карточка общего протокола (есть `executor=`) получает `access` роли из реестра engine-config.py — по нему тик
ловит участника на чтение, который закоммитил (P0 read_agent_owns_files).
  run-state.py show <run-dir> [<name>]

`set` всегда ставит lastEventAt=сейчас; первый `set status=running` ставит ещё и
startedAt — от него тик считает первую минуту, а не от карточки, которую ведущий
завёл за несколько минут до спавна. Файл переписывается целиком через
временный и rename: у карточки один владелец, гонок нет. Роль без Bash пишет
тот же JSON инструментом Write — поля те же.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_support import alive, append_record, component, latest_call, take_result, validate_run_directory, write_json

LISTS = ("files", "acceptance", "nonGoals", "commits")


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse(pairs):
    out = {}
    for p in pairs:
        if "=" not in p:
            raise SystemExit(f"run-state: ожидается key=value, а не {p!r}")
        k, v = p.split("=", 1)
        if k in LISTS:
            v = [x.strip() for x in v.split(",") if x.strip()]
        elif k == "checkAfterSec":
            v = int(v)
        out[k] = v
    return out


def role_access(role):
    """read | write роли по реестру engine-config.py (second-reviewer и architect-* — как у их брифов)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("engine_config_rs", Path(__file__).with_name("engine-config.py"))
    try:
        ec = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ec)
    except (OSError, ImportError, SyntaxError):
        return ""
    return ec.ROLES.get(role, {}).get("access", "")


def main(argv):
    if len(argv) < 2:
        raise SystemExit(__doc__)
    cmd, run = argv[0], Path(argv[1])
    if cmd == "take" and (len(argv) == 3 or (len(argv) == 4 and argv[3] == "--head")):
        result = take_result(run, argv[2])
        if len(argv) == 4 and result["report"]:
            with open(result["report"], encoding="utf-8", errors="replace") as stream:
                lines = (line for line in stream if line.strip())
                from itertools import islice
                print("".join(islice(lines, 3)), end="")
        else:
            print(json.dumps(result, ensure_ascii=False))
        return 0
    if cmd == "manual-accept" and len(argv) >= 3:
        import re
        task, fields = component(argv[2]), parse(argv[3:])
        if not re.fullmatch(r"[0-9a-f]{7,64}", fields.get("commit", "")) or not all(
            fields.get(key, "").strip() for key in ("reason", "evidence")
        ):
            raise ValueError("manual-accept requires commit=<sha> reason=<reason> evidence=<approval link or record>")
        validate_run_directory(run)
        path = run / "reports" / f"manual-accept-task{task}.json"
        if path.exists():
            raise ValueError(f"manual acceptance exists: {path}")
        write_json(path, {**fields, "status": "MANUAL_ACCEPTED", "task": task, "at": now()})
        print(path)
        return 0
    if cmd == "show":
        names = [argv[2]] if len(argv) > 2 else [p.stem for p in sorted((run / "runs").glob("*.json"))]
        for n in names:
            component(n)
            print(n, json.dumps(json.loads((run / "runs" / f"{n}.json").read_text(encoding="utf-8")), ensure_ascii=False))
        return 0
    if len(argv) < 3:
        raise SystemExit(__doc__)
    name, fields = component(argv[2]), parse(argv[3:])
    path = run / "runs" / f"{name}.json"
    if cmd == "new":
        call = latest_call(run, name)
        if call and not Path(f"{call}.done").exists() and alive(Path(f"{call}.pid").read_text().strip()):
            raise ValueError("cannot replace the card of a running engine")
        role, task = fields.pop("role", ""), fields.pop("task", "")
        data = {"name": name, "role": role, "task": task,
                "spawnedAt": now(), "lastEventAt": now(), "status": "running" if task else "idle",
                "files": fields.pop("files", []), "acceptance": fields.pop("acceptance", []),
                "nonGoals": fields.pop("nonGoals", []), "checkAfterSec": fields.pop("checkAfterSec", 900), "note": ""}
        data.update(fields)
        if "executor" in data and "access" not in data:   # общий протокол (121-567, X1): доступ роли из реестра
            data["access"] = role_access(role)
        write_json(path, data)
        if data.get("executor") == "native":
            append_record(run / "ledger.jsonl", {"ts": now(), "event": "native_card", "role": name,
                          "task": task, "engine": "native", "role_type": role})
    elif cmd == "set":
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"name": name, "spawnedAt": now()}
        native_start = data.get("executor") == "native" and not data.get("startedAt") and (
            fields.get("status") == "running" or fields.get("note", "").startswith("agent=")
        )
        if "timeoutSec" in fields and str(fields["timeoutSec"]) != str(data.get("timeoutSec", "")):
            call = latest_call(run, name)
            if call and not Path(f"{call}.done").exists():
                pid_file = Path(f"{call}.pid")
                if pid_file.exists() and alive(pid_file.read_text().strip()):
                    raise ValueError("the running engine deadline is immutable; set timeoutSec before the next launch")
        data.update(fields)
        data["lastEventAt"] = now()
        if native_start or (fields.get("status") == "running" and not data.get("startedAt")):
            data["startedAt"] = data["lastEventAt"]
        write_json(path, data)
        if native_start:
            append_record(run / "ledger.jsonl", {"ts": data["lastEventAt"], "event": "native_launch",
                          "role": name, "task": data.get("task"), "role_type": data.get("role"), "engine": "native"})
    else:
        raise SystemExit(__doc__)
    print(path)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (OSError, ValueError) as exc:
        sys.exit(f"run-state: {exc}")
