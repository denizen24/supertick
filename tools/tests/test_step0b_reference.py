"""Эталонная таблица шага 0b на трёх конфигах и их вариантах (121-567, F1).

В 1.1.1 шаг 0b («Шаг 0b: разрешение движков», engines.md) описан словами и исполняется самим
ведущим: кода, который его считает, нет. Чтобы F3 (`engine-config.py resolve`) было с чем сверять,
ожидания записаны руками в `snapshots/step0b-cases.json`. Здесь они проверяются дважды:
1. эталонным разрешением правил из текста (ниже, ~50 строк) — две независимые записи одних и тех же
   правил обязаны совпасть, иначе я неверно прочёл текст или ошибся в таблице;
2. инвариантами, которые должны держаться на любом конфиге.

Когда появится `engine-config.py`, тот же файл сверяют уже с ним; эталонное разрешение отсюда уйдёт.
"""
import json
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
CASES = json.loads((HERE / "snapshots" / "step0b-cases.json").read_text(encoding="utf-8"))["cases"]

BINARY = {"cursor": "cursor-agent", "codex": "codex", "kimi": "kimi", "grok": "grok"}
FOREIGN = {"research-challenger", "research-critic", "rollback-critic"}        # роли чужих плагинов
RETIRED = {"security-reviewer", "logic-reviewer", "quality-reviewer"}          # снятые id: одно предупреждение
CLAUDE_ONLY = {"lead", "browser-verifier", "acceptance-checker"}               # любое другое назначение игнорируется


def strip_comments(x):
    if isinstance(x, dict):
        return {k: strip_comments(v) for k, v in x.items() if not k.startswith("_")}
    if isinstance(x, list):
        return [strip_comments(v) for v in x]
    return x


def reference_resolve(raw, available, engines_off):
    """Шаг 0b по тексту engines.md. raw — текст файла или None; available — найденные бинари."""
    out = {"stop": False, "table": {}, "warnings": [], "summary_line": False}
    if raw is None:
        return out
    try:
        cfg = json.loads(raw)
    except ValueError:
        out["warnings"].append("broken_json")
        return out
    cfg = strip_comments(cfg)
    if engines_off or cfg.get("enabled", True) is False:
        return out
    fallback = cfg.get("fallback", "claude")
    table, second = {}, None
    for role, spec in (cfg.get("roles") or {}).items():
        if role in FOREIGN:
            continue
        if role in RETIRED:
            out["warnings"].append(f"retired_role:{role}")
            continue
        engine = spec if isinstance(spec, str) else spec.get("engine")
        if role in CLAUDE_ONLY and engine not in (None, "claude"):
            out["warnings"].append(f"assignment_ignored:{role}")
            continue
        if role == "second-reviewer":
            second = engine
        else:
            table[role] = engine
    for role, engine in list(table.items()):
        if engine != "claude" and BINARY.get(engine) not in available:
            if fallback == "fail":
                return {"stop": True, "reason": f"engine_missing:{role}:{engine}"}
            out["warnings"].append(f"engine_missing:{role}:{engine}")
            table[role] = "claude"
    unified = table.get("unified-reviewer", "claude")
    if second is None:
        table["second-reviewer"] = "none"
    elif second != "claude" and BINARY.get(second) not in available:
        table["second-reviewer"] = "none"
    elif second == unified:
        out["warnings"].append("second_reviewer_same_engine")
        table["second-reviewer"] = "none"
    else:
        table["second-reviewer"] = second
    out["table"] = table
    out["summary_line"] = any(v not in ("claude", "none") for v in table.values())
    return out


def case_config(case, by_name):
    """Текст конфига случая: объект, ссылка на другой случай или сырая строка."""
    if "config_from" in case:
        return case_config(by_name[case["config_from"]], by_name)
    if "config_raw" in case:
        return case["config_raw"]
    return json.dumps(case["config"], ensure_ascii=False)


class Step0bReference(unittest.TestCase):
    def setUp(self):
        self.by_name = {c["name"]: c for c in CASES}

    def test_reference_resolution_matches_the_hand_written_tables(self):
        for case in CASES:
            with self.subTest(case=case["name"]):
                got = reference_resolve(case_config(case, self.by_name), set(case["available"]), case["engines_off"])
                want = dict(case["expect"])
                self.assertEqual(got["stop"], want["stop"])
                if want["stop"]:
                    self.assertEqual(got["reason"], want["reason"])
                    continue
                self.assertEqual(got["table"], want["table"])
                self.assertEqual(sorted(got["warnings"]), sorted(want["warnings"]))
                self.assertEqual(got["summary_line"], want["summary_line"])

    def test_invariants_hold_on_every_case(self):
        for case in CASES:
            table = case["expect"].get("table")
            if table is None:
                continue
            with self.subTest(case=case["name"]):
                self.assertFalse(FOREIGN & set(table), "роли чужих плагинов не попадают в таблицу")
                self.assertFalse(RETIRED & set(table))
                for role, engine in table.items():
                    self.assertTrue(engine in BINARY or engine in ("claude", "none"), (role, engine))
                    if engine == "none":
                        self.assertEqual(role, "second-reviewer", "none бывает только у second-reviewer")
                    if role in CLAUDE_ONLY:
                        self.assertEqual(engine, "claude")
                if table:
                    self.assertIn("second-reviewer", table, "в непустой таблице second-reviewer решён всегда")

    def test_cases_cover_every_rule_of_the_step(self):
        text = json.dumps(CASES, ensure_ascii=False)
        for marker in ("engine_missing", "assignment_ignored", "retired_role", "second_reviewer_same_engine",
                       "broken_json", '"fallback": "fail"', '"enabled": false', '"engines_off": true',
                       "research-critic", '"_note"'):
            self.assertIn(marker, text, f"эталон не покрывает правило: {marker}")


if __name__ == "__main__":
    unittest.main()
