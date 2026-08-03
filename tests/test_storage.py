from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from astrbot_plugin_llm_trace.storage import TraceStore


class TraceStoreTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = TraceStore(Path(self.temp_dir.name) / "traces.db")
        await self.store.start()

    async def asyncTearDown(self) -> None:
        await self.store.stop()
        self.temp_dir.cleanup()

    async def test_persists_and_reads_a_trace(self) -> None:
        now = time.time()
        self.assertTrue(
            self.store.submit(
                {
                    "kind": "start",
                    "run_id": "run-1",
                    "span_id": "span-1",
                    "session_id": "session",
                    "sender_id": "sender",
                    "sender_name": "name",
                    "platform": "webchat",
                    "provider_id": "provider",
                    "model": "model",
                    "timestamp": now,
                }
            )
        )
        self.store.submit(
            {
                "kind": "event",
                "run_id": "run-1",
                "sequence": 1,
                "timestamp": now + 0.1,
                "event_type": "llm.request",
                "payload": {"prompt": "hello"},
            }
        )
        self.store.submit(
            {
                "kind": "finish",
                "run_id": "run-1",
                "timestamp": now + 0.2,
                "status": "completed",
            }
        )
        await self.store.queue.join()

        runs = await self.store.list_runs()
        detail = await self.store.get_run("run-1")

        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["event_count"], 1)
        self.assertIsNotNone(detail)
        self.assertEqual(detail["status"], "completed")
        self.assertEqual(detail["events"][0]["payload"]["prompt"], "hello")

    async def test_delete_and_clear(self) -> None:
        now = time.time()
        for run_id in ("run-1", "run-2"):
            self.store.submit(
                {
                    "kind": "start",
                    "run_id": run_id,
                    "span_id": run_id,
                    "session_id": "session",
                    "sender_id": "sender",
                    "sender_name": "name",
                    "platform": "webchat",
                    "timestamp": now,
                }
            )
        await self.store.queue.join()

        self.assertTrue(await self.store.delete_run("run-1"))
        self.assertIsNone(await self.store.get_run("run-1"))
        await self.store.clear()
        self.assertEqual(await self.store.list_runs(), [])


if __name__ == "__main__":
    unittest.main()

