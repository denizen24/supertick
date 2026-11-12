#!/usr/bin/env python3
"""mock-engine.py — мок-движок supertick (121-567, F4): цикл без единой настоящей модели.

    mock-engine.py [--model M] [--read-only] [--session ID] [--mode РЕЖИМ] -p ПРОМПТ

Печатает одну строку JSON в формате `claude -p --output-format json`:
`{"type": "result", "is_error": …, "result": "<ответ>", "session_id": "<id>"}` — run-engine.sh
разбирает её как любой движок. Режим — `--mode` или переменная SUPERTICK_MOCK_MODE:

  ok          ответ и сессия (по умолчанию)
  fail        текст в stderr, код выхода 3
  hang        молчит час — для проверки таймаута и зависшего исполнителя
  no-session  ответ без session_id
  is_error    ответ с is_error: true

Сессия «помнит»: продолжение (`--session`) получает номер хода и первую строку первого задания.
Состояние — в $SUPERTICK_MOCK_STATE (по умолчанию <tmp>/supertick-mock). Файлов проекта мок не меняет.
"""
import argparse
import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

MODES = ("ok", "fail", "hang", "no-session", "is_error")


def main(argv):
    ap = argparse.ArgumentParser(description="Мок-движок supertick")
    ap.add_argument("-p", "--prompt", required=True)
    ap.add_argument("--model", default="mock-1")
    ap.add_argument("--read-only", action="store_true")
    ap.add_argument("--session")
    ap.add_argument("--mode", default=os.environ.get("SUPERTICK_MOCK_MODE", "ok"), choices=MODES)
    args = ap.parse_args(argv)

    if args.mode == "fail":
        print("mock-engine: запрошен провал (режим fail)", file=sys.stderr)
        return 3
    if args.mode == "hang":
        time.sleep(3600)
        return 0

    state_dir = Path(os.environ.get("SUPERTICK_MOCK_STATE") or Path(tempfile.gettempdir()) / "supertick-mock")
    state_dir.mkdir(parents=True, exist_ok=True)
    session = args.session or str(uuid.uuid4())
    state_file = state_dir / f"{session.replace('/', '_')}.json"
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {"turns": 0, "first": ""}
    first_line = next((l.strip() for l in args.prompt.splitlines() if l.strip() and l.strip() != "---"), "")
    state["turns"] += 1
    if not state["first"]:
        state["first"] = first_line[:120]
    state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    access = "read" if args.read_only else "write"
    reply = f"mock turn {state['turns']} ({access}, {args.model}): {first_line[:120]}"
    if state["turns"] > 1:
        reply += f"\nremembered: {state['first']}"
    out = {"type": "result", "subtype": "success", "is_error": args.mode == "is_error", "result": reply}
    if args.mode != "no-session":
        out["session_id"] = session
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
