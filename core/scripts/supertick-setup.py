#!/usr/bin/env python3
"""supertick-setup.py — подключение: что установлено, диалог выбора, профиль (121-567, F5, п. 4.4 ТЗ).

Работает из обычного терминала без какого-либо хоста; только stdlib. Среда вызывает его тонкой точкой
входа (навык или команда `setup`): `discover --json`, вопросы средствами хоста, затем `apply --answers`.

  supertick-setup.py discover [--no-probe] [--probe-timeout С] [--host H] [--json]
  supertick-setup.py plan     --answers ФАЙЛ [--no-probe] [--json]   (таблица «роль → движок → модель», без записи)
  supertick-setup.py apply    --answers ФАЙЛ [--no-probe] [--force] [--json]
  supertick-setup.py setup    [--host H] [--no-probe]                (диалог в терминале, затем plan и apply)
  supertick-setup.py launch   <профиль> [--json]
  supertick-setup.py lead-probe <профиль> [--timeout С] [--root НАБОР] [--keep КАТАЛОГ] [--no-write] [--json]
                                (проба пригодности ведущего, п. 4.5: итог пишется в профиль как lead_probe)

Файл ответов (`--answers`), все ключи кроме `lead` необязательны:
  {"profile": "kimi-team",
   "lead":    {"host": "kimi", "model": "kimi-code/k3"},
   "layout":  "all-on-lead" | "split" | "groups",
   "split":   {"coder": {"engine": "codex", "model": "gpt-5.6-sol"}, "reviewer": {"engine": "cursor"}},
   "groups":  {"coders": {...}, "reviewer": {...}, "architects": {...}, "researchers": {...}, "verifiers": {...}},
   "second_opinion": {"engine": "cursor", "model": "…"} | "off",
   "language": "ru" | "en",   (121-604: язык ленты и отчётов, пишется в профиль)
   "activate": true}
Группа или роль без значения (null) остаётся на родном исполнителе хоста.

Пишется только профиль в `$SUPERTICK_CONFIG` или `~/.config/supertick/config.json` — атомарно, с копией
прежнего файла. Прежний конфиг 1.1.x не читается на запись и не меняется никогда.

Коды выхода: 0 готово; 1 есть ошибки проверки, apply не выполнен; 2 неверный вызов или ответы.
"""
import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
CATALOG = SCRIPTS_DIR.parent / "references" / "models-catalog.json"

_spec = importlib.util.spec_from_file_location("engine_config", SCRIPTS_DIR / "engine-config.py")
ec = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ec)
_spec = importlib.util.spec_from_file_location("lead_probe", SCRIPTS_DIR / "lead-probe.py")
lp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lp)
T = ec.T   # текст для человека на языке ec.OUTPUT_LANG (121-604)

# CLI → (движок в конфиге, хост, которому он соответствует). hosts — только среды, где может жить ведущий.
CLIS = {
    "claude":       {"engine": "claude",   "host": "claude-code"},
    "kimi":         {"engine": "kimi",     "host": "kimi"},
    "opencode":     {"engine": "opencode", "host": "opencode"},
    "codex":        {"engine": "codex",    "host": "codex"},
    "cursor-agent": {"engine": "cursor",   "host": None},
    "grok":         {"engine": "grok",     "host": None},
}
# Годится ли ведущим по итогам спайков S0: True — проверено, None — ещё не проверено, False — нет.
LEAD_CAPABLE = {"claude-code": True, "kimi": True, "codex": True, "opencode": True}

PROBE_PROMPT = "Ответь одним словом: ok"
PROBE_ARGV = {
    "claude":       ["env", "-u", ec.HOST_MARKER_ENV, "{bin}", "-p", "--disable-slash-commands", "--strict-mcp-config",
                     "--setting-sources", "project,local", "--", PROBE_PROMPT],
    "kimi":         ["{bin}", "-p", PROBE_PROMPT],
    "opencode":     ["{bin}", "run", PROBE_PROMPT],
    "codex":        ["{bin}", "exec", "--skip-git-repo-check", "--sandbox", "read-only", "--", PROBE_PROMPT],
    "cursor-agent": ["{bin}", "-p", "--trust", "--mode", "ask", "--", PROBE_PROMPT],
    "grok":         ["{bin}", "--sandbox", "read-only", "-p", PROBE_PROMPT],
}
LOGIN_HINT = re.compile(r"log ?in|sign ?in|not authenticated|unauthori[sz]ed|\b401\b|api[ _-]?key|credentials|"
                        r"войд|авториз", re.I)

