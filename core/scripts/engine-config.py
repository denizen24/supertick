#!/usr/bin/env python3
"""engine-config.py — конфиг движков supertick: кто исполняет роль (121-567, F3, п. 4.3 ТЗ).

Заменяет разбор конфига ведущим на шаге 0b. Только stdlib. Команды:

  engine-config.py host     [--host H] [--model M] [--profile P] [--json]
  engine-config.py resolve  [--host H] [--profile P] [--engines off] [--available b1,b2] [--json]
  engine-config.py argv     <роль> --run-dir D --prompt F [--name N] [--task T] [--report R]
                            [--timeout S] [--resume [ID]] [--json]
  engine-config.py validate [--json]
  (у всех: --config ФАЙЛ, повторяемый, вместо поиска по умолчанию)

Конфиг ищется так: $SUPERTICK_CONFIG, затем ~/.config/supertick/config.json, затем прежний
файл 1.1.x (путь в LEGACY_CONFIG). Найденные файлы сливаются по ключам, более ранний главнее.
Ключи на «_» на любом уровне — комментарии. Активный профиль накладывает свои роли поверх `roles`.

Правила разрешения роли (п. 4.3):
 1. роль без назначения исполняет родной исполнитель хоста; без субагентов у хоста — его движок;
 2. движок назначен, но бинаря нет — роль уходит по `fallback` (host | fail; claude = host);
 3. доступ роли берётся из реестра; явный `access` может только сузить запись до чтения;
 4. роль с записью нельзя назначить движку без режима записи: ошибка, а не тихая замена;
 5. `second-reviewer` выключен, если ключа или бинаря нет либо движок и модель совпали с рецензентом (D7);
 6. роль только для Claude Code на другом хосте выключена; `web-researcher` — только на движок с поиском.

Коды выхода: 0 готово; 1 stop/ошибки validate; 2 неверный вызов или нечитаемый конфиг-аргумент;
3 `argv` для роли, которую исполняет родной исполнитель (команды нет).
"""
import argparse
import json
import os
import shlex
import shutil
import sys
import uuid
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
LAUNCHER = SCRIPTS_DIR / "run-engine.sh"
ENGINES_DIR = SCRIPTS_DIR.parent / "engines"      # профили движков, которые лежат в наборе плагина
LEGACY_CONFIG = "~/.claude/agent-teams.json"      # D18: путь 1.1.x читается как раньше
HOST_MARKER_ENV = "CLAUDECODE"                    # D27: среда Claude Code узнаётся по переменной
XDG_CONFIG = "~/.config/supertick/config.json"

# Хосты. native — как называется родной исполнитель в таблице; subagents — есть ли у хоста родные
# субагенты. Уровни L1–L3 из п. 1.3; opencode и codex — предварительно, до спайка S0-OC.
HOSTS = {
    "claude-code": {"level": "L3", "native": "claude", "subagents": True, "engine": "claude"},
    "kimi":        {"level": "L2", "native": "native", "subagents": True, "engine": "kimi"},
    "opencode":    {"level": "L1", "native": "opencode", "subagents": False, "engine": "opencode"},
    "codex":       {"level": "L1", "native": "codex", "subagents": False, "engine": "codex"},
}

