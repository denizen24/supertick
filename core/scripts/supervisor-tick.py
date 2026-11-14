#!/usr/bin/env python3
"""supervisor-tick.py — один тик супервизора команды, без LLM.

Читает каталог прогона `.claude/teams/<team>/` и отвечает на один вопрос: есть ли
сейчас что-то, ради чего стоит будить ведущего. Ничего не сочиняет и никому не
пишет — только факты из файлов, которые команда и так ведёт:

  PLAN.md          статусы задач (пишет только ведущий)
  state.md         фаза, движки
  runs/<name>.json карточка участника: роль, задача, статус, время последнего события
                   (создаёт ведущий при спавне, дальше ведёт сам участник)
  mail/<to>/*.md   письма: одно письмо — один файл (`team-mail.sh` или Write)
  pending.log      копии писем, вернувшихся `queued` (строки `| OPEN`)
  ledger.jsonl     события движков (`run-engine.sh`)
  engine/<role>/   маркеры движков: *.out.pid / .done / .taken

Пишет только своё: state/supervisor.json (снимок + память тика) и печатает
действия. Код выхода всегда 0, кроме поломки окружения (нет каталога).

Приоритеты (от срочного к фоновому):
  P0  доставить копию `queued`; письмо участнику лежит дольше двух минут, а адресат
      его не взял (undelivered_mail); участник STUCK / ENGINE_DOWN / прокси мёртв;
      движок упал или досчитал, а результат никто не забрал (15 мин, у прокси 3 —
      engine_unread_sec); участник молчит дольше двух интервалов
  P1  DONE участника, которого ведущий ещё не отметил в PLAN.md (accept_needed);
      тот же DONE, когда в mail/unified-reviewer/ нет REVIEW по задаче (done_without_review);
      готовый отчёт приёмки reports/accept-task<id>.md при статусе ACCEPTING (accept_result);
      письмо ведущему без ответа (QUESTION / ESCALATION / SENSITIVE / QUEUED);
      первая минута: участник молчит уже 3 минуты (один раз)
  P2  первая минута: старт старше 60 с, а признаков жизни нет (один раз)
  P3  контрольная точка: с последнего события прошёл интервал участника; busy — молчит
      дольше двух интервалов, но под корнем идут тесты/сборка
  P4  всё тихо

Первая минута ловит спавн, который так и не начал работать. Участник, сам
поставивший `startedAt` (первый `run-state.py set … status=running`), уже работает —
дальше он читает файлы и может минутами не давать других признаков; тревога ему не
нужна (прогон 121-374: ложное P1 на кодера, читавшего код 3 минуты). Без `startedAt`
первая минута считается от `spawnedAt`. Каждая тревога первой минуты
звучит один раз (память тика); дальше участник под обычным надзором: контрольная
точка через интервал, `silent_too_long` через два. Роль без задачи (рецензент,
техлид до первого запроса) первой минуты не имеет — она ждёт, а не молчит.
«Правки в файлах задачи» — файлы с mtime внутри окна, а не `git status`: правка,
сделанная час назад и не закоммиченная, участника живым не делает.

Занятой участник: кодер может минутами гонять тяжёлый набор тестов, не трогая файлы
задачи (прогон 121-393: денежный pytest 5–6 минут — дважды ложный P0, и цикл тут же
будил ведущего снова). Если под корнем репозитория (cwd процесса из /proc) идёт
pytest / jest / yarn / npm / craco моложе окна молчания, `silent_too_long` понижается
до P3 `busy` с командой процесса. Процесс старше окна — вероятно, зависший, — не
спасает. Нет /proc (не Linux) — правило работает как раньше.

Письма участникам (mail/<имя>/, не lead): вердикт рецензента кодеру, REVIEW кодера
рецензенту. Письмо доставлено, если адресат отметил его в mail/<имя>/.seen
(`team-wait.sh`) или после письма обновил свою карточку. Время карточки — позднее из
lastEventAt и mtime файла runs/<имя>.json: роль без Bash пишет lastEventAt руками и
может выдумать время (121-393: 12:06+03:00 при реальных 13:48+02:00 — дважды ложный
P0), а mtime ставит файловая система.
Иначе через две минуты — P0 undelivered_mail: SendMessage вернулся `queued` и
потерялся, адресат стоит. Ведущий шлёт RESEND с текстом файла и делает `ack`.

Режим общего протокола (`--mode shared`, 121-567 P1): среды без сообщений между агентами, где цикл
ведёт сам ведущий (Kimi, Codex, OpenCode). Исполнитель — родной субагент среды или движок через
run-engine.sh; свою карточку он не ведёт, её заводит и закрывает ведущий. Поэтому правила почты
(копии, письма ведущему, недоставленные письма, DONE без REVIEW) и правила молчания (первая минута,
контрольная точка, silent_too_long, busy) в этом режиме молчат. Работают:
  P0  движок умер без .done, упал или результат не забран 15 мин (engine_dead, engine_failed_unread,
      engine_result_unread); исполнитель вышел за свой срок (executor_overdue: от startedAt/spawnedAt
      дольше timeoutSec карточки, по умолчанию час, и нет живого вызова движка с его именем);
      карточка stuck
  P1  движок досчитал — результат ждёт ведущего (engine_result_ready); отчёт исполнителя лежит
      (executor_report_ready: файл из поля `report` карточки, новее старта); движок исполнителя
      так и не запустился (engine_not_started: `executor: engine`, минута после старта, вызовов нет);
      отчёт приёмки готов (accept_result, как и без флага)
Имя карточки и имя каталога engine/<имя>/ у движка совпадают — так тик связывает их.
Без флага (`--mode claude`) поведение прежнее до байта (снапшоты tools/tests/snapshots/tick).

Использование:
  supervisor-tick.py <run-dir> [--root <корень репозитория>] [--mode claude|shared] [--json] [--now ISO]
  supervisor-tick.py ack <run-dir> <имя файла письма>   — ведущий ответил на письмо
  supervisor-tick.py budgets <SIMPLE|MEDIUM|COMPLEX> [--budget=enforce|off|a/r/c]   — раздел ## Budgets для state.md
  supervisor-tick.py plan <run-dir>            — planned_agents против max_agents (2 — превышение в enforce)
  supervisor-tick.py postrun-check <run-dir> [--root <репозиторий>]   — сверки перед сводкой (1 — есть находки)

Бюджеты (121-567, X1; идея — dsh-supertick, см. NOTICE): только при разделе `## Budgets` в state.md. Режим
`observe` (по умолчанию) — превышения P3 budget_note; `enforce` — P1 agent_budget_exceeded, concurrency_exceeded,
review_rounds_exceeded, fanout_planned_exceeded; `off` — молчат. Каждое превышение — одно событие (память тика).
P0 read_agent_owns_files — участник с `access: read` закоммитил (поле commits карточки или отчёт COMMITTED) —
нарушение контракта, в любом режиме. Пороги — docs/budgets.md.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

FIRST_MINUTE_SEC = 60          # после этого ждём признаков жизни
FIRST_MINUTE_LOUD_SEC = 180    # молчание дольше — уже P1
DEFAULT_CHECK_SEC = 900        # контрольная точка по умолчанию, 15 мин
PENDING_STALE_SEC = 120        # OPEN в pending.log старше двух минут — P0
UNDELIVERED_SEC = 120          # письмо участнику не взято две минуты — P0
ENGINE_UNREAD_SEC = 900        # движок досчитал, а результат не забрали 15 мин
ENGINE_UNREAD_PROXY_SEC = 180  # то же у прокси, пока он не отметил чтение карточкой
DEFAULT_EXECUTOR_SEC = 3600    # срок исполнителя в режиме общего протокола, если в карточке нет timeoutSec
MODES = ("claude", "shared")
LIVE = ("running", "in_review", "fixing", "reviewing")
NEEDS_ANSWER = ("QUESTION", "ESCALATION", "SENSITIVE", "QUEUED", "REVIEW_LOOP")
PRIORITY = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4}


def parse_ts(value):
    """ISO-время в aware datetime; пустое или кривое — None."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def plan_tasks(run_dir):
    """{id: status} из PLAN.md: `## Task 3: …` и следующая за ним строка `Status: X`."""
    text = (run_dir / "PLAN.md").read_text(encoding="utf-8") if (run_dir / "PLAN.md").exists() else ""
    tasks = {}
    current = None
    for line in text.splitlines():
        head = re.match(r"^## Task\s+([^:\s]+)\s*:", line)
        if head:
            current = head.group(1)
            tasks[current] = {"status": "", "files": []}
            continue
        if current is None:
            continue
        m = re.match(r"^Status:\s*(\S+)", line)
        if m:
            tasks[current]["status"] = m.group(1)
        m = re.match(r"^Files to (?:create/edit|edit|create):\s*(.+)$", line)
        if m:
            tasks[current]["files"] = [f.strip() for f in m.group(1).split(",") if f.strip()]
        m = re.match(r"^-?\s*planned[_ ]agents:\s*(\d+)", line, re.I)
        if m:
            tasks[current]["planned"] = int(m.group(1))
    return tasks


