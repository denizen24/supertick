#!/usr/bin/env bash
# run-engine.sh — launch one external CLI engine call, detached, with its result kept on disk.
#
# Launch (returns at once; the caller never waits on this command):
#
#   run-engine.sh --role <name> --run-dir .claude/teams/<team> --prompt <file> \
#       [--label <NNN|tag>] [--engine cursor|codex|kimi|grok|claude|mock] [--task <id>] \
#       [--report <name.md>] [--session <id>] [--session-regex <re>] [--timeout <sec>] \
#       -- <engine argv, with {prompt} / {prompt_file} placeholders>
#
#   e.g. PATH="$HOME/.local/bin:$PATH" run-engine.sh --role second-reviewer-3 \
#          --run-dir .claude/teams/feature-x --prompt /tmp/p.md --engine cursor --task 3 \
#          --report review-task3-second-r1.md --timeout 1800 \
#          -- cursor-agent -p --trust --output-format json --model grok-4.7-xhigh-fast --mode ask -- '{prompt}'
#
# Wait (a separate Bash call, run_in_background: true):
#
#   until [ -f <out>.done ]; do sleep 5; done; cat <out>.done
#
# Status of a role's calls (running / done-unread / taken):
#
#   run-engine.sh --status .claude/teams/<team> <role>
#
# Files, all under <run-dir>/engine/<role>/ :
#   NNN.prompt.md        the prompt (copied here if it lived elsewhere)
#   NNN.out.part         stdout+stderr while the engine runs
#   NNN.out              the same, atomically renamed when the engine exits — never overwritten
#   NNN.out.pid          pid of the detached worker (the engine is its child)
#   NNN.out.result.md    the reply: the `result` field of the engine's JSON, else the whole output
#   NNN.out.done         written LAST: exit code, times, paths. Its presence means everything is ready
#   NNN.out.taken        written by the caller after it has read and relayed the result
#   session.txt          the engine session id, for `resume`
# Ledger: <run-dir>/ledger.jsonl gets `launch` (with pid) and `done` | `failed`, with real `date -Is` times and the
# model from the engine command (--model / -m). reports/<name> starts at the reply's first protocol head line
# (a preamble glued in front of it is cut, `report_trimmed` = chars cut); .out.result.md keeps the reply as is.
# A label that already exists is never reused: the next free number (or tag-2, tag-3…) is taken.
set -u

die() { echo "run-engine: $*" >&2; exit 2; }

json_line() {  # json_line key value key value ... -> one JSON object on stdout
  python3 - "$@" <<'PY'
import json, sys
a = sys.argv[1:]
d = {}
for k, v in zip(a[0::2], a[1::2]):
    if v == "":
        continue
    if k in ("pid", "exit", "wall_s"):
        try: v = int(v)
        except ValueError: pass
    d[k] = v
print(json.dumps(d, ensure_ascii=False))
PY
}