# Пресеты. Шаблоны — списки аргументов; {prompt} раскрывает run-engine.sh, {engines} и {scripts} —
# этот скрипт (каталоги набора). cursor, codex, grok — как в 1.1.1 (engines.md «Пресеты»);
# kimi переписан под Kimi Code CLI 2.x, claude и mock — новые (F4). Все новые проверены живыми вызовами
# 12.11.2026: ответ, продолжение помнит слово, режим чтения не пишет файлы.
PRESETS = {
    "cursor": {
        "binary": "cursor-agent", "model": "cursor-grok-4.6-xhigh", "effort": "",
        "cmd": ["cursor-agent", "-p", "--trust", "--output-format", "json", "--model", "{model}", "{mode_flags}", "--", "{prompt}"],
        "resume": ["cursor-agent", "-p", "--trust", "--output-format", "json", "--model", "{model}", "{mode_flags}",
                   "--resume", "{session}", "--", "{prompt}"],
        "mode_flags": {"read": "--mode ask", "write": "--sandbox enabled -f"},
        "read_runs_commands": False,   # `--mode ask` отклоняет shell (22.10.2026): роли с командами берут запись
        "path_prefix": True,           # бинарь в ~/.local/bin
    },
    "codex": {
        "binary": "codex", "model": "gpt-5.6-sol", "effort": "xhigh",
        "cmd": ["codex", "exec", "--skip-git-repo-check", "--sandbox", "{sandbox}", "-m", "{model}",
                "-c", "model_reasoning_effort={effort}", "{prompt}"],
        "resume": ["codex", "exec", "resume", "{session}", "{prompt}"],
        "sandbox": True, "read_runs_commands": True,
    },
    "kimi": {   # Kimi Code CLI 2.x: JSONL, ответ — строки assistant, сессия — строка session.resume_hint
        "binary": "kimi", "model": "kimi-code/k3", "effort": "",
        "cmd": ["kimi", "-m", "{model}", "--output-format", "stream-json", "{mode_flags}", "-p", "{prompt}"],
        # --agent-file с -S не сочетается: профиль привязан к сессии и при продолжении восстанавливается сам
        "resume": ["kimi", "-S", "{session}", "-m", "{model}", "--output-format", "stream-json", "-p", "{prompt}"],
        "mode_flags": {"read": "--agent-file {engines}/kimi-read.md", "write": ""},
        "write": True,                 # запись — режим по умолчанию в -p
        "read_runs_commands": False,   # профиль чтения без shell: роли с командами берут запись
    },
    "claude": {   # дочерний Claude Code без навыков, команд, MCP и пользовательских настроек (нет рекурсии)
        "binary": "claude", "model": "sonnet", "effort": "",
        "cmd": ["env", "-u", HOST_MARKER_ENV, "claude", "-p", "--output-format", "json", "--model", "{model}",
                "--disable-slash-commands", "--strict-mcp-config", "--setting-sources", "project,local",
                "{mode_flags}", "--", "{prompt}"],
        "resume": ["env", "-u", HOST_MARKER_ENV, "claude", "-p", "--output-format", "json", "--model", "{model}",
                   "--disable-slash-commands", "--strict-mcp-config", "--setting-sources", "project,local",
                   "{mode_flags}", "--resume", "{session}", "--", "{prompt}"],
        "mode_flags": {"read": "--tools Read,Grep,Glob",
                       "write": "--permission-mode acceptEdits --allowedTools Bash,Edit,Write,Read,Grep,Glob"},
        "read_runs_commands": False,
    },
    "mock": {   # мок-движок набора: формат как у claude, режим из SUPERTICK_MOCK_MODE (ok|fail|hang|no-session|is_error)
        "binary": "mock-engine.py", "model": "mock-1", "effort": "", "always_available": True,
        "cmd": ["{scripts}/mock-engine.py", "--model", "{model}", "{mode_flags}", "-p", "{prompt}"],
        "resume": ["{scripts}/mock-engine.py", "--model", "{model}", "{mode_flags}", "--session", "{session}", "-p", "{prompt}"],
        "mode_flags": {"read": "--read-only", "write": ""},
        "write": True, "read_runs_commands": True,
    },
    "grok": {
        "binary": "grok", "model": "grok-4.6", "effort": "high",
        "cmd": ["grok", "--sandbox", "{sandbox}", "--always-approve", "-m", "{model}", "--effort", "{effort}",
                "--session-id", "{session}", "-p", "{prompt}"],
        "resume": ["grok", "--sandbox", "{sandbox}", "--always-approve", "--effort", "{effort}", "-r", "{session}", "-p", "{prompt}"],
        "sandbox": True, "read_runs_commands": True, "live_search": True,
        "caller_session": True,        # id сессии заводит вызывающий (uuid) и отдаёт скрипту --session
    },
}
SANDBOX = {"read": "read-only", "write": "workspace-write"}
ACCESS_ORDER = {"read": 0, "write": 1}