def state_phase(run_dir):
    text = (run_dir / "state.md").read_text(encoding="utf-8") if (run_dir / "state.md").exists() else ""
    m = re.search(r"^## Phase:\s*(\S+)", text, re.M)
    return m.group(1) if m else ""


def read_runs(run_dir):
    runs = {}
    for f in sorted((run_dir / "runs").glob("*.json")) if (run_dir / "runs").is_dir() else []:
        data = read_json(f, None)
        if isinstance(data, dict):
            data.setdefault("name", f.stem)
            runs[data["name"]] = data
    return runs


def task_id(value):
    """id задачи из `task:` письма: обрезать, первое слово, снять один ведущий `#` (`#3`, `3 (rework)` → `3`)."""
    words = value.split()
    return words[0][1:] if words and words[0].startswith("#") else (words[0] if words else "")


def read_mail(run_dir, box):
    """Письма в mail/<box>/: [{file, from, kind, task, ts, first}] по времени файла."""
    folder = run_dir / "mail" / box
    if not folder.is_dir():
        return []
    letters = []
    for f in sorted(folder.glob("*.md")):
        head = {}
        body_first = ""
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        i = 0
        for i, line in enumerate(lines):
            m = re.match(r"^\s*(from|kind|task|ts)\s*:\s*(.*)$", line, re.I)   # ключ без регистра, последний побеждает
            if not m:
                break
            head[m.group(1).lower()] = m.group(2).strip()
        for line in lines[i:]:
            if line.strip():
                body_first = line.strip()
                break
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        letters.append({"file": f.name, "from": head.get("from", ""), "kind": head.get("kind", "").upper(),
                        "task": task_id(head.get("task", "")), "ts": head.get("ts", ""), "first": body_first,
                        "at": parse_ts(head.get("ts")) or mtime})
    return letters