# Группы ролей для раскладки «по группам» (п. 4.4, вопрос 2).
GROUPS = {
    "coders":      ["coder"],
    "reviewer":    ["unified-reviewer"],
    "architects":  ["architect", "architect-frontend", "architect-backend", "architect-systems", "tech-lead"],
    "researchers": ["codebase-researcher", "reference-researcher", "web-researcher", "legacy-scanner"],
    "verifiers":   ["spec-verifier", "ci-verifier", "risk-tester", "acceptance-checker"],
}
class SetupError(Exception):
    pass


# ------------------------------------------------------------------ поиск подключённого
def find_binary(name):
    found = shutil.which(name)
    if found:
        return found
    local = Path.home() / ".local" / "bin" / name
    return str(local) if local.exists() else None


def cli_version(path):
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10)
        text = (out.stdout or out.stderr).strip()
        return text.splitlines()[0][:80] if text else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def catalog_models(cli):
    try:
        data = json.loads(CATALOG.read_text(encoding="utf-8"))
        return list((data.get("clis") or {}).get(cli) or [])
    except (OSError, ValueError):
        return []


def models_of(cli):
    """(модели, источник): конфиг самого CLI, а если его нет — встроенный каталог."""
    home = Path.home()
    if cli == "kimi":
        cfg = home / ".kimi-code" / "config.toml"
        try:
            text = cfg.read_text(encoding="utf-8")
            names = re.findall(r'^\[models\."([^"]+)"\]', text, re.M)
            default = re.search(r'^default_model\s*=\s*"([^"]+)"', text, re.M)
            if default and default.group(1) in names:
                names.remove(default.group(1))
                names.insert(0, default.group(1))
            if names:
                return names, "cli-config"
        except OSError:
            pass
    if cli == "codex":
        try:
            data = json.loads((home / ".codex" / "models_cache.json").read_text(encoding="utf-8"))
            names = [m["slug"] for m in data.get("models", []) if m.get("visibility") != "hide" and m.get("slug")]
            if names:
                return names, "cli-config"
        except (OSError, ValueError, KeyError, TypeError):
            pass
    if cli == "opencode":   # каталог провайдеров opencode (Zen — 88 моделей 14.11.2026); бесплатные первыми
        path = find_binary("opencode")
        try:
            out = subprocess.run([path, "models"], capture_output=True, text=True, timeout=30,
                                 stdin=subprocess.DEVNULL) if path else None
        except (OSError, subprocess.SubprocessError):
            out = None
        if out and out.returncode == 0:
            names = [l.strip() for l in out.stdout.splitlines() if re.fullmatch(r"[\w.-]+/[\w.:/-]+", l.strip())]
            if names:
                return sorted(names, key=lambda n: not n.endswith("-free")), "cli-config"
    names = catalog_models(cli)
    preset = ec.PRESETS.get(CLIS[cli]["engine"], {}).get("model")
    if preset and preset not in names:
        names.insert(0, preset)
    return names, "catalog"


def probe_cli(cli, path, timeout):
    """Один короткий headless-вызов: responds | not_logged_in | no_answer | error."""
    argv = [path if a == "{bin}" else a for a in PROBE_ARGV[cli]]
    try:
        res = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return "no_answer", T(f"нет ответа за {timeout} с", f"no answer in {timeout} s")
    except OSError as exc:
        return "error", str(exc)
    out = (res.stdout or "").strip()
    err = (res.stderr or "").strip()
    if res.returncode == 0 and out:
        return "responds", ""
    blob = out + "\n" + err
    if LOGIN_HINT.search(blob):
        return "not_logged_in", blob.strip().splitlines()[0][:120] if blob.strip() else ""
    return "error", (err or out or T(f"код {res.returncode}", f"exit code {res.returncode}")).splitlines()[0][:120]


