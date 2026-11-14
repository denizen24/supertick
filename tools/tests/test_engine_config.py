"""engine-config.py (121-567, F3): конфиг, профили, правила п. 4.3 по одному, argv против эталона 1.1.1.

Эталоны F1: `snapshots/step0b-cases.json` (шаг 0b на конфигах 1.1.x) и `snapshots/presets-1.1.1.json`
(argv четырёх пресетов). Сеть и настоящие CLI не нужны: наличие бинарей задаётся тестом, HOME — временный.

Запуск: python3 -m unittest discover -s tools/tests   (из корня репозитория)
"""
import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from test_engine_presets import GOLDEN, render
from test_scripts import tmpdir
from test_step0b_reference import case_config

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parents[1] / "core" / "scripts" / "engine-config.py"
spec = importlib.util.spec_from_file_location("engine_config", SCRIPT)
ec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ec)

CASES = json.loads((HERE / "snapshots" / "step0b-cases.json").read_text(encoding="utf-8"))["cases"]
ALL_BINS = {"cursor-agent", "codex", "kimi", "grok"}
OWNER = next(c for c in CASES if c["name"] == "owner-config-cursor-present")["config"]


def resolved(cfg, host="claude-code", avail=ALL_BINS, **kw):
    return ec.resolve(ec.strip_comments(cfg), host, set(avail), **kw)


class F1Compatibility(unittest.TestCase):
    """На конфигах 1.1.x resolve совпадает с руками записанным эталоном шага 0b."""

    def test_every_case_matches_the_hand_written_table(self):
        by_name = {c["name"]: c for c in CASES}
        for case in CASES:
            with self.subTest(case=case["name"]):
                raw = case_config(case, by_name)
                files = []
                if raw is not None:
                    path = tmpdir(self) / "c.json"
                    path.write_text(raw, encoding="utf-8")
                    files = [path]
                cfg, load_warn, used = ec.load_config(files)
                got = ec.resolve(cfg, "claude-code", set(case["available"]), engines_off=case["engines_off"],
                                 load_warnings=load_warn, have_config=bool(used))
                want = case["expect"]
                self.assertEqual(got["stop"], want["stop"])
                if want["stop"]:
                    self.assertEqual(got["reason"], want["reason"])
                    continue
                self.assertEqual(got["table"], want["table"])
                self.assertEqual(sorted(got["warnings"]), sorted(want["warnings"]))
                self.assertEqual(got["summary_line"], want["summary_line"])


