# supertick в Kimi, Codex и OpenCode (2.0.0-beta)

С 2.0 командный прогон можно вести не только из Claude Code. Ведущим может быть Kimi Code CLI, Codex CLI
или OpenCode, а роли команды можно раздать по разным CLI и моделям. В Claude Code всё осталось как в
1.1.1: те же команды, тот же `~/.claude/agent-teams.json`, сборка отличается от 1.1.1 только файлами из
`tools/tests/snapshots/plugin-1.1.1.changes`.

| Среда | Уровень | Как ведущий ждёт | Исполнители по умолчанию | Состояние |
|---|---|---|---|---|
| Claude Code | L3 | фоновый `supervisor-wait.sh`, пробуждение по концу задачи | субагенты Claude, участники с сообщениями | 1.1.1, без изменений |
| Kimi Code CLI | L2 | фоновый `supervisor-wait.sh`, Kimi будит по концу фоновой задачи | родные субагенты Kimi (`supertick-<роль>`) | alpha |
| Codex CLI | L1 | `supervisor-wait.sh` на переднем плане, вызов за вызовом, внутри одного хода | движок `codex` с моделью ведущего | alpha |
| OpenCode | L1 | родные исполнители синхронны (`task`); движки — `supervisor-wait.sh` на переднем плане с `timeout` | родные агенты OpenCode (`supertick-<роль>`) | beta |

Kimi, Codex и OpenCode работают по общему протоколу `hosts/shared/LEAD.md`: сообщений между исполнителями
нет, цикл (кодер → ревью → правка → коммит → приёмка) ведёт сам ведущий, продолжая того же исполнителя.
Каталог прогона — `<репозиторий>/.supertick/teams/feature-<имя>/`. Как движок роли (исполнитель на другом
CLI) годится любой из `cursor`, `codex`, `kimi`, `opencode`, `claude`.

Общее для всех: из клона репозитория (ветка `universal`), нужны `bash`, `python3` (только stdlib), `git`;
Linux или macOS (Windows — только WSL). На macOS скрипты ждут GNU-инструменты: `brew install bash coreutils
util-linux` и в `PATH` перед системными — `$(brew --prefix coreutils)/libexec/gnubin`, `$(brew --prefix util-linux)/bin`
и `/sbin` (так собран и CI на macOS). Установщики повторяются безвредно и обновляют копию; удалить — тот
же скрипт с `--uninstall`. Новые навыки среда видит в новой сессии.

## Рецепты по средам

### Kimi Code CLI

```bash
tools/install-kimi.sh                  # ~/.kimi-code/plugins/managed/supertick + запись в plugins/installed.json
kimi -m kimi-code/k3                   # в сессии: «Используй навык supertick:setup», затем
                                       # «Используй навык supertick:team-feature с аргументами: «<фича>» --complexity=simple»
# без человека:
kimi -m kimi-code/k3 --output-format stream-json -p 'Используй навык supertick:team-feature с аргументами: …'
```

- Исполнители — родные субагенты `supertick-<роль>`; доступ держит тип (у рецензента нет записи и shell).
- `kimi -p` не выходит, пока жива фоновая задача: ведущий держит одно ожидание и гасит его на финише.
- Квота подписки — общая на всю команду: SIMPLE на родных субагентах выбирает большую часть пятичасового
  окна Kimi Code. Роли на других CLI её разгружают.

### Codex CLI

```bash
tools/install-codex.sh                 # локальный маркетплейс supertick-local, codex plugin add supertick@supertick-local
codex --sandbox danger-full-access -m gpt-5.6-sol
# без человека:
codex exec --sandbox danger-full-access -m gpt-5.6-sol --json -- 'Используй навык supertick:team-feature с аргументами: …'
```

- Ведущий — **без своей песочницы**, иначе исполнители `codex exec` из его shell не стартуют. Ограничения
  ролей держат песочницы исполнителей: чтение — `--sandbox read-only`, кодер — `workspace-write` с записью в
  каталоги `.git`. Поэтому роль без назначения идёт движком `codex`, а не `spawn_agent` (у него нет своей
  песочницы).
- Codex не будит: прогон — один длинный ход, `--` перед промптом обязателен (бриф начинается с `---`).

### OpenCode

```bash
tools/install-opencode.sh              # ~/.config/opencode: skills/supertick-team-feature, skills/supertick-setup,
                                       # agents/supertick-<роль>.md;  --project <репо> — в <репо>/.opencode
cd <репозиторий> && opencode -m opencode/glm-5.3-flash
# без человека — из каталога репозитория, с его PWD:
cd <репозиторий> && opencode run --format json -m opencode/glm-5.3-flash -- 'Используй навык supertick-team-feature с аргументами: …'
```

- Навыки называются `supertick-team-feature` и `supertick-setup` (у OpenCode нет пространств имён).
- **Весь набор лежит внутри навыка.** Файлы вне проекта OpenCode в `opencode run` не даёт читать
  (`external_directory` — запрос, в headless — отказ), а **любой отказ в правах обрывает прогон на месте**.
  Каталоги навыков открыты ведущему и исполнителям без вопросов — поэтому скрипты, LEAD.md и брифы ролей там.
- Каталог проекта OpenCode берёт из `PWD`, а не из cwd процесса: запускать из каталога репозитория.
- Исполнители — родные агенты `supertick-<роль>` с правами (`permission`): правят файлы только кодер,
  ci-verifier и risk-tester, у рецензента и исследователей нет ни правки, ни shell. Вызов `task` синхронный,
  продолжение — по `task_id`.
