"""Request pacing, counting and the daily cap (no API calls).

Run with: python -m unittest tests.test_rate_limit
"""

import tempfile

import httpx
import unittest
from pathlib import Path
from types import SimpleNamespace

from experiments.common.rate_limit import RequestBudgetExceeded, RequestPacer, retry_generate_on_429


class _RateLimited(Exception):
    code = 429

    def __str__(self):
        return "429 RESOURCE_EXHAUSTED. Please retry in 0.0s."


class RateLimitTest(unittest.TestCase):
    def _client(self, outcomes):
        calls = []

        def generate_content(**kwargs):
            calls.append(kwargs)
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        return SimpleNamespace(client=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))), calls

    def test_counts_every_attempt_including_429(self):
        with tempfile.TemporaryDirectory() as tmp:
            pacer = RequestPacer(max_rpm=6000, daily_cap=10, ledger_path=str(Path(tmp) / "ledger.json"))
            client, calls = self._client([_RateLimited(), "ok"])
            retry_generate_on_429(client, pacer=pacer, default_delay=0)
            self.assertEqual(client.client.models.generate_content(model="m"), "ok")
            self.assertEqual(len(calls), 2)
            self.assertEqual(pacer.used_today(), 2)

    def test_retries_503(self):
        unavailable = _RateLimited()
        unavailable.code = 503
        with tempfile.TemporaryDirectory() as tmp:
            pacer = RequestPacer(max_rpm=6000, daily_cap=10, ledger_path=str(Path(tmp) / "ledger.json"))
            client, calls = self._client([unavailable, "ok"])
            retry_generate_on_429(client, pacer=pacer, default_delay=0)
            self.assertEqual(client.client.models.generate_content(), "ok")
            self.assertEqual(pacer.used_today(), 2)

    def test_retries_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            pacer = RequestPacer(max_rpm=6000, daily_cap=10, ledger_path=str(Path(tmp) / "ledger.json"))
            client, calls = self._client([httpx.ReadTimeout("timed out"), "ok"])
            retry_generate_on_429(client, pacer=pacer, default_delay=0)
            self.assertEqual(client.client.models.generate_content(), "ok")
            self.assertEqual(pacer.used_today(), 2)

    def test_cap_blocks_request_before_sending(self):
        with tempfile.TemporaryDirectory() as tmp:
            pacer = RequestPacer(max_rpm=6000, daily_cap=1, ledger_path=str(Path(tmp) / "ledger.json"))
            client, calls = self._client(["ok", "ok"])
            retry_generate_on_429(client, pacer=pacer)
            client.client.models.generate_content()
            with self.assertRaises(RequestBudgetExceeded):
                client.client.models.generate_content()
            self.assertEqual(len(calls), 1)
            self.assertTrue(pacer.exhausted)


if __name__ == "__main__":
    unittest.main()
