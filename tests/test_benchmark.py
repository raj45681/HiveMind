import unittest

from hivemind.benchmark import run


class RetrievalBenchmarkTests(unittest.IsolatedAsyncioTestCase):
    async def test_disposable_fixture_reports_recall_scope_budget_and_schema(self):
        report = await run(".", distractors=25)
        self.assertEqual(report["fixture"], "disposable synthetic vault")
        self.assertEqual(report["paid_model_calls"], 0)
        cases = {case["case"]: case for case in report["search"]["cases"]}
        self.assertTrue(cases["exact"]["hit"])
        self.assertFalse(cases["paraphrase"]["hit"])
        self.assertTrue(cases["unrelated"]["hit"])
        self.assertTrue(cases["wrong_project"]["hit"])
        for budget in report["context"]["budgets"]:
            self.assertTrue(budget["target_included"])
            self.assertLessEqual(budget["estimated_tokens"], budget["budget_tokens"])
        self.assertEqual(report["tool_schemas"]["memory"]["tools"], 8)
        self.assertLess(report["tool_schemas"]["memory"]["json_bytes"],
                        report["tool_schemas"]["full"]["json_bytes"])


if __name__ == "__main__":
    unittest.main()
