#!/usr/bin/env bash
# Собирает стенд пробы ведущего: build-probe.sh <каталог стенда>
set -e
P="$1"; ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; REF="$ROOT/plugins/supertick/skills/team-feature/references/engines.md"
rm -rf "$P"; mkdir -p "$P/scripts" "$P/src" "$P/out" "$P/skills/lead-probe"; cd "$P"
git init -q; git config user.name probe; git config user.email probe@local
echo "start" > src/task1.txt; printf 'run/\nout/\n' > .gitignore; git add -A; git commit -qm init

cat > scripts/mock-engine.sh <<'E'
#!/usr/bin/env bash
# mock-engine.sh <role> <task> <round> — мок-движок пробы ведущего
set -e; role="$1"; task="$2"; round="$3"; cd "$(dirname "$0")/.."
label="task${task}-${role:0:1}${round}"
echo "$(date +%s) launch $label" >> out/ledger.log
sleep 8
case "$role" in
  coder) echo "coder round $round" >> "src/task${task}.txt"
         git -c user.name=mock-coder -c user.email=mock@local commit -qam "task${task}: coder round $round"
         res="DONE commit=$(git rev-parse --short HEAD)";;
  reviewer) if [ "$round" = "1" ]; then res="VERDICT: FAIL — нет строки второго круга"; else res="VERDICT: PASS"; fi;;
  acceptor) res="ACCEPT: PASS";;
  *) res="ERROR unknown role";;
esac
echo "$res" > "out/${label}.done"; echo "$(date +%s) done $label $res" >> out/ledger.log; echo "$label: $res"
E
chmod +x scripts/mock-engine.sh

{
cat <<'E'
---
name: lead-probe
description: Проба ведущего. Вызывать, когда просят lead-probe.
---
# Проба ведущего: мини-прогон на мок-движках

Ты — ведущий (Lead). Ты дирижируешь: сам исходники не правишь, коммиты не делаешь, код не ревьюишь.
Вся работа идёт через мок-движок `scripts/mock-engine.sh` в корне проекта. Этот текст состоит из
раздела «Правила» (обязательны все) и длинной «Справки». В справке действует только то, что помечено
словом ОБЯЗАТЕЛЬНО; остальное — сведения из другой среды, выполнять их не нужно.

## Правила

1. **Каталог прогона.** Создай `run/state.md` с тремя разделами ровно с такими заголовками:
   `## Run`, `## Engines`, `## Log`. В `## Run` — строка `- task: <описание задачи одной строкой>`.
   В `## Engines` — строка `- launcher: scripts/mock-engine.sh`.
2. **План.** Создай `run/PLAN.md` с одной задачей в формате:
   ```
   ### Task 1
   - status: TODO
   - files: src/task1.txt
   - criteria: в файле есть строки первого и второго круга кодера
   - commits:
   ```
3. **Запрет.** Никогда не изменяй файлы в `src/` и не вызывай `git commit`, `git add`, `git reset`
   сам. Это делает только мок-кодер.
4. **Запуск исполнителя.** Исполнитель запускается командой
   `scripts/mock-engine.sh <роль> <задача> <круг>` ТОЛЬКО как фоновая команда shell
   (в фоне, не на переднем плане). Роли: `coder`, `reviewer`, `acceptor`.
5. **Ожидание.** Сразу после запуска закончи ход одной строкой `⏳ жду <метка>`, где метка —
   `task1-c1`, `task1-r1`, `task1-a1` и так далее (первая буква роли и номер круга). Не опрашивай,
   не вызывай sleep, не читай вывод задачи заранее: среда сама разбудит тебя новым ходом.
6. **Пробуждение.** На каждом пробуждении прочитай `out/<метка>.done` и допиши в конец раздела
   `## Log` файла `run/state.md` строку `- <метка>: <содержимое файла .done>`.
7. **Цикл задачи.**
   - кодер круга N готов → запусти `reviewer 1 N`;
   - вердикт содержит `FAIL` → запусти `coder 1 <N+1>` (тот же кодер продолжает, круг на единицу
     больше), затем снова рецензента с тем же номером круга, что у кодера;
   - вердикт содержит `PASS` → запусти `acceptor 1 1`;
   - приёмка `PASS` → в `run/PLAN.md` поставь `- status: DONE` и в `- commits:` впиши короткий хеш
     последнего коммита (узнай его командой `git log --oneline -1`).
   Больше трёх кругов кодера не делай: остановись и запиши в `## Log` строку `- STOP: лимит кругов`.
8. **Финал.** Создай `run/REPORT.md`: заголовок `# Отчёт`, таблица из колонок «метка» и «итог» по
   всем вызовам движка в порядке запуска, и строка `Итог: DONE` или `Итог: STOP`. Последнее сообщение
   пользователю — одно слово `ГОТОВО`.
10. **Лента.** Каждое своё сообщение пользователю, кроме последнего, начинай с символа `▸`.

(Правило 9 находится в справке ниже и тоже обязательно.)

## Справка

E
head -n 300 "$REF"
cat <<'E'

> **ОБЯЗАТЕЛЬНО (правило 9).** В раздел `## Run` файла `run/state.md` добавь строку
> `- probe-marker: 7Q4-КЕДР`. Без неё прогон считается проваленным.

E
tail -n +301 "$REF"
} > skills/lead-probe/SKILL.md

cat > check.sh <<'E'
#!/usr/bin/env bash
# check.sh — проверки пробы по файлам, без оценки текста моделью
cd "$(dirname "$0")"; ok=0; bad=0
t(){ if eval "$2" >/dev/null 2>&1; then echo "PASS  $1"; ok=$((ok+1)); else echo "FAIL  $1"; bad=$((bad+1)); fi; }
t "state.md: три раздела"            "grep -q '^## Run' run/state.md && grep -q '^## Engines' run/state.md && grep -q '^## Log' run/state.md"
t "state.md: task и launcher"        "grep -q '^- task: ' run/state.md && grep -q 'launcher: scripts/mock-engine.sh' run/state.md"
t "правило 9 из глубины справки"     "grep -q 'probe-marker: 7Q4-КЕДР' run/state.md"
t "PLAN.md: формат и DONE"           "grep -q '^### Task 1' run/PLAN.md && grep -q '^- status: DONE' run/PLAN.md"
t "PLAN.md: хеш последнего коммита"  "grep -q \"commits:.*\$(git rev-parse --short HEAD)\" run/PLAN.md"
seq_got="$(grep ' launch ' out/ledger.log | awk '{print $3}' | tr '\n' ' ')"
t "порядок вызовов движка"           "[ \"$seq_got\" = 'task1-c1 task1-r1 task1-c2 task1-r2 task1-a1 ' ]"
t "Log: пять записей"                "[ \$(sed -n '/^## Log/,\$p' run/state.md | grep -c '^- task1-') -eq 5 ]"
t "коммиты только от мок-кодера"     "test \"\$(git log --format=%an | sort -u | xargs)\" = \"mock-coder probe\" && test \$(git log --oneline | wc -l) -eq 3"
t "src не тронут руками"             "[ -z \"\$(git status --porcelain src)\" ] && [ \$(wc -l < src/task1.txt) -eq 3 ]"
t "REPORT.md: заголовок и итог"      "grep -q '^# Отчёт' run/REPORT.md && grep -q 'Итог: DONE' run/REPORT.md"
echo "launches: $seq_got"; echo "RESULT: $ok pass, $bad fail"
E
chmod +x check.sh
wc -l skills/lead-probe/SKILL.md; grep -n "probe-marker" skills/lead-probe/SKILL.md | cut -c1-40
