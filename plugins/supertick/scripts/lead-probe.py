#!/usr/bin/env python3
"""lead-probe.py — проба пригодности ведущего (121-567, H2, п. 4.5 ТЗ).

Ведущий держит длинный протокол; слабая модель с ним не справится. Проба запускает среду ведущего в
headless-режиме на выбранной модели с крошечной задачей. Все роли — на мок-движке со сценарием:
кодер пишет файлы и коммитит, на первом круге первой задачи оставляет метку PROBE-BUG, рецензент
отвечает CHANGES REQUESTED, потом APPROVED, приёмщик — PASS. Итог выносится по файлам прогона,
без оценки текста моделью.

Обычно её зовут через `supertick-setup.py lead-probe <профиль>` (итог пишется в профиль); этот файл —
стенд, запуск и проверки:

  lead-probe.py --host kimi|codex [--model M] [--root НАБОР] [--timeout С] [--keep КАТАЛОГ] [--json]

Стенд — во временном каталоге: `repo/` (git, задача), `root/` (копия набора, где supervisor-wait.sh
обёрнут счётчиком вызовов), `probe/` (сценарий мока, его журнал, конфиг пробы, вывод среды).
Конфиг пробы отдаётся среде через SUPERTICK_CONFIG и перекрывает пользовательский; пользовательские
файлы проба не трогает.

Проверки — по порядку; итог `passed` или `failed: <первая не прошедшая>`:
  finish  среда завершилась сама и в срок
  root    в state.md строка `Plugin root:` указывает на корень набора
  state   state.md с обязательными разделами и базовым коммитом стенда
  plan    PLAN.md: задачи в формате шаблона (`## Task N:`, Status, Blocked by, Files, критерии)
  engine  исполнители запущены через run-engine.sh: вызовов мока не больше, чем запусков в ledger
  wait    ожидание через supervisor-wait.sh --mode shared, без опроса sleep
  cycle   задача DONE прошла ревью и приёмку; после CHANGES REQUESTED кодер продолжен, а не заменён
  taken   результаты разобраны: финальный тик без P0/P1
  hands   исходники руками ведущего не правились: коммиты только мок-кодера, дерево чистое,
          .git/hooks и .git/config не тронуты (риск R11)

Замечания (не влияют на итог, видны в нём и пишутся в профиль):
  tests   ведущий сам запускал тесты или сборку (по LEAD.md это дело проверяющих)

Коды выхода CLI: 0 passed; 1 failed; 2 неверный вызов (хост без пробы, нет набора, нет CLI).
"""
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import importlib.util
from datetime import date
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("git_guard", SCRIPTS_DIR / "git-guard.py")
gg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gg)
CHECKS = ("finish", "root", "state", "plan", "engine", "wait", "cycle", "taken", "hands")
MOCK_AUTHOR = "supertick-mock-coder"
FEATURE = ("Добавь в src/probe_calc.py функцию double(x), которая возвращает 2 * x, "
           "и тест в tests/test_probe_calc.py")
LEAD_ARGS = "--no-research --complexity=simple --force-team --coders=1"
PROMPT = ("Запусти навык supertick:team-feature (инструмент Skill) с аргументами: «{feature}» {args}. "
          "Это проба ведущего: исполни протокол навыка до конца, вопросов не задавай.")
PROMPTS = {   # у Codex нет инструмента Skill: навык зовётся по имени, файл SKILL.md он читает сам
    "codex": ("Используй навык supertick:team-feature с аргументами: «{feature}» {args}. "
              "Это проба ведущего: исполни протокол навыка до конца в этом ходе, вопросов не задавай."),
}
# Среды, для которых проба есть: argv headless-запуска. OpenCode — с его адаптером (H3).
HOST_ARGV = {
    "kimi": lambda bin_, model, prompt: [bin_] + (["-m", model] if model else []) +
                                       ["--output-format", "stream-json", "-p", prompt],
    # ведущий Codex — без песочницы, как его запускает пользователь (engine-config LAUNCH): иначе
    # исполнители `codex exec` из его shell не стартуют
    "codex": lambda bin_, model, prompt: [bin_, "exec", "--skip-git-repo-check", "--sandbox", "danger-full-access"] +
                                        (["-m", model] if model else []) + ["--json", "--", prompt],
}
HOST_BINARY = {"kimi": "kimi", "codex": "codex"}
INSTALLED_ROOT = {"kimi": "~/.kimi-code/plugins/managed/supertick",
                  "codex": "${CODEX_HOME:-~/.codex}/plugins/cache/supertick-local/supertick/*"}
