#!/usr/bin/env python3
"""supertick-doctor.py — что установлено и как настроено (121-567, F7, п. 4.7 ТЗ). Только stdlib, ничего не пишет.

  supertick-doctor.py [--host H] [--probe] [--probe-timeout С] [--config ФАЙЛ ...] [--json]

Печатает: версию набора, среду и её уровень, корень набора, файлы конфига и результат `validate`,
найденные CLI с версиями (с `--probe` — и итог пробы каждого одним вызовом), активный профиль с
ведущим, отпечатком и итогом пробы ведущего, решение шага 0 (`engine-config.py host`).

Корень набора — $SUPERTICK_ROOT, иначе каталог над scripts/. Версия — из манифеста, который лежит в
корне (у каждой среды свой), иначе «неизвестна».

Коды выхода: 0 всё читается и проходит validate; 1 есть ошибки validate или битый файл конфига; 2 неверный вызов.
"""
import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


setup = _load("supertick_setup", "supertick-setup.py")
ec = setup.ec
T = ec.T   # текст для человека на языке ec.OUTPUT_LANG (121-604)

# Манифесты наборов по средам: первый найденный в корне даёт версию.
MANIFESTS = (".claude-plugin/plugin.json", "kimi.plugin.json", ".codex-plugin/plugin.json", "supertick.json", "package.json")


def bundle_root(env=None):
    env = os.environ if env is None else env
    if env.get("SUPERTICK_ROOT"):
        return Path(env["SUPERTICK_ROOT"]).expanduser()
    return SCRIPTS_DIR.parent


def bundle_version(root):
    for rel in MANIFESTS:
        path = root / rel
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("version"):
            return str(data["version"]), rel
    return "", ""


def report(host_flag=None, probe=False, probe_timeout=90, config=None, env=None, lang=None):
    env = os.environ if env is None else env
    root = bundle_root(env)
    version, manifest = bundle_version(root)
    try:
        host, source = ec.detect_host(host_flag, env)
        level = ec.HOSTS[host]["level"]
    except ValueError as exc:
        host, source, level = None, str(exc), ""
    paths = ec.config_paths(config, env)
    cfg, load_warn, used = ec.load_config(paths)
    errors, warnings = ec.validate(cfg)
    language = ec.resolve_language(cfg, flag=lang, env=env)
    ec.OUTPUT_LANG = language["lang"]
    d = setup.discover(probe=probe, probe_timeout=probe_timeout, current_host=host)
    name, prof = ec.active_profile(cfg)
    profile = None
    if name:
        profile = {"name": name, "found": prof is not None}
        if prof is not None:
            profile.update({
                "lead": prof.get("lead"), "roles": len(prof.get("roles") or {}),
                "confirmed_at": prof.get("confirmed_at", ""), "lead_probe": prof.get("lead_probe"),
                "fingerprint_matches": (prof.get("fingerprint") == d["fingerprint"]) if prof.get("fingerprint") else None,
            })
    decision = None
    if host:
        avail = {e["cli"] for e in d["clis"] if e["installed"]}
        decision = ec.host_decision(cfg, host, name, prof, None, avail)
    return {
        "version": version, "manifest": manifest, "root": str(root),
        "host": host, "host_source": source, "level": level, "language": language,
        "config": {"searched": [str(p) for p in paths], "used": used,
                   "write_target": str(setup.target_path(env)),
                   "broken": [w["detail"] for w in load_warn],
                   "ok": not errors and not load_warn, "errors": errors, "warnings": warnings},
        "clis": [{k: e[k] for k in ("cli", "installed", "version", "state", "detail", "path", "lead_capable",
                                    "engine_preset")} for e in d["clis"]],
        "sources": d["sources"], "probed": probe, "profile": profile, "step0": decision,
        "busy": busy_rule(),
    }


def busy_rule(platform=None, proc=None, which=None):
    """Откуда тик берёт процессы для правила busy (тесты под корнем понижают молчание до P3), или почему выключено."""
    import shutil
    platform = platform or sys.platform
    proc = Path("/proc") if proc is None else Path(proc)
    which = which or shutil.which
    if proc.is_dir():
        return "busy: /proc"
    if platform == "darwin":
        return "busy: ps и lsof" if which("lsof") else T("busy: выключено — на macOS нет lsof", "busy: off — no lsof on macOS")
    return T(f"busy: выключено — на {platform} нет /proc", f"busy: off — no /proc on {platform}")


def state_label(state):
    return {"responds": T("отвечает", "responds"), "not_logged_in": T("не выполнен вход", "not logged in"),
            "no_answer": T("не ответил", "no answer"), "error": T("ошибка пробы", "probe error"),
            "unchecked": T("установлен (проба не запускалась)", "installed (not probed)"),
            "not_installed": T("не установлен", "not installed")}[state]


