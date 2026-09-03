from __future__ import annotations

import asyncio
import json
import logging
import unittest
from types import SimpleNamespace

from astrbot.api.provider import LLMResponse

from astrbot_plugin_llm_trace.main import LLMTracePlugin
from astrbot_plugin_llm_trace.provider_probe import ProviderProbe
from astrbot_plugin_llm_trace.serializer import TraceSerializer


class _FakeStore:
    def __init__(self) -> None:
        self.operations: list[dict] = []

    def submit(self, operation: dict) -> bool:
        self.operations.append(operation)
        return True


class _FakeProvider:
    def __init__(self) -> None:
        self.provider_config = {"id": "probe-provider", "type": "fake"}
        self.calls: list[dict] = []
        self.error: Exception | None = None
        self.stream_completes = False

    def meta(self):
        return SimpleNamespace(
            id=self.provider_config["id"], type="fake", model="fake-model"
        )

    def get_model(self) -> str:
        return "fake-model"

    async def text_chat(self, **kwargs) -> LLMResponse:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return LLMResponse(role="assistant", completion_text="probe complete")

    async def text_chat_stream(self, **kwargs):
        self.calls.append(kwargs)
        yield LLMResponse(role="assistant", completion_text="chunk", is_chunk=True)
        if self.stream_completes:
            yield LLMResponse(role="assistant", completion_text="stream complete")
            return
        await asyncio.Event().wait()


def _probe_plugin(
    providers: list[_FakeProvider], *, probe_enabled: bool
) -> tuple[LLMTracePlugin, _FakeStore]:
    plugin = LLMTracePlugin.__new__(LLMTracePlugin)
    store = _FakeStore()
    plugin.context = SimpleNamespace(get_all_providers=lambda: providers)
    plugin.config = {}
    plugin.enabled = True
    plugin.provider_probe_enabled = probe_enabled
    plugin.serializer = TraceSerializer()
    plugin.store = store
    plugin._sequence_by_run = {}
    plugin._subscribers = set()
    plugin._terminating = False
    plugin._provider_probe_task = None
    plugin._provider_probe = ProviderProbe(
        plugin.context.get_all_providers,
        plugin._start_provider_probe,
        plugin._finish_provider_probe,
        plugin._fail_provider_probe,
        plugin._cancel_provider_probe,
        logging.getLogger("test_provider_probe"),
    )
    return plugin, store


class ProviderProbeTest(unittest.IsolatedAsyncioTestCase):
    async def test_default_disabled_does_not_wrap_or_record(self) -> None:
        provider = _FakeProvider()
        plugin, store = _probe_plugin([provider], probe_enabled=False)
        original = provider.text_chat

        self.assertEqual(plugin._sync_provider_probe(), 0)
        response = await provider.text_chat(prompt="unobserved")

        self.assertEqual(response.completion_text, "probe complete")
        self.assertEqual(store.operations, [])
        self.assertEqual(provider.text_chat, original)

    async def test_records_direct_provider_call_and_redacts_extra_kwargs(self) -> None:
        provider = _FakeProvider()
        plugin, store = _probe_plugin([provider], probe_enabled=True)

        self.assertEqual(plugin._sync_provider_probe(), 1)
        response = await provider.text_chat(
            prompt="inspect this call",
            model="chosen-model",
            api_key="do-not-store",
        )

        self.assertEqual(response.completion_text, "probe complete")
        self.assertEqual(
            [op["kind"] for op in store.operations],
            ["start", "event", "event", "finish"],
        )
        self.assertEqual(store.operations[0]["platform"], "provider_probe")
        self.assertEqual(store.operations[0]["model"], "chosen-model")
        self.assertEqual(store.operations[1]["event_type"], "provider.request")
        self.assertEqual(
            store.operations[1]["payload"]["extra_kwargs"]["api_key"], "[REDACTED]"
        )
        self.assertEqual(store.operations[2]["event_type"], "provider.response")
        self.assertEqual(store.operations[3]["status"], "completed")
        self.assertEqual(plugin._sequence_by_run, {})

    async def test_summarizes_abort_signal_without_serializing_event_loop(self) -> None:
        provider = _FakeProvider()
        plugin, store = _probe_plugin([provider], probe_enabled=True)
        plugin._sync_provider_probe()
        abort_signal = asyncio.Event()
        waiter = asyncio.create_task(abort_signal.wait())
        await asyncio.sleep(0)

        try:
            await provider.text_chat(prompt="bounded call", abort_signal=abort_signal)
        finally:
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)

        request = store.operations[1]["payload"]
        self.assertEqual(
            request["extra_kwargs"]["abort_signal"],
            {"type": "Event", "is_set": False},
        )
        self.assertNotIn("_loop", str(request))
        self.assertLess(len(json.dumps(request)), 5_000)

    async def test_records_error_and_preserves_provider_exception(self) -> None:
        provider = _FakeProvider()
        provider.error = RuntimeError("provider unavailable")
        plugin, store = _probe_plugin([provider], probe_enabled=True)
        plugin._sync_provider_probe()

        with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
            await provider.text_chat(prompt="failing call")

        self.assertEqual(store.operations[-2]["event_type"], "provider.error")
        self.assertEqual(store.operations[-2]["payload"]["error_type"], "RuntimeError")
        self.assertEqual(store.operations[-1]["status"], "error")

    async def test_records_stream_close_and_restores_provider_method(self) -> None:
        provider = _FakeProvider()
        plugin, store = _probe_plugin([provider], probe_enabled=True)
        plugin._sync_provider_probe()
        wrapped = provider.text_chat_stream

        stream = provider.text_chat_stream(prompt="stream call")
        first = await anext(stream)
        self.assertTrue(first.is_chunk)
        await stream.aclose()

        self.assertEqual(store.operations[-2]["event_type"], "provider.cancelled")
        self.assertEqual(store.operations[-1]["status"], "error")
        plugin.provider_probe_enabled = False
        self.assertEqual(plugin._sync_provider_probe(), 0)
        self.assertIsNot(provider.text_chat_stream, wrapped)

    async def test_records_completed_stream_and_rescans_reloaded_provider(self) -> None:
        first_provider = _FakeProvider()
        providers = [first_provider]
        plugin, store = _probe_plugin(providers, probe_enabled=True)
        plugin._sync_provider_probe()
        first_wrapped = first_provider.text_chat

        replacement = _FakeProvider()
        replacement.provider_config["id"] = "replacement-provider"
        providers[:] = [replacement]
        self.assertEqual(plugin._sync_provider_probe(), 1)
        self.assertIsNot(first_provider.text_chat, first_wrapped)

        replacement.stream_completes = True
        responses = [
            response
            async for response in replacement.text_chat_stream(prompt="complete stream")
        ]

        self.assertEqual(len(responses), 2)
        self.assertEqual(store.operations[-2]["event_type"], "provider.response")
        self.assertEqual(store.operations[-1]["status"], "completed")
        self.assertEqual(store.operations[0]["provider_id"], "replacement-provider")


if __name__ == "__main__":
    unittest.main()