def discover(probe=True, probe_timeout=90, current_host=None):
    found = {cli: find_binary(cli) for cli in CLIS}
    probes = {}
    todo = {c: p for c, p in found.items() if p}
    if probe and todo:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(todo)) as pool:
            futures = {c: pool.submit(probe_cli, c, p, probe_timeout) for c, p in todo.items()}
            probes = {c: f.result() for c, f in futures.items()}
    entries = []
    for cli, meta in CLIS.items():
        path = found[cli]
        models, source = models_of(cli) if path else ([], "")
        if not path:
            state, detail = "not_installed", ""
        elif probe:
            state, detail = probes[cli]
        else:
            state, detail = "unchecked", ""
        host = meta["host"]
        engine = meta["engine"]
        entries.append({
            "cli": cli, "engine": engine, "installed": bool(path), "path": path or "",
            "version": cli_version(path) if path else "", "state": state, "detail": detail,
            "models": models, "models_source": source,
            "engine_preset": engine in ec.PRESETS,
            "host": host, "lead_capable": LEAD_CAPABLE.get(host, False) if host else False,
        })
    usable = sorted(e["cli"] for e in entries if e["state"] == "responds" or (not probe and e["installed"]))
    return {"clis": entries, "sources": usable, "fingerprint": fingerprint(usable), "probed": probe,
            "current_host": current_host}


def fingerprint(sources):
    return "sha256:" + hashlib.sha256("\n".join(sorted(sources)).encode()).hexdigest()


def discover_text(d):
    lines = []
    for e in d["clis"]:
        if not e["installed"]:
            lines.append(f"{e['cli']}: " + T("не установлен", "not installed"))
            continue
        label = {"responds": T("отвечает", "responds"), "not_logged_in": T("не выполнен вход", "not logged in"),
                 "no_answer": T("не ответил", "no answer"), "error": T("ошибка пробы", "probe error"),
                 "unchecked": T("не проверен", "not checked")}[e["state"]]
        lead = {True: T("может быть ведущим", "can lead"), None: T("ведущим не проверен", "not checked as lead"),
                False: ""}[e["lead_capable"]]
        extra = ", ".join(x for x in (e["version"], lead, e["detail"]) if x)
        lines.append(f"{e['cli']}: {label}" + (f" ({extra})" if extra else "")
                     + T(f"; моделей {len(e['models'])}", f"; models {len(e['models'])}"))
    lines.append(T("источники: ", "sources: ") + (", ".join(d["sources"]) or T("нет", "none")))
    for p in d.get("lead_probes") or []:
        lines.append(T("проба ведущего ", "lead probe ") + p["host"] + (f" / {p['model']}" if p["model"] else "")
                     + T(f" (профиль {p['profile']}): ", f" (profile {p['profile']}): ") + f"{p['result']} {p['date']}")
    return "\n".join(lines)


# ------------------------------------------------------------------ проба ведущего
def lead_probes(cfg):
    """Итоги проб ведущего из профилей конфига: [{profile, host, model, result, date, version}]."""
    out = []
    for name, prof in sorted(((cfg or {}).get("profiles") or {}).items()):
        if not isinstance(prof, dict) or not isinstance(prof.get("lead_probe"), dict):
            continue
        lead, probe = prof.get("lead") or {}, prof["lead_probe"]
        out.append({"profile": name, "host": lead.get("host", ""), "model": lead.get("model", ""),
                    "result": probe.get("result", ""), "date": probe.get("date", ""),
                    "version": probe.get("version", "")})
    return out


def probe_for(cfg, host, model):
    """Свежайший итог пробы для пары «среда, модель» или None."""
    found = [p for p in lead_probes(cfg) if p["host"] == host and p["model"] == (model or "")]
    return max(found, key=lambda p: p["date"]) if found else None


def probe_mark(cfg, host, model):
    """Пометка ведущего для диалога (п. 4.4, вопрос 1)."""
    if host == "claude-code":
        return T("свой протокол 1.1.x", "own 1.1.x protocol")
    p = probe_for(cfg, host, model)
    if not p:
        return T("не проверена как ведущий", "not checked as lead")
    if p["result"] == "passed":
        return T(f"проверена как ведущий {p['date']}", f"checked as lead {p['date']}")
    return T(f"проба ведущего {p['date']}: {p['result']}", f"lead probe {p['date']}: {p['result']}")


def run_lead_probe(cfg, name, timeout=1800, root=None, keep=None):
    prof = (cfg.get("profiles") or {}).get(name)
    if not isinstance(prof, dict) or not isinstance(prof.get("lead"), dict):
        raise SetupError(T(f"профиль {name} не найден или без ведущего", f"profile {name} not found or has no lead"))
    lead = prof["lead"]
    try:
        r = lp.probe(lead.get("host", ""), lead.get("model"), root=root, timeout=timeout, keep=keep, base_profile=prof)
    except lp.ProbeError as exc:
        raise SetupError(str(exc))
    stored = {"result": r["result"], "date": r["date"], "version": r["version"], "model": r["model"],
              "seconds": r["seconds"]}
    if r["failed"]:
        stored["detail"] = r["checks"][r["failed"]]["detail"]
    if r.get("notes"):
        stored["notes"] = r["notes"]
    return r, stored


