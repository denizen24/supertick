# H3: живые проверки адаптера OpenCode (14.11.2026)

OpenCode 1.19.5, набор поставлен `tools/install-opencode.sh` в `~/.config/opencode/`. Стенд —
`/var/www/supertick-bench/alpha/` (тот же, что в прогонах alpha, задача `slugify`).

## Проба ведущего (`test_opencode_host.LiveOpencode`)

| № | Модель | Итог | Что выяснилось |
|---|---|---|---|
| 1 | `deepseek-v4.1-flash-free` | failed: root, 22 с | корень пробы — копия набора вне проекта: чтение LEAD.md — `external_directory`, отказ |
| 2 | то же | failed: root, 95 с | OpenCode берёт каталог проекта из `PWD`, а не из cwd: ведущий ушёл в чужой репозиторий |
| 3, 4 | то же | failed: root | ведущий читает внутренности пробы и файлы вне проекта; **любой отказ в правах обрывает `opencode run`** |
| 5 | `glm-5.3-flash-free` | failed: finish, 2400 с | протокол держит (8 движков, 8 ожиданий, задача 1 целиком) до «Free usage limit reached»; `opencode run` после этого молча висит |
| 6 | `glm-5.3-flash` (платная, $0,15/$0,50 за 1M) | **passed**, 413 с, ≈$0,16 | 14 запусков исполнителей, 13 ожиданий, все 9 проверок |

Починки: набор лежит внутри каталога навыка (каталоги навыков открыты ведущему и субагентам); проба ставит
`PWD` и разрешает каталог стенда своим `OPENCODE_CONFIG`; в `primitives.md` — правило «вне проекта только
корень набора».

## Приёмка H3: живой SIMPLE из OpenCode (`alpha/h3`)

Профиль `h3` собран `supertick-setup.py apply`: ведущий `opencode/glm-5.3-flash`, «всё на модели ведущего» —
все роли родные агенты `supertick-*`. Запуск из репозитория стенда с верным `PWD`:
`opencode run --format json -m opencode/glm-5.3-flash -- "Используй навык supertick-team-feature …"`.

- 14.11 07:47–08:02, 15 минут, `opencode run` вышел сам с кодом 0, сводка написана. ≈$0,30.
- 3 задачи DONE (ревью APPROVED → приёмка PASS у каждой), `VERIFICATION_REPORT`: ALL_PASS (Business 4/4,
  Spec 3/3, compileall, полный набор 15 тестов под flock). LEGACY — 3 пункта, «оставить».
- Коммиты кодера `a429eaa`, `39dc6dc`, `8cc7d7d` (подпись из `.git/config`), `GIT GUARD OK`.
- Родные агенты через `task`: coder 10, unified-reviewer 7, acceptance-checker 3, spec-verifier 2, ci-verifier 1,
  legacy-scanner 1; 11 вызовов — продолжения по `task_id`. Движков нет. «Замены движков: нет».

Кандидаты на доработку:
- родные исполнители на `glm-5.3-flash` в первых ответах ломали первую строку отчёта (русские маркеры,
  ограждения markdown) — ведущий исправлял продолжением; срез вступления из `run-engine.sh` к родным не
  применяется (отчёт пишет ведущий);
- два вызова `supervisor-wait.sh` без `timeout` (родные синхронны, ожидание вернулось быстро) — правило
  `primitives.md` модель соблюдает не всегда.
