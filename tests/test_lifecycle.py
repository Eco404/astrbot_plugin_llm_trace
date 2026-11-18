from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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
    plugin._fallback_state_by_run = {}
    plugin._initial_model_by_run = {}
    plugin._actual_model_by_run = {}
    plugin._subscribers = set()
    return plugin, store


class LifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def test_records_actual_response_model_without_probe(self) -> None:
        plugin, store = _plugin_with_active_run("run-model")
        plugin.config = {"capture_reasoning": True, "capture_raw_response": True}
        plugin._initial_model_by_run["run-model"] = "gemini-3.8-flash"
        event = _FakeEvent("run-model")
        response = LLMResponse(
            role="assistant",
            completion_text="fallback response",
            raw_completion={"model": "gemini-3.5-flash-lite"},
        )

        await plugin.capture_llm_response(event, response)

        fallback = [
            operation
            for operation in store.operations
            if operation.get("event_type") == "provider.fallback"
        ]
        self.assertEqual(len(fallback), 1)
        self.assertEqual(fallback[0]["payload"]["from_model"], "gemini-3.8-flash")
        self.assertEqual(
            fallback[0]["payload"]["to_model"], "gemini-3.5-flash-lite"
        )
        self.assertEqual(
            store.operations[-1]["payload"]["model"], "gemini-3.5-flash-lite"
        )

        await plugin.capture_agent_done(
            event, SimpleNamespace(messages=[]), response
        )
        self.assertEqual(store.operations[-1]["actual_model"], "gemini-3.5-flash-lite")

    async def test_error_trace_detail_uses_success_envelope(self) -> None:
        plugin = LLMTracePlugin.__new__(LLMTracePlugin)
        run = {
            "run_id": "run-error",
            "status": "error",
            "events": [{"sequence": 1, "event_type": "agent.error"}],
        }
        plugin.store = SimpleNamespace(get_run=AsyncMock(return_value=run))
        plugin._ensure_ready = AsyncMock()

        with patch(
            "astrbot_plugin_llm_trace.main.request",
            SimpleNamespace(method="GET"),
        ):
            response = await plugin.page_run_detail("run-error")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            json.loads(response.body),
            {"status": "ok", "data": run},
        )

    async def test_stream_emits_forwardable_heartbeat(self) -> None:
        plugin = LLMTracePlugin.__new__(LLMTracePlugin)
        plugin._subscribers = set()
        plugin._terminating = False

        async def timeout_immediately(awaitable, *_args, **_kwargs):
            awaitable.close()
            raise TimeoutError

        with (
            patch(
                "astrbot_plugin_llm_trace.main.asyncio.wait_for",
                new=timeout_immediately,
            ),
            patch(
                "astrbot_plugin_llm_trace.main.stream_response",
                side_effect=lambda events: events,
            ),
        ):
            stream = await plugin.page_stream()
            self.assertEqual(await anext(stream), "event: ready\ndata: {}\n\n")
            self.assertEqual(
                await anext(stream),
                "event: heartbeat\ndata: {}\n\n",
            )
            await stream.aclose()

        self.assertEqual(plugin._subscribers, set())

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
