"""Отчёт начинается с первой строки по протоколу, ledger знает модель вызова (121-567, V1, прогон R3).

R3, 14.11.2026: рецензент на cursor ответил «Читаю бриф роли…VERDICT: task #1 — APPROVED» — вступление
приклеено к вердикту. Приёмка честно отклонила отчёт по первой строке, лишний круг ревью и приёмки.
Теперь run-engine.sh кладёт в reports/ текст с первой известной строки-шапки; ответ движка как был
остаётся в .out.result.md, а сколько срезано — в ledger (`report_trimmed`).
Там же: ТЗ п. 7 (R3) — ledger показывает движок и модель каждого вызова; модель берётся из `--model`/`-m`.

Запуск: python3 -m unittest discover -s tools/tests -p test_report_head.py
"""
import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

from test_engine_presets_f4 import CORE, wait_done
from test_scripts import executable, tmpdir

FAKE = "#!/usr/bin/env bash\ncat \"$REPLY_FILE\"\n"


@unittest.skipUnless(shutil.which("setsid"), "нет setsid")
class ReportHeadAndModel(unittest.TestCase):
    def launch(self, reply, model_args=("--model", "grok-4.7-xhigh-fast")):
        root = tmpdir(self)
        (root / "bin").mkdir()
        executable(root / "bin" / "fake-engine", FAKE)
        (root / "reply.txt").write_text(reply, encoding="utf-8")
        prompt = root / "p.md"
        prompt.write_text("review\n", encoding="utf-8")
        run = root / "run"
        env = {**os.environ, "PATH": f"{root / 'bin'}:{os.environ['PATH']}", "REPLY_FILE": str(root / "reply.txt")}
        launched = subprocess.run(["bash", str(CORE / "run-engine.sh"), "--role", "task1-review", "--run-dir", str(run),
                                   "--prompt", str(prompt), "--engine", "fake", "--task", "1",
                                   "--report", "review-task1-r1.md", "--", "fake-engine", *model_args, "-p", "{prompt}"],
                                  capture_output=True, text=True, timeout=60, env=env)
        fields, result = wait_done(self, launched)
        ledger = [json.loads(l) for l in (run / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
        report = Path(fields["report"]).read_text(encoding="utf-8")
        return report, result, ledger

    def test_glued_preamble_is_cut_from_the_report_but_kept_in_the_reply(self):
        reply = "Читаю бриф роли и материалы ревью.VERDICT: task #1 — APPROVED\nCRITICAL 0 · MAJOR 0 · MINOR 0\n"
        report, result, ledger = self.launch(reply)
        self.assertTrue(report.startswith("VERDICT: task #1 — APPROVED\n"), report)
        self.assertEqual(result, reply)
        self.assertEqual(ledger[-1]["report_trimmed"], str(reply.index("VERDICT")))

    def test_preamble_on_its_own_lines(self):
        report, _, _ = self.launch("Сначала прогоню команды.\n\nACCEPT: task #2 — PASS\n| a | b |\n")
        self.assertTrue(report.startswith("ACCEPT: task #2 — PASS"), report)

    def test_a_quoted_head_in_the_preamble_is_not_the_head_line(self):
        reply = "Отчёт начну с `VERDICT: task #1` по брифу.\nVERDICT: task #1 — CHANGES REQUESTED\n"
        report, _, _ = self.launch(reply)
        self.assertEqual(report, "VERDICT: task #1 — CHANGES REQUESTED\n")

    def test_a_clean_reply_and_a_reply_without_a_head_are_copied_as_is(self):
        for reply in ("VERDICT: task #1 — APPROVED\nок\n", "  READY FOR REVIEW: task #3\n", "просто текст без шапки\n"):
            with self.subTest(reply=reply):
                report, _, ledger = self.launch(reply)
                self.assertEqual(report, reply)
                self.assertNotIn("report_trimmed", ledger[-1])

    def test_ledger_names_the_model_of_each_call(self):
        for args, model in ((("--model", "gpt-5.6-terra"), "gpt-5.6-terra"), (("-m", "kimi-code/k3"), "kimi-code/k3"),
                            (("--model=composer-2.5",), "composer-2.5"), ((), None)):
            with self.subTest(args=args):
                _, _, ledger = self.launch("VERDICT: task #1 — APPROVED\n", model_args=args)
                self.assertEqual([e.get("model") for e in ledger], [model, model])
                self.assertEqual(ledger[0]["engine"], "fake")


if __name__ == "__main__":
    unittest.main()