# Реестр ролей (engines.md «Реестр ролей», п. 4.6). access — read | write; commands — роли, которым
# нужен shell; native_on — хосты, где роль всегда на родном исполнителе; only_on — роль есть лишь там.
ROLES = {
    "lead":                 {"kind": "orchestrator", "access": "write", "native_on": "all"},
    "codebase-researcher":  {"kind": "one-shot", "access": "read"},
    "reference-researcher": {"kind": "one-shot", "access": "read"},
    "web-researcher":       {"kind": "one-shot", "access": "read", "needs_search": True},
    "risk-tester":          {"kind": "one-shot", "access": "write", "timeout": 3600},
    "acceptance-checker":   {"kind": "one-shot", "access": "read", "commands": True, "native_on": ["claude-code"]},
    "ci-verifier":          {"kind": "one-shot", "access": "write", "commands": True},
    "spec-verifier":        {"kind": "one-shot", "access": "read", "commands": True},
    "browser-verifier":     {"kind": "one-shot", "access": "read", "native_on": "all", "only_on": ["claude-code"]},
    "legacy-scanner":       {"kind": "one-shot", "access": "read"},
    "tech-lead":            {"kind": "participant", "access": "read", "timeout": 1800},
    "architect":            {"kind": "participant", "access": "read", "timeout": 1800},
    "architect-frontend":   {"kind": "participant", "access": "read", "timeout": 1800},
    "architect-backend":    {"kind": "participant", "access": "read", "timeout": 1800},
    "architect-systems":    {"kind": "participant", "access": "read", "timeout": 1800},
    "unified-reviewer":     {"kind": "participant", "access": "read", "timeout": 1800},
    "second-reviewer":      {"kind": "participant", "access": "read", "timeout": 1800},
    "coder":                {"kind": "participant", "access": "write", "timeout": 3600},
}
FOREIGN = {"research-challenger", "research-critic", "rollback-critic"}   # роли чужих плагинов
RETIRED = {"security-reviewer", "logic-reviewer", "quality-reviewer"}     # снятые id: предупреждение и игнор
FALLBACKS = {"host", "fail", "claude"}
ROLE_KEYS = {"engine", "model", "effort", "sandbox", "access"}
ENGINE_KEYS = {"model", "effort", "cmd", "resume", "mode_flags", "session", "result"}
TOP_KEYS = {"enabled", "fallback", "active_profile", "profiles", "roles", "engines"}
PROFILE_KEYS = {"lead", "roles", "confirmed_at", "fingerprint", "lead_probe"}


# ------------------------------------------------------------------ конфиг
def strip_comments(x):
    if isinstance(x, dict):
        return {k: strip_comments(v) for k, v in x.items() if not str(k).startswith("_")}
    if isinstance(x, list):
        return [strip_comments(v) for v in x]
    return x


def merge(high, low):
    """Слияние по ключам: словари вглубь, иначе побеждает более ранний (high)."""
    if isinstance(high, dict) and isinstance(low, dict):
        out = dict(low)
        for k, v in high.items():
            out[k] = merge(v, low[k]) if k in low else v
        return out
    return high


def config_paths(explicit=None, env=None):
    env = os.environ if env is None else env
    if explicit:
        return [Path(p) for p in explicit]
    found = []
    if env.get("SUPERTICK_CONFIG"):
        found.append(Path(env["SUPERTICK_CONFIG"]).expanduser())
    found.append(Path(XDG_CONFIG).expanduser())
    found.append(Path(LEGACY_CONFIG).expanduser())
    return found


def load_config(paths):
    """(слитый конфиг без комментариев, предупреждения, использованные файлы)."""
    merged, warnings, used = {}, [], []
    for p in paths:
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("корень не объект")
        except (ValueError, OSError):
            warnings.append({"code": "broken_json", "detail": str(p)})
            continue
        merged = merge(merged, strip_comments(data))
        used.append(str(p))
    return merged, warnings, used


def role_spec(spec):
    if isinstance(spec, str):
        return {"engine": spec}
    return dict(spec) if isinstance(spec, dict) else {}


def active_profile(cfg, name=None):
    name = name or cfg.get("active_profile")
    if not name:
        return None, None
    profile = (cfg.get("profiles") or {}).get(name)
    return name, profile if isinstance(profile, dict) else None


def effective_roles(cfg, profile):
    roles = {r: role_spec(s) for r, s in (cfg.get("roles") or {}).items()}
    for r, s in ((profile or {}).get("roles") or {}).items():
        roles[r] = role_spec(s)   # профиль накладывается поверх: роль заменяется целиком
    return roles


# ------------------------------------------------------------------ движки
def split_template(value):
    """Строка cmd/resume из конфига -> шаблон аргументов (без оболочки: никакого eval)."""
    tokens = list(value) if isinstance(value, list) else shlex.split(value)
    out, skip = [], False
    for i, t in enumerate(tokens):
        if skip:
            skip = False
            continue
        if t == "<" and i + 1 < len(tokens) and tokens[i + 1] == "/dev/null":
            skip = True       # stdin скрипт и так не даёт движку
            continue
        out.append(t.replace("$(cat {prompt_file})", "{prompt}"))
    return out


