import unittest
from pathlib import Path

from hivemind.verify import verify


ROOT = Path(__file__).resolve().parents[1]


class VerifyTests(unittest.IsolatedAsyncioTestCase):
    async def test_disposable_cross_client_handoff_and_drift(self):
        result = await verify(ROOT)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["model_calls"], 0)
        self.assertLessEqual(result["estimated_brief_tokens"], 1000)
        self.assertTrue(any("Git drift" in check for check in result["checks"]))


if __name__ == "__main__":
    unittest.main()
