from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from src.cache import TTLCache
from src.intelligence import gemini
from src.request_queue_service import RequestQueueService


class RateLimitProtectionTests(unittest.IsolatedAsyncioTestCase):
    def test_cache_reports_hit_ratio(self) -> None:
        cache = TTLCache()

        self.assertIsNone(cache.get("missing"))
        cache.set("provider:item", {"ok": True}, 30)
        self.assertIsNotNone(cache.get("provider:item"))

        stats = cache.stats("provider:")
        self.assertEqual(stats["entries"], 1)
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["misses"], 1)
        self.assertEqual(stats["cache_hit_ratio"], 0.5)

    async def test_request_queue_coalesces_identical_requests(self) -> None:
        queue = RequestQueueService()
        calls = 0

        async def factory() -> dict:
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.05)
            return {"ok": True, "calls": calls}

        results = await asyncio.gather(
            queue.run("gemini", "same-prompt", factory, max_concurrency=1),
            queue.run("gemini", "same-prompt", factory, max_concurrency=1),
            queue.run("gemini", "same-prompt", factory, max_concurrency=1),
        )

        self.assertEqual(calls, 1)
        self.assertTrue(all(result["ok"] for result in results))
        self.assertGreaterEqual(queue.stats("gemini").get("coalesced", 0), 2)

    async def test_user_cooldown_blocks_user_temporarily(self) -> None:
        queue = RequestQueueService()
        self.assertFalse(queue.user_status("gemini", "user-1").active)

        queue.cooldown_user("gemini", "user-1", 30, reason="429")
        status = queue.user_status("gemini", "user-1")

        self.assertTrue(status.active)
        self.assertGreater(status.wait_seconds, 0)
        self.assertIn("429", status.reason)

    async def test_gemini_answer_question_uses_15_minute_cache(self) -> None:
        calls = 0

        async def fake_call(**kwargs):
            nonlocal calls
            calls += 1
            return {"usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 4}}, "resposta cacheada", 100

        unique_question = "Como esta o jogo cache-test-123?"
        context = {"game_id": "cache-test-123", "market": "gols", "minute": 61}

        with patch.object(gemini, "_call_gemini", side_effect=fake_call):
            first = await gemini.answer_question(unique_question, context, "fake-key", "models/test")
            second = await gemini.answer_question(unique_question, context, "fake-key", "models/test")

        self.assertEqual(first, "resposta cacheada")
        self.assertEqual(second, "resposta cacheada")
        self.assertEqual(calls, 1)


if __name__ == "__main__":
    unittest.main()
