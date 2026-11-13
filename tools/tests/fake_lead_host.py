#!/usr/bin/env python3
"""Поддельная среда ведущего для тестов пробы (121-567, H2).

Стоит в PATH под именем CLI среды (`kimi` или `codex`, H4): по имени выбирает среду, её уровень и формат
вывода (stream-json Kimi или JSONL Codex) по FAKE_LEAD_HOST из обёртки. Аргументы headless-запуска игнорирует, сам играет ведущего:
каждая команда — блок `# lead:<id>` из LEAD.md набора ($SUPERTICK_ROOT), как в test_lead_protocol.py.
Одна задача: кодер (мок оставит PROBE-BUG) → ревью CHANGES REQUESTED → продолжение кодера → ревью
APPROVED → коммит → приёмка PASS → DONE.

FAKE_LEAD_BREAK портит ведущего ровно в одном месте:
  none    исправный ведущий
  root    в state.md чужой Plugin root
  state   state.md без раздела ## Executors
  plan    в PLAN.md нет строки Blocked by
  engine  рецензента второго круга зовёт мок-движком напрямую, мимо run-engine.sh
  wait    ждёт опросом файла .done, без supervisor-wait.sh
  poll    ждёт через supervisor-wait.sh, но приёмку — опросом с sleep
  resume  после CHANGES REQUESTED запускает нового кодера вместо продолжения
  hands   сам правит файл задачи перед коммитом
  hook    кладёт файл в .git/hooks (как кодер с записью в .git, риск R11)
  tests   исправный, но в конце сам гоняет тесты (замечание, не провал)
  hang    ничего не делает и висит (для проверки срока)
  exit    падает с кодом 3 сразу
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

BREAK = os.environ.get("FAKE_LEAD_BREAK", "none")
HOST = os.environ.get("FAKE_LEAD_HOST", "kimi")   # обёртка в PATH ставит имя своей среды
LEVEL = {"codex": "L1", "kimi": "L2"}[HOST]
ROOT = Path(os.environ["SUPERTICK_ROOT"])
S = ROOT / "scripts"
REPO = Path.cwd()
RUN = REPO / ".supertick" / "teams" / "feature-double"
BLOCK = re.compile(r"```bash\n\s*# lead:(?P<id>[\w-]+)\n\s*(?P<cmd>.+?)\n\s*```", re.S)
BLOCKS = {m["id"]: m["cmd"].strip() for m in BLOCK.finditer((ROOT / "LEAD.md").read_text(encoding="utf-8"))}
TRANSCRIPT = []


def sh(block, extra="", **subs):
    cmd = BLOCKS[block]
    for key, value in {"S": S, "RUN": RUN, "REPO": REPO, "HOST": HOST, **subs}.items():
        cmd = cmd.replace(f"<{key}>", str(value))
    left = re.findall(r"<[^\s<>][^<>]*>", cmd)
    if left:
        sys.exit(f"fake lead: lead:{block} не подставлено {left}")
    return raw(cmd + extra)


def raw(cmd):
    res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, cwd=str(REPO), timeout=300)
    if HOST == "codex":   # как codex exec --json: команда обёрнута в bash -lc
        wrapped = "/bin/bash -lc " + json.dumps(cmd)
        TRANSCRIPT.append({"type": "item.completed", "item": {"id": f"item_{len(TRANSCRIPT)}", "type": "command_execution",
                           "command": wrapped, "aggregated_output": res.stdout[-200:], "exit_code": res.returncode,
                           "status": "completed"}})
    else:
        TRANSCRIPT.append({"role": "assistant", "tool_calls": [{"type": "function", "function": {
            "name": "Bash", "arguments": json.dumps({"command": cmd})}}]})
    return res


def task_section():
    text = (RUN / "PLAN.md").read_text(encoding="utf-8")
    return re.search(r"(## Task 1:.*?)(?=\n## Task |\Z)", text, re.S).group(1)


def plan(status):
    blocked = "" if BREAK == "plan" else "Blocked by: none\n"
    (RUN / "PLAN.md").write_text(
        "# Plan — feature-double\n\n"
        f"## Task 1: double\nStatus: {status}\n{blocked}"
        "Files to create/edit: src/probe_calc.py\nReference files: none\n\n"
        "Description: double(x) = 2 * x.\n\n"
        "Acceptance criteria:\n- `python3 -c 'from src.probe_calc import double; print(double(3))'` → 6\n\n"
        "Tooling: python3 -m unittest\n", encoding="utf-8")


def wait_for(name):
    if BREAK == "wait" or (BREAK == "poll" and name == "task1-accept"):
        for _ in range(120):
            r = raw(f"ls {RUN}/engine/{name}/*.out.done 2>/dev/null | tail -1; sleep 1")
            done = [l for l in r.stdout.split() if l.endswith(".done")]
            fresh = [d for d in done if not Path(d[:-5] + ".taken").exists()]
            if fresh:
                return fresh[-1][:-5]
        sys.exit("fake lead: не дождался")
    for _ in range(30):
        woke = sh("wait", extra=" --interval 1 --max 30")
        m = re.search(rf"^P1 engine_result_ready {re.escape(name)}: .*\[(\S+)\.result\.md\]", woke.stdout, re.M)
        if m:
            return m.group(1)
    sys.exit(f"fake lead: {name}: результат не пришёл")


def call(role, name, n, report, body, resume=False):
    sh("card", NAME=name, **{"роль": role, "id": "1", "engine|native": "engine", "срок": "600", "отчёт": report})
    prompt = RUN / "engine" / f"{name}-{n}.prompt.md"
    brief = "unified-reviewer" if role == "second-reviewer" else role
    head = "" if resume else (f"ROLE BRIEF FILE: {ROOT}/roles/{brief}.md\nRUN DIR: {RUN}\nREPO: {REPO}\nNAME: {name}\n")
    prompt.write_text(head + f"REPORT: {RUN}/reports/{report}.md\n" + body, encoding="utf-8")
    if BREAK == "engine" and role == "unified-reviewer" and resume:
        # мимо run-engine.sh: мок напрямую, отчёт пишет сам
        prompt.write_text(f"ROLE BRIEF FILE: {ROOT}/roles/{brief}.md\n" + prompt.read_text(encoding="utf-8"),
                          encoding="utf-8")
        r = raw(f"python3 {S}/mock-engine.py -p \"$(cat {prompt})\"")
        reply = json.loads(r.stdout)["result"]
        (RUN / "reports" / f"{report}.md").write_text(reply + "\n", encoding="utf-8")
        sh("card", NAME=name, **{"роль": role, "id": "1", "engine|native": "engine", "срок": "600", "отчёт": report})
        raw(f"python3 {S}/run-state.py set {RUN} {name} status=done")
        return reply.splitlines()
    argv = sh("resume" if resume else "argv", NAME=name, n=n, **{"роль": role, "отчёт": report, "id": "1"})
    if "argv rc=0" not in argv.stdout:
        sys.exit(f"fake lead: argv {name}: {argv.stdout} {argv.stderr}")
    sh("launch", NAME=name, n=n)
    ref = wait_for(name)
    taken = sh("take", NAME=name, **{"отчёт": report, "путь .out из ref тика": ref})
    return taken.stdout.strip().splitlines()


def main():
    if BREAK == "exit":
        sys.exit(3)
    if BREAK == "hang":
        time.sleep(3600)
    assert "ROOT OK" in sh("root").stdout
    host = json.loads(sh("host").stdout)
    engines = sh("resolve").stdout.strip()
    base = sh("run-dir").stdout.strip().splitlines()[-1]
    assert "GIT GUARD SNAPSHOT" in sh("git-guard").stdout
    root_line = "/elsewhere/supertick" if BREAK == "root" else str(ROOT)
    executors = "" if BREAK == "state" else "## Executors\n- task1-coder: task 1, engine mock, запуск 1\n"
    (RUN / "state.md").write_text(
        "# Team State — feature-double\n\n## Phase: EXECUTION\n## Team Name: feature-double\n"
        f"## Complexity: SIMPLE\n## Base Commit: {base}\n## Host: {HOST} ({LEVEL})\nPlugin root: {root_line}\n\n"
        f"## Engines\n{engines}\n\n{executors}", encoding="utf-8")
    assert host["action"] in ("line", "none"), host
    plan("IN_PROGRESS(task1-coder)")
    task = f"--- TASK ---\n{task_section()}\n--- END TASK ---\n"
    first = call("coder", "task1-coder", 1, "task1-coder-1", task)
    assert first[0] == "READY FOR REVIEW: task #1", first
    n, rnd = 1, 1
    while True:
        plan("IN_REVIEW(task1-coder)")
        sh("diff", **{"файлы задачи": "src/probe_calc.py", "id": "1", "n": rnd})
        review = call("unified-reviewer", "task1-review", rnd, f"review-task1-r{rnd}",
                      f"REVIEW: task #1, round {rnd}\n{task}DIFF: {RUN}/reports/review-task1-r{rnd}.diff\n",
                      resume=rnd > 1)
        if review[0].endswith("APPROVED"):
            break
        assert rnd < 3, review
        n += 1
        fresh = BREAK == "resume"
        body = (task if fresh else "") + f"CHANGES REQUESTED: task #1. Report: {RUN}/reports/review-task1-r{rnd}.md\n"
        again = call("coder", "task1-coder" + ("-b" if fresh else ""), n if not fresh else 1,
                     f"task1-coder-{n}", body, resume=not fresh)
        assert again[0] == "READY FOR REVIEW: task #1", again
        rnd += 1
    if BREAK == "hands":
        with open(REPO / "src" / "probe_calc.py", "a") as fh:
            fh.write("# поправил ведущий\n")
    if BREAK == "hook":
        (REPO / ".git" / "hooks" / "post-checkout").write_text("#!/bin/sh\necho owned\n")
    n += 1
    coder = "task1-coder-b" if BREAK == "resume" else "task1-coder"
    done = call("coder", coder, n if BREAK != "resume" else 2, f"task1-coder-{n}",
                f"APPROVED: task #1. Report: {RUN}/reports/review-task1-r{rnd}.md. Commit now.\n", resume=True)
    assert done[0] == "COMMITTED: task #1", done
    sha = done[1].split()[1]
    plan("ACCEPTING(task1-coder)")
    sh("accept-diff", **{"sha …": sha, "файлы задачи": "src/probe_calc.py", "id": "1"})
    accept = call("acceptance-checker", "task1-accept", 1, "accept-task1",
                  f"ACCEPTANCE of task #1, team feature-double.\n{task}"
                  f"DIFF FILE: {RUN}/reports/accept-task1.diff (commits {sha})\n"
                  f"REVIEW REPORT: {RUN}/reports/review-task1-r{rnd}.md\nAPPROVED DEVIATIONS: none\n")
    assert accept[0] == "ACCEPT: task #1 — PASS", accept
    plan("DONE")
    sh("tick")
    if BREAK == "tests":
        raw(f"cd {REPO} && python3 -m unittest 2>&1 | tail -1")
    check = sh("git-check")
    assert ("GIT GUARD CHANGED" in check.stdout) == (BREAK == "hook"), check.stdout
    for rec in TRANSCRIPT:
        print(json.dumps(rec, ensure_ascii=False))
    final = ({"type": "item.completed", "item": {"type": "agent_message", "text": "ГОТОВО"}} if HOST == "codex"
             else {"role": "assistant", "content": "ГОТОВО"})
    print(json.dumps(final, ensure_ascii=False))


if __name__ == "__main__":
    main()
