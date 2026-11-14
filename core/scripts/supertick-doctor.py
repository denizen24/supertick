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


def report(host_flag=None, probe=False, probe_timeout=90, config=None, env=None):
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
        "host": host, "host_source": source, "level": level,
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
        return "busy: ps и lsof" if which("lsof") else "busy: выключено — на macOS нет lsof"
    return f"busy: выключено — на {platform} нет /proc"


STATE = {"responds": "отвечает", "not_logged_in": "не выполнен вход", "no_answer": "не ответил",
         "error": "ошибка пробы", "unchecked": "установлен (проба не запускалась)", "not_installed": "не установлен"}


def text(r):
    lines = [f"supertick {r['version'] or 'версия неизвестна'}" + (f" ({r['manifest']})" if r["manifest"] else ""),
             f"корень: {r['root']}",
             f"среда: {r['host']} ({r['level']}, по {r['host_source']})" if r["host"]
             else f"среда: не определена — {r['host_source']}"]
    c = r["config"]
    lines.append("конфиг: " + (", ".join(c["used"]) if c["used"] else "файлов нет (искал: " + ", ".join(c["searched"]) + ")"))
    lines.append(f"запись профилей: {c['write_target']}")
    if r.get("busy"):
        lines.append(r["busy"])
    for b in c["broken"]:
        lines.append(f"  ОШИБКА: {b} не читается (битый JSON)")
    for e in c["errors"]:
        lines.append(f"  ОШИБКА: {e['path']}: {e['message']}")
    for w in c["warnings"]:
        lines.append(f"  предупреждение: {w['path']}: {w['message']}")
    if c["ok"]:
        lines.append("  validate: в порядке")
    lines.append("CLI:")
    for e in r["clis"]:
        extra = ", ".join(x for x in (e["version"], e["detail"]) if x)
        lead = " · может быть ведущим" if e["lead_capable"] else (" · ведущим не проверен" if e["lead_capable"] is None and e["installed"] else "")
        lines.append(f"  {e['cli']}: {STATE[e['state']]}" + (f" ({extra})" if extra else "") + lead)
    p = r["profile"]
    if p is None:
        lines.append("профиль: нет активного")
    elif not p["found"]:
        lines.append(f"профиль: {p['name']} указан, но в profiles его нет")
    else:
        lead = p["lead"] or {}
        probe = p["lead_probe"]
        probe_txt = "не запускалась" if not probe else (
            f"{probe.get('result', '?')} {probe.get('date', '')} {probe.get('version', '')}".strip()
            if isinstance(probe, dict) else str(probe))
        fp = {True: "набор CLI тот же", False: "набор CLI изменился", None: "отпечатка нет"}[p["fingerprint_matches"]]
        lines.append(f"профиль: {p['name']} — ведущий {lead.get('host', '?')}"
                     + (f"/{lead['model']}" if lead.get("model") else "")
                     + f", ролей назначено {p['roles']}, подтверждён {p['confirmed_at'] or '—'}, {fp}")
        lines.append(f"  проба ведущего: {probe_txt}")
    s = r["step0"]
    if s:
        lines.append(f"шаг 0: {s['action']}" + (f" — {s['launch']}" if s.get("launch") else "")
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
    args = ap.parse_args(argv)
    r = report(args.host, args.probe, args.probe_timeout, args.config)
    print(json.dumps(r, ensure_ascii=False) if args.json else text(r))
    return 0 if r["config"]["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