def engine_def(cfg, name):
    """Пресет, поле за полем переопределённый `engines.<name>`; None, если движок неизвестен."""
    base = dict(PRESETS.get(name) or {})
    over = (cfg.get("engines") or {}).get(name)
    over = over if isinstance(over, dict) else {}
    if not base and not over.get("cmd"):
        return None
    if not base:
        base = {"binary": split_template(over["cmd"])[0], "model": "", "effort": "", "read_runs_commands": True}
    for key in ("model", "effort"):
        if over.get(key):
            base[key] = over[key]
    for key in ("cmd", "resume"):
        if over.get(key):
            base[key] = split_template(over[key])
    if isinstance(over.get("mode_flags"), dict):
        base["mode_flags"] = over["mode_flags"]
    base["name"] = name
    return base


def write_supported(eng):
    """Есть ли у движка режим записи: флаги записи из конфига, явный признак пресета или {sandbox}."""
    if (eng.get("mode_flags") or {}).get("write"):
        return True
    if eng.get("write") is not None:
        return bool(eng["write"])
    if eng.get("sandbox"):
        return True
    text = " ".join(eng.get("cmd", []) + eng.get("resume", []))
    return "{sandbox}" in text


def role_access(role, spec):
    """(доступ роли, ошибка). Реестр + явные `sandbox` (ключ 1.1.x) и `access` (только сужает)."""
    reg = ROLES.get(role, {}).get("access", "read")
    access, error = reg, None
    if spec.get("sandbox") in SANDBOX.values():
        access = "write" if spec["sandbox"] == "workspace-write" else "read"
    if spec.get("access") in ACCESS_ORDER:
        if ACCESS_ORDER[spec["access"]] > ACCESS_ORDER[reg]:
            error = f"access {spec['access']} шире, чем в реестре ({reg})"
        else:
            access = spec["access"]
    return access, error


def call_access(eng, role, access):
    """Режим вызова движка: роль с командами на движке, чей режим чтения shell не пускает, берёт запись."""
    if access == "read" and ROLES.get(role, {}).get("commands") and not eng.get("read_runs_commands", True):
        return "write"
    return access


def model_of(cfg, role_spec_, eng):
    return role_spec_.get("model") or eng.get("model") or ""


def effort_of(role_spec_, eng):
    return role_spec_.get("effort") or eng.get("effort") or ""


# ------------------------------------------------------------------ хост
def detect_host(flag, env=None):
    env = os.environ if env is None else env
    if flag:
        if flag not in HOSTS:
            raise ValueError(f"неизвестный хост {flag}: {', '.join(HOSTS)}")
        return flag, "flag"
    if env.get(HOST_MARKER_ENV) == "1":
        return "claude-code", "env"
    raise ValueError("хост не определён: передайте --host (навык среды делает это сам)")


def binary_of(cfg, engine, host):
    if engine == "claude" and host == "claude-code":
        return None          # родной исполнитель: процесса нет
    eng = engine_def(cfg, engine)
    return eng["binary"] if eng else engine


def system_available(binary):
    if shutil.which(binary):
        return True
    return (Path.home() / ".local" / "bin" / binary).exists()


# ------------------------------------------------------------------ разрешение
# Коды предупреждений и их ключи в строке кода (как в эталоне F1): engine_missing:<роль>:<движок> и т. п.
CODE_KEYS = {"engine_missing": ("role", "engine"), "assignment_ignored": ("role",), "retired_role": ("role",),
             "role_disabled_on_host": ("role",), "web_search_required": ("role",), "profile_missing": ("role",)}
FEED = {
    "broken_json": "конфиг {detail} не читается (битый JSON): он пропущен",
    "engine_missing": "роль {role}: движок {engine} не найден, роль на {native}",
    "assignment_ignored": "роль {role} всегда на родном исполнителе: назначение {engine} игнорируется",
    "retired_role": "id {role} снят: назначение игнорируется",
    "second_reviewer_same_engine": "second-reviewer совпал с unified-reviewer ({detail}): второе мнение выключено",
    "profile_missing": "профиль {role} не найден: взяты роли из roles",
    "role_disabled_on_host": "роль {role} есть только в Claude Code: на хосте {detail} выключена",
    "web_search_required": "роль {role}: движок {engine} без живого поиска, роль выключена",
}
NATIVE_NAME = {"claude": "Claude", "native": "родном исполнителе"}