# ------------------------------------------------------------------ ответы → профиль
def usable_engines(d):
    """Движки, которые можно назначить ролям: отвечают (или не пробовались) и имеют пресет."""
    ok = {}
    for e in d["clis"]:
        if e["installed"] and e["engine_preset"] and e["state"] in ("responds", "unchecked"):
            ok[e["engine"]] = e
    return ok


def default_model(engine, d):
    entry = next((e for e in d["clis"] if e["engine"] == engine), None)
    if entry and entry["models"]:
        return entry["models"][0]
    return ec.PRESETS.get(engine, {}).get("model", "")


def role_assignment(value, d, where):
    if value in (None, "native"):
        return None
    if isinstance(value, str):
        value = {"engine": value}
    if not isinstance(value, dict) or not value.get("engine"):
        raise SetupError(T(f"{where}: нужен объект с engine или null", f"{where}: expected an object with engine, or null"))
    spec = {"engine": value["engine"], "model": value.get("model") or default_model(value["engine"], d)}
    for key in ("effort", "access"):
        if value.get(key):
            spec[key] = value[key]
    return spec


def build_profile(answers, d):
    lead = answers.get("lead")
    if not isinstance(lead, dict) or lead.get("host") not in ec.HOSTS:
        raise SetupError(T(f"lead: нужен объект с host из {sorted(ec.HOSTS)}", f"lead: expected an object with host from {sorted(ec.HOSTS)}"))
    language = answers.get("language")
    if language is not None and ec.norm_language(language) is None:
        raise SetupError(T(f"language: ru или en, получено {language!r}", f"language: ru or en, got {language!r}"))
    lead_spec = {"host": lead["host"]}
    if lead.get("model"):
        lead_spec["model"] = lead["model"]
    layout = answers.get("layout") or "all-on-lead"
    roles = {}
    if layout == "all-on-lead":
        pass
    elif layout == "split":
        if len(usable_engines(d)) < 2:
            raise SetupError(T("раскладка split доступна при двух и более источниках", "layout split needs two or more sources"))
        split = answers.get("split") or {}
        for key, group in (("coder", "coders"), ("reviewer", "reviewer")):
            spec = role_assignment(split.get(key), d, f"split.{key}")
            if spec:
                for role in GROUPS[group]:
                    roles[role] = dict(spec)
    elif layout == "groups":
        groups = answers.get("groups") or {}
        for name in groups:
            if name not in GROUPS:
                raise SetupError(T(f"groups.{name}: неизвестная группа, есть {sorted(GROUPS)}", f"groups.{name}: unknown group, known {sorted(GROUPS)}"))
        for name, members in GROUPS.items():
            spec = role_assignment(groups.get(name), d, f"groups.{name}")
            if spec:
                for role in members:
                    roles[role] = dict(spec)
    else:
        raise SetupError(T(f"layout: all-on-lead, split или groups, получено {layout!r}", f"layout: all-on-lead, split or groups, got {layout!r}"))
    second = answers.get("second_opinion", "off")
    if second not in ("off", None):
        if len(usable_engines(d)) < 2:
            raise SetupError(T("второе мнение нужно при двух и более источниках: при одном вопрос не задаётся",
                               "a second opinion needs two or more sources: with one the question is not asked"))
        spec = role_assignment(second, d, "second_opinion")
        if spec:
            roles["second-reviewer"] = spec
    # Профиль полный: каждая роль без назначения записана null («родной исполнитель среды»). Иначе роли
    # из прежнего конфига 1.1.x (профиль накладывается поверх них) исполнялись бы не так, как показал диалог.
    host = lead_spec["host"]
    for role, reg in ec.ROLES.items():
        if role == "lead" or role in roles or reg.get("only_on") and host not in reg["only_on"]:
            continue
        if role == "web-researcher" and host != "claude-code":
            continue   # вне Claude Code поиск только у движка с живым поиском — родного варианта нет
        roles[role] = None
    profile = {"lead": lead_spec, "roles": roles, "confirmed_at": date.today().isoformat(),
               "fingerprint": d["fingerprint"],
               "sources": d["sources"], "installed": sorted(e["cli"] for e in d["clis"] if e["installed"])}
    if language is not None:
        profile["language"] = ec.norm_language(language)   # язык ленты и отчётов (121-604)
    return answers.get("profile") or "default", profile