# ---------------------------------------------------------------- status mode
if [ "${1:-}" = "--status" ]; then
  run="${2:-}"; role="${3:-}"
  [ -n "$run" ] && [ -n "$role" ] || die "usage: --status <run-dir> <role>"
  dir="$run/engine/$role"
  [ -d "$dir" ] || { echo "no calls for $role"; exit 0; }
  shopt -s nullglob
  for f in "$dir"/*.out.pid; do
    base="${f%.pid}"; pid=$(cat "$f" 2>/dev/null)
    if [ -f "$base.done" ]; then
      if [ -f "$base.taken" ]; then st="taken"; else st="DONE-UNREAD"; fi
      echo "$st $base $(tr '\n' ' ' < "$base.done")"
    elif [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      echo "RUNNING $base pid=$pid $(tr '\n' ' ' < "$base.limit" 2>/dev/null || true)"
    else
      echo "DEAD $base pid=$pid (no .done, process gone)"
    fi
  done
  exit 0
fi

# ---------------------------------------------------------------- worker (detached)
if [ "${1:-}" = "__worker" ]; then
  shift
  out="$RE_OUT"; dir=$(dirname "$out")
  printf 'timeout=%ss\nsource=%s\n' "$RE_TIMEOUT" "$RE_TIMEOUT_SOURCE" > "$out.limit"
  echo $$ > "$out.pid"
  started=$(date -Is); t0=$(date +%s)
  model=""; prev=""   # the model as the engine command names it (--model X | --model=X | -m X)
  for a in "$@"; do
    case "$prev" in --model|-m) model="$a" ;; esac
    case "$a" in --model=*) model="${a#--model=}" ;; esac
    prev="$a"
  done
  json_line ts "$started" event launch role "$RE_ROLE" engine "$RE_ENGINE" model "$model" task "$RE_TASK" \
    label "$RE_LABEL" pid "$$" timeout_s "$RE_TIMEOUT" timeout_source "$RE_TIMEOUT_SOURCE" session "$RE_SESSION" prompt "$RE_PROMPT" out "$out" >> "$RE_LEDGER"

  prompt_text=$(cat "$RE_PROMPT")
  argv=()
  for a in "$@"; do
    a="${a//\{prompt_file\}/"$RE_PROMPT"}"   # quoted: bash 5.2 treats a bare & in the
    a="${a//\{prompt\}/"$prompt_text"}"      # replacement as "the matched text"
    argv+=("$a")
  done

  python3 "$(dirname "$0")/engine-process.py" "$RE_TIMEOUT" "$out.failure" "$(dirname "$RE_LEDGER")" "$RE_ROLE" "${argv[@]}" < /dev/null > "$out.part" 2>&1 &
  child=$!
  trap 'kill -TERM "$child" 2>/dev/null || true' TERM INT
  wait "$child"
  rc=$?
  if kill -0 "$child" 2>/dev/null; then wait "$child"; rc=$?; fi
  trap - TERM INT
  mv "$out.part" "$out"
  ended=$(date -Is); wall=$(( $(date +%s) - t0 ))

  # session id and reply
  session=$(python3 - "$out" "$out.result.md" "${RE_SESSION_REGEX:-}" "$RE_ENGINE" <<'PY'
import json, re, sys
out, res, rx, engine = sys.argv[1:5]
text = open(out, encoding="utf-8", errors="replace").read()
session, result, is_error = "", None, False
for line in reversed(text.splitlines()):
    line = line.strip()
    if not line.startswith("{"):
        continue
    try:
        obj = json.loads(line)
    except ValueError:
        continue
    if isinstance(obj, dict) and ("result" in obj or "session_id" in obj):
        session = obj.get("session_id") or ""
        result = obj.get("result")
        is_error = bool(obj.get("is_error"))
        break
if result is None:   # opencode run --format json: события с sessionID; ответ — тексты последнего сообщения с текстом
    events = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("sessionID"), str) and isinstance(obj.get("type"), str):
            events.append(obj)
    if events:
        session = events[0]["sessionID"]
        is_error = any(e["type"] == "error" for e in events)
        texts = [e["part"] for e in events if e["type"] == "text" and isinstance(e.get("part"), dict)
                 and isinstance(e["part"].get("text"), str) and e["part"]["text"].strip()]
        if texts:
            last = texts[-1].get("messageID")
            result = "\n".join(p["text"] for p in texts if p.get("messageID") == last)
        elif is_error:
            err = next(e for e in events if e["type"] == "error").get("error") or {}
            result = json.dumps(err, ensure_ascii=False)
if result is None:   # JSONL без поля result (kimi 2.x --output-format stream-json): ответ — последняя реплика assistant
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("role") == "assistant" and isinstance(obj.get("content"), str) and obj["content"].strip():
            result = obj["content"]
            break
        # codex exec --json: ответ — последняя agent_message, сессия — thread_id из thread.started
        item = obj.get("item") if isinstance(obj, dict) else None
        if isinstance(item, dict) and obj.get("type") == "item.completed" and item.get("type") == "agent_message" \
                and isinstance(item.get("text"), str) and item["text"].strip():
            result = item["text"]
            break
    if not session:
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("{") and '"thread.started"' in line:
                try:
                    session = json.loads(line).get("thread_id") or ""
                except ValueError:
                    pass
                break
if not rx:
    rx = {"codex": r"session id:\s*([0-9a-fA-F-]{36})",
          "kimi": r"kimi -r (session_[0-9a-f-]+)"}.get(engine, "")
if not session and rx:
    m = re.findall(rx, text)
    if m:
        session = m[-1] if isinstance(m[-1], str) else m[-1][0]
if result is None:
    result = text
if result.strip():
    open(res, "w", encoding="utf-8").write(result if result.endswith("\n") else result + "\n")
errors = []
for line in text.splitlines():
    try:
        event = json.loads(line)
    except ValueError:
        continue
    if isinstance(event, dict) and (event.get("is_error") or event.get("type") in
                                    ("error", "turn.failed", "provider.api_error")):
        errors.append(json.dumps(event, ensure_ascii=False))
if errors:
    is_error = True
failure = ""
if is_error:
    error_text = "\n".join(errors + ([result] if isinstance(result, str) else []))
    if re.search(r"quota|usage.?limit|rate.?limit|insufficient_quota|\b429\b", error_text, re.I):
        failure = "quota"
    elif re.search(r"flagged|safety|policy.?violation|content.?filter", error_text, re.I):
        failure = "service_rejection"
    else:
        failure = "provider_error"
print(session + ("\t" + failure if failure else ""))
PY
)
  provider_failure=""
  case "$session" in *$'\t'*) provider_failure="${session#*$'\t'}"; session="${session%%$'\t'*}" ;; esac
  [ -z "$session" ] && session="$RE_SESSION"
  [ -n "$session" ] && printf '%s\n' "$session" > "$dir/session.txt"

  status=done; reason=""; failure=""
  if [ -s "$out.failure" ]; then
    failure=$(cat "$out.failure")
    if [ "$failure" = timeout ]; then reason="timeout ${RE_TIMEOUT}s"; else reason="$failure (exit $rc)"; fi
  elif [ -n "$provider_failure" ]; then failure="$provider_failure"; reason="$provider_failure (exit $rc)"
  elif [ "$rc" -ne 0 ]; then failure=engine_exit; reason="exit $rc"
  elif [ ! -s "$out.result.md" ]; then failure=no_reply; reason="no reply in output"
  elif [ "$RE_ENGINE" = cursor ] && [ -z "$session" ]; then failure=no_reply; reason="no JSON reply (no session_id)"
  fi
  [ -n "$failure" ] && status=failed

  report=""
  if [ -n "$RE_REPORT" ] && [ "$status" = done ]; then   # a failed call never lands in reports/
    mkdir -p "$(dirname "$RE_REPORT")"
    report="$RE_REPORT"
    if [ "${RE_REPORT_PRE:-1}" = 0 ] && [ -s "$report" ]; then
      # the engine wrote the report file itself during this call (a role with write access does so despite
      # its brief, 121-567 H4 live run): that file IS the report; the reply stays in .out.result.md
      report_by=engine_file
    elif [ -e "$report" ]; then   # never overwrite: name-<label>.md, then -2, -3…
      stem="${RE_REPORT%.md}"; report="$stem-$RE_LABEL.md"; n=2
      while [ -e "$report" ]; do report="$stem-$RE_LABEL-$n.md"; n=$((n+1)); done
    fi
    if [ "${report_by:-}" != engine_file ]; then
      # the report starts at the reply's first known head line: an engine may glue a preamble in front of
      # it ("Читаю бриф…VERDICT: task #1 — APPROVED", cursor in R3) and the reader judges by line one.
      # The reply itself stays untouched in .out.result.md
      trimmed=$(python3 - "$out.result.md" "$report" <<'PY'
import re, sys
src, dst = sys.argv[1:3]
text = open(src, encoding="utf-8", errors="replace").read()
heads = (r"VERDICT: task #|ACCEPT: task #|ACCEPTANCE FAILED: task #|READY FOR REVIEW: task #|COMMITTED: task #"
         r"|QUESTION: task #|ESCALATION: |STUCK: task #|CI: \d|SPEC: \d|CROSS-TASK: \d|LEGACY: \d|RISK \d+: "
         r"|RISKS: \d|RISK DECISIONS|ROUND \d+: |REVIEW BRIEF: |RESEARCH: |PLAN OK|FULL RUN: ")
cut = 0
if not re.match(r"\s*(?:%s)" % heads, text):
    for m in re.finditer(heads, text[:1500]):
        if m.start() and text[m.start() - 1] in "`\"'«":   # quoted in the preamble, not the head line
            continue
        cut = m.start()
        break
open(dst, "w", encoding="utf-8").write(text[cut:])
print(cut or "")
PY
)
    fi
  fi

  json_line ts "$ended" event "$status" role "$RE_ROLE" engine "$RE_ENGINE" task "$RE_TASK" \
    label "$RE_LABEL" pid "$$" exit "$rc" wall_s "$wall" session "$session" reason "$reason" \
    model "$model" failure_kind "$failure" timeout_s "$RE_TIMEOUT" timeout_source "$RE_TIMEOUT_SOURCE" out "$out" result "$out.result.md" report "$report" report_by "${report_by:-reply}" \
    report_trimmed "${trimmed:-}" >> "$RE_LEDGER"

  {
    echo "status=$status"
    echo "exit=$rc"
    echo "engine=$RE_ENGINE"
    echo "timeout_s=$RE_TIMEOUT"
    echo "timeout_source=$RE_TIMEOUT_SOURCE"
    [ -n "$failure" ] && echo "failure_kind=$failure"
    [ -n "$reason" ] && echo "reason=$reason"
    echo "started=$started"
    echo "ended=$ended"
    echo "wall_s=$wall"
    echo "session=$session"
    echo "out=$out"
    [ -s "$out.result.md" ] && echo "result=$out.result.md"
    [ -n "$report" ] && echo "report=$report"
    [ -n "${report_by:-}" ] && echo "report_by=$report_by"
  } > "$out.done.tmp"
  mv "$out.done.tmp" "$out.done"
  exit 0
fi

# ---------------------------------------------------------------- launcher
role="" run="" prompt="" label="" engine="" task="" report="" session="" sregex="" tmo="" cwd="" retry_quota=0
while [ $# -gt 0 ]; do
  case "$1" in
    --role) role="$2"; shift 2 ;;
    --run-dir) run="$2"; shift 2 ;;
    --prompt) prompt="$2"; shift 2 ;;
    --label) label="$2"; shift 2 ;;
    --engine) engine="$2"; shift 2 ;;
    --task) task="$2"; shift 2 ;;
    --report) report="$2"; shift 2 ;;
    --session) session="$2"; shift 2 ;;
    --session-regex) sregex="$2"; shift 2 ;;
    --timeout) tmo="$2"; shift 2 ;;
    --cwd) cwd="$2"; shift 2 ;;
    --retry-quota) retry_quota=1; shift ;;
    -h|--help) sed -n '2,36p' "$0"; exit 0 ;;
    --) shift; break ;;
    *) die "unknown option: $1 (the engine command goes after --)" ;;
  esac
done
[ -n "$role" ] && [ -n "$run" ] && [ -n "$prompt" ] || die "--role, --run-dir and --prompt are required"
[ $# -gt 0 ] || die "no engine command after --"
[ -s "$prompt" ] || die "prompt file missing or empty: $prompt"
command -v "$1" >/dev/null 2>&1 || die "engine binary not found: $1 (for cursor-agent try PATH=\"\$HOME/.local/bin:\$PATH\")"
case "$tmo" in *[!0-9]*) die "--timeout must be seconds" ;; esac
command -v python3 >/dev/null || die "python3 is required"

mkdir -p "$run" || die "cannot create $run"
run=$(cd "$run" && pwd)
limits=$(python3 - "$(dirname "$0")" "$run" "$role" "$label" "$tmo" "$engine" "$retry_quota" <<'PY_LIMIT'
import sys
sys.path.insert(0, sys.argv[1])
from run_support import component, engine_timeout, done_data, latest_call
run, role, label, timeout, engine, retry = sys.argv[2:]
try:
    if label:
        component(label)
    last = latest_call(run, role)
    if last and retry != "1":
        previous = done_data(f"{last}.done")
        if previous.get("failure_kind") == "quota" and previous.get("engine") == engine:
            raise ValueError("provider quota exhausted; wait for reset or change the approved engine. Use --retry-quota after confirming reset")
    seconds, source = engine_timeout(run, role, timeout or None)
    if seconds < 0:
        raise ValueError("timeout must be nonnegative")
except (OSError, ValueError) as exc:
    sys.exit(str(exc))
print(seconds, source)
PY_LIMIT
) || exit 2
read -r tmo timeout_source <<< "$limits"
dir="$run/engine/$role"; mkdir -p "$dir" "$run/reports"
prompt=$(cd "$(dirname "$prompt")" && pwd)/$(basename "$prompt")
if [ -n "$cwd" ]; then cd "$cwd" || die "invalid --cwd: $cwd"; fi

# pick a label that has never been used, and reserve it atomically (noclobber on .part)
next_num() {
  local max=0 f n
  for f in "$dir"/*.out "$dir"/*.out.part; do
    [ -e "$f" ] || continue
    n=$(basename "$f"); n="${n%%.*}"
    case "$n" in ''|*[!0-9]*) continue ;; esac
    n=$((10#$n)); [ "$n" -gt "$max" ] && max=$n
  done
  printf '%03d' $((max + 1))
}
[ -z "$label" ] && label=$(next_num)
base="$label"; k=2
while :; do
  out="$dir/$label.out"
  if [ ! -e "$out" ] && [ ! -e "$out.done" ] && ( set -C; : > "$out.part" ) 2>/dev/null; then break; fi
  if [[ "$base" =~ ^[0-9]+$ ]]; then label=$(printf "%0${#base}d" $((10#$label + 1))); else label="$base-$k"; k=$((k+1)); fi
done

# keep the prompt next to its output
target="$dir/$label.prompt.md"
if [ "$prompt" != "$target" ]; then
  if [ ! -e "$target" ]; then cp "$prompt" "$target"; prompt="$target"; fi
fi
[ -n "$session" ] && printf '%s\n' "$session" > "$dir/session.txt"

rep=""
if [ -n "$report" ]; then
  case "$report" in /*) rep="$report" ;; *) rep="$run/reports/$report" ;; esac
fi

rep_pre=1; [ -n "$rep" ] && [ ! -e "$rep" ] && rep_pre=0   # absent at launch: if it appears, the engine wrote it
RE_OUT="$out" RE_ROLE="$role" RE_ENGINE="$engine" RE_TASK="$task" RE_LABEL="$label" \
RE_SESSION="$session" RE_SESSION_REGEX="$sregex" RE_PROMPT="$prompt" RE_REPORT="$rep" RE_REPORT_PRE="$rep_pre" \
RE_TIMEOUT="$tmo" RE_TIMEOUT_SOURCE="$timeout_source" RE_LEDGER="$run/ledger.jsonl" \
  setsid nohup "$(readlink -f "$0")" __worker "$@" < /dev/null > /dev/null 2>&1 &

for _ in $(seq 1 50); do [ -s "$out.pid" ] && break; sleep 0.1; done
pid=$(cat "$out.pid" 2>/dev/null || echo "$!")
echo "pid=$pid"
echo "timeout_s=$tmo"
echo "timeout_source=$timeout_source"
echo "out=$out"
echo "done=$out.done"
echo "result=$out.result.md"
[ -n "$rep" ] && echo "report=$rep (or a -$label suffixed name if that one exists; see the .done file)"
echo "wait: until [ -f '$out.done' ]; do sleep 5; done; cat '$out.done'"
exit 0