def code_of(w):
    return ":".join([w["code"]] + [w[k] for k in CODE_KEYS.get(w["code"], ()) if w.get(k)])


def feed_line(w, native):
    return "⚙️ " + FEED.get(w["code"], w["code"]).format(
        native=NATIVE_NAME.get(native, native), detail=w.get("detail", ""), role=w.get("role", ""), engine=w.get("engine", ""))


def discover_available(cfg):
    """Бинари, найденные в системе: пресеты и движки из конфига."""
    engines = [PRESETS[n] for n in PRESETS] + [d for n in (cfg.get("engines") or {})
                                               if n not in PRESETS and (d := engine_def(cfg, n))]
    return {e["binary"] for e in engines if system_available(e["binary"])}


def resolve(cfg, host, available, engines_off=False, profile=None, load_warnings=(), have_config=True):
    """Шаг 0b. Словарь: stop/reason, table, detail, warnings (коды), feed, summary_line, host, level, profile."""
    h = HOSTS[host]
    native = h["native"]
    res = {"stop": False, "table": {}, "detail": {}, "warnings": [], "feed": [], "summary_line": False,
           "host": host, "level": h["level"], "profile": None}
    warns = list(load_warnings)

    def done(**extra):
        res.update(extra)
        res["warnings"] = [code_of(w) for w in warns]
        res["feed"] = [feed_line(w, native) for w in warns]
        return res

    def stop(reason):
        for k in ("table", "detail", "summary_line"):
            res.pop(k, None)
        return done(stop=True, reason=reason)

    if not have_config or engines_off or cfg.get("enabled", True) is False:
        return done()
    name, prof = active_profile(cfg, profile)
    if name and prof is None:
        warns.append({"code": "profile_missing", "role": name})
    res["profile"] = name if prof is not None else None
    fallback = cfg.get("fallback", "host")
    roles = effective_roles(cfg, prof)

    def is_native(engine):
        return engine is None or (host == "claude-code" and engine == "claude")

    def usable(engine):
        if is_native(engine):
            return True
        eng = engine_def(cfg, engine)
        return eng is not None and (bool(eng.get("always_available")) or binary_of(cfg, engine, host) in available)

    def detail_of(engine, spec):
        if is_native(engine):
            return {"engine": native, "model": ""}
        return {"engine": engine, "model": model_of(cfg, spec, engine_def(cfg, engine) or {})}

    table, details, explicit = {}, {}, set()
    for role, spec in roles.items():
        if role in RETIRED:
            warns.append({"code": "retired_role", "role": role})
            continue
        if role in FOREIGN or role not in ROLES:
            continue
        reg, engine = ROLES[role], spec.get("engine")
        if reg.get("only_on") and host not in reg["only_on"]:          # правило 6: роль только для Claude Code
            table[role], details[role] = "none", {"engine": "none", "model": ""}
            warns.append({"code": "role_disabled_on_host", "role": role, "detail": host})
            continue
        forced = reg.get("native_on") == "all" or host in (reg.get("native_on") or [])
        if forced and not is_native(engine):
            warns.append({"code": "assignment_ignored", "role": role, "engine": engine})
            continue
        table[role], details[role] = detail_of(engine, spec)["engine"], detail_of(engine, spec)
        explicit.add(role)
    # правило 4: роль с записью — только на движок с режимом записи; не тихая замена, а остановка
    for role in table:
        eng = None if is_native(roles[role].get("engine")) else engine_def(cfg, roles[role].get("engine"))
        if eng and role_access(role, roles[role])[0] == "write" and not write_supported(eng):
            return stop(f"no_write_mode:{role}:{roles[role]['engine']}")
    # правило 6, поиск: web-researcher вне Claude Code — только на движок с живым поиском
    if "web-researcher" in explicit and host != "claude-code":
        if not (engine_def(cfg, roles["web-researcher"].get("engine")) or {}).get("live_search"):
            warns.append({"code": "web_search_required", "role": "web-researcher", "engine": table["web-researcher"]})
            table["web-researcher"], details["web-researcher"] = "none", {"engine": "none", "model": ""}
            explicit.discard("web-researcher")
    # правило 2: движка нет — по fallback (second-reviewer от него свободен: правило 5)
    for role in [r for r in table if r != "second-reviewer"]:
        engine = table[role]
        if engine in (native, "none") or usable(engine):
            continue
        if fallback == "fail":
            return stop(f"engine_missing:{role}:{engine}")
        warns.append({"code": "engine_missing", "role": role, "engine": engine})
        table[role], details[role] = native, {"engine": native, "model": ""}
        explicit.discard(role)
    # правило 1: хост без субагентов исполняет неназначенные роли своим движком
    if not h["subagents"]:
        for role, reg in ROLES.items():
            if role not in table and role not in ("second-reviewer", "browser-verifier", "web-researcher") \
                    and reg.get("native_on") != "all":
                table[role], details[role] = h["engine"], {"engine": h["engine"], "model": ""}
    # правило 5: second-reviewer (D7) — ключ, бинарь и несовпадение с рецензентом по движку И модели
    sec_engine = roles.get("second-reviewer", {}).get("engine") if "second-reviewer" in roles else None
    sec_named = "second-reviewer" in table and not (ROLES["second-reviewer"].get("only_on"))
    udet = details.get("unified-reviewer", {"engine": native, "model": ""})
    if not sec_named or not usable(sec_engine):
        table["second-reviewer"], details["second-reviewer"] = "none", {"engine": "none", "model": ""}
    else:
        sdet = detail_of(sec_engine, roles["second-reviewer"])
        if (sdet["engine"], sdet["model"]) == (udet["engine"], udet.get("model", "")):
            warns.append({"code": "second_reviewer_same_engine", "detail": f"{sdet['engine']} {sdet['model']}".strip()})
            table["second-reviewer"], details["second-reviewer"] = "none", {"engine": "none", "model": ""}
        else:
            table["second-reviewer"], details["second-reviewer"] = sdet["engine"], sdet
    explicit.add("second-reviewer")
    return done(table=table, detail=details,
                summary_line=any(table[r] not in (native, "none") for r in explicit if r in table))