NO_PROBE = {
    "claude-code": "ведущий Claude Code работает по своему протоколу 1.1.x; проба общего протокола к нему не относится",
    "opencode": "проба ведущего OpenCode появится вместе с адаптером OpenCode (H3)",
}
MOCK_ROLES = ("coder", "unified-reviewer", "second-reviewer", "acceptance-checker", "spec-verifier", "ci-verifier",
              "risk-tester", "codebase-researcher", "reference-researcher", "web-researcher", "legacy-scanner",
              "tech-lead", "architect", "architect-frontend", "architect-backend", "architect-systems")

# Сценарий мок-движка (SUPERTICK_MOCK_ACT): одна программа на все роли, роль — из строки ROLE BRIEF FILE
# первого задания сессии, дальше из памяти сессии. Журнал вызовов — calls.jsonl, следы правок чужими
# руками — tamper.log.
ACT = r'''
import hashlib, json, os, re, subprocess, sys
from pathlib import Path
p = os.environ.get("MOCK_PROMPT", ""); access = os.environ.get("MOCK_ACCESS", "?")
sess = os.environ.get("MOCK_SESSION", "?"); state = Path(os.environ["SUPERTICK_MOCK_STATE"])
AUTHOR = "%(author)s"
mem_file = state / ("act-" + re.sub(r"[^\w.-]", "_", sess) + ".json")
mem = json.loads(mem_file.read_text()) if mem_file.exists() else {}
brief = re.search(r"ROLE BRIEF FILE: \S*?([\w-]+)\.md", p)
role = brief.group(1) if brief else mem.get("role", "?")
mem["role"] = role
m = re.search(r"task #(\d+)", p) or re.search(r"## Task (\d+)", p)
if m:
    mem.setdefault("task", m.group(1))
task = mem.get("task", "?")
def log(kind, **extra):
    rec = {"role": role, "session": sess, "task": task, "access": access, "kind": kind, **extra}
    with (state / "calls.jsonl").open("a") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
def out(text):
    mem_file.write_text(json.dumps(mem)); print(text); sys.exit(0)
def sha(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return ""
def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True)
def task_files(text):
    m = re.search(r"Files to create/edit:\s*(.+)", text)
    raw = m.group(1) if m else ""
    found = [t.strip("`'\" ,;()") for t in re.split(r"[,\s]+", raw)]
    found = [t for t in found if t and "<" not in t and ("/" in t or "." in t) and not t.startswith("-")]
    return found or ["src/probe_calc.py"]
def body(path, buggy):
    name = Path(path).name
    if path.endswith(".py") and name.startswith("test"):
        text = ("import unittest\n\nfrom src.probe_calc import double\n\n\n"
                "class DoubleTest(unittest.TestCase):\n    def test_double(self):\n"
                "        self.assertEqual(double(3), 6)\n")
    elif path.endswith(".py"):
        text = "def double(x):\n    return 2 * x\n"
    else:
        text = "# " + name + "\n\nЗаписано мок-кодером пробы ведущего.\n"
    return ("# PROBE-BUG\n" if buggy else "") + text
def write_files(buggy):
    mem["written"] = {}
    for f in mem["files"]:
        Path(f).parent.mkdir(parents=True, exist_ok=True)
        Path(f).write_text(body(f, buggy))
        mem["written"][f] = sha(f)
if role == "coder":
    if "files" not in mem:
        mem["files"] = task_files(p)
    if re.search(r"APPROVED: task #|Commit now", p):
        for f, h in (mem.get("written") or {}).items():
            if sha(f) != h:
                with (state / "tamper.log").open("a") as fh:
                    fh.write(f"{f}: изменён не мок-кодером перед коммитом\n")
        git("add", "--", *mem["files"])
        git("-c", "user.name=" + AUTHOR, "-c", "user.email=mock-coder@supertick.invalid",
            "commit", "-q", "-m", f"task {task}: lead-probe", "--", *mem["files"])
        head = git("log", "-1", "--format=%%h").stdout.strip()
        log("commit", commit=head)
        out(f"COMMITTED: task #{task}\nCOMMITS: {head}\nSUMMARY: {', '.join(mem['files'])}")
    first = not (state / "first-coder").exists()
    fix = bool(re.search(r"CHANGES REQUESTED|ACCEPTANCE FAILED|ANSWER:|дифф пуст", p))
    if first and not fix:
        (state / "first-coder").write_text(sess)
    buggy = first and not fix
    write_files(buggy)
    log("fix" if fix else "start", files=mem["files"], buggy=buggy)
    out(f"READY FOR REVIEW: task #{task}\nFILES: {', '.join(mem['files'])}")
if role == "unified-reviewer":
    blob = git("diff", "HEAD").stdout
    for line in git("status", "--porcelain", "-uall").stdout.splitlines():
        path = line[3:]
        if line.startswith("??") and not path.startswith(".supertick/"):
            try:
                blob += Path(path).read_text(errors="replace")
            except OSError:
                pass
    if "PROBE-BUG" in blob:
        log("verdict", verdict="CHANGES REQUESTED")
        out(f"VERDICT: task #{task} — CHANGES REQUESTED\nCRITICAL 0 · MAJOR 1 · MINOR 0\n\n### MAJOR\n"
            "- [quality] строка `# PROBE-BUG` в начале файла — убрать")
    log("verdict", verdict="APPROVED")
    out(f"VERDICT: task #{task} — APPROVED\nCRITICAL 0 · MAJOR 0 · MINOR 0")
if role == "acceptance-checker":
    bad = git("grep", "-q", "PROBE-BUG", "HEAD", "--", ".", ":!.supertick").returncode == 0
    log("accept", result="FAIL" if bad else "PASS")
    out(f"ACCEPT: task #{task} — {'FAIL' if bad else 'PASS'}\n\n$ git grep PROBE-BUG HEAD\n(exit {0 if bad else 1})")
if role == "spec-verifier":
    log("spec")
    if "CROSS-TASK" in p:
        out("CROSS-TASK: 0 findings")
    out("SPEC: 1 PASS · 0 FAIL · 0 другое\n\n- PASS: double(3) == 6\n$ python3 -m unittest\nOK")
if role == "ci-verifier":
    log("ci")
    out("CI: 1 PASS · 0 FAIL · 0 BROKEN\n\n| Tests | python3 -m unittest | 0 |\n\n$ python3 -m unittest\nOK")
if role == "legacy-scanner":
    log("legacy"); out("LEGACY: 0 items")
if role == "codebase-researcher":
    log("research"); out("RESEARCH: codebase\n**Стек** Python 3, stdlib, unittest. Тесты: python3 -m unittest.")
if role == "reference-researcher":
    log("research"); out("RESEARCH: references 0 files\nОбразцов нет: проект пустой.")
if role == "web-researcher":
    log("research"); out("RESEARCH: web\nВнешних библиотек не нужно.")
if role == "tech-lead":
    log("tech-lead")
    if "IDENTIFY RISKS" in p:
        out("RISKS: 0")
    if "RISK RESULTS" in p:
        out("RISK DECISIONS\nнет")
    out("PLAN OK")
if role == "architect":
    log("architect")
    rn = re.search(r"ROUND (\d+)", p)
    if rn:
        out(f"ROUND {rn.group(1)}: AGREE")
    out("REVIEW BRIEF: probe\nНичего особенного.")
if role == "risk-tester":
    log("risk"); out("RISK 1: THEORETICAL")
log("unknown")
out("UNKNOWN ROLE " + role)
''' % {"author": MOCK_AUTHOR}

