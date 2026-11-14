# Зеркало прогона `state/run.json` (121-567, X2)

Сводка состояния прогона одним JSON — для внешних наблюдателей (дашборд, CI, разбор после прогона). Пишет только
тик (`supervisor-tick.py … --mode shared`) на каждом проходе: атомарно (временный файл и rename) и только если
содержимое изменилось. Ведущий и роли его не читают и не пишут — источник правды прежний: `PLAN.md`, `state.md`,
`runs/`, `reports/`, `ledger.jsonl`. В режиме Claude Code (1.1.x) зеркала нет.

```json
{
 "schema": 1,
 "name": "feature-slugify",
 "phase": "EXECUTION",
 "complexity": "SIMPLE",
 "budgets": {"mode": "observe", "max_agents": 17, "max_review_rounds": 3, "max_concurrent_agents": 4},
 "host": "opencode (L1)",
 "tasks": [{"id": "1", "title": "…", "status": "DONE", "files": ["src/textutil.py"], "planned_agents": 3,
            "coder": "task1-coder", "reviewer": "task1-review", "commits": ["a429eaa"]}],
 "agents": [{"name": "task1-coder", "role": "coder", "task": "1", "status": "done", "access": "write",
             "engine": "codex", "provider": "codex", "model": "gpt-5.6-sol", "last_event_at": "2026-11-14T07:50:12+01:00"}],
 "engines": [{"role": "task1-coder", "label": "001", "status": "done"}],
 "revision": "sha256:…"
}
```

| Поле | Откуда |
|---|---|
| `name`, `phase`, `complexity`, `host` | `state.md`: `## Team Name:`, `## Phase:`, `## Complexity:`, `## Host:` |
| `budgets` | раздел `## Budgets` (`docs/budgets.md`), иначе `null` |
| `tasks[]` | `PLAN.md` (`## Task <id>: <title>`, `Status:`, `Files to create/edit:`, `Planned agents:`); `coder` и `reviewer` — карточки задачи с ролью `coder` / `unified-reviewer`; `commits` — строки `COMMITS:` в `reports/task<id>-coder*.md` |
| `agents[]` | карточки `runs/`; `engine` и `model` — последний вызов движка с этим именем в `ledger.jsonl`, у родного исполнителя `engine: native`; `provider` — часть модели до `/`, иначе движок |
| `engines[]` | `ledger.jsonl`: последнее событие каждого вызова (роль, метка) — `running`, `done` или `failed` |
| `revision` | `sha256:` от JSON без поля `revision` (ключи по алфавиту) — меняется только вместе с содержимым |

Ключей, токенов и текстов писем в зеркале нет и быть не должно.

## Проверка

`supervisor-tick.py validate-run <run>` — код 1 и список проблем: не JSON или не объект; нет ключей или `schema` не 1;
`revision` не сходится с содержимым (файл правили не тиком); задачи расходятся с `PLAN.md` (статус, пропавшая задача,
повтор id, статус не из списка); исполнитель ссылается на чужую задачу, вызов движка с битым статусом; ключи вида
`api_key`, `token`, `secret`. `postrun-check` проверяет зеркало, если оно есть.