def engines_section(res):
    lines = ["## Engines", f"- launcher: {LAUNCHER}", f"- host: {res['host']} ({res['level']})"]
    if res.get("profile"):
        lines.append(f"- profile: {res['profile']}")
    for role, engine in sorted(res.get("table", {}).items()):
        model = res.get("detail", {}).get(role, {}).get("model", "")
        lines.append(f"- {role}: {engine}" + (f" {model}" if model and engine != "none" else ""))
    return "\n".join(lines)


# ------------------------------------------------------------------ argv
def build_argv(cfg, host, role, name, run_dir, prompt, *, task="", report="", timeout=None,
               resume=None, profile=None, available=None, have_config=True):
    h = HOSTS[host]
    res = resolve(cfg, host, available if available is not None else discover_available(cfg),
                  profile=profile, have_config=have_config)
    if res["stop"]:
        raise ValueError(f"прогон остановлен: {res['reason']}")
    engine = res["table"].get(role)
    if engine in (None, "none") or (h["subagents"] and engine == h["native"]):
        return None
    eng = engine_def(cfg, engine)
    if eng is None:
        raise ValueError(f"для движка {engine} нет пресета")
    _, prof = active_profile(cfg, profile)
    spec = effective_roles(cfg, prof).get(role, {})
    access = call_access(eng, role, role_access(role, spec)[0])
    session = resume or (str(uuid.uuid4()) if eng.get("caller_session") else "")
    values = {"{model}": model_of(cfg, spec, eng), "{effort}": effort_of(spec, eng),
              "{sandbox}": SANDBOX[access], "{session}": session,
              "{engines}": str(ENGINES_DIR), "{scripts}": str(SCRIPTS_DIR)}
    argv = []
    for tok in (eng["resume"] if resume else eng["cmd"]):
        if tok == "{mode_flags}":   # режется по пробелам ДО подстановки путей: путь с пробелом остаётся одним аргументом
            argv += [f.replace("{engines}", str(ENGINES_DIR)).replace("{scripts}", str(SCRIPTS_DIR))
                     for f in shlex.split((eng.get("mode_flags") or {}).get(access, ""))]
            continue
        for k, v in values.items():
            if k in tok:
                if not v:
                    raise ValueError(f"в шаблоне {engine} нужен {k}, а значения нет")
                tok = tok.replace(k, v)
        argv.append(tok)
    launcher = [str(LAUNCHER), "--role", name or role, "--run-dir", str(run_dir), "--prompt", str(prompt), "--engine", engine]
    if task:
        launcher += ["--task", str(task)]
    if report:
        launcher += ["--report", report]
    tmo = timeout or ROLES.get(role, {}).get("timeout")
    if tmo:
        launcher += ["--timeout", str(tmo)]
    if eng.get("caller_session"):
        launcher += ["--session", session]
    command = ("PATH=\"$HOME/.local/bin:$PATH\" " if eng.get("path_prefix") else "") + \
        " ".join([shlex.quote(a) for a in launcher] + ["--"] + [shlex.quote(a) for a in argv])
    return {"role": role, "name": name or role, "engine": engine, "access": access, "launcher": launcher,
            "argv": argv, "session": session if eng.get("caller_session") else "", "command": command}