def mail_boxes(run_dir):
    """Почтовые ящики участников — все каталоги mail/, кроме ящика ведущего."""
    folder = run_dir / "mail"
    return sorted(d.name for d in folder.iterdir() if d.is_dir() and d.name != "lead") if folder.is_dir() else []


def seen(run_dir, box):
    f = run_dir / "mail" / box / ".seen"
    return set(f.read_text(encoding="utf-8").split()) if f.exists() else set()


def acked(run_dir):
    """Имена писем, на которые ведущий ответил, — всегда basename: `ack` принимает и путь."""
    f = run_dir / "state" / "acked.log"
    return {Path(x).name for x in f.read_text(encoding="utf-8").split()} if f.exists() else set()


def pending_open(run_dir, now):
    """Строки pending.log со статусом OPEN: [(строка, возраст в секундах или None)]."""
    f = run_dir / "pending.log"
    if not f.exists():
        return []
    out = []
    for line in f.read_text(encoding="utf-8").splitlines():
        if not line.strip() or not re.search(r"\|\s*OPEN\s*$", line):
            continue
        age = None
        m = re.match(r"^(\d{2}):(\d{2})\s", line)
        if m:
            stamp = now.astimezone().replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
            if stamp > now:
                stamp -= timedelta(days=1)
            age = int((now - stamp).total_seconds())
        out.append((line.strip(), age))
    return out


def engine_unread_sec(run_dir, runs, call):
    """Порог engine_result_unread для этого `.done`: прокси — участник с живой карточкой
    runs/<роль>.json — 180 с, пока время карточки (позднее из lastEventAt и mtime) не новее
    `.done`: сразу после чтения result.md прокси ставит note="ENGINE READ", а `.taken` — только
    после разбора и пересылки (feature-trial-medium: до 271 с). Отметил чтение — 900 с, как у
    одноразовой роли ведущего без карточки; карточка done/stopped считается отсутствующей."""
    card = runs.get(call["role"])
    if not card or str(card.get("status", "")) in ("done", "stopped"):
        return ENGINE_UNREAD_SEC
    card_at = parse_ts(card.get("lastEventAt"))
    try:
        card_mtime = datetime.fromtimestamp((run_dir / "runs" / f"{call['role']}.json").stat().st_mtime, tz=timezone.utc)
        card_at = max(card_at, card_mtime) if card_at else card_mtime
    except OSError:
        pass
    if card_at and card_at.timestamp() > call["done_at"]:
        return ENGINE_UNREAD_SEC
    return ENGINE_UNREAD_PROXY_SEC


def engine_calls(run_dir, now):
    """Маркеры движков, как их видит `run-engine.sh --status`: [{role, base, state, age}]."""
    calls = []
    root = run_dir / "engine"
    if not root.is_dir():
        return calls
    for pid_file in sorted(root.glob("*/*.out.pid")):
        base = pid_file.with_suffix("")          # …/NNN.out
        role = pid_file.parent.name
        done = Path(str(base) + ".done")
        taken = Path(str(base) + ".taken")
        try:
            pid = int(pid_file.read_text().strip() or 0)
        except (OSError, ValueError):
            pid = 0
        if done.exists():
            state = "taken" if taken.exists() else "done-unread"
            failed = "status=failed" in done.read_text(encoding="utf-8", errors="replace")
            age = int(now.timestamp() - done.stat().st_mtime)
            calls.append({"role": role, "base": str(base), "state": state, "failed": failed, "age": age,
                          "done_at": done.stat().st_mtime})
        else:
            alive = False
            if pid:
                try:
                    os.kill(pid, 0)
                    alive = True
                except ProcessLookupError:
                    alive = False
                except PermissionError:
                    alive = True
            calls.append({"role": role, "base": str(base), "state": "running" if alive else "dead",
                          "failed": False, "age": int(now.timestamp() - pid_file.stat().st_mtime)})
    return calls


def touched_since(root, files, since):
    """Есть ли среди файлов задачи файл, изменённый после `since` (по mtime).

    Не `git status`: он показывает всё незакоммиченное, и правка часовой давности
    делала бы участника живым до самого коммита (24.09.2026 так 45 минут не
    видели кодера, уснувшего на зависшем jest). Нового, ещё не созданного файла
    нет — значит, и правки нет."""
    if not root or not files or since is None:
        return False
    for f in files:
        try:
            mtime = datetime.fromtimestamp((Path(root) / f).stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if mtime > since:
            return True
    return False


BUSY_CMD = re.compile(r"^(pytest|py\.test|jest|yarn|npm|craco)(\.c?js)?$")


def list_processes():
    """Процессы машины из /proc: [{pid, cwd, argv, started}]. Нет /proc — пустой список.

    Вынесено отдельно, чтобы тест мог подменить список процессов."""
    proc = Path("/proc")
    if not proc.is_dir():
        return []
    try:
        btime = next(int(l.split()[1]) for l in (proc / "stat").read_text().splitlines() if l.startswith("btime "))
        hz = os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, StopIteration):
        return []
    out = []
    for d in proc.iterdir():
        if not d.name.isdigit():
            continue
        try:
            argv = [a for a in (d / "cmdline").read_bytes().decode("utf-8", "replace").split("\0") if a]
            cwd = os.readlink(d / "cwd")
            stat = (d / "stat").read_text()
            start_ticks = int(stat[stat.rindex(")") + 2:].split()[19])
        except (OSError, ValueError, IndexError):
            continue
        if argv:
            out.append({"pid": int(d.name), "cwd": cwd, "argv": argv,
                        "started": datetime.fromtimestamp(btime + start_ticks / hz, tz=timezone.utc)})
    return out


