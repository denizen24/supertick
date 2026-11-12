#!/usr/bin/env python3
"""supertick-setup.py — подключение: что установлено, диалог выбора, профиль (121-567, F5, п. 4.4 ТЗ).

Работает из обычного терминала без какого-либо хоста; только stdlib. Среда вызывает его тонкой точкой
входа (навык или команда `setup`): `discover --json`, вопросы средствами хоста, затем `apply --answers`.

  supertick-setup.py discover [--no-probe] [--probe-timeout С] [--host H] [--json]
  supertick-setup.py plan     --answers ФАЙЛ [--no-probe] [--json]   (таблица «роль → движок → модель», без записи)
  supertick-setup.py apply    --answers ФАЙЛ [--no-probe] [--force] [--json]
  supertick-setup.py setup    [--host H] [--no-probe]                (диалог в терминале, затем plan и apply)
  supertick-setup.py launch   <профиль> [--json]

Файл ответов (`--answers`), все ключи кроме `lead` необязательны:
  {"profile": "kimi-team",
   "lead":    {"host": "kimi", "model": "kimi-code/k3"},
   "layout":  "all-on-lead" | "split" | "groups",
   "split":   {"coder": {"engine": "codex", "model": "gpt-5.6-sol"}, "reviewer": {"engine": "cursor"}},
   "groups":  {"coders": {...}, "reviewer": {...}, "architects": {...}, "researchers": {...}, "verifiers": {...}},
   "second_opinion": {"engine": "cursor", "model": "…"} | "off",
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
LEAD_CAPABLE = {"claude-code": True, "kimi": True, "codex": True, "opencode": None}

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
        return "no_answer", f"нет ответа за {timeout} с"
    except OSError as exc:
        return "error", str(exc)
    out = (res.stdout or "").strip()
    err = (res.stderr or "").strip()
    if res.returncode == 0 and out:
        return "responds", ""
    blob = out + "\n" + err
    if LOGIN_HINT.search(blob):
        return "not_logged_in", blob.strip().splitlines()[0][:120] if blob.strip() else ""
    return "error", (err or out or f"код {res.returncode}").splitlines()[0][:120]


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
            lines.append(f"{e['cli']}: не установлен")
            continue
        label = {"responds": "отвечает", "not_logged_in": "не выполнен вход", "no_answer": "не ответил",
                 "error": "ошибка пробы", "unchecked": "не проверен"}[e["state"]]
        lead = {True: "может быть ведущим", None: "ведущим не проверен", False: ""}[e["lead_capable"]]
        extra = ", ".join(x for x in (e["version"], lead, e["detail"]) if x)
        lines.append(f"{e['cli']}: {label}" + (f" ({extra})" if extra else "") + f"; моделей {len(e['models'])}")
    lines.append("источники: " + (", ".join(d["sources"]) or "нет"))
    return "\n".join(lines)


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
        raise SetupError(f"{where}: нужен объект с engine или null")
    spec = {"engine": value["engine"], "model": value.get("model") or default_model(value["engine"], d)}
    for key in ("effort", "access"):
        if value.get(key):
            spec[key] = value[key]
    return spec


def build_profile(answers, d):
    lead = answers.get("lead")
    if not isinstance(lead, dict) or lead.get("host") not in ec.HOSTS:
        raise SetupError(f"lead: нужен объект с host из {sorted(ec.HOSTS)}")
    lead_spec = {"host": lead["host"]}
    if lead.get("model"):
        lead_spec["model"] = lead["model"]
    layout = answers.get("layout") or "all-on-lead"
    roles = {}
    if layout == "all-on-lead":
        pass
    elif layout == "split":
        if len(usable_engines(d)) < 2:
            raise SetupError("раскладка split доступна при двух и более источниках")
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
                raise SetupError(f"groups.{name}: неизвестная группа, есть {sorted(GROUPS)}")
        for name, members in GROUPS.items():
            spec = role_assignment(groups.get(name), d, f"groups.{name}")
            if spec:
                for role in members:
                    roles[role] = dict(spec)
    else:
        raise SetupError(f"layout: all-on-lead, split или groups, получено {layout!r}")
    second = answers.get("second_opinion", "off")
    if second not in ("off", None):
        if len(usable_engines(d)) < 2:
            raise SetupError("второе мнение нужно при двух и более источниках: при одном вопрос не задаётся")
        spec = role_assignment(second, d, "second_opinion")
        if spec:
            roles["second-reviewer"] = spec
    profile = {"lead": lead_spec, "roles": roles, "confirmed_at": date.today().isoformat(),
               "fingerprint": d["fingerprint"],
               "sources": d["sources"], "installed": sorted(e["cli"] for e in d["clis"] if e["installed"])}
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
        raise SetupError(f"{path} не читается ({exc}): исправьте или удалите файл, затем повторите")
    if not isinstance(data, dict):
        raise SetupError(f"{path}: корень не объект")
    return data


def candidate_config(existing, name, profile, activate):
    cfg = json.loads(json.dumps(existing))
    cfg.setdefault("profiles", {})[name] = profile
    cfg.setdefault("fallback", "host")
    if activate:
        cfg["active_profile"] = name
    return cfg


def plan_rows(cfg, name, host):
    """Таблица «роль → движок → модель → доступ» активного состояния профиля."""
    prof = cfg["profiles"][name]
    roles = ec.effective_roles(cfg, prof)
    rows = []
    for role in ec.ROLES:
        if role in ("lead", "browser-verifier", "acceptance-checker") and role not in roles:
            continue
        spec = roles.get(role, {})
        engine = spec.get("engine")
        access, _ = ec.role_access(role, spec)
        rows.append({"role": role, "engine": engine or ec.HOSTS[host]["native"],
                     "model": spec.get("model", ""), "access": "запись" if access == "write" else "чтение",
                     "native": engine is None})
    return rows


def build_plan(answers, d, existing):
    name, profile = build_profile(answers, d)
    activate = answers.get("activate", True)
    cfg = candidate_config(existing, name, profile, activate)
    errors, warnings = ec.validate(cfg)
    problems = [e["message"] + f" [{e['path']}]" for e in errors]
    # назначенный движок должен быть подключён
    ready = usable_engines(d)
    for role, spec in profile["roles"].items():
        if spec["engine"] not in ready:
            problems.append(f"роль {role}: движок {spec['engine']} не подключён (нет CLI, входа или пресета)")
    host = profile["lead"]["host"]
    avail = {e["cli"] for e in d["clis"] if e["installed"]}
    avail |= {ec.PRESETS[e["engine"]]["binary"] for e in d["clis"] if e["installed"] and e["engine_preset"]}
    res = ec.resolve(cfg, host, avail, profile=name, have_config=True)
    notes = list(res["feed"])
    entry = next((e for e in d["clis"] if e["host"] == host), None)
    if entry is None or not entry["installed"]:
        problems.append(f"ведущий: среда {host} не установлена")
    elif entry["state"] not in ("responds", "unchecked"):
        problems.append(f"ведущий: {entry['cli']} не готов ({entry['state']})")
    if entry and entry["lead_capable"] is None:
        notes.append(f"⚙️ ведущий {host} не проверен как ведущий (спайк S0 не проведён)")
    elif not LEAD_CAPABLE.get(host):
        problems.append(f"ведущий: {host} не годится в ведущие")
    if entry and entry["models"] and profile["lead"].get("model") and profile["lead"]["model"] not in entry["models"]:
        notes.append(f"⚙️ модель ведущего {profile['lead']['model']} не из списка {entry['cli']} (проверьте имя)")
    return {"profile": name, "activate": activate, "lead": profile["lead"], "rows": plan_rows(cfg, name, host),
            "problems": problems, "notes": notes, "warnings": [w["message"] + f" [{w['path']}]" for w in warnings],
            "launch": launch_command(profile["lead"]), "config": cfg, "profile_body": profile}


def plan_text(plan):
    lines = [f"Профиль {plan['profile']}: ведущий {plan['lead']['host']}"
             + (f" / {plan['lead']['model']}" if plan["lead"].get("model") else "")]
    lines += [f"  {r['role']:<22} {r['engine']:<10} {r['model'] or '—':<28} {r['access']}" for r in plan["rows"]]
    lines += plan["notes"] + [f"предупреждение: {w}" for w in plan["warnings"]]
    lines += [f"ОШИБКА: {p}" for p in plan["problems"]]
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
        lines.append("  0. Другое")
    answer = ask("\n".join(lines) + "\n> ").strip()
    if answer.isdigit() and 1 <= int(answer) <= len(options):
        return options[int(answer) - 1][1]
    if other and answer == "0":
        return ("other", ask("Введите значение: ").strip())
    raise SetupError(f"нужен номер от {0 if other else 1} до {len(options)}")


def pick_engine(ask, d, question):
    ready = usable_engines(d)
    opts = [(f"{n}: {default_model(n, d)}", {"engine": n, "model": default_model(n, d)}) for n in ready]
    opts.append(("родной исполнитель среды", None))
    got = choose(ask, question, opts, other=False)
    return got


def dialog(d, ask, say, current_host=None):
    leads = [e for e in d["clis"] if e["host"] and e["lead_capable"] is not False and e["state"] in ("responds", "unchecked")]
    if not leads:
        raise SetupError("нет ни одного CLI, который может быть ведущим: установите и войдите хотя бы в один")
    opts = []
    for e in leads:
        mark = "проверен как ведущий" if e["lead_capable"] else "не проверен как ведущий"
        here = ", текущий сеанс" if e["host"] == current_host else ", нужен перезапуск"
        opts.append((f"{e['host']} / {e['models'][0] if e['models'] else 'по умолчанию'} ({mark}{here})",
                     {"host": e["host"], "model": e["models"][0] if e["models"] else ""}))
    lead = choose(ask, "Кто ведущий?", opts)
    if isinstance(lead, tuple):
        host, _, model = lead[1].partition("/")
        lead = {"host": host.strip(), "model": model.strip()}
    answers = {"lead": lead, "profile": ask("Имя профиля [default]: ").strip() or "default", "activate": True}
    sources = usable_engines(d)
    layouts = [("всё на модели ведущего", "all-on-lead")]
    if len(sources) >= 2:
        layouts.append(("код и ревью на разных моделях", "split"))
    layouts.append(("по группам ролей", "groups"))
    layout = choose(ask, "Состав команды?", layouts, other=False)
    answers["layout"] = layout
    if layout == "split":
        answers["split"] = {"coder": pick_engine(ask, d, "Кто пишет код?"),
                            "reviewer": pick_engine(ask, d, "Кто рецензирует?")}
    elif layout == "groups":
        titles = {"coders": "кодеры", "reviewer": "рецензент", "architects": "архитекторы и техлид",
                  "researchers": "исследователи", "verifiers": "проверяющие"}
        answers["groups"] = {g: pick_engine(ask, d, f"Кто исполняет группу «{titles[g]}»?") for g in GROUPS}
    if len(sources) >= 2:
        second = pick_engine(ask, d, "Второе мнение (другая модель)?")
        answers["second_opinion"] = second or "off"
    return answers


# ------------------------------------------------------------------ команды
def load_answers(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SetupError(f"ответы не читаются: {exc}")
    if not isinstance(data, dict):
        raise SetupError("ответы: корень не объект")
    return data


def emit(data, as_json, text):
    print(json.dumps(data, ensure_ascii=False) if as_json else text)


def public(plan):
    return {k: v for k, v in plan.items() if k not in ("config", "profile_body")}


def main(argv):
    top = argparse.ArgumentParser(description="Подключение supertick: поиск CLI, диалог, профиль")
    sub = top.add_subparsers(dest="cmd", required=True)
    for name in ("discover", "plan", "apply", "setup", "launch"):
        p = sub.add_parser(name)
        p.add_argument("--json", action="store_true")
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
    args = top.parse_args(argv)
    try:
        if args.cmd == "launch":
            cfg, _, _ = ec.load_config(ec.config_paths())
            prof = (cfg.get("profiles") or {}).get(args.profile)
            if not isinstance(prof, dict) or not prof.get("lead"):
                raise SetupError(f"профиль {args.profile} не найден или без ведущего")
            cmd = launch_command(prof["lead"])
            emit({"profile": args.profile, "lead": prof["lead"], "command": cmd}, args.json, cmd or
                 f"для хоста {prof['lead']['host']} команда запуска не известна")
            return 0
        current = None
        try:
            current = ec.detect_host(args.host)[0]
        except ValueError:
            pass
        d = discover(probe=not args.no_probe, probe_timeout=args.probe_timeout, current_host=current)
        if args.cmd == "discover":
            emit(d, args.json, discover_text(d))
            return 0
        path = target_path()
        existing = read_target(path)
        if args.cmd == "setup":
            if not sys.stdin.isatty():
                raise SetupError("диалогу нужен терминал; без него — `apply --answers ФАЙЛ`")
            print(discover_text(d))
            answers = dialog(d, input, print, current)
        else:
            answers = load_answers(args.answers)
        plan = build_plan(answers, d, existing)
        if args.cmd == "plan" or (args.cmd == "setup" and plan["problems"]):
            emit(public(plan), args.json, plan_text(plan))
            return 1 if plan["problems"] else 0
        if args.cmd == "setup":
            print(plan_text(plan))
            if input("Записать профиль? [y/N] ").strip().lower() not in ("y", "д", "yes", "да"):
                print("отменено")
                return 0
        if plan["problems"] and not getattr(args, "force", False):
            emit(public(plan), args.json, plan_text(plan))
            return 1
        backup = write_atomic(path, plan["config"])
        result = dict(public(plan), written=str(path), backup=backup)
        emit(result, args.json, plan_text(plan) + f"\nзаписано: {path}" + (f" (копия {backup})" if backup else "")
             + (f"\nзапуск: {plan['launch']}" if plan["launch"] else ""))
        return 0
    except SetupError as exc:
        print(f"supertick-setup: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