class ConfigLoading(unittest.TestCase):
    def write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        return path

    def test_search_order_and_merge_earlier_wins(self):
        home = tmpdir(self)
        env = self.write(home / "env.json", {"fallback": "fail", "roles": {"coder": "codex"}})
        self.write(home / ".config" / "supertick" / "config.json", {"fallback": "host", "roles": {"coder": "grok", "spec-verifier": "grok"}})
        self.write(home / ".claude" / "agent-teams.json", {"enabled": True, "roles": {"spec-verifier": "cursor", "risk-tester": "cursor"}})
        with mock.patch.dict(os.environ, {"HOME": str(home), "SUPERTICK_CONFIG": str(env)}):
            cfg, warn, used = ec.load_config(ec.config_paths())
        self.assertEqual(len(used), 3)
        self.assertEqual(cfg["fallback"], "fail", "более ранний файл главнее")
        self.assertEqual(cfg["roles"], {"coder": "codex", "spec-verifier": "grok", "risk-tester": "cursor"},
                         "роли сливаются по ключам, при споре — ранний")
        self.assertEqual(warn, [])

    def test_without_the_env_variable_the_xdg_file_beats_the_legacy_one(self):
        home = tmpdir(self)
        self.write(home / ".config" / "supertick" / "config.json", {"roles": {"coder": "codex"}})
        self.write(home / ".claude" / "agent-teams.json", {"roles": {"coder": "grok"}})
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            os.environ.pop("SUPERTICK_CONFIG", None)
            cfg, _, _ = ec.load_config(ec.config_paths())
        self.assertEqual(cfg["roles"]["coder"], "codex")

    def test_legacy_file_alone_works_without_any_edit(self):
        home = tmpdir(self)
        self.write(home / ".claude" / "agent-teams.json", OWNER)
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            os.environ.pop("SUPERTICK_CONFIG", None)
            cfg, warn, used = ec.load_config(ec.config_paths())
        self.assertEqual(used, [str(home / ".claude" / "agent-teams.json")])
        self.assertEqual(cfg["roles"]["second-reviewer"]["model"], "gpt-5.6-sol-high")

    def test_a_broken_file_is_skipped_with_a_warning_and_the_rest_is_used(self):
        home = tmpdir(self)
        broken = self.write(home / "a.json", '{"roles": ')
        good = self.write(home / "b.json", {"roles": {"coder": "codex"}})
        cfg, warn, used = ec.load_config([broken, good])
        self.assertEqual(cfg["roles"], {"coder": "codex"})
        self.assertEqual(warn, [{"code": "broken_json", "detail": str(broken)}])
        self.assertEqual(used, [str(good)])

    def test_underscore_keys_are_comments_on_every_level(self):
        stripped = ec.strip_comments({"_a": 1, "roles": {"coder": {"engine": "codex", "_why": "x"}}, "engines": {"_n": 1, "cursor": {"_m": 2, "model": "m"}}})
        self.assertEqual(stripped, {"roles": {"coder": {"engine": "codex"}}, "engines": {"cursor": {"model": "m"}}})

    def test_explicit_config_replaces_the_search(self):
        home = tmpdir(self)
        explicit = self.write(home / "x.json", {"roles": {"coder": "codex"}})
        self.assertEqual(ec.config_paths([str(explicit)], env={"SUPERTICK_CONFIG": "/nope"}), [explicit])


class Profiles(unittest.TestCase):
    CFG = {"fallback": "host", "roles": {"coder": "codex", "spec-verifier": "cursor"},
           "active_profile": "kimi-team",
           "profiles": {
               "kimi-team": {"lead": {"host": "kimi", "model": "kimi-code/k3"},
                             "roles": {"coder": {"engine": "grok", "model": "grok-4.6"}, "unified-reviewer": "codex"},
                             "confirmed_at": "2026-11-10", "fingerprint": "sha256:abc"},
               "other": {"roles": {"coder": "cursor"}}}}

    def test_the_active_profile_overlays_its_roles_over_roles(self):
        res = resolved(self.CFG)
        self.assertEqual(res["profile"], "kimi-team")
        self.assertEqual(res["table"]["coder"], "grok", "роль профиля заменяет роль конфига")
        self.assertEqual(res["table"]["spec-verifier"], "cursor", "остальные роли — из roles")
        self.assertEqual(res["table"]["unified-reviewer"], "codex")

    def test_the_profile_flag_picks_another_profile(self):
        res = resolved(self.CFG, profile="other")
        self.assertEqual((res["profile"], res["table"]["coder"]), ("other", "cursor"))

    def test_an_unknown_profile_warns_and_falls_back_to_plain_roles(self):
        res = resolved(self.CFG, profile="ghost")
        self.assertIsNone(res["profile"])
        self.assertIn("profile_missing:ghost", res["warnings"])
        self.assertEqual(res["table"]["coder"], "codex")

    def test_no_profile_means_plain_roles(self):
        res = resolved({"roles": self.CFG["roles"]})
        self.assertIsNone(res["profile"])
        self.assertEqual(res["table"]["coder"], "codex")

    def test_host_command_compares_the_session_with_the_profile_lead(self):
        out = subprocess.run([sys.executable, str(SCRIPT), "host", "--host", "kimi", "--model", "kimi-code/k3", "--json",
                              "--config", self.config_file()], capture_output=True, text=True)
        data = json.loads(out.stdout)
        self.assertEqual((data["host"], data["source"], data["level"], data["profile"], data["lead_matches"]),
                         ("kimi", "flag", "L2", "kimi-team", True))
        other = subprocess.run([sys.executable, str(SCRIPT), "host", "--host", "claude-code", "--json", "--config", self.config_file()],
                               capture_output=True, text=True)
        self.assertFalse(json.loads(other.stdout)["lead_matches"])

    def config_file(self):
        path = tmpdir(self) / "p.json"
        path.write_text(json.dumps(self.CFG), encoding="utf-8")
        return str(path)