def busy_command(root, now, window_sec):
    """Тесты или сборка под корнем, начатые не раньше окна молчания: команда или None.

    Учитывается cwd процесса внутри `root`; сам супервизор и его цикл — не в счёт."""
    if not root:
        return None
    try:
        base = Path(root).resolve()
    except OSError:
        return None
    me = {os.getpid(), os.getppid()}
    for p in list_processes():
        argv = p.get("argv") or []
        if p.get("pid") in me or any("supervisor-" in a for a in argv):
            continue
        cwd = Path(p.get("cwd") or "/")
        if cwd != base and base not in cwd.parents:
            continue
        started = p.get("started")
        if started is None or (now - started).total_seconds() > window_sec:
            continue
        hit = any(BUSY_CMD.match(Path(a).name) for a in argv[:4]) or \
            any(a == "-m" and argv[i + 1:i + 2] == ["pytest"] for i, a in enumerate(argv))
        if hit:
            return " ".join(argv)[:120]
    return None


def shared_executors(run_dir, runs, calls, now, add):
    """Правила карточек исполнителей в режиме общего протокола (свою карточку исполнитель не ведёт)."""
    for name, run in runs.items():
        status = str(run.get("status", "running"))
        if status in ("done", "stopped", "idle"):
            continue
        ref = f"runs/{name}.json"
        if status == "stuck":
            add("P0", "stuck", run=name, detail=run.get("note", ""), ref=ref)
            continue
        started = parse_ts(run.get("startedAt")) or parse_ts(run.get("spawnedAt"))
        age = int((now - started).total_seconds()) if started else None
        mine = [c for c in calls if c["role"] == name]
        report = run.get("report")
        if report:
            path = run_dir / report
            try:
                fresh = path.is_file() and (started is None or path.stat().st_mtime >= started.timestamp())
            except OSError:
                fresh = False
            if fresh:
                add("P1", "executor_report_ready", run=name, detail=f"отчёт готов: {report}", ref=report)
                continue
        if run.get("executor") == "engine" and not mine and age is not None and age >= FIRST_MINUTE_SEC:
            add("P1", "engine_not_started", run=name, detail=f"{age} с после старта, а вызова движка с этим именем нет", ref=ref)
            continue
        timeout = int(run.get("timeoutSec") or DEFAULT_EXECUTOR_SEC)
        if age is not None and age >= timeout and not any(c["state"] == "running" for c in mine):
            add("P0", "executor_overdue", run=name,
                detail=f"{age // 60} мин после старта при сроке {timeout // 60} мин, статус {status}", ref=ref)


# ---------------------------------------------------------------- бюджеты (121-567, X1)
# Пороги по прошлым прогонам (tools/calibrate_budgets.py, docs/budgets.md): максимум по сложности с запасом 25 %.
BUDGET_DEFAULTS = {"SIMPLE": (17, 3, 4), "MEDIUM": (27, 4, 9), "COMPLEX": (42, 5, 19)}
BUDGET_KEYS = ("max_agents", "max_review_rounds", "max_concurrent_agents")
BUDGET_MODES = ("observe", "enforce", "off")