- `bash` обрывает команду через 120 с без параметра `timeout`; ожидание движков — с `timeout: 1800000`.
- **Оплата.** Zen берёт за токены. Проверено на `opencode/glm-5.3-flash` ($0,15 / $0,50 за 1M): проба ведущего
  ≈$0,16, приёмочный SIMPLE ≈$0,30. Бесплатные модели (`*-free`) упираются в общий лимит, после которого
  `opencode run` молча висит (ошибка только в `~/.local/share/opencode/log/opencode.log`); `deepseek-v4.1-flash-free`
  ведущим не держит протокол. Расход — `opencode stats`.

## Подключение: профиль

Навык setup (в Kimi и Codex — `supertick:setup`, в OpenCode — `supertick-setup`) показывает установленные CLI и
их модели, задаёт до четырёх вопросов и пишет профиль в `~/.config/supertick/config.json` (или в
`$SUPERTICK_CONFIG`):

- **ведущий** — среда и модель;
- **состав команды** — «всё на модели ведущего», «код и ревью на разных моделях» или «по группам ролей»
  (кодеры, рецензент, архитекторы и техлид, исследователи, проверяющие);
- **второе мнение** — другая модель для чувствительных задач или «выключено»;
- **имя профиля**.

Перед записью навык показывает таблицу «роль → движок → модель → доступ». Это ровно то, что исполнит
прогон: роль без назначения записывается в профиль явно (`null` — родной исполнитель среды), поэтому
роли из прежнего `~/.claude/agent-teams.json` профиль не перекрывают. Сам `agent-teams.json` диалог не
меняет никогда.

Без человека (скрипты, CI) — тот же диалог файлом ответов:

```bash
python3 <корень набора>/scripts/supertick-setup.py plan  --answers answers.json   # таблица, без записи
python3 <корень набора>/scripts/supertick-setup.py apply --answers answers.json   # запись профиля
```

Формат файла ответов — в шапке `supertick-setup.py`. `supertick-setup.py launch <профиль>` печатает
команду запуска ведущего.

**Проба ведущего.** `supertick-setup.py lead-probe <профиль>` — несколько минут вызовов модели на стенде с
мок-исполнителями: держит ли модель ведущего протокол (запуски через `run-engine.sh`, ожидание скриптом, цикл
ревью, приёмка, не трогает код сама). Итог пишется в профиль и показывается в диалоге. Проверены (passed):
Kimi `kimi-code/k3`, Codex `gpt-5.6-sol`, OpenCode `opencode/glm-5.3-flash`.

Аргументы прогона те же, что в Claude Code: `--complexity=simple|medium|complex`, `--coders=N`,
`--no-research`, `--force-team`, `--engines=off`.

## Чем отличается от Claude Code

- **Ожидание.** Kimi будит ведущего по концу фоновой задачи, как Claude Code: ведущий спит вне хода. Codex и
  OpenCode не будят: прогон идёт одним длинным ходом, ведущий ждёт внутри него `supervisor-wait.sh --max 1500`
  и снова. Закончил ход раньше сводки — прогон встал.
- **`.git` под присмотром.** Кодер может писать в `.git`, значит и в `.git/hooks`, а хук потом исполнит
  владелец вне песочницы (риск R11). `git-guard.py` снимает отпечаток `.git/hooks` и `.git/config` в начале
  прогона и сверяет в конце; изменение — строка ⚠️ и первый пункт ручных проверок, ведущий ничего не
  откатывает.
- **Нет браузерного проверяющего и живого веб-поиска** вне Claude Code: такие пункты уходят в ручные
  проверки; `web-researcher` работает только на движке с живым поиском.
- **Тихой замены нет.** Роль, чей CLI не найден, уходит по `fallback` (`host` — на исполнителя среды,
  `fail` — остановка) с видимой строкой ⚙️ в ленте и в сводке.

## Диагностика

```bash
python3 <корень набора>/scripts/supertick-doctor.py --host <среда>       # версия, среда, конфиг, CLI, профиль
python3 <корень набора>/scripts/engine-config.py resolve --host <среда>  # кто исполнит каждую роль
```

Корень набора: Kimi — `~/.kimi-code/plugins/managed/supertick`, Codex —
`~/.codex/plugins/cache/supertick-local/supertick/<версия>`, OpenCode — `~/.config/opencode/skills/supertick-team-feature`.

Каталог прогона — всё состояние: `PLAN.md`, `state.md`, `runs/`, `reports/`, `engine/`, `ledger.jsonl`
(каждый вызов движка с ролью, движком и моделью). После потери контекста ведущий восстанавливается по
нему и одному тику.

## Известные ограничения beta

- Квота подписки или бесплатный лимит модели — общий ресурс всей команды на родных исполнителях; исчерпание
  обрывает прогон (Kimi — `APIProviderQuotaExhaustedError`, OpenCode — молчаливое зависание).
- Исполнитель бывает отвечает вступлением перед первой строкой отчёта. У движков отчёт режется с первой
  строки-шапки автоматически; у родных исполнителей ведущий продолжает исполнителя, как велит протокол.
- Роли и навыки написаны по-русски; лента прогона — на языке пользователя.
- Прогоны приёмки — `docs/trials/2.0-alpha-runs.md` (R1–R3, R5, R6) и `docs/trials/2.0-beta-runs.md` (R4);
  разбор сред — `docs/spikes/`.