class Fallback(unittest.TestCase):
    CFG = {"roles": {"spec-verifier": "cursor"}}

    def test_host_is_the_default_and_claude_is_its_synonym(self):
        for fb in (None, "host", "claude"):
            with self.subTest(fallback=fb):
                cfg = dict(self.CFG, **({"fallback": fb} if fb else {}))
                res = resolved(cfg, avail=())
                self.assertEqual(res["table"]["spec-verifier"], "claude")
                self.assertEqual(res["warnings"], ["engine_missing:spec-verifier:cursor"])

    def test_fail_stops_the_run(self):
        res = resolved(dict(self.CFG, fallback="fail"), avail=())
        self.assertEqual((res["stop"], res["reason"]), (True, "engine_missing:spec-verifier:cursor"))

    def test_on_another_host_the_fallback_target_is_that_hosts_native_executor(self):
        res = resolved(self.CFG, host="kimi", avail=())
        self.assertEqual(res["table"]["spec-verifier"], "native")
        self.assertIn("роль spec-verifier: движок cursor не найден, роль на родном исполнителе", res["feed"][0])


class Rule1NativeByDefault(unittest.TestCase):
    def test_an_unassigned_role_is_not_in_the_table_on_hosts_with_subagents(self):
        for host in ("claude-code", "kimi", "opencode"):
            with self.subTest(host=host):
                self.assertNotIn("coder", resolved({"roles": {"spec-verifier": "cursor"}}, host=host)["table"])

    def test_a_host_without_subagents_runs_unassigned_roles_on_its_own_engine(self):
        # после спайка S0-OC у всех известных сред субагенты есть — среда без них проверяется подменой таблицы
        hosts = {**ec.HOSTS, "bare": {"level": "L1", "native": "bare", "subagents": False, "engine": "opencode"}}
        with mock.patch.object(ec, "HOSTS", hosts):
            res = resolved({"roles": {"spec-verifier": "cursor"}}, host="bare")
        self.assertEqual(res["table"]["coder"], "opencode")
        self.assertEqual(res["table"]["spec-verifier"], "cursor")
        self.assertNotIn("browser-verifier", res["table"])
        self.assertNotIn("lead", res["table"])
        self.assertTrue(res["summary_line"], "cursor назначен явно")


class Rule2MissingEngine(unittest.TestCase):
    def test_each_missing_binary_goes_by_fallback_with_a_line_per_role(self):
        res = resolved({"roles": {"coder": "codex", "spec-verifier": "grok"}}, avail={"codex"})
        self.assertEqual(res["table"]["coder"], "codex")
        self.assertEqual(res["table"]["spec-verifier"], "claude")
        self.assertEqual(res["warnings"], ["engine_missing:spec-verifier:grok"])
        self.assertTrue(res["feed"][0].startswith("⚙️ "))

    def test_an_unknown_engine_without_a_command_is_treated_as_missing(self):
        res = resolved({"roles": {"spec-verifier": "mystery"}})
        self.assertEqual(res["table"]["spec-verifier"], "claude")

    def test_a_custom_engine_with_a_command_is_found_by_its_binary(self):
        cfg = {"roles": {"spec-verifier": "mine"}, "engines": {"mine": {"cmd": "mine-cli --go {prompt}"}}}
        self.assertEqual(resolved(cfg, avail={"mine-cli"})["table"]["spec-verifier"], "mine")
        self.assertEqual(resolved(cfg, avail=())["table"]["spec-verifier"], "claude")