def state_budgets(run_dir):
    """Раздел `## Budgets` state.md: {mode, max_agents, max_review_rounds, max_concurrent_agents} или None —
    раздела нет (прогоны 1.1.x): бюджетных правил нет вовсе."""
    path = run_dir / "state.md"
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    m = re.search(r"^## Budgets[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not m:
        return None
    body = m.group(1)
    mode = re.search(r"mode:\s*(\w+)", body)
    out = {"mode": mode.group(1) if mode and mode.group(1) in BUDGET_MODES else "observe"}
    for key in BUDGET_KEYS:
        n = re.search(rf"{key}:\s*(\d+)", body)
        out[key] = int(n.group(1)) if n else None
    return out


def budgets_section(complexity, flags=()):
    """Текст раздела `## Budgets` для state.md: пороги по сложности, `--budget=enforce|off|observe|a/r/c`."""
    level = str(complexity).upper()
    if level not in BUDGET_DEFAULTS:
        raise SystemExit(f"supervisor-tick budgets: сложность {complexity!r}, ждём SIMPLE, MEDIUM или COMPLEX")
    mode, nums = "observe", list(BUDGET_DEFAULTS[level])
    for flag in flags:
        for part in str(flag).replace("--budget=", "").split(","):
            if part in BUDGET_MODES:
                mode = part
            elif re.fullmatch(r"\d+/\d+/\d+", part):
                nums = [int(x) for x in part.split("/")]
            elif part:
                raise SystemExit(f"supervisor-tick budgets: --budget={part!r} — ждём observe, enforce, off или a/r/c")
    return (f"## Budgets\n- mode: {mode}\n"
            f"- max_agents: {nums[0]}  max_review_rounds: {nums[1]}  max_concurrent_agents: {nums[2]}\n")


def run_agents(run_dir, runs):
    """{имя участника: id задачи или ""}: карточки runs/ и одноразовые роли без карточки (engine/<имя>/)."""
    names = {n: str(r.get("task", "") or "") for n, r in runs.items()}
    if (run_dir / "engine").is_dir():
        for d in (run_dir / "engine").iterdir():
            if d.is_dir() and d.name not in names:
                m = re.match(r"task([^-]+)-", d.name)
                names[d.name] = m.group(1) if m else ""
    for name, task in list(names.items()):
        if not task:
            m = re.match(r"task([^-]+)-", name)
            names[name] = m.group(1) if m else ""
    return names


def first_line(path):
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip():
                return line.strip()
    except OSError:
        pass
    return ""


def review_fails(run_dir):
    """{задача: число вердиктов «на доработку»}: отчёты ревью (общий протокол) и письма-вердикты (Claude Code)."""
    fails = {}
    for f in sorted((run_dir / "reports").glob("review-task*-r*.md")) if (run_dir / "reports").is_dir() else []:
        m = re.match(r"review-task([^-]+)-r\d+", f.name)
        if m and re.search(r"CHANGES REQUESTED|\bFAIL\b", first_line(f)):
            fails[m.group(1)] = fails.get(m.group(1), 0) + 1
    for box in mail_boxes(run_dir) if (run_dir / "mail").is_dir() else []:
        for letter in read_mail(run_dir, box):
            if letter["kind"] == "VERDICT" and letter["task"] and re.search(r"CHANGES|FAIL", letter["first"]):
                fails[letter["task"]] = fails.get(letter["task"], 0) + 1
    return fails


def budget_events(run_dir, tasks, runs, budgets):
    """Превышения бюджета сейчас: [(kind, ключ памяти, деталь)]."""
    if not budgets or budgets["mode"] == "off":
        return []
    events = []
    agents = run_agents(run_dir, runs)
    if budgets["max_agents"] is not None and len(agents) > budgets["max_agents"]:
        events.append(("agent_budget_exceeded", "agents",
                       f"участников {len(agents)} при max_agents {budgets['max_agents']}"))
    live = [n for n, r in runs.items() if str(r.get("status", "")) in LIVE]
    if budgets["max_concurrent_agents"] is not None and len(live) > budgets["max_concurrent_agents"]:
        events.append(("concurrency_exceeded", f"concurrent:{len(live)}",
                       f"работают одновременно {len(live)} при max_concurrent_agents {budgets['max_concurrent_agents']}: "
                       + ", ".join(sorted(live))))
    for task, n in sorted(review_fails(run_dir).items()):
        if budgets["max_review_rounds"] is not None and n > budgets["max_review_rounds"]:
            events.append(("review_rounds_exceeded", f"rounds:{task}",
                           f"task {task}: вердиктов на доработку {n} при max_review_rounds {budgets['max_review_rounds']}"))
    per_task = {}
    for name, task in agents.items():
        if task:
            per_task[task] = per_task.get(task, 0) + 1
    for task, info in sorted(tasks.items()):
        planned = info.get("planned")
        if planned is not None and per_task.get(task, 0) > planned:
            events.append(("fanout_planned_exceeded", f"fanout:{task}",
                           f"task {task}: участников {per_task[task]} при planned_agents {planned}"))
    return events


def read_agent_commits(run_dir, runs):
    """Участники с `access: read`, у которых есть коммиты (поле `commits` карточки или отчёт COMMITTED)."""
    found = []
    for name, card in runs.items():
        if str(card.get("access", "")) != "read":
            continue
        report = run_dir / str(card["report"]) if card.get("report") else None
        if card.get("commits") or (report and first_line(report).startswith(("COMMITTED", "COMMITS"))):
            found.append((name, ", ".join(card.get("commits") or []) or f"отчёт {card.get('report')}"))
    return found


def tick(run_dir, root=None, now=None, mode="claude"):
    if mode not in MODES:
        raise SystemExit(f"supervisor-tick: --mode ждёт одно из {', '.join(MODES)}")
    shared = mode == "shared"
    run_dir = Path(run_dir).resolve()
    if not run_dir.is_dir():
        raise SystemExit(f"supervisor-tick: нет каталога прогона {run_dir}")
    now = now or datetime.now(timezone.utc).astimezone()
    root = Path(root) if root else run_dir.resolve().parents[1]
    memory_file = run_dir / "state" / "supervisor.json"
    memory = read_json(memory_file, {})
    firsts = memory.get("firstMinute", {})          # name -> "ok" | "p2" | "p1" (какая тревога уже подана)
    checkpoints = memory.get("checkpoints", {})     # name -> ISO последней контрольной точки

    tasks = plan_tasks(run_dir)
    runs = read_runs(run_dir)
    lead_mail = [] if shared else read_mail(run_dir, "lead")
    done_ack = acked(run_dir)
    actions = []

    def add(priority, kind, run="", detail="", ref=""):
        actions.append({"priority": priority, "kind": kind, "run": run, "detail": detail, "ref": ref})

    # ---- P0: копии `queued`, застрявшие у ведущего (почта есть только без --mode shared)
    for line, age in ([] if shared else pending_open(run_dir, now)):
        add("P0" if age is None or age >= PENDING_STALE_SEC else "P1", "deliver_pending", detail=line, ref="pending.log")

    # ---- письма ведущему
    mailed_done = {}   # task -> letter
    for letter in lead_mail:
        if letter["file"] in done_ack:
            continue
        kind = letter["kind"]
        if kind == "DONE":
            mailed_done[letter["task"]] = letter
        elif kind in ("STUCK", "ENGINE_DOWN"):
            add("P0", kind.lower(), run=letter["from"], detail=letter["first"], ref=f"mail/lead/{letter['file']}")
        elif kind in NEEDS_ANSWER:
            add("P1", "answer_needed", run=letter["from"], detail=f"{kind}: {letter['first']}", ref=f"mail/lead/{letter['file']}")
    reviewer_box = run_dir / "mail" / "unified-reviewer"
    reviewed = {m["task"] for m in read_mail(run_dir, "unified-reviewer") if m["kind"] == "REVIEW"} if reviewer_box.is_dir() else None
    for task, letter in mailed_done.items():
        status = tasks.get(task, {}).get("status", "")
        if status == "DONE" or status.startswith(("ACCEPTING", "REOPENED")):
            continue   # принято, либо приёмка уже идёт / задача вернулась кодеру — DONE-письмо отработано
        if reviewed is not None and task not in reviewed:
            # ящик рецензента есть, а REVIEW по задаче в нём нет: кодер сдал DONE мимо ревью
            # (22.10.2026, прокси-кодер coder-5 закоммитил без вердикта; тик показал штатный accept_needed)
            add("P1", "done_without_review", run=letter["from"],
                detail=f"task {task}: DONE без REVIEW в mail/unified-reviewer/ (PLAN: {status or 'нет задачи'})",
                ref=f"mail/lead/{letter['file']}")
            continue
        add("P1", "accept_needed", run=letter["from"], detail=f"task {task}: {letter['first']} (PLAN: {status or 'нет задачи'})",
            ref=f"mail/lead/{letter['file']}")

    # ---- письма участникам: лежит, а адресат не взял (SendMessage ушёл `queued` и пропал)
    for box in ([] if shared else mail_boxes(run_dir)):
        card = runs.get(box, {})
        if str(card.get("status", "")) in ("done", "stopped"):
            continue   # адресат закончил: письмо ему уже не нужно, ротацию ведёт ведущий
        card_at = parse_ts(card.get("lastEventAt"))
        try:   # роль без Bash ставит lastEventAt руками — mtime карточки честнее
            card_mtime = datetime.fromtimestamp((run_dir / "runs" / f"{box}.json").stat().st_mtime, tz=timezone.utc)
            card_at = max(card_at, card_mtime) if card_at else card_mtime
        except OSError:
            pass
        taken = seen(run_dir, box)
        for letter in read_mail(run_dir, box):
            if letter["file"] in taken or letter["file"] in done_ack:
                continue
            if card_at and card_at >= letter["at"]:
                continue
            age = int((now - letter["at"]).total_seconds())
            if age >= UNDELIVERED_SEC:
                add("P0", "undelivered_mail", run=box,
                    detail=f"{letter['kind'] or 'письмо'} от {letter['from'] or '?'} лежит {age // 60} мин, адресат не взял: {letter['first']}",
                    ref=f"mail/{box}/{letter['file']}")

    # ---- приёмка: отчёт проверяющего лежит, а ведущий ещё не вынес решение
    for task, info in tasks.items():
        if info["status"].startswith("ACCEPTING"):
            report = run_dir / "reports" / f"accept-task{task}.md"
            if report.exists():
                lines = report.read_text(encoding="utf-8", errors="replace").splitlines()
                first = next((line for line in lines if line.strip()), "")   # вердикт — только первая непустая строка
                verdict = "PASS" if re.search(r"^ACCEPT:.*\bPASS\b", first) else "FAIL/?"
                add("P1", "accept_result", detail=f"task {task}: отчёт приёмки готов ({verdict})", ref=f"reports/accept-task{task}.md")

    # ---- движки
    calls = engine_calls(run_dir, now)
    for call in calls:
        if call["state"] == "dead":
            add("P0", "engine_dead", run=call["role"], detail=f"процесс исчез без маркера .done", ref=call["base"])
        elif call["state"] == "done-unread" and call["failed"]:
            add("P0", "engine_failed_unread", run=call["role"], detail="движок упал, отчёт не забран", ref=call["base"] + ".done")
        elif call["state"] == "done-unread" and call["age"] >= engine_unread_sec(run_dir, runs, call):
            add("P0", "engine_result_unread", run=call["role"],
                detail=f"результат готов {call['age'] // 60} мин, никто не забрал", ref=call["base"] + ".result.md")
        elif shared and call["state"] == "done-unread":
            # цикл ведёт ведущий: досчитавший движок — это его следующий шаг, а не повод ждать 15 минут
            add("P1", "engine_result_ready", run=call["role"], detail=f"результат готов {call['age']} с назад",
                ref=call["base"] + ".result.md")

    if shared:
        shared_executors(run_dir, runs, calls, now, add)

    # ---- бюджеты и контракт доступа: только когда ведущий записал `## Budgets` / карточки с `access`
    budgets = state_budgets(run_dir)
    budget_fired = list(memory.get("budget", []))
    for name, what in read_agent_commits(run_dir, runs):   # нарушение контракта, не бюджет: P0 в любом режиме
        if f"read_owns:{name}" not in budget_fired:
            budget_fired.append(f"read_owns:{name}")
            add("P0", "read_agent_owns_files", run=name, detail=f"участник с access: read закоммитил: {what}",
                ref=f"runs/{name}.json")
    for kind, key, detail in budget_events(run_dir, tasks, runs, budgets):
        if key in budget_fired:
            continue   # одно превышение — одно событие
        budget_fired.append(key)
        if budgets["mode"] == "enforce":
            add("P1", kind, detail=detail, ref="state.md")
        else:
            add("P3", "budget_note", detail=f"{kind}: {detail}", ref="state.md")

    # ---- участники: первая минута, контрольные точки, молчание
    signals_from = {l["from"] for l in lead_mail}
    busy = {}   # окно -> команда живого теста под корнем; считается по нужде, раз за тик
    for name, run in ([] if shared else runs.items()):
        status = str(run.get("status", "running"))
        if status in ("done", "stopped", "idle"):
            continue
        if status == "stuck":
            add("P0", "stuck", run=name, detail=run.get("note", ""), ref=f"runs/{name}.json")
            continue
        spawned = parse_ts(run.get("startedAt")) or parse_ts(run.get("spawnedAt"))
        last = parse_ts(run.get("lastEventAt")) or spawned
        if last and spawned and last < spawned:
            last = spawned
        age = int((now - spawned).total_seconds()) if spawned else None
        since = int((now - last).total_seconds()) if last else None
        interval = int(run.get("checkAfterSec") or DEFAULT_CHECK_SEC)
        task = str(run.get("task", ""))
        files = run.get("files") or tasks.get(task, {}).get("files", [])

        # первая минута — только у роли с задачей, и каждая тревога один раз
        if not task or run.get("startedAt"):
            firsts[name] = "ok"
        if age is not None and firsts.get(name) not in ("ok", "p1"):
            alive = (last and spawned and last > spawned) or name in signals_from or touched_since(root, files, spawned) \
                or any(c["role"] == name for c in engine_calls(run_dir, now))
            if alive:
                firsts[name] = "ok"
            elif age >= FIRST_MINUTE_LOUD_SEC:
                firsts[name] = "p1"
                add("P1", "first_minute_silent", run=name, detail=f"{age} с после старта — ни письма, ни правок, ни движка",
                    ref=f"runs/{name}.json")
            elif age >= FIRST_MINUTE_SEC and firsts.get(name) != "p2":
                firsts[name] = "p2"
                add("P2", "first_minute_silent", run=name, detail=f"{age} с после старта без признаков жизни", ref=f"runs/{name}.json")

        # молчание и контрольные точки — после первой минуты (живой или уже названный молчащим)
        if since is not None and firsts.get(name) in ("ok", "p1"):
            if since >= 2 * interval and not touched_since(root, files, now - timedelta(seconds=2 * interval)):
                if 2 * interval not in busy:
                    busy[2 * interval] = busy_command(root, now, 2 * interval)
                cmd = busy[2 * interval]
                if cmd:
                    add("P3", "busy", run=name, detail=f"busy: {cmd} — {since // 60} мин без событий, но тесты/сборка идут",
                        ref=f"runs/{name}.json")
                else:
                    add("P0", "silent_too_long", run=name, detail=f"{since // 60} мин без событий и без правок в файлах задачи",
                        ref=f"runs/{name}.json")
            elif since >= interval:
                fired = parse_ts(checkpoints.get(name))
                if not fired or fired < last or (now - fired).total_seconds() >= interval:
                    checkpoints[name] = now.isoformat(timespec="seconds")
                    add("P3", "checkpoint", run=name, detail=f"{since // 60} мин с последнего события, статус {status}",
                        ref=f"runs/{name}.json")

    if not actions:
        add("P4", "ok", detail="тихо: событий, требующих ведущего, нет")
    actions.sort(key=lambda a: PRIORITY[a["priority"]])

    snapshot = {
        "ts": now.isoformat(timespec="seconds"),
        "runDir": str(run_dir),
        "phase": state_phase(run_dir),
        "tasks": {k: v["status"] for k, v in tasks.items()},
        "runs": {n: {"status": r.get("status", ""), "task": r.get("task", "")} for n, r in runs.items()},
        "actions": actions,
        "firstMinute": firsts,
        "checkpoints": checkpoints,
    }
    if shared:
        snapshot["mode"] = "shared"
    if budgets is not None or budget_fired:
        snapshot["budget"] = budget_fired
    (run_dir / "state").mkdir(exist_ok=True)
    tmp = memory_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, memory_file)
    return snapshot