def target_path(env=None):
    env = os.environ if env is None else env
    if env.get("SUPERTICK_CONFIG"):
        return Path(env["SUPERTICK_CONFIG"]).expanduser()
    return Path(ec.XDG_CONFIG).expanduser()


def read_target(path):
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise SetupError(T(f"{path} не читается ({exc}): исправьте или удалите файл, затем повторите",
                             f"{path} is unreadable ({exc}): fix or remove the file, then retry"))
    if not isinstance(data, dict):
        raise SetupError(T(f"{path}: корень не объект", f"{path}: root is not an object"))
    return data


def candidate_config(existing, name, profile, activate):
    cfg = json.loads(json.dumps(existing))
    old = ((cfg.get("profiles") or {}).get(name) or {})
    if isinstance(old, dict) and old.get("lead") == profile["lead"] and old.get("lead_probe") \
            and "lead_probe" not in profile:
        profile = dict(profile, lead_probe=old["lead_probe"])   # тот же ведущий — итог пробы остаётся в силе
    cfg.setdefault("profiles", {})[name] = profile
    cfg.setdefault("fallback", "host")
    if activate:
        cfg["active_profile"] = name
    return cfg


def plan_rows(cfg, name, host, res):
    """Таблица «роль → движок → модель → доступ» — то, что исполнит прогон: из того же resolve, что шаг 0b."""
    roles = ec.effective_roles(cfg, cfg["profiles"][name])
    native = ec.HOSTS[host]["native"]
    rows = []
    for role in ec.ROLES:
        if role == "lead" or ec.ROLES[role].get("only_on") and host not in ec.ROLES[role]["only_on"]:
            continue
        detail = (res.get("detail") or {}).get(role) or {"engine": native, "model": ""}
        if detail["engine"] == "none":
            continue
        access, _ = ec.role_access(role, roles.get(role) or {})
        rows.append({"role": role, "engine": detail["engine"], "model": detail.get("model", ""),
                     "access": T("запись", "write") if access == "write" else T("чтение", "read"), "native": detail["engine"] == native})
    return rows


def runtime_config(cfg):
    """Конфиг так, как его прочтёт прогон: профиль поверх ролей прежнего конфига 1.1.x (LEGACY_CONFIG)."""
    legacy, _, _ = ec.load_config([Path(ec.LEGACY_CONFIG).expanduser()])
    return ec.merge(cfg, legacy)


def build_plan(answers, d, existing):
    name, profile = build_profile(answers, d)
    activate = answers.get("activate", True)
    cfg = candidate_config(existing, name, profile, activate)
    errors, warnings = ec.validate(cfg)
    problems = [e["message"] + f" [{e['path']}]" for e in errors]
    # назначенный движок должен быть подключён
    ready = usable_engines(d)
    for role, spec in profile["roles"].items():
        if spec and spec["engine"] not in ready:
            problems.append(T(f"роль {role}: движок {spec['engine']} не подключён (нет CLI, входа или пресета)",
                              f"role {role}: engine {spec['engine']} is not connected (no CLI, login or preset)"))
    host = profile["lead"]["host"]
    avail = {e["cli"] for e in d["clis"] if e["installed"]}
    avail |= {ec.PRESETS[e["engine"]]["binary"] for e in d["clis"] if e["installed"] and e["engine_preset"]}
    res = ec.resolve(runtime_config(cfg), host, avail, profile=name, have_config=True)
    notes = list(res["feed"])
    entry = next((e for e in d["clis"] if e["host"] == host), None)
    if entry is None or not entry["installed"]:
        problems.append(T(f"ведущий: среда {host} не установлена", f"lead: host {host} is not installed"))
    elif entry["state"] not in ("responds", "unchecked"):
        problems.append(T(f"ведущий: {entry['cli']} не готов ({entry['state']})", f"lead: {entry['cli']} is not ready ({entry['state']})"))
    if entry and entry["lead_capable"] is None:
        notes.append(T(f"⚙️ ведущий {host} не проверен как ведущий (спайк S0 не проведён)",
                       f"⚙️ lead {host} is not checked as lead (spike S0 not done)"))
    elif not LEAD_CAPABLE.get(host):
        problems.append(T(f"ведущий: {host} не годится в ведущие", f"lead: {host} cannot lead"))
    if entry and entry["models"] and profile["lead"].get("model") and profile["lead"]["model"] not in entry["models"]:
        notes.append(T(f"⚙️ модель ведущего {profile['lead']['model']} не из списка {entry['cli']} (проверьте имя)",
                       f"⚙️ lead model {profile['lead']['model']} is not in the {entry['cli']} list (check the name)"))
    if host != "claude-code" and LEAD_CAPABLE.get(host):
        p = probe_for(cfg, host, profile["lead"].get("model"))
        if not p:
            notes.append(T(f"⚙️ ведущий не проверен пробой: supertick-setup.py lead-probe {name} (несколько минут вызовов модели)",
                           f"⚙️ lead not probed: supertick-setup.py lead-probe {name} (a few minutes of model calls)"))
        elif p["result"] != "passed":
            notes.append(T(f"⚙️ проба ведущего {p['date']}: {p['result']} — модель может не удержать протокол "
                           "(проба ничего не запрещает)",
                           f"⚙️ lead probe {p['date']}: {p['result']} — the model may not hold the protocol "
                           "(the probe forbids nothing)"))
    return {"profile": name, "activate": activate, "lead": profile["lead"], "rows": plan_rows(runtime_config(cfg), name, host, res),
            "problems": problems, "notes": notes, "warnings": [w["message"] + f" [{w['path']}]" for w in warnings],
            "launch": launch_command(profile["lead"]), "config": cfg, "profile_body": profile}