class Rule3Access(unittest.TestCase):
    def access(self, role, **spec):
        return ec.role_access(role, spec)

    def test_access_comes_from_the_registry(self):
        self.assertEqual([self.access(r)[0] for r in ("coder", "risk-tester", "ci-verifier", "unified-reviewer", "spec-verifier")],
                         ["write", "write", "write", "read", "read"])

    def test_an_explicit_value_can_only_narrow_write_to_read(self):
        self.assertEqual(self.access("coder", access="read"), ("read", None))
        access, error = self.access("unified-reviewer", access="write")
        self.assertEqual(access, "read")
        self.assertIn("шире", error)
        errors, _ = ec.validate({"roles": {"unified-reviewer": {"engine": "codex", "access": "write"}}})
        self.assertEqual([e["code"] for e in errors], ["access_widen"])

    def test_the_old_sandbox_key_of_1_1_x_still_sets_the_mode(self):
        self.assertEqual(self.access("spec-verifier", sandbox="workspace-write")[0], "write")
        self.assertEqual(self.access("coder", sandbox="read-only")[0], "read")

    def test_a_role_with_commands_takes_write_flags_where_read_mode_blocks_the_shell(self):
        cfg = {"roles": {"spec-verifier": "cursor"}}
        cursor = ec.build_argv(cfg, "claude-code", "spec-verifier", None, "/r", "/p.md", available=ALL_BINS)
        self.assertEqual(cursor["access"], "write")
        self.assertEqual(cursor["argv"][cursor["argv"].index("--sandbox") + 1], "enabled")
        codex = ec.build_argv({"roles": {"spec-verifier": "codex"}}, "claude-code", "spec-verifier", None, "/r", "/p.md", available=ALL_BINS)
        self.assertEqual(codex["access"], "read", "в песочнице codex чтение shell не блокирует")
        self.assertEqual(codex["argv"][codex["argv"].index("--sandbox") + 1], "read-only")


class Rule4WriteNeedsWriteMode(unittest.TestCase):
    # движок без режима записи: свой, из конфига, без {sandbox} и mode_flags.write
    RO = {"engines": {"ro": {"cmd": "ro-cli -p {prompt}"}}}

    def test_a_writing_role_on_an_engine_without_write_mode_is_an_error_not_a_silent_swap(self):
        cfg = dict(self.RO, roles={"coder": "ro"})
        errors, _ = ec.validate(cfg)
        self.assertEqual([(e["code"], e["path"]) for e in errors], [("no_write_mode", "roles.coder.engine")])
        res = resolved(cfg, avail=ALL_BINS | {"ro-cli"})
        self.assertEqual((res["stop"], res["reason"]), (True, "no_write_mode:coder:ro"))

    def test_narrowing_the_role_to_read_makes_it_legal(self):
        cfg = dict(self.RO, roles={"coder": {"engine": "ro", "access": "read"}})
        self.assertEqual(ec.validate(cfg)[0], [])
        self.assertEqual(resolved(cfg, avail={"ro-cli"})["table"]["coder"], "ro")

    def test_a_config_may_add_write_mode_to_an_engine_by_mode_flags(self):
        cfg = {"roles": {"coder": "ro"}, "engines": {"ro": {"cmd": "ro-cli {mode_flags} -p {prompt}",
                                                           "mode_flags": {"read": "--plan", "write": "--yolo"}}}}
        self.assertEqual(ec.validate(cfg)[0], [])

    def test_engines_with_a_write_mode_accept_writing_roles(self):
        for engine in ("cursor", "codex", "grok", "kimi", "claude", "mock"):
            self.assertEqual(ec.validate({"roles": {"coder": engine}})[0], [], engine)


