# Примитивы среды: OpenCode (уровень L1)

Проверено спайком S0-OC (OpenCode 1.19.5, `docs/spikes/s0-hosts.md`). OpenCode **не будит** ведущего:
закончил ход — прогон встал. Весь цикл до сводки ты ведёшь в одном ходе и ждёшь внутри него.

| Операция | В OpenCode |
|---|---|
| ЗАПУСТИТЬ | роль на движке (`lead:argv` дал `rc=0`) — `lead:launch`. Родной исполнитель (`rc=3`) — инструмент `task`: `subagent_type: "supertick-<роль>"`, `description: "<NAME>"`, `prompt` — текст промпт-файла целиком. Вызов **синхронный**: возвращается, когда исполнитель закончил, с его ответом. id из `<task id="ses_…">` — в `## Executors` и `run-state.py set <RUN> <NAME> note=agent=<id>`. Независимых исполнителей можно запустить разом — несколько `task` в одном сообщении |
| ПРОДОЛЖИТЬ | движок — `lead:resume`. Родной — `task` с тем же `subagent_type` и `task_id: "<id>"`, `prompt` — текст нового промпт-файла: исполнитель помнит прошлый контекст |
| ОТВЕТ | движок сам кладёт отчёт (`lead:take`). Родной — текст внутри `<task_result>` дословно в `<RUN>/reports/<отчёт>.md` (инструмент записи), без `touch` |
| ЖДАТЬ | родного ждать не нужно — его ответ уже пришёл. Движки — `bash` с параметром `timeout: 1800000`: `bash <S>/supervisor-wait.sh <RUN> --root <REPO> --mode shared --max 1500` **на переднем плане**. Вышла — разбери вывод и снова ЖДАТЬ |
| СПРОСИТЬ | в интерактивной сессии — вопрос текстом с нумерованными вариантами и конец хода (ответ — новый ход). В `opencode run` спросить некого: выбери безопасный вариант, запиши его в DECISIONS.md и строку ⚙️ в ленту |
| ОСТАНОВИТЬ | движок — `kill <pid>` из `run-engine.sh --status <RUN> <NAME>`. Родной синхронный — останавливать нечего |

## Что важно знать

- **Ход не заканчивай**, пока прогон не дошёл до сводки или до вопроса пользователю. Фраза «жду
  исполнителя» с концом хода — это остановка прогона.
- **Вне проекта — только корень набора.** Файлы вне каталога проекта и вне `<корень>` не открывай и не
  читай (ни `cat`, ни чтением): OpenCode спрашивает разрешение, в `opencode run` спросить некого — отказ, и
  **отказ обрывает весь прогон на месте**, без сводки. Нужное снаружи (конфиг, логи) — через скрипты `<S>`.
- **`timeout` у долгих команд обязателен.** Без него `bash` обрывает команду через 120 секунд
  («shell tool terminated command after exceeding timeout»). У `supervisor-wait.sh` — всегда
  `timeout: 1800000`. Строка `WAKE: истёк максимум` — плановая проверка, не ошибка: сразу снова ЖДАТЬ.
- **Не опрашивай `sleep`** и не читай `.out` движков в цикле: ждёт `supervisor-wait.sh`.
- **Типы исполнителей** — `supertick-<роль>` из этого набора: `supertick-coder`,
  `supertick-unified-reviewer`, `supertick-acceptance-checker`, `supertick-spec-verifier`,
  `supertick-ci-verifier`, `supertick-tech-lead`, `supertick-architect`, `supertick-risk-tester`,
  `supertick-codebase-researcher`, `supertick-reference-researcher`, `supertick-legacy-scanner`.
  `second-reviewer` и `architect-*` — `supertick-unified-reviewer` и `supertick-architect`. Встроенные
  `general` и `explore` не используй: у них нет брифа роли и своих прав.
- **Доступ** держат права типа (`permission`): у рецензента, техлида, архитектора и исследователей нет
  правки файлов и shell; у приёмщика и проверяющего спецификации есть shell для команд, а правка
  запрещена; правят файлы только кодер, ci-verifier и risk-tester.
- **Модель родных исполнителей** — модель твоей сессии. Роль на другой модели или другом CLI —
  движком через `engine-config.py argv` (профиль, `supertick-setup.py`).
- **Корень набора** — `$SUPERTICK_ROOT`, если задан, иначе каталог навыка `supertick-team-feature`
  («Base directory for this skill» в тексте навыка). Набор лежит внутри навыка не случайно: файлы вне
  проекта OpenCode в `opencode run` не даёт читать (`external_directory` — отказ), а каталоги навыков
  открыты и тебе, и исполнителям.
- **Диалог подключения** — навык `supertick-setup`; из `opencode run` — `python3 <S>/supertick-setup.py
  apply --answers <файл>`.