def plan_text(plan):
    lines = [T(f"Профиль {plan['profile']}: ведущий ", f"Profile {plan['profile']}: lead ") + plan["lead"]["host"]
             + (f" / {plan['lead']['model']}" if plan["lead"].get("model") else "")]
    if plan["profile_body"].get("language"):
        lines.append(T("  язык ленты и отчётов: ", "  feed and report language: ") + plan["profile_body"]["language"])
    lines += [f"  {r['role']:<22} {r['engine']:<10} {r['model'] or '—':<28} {r['access']}" for r in plan["rows"]]
    lines += plan["notes"] + [T("предупреждение: ", "warning: ") + w for w in plan["warnings"]]
    lines += [T("ОШИБКА: ", "ERROR: ") + p for p in plan["problems"]]
    return "\n".join(lines)


launch_command = ec.launch_command


def write_atomic(path, cfg):
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = ""
    if path.exists():
        backup = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(path, backup)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return backup


# ------------------------------------------------------------------ диалог в терминале
def choose(ask, question, options, other=True):
    """options — список (подпись, значение); не более четырёх, остальное через «Другое»."""
    options = options[:4]
    lines = [question] + [f"  {i}. {label}" for i, (label, _) in enumerate(options, 1)]
    if other:
        lines.append(T("  0. Другое", "  0. Other"))
    answer = ask("\n".join(lines) + "\n> ").strip()
    if answer.isdigit() and 1 <= int(answer) <= len(options):
        return options[int(answer) - 1][1]
    if other and answer == "0":
        return ("other", ask(T("Введите значение: ", "Enter a value: ")).strip())
    raise SetupError(T(f"нужен номер от {0 if other else 1} до {len(options)}", f"expected a number from {0 if other else 1} to {len(options)}"))


def pick_engine(ask, d, question):
    ready = usable_engines(d)
    opts = [(f"{n}: {default_model(n, d)}", {"engine": n, "model": default_model(n, d)}) for n in ready]
    opts.append((T("родной исполнитель среды", "the host's native executor"), None))
    got = choose(ask, question, opts, other=False)
    return got


def ask_language(ask):
    """Первый вопрос диалога (121-604), задаётся на двух языках; ответ сразу переключает язык диалога."""
    suggested = ec.OUTPUT_LANG
    opts = [("русский", "ru"), ("English", "en")]
    opts.sort(key=lambda o: o[1] != suggested)   # предложенный по локали и конфигу — первым
    lang = choose(ask, "Язык ленты и отчётов / Feed and report language:", opts, other=False)
    ec.OUTPUT_LANG = lang
    return lang