# ------------------------------------------------------------------ validate
def validate(cfg):
    errors, warnings = [], []

    def err(path, code, msg):
        errors.append({"path": path, "code": code, "message": msg})

    def warn(path, code, msg):
        warnings.append({"path": path, "code": code, "message": msg})

    if "enabled" in cfg and not isinstance(cfg["enabled"], bool):
        err("enabled", "type", "нужно true или false")
    if "fallback" in cfg and cfg["fallback"] not in FALLBACKS:
        err("fallback", "value", f"одно из {sorted(FALLBACKS)}")
    for key in cfg:
        if key not in TOP_KEYS:
            warn(key, "unknown_key", "неизвестный ключ верхнего уровня: игнорируется")
    engines = cfg.get("engines") or {}
    if not isinstance(engines, dict):
        err("engines", "type", "нужен объект")
        engines = {}
    for name, spec in engines.items():
        if not isinstance(spec, dict):
            err(f"engines.{name}", "type", "нужен объект")
            continue
        for key in spec:
            if key not in ENGINE_KEYS:
                warn(f"engines.{name}.{key}", "unknown_key", "неизвестное поле движка")
        for key in ("cmd", "resume"):
            if key in spec and not isinstance(spec[key], (str, list)):
                err(f"engines.{name}.{key}", "type", "нужна строка или список")
        if "mode_flags" in spec and not (isinstance(spec["mode_flags"], dict)
                                         and all(isinstance(v, str) for v in spec["mode_flags"].values())):
            err(f"engines.{name}.mode_flags", "type", "нужен объект {read, write} со строками")
        if name not in PRESETS and not spec.get("cmd"):
            err(f"engines.{name}", "no_cmd", "у нового движка нужен cmd")

    def check_roles(prefix, roles):
        if not isinstance(roles, dict):
            err(prefix, "type", "нужен объект")
            return
        for role, raw in roles.items():
            path = f"{prefix}.{role}"
            if not isinstance(raw, (str, dict)):
                err(path, "type", "нужна строка-движок или объект")
                continue
            spec = role_spec(raw)
            if isinstance(raw, dict):
                for key in raw:
                    if key not in ROLE_KEYS:
                        warn(f"{path}.{key}", "unknown_key", "неизвестное поле роли")
            if role in RETIRED:
                warn(path, "retired_role", "id снят: игнорируется")
                continue
            if role in FOREIGN:
                continue
            if role not in ROLES:
                warn(path, "unknown_role", "роли нет в реестре: игнорируется")
                continue
            if spec.get("sandbox") not in (None, *SANDBOX.values()):
                err(f"{path}.sandbox", "value", "read-only или workspace-write")
            access, bad = role_access(role, spec)
            if bad:
                err(f"{path}.access", "access_widen", bad)
            engine = spec.get("engine")
            if engine is None or engine == "claude":
                continue
            eng = engine_def(cfg, engine)
            if eng is None:
                err(f"{path}.engine", "unknown_engine", f"движок {engine} не известен: нужен пресет или engines.{engine}.cmd")
            elif access == "write" and not write_supported(eng):
                err(f"{path}.engine", "no_write_mode", f"роли {role} нужна запись, у движка {engine} режима записи нет")

    check_roles("roles", cfg.get("roles") or {})
    profiles = cfg.get("profiles") or {}
    if not isinstance(profiles, dict):
        err("profiles", "type", "нужен объект")
        profiles = {}
    for pname, prof in profiles.items():
        if not isinstance(prof, dict):
            err(f"profiles.{pname}", "type", "нужен объект")
            continue
        lead = prof.get("lead")
        if lead is not None and not (isinstance(lead, dict) and lead.get("host") in HOSTS):
            err(f"profiles.{pname}.lead", "value", f"нужен объект с host из {sorted(HOSTS)}")
        if "fingerprint" in prof and not str(prof["fingerprint"]).startswith("sha256:"):
            err(f"profiles.{pname}.fingerprint", "value", "ожидается sha256:…")
        check_roles(f"profiles.{pname}.roles", prof.get("roles") or {})
    active = cfg.get("active_profile")
    if active and active not in profiles:
        err("active_profile", "missing_profile", f"профиля {active} нет в profiles")
    return errors, warnings


