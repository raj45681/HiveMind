import unittest
from contextlib import redirect_stdout
import io
import json
import sys
from unittest.mock import patch, AsyncMock

from hivemind.benchmark import run
import hive


class RetrievalBenchmarkTests(unittest.IsolatedAsyncioTestCase):
    async def test_disposable_fixture_reports_recall_scope_budget_and_schema(self):
        report = await run(".", distractors=25)
        self.assertEqual(report["fixture"], "disposable synthetic vault")
        self.assertEqual(report["paid_model_calls"], 0)
        self.assertTrue(report["ok"], report["failures"])
        self.assertGreaterEqual(report["case_count"], 15)
        cases = {case["case"]: case for case in report["search"]["cases"]}
        self.assertTrue(cases["exact"]["hit"])
        self.assertFalse(cases["paraphrase"]["hit"])
        self.assertTrue(cases["unrelated"]["hit"])
        self.assertTrue(cases["unrelated_domain"]["hit"])
        self.assertTrue(cases["unrelated_near"]["hit"])
        self.assertTrue(cases["wrong_project"]["hit"])
        for label in ("upload_exact", "ambiguous", "conflicting_archive", "candidate_excluded", "draft_excluded",
                      "archive_excluded", "literal_project", "project_prefix", "empty", "stopwords"):
            self.assertTrue(cases[label]["hit"], label)
        self.assertEqual(report["search"]["excluded_results"], 0)
        self.assertEqual(report["indexing"]["unchanged"]["read_notes"], 0)
        self.assertEqual(report["indexing"]["one_edit"]["read_notes"], 1)
        for budget in report["context"]["budgets"]:
            self.assertTrue(budget["target_included"])
            self.assertLessEqual(budget["estimated_tokens"], budget["budget_tokens"])
        self.assertEqual(report["tool_schemas"]["memory"]["tools"], 11)
        self.assertLess(report["tool_schemas"]["memory"]["json_bytes"],
                        report["tool_schemas"]["full"]["json_bytes"])


class BenchmarkCLITests(unittest.TestCase):
    def test_check_fails_when_report_contains_a_retrieval_regression(self):
        report = {"ok": False, "failures": ["wrong_project"]}
        with patch.object(sys, "argv", ["hive.py", "benchmark", "--check"]), \
                patch.object(hive, "execute", AsyncMock(return_value=report)), redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as raised:
                hive.main()
        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(json.loads(output.getvalue())["failures"], ["wrong_project"])


if __name__ == "__main__":
    unittest.main()