def dialog(d, ask, say, current_host=None, cfg=None):
    language = ask_language(ask)
    leads = [e for e in d["clis"] if e["host"] and e["lead_capable"] is not False and e["state"] in ("responds", "unchecked")]
    if not leads:
        raise SetupError(T("нет ни одного CLI, который может быть ведущим: установите и войдите хотя бы в один",
                             "no CLI can lead: install and log in to at least one"))
    opts = []
    for e in leads:
        model = e["models"][0] if e["models"] else ""
        mark = probe_mark(cfg, e["host"], model) if e["lead_capable"] else T("среда не проверена спайком", "host not checked by a spike")
        here = T(", текущий сеанс", ", current session") if e["host"] == current_host else T(", нужен перезапуск", ", restart needed")
        opts.append((f"{e['host']} / {model or T('по умолчанию', 'default')} ({mark}{here})", {"host": e["host"], "model": model}))
    lead = choose(ask, T("Кто ведущий?", "Who leads?"), opts)
    if isinstance(lead, tuple):
        host, _, model = lead[1].partition("/")
        lead = {"host": host.strip(), "model": model.strip()}
    answers = {"language": language, "lead": lead,
               "profile": ask(T("Имя профиля [default]: ", "Profile name [default]: ")).strip() or "default", "activate": True}
    sources = usable_engines(d)
    layouts = [(T("всё на модели ведущего", "everything on the lead's model"), "all-on-lead")]
    if len(sources) >= 2:
        layouts.append((T("код и ревью на разных моделях", "code and review on different models"), "split"))
    layouts.append((T("по группам ролей", "by role groups"), "groups"))
    layout = choose(ask, T("Состав команды?", "Team layout?"), layouts, other=False)
    answers["layout"] = layout
    if layout == "split":
        answers["split"] = {"coder": pick_engine(ask, d, T("Кто пишет код?", "Who writes code?")),
                            "reviewer": pick_engine(ask, d, T("Кто рецензирует?", "Who reviews?"))}
    elif layout == "groups":
        titles = {"coders": T("кодеры", "coders"), "reviewer": T("рецензент", "reviewer"),
                  "architects": T("архитекторы и техлид", "architects and tech lead"),
                  "researchers": T("исследователи", "researchers"), "verifiers": T("проверяющие", "verifiers")}
        answers["groups"] = {g: pick_engine(ask, d, T(f"Кто исполняет группу «{titles[g]}»?", f"Who runs the group “{titles[g]}”?"))
                             for g in GROUPS}
    if len(sources) >= 2:
        second = pick_engine(ask, d, T("Второе мнение (другая модель)?", "Second opinion (another model)?"))
        answers["second_opinion"] = second or "off"
    return answers