def ack(run_dir, name):
    run_dir = Path(run_dir)
    (run_dir / "state").mkdir(exist_ok=True)
    with (run_dir / "state" / "acked.log").open("a", encoding="utf-8") as f:
        f.write(Path(name).name + "\n")


def plan_check(run_dir):
    """`plan <run>`: сумма planned_agents против max_agents. (код, строки): 2 — превышение в enforce или нет
    раздела ## Budgets; в observe превышение — предупреждение и 0; off — 0."""
    run_dir = Path(run_dir)
    budgets = state_budgets(run_dir)
    if budgets is None:
        return 2, ["PLAN BUDGET: в state.md нет раздела ## Budgets (supervisor-tick.py budgets <сложность>)"]
    if budgets["mode"] == "off":
        return 0, ["PLAN BUDGET: off — бюджет не проверяется"]
    tasks = plan_tasks(run_dir)
    total = sum(t.get("planned", 0) for t in tasks.values())
    missing = [t for t, info in tasks.items() if "planned" not in info and not info["status"].startswith("DONE")]
    lines = [f"PLAN BUDGET: planned_agents {total} из max_agents {budgets['max_agents']} ({budgets['mode']})"]
    if missing:
        lines.append("без planned_agents: " + ", ".join(f"task {t}" for t in missing))
    over = budgets["max_agents"] is not None and total > budgets["max_agents"]
    if over and budgets["mode"] == "enforce":
        lines.append("ПРЕВЫШЕНИЕ: сократи план или спроси пользователя")
        return 2, lines
    if over:
        lines.append("⚠️ превышение (observe): запиши в сводку, прогон идёт")
    return 0, lines