# ------------------------------------------------------------------ CLI
def emit(data, as_json, text):
    print(json.dumps(data, ensure_ascii=False) if as_json else text)


def common(ap):
    ap.add_argument("--config", action="append", help="файл конфига вместо поиска по умолчанию (можно несколько)")
    ap.add_argument("--json", action="store_true")


def main(argv):
    top = argparse.ArgumentParser(description="Конфиг движков supertick")
    sub = top.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("host")
    p.add_argument("--host"); p.add_argument("--model"); p.add_argument("--profile"); common(p)
    p = sub.add_parser("resolve")
    p.add_argument("--host"); p.add_argument("--profile"); p.add_argument("--engines")
    p.add_argument("--available", help="бинари через запятую вместо поиска в системе"); common(p)
    p = sub.add_parser("argv")
    p.add_argument("role"); p.add_argument("--host"); p.add_argument("--profile"); p.add_argument("--name")
    p.add_argument("--run-dir", required=True); p.add_argument("--prompt", required=True)
    p.add_argument("--task", default=""); p.add_argument("--report", default=""); p.add_argument("--timeout", type=int)
    p.add_argument("--resume", nargs="?", const="auto"); p.add_argument("--available"); common(p)
    p = sub.add_parser("validate"); common(p)
    args = top.parse_args(argv)

    cfg, load_warn, used = load_config(config_paths(args.config))
    try:
        if args.cmd == "validate":
            errors, warnings = validate(cfg)
            data = {"ok": not errors, "errors": errors, "warnings": warnings, "files": used,
                    "broken": [w["detail"] for w in load_warn]}
            lines = [f"{e['path']}: {e['message']} [{e['code']}]" for e in errors] + \
                    [f"предупреждение {w['path']}: {w['message']} [{w['code']}]" for w in warnings]
            emit(data, args.json, "\n".join(lines) if lines else f"конфиг в порядке ({len(used)} файл.)")
            return 0 if not errors else 1
        host, source = detect_host(args.host)
        if args.cmd == "host":
            name, prof = active_profile(cfg, args.profile)
            lead = (prof or {}).get("lead")
            match = None
            if lead:
                match = lead.get("host") == host and (not args.model or not lead.get("model") or lead["model"] == args.model)
            data = {"host": host, "source": source, "level": HOSTS[host]["level"],
                    "profile": name if prof is not None else None, "profile_lead": lead, "lead_matches": match}
            emit(data, args.json, "\n".join(f"{k}: {v}" for k, v in data.items()))
            return 0
        available = None
        if getattr(args, "available", None) is not None:
            available = {b for b in args.available.split(",") if b}
        if args.cmd == "resolve":
            avail = available if available is not None else discover_available(cfg)
            res = resolve(cfg, host, avail, engines_off=(args.engines == "off"), profile=args.profile,
                          load_warnings=load_warn, have_config=bool(used))
            if args.json:
                print(json.dumps(res, ensure_ascii=False))
            else:
                if res["stop"]:
                    print(f"STOP: {res['reason']}")
                else:
                    print(engines_section(res))
                for line in res["feed"]:
                    print(line)
            return 1 if res["stop"] else 0
        if args.cmd == "argv":
            resume = args.resume
            if resume == "auto":
                sess = Path(args.run_dir) / "engine" / (args.name or args.role) / "session.txt"
                if not sess.is_file():
                    raise ValueError(f"нет сессии для продолжения: {sess}")
                resume = sess.read_text(encoding="utf-8").strip()
            built = build_argv(cfg, host, args.role, args.name, args.run_dir, args.prompt, task=args.task,
                               report=args.report, timeout=args.timeout, resume=resume, profile=args.profile,
                               available=available, have_config=bool(used))
            if built is None:
                emit({"native": True, "role": args.role}, args.json, f"роль {args.role} исполняет родной исполнитель: команды нет")
                return 3
            emit(built, args.json, built["command"])
            return 0
    except ValueError as exc:
        print(f"engine-config: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