class Rule5SecondReviewer(unittest.TestCase):
    def second(self, roles, **kw):
        return resolved({"roles": roles}, **kw)

    def test_no_key_means_none(self):
        self.assertEqual(self.second({"spec-verifier": "cursor"})["table"]["second-reviewer"], "none")

    def test_no_binary_means_none_without_a_line(self):
        res = self.second({"second-reviewer": "cursor"}, avail=())
        self.assertEqual(res["table"]["second-reviewer"], "none")
        self.assertEqual(res["warnings"], [])

    def test_the_same_engine_and_model_as_the_reviewer_is_none_with_one_line(self):
        res = self.second({"unified-reviewer": {"engine": "cursor", "model": "m1"},
                           "second-reviewer": {"engine": "cursor", "model": "m1"}})
        self.assertEqual(res["table"]["second-reviewer"], "none")
        self.assertEqual(res["warnings"], ["second_reviewer_same_engine"])

    def test_the_same_engine_with_another_model_is_kept_D7(self):
        res = self.second({"unified-reviewer": {"engine": "cursor", "model": "m1"},
                           "second-reviewer": {"engine": "cursor", "model": "m2"}})
        self.assertEqual(res["table"]["second-reviewer"], "cursor")
        self.assertEqual(res["warnings"], [])

    def test_another_engine_is_kept(self):
        res = self.second({"unified-reviewer": "codex", "second-reviewer": "cursor"})
        self.assertEqual(res["table"]["second-reviewer"], "cursor")

    def test_the_default_model_of_a_preset_counts_as_the_model(self):
        res = self.second({"unified-reviewer": "codex", "second-reviewer": "codex"})
        self.assertEqual(res["table"]["second-reviewer"], "none")

    def test_fallback_fail_does_not_apply_to_the_second_reviewer(self):
        res = resolved({"fallback": "fail", "roles": {"second-reviewer": "cursor"}}, avail=())
        self.assertFalse(res["stop"])


class Rule6HostBoundRoles(unittest.TestCase):
    def test_browser_verifier_is_off_outside_claude_code(self):
        res = resolved({"roles": {"browser-verifier": "claude"}}, host="kimi")
        self.assertEqual(res["table"]["browser-verifier"], "none")
        self.assertEqual(res["warnings"], ["role_disabled_on_host:browser-verifier"])

    def test_web_researcher_outside_claude_code_goes_only_to_an_engine_with_live_search(self):
        off = resolved({"roles": {"web-researcher": "cursor"}}, host="kimi")
        self.assertEqual((off["table"]["web-researcher"], off["warnings"]), ("none", ["web_search_required:web-researcher"]))
        on = resolved({"roles": {"web-researcher": "grok"}}, host="kimi")
        self.assertEqual((on["table"]["web-researcher"], on["warnings"]), ("grok", []))

    def test_claude_code_is_unrestricted_for_web_researcher(self):
        self.assertEqual(resolved({"roles": {"web-researcher": "cursor"}})["table"]["web-researcher"], "cursor")

    def test_lead_is_always_native_and_acceptance_is_native_only_in_claude_code(self):
        cc = resolved({"roles": {"lead": "codex", "acceptance-checker": "cursor"}})
        self.assertEqual(sorted(cc["warnings"]), ["assignment_ignored:acceptance-checker", "assignment_ignored:lead"])
        kimi = resolved({"roles": {"lead": "codex", "acceptance-checker": "cursor"}}, host="kimi")
        self.assertEqual(kimi["warnings"], ["assignment_ignored:lead"])
        self.assertEqual(kimi["table"]["acceptance-checker"], "cursor", "вне Claude Code приёмку исполняет движок (D6)")


class EnginesOverride(unittest.TestCase):
    def test_a_config_overrides_a_preset_field_by_field(self):
        eng = ec.engine_def(ec.strip_comments(OWNER), "cursor")
        self.assertEqual(eng["model"], "grok-4.7-xhigh-fast")
        self.assertEqual(eng["cmd"][-2:], ["--", "{prompt}"], "$(cat {prompt_file}) превращается в {prompt}")
        self.assertNotIn("<", eng["cmd"])
        base = ec.engine_def({}, "codex")
        over = ec.engine_def({"engines": {"codex": {"model": "x"}}}, "codex")
        self.assertEqual((over["model"], over["cmd"]), ("x", base["cmd"]))

    def test_stdin_redirect_is_dropped(self):
        self.assertEqual(ec.split_template('codex exec "{prompt}" < /dev/null'), ["codex", "exec", "{prompt}"])


