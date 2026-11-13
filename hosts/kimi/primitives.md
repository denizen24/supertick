# Примитивы среды: Kimi Code CLI (уровень L2)

Проверено спайком S0-K и S1 (Kimi Code CLI 2.5.3, `docs/spikes/s0-hosts.md`). Операции LEAD.md:

| Операция | В Kimi |
|---|---|
| ЗАПУСТИТЬ | `Agent(subagent_type="supertick-<роль>", description="<NAME>", prompt=<текст промпт-файла>, run_in_background=true)`. Ответ сразу: `task_id`, `agent_id` — запиши `agent_id` в `## Executors` state.md и в карточку: `run-state.py set <RUN> <NAME> note=agent=<agent_id>` |
| ПРОДОЛЖИТЬ | `Agent(resume="<agent_id>", prompt=<текст нового промпт-файла>, run_in_background=true)`. Тип при продолжении не указывается: исполнитель помнит прошлый контекст |
| ОТВЕТ | конец фонового исполнителя приходит сам отдельным ходом с его итоговым сообщением (`[summary]`). Его ты дословно кладёшь в `<RUN>/reports/<отчёт>.md` (Write) — и дальше как в `lead:take`, без `touch` |
| ЖДАТЬ | `Bash(command="bash <S>/supervisor-wait.sh <RUN> --root <REPO> --mode shared", run_in_background=true)` и конец хода. Будит тебя его конец или конец любого фонового исполнителя |
| СПРОСИТЬ | `AskUserQuestion` с 2–4 вариантами — в интерактивной сессии. В режиме `kimi -p` он запрещён: вопрос текстом с нумерованными вариантами и конец хода |
| ОСТАНОВИТЬ | `TaskStop(task_id=<task_id>)` для фоновой задачи или исполнителя |

## Что важно знать

- **Не опрашивай.** После запуска фонового исполнителя не зови `TaskOutput` и не жди в цикле: его
  конец придёт сам. Ожидание — только `supervisor-wait.sh` в фоне.
- **Одно ожидание.** `kimi -p` не выходит, пока жива хоть одна фоновая задача: забытое ожидание держит
  сессию до таймаута. Новое ожидание гасит прежнее само; конец погашенного (в выводе нет строки `WAKE:`) — не дело,
  ещё одно ожидание на него не запускай. На финише — `lead:wait-stop`.
- **Shell на переднем плане** — до 300 с; дольше — только в фоне.
- **Типы исполнителей** — `supertick-<роль>` из этого плагина: `supertick-coder`,
  `supertick-unified-reviewer`, `supertick-acceptance-checker`, `supertick-spec-verifier`,
  `supertick-ci-verifier`, `supertick-tech-lead`, `supertick-architect`, `supertick-risk-tester`,
  `supertick-codebase-researcher`, `supertick-reference-researcher`, `supertick-legacy-scanner`.
  `second-reviewer` и `architect-*` — `supertick-unified-reviewer` и `supertick-architect`. Чужие
  типы (`coder`, `explore`, `plan`) не используй: у них нет брифа роли.
- **Доступ** держит тип: у рецензента, техлида, архитектора и исследователей нет записи и shell; у
  приёмщика и проверяющего спецификации есть shell для команд, но запись им запрещает бриф.
- **Модель родных исполнителей** — настройка Kimi `[secondary_model]` (или модель сессии); плагин её
  не меняет. Роль на другой модели — движком через `engine-config.py argv`.
- **Корень набора** — `$SUPERTICK_ROOT`, если задан, иначе два уровня над каталогом навыка
  (`${KIMI_SKILL_DIR}/../..`); точка входа считает его одной командой `cd "${SUPERTICK_ROOT:-…}"`.
- **Диалог подключения** — навык `supertick:setup`; из `-p` — `python3 <S>/supertick-setup.py apply
  --answers <файл>`.
