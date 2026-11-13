#!/usr/bin/env bash
# supervisor-wait.sh — сон ведущего: крутит тики, пока не появится действие нужного приоритета.
#
#   supervisor-wait.sh <run-dir> [--wake-on P0|P1|P2|P3] [--interval <сек>] [--max <сек>] [--root <корень>]
#                      [--mode claude|shared]
#   supervisor-wait.sh <run-dir> --stop        (остановить живое ожидание этого прогона)
#
# Запускается ведущим в фоне (run_in_background: true) после спавна команды и после
# каждого обработанного пробуждения. Завершается — и тем самым будит ведущего —
# в трёх случаях: тик нашёл действие с приоритетом не ниже --wake-on (по
# умолчанию P1), истёк --max (по умолчанию 3600 с), каталог прогона пропал.
# На выходе печатает причину и действия тика: это всё, что ведущему нужно
# прочитать, полный снимок лежит в state/supervisor.json.
#
# Ожидание стоит ноль токенов: между тиками спит sleep, а не модель.
#
# Режим общего протокола (--mode shared, 121-567 P1) передаётся тику как есть. Там, где среда сама
# будит ведущего по концу фоновой задачи (Kimi), скрипт идёт в фоне, как здесь. Где не будит (Codex),
# ведущий зовёт его на переднем плане с --max меньше лимита одного вызова shell, вызов за вызовом:
# выход по --max печатает строку WAKE и действия тика — это плановая проверка, а не ошибка.
#
# В режиме shared у прогона одно ожидание: новое ожидание того же прогона сначала гасит прежние (печатает
# «прежнее ожидание … остановлено»), а `--stop` гасит живые на финише. Ищутся по командной строке.
# Иначе ведущий Kimi оставлял забытое фоновое ожидание, и `kimi -p` не выходил до таймаута (V1, R2).
# Погашенное ожидание выходит молча с кодом 0 — это не пробуждение.
set -u
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
run="${1:-}"; shift || true
[ -n "$run" ] && [ -d "$run" ] || { echo "supervisor-wait: нужен каталог прогона" >&2; exit 2; }
# Абсолютный путь сразу: фоновый Bash ведущего может крутиться не в том каталоге,
# где его запустили, и относительный путь «пропадал» на первом же тике (121-374).
run=$(cd "$run" && pwd)
# Другие ожидания этого же прогона: процессы, в командной строке которых `supervisor-wait.sh <run>`,
# кроме себя и своих предков (обёртка `bash -c` среды тоже содержит эту строку). Без pid-файла: файл
# терялся в гонке «новое пишет pid — ловушка выхода старого его стирает» (V1, второй прогон R2).
other_waits() {
  local anc=" " p=$BASHPID pid cmd   # BASHPID: подоболочка $(…) — тоже «я»
  while [ -n "$p" ] && [ "$p" -gt 1 ] 2>/dev/null; do
    anc="$anc$p "; p=$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')
  done
  for pid in $(pgrep -f "supervisor-wait.sh" 2>/dev/null); do
    case "$anc" in *" $pid "*) continue ;; esac
    cmd=$(ps -o command= -p "$pid" 2>/dev/null) || continue
    case "$cmd " in *"supervisor-wait.sh $run "*|*"supervisor-wait.sh $run/ "*) echo "$pid" ;; esac
  done
}

# Погасить их и дождаться выхода (до 10 с); печатает погашенные pid через пробел.
stop_others() {
  local pids n=0
  pids=$(other_waits | tr '\n' ' ')
  [ -n "${pids// /}" ] || return 1
  kill $pids 2>/dev/null
  while [ $n -lt 50 ] && [ -n "$(other_waits)" ]; do sleep 0.2; n=$((n + 1)); done
  echo "${pids% }"
}

wake="P1"; interval=20; max=3600; root=""; mode=""; stop=0
while [ $# -gt 0 ]; do
  case "$1" in
    --stop) stop=1; shift ;;
    --wake-on) wake="$2"; shift 2 ;;
    --interval) interval="$2"; shift 2 ;;
    --max) max="$2"; shift 2 ;;
    --root) root="$2"; shift 2 ;;
    --mode) mode="$2"; shift 2 ;;
    *) echo "supervisor-wait: неизвестный аргумент $1" >&2; exit 2 ;;
  esac
done
case "$wake" in P0|P1|P2|P3) ;; *) echo "supervisor-wait: --wake-on ждёт P0..P3" >&2; exit 2 ;; esac
rootarg=(); [ -n "$root" ] && rootarg=(--root "$(cd "$root" && pwd)")
case "$mode" in ""|claude|shared) ;; *) echo "supervisor-wait: --mode ждёт claude или shared" >&2; exit 2 ;; esac
[ -n "$mode" ] && rootarg+=(--mode "$mode")
if [ "$stop" = 1 ]; then
  if pids=$(stop_others); then echo "supervisor-wait: ожидание $pids остановлено"
  else echo "supervisor-wait: живого ожидания нет"; fi
  exit 0
fi
if [ "$mode" = shared ]; then
  if pids=$(stop_others); then
    echo "supervisor-wait: прежнее ожидание $pids остановлено — у прогона одно ожидание"
  fi
  trap 'exit 0' TERM
fi
started=$(date +%s)
echo "supervisor-wait: сплю на $run, бужу на $wake и выше, тик каждые ${interval} с, не дольше ${max} с"
while :; do
  [ -d "$run" ] || { echo "WAKE: каталог прогона пропал"; exit 0; }
  out=$(python3 "$here/supervisor-tick.py" "$run" "${rootarg[@]}" 2>&1) || { echo "WAKE: тик сломался"; echo "$out"; exit 0; }
  # Действие уровня --wake-on и выше: строка тика начинается с P0..P<wake>.
  if echo "$out" | grep -qE "^P[0-${wake#P}] "; then
    echo "WAKE: есть действие уровня $wake или выше"; echo "$out"; exit 0
  fi
  if [ $(( $(date +%s) - started )) -ge "$max" ]; then
    echo "WAKE: истёк максимум ожидания ${max} с — плановая проверка"; echo "$out"; exit 0
  fi
  sleep "$interval" </dev/null >/dev/null 2>&1 & wait $! 2>/dev/null   # wait прерывается сигналом; sleep не держит вывод
done