def git_has_commit(root, sha):
    import subprocess
    try:
        return subprocess.run(["git", "-C", str(root), "cat-file", "-e", f"{sha}^{{commit}}"],
                              capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def postrun_check(run_dir, root=None, now=None):
    """`postrun-check <run>`: механические сверки перед сводкой. Список находок (пусто — всё сходится)."""
    run_dir = Path(run_dir).resolve()
    root = Path(root) if root else run_dir.parents[2] if run_dir.parent.parent.name == ".supertick" else run_dir.parents[1]
    now = now or datetime.now(timezone.utc).astimezone()
    findings = []
    tasks = plan_tasks(run_dir)
    reports = run_dir / "reports"
    for task, info in tasks.items():
        if info["status"] != "DONE":
            continue
        shas = []
        for f in sorted(reports.glob(f"task{task}-coder*.md")) if reports.is_dir() else []:
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                m = re.match(r"^\s*COMMITS?:\s*(.+)$", line)
                if m:
                    shas += re.findall(r"\b[0-9a-f]{7,40}\b", m.group(1))
        if not shas:
            findings.append(f"task {task}: DONE без строки COMMITS: в отчётах кодера (reports/task{task}-coder*.md)")
        for sha in shas:
            if not git_has_commit(root, sha):
                findings.append(f"task {task}: коммита {sha} нет в репозитории {root}")
        accept = reports / f"accept-task{task}.md"
        if not re.search(r"^ACCEPT:.*\bPASS\b", first_line(accept)):
            findings.append(f"task {task}: DONE без reports/accept-task{task}.md с ACCEPT … PASS")
    for line, _ in pending_open(run_dir, now):
        findings.append(f"pending.log: OPEN — {line}")
    for call in engine_calls(run_dir, now):
        if call["state"] == "done-unread":
            findings.append(f"движок {call['role']}: результат не забран ({call['base']}.done без .taken)")
        elif call["state"] in ("dead", "running"):
            findings.append(f"движок {call['role']}: {call['state']} ({call['base']})")
    budgets = state_budgets(run_dir)
    events = budget_events(run_dir, tasks, read_runs(run_dir), budgets)
    report = run_dir / "VERIFICATION_REPORT.md"
    text = report.read_text(encoding="utf-8", errors="replace") if report.exists() else ""
    for kind, _, detail in events:
        if kind not in text and "Budgets" not in text:
            findings.append(f"бюджет: {kind} ({detail}) не записан в VERIFICATION_REPORT.md (строка Budgets:)")
    for name, what in read_agent_commits(run_dir, read_runs(run_dir)):
        findings.append(f"read_agent_owns_files: {name} — {what}")
    mirror = run_dir / "state" / "run.json"
    if mirror.exists() and "validate_run" in globals():
        findings += [f"state/run.json: {p}" for p in validate_run(run_dir)]
    return findings


def main(argv):
    if argv and argv[0] == "budgets":
        if len(argv) < 2:
            raise SystemExit("использование: supervisor-tick.py budgets <SIMPLE|MEDIUM|COMPLEX> [--budget=…]")
        print(budgets_section(argv[1], argv[2:]), end="")
        return 0
    if argv and argv[0] == "plan":
        if len(argv) != 2:
            raise SystemExit("использование: supervisor-tick.py plan <run-dir>")
        code, lines = plan_check(argv[1])
        print("\n".join(lines))
        return code
    if argv and argv[0] == "postrun-check":
        rest, root = argv[1:], None
        if "--root" in rest:
            i = rest.index("--root")
            root = rest[i + 1]
            rest = rest[:i] + rest[i + 2:]
        if len(rest) != 1:
            raise SystemExit("использование: supervisor-tick.py postrun-check <run-dir> [--root <репозиторий>]")
        found = postrun_check(rest[0], root)
        print("\n".join(["POSTRUN CHECK: находок нет"] if not found else [f"POSTRUN CHECK: находок {len(found)}"] + [f"- {f}" for f in found]))
        return 1 if found else 0
    if argv and argv[0] == "ack":
        if len(argv) != 3:
            raise SystemExit("использование: supervisor-tick.py ack <run-dir> <файл письма>")
        ack(argv[1], argv[2])
        return 0
    if not argv:
        raise SystemExit(__doc__)
    run_dir, root, as_json, now, mode = argv[0], None, False, None, "claude"
    rest = argv[1:]
    while rest:
        a = rest.pop(0)
        if a == "--root":
            root = rest.pop(0)
        elif a == "--json":
            as_json = True
        elif a == "--now":
            now = parse_ts(rest.pop(0))
        elif a == "--mode":
            mode = rest.pop(0) if rest else ""
        else:
            raise SystemExit(f"supervisor-tick: неизвестный аргумент {a}")
    snap = tick(run_dir, root, now, mode)
    if as_json:
        print(json.dumps(snap, ensure_ascii=False))
    else:
        for a in snap["actions"]:
            who = f" {a['run']}" if a["run"] else ""
            ref = f"  [{a['ref']}]" if a["ref"] else ""
            print(f"{a['priority']} {a['kind']}{who}: {a['detail']}{ref}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