class Argv(unittest.TestCase):
    def build(self, preset, which, access, session=""):
        role = {"read": "unified-reviewer", "write": "coder"}[access]
        cfg = {"roles": {role: preset}}
        built = ec.build_argv(cfg, "claude-code", role, None, "/r", "/p.md", resume=session or None, available=ALL_BINS)
        return built

    def test_old_presets_except_kimi_and_codex_give_exactly_the_golden_argv(self):
        # kimi переписан под Kimi Code CLI 2.x (F4), codex исправлен по итогам спайка S0-CX (-- перед промптом,
        # -m и режим на продолжении, --json): их argv 1.1.1 не сохраняется, новый — в test_engine_presets_f4.py
        for preset, spec in GOLDEN["presets"].items():
            if preset in ("kimi", "codex"):
                self.assertNotEqual(ec.PRESETS[preset]["cmd"], spec["cmd"])
                continue
            for access in spec["access"]:
                for which in ("cmd", "resume"):
                    with self.subTest(preset=preset, access=access, which=which):
                        session = "sess-1" if which == "resume" else ""
                        built = self.build(preset, which, access, session)
                        fresh = built["session"] if (preset == "grok" and which == "cmd") else session
                        self.assertEqual(built["argv"], render(preset, which, access, session=fresh))

    def test_the_owner_config_gives_the_documented_risk_tester_command(self):
        cfg = ec.strip_comments(OWNER)
        built = ec.build_argv(cfg, "claude-code", "risk-tester", "risk-tester-1", "/run", "/run/engine/risk-tester-1/draft.prompt.md",
                              task="3", report="risk-1.md", available={"cursor-agent"})
        self.assertEqual(built["argv"], ["cursor-agent", "-p", "--trust", "--output-format", "json", "--model", "composer-2.5",
                                         "--sandbox", "enabled", "-f", "--", "{prompt}"])
        self.assertTrue(built["command"].startswith('PATH="$HOME/.local/bin:$PATH" '))
        self.assertIn("--role risk-tester-1 --run-dir /run --prompt /run/engine/risk-tester-1/draft.prompt.md --engine cursor "
                      "--task 3 --report risk-1.md --timeout 3600 -- cursor-agent", built["command"])
        self.assertTrue(built["command"].endswith("-- '{prompt}'"))

    def test_the_model_comes_from_the_role_then_the_engine_then_the_preset(self):
        cfg = {"roles": {"unified-reviewer": {"engine": "cursor", "model": "role-model"}}}
        self.assertIn("role-model", self.model_args(cfg))
        cfg = {"roles": {"unified-reviewer": "cursor"}, "engines": {"cursor": {"model": "engine-model"}}}
        self.assertIn("engine-model", self.model_args(cfg))
        self.assertIn("cursor-grok-4.6-xhigh", self.model_args({"roles": {"unified-reviewer": "cursor"}}))

    def model_args(self, cfg):
        return ec.build_argv(cfg, "claude-code", "unified-reviewer", None, "/r", "/p.md", available=ALL_BINS)["argv"]

    def test_grok_gets_a_fresh_uuid_session_for_the_script_and_for_itself(self):
        built = ec.build_argv({"roles": {"codebase-researcher": "grok"}}, "claude-code", "codebase-researcher", None, "/r", "/p.md", available=ALL_BINS)
        session = built["session"]
        self.assertEqual(len(session), 36)
        self.assertEqual(built["argv"][built["argv"].index("--session-id") + 1], session)
        self.assertEqual(built["launcher"][built["launcher"].index("--session") + 1], session)
        resumed = ec.build_argv({"roles": {"codebase-researcher": "grok"}}, "claude-code", "codebase-researcher", None, "/r", "/p.md",
                                resume=session, available=ALL_BINS)
        self.assertEqual(resumed["launcher"][resumed["launcher"].index("--session") + 1], session)

    def test_timeouts_follow_the_role_registry_and_can_be_overridden(self):
        def tmo(role, **kw):
            built = ec.build_argv({"roles": {role: "codex"}}, "claude-code", role, None, "/r", "/p.md", available=ALL_BINS, **kw)
            return built["launcher"][built["launcher"].index("--timeout") + 1] if "--timeout" in built["launcher"] else None
        self.assertEqual((tmo("unified-reviewer"), tmo("coder"), tmo("risk-tester"), tmo("codebase-researcher")),
                         ("1800", "3600", "3600", None))
        self.assertEqual(tmo("coder", timeout=7200), "7200")

    def test_a_native_role_has_no_command(self):
        self.assertIsNone(ec.build_argv({"roles": {"spec-verifier": "cursor"}}, "claude-code", "coder", None, "/r", "/p.md", available=ALL_BINS))

    def test_a_participant_name_may_differ_from_the_role(self):
        built = ec.build_argv({"roles": {"second-reviewer": {"engine": "cursor", "model": "gpt-x"}}}, "claude-code", "second-reviewer",
                              "second-reviewer-3", "/r", "/p.md", available=ALL_BINS)
        self.assertEqual(built["launcher"][built["launcher"].index("--role") + 1], "second-reviewer-3")