WAIT_WRAPPER = """#!/usr/bin/env bash
# Обёртка пробы ведущего: считает вызовы supervisor-wait.sh и передаёт их настоящему скрипту.
printf '%s\\t%s\\n' "$(date +%s)" "$*" >> {trace}
exec bash "$(dirname "${{BASH_SOURCE[0]}}")/.probe-supervisor-wait.sh" "$@"
"""


class ProbeError(Exception):
    pass


# ------------------------------------------------------------------ стенд
def default_root(host):
    if os.environ.get("SUPERTICK_ROOT"):
        return Path(os.environ["SUPERTICK_ROOT"]).expanduser()
    if (SCRIPTS_DIR.parent / "LEAD.md").is_file():
        return SCRIPTS_DIR.parent
    return installed_root(host)


def installed_root(host):
    """Корень набора, установленного в среду; у Codex каталог версии — берётся самый свежий."""
    pattern = INSTALLED_ROOT.get(host, "")
    pattern = pattern.replace("${CODEX_HOME:-~/.codex}", os.environ.get("CODEX_HOME") or "~/.codex")
    pattern = os.path.expanduser(pattern)
    if "*" not in pattern:
        return Path(pattern)
    found = sorted(Path("/").glob(pattern.lstrip("/")), key=lambda p: p.stat().st_mtime)
    return found[-1] if found else Path(pattern.split("*")[0])


