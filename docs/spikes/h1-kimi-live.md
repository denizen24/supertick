# H1: живой SIMPLE из Kimi (12.11.2026)

Kimi Code CLI 2.5.3, ведущий `kimi-code/k3` в режиме `kimi -p --output-format stream-json`, плагин поставлен
`tools/install-kimi.sh` (без интерактивного `/plugins install`). Профиль `kimi-live` (всё на модели ведущего)
через `SUPERTICK_CONFIG`; прежний конфиг 1.1.x сливался с ним (исследователи, проверяющие — cursor).

Задача: песочница-калькулятор, «добавь div(a, b), при b == 0 — ValueError, тесты», аргументы
`--no-research --force-team`.

## Итог

- План из трёх задач (div, тесты, `.conventions/`), все три DONE: ревью — APPROVED с первого круга, приёмка —
  PASS, три коммита кодеров; фаза 3 — сверка задач, ci, spec, скан наследия, VERIFICATION_REPORT `ALL_PASS`.
  Тесты песочницы зелёные, финальный тик `--mode shared` — P4.
- 126 ходов ведущего: Bash 63, Read 25, Write 24, Agent 16, Skill 1, TaskList 1, TaskStop 1. Около 17 минут.
- Исполнители — родные субагенты `supertick-*` (тип из плагина), все `run_in_background=true`.
- **Продолжение работало:** коммит после APPROVED — `Agent(resume="agent-0" / "agent-3" / "agent-6")`, тот же
  кодер, а не новый запуск. FAIL ревью в этом прогоне не случился, поэтому продолжение после CHANGES REQUESTED
  живьём не проверено; механизм тот же (`resume`), путь FAIL закрыт сквозным тестом `test_lead_protocol.py`.
- **Ожидание в фоне:** пять фоновых `supervisor-wait.sh --mode shared`; ведущий просыпался сам (по концу
  ожидания или исполнителя) и в режиме `-p`.
- **Запасной путь сработал:** сверка задач на cursor упала дважды за 3 с (`Authentication required` у
  cursor-agent в окружении Kimi) — ведущий по LEAD.md перезапустил один раз и ушёл на родной исполнитель
  (`fallback: host`), строка в state.md. Отдельно проверить вход cursor-agent из дочерних процессов Kimi.

Каталог прогона и журнал — в scratchpad сессии (`kimi-simple/.supertick/teams/feature-div`, `kimi-live.jsonl`).