def text(r):
    lines = [f"supertick {r['version'] or T('версия неизвестна', 'version unknown')}" + (f" ({r['manifest']})" if r["manifest"] else ""),
             T("корень: ", "root: ") + r["root"],
             T(f"среда: {r['host']} ({r['level']}, по {r['host_source']})", f"host: {r['host']} ({r['level']}, by {r['host_source']})")
             if r["host"] else T(f"среда: не определена — {r['host_source']}", f"host: unknown — {r['host_source']}")]
    if r.get("language"):
        lines.append(T("язык: ", "language: ") + f"{r['language']['lang']} ({r['language']['source']})")
    c = r["config"]
    lines.append(T("конфиг: ", "config: ") + (", ".join(c["used"]) if c["used"] else
                 T("файлов нет (искал: ", "no files (searched: ") + ", ".join(c["searched"]) + ")"))
    lines.append(T("запись профилей: ", "profiles written to: ") + c["write_target"])
    if r.get("busy"):
        lines.append(r["busy"])
    for b in c["broken"]:
        lines.append(T(f"  ОШИБКА: {b} не читается (битый JSON)", f"  ERROR: {b} is unreadable (broken JSON)"))
    for e in c["errors"]:
        lines.append(T("  ОШИБКА: ", "  ERROR: ") + f"{e['path']}: {e['message']}")
    for w in c["warnings"]:
        lines.append(T("  предупреждение: ", "  warning: ") + f"{w['path']}: {w['message']}")
    if c["ok"]:
        lines.append(T("  validate: в порядке", "  validate: OK"))
    lines.append("CLI:")
    for e in r["clis"]:
        extra = ", ".join(x for x in (e["version"], e["detail"]) if x)
        lead = T(" · может быть ведущим", " · can lead") if e["lead_capable"] else (
            T(" · ведущим не проверен", " · not checked as lead") if e["lead_capable"] is None and e["installed"] else "")
        lines.append(f"  {e['cli']}: {state_label(e['state'])}" + (f" ({extra})" if extra else "") + lead)
    p = r["profile"]
    if p is None:
        lines.append(T("профиль: нет активного", "profile: none active"))
    elif not p["found"]:
        lines.append(T(f"профиль: {p['name']} указан, но в profiles его нет", f"profile: {p['name']} is set but missing from profiles"))
    else:
        lead = p["lead"] or {}
        probe = p["lead_probe"]
        probe_txt = T("не запускалась", "not run") if not probe else (
            f"{probe.get('result', '?')} {probe.get('date', '')} {probe.get('version', '')}".strip()
            if isinstance(probe, dict) else str(probe))
        fp = {True: T("набор CLI тот же", "same CLI set"), False: T("набор CLI изменился", "CLI set changed"),
              None: T("отпечатка нет", "no fingerprint")}[p["fingerprint_matches"]]
        model = f"/{lead['model']}" if lead.get("model") else ""
        lines.append(T(f"профиль: {p['name']} — ведущий {lead.get('host', '?')}{model}"
                       f", ролей назначено {p['roles']}, подтверждён {p['confirmed_at'] or '—'}, {fp}",
                       f"profile: {p['name']} — lead {lead.get('host', '?')}{model}"
                       f", roles assigned {p['roles']}, confirmed {p['confirmed_at'] or '—'}, {fp}"))
        lines.append(T("  проба ведущего: ", "  lead probe: ") + probe_txt)
    s = r["step0"]
    if s:
        lines.append(T("шаг 0: ", "step 0: ") + s["action"] + (f" — {s['launch']}" if s.get("launch") else "")
                     + (f" — {s['command']}" if s.get("command") else ""))
        lines += ["  " + line for line in s["lines"]]
    return "\n".join(lines)


def main(argv):
    ap = argparse.ArgumentParser(description="Диагностика supertick: версия, среда, конфиг, CLI, профиль")
    ap.add_argument("--host")
    ap.add_argument("--probe", action="store_true", help="пробовать каждый CLI одним вызовом (тратит вызовы)")
    ap.add_argument("--probe-timeout", type=int, default=90)
    ap.add_argument("--config", action="append")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--lang", help="язык вывода: ru | en (по умолчанию SUPERTICK_LANG, профиль, конфиг, локаль, en)")
    args = ap.parse_args(argv)
    try:
        r = report(args.host, args.probe, args.probe_timeout, args.config, lang=args.lang)
    except ValueError as exc:
        print(f"supertick-doctor: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(r, ensure_ascii=False) if args.json else text(r))
    return 0 if r["config"]["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