def git(repo, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=check)


def build_stand(stand, root, host, model, base_cfg=None):
    """Стенд пробы в каталоге stand. Возвращает словарь путей и базовый коммит."""
    root = Path(root)
    if not (root / "LEAD.md").is_file() or not (root / "scripts" / "supervisor-wait.sh").is_file():
        raise ProbeError(f"{root}: не набор supertick (нет LEAD.md или scripts/); соберите и поставьте его для {host}")
    stand = Path(stand)
    repo, sroot, probe = stand / "repo", stand / "root", stand / "probe"
    for d in (repo, probe / "mock"):
        d.mkdir(parents=True, exist_ok=True)
    shutil.copytree(root, sroot, symlinks=True, ignore=shutil.ignore_patterns("__pycache__"))
    wait = sroot / "scripts" / "supervisor-wait.sh"
    wait.rename(sroot / "scripts" / ".probe-supervisor-wait.sh")
    trace = probe / "wait.log"
    wait.write_text(WAIT_WRAPPER.format(trace=shlex.quote(str(trace))), encoding="utf-8")
    wait.chmod(0o755)
    act = probe / "act.py"
    act.write_text(ACT, encoding="utf-8")

    git(repo, "init", "-q")
    git(repo, "config", "user.name", "probe-owner")
    git(repo, "config", "user.email", "probe-owner@supertick.invalid")
    (repo / "AGENTS.md").write_text("# Проба ведущего supertick\n\nPython 3, только stdlib. "
                                    "Тесты: `python3 -m unittest`.\n", encoding="utf-8")
    (repo / "README.md").write_text("Песочница пробы ведущего supertick.\n", encoding="utf-8")
    (repo / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")   # как в настоящем проекте
    (repo / "src").mkdir()
    (repo / "src" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "__init__.py").write_text("", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD").stdout.strip()
    (probe / "git-before.json").write_text(json.dumps(gg.fingerprint(repo)["files"]), encoding="utf-8")

    base_cfg = base_cfg or {}
    profile = {"lead": {"host": host, **({"model": model} if model else {})},
               "roles": {r: {"engine": "mock"} for r in MOCK_ROLES}}
    for key in ("sources", "installed", "fingerprint", "confirmed_at"):
        if key in base_cfg:
            profile[key] = base_cfg[key]
    cfg = {"active_profile": "lead-probe", "fallback": "host", "profiles": {"lead-probe": profile}}
    (probe / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"stand": stand, "repo": repo, "root": sroot, "probe": probe, "trace": trace, "base": base,
            "config": probe / "config.json", "act": act}


def probe_env(paths, env=None):
    env = dict(os.environ if env is None else env)
    env.update(SUPERTICK_ROOT=str(paths["root"]), SUPERTICK_CONFIG=str(paths["config"]),
               SUPERTICK_MOCK_STATE=str(paths["probe"] / "mock"),
               SUPERTICK_MOCK_ACT=f"{sys.executable} {paths['act']}")
    env.pop("SUPERTICK_MOCK_MODE", None)
    return env


def run_host(paths, host, model, timeout, env=None, binary=None):
    """Запуск ведущего headless. (код выхода или None по таймауту, секунды)."""
    if host not in HOST_ARGV:
        raise ProbeError(NO_PROBE.get(host, f"для среды {host} пробы нет"))
    bin_ = binary or shutil.which(HOST_BINARY[host], path=(env or os.environ).get("PATH"))
    if not bin_:
        raise ProbeError(f"{HOST_BINARY[host]} не найден в PATH")
    prompt = PROMPTS.get(host, PROMPT).format(feature=FEATURE, args=LEAD_ARGS)
    argv = HOST_ARGV[host](bin_, model, prompt)
    started = time.time()
    with open(paths["probe"] / "host.out", "wb") as out, open(paths["probe"] / "host.err", "wb") as err:
        proc = subprocess.Popen(argv, cwd=str(paths["repo"]), env=probe_env(paths, env), stdout=out, stderr=err,
                                stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_group(proc)
            code = None
    # фоновые движки и ожидания стенда переживают ведущего — гасим всё, что запущено из стенда
    stop_stand_processes(paths)
    return code, round(time.time() - started)


def kill_group(proc):
    import signal
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=10)
            return
        except subprocess.TimeoutExpired:
            continue


def ancestors():
    """pid своего процесса и всех предков: их не трогать, даже если путь стенда в их командной строке."""
    out, pid = set(), os.getpid()
    while pid > 1 and pid not in out:
        out.add(pid)
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
            pid = int(stat.rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return out


def stop_stand_processes(paths):
    """Убить процессы, в командной строке которых есть путь стенда (по pid, не pkill -f: свою оболочку не задеть)."""
    import signal
    mark = str(paths["stand"]).rstrip("/") + "/"
    spare = ancestors()
    for d in Path("/proc").glob("[0-9]*"):
        try:
            pid = int(d.name)
            if pid in spare:
                continue
            cmd = (d / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except (OSError, ValueError):
            continue
        if mark in cmd:
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


# ------------------------------------------------------------------ проверки
def find_run(repo):
    runs = sorted((Path(repo) / ".supertick" / "teams").glob("feature-*"))
    with_state = [r for r in runs if (r / "state.md").is_file()]
    return (with_state or runs or [None])[0]


def read(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def jsonl(path):
    out = []
    for line in read(path).splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def shell_commands(transcript):
    """Команды shell ведущего из вывода среды: stream-json Kimi (tool_calls → arguments.command) и
    JSONL Codex (item.completed с command_execution → command)."""
    cmds = []
    for rec in jsonl(transcript):
        item = rec.get("item") if isinstance(rec.get("item"), dict) else {}
        if rec.get("type") == "item.completed" and item.get("type") == "command_execution" \
                and isinstance(item.get("command"), str):
            cmds.append(item["command"])
            continue
        for call in rec.get("tool_calls") or []:
            fn = call.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                continue
            if isinstance(args, dict) and isinstance(args.get("command"), str):
                cmds.append(args["command"])
    return cmds


def check_all(paths, host, exit_code, timeout):
    repo, probe = paths["repo"], paths["probe"]
    run = find_run(repo)
    res = {}

    def put(name, ok, detail=""):
        res[name] = {"ok": bool(ok), "detail": detail}

    detail = ""
    if exit_code is None:
        detail = f"не закончил за {timeout} с"
        try:
            silent = round(time.time() - (probe / "host.out").stat().st_mtime)
            detail += f"; среда молчала последние {silent} с (долгое молчание — обычно зависший вызов модели)"
        except OSError:
            pass
    elif exit_code != 0:
        detail = f"код выхода {exit_code}"
    put("finish", exit_code == 0, detail)

    state = read(run / "state.md") if run else ""
    m = re.search(r"^Plugin root:\s*(\S+)", state, re.M)
    if not run:
        put("root", False, "каталог прогона .supertick/teams/feature-* не создан")
    elif not state:
        put("root", False, f"{run.name}/state.md не создан")
    elif not m:
        put("root", False, "в state.md нет строки «Plugin root:»")
    else:
        same = os.path.realpath(m.group(1).rstrip("/")) == os.path.realpath(paths["root"])
        put("root", same, "" if same else f"Plugin root: {m.group(1)} — не корень набора {paths['root']}")

    need = ["# Team State", "## Phase:", "## Base Commit:", "## Host:", "## Engines", "## Executors"]
    missing = [h for h in need if not re.search(r"^" + re.escape(h), state, re.M)]
    bc = re.search(r"^## Base Commit:\s*(\w+)", state, re.M)
    if missing:
        put("state", False, "нет разделов: " + ", ".join(missing))
    elif not bc or not paths["base"].startswith(bc.group(1)) or len(bc.group(1)) < 7:
        put("state", False, "## Base Commit не равен базовому коммиту стенда")
    elif host not in (re.search(r"^## Host:(.*)$", state, re.M).group(1)):
        put("state", False, f"## Host не называет среду {host}")
    else:
        put("state", True)

    plan = read(run / "PLAN.md") if run else ""
    tasks = re.split(r"(?m)^(?=## Task \d+:)", plan)[1:]
    bad = []
    for t in tasks:
        tid = re.match(r"## Task (\d+)", t).group(1)
        for key in ("Status:", "Blocked by:", "Files to create/edit:", "Acceptance criteria:"):
            if not re.search(r"(?m)^" + re.escape(key), t):
                bad.append(f"задача {tid}: нет «{key}»")
        st = re.search(r"(?m)^Status:\s*(\S+)", t)
        if st and not re.match(r"(TODO|IN_PROGRESS|IN_REVIEW|ACCEPTING|DONE|REOPENED)\b", st.group(1)):
            bad.append(f"задача {tid}: статус {st.group(1)} не из шаблона")
    if not plan:
        put("plan", False, "PLAN.md не создан")
    elif not tasks:
        put("plan", False, "в PLAN.md нет ни одного «## Task N:»")
    else:
        put("plan", not bad, "; ".join(bad[:3]))

    calls = jsonl(probe / "mock" / "calls.jsonl")
    launches = [e for e in jsonl(run / "ledger.jsonl") if e.get("event") == "launch"] if run else []
    if len(calls) > len(launches):
        put("engine", False, f"мок вызван {len(calls)} раз, через run-engine.sh запущено {len(launches)}: движок звали напрямую")
    elif not calls:
        put("engine", False, "ни одного вызова исполнителя: отчёты написаны без движка")
    else:
        put("engine", True, f"{len(launches)} запусков")

    waits = [l for l in read(paths["trace"]).splitlines() if l.strip()]
    shared = [l for l in waits if "--mode shared" in l]
    cmds = shell_commands(probe / "host.out")
    polls = [c for c in cmds
             if re.search(r"\bsleep\b", c) and "supervisor-wait.sh" not in c]
    elsewhere = [c for c in cmds if "supervisor-wait.sh" in c and "--mode shared" in c and str(paths["root"]) not in c]
    if not shared and elsewhere:
        put("wait", False, f"supervisor-wait.sh вызывался {len(elsewhere)} раз, но не из корня набора пробы: "
                           + elsewhere[0][:100])
    elif not shared:
        put("wait", False, "supervisor-wait.sh --mode shared не вызывался" + (f" ({len(waits)} вызовов без --mode shared)" if waits else ""))
    elif polls:
        put("wait", False, f"опрос sleep вместо ожидания: {polls[0][:80]}")
    else:
        put("wait", True, f"{len(shared)} ожиданий")

    done = [re.match(r"## Task (\d+)", t).group(1) for t in tasks if re.search(r"(?m)^Status:\s*DONE\b", t)]
    problems = []
    if not done:
        problems.append("ни одна задача не дошла до DONE")
    for tid in done:
        mine = [c for c in calls if c.get("task") == tid]
        if not any(c["role"] == "unified-reviewer" for c in mine):
            problems.append(f"задача {tid} DONE без ревью")
        if not any(c["role"] == "acceptance-checker" and c.get("result") == "PASS" for c in mine):
            problems.append(f"задача {tid} DONE без приёмки PASS")
        if not any(c.get("kind") == "commit" for c in mine):
            problems.append(f"задача {tid} DONE без коммита кодера")
    first = read(probe / "mock" / "first-coder").strip()
    if first:
        fixes = [c for c in calls if c["role"] == "coder" and c.get("kind") == "fix"]
        if not any(c["session"] == first for c in fixes):
            other = [c for c in fixes]
            problems.append("после CHANGES REQUESTED кодер запущен заново, а не продолжен" if other
                            else "после CHANGES REQUESTED кодера не продолжили")
    put("cycle", not problems, "; ".join(problems[:3]))

    if run:
        tick = subprocess.run([sys.executable, str(paths["root"] / "scripts" / "supervisor-tick.py"), str(run),
                               "--root", str(repo), "--mode", "shared"], capture_output=True, text=True,
                              env=probe_env(paths))
        urgent = [l for l in tick.stdout.splitlines() if re.match(r"P[01] ", l)]
        put("taken", not urgent, urgent[0][:120] if urgent else "")
    else:
        put("taken", False, "нет каталога прогона")

    authors = git(repo, "log", "--format=%an", f"{paths['base']}..HEAD", check=False).stdout.split("\n")
    foreign = sorted({a for a in authors if a and a != MOCK_AUTHOR})
    dirty = [l for l in git(repo, "status", "--porcelain", "-uall", check=False).stdout.splitlines()
             if not l[3:].startswith(".supertick/")]
    tamper = read(probe / "mock" / "tamper.log").strip().splitlines()
    try:
        guard = gg.diff(json.loads(read(probe / "git-before.json") or "{}"), gg.fingerprint(repo)["files"])
    except SystemExit:
        guard = ["каталог git стенда пропал"]
    if foreign:
        put("hands", False, "коммиты не мок-кодера: " + ", ".join(foreign))
    elif tamper:
        put("hands", False, tamper[0])
    elif dirty:
        put("hands", False, "незакоммиченные правки: " + ", ".join(l[3:] for l in dirty[:3]))
    elif guard:
        put("hands", False, ".git тронут: " + ", ".join(guard[:3]))
    else:
        put("hands", True)
    notes = []
    ran = [c for c in cmds if TEST_COMMAND.search(c)]
    if ran:
        notes.append(f"tests: ведущий сам запускал тесты, вызовов {len(ran)}: {TEST_COMMAND.search(ran[0]).group(0)}")
    return res, run, notes


TEST_COMMAND = re.compile(r"python3? -m (unittest|pytest)\b|\bpytest\b|\b(npm|pnpm|yarn) (run )?test\b|\bgo test\b|\bcargo test\b")


def verdict(checks):
    failed = [c for c in CHECKS if not checks[c]["ok"]]
    return ("passed", None) if not failed else (f"failed: {failed[0]}", failed[0])


def bundle_version(root):
    for rel in ("kimi.plugin.json", ".codex-plugin/plugin.json", ".claude-plugin/plugin.json", "plugin.json"):
        try:
            data = json.loads((Path(root) / rel).read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("version"):
                return str(data["version"])
        except (OSError, ValueError):
            continue
    return ""


def probe(host, model=None, root=None, timeout=1800, keep=None, env=None, base_profile=None, binary=None):
    """Полная проба. Словарь: result, failed, checks, date, version, host, model, seconds, stand."""
    if host not in HOST_ARGV:
        raise ProbeError(NO_PROBE.get(host, f"для среды {host} пробы нет"))
    root = Path(root).expanduser() if root else default_root(host)
    stand = Path(keep) if keep else Path(tempfile.mkdtemp(prefix="supertick-lead-probe-"))
    if keep and stand.exists() and any(stand.iterdir()):
        raise ProbeError(f"{stand}: каталог для стенда должен быть пустым")
    try:
        paths = build_stand(stand, root, host, model, base_profile)
        code, seconds = run_host(paths, host, model, timeout, env=env, binary=binary)
        checks, run, notes = check_all(paths, host, code, timeout)
    finally:
        if not keep:
            shutil.rmtree(stand, ignore_errors=True)
    result, failed = verdict(checks)
    return {"result": result, "failed": failed, "checks": checks, "notes": notes, "date": date.today().isoformat(),
            "version": bundle_version(root), "host": host, "model": model or "", "seconds": seconds,
            "stand": str(stand) if keep else ""}


def text(r):
    lines = [f"Проба ведущего {r['host']}" + (f" / {r['model']}" if r["model"] else "") + f": {r['result']}"
             f" ({r['seconds']} с, набор {r['version'] or '?'})"]
    for name in CHECKS:
        c = r["checks"][name]
        lines.append(f"  {'✓' if c['ok'] else '✗'} {name:<7}" + (f" {c['detail']}" if c["detail"] else ""))
    lines += [f"  ⚠ {n}" for n in r.get("notes") or []]
    if r.get("stand"):
        lines.append(f"стенд: {r['stand']}")
    return "\n".join(lines)


def main(argv):
    ap = argparse.ArgumentParser(description="Проба пригодности ведущего supertick")
    ap.add_argument("--host", required=True)
    ap.add_argument("--model")
    ap.add_argument("--root", help="корень набора (по умолчанию $SUPERTICK_ROOT или установленный для среды)")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--keep", help="оставить стенд в этом (пустом) каталоге")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        r = probe(args.host, args.model, args.root, args.timeout, args.keep)
    except ProbeError as exc:
        print(f"lead-probe: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(r, ensure_ascii=False) if args.json else text(r))
    return 0 if r["result"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