# ------------------------------------------------------------------ команды
def load_answers(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SetupError(T(f"ответы не читаются: {exc}", f"answers are unreadable: {exc}"))
    if not isinstance(data, dict):
        raise SetupError(T("ответы: корень не объект", "answers: root is not an object"))
    return data


def emit(data, as_json, text):
    print(json.dumps(data, ensure_ascii=False) if as_json else text)


def public(plan):
    return {k: v for k, v in plan.items() if k not in ("config", "profile_body")}


def main(argv):
    top = argparse.ArgumentParser(description="Подключение supertick: поиск CLI, диалог, профиль")
    sub = top.add_subparsers(dest="cmd", required=True)
    for name in ("discover", "plan", "apply", "setup", "launch", "lead-probe"):
        p = sub.add_parser(name)
        p.add_argument("--json", action="store_true")
        if name == "lead-probe":
            p.add_argument("profile")
            p.add_argument("--timeout", type=int, default=1800, help="сколько ждать ведущего, с")
            p.add_argument("--root", help="корень набора (по умолчанию $SUPERTICK_ROOT или установленный для среды)")
            p.add_argument("--keep", help="оставить стенд в этом пустом каталоге")
            p.add_argument("--no-write", action="store_true", help="не записывать итог в профиль")
            continue
        if name != "launch":
            p.add_argument("--no-probe", action="store_true", help="не вызывать CLI: считать установленные доступными")
            p.add_argument("--probe-timeout", type=int, default=90)
            p.add_argument("--host", help="среда текущего сеанса (для пометок диалога)")
        if name in ("plan", "apply"):
            p.add_argument("--answers", required=True)
        if name == "apply":
            p.add_argument("--force", action="store_true", help="записать профиль, даже если есть ошибки проверки")
        if name == "launch":
            p.add_argument("profile")
        p.add_argument("--lang", help="язык вывода: ru | en (по умолчанию SUPERTICK_LANG, профиль, конфиг, локаль, en)")
    args = top.parse_args(argv)
    try:
        try:
            lang_cfg, _, _ = ec.load_config(ec.config_paths())
        except Exception:
            lang_cfg = {}
        ec.OUTPUT_LANG = ec.resolve_language(lang_cfg, flag=getattr(args, "lang", None))["lang"]
        if args.cmd == "launch":
            cfg, _, _ = ec.load_config(ec.config_paths())
            prof = (cfg.get("profiles") or {}).get(args.profile)
            if not isinstance(prof, dict) or not prof.get("lead"):
                raise SetupError(T(f"профиль {args.profile} не найден или без ведущего", f"profile {args.profile} not found or has no lead"))
            cmd = launch_command(prof["lead"])
            emit({"profile": args.profile, "lead": prof["lead"], "command": cmd}, args.json, cmd or
                 T(f"для хоста {prof['lead']['host']} команда запуска не известна", f"no launch command known for host {prof['lead']['host']}"))
            return 0
        if args.cmd == "lead-probe":
            path = target_path()
            existing = read_target(path)
            cfg, _, _ = ec.load_config(ec.config_paths())
            r, stored = run_lead_probe(cfg, args.profile, args.timeout, args.root, args.keep)
            written = ""
            if not args.no_write:
                if args.profile not in (existing.get("profiles") or {}):
                    raise SetupError(T(f"профиль {args.profile} не в {path}: итог не записан (--no-write, чтобы только посмотреть)",
                                      f"profile {args.profile} is not in {path}: result not written (--no-write to just look)"))
                new = json.loads(json.dumps(existing))
                new["profiles"][args.profile]["lead_probe"] = stored
                write_atomic(path, new)
                written = str(path)
            emit(dict(r, profile=args.profile, written=written), args.json,
                 lp.text(r) + (T(f"\nзаписано в профиль {args.profile}: {path}", f"\nwritten to profile {args.profile}: {path}") if written else ""))
            return 0 if r["result"] == "passed" else 1
        current = None
        try:
            current = ec.detect_host(args.host)[0]
        except ValueError:
            pass
        d = discover(probe=not args.no_probe, probe_timeout=args.probe_timeout, current_host=current)
        try:
            known, _, _ = ec.load_config(ec.config_paths())
        except Exception:   # конфиг пользователя не мешает поиску CLI
            known = {}
        d["lead_probes"] = lead_probes(known)
        if args.cmd == "discover":
            emit(d, args.json, discover_text(d))
            return 0
        path = target_path()
        existing = read_target(path)
        if args.cmd == "setup":
            if not sys.stdin.isatty():
                raise SetupError(T("диалогу нужен терминал; без него — `apply --answers ФАЙЛ`", "the dialog needs a terminal; without one use `apply --answers FILE`"))
            print(discover_text(d))
            answers = dialog(d, input, print, current, existing)
        else:
            answers = load_answers(args.answers)
        plan = build_plan(answers, d, existing)
        if args.cmd == "plan" or (args.cmd == "setup" and plan["problems"]):
            emit(public(plan), args.json, plan_text(plan))
            return 1 if plan["problems"] else 0
        if args.cmd == "setup":
            print(plan_text(plan))
            if input(T("Записать профиль? [y/N] ", "Write the profile? [y/N] ")).strip().lower() not in ("y", "д", "yes", "да"):
                print(T("отменено", "cancelled"))
                return 0
        if plan["problems"] and not getattr(args, "force", False):
            emit(public(plan), args.json, plan_text(plan))
            return 1
        backup = write_atomic(path, plan["config"])
        result = dict(public(plan), written=str(path), backup=backup)
        emit(result, args.json, plan_text(plan) + T("\nзаписано: ", "\nwritten: ") + str(path)
             + (T(f" (копия {backup})", f" (backup {backup})") if backup else "")
             + (T("\nзапуск: ", "\nlaunch: ") + plan["launch"] if plan["launch"] else ""))
        host = plan["lead"]["host"]
        if args.cmd == "setup" and host in lp.HOST_ARGV and not probe_for(plan["config"], host, plan["lead"].get("model")):
            if input(T("Проверить ведущего пробой (несколько минут вызовов модели)? [y/N] ",
                       "Probe the lead (a few minutes of model calls)? [y/N] ")).strip().lower() in ("y", "д", "yes", "да"):
                r, stored = run_lead_probe(plan["config"], plan["profile"])
                cfg = read_target(path)
                cfg["profiles"][plan["profile"]]["lead_probe"] = stored
                write_atomic(path, cfg)
                print(lp.text(r))
        return 0
    except SetupError as exc:
        print(f"supertick-setup: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