class Validate(unittest.TestCase):
    def test_the_shipped_example_and_the_owner_config_are_valid(self):
        example = json.loads((HERE.parents[1] / "hosts" / "claude-code" / "agent-teams.example.json").read_text(encoding="utf-8"))
        for cfg in (ec.strip_comments(example), ec.strip_comments(OWNER)):
            errors, _ = ec.validate(cfg)
            self.assertEqual(errors, [])

    def test_schema_errors_are_found(self):
        bad = {"enabled": "yes", "fallback": "sometimes", "roles": {"coder": 5, "tech-lead": {"engine": "codex", "sandbox": "full"}},
               "engines": {"x": {"model": "m"}, "cursor": {"mode_flags": "--mode ask"}},
               "profiles": {"p": {"lead": {"host": "nowhere"}, "fingerprint": "md5:1"}}, "active_profile": "ghost"}
        errors, _ = ec.validate(bad)
        codes = {(e["path"], e["code"]) for e in errors}
        self.assertEqual(codes, {("enabled", "type"), ("fallback", "value"), ("roles.coder", "type"),
                                 ("roles.tech-lead.sandbox", "value"), ("engines.x", "no_cmd"),
                                 ("engines.cursor.mode_flags", "type"), ("profiles.p.lead", "value"),
                                 ("profiles.p.fingerprint", "value"), ("active_profile", "missing_profile")})

    def test_tolerance_of_1_1_x_is_kept_unknown_and_foreign_roles_are_warnings_or_silent(self):
        errors, warnings = ec.validate({"roles": {"research-critic": "cursor", "security-reviewer": "cursor", "ghost-role": "cursor"}, "future": 1})
        self.assertEqual(errors, [])
        self.assertEqual(sorted(w["code"] for w in warnings), ["retired_role", "unknown_key", "unknown_role"])


class HostDetection(unittest.TestCase):
    def test_flag_beats_env_and_env_marks_claude_code(self):
        self.assertEqual(ec.detect_host("kimi", {"CLAUDECODE": "1"}), ("kimi", "flag"))
        self.assertEqual(ec.detect_host(None, {"CLAUDECODE": "1"}), ("claude-code", "env"))

    def test_without_a_flag_or_marker_the_host_is_unknown(self):
        with self.assertRaises(ValueError):
            ec.detect_host(None, {})
        with self.assertRaises(ValueError):
            ec.detect_host("emacs", {})


