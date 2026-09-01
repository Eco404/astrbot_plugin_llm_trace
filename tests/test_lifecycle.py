from __future__ import annotations

import unittest
from types import SimpleNamespace

from astrbot.api.provider import LLMResponse

from astrbot_plugin_llm_trace.main import LLMTracePlugin
from astrbot_plugin_llm_trace.serializer import TraceSerializer


class _FakeStore:
    def __init__(self) -> None:
        self.operations: list[dict] = []

    def submit(self, operation: dict) -> bool:
        self.operations.append(operation)
        return True


class _FakeEvent:
    def __init__(
        self, run_id: str, result_content_type: str = "GENERAL_RESULT"
    ) -> None:
        self._extras = {"_llm_trace_run_id": run_id}
        self._result = SimpleNamespace(
            result_content_type=SimpleNamespace(name=result_content_type)
        )

    def get_extra(self, key: str):
        return self._extras.get(key)

    def get_result(self):
        return self._result


def _plugin_with_active_run(run_id: str) -> tuple[LLMTracePlugin, _FakeStore]:
    plugin = LLMTracePlugin.__new__(LLMTracePlugin)
    store = _FakeStore()
    plugin.enabled = True
    plugin.serializer = TraceSerializer()
    plugin.store = store
    plugin._sequence_by_run = {run_id: 0}
    plugin._subscribers = set()
    return plugin, store


class LifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def test_agent_error_finishes_trace(self) -> None:
        plugin, store = _plugin_with_active_run("run-error")
        event = _FakeEvent("run-error")

        await plugin.capture_agent_done(
            event,
            SimpleNamespace(messages=[]),
            LLMResponse(role="err", completion_text="provider failed"),
        )

        self.assertEqual(store.operations[-1]["kind"], "finish")
        self.assertEqual(store.operations[-1]["status"], "error")
        self.assertNotIn("run-error", plugin._sequence_by_run)

    async def test_sent_response_without_agent_done_finishes_as_error(self) -> None:
        plugin, store = _plugin_with_active_run("run-fallback")
        event = _FakeEvent("run-fallback", "AGENT_RUNNER_ERROR")

        await plugin.cleanup_finished_event(event)

        self.assertEqual(store.operations[-2]["event_type"], "agent.error")
        self.assertEqual(
            store.operations[-2]["payload"]["result_content_type"],
            "AGENT_RUNNER_ERROR",
        )
        self.assertEqual(store.operations[-1]["kind"], "finish")
        self.assertEqual(store.operations[-1]["status"], "error")
        self.assertNotIn("run-fallback", plugin._sequence_by_run)

        await plugin.cleanup_finished_event(event)

        self.assertEqual(
            sum(operation["kind"] == "finish" for operation in store.operations),
            1,
        )


if __name__ == "__main__":
    unittest.main()