class CliSmoke(unittest.TestCase):
    def setUp(self):
        self.home = tmpdir(self)
        (self.home / "c.json").write_text(json.dumps(OWNER), encoding="utf-8")
        self.env = {**os.environ, "HOME": str(self.home)}
        self.env.pop("SUPERTICK_CONFIG", None)
        self.env.pop("CLAUDECODE", None)

    def cli(self, *args):
        return subprocess.run([sys.executable, "-I", str(SCRIPT), *args], capture_output=True, text=True, env=self.env, timeout=60)

    def test_resolve_prints_the_engines_section_for_state_md(self):
        out = self.cli("resolve", "--host", "claude-code", "--available", "cursor-agent", "--config", str(self.home / "c.json"))
        self.assertEqual(out.returncode, 0, out.stderr)
        lines = out.stdout.splitlines()
        self.assertEqual(lines[0], "## Engines")
        self.assertTrue(lines[1].startswith("- launcher: ") and lines[1].endswith("/run-engine.sh"))
        self.assertIn("- risk-tester: cursor composer-2.5", lines)
        self.assertIn("- second-reviewer: cursor gpt-5.6-sol-high", lines)

    def test_resolve_stop_exits_1(self):
        (self.home / "f.json").write_text(json.dumps({"fallback": "fail", "roles": {"coder": "codex"}}), encoding="utf-8")
        out = self.cli("resolve", "--host", "claude-code", "--available", "", "--config", str(self.home / "f.json"))
        self.assertEqual((out.returncode, out.stdout.strip()), (1, "STOP: engine_missing:coder:codex"))

    def test_engines_off_gives_an_empty_table(self):
        out = self.cli("resolve", "--host", "claude-code", "--engines=off", "--available", "cursor-agent", "--json", "--config", str(self.home / "c.json"))
        self.assertEqual(json.loads(out.stdout)["table"], {})

    def test_argv_prints_a_ready_command_and_exits_3_for_native_roles(self):
        run, prompt = self.home / "run", self.home / "p.md"
        out = self.cli("argv", "risk-tester", "--host", "claude-code", "--available", "cursor-agent", "--run-dir", str(run),
                       "--prompt", str(prompt), "--config", str(self.home / "c.json"))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("-- cursor-agent -p --trust --output-format json --model composer-2.5 --sandbox enabled -f -- '{prompt}'", out.stdout)
        native = self.cli("argv", "coder", "--host", "claude-code", "--available", "cursor-agent", "--run-dir", str(run),
                          "--prompt", str(prompt), "--json", "--config", str(self.home / "c.json"))
        self.assertEqual((native.returncode, json.loads(native.stdout)), (3, {"native": True, "role": "coder"}))

    def test_resume_without_an_id_reads_session_txt(self):
        run = self.home / "run"
        (run / "engine" / "risk-tester").mkdir(parents=True)
        (run / "engine" / "risk-tester" / "session.txt").write_text("abc-123\n", encoding="utf-8")
        out = self.cli("argv", "risk-tester", "--host", "claude-code", "--available", "cursor-agent", "--run-dir", str(run),
                       "--prompt", str(self.home / "p.md"), "--resume", "--json", "--config", str(self.home / "c.json"))
        argv = json.loads(out.stdout)["argv"]
        self.assertEqual(argv[argv.index("--resume") + 1], "abc-123")
        missing = self.cli("argv", "risk-tester", "--host", "claude-code", "--available", "cursor-agent", "--run-dir", str(self.home / "none"),
                           "--prompt", "p", "--resume", "--config", str(self.home / "c.json"))
        self.assertEqual(missing.returncode, 2)

    def test_validate_exit_codes(self):
        ok = self.cli("validate", "--config", str(self.home / "c.json"))
        self.assertEqual(ok.returncode, 0, ok.stdout)
        (self.home / "bad.json").write_text(json.dumps({"fallback": "x"}), encoding="utf-8")
        bad = self.cli("validate", "--json", "--config", str(self.home / "bad.json"))
        self.assertEqual((bad.returncode, json.loads(bad.stdout)["ok"]), (1, False))

    def test_host_without_flag_or_marker_fails_with_a_hint(self):
        out = self.cli("host", "--config", str(self.home / "c.json"))
        self.assertEqual(out.returncode, 2)
        self.assertIn("--host", out.stderr)
        self.env["CLAUDECODE"] = "1"
        self.assertEqual(self.cli("host", "--config", str(self.home / "c.json")).returncode, 0)


if __name__ == "__main__":
    unittest.main()
