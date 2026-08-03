from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.api.web import error_response, json_response, request, stream_response
from astrbot.core.config.astrbot_config import AstrBotConfig

from .serializer import TraceSerializer
from .storage import TraceStore

PLUGIN_NAME = "astrbot_plugin_llm_trace"
PLUGIN_VERSION = "0.1.4"
RUN_ID_EXTRA_KEY = "_llm_trace_run_id"


@register(
    PLUGIN_NAME,
    "econeco",
    "Inspect AstrBot LLM requests, tool calls, and responses as a timeline.",
    PLUGIN_VERSION,
)
class LLMTracePlugin(Star):
    """Capture LLM lifecycle events without modifying the live request."""

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.context = context
        self.config = config
        self.enabled = bool(config.get("enabled", False))
        self.serializer = TraceSerializer(
            capture_content=bool(config.get("capture_content", True)),
            hash_identity=bool(config.get("hash_identity", True)),
            max_text_chars=int(config.get("max_text_chars", 20000)),
        )
        data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        self.store = TraceStore(
            data_dir / "traces.db",
            max_runs=int(config.get("max_runs", 200)),
            retention_hours=int(config.get("retention_hours", 24)),
            queue_size=int(config.get("queue_size", 1000)),
        )
        self._sequence_by_run: dict[str, int] = {}
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._terminating = False
        self._register_page_api()

    def _register_page_api(self) -> None:
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/status",
            self.page_status,
            ["GET", "POST"],
            "Read or update LLM Trace status",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/runs",
            self.page_runs,
            ["GET", "DELETE"],
            "List or clear LLM traces",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/runs/<run_id>",
            self.page_run_detail,
            ["GET", "DELETE"],
            "Read or delete one LLM trace",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/stream",
            self.page_stream,
            ["GET"],
            "Stream live LLM trace events",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/clear",
            self.page_clear,
            ["POST"],
            "Clear all LLM traces",
        )
        self.context.register_web_api(
            f"/{PLUGIN_NAME}/runs/<run_id>/delete",
            self.page_delete_run,
            ["POST"],
            "Delete one LLM trace",
        )

    async def _ensure_ready(self) -> None:
        await self.store.start()

    def _run_id(self, event: AstrMessageEvent, *, create: bool = False) -> str | None:
        run_id = event.get_extra(RUN_ID_EXTRA_KEY)
        if run_id:
            return str(run_id)
        if not create:
            return None
        trace = getattr(event, "trace", None)
        run_id = str(getattr(trace, "span_id", "") or uuid.uuid4())
        event.set_extra(RUN_ID_EXTRA_KEY, run_id)
        self._sequence_by_run[run_id] = 0
        return run_id

    def _record(self, run_id: str, event_type: str, payload: Any) -> None:
        sequence = self._sequence_by_run.get(run_id, 0) + 1
        self._sequence_by_run[run_id] = sequence
        timestamp = time.time()
        safe_payload = self.serializer.convert(payload)
        operation = {
            "kind": "event",
            "run_id": run_id,
            "sequence": sequence,
            "timestamp": timestamp,
            "event_type": event_type,
            "payload": safe_payload,
        }
        if not self.store.submit(operation):
            logger.warning(
                "LLM Trace write queue is unavailable; dropped event %s", event_type
            )
        live_message = json.dumps(
            {
                "run_id": run_id,
                "sequence": sequence,
                "timestamp": timestamp,
                "event_type": event_type,
                "payload": safe_payload,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        for subscriber in tuple(self._subscribers):
            try:
                subscriber.put_nowait(live_message)
            except asyncio.QueueFull:
                pass

    def _provider_metadata(self, event: AstrMessageEvent, req: ProviderRequest) -> dict:
        metadata = {"provider_id": "", "provider_type": "", "model": req.model or ""}
        try:
            provider = self.context.get_using_provider(umo=event.unified_msg_origin)
            if provider:
                meta = provider.meta()
                metadata.update(
                    provider_id=meta.id,
                    provider_type=meta.type,
                    model=req.model or provider.get_model(),
                )
        except Exception as exc:
            metadata["metadata_error"] = str(exc)
        return metadata

    def _tools(self, tool_set: Any) -> dict[str, Any]:
        if tool_set is None:
            return {"names": [], "schemas": [], "schema_chars": 0}
        names: list[str] = []
        schemas: Any = []
        try:
            names = list(tool_set.names())
        except Exception:
            names = []
        try:
            schemas = tool_set.get_func_desc_openai_style()
        except Exception:
            schemas = [repr(tool_set)]
        return {
            "names": names,
            "schemas": self.serializer.convert(schemas),
            "schema_chars": self.serializer.character_count(schemas),
        }

    def _request_snapshot(self, req: ProviderRequest) -> dict[str, Any]:
        conversation = getattr(req, "conversation", None)
        tools = self._tools(req.func_tool)
        prompt_chars = self.serializer.character_count(req.prompt)
        system_prompt_chars = self.serializer.character_count(req.system_prompt)
        context_chars = self.serializer.message_character_count(req.contexts)
        extra_user_chars = self.serializer.character_count(
            req.extra_user_content_parts
        )
        tool_schema_chars = tools["schema_chars"]
        tool_result_chars = self.serializer.character_count(req.tool_calls_result)
        return {
            "prompt": self.serializer.content(req.prompt),
            "system_prompt": self.serializer.content(req.system_prompt),
            "contexts": self.serializer.messages(req.contexts),
            "extra_user_content_parts": self.serializer.content(
                req.extra_user_content_parts
            ),
            "images": [
                {
                    "chars": len(str(ref)),
                    "sha256": hashlib.sha256(str(ref).encode()).hexdigest(),
                }
                for ref in (req.image_urls or [])
            ],
            "audio": [
                {
                    "chars": len(str(ref)),
                    "sha256": hashlib.sha256(str(ref).encode()).hexdigest(),
                }
                for ref in (req.audio_urls or [])
            ],
            "tools": tools,
            "tool_calls_result": self.serializer.content(req.tool_calls_result),
            "conversation_id": str(getattr(conversation, "cid", "") or ""),
            "session_id": self.serializer.identity(req.session_id),
            "model": req.model,
            "stats": {
                "prompt_chars": prompt_chars,
                "system_prompt_chars": system_prompt_chars,
                "context_chars": context_chars,
                "extra_user_chars": extra_user_chars,
                "tool_schema_chars": tool_schema_chars,
                "tool_result_chars": tool_result_chars,
                "input_chars": (
                    prompt_chars
                    + system_prompt_chars
                    + context_chars
                    + extra_user_chars
                    + tool_schema_chars
                    + tool_result_chars
                ),
            },
        }

    def _response_snapshot(self, resp: LLMResponse | None) -> dict[str, Any]:
        if resp is None:
            return {"response": None}
        reasoning = resp.reasoning_content
        if not bool(self.config.get("capture_reasoning", True)) and reasoning:
            reasoning = {
                "chars": len(reasoning),
                "sha256": hashlib.sha256(reasoning.encode()).hexdigest(),
            }
        raw_completion: Any = None
        if bool(self.config.get("capture_raw_response", True)):
            raw_completion = self.serializer.content(resp.raw_completion)
        return {
            "id": getattr(resp, "id", None),
            "role": resp.role,
            "is_chunk": resp.is_chunk,
            "completion_text": self.serializer.content(resp.completion_text),
            "reasoning_content": self.serializer.content(reasoning),
            "reasoning_signature": self.serializer.content(resp.reasoning_signature),
            "tool_names": self.serializer.convert(resp.tools_call_name),
            "tool_args": self.serializer.content(resp.tools_call_args),
            "tool_ids": self.serializer.convert(resp.tools_call_ids),
            "usage": self.serializer.convert(getattr(resp, "usage", None)),
            "raw_completion": raw_completion,
            "stats": {
                "output_chars": self.serializer.character_count(
                    resp.completion_text
                ),
                "reasoning_chars": self.serializer.character_count(
                    resp.reasoning_content
                ),
                "tool_args_chars": self.serializer.character_count(
                    resp.tools_call_args
                ),
            },
        }

    @filter.on_astrbot_loaded()
    async def initialize(self) -> None:
        """Initialize storage after AstrBot has loaded."""
        await self._ensure_ready()
        logger.info("LLM Trace initialized; capture enabled=%s", self.enabled)

    @filter.on_llm_request(priority=-1_000_000)
    async def capture_llm_request(
        self, event: AstrMessageEvent, req: ProviderRequest
    ) -> None:
        """Capture the final public ProviderRequest snapshot."""
        if not self.enabled or self._terminating:
            return
        await self._ensure_ready()
        run_id = self._run_id(event, create=True)
        if run_id is None:
            return
        metadata = self._provider_metadata(event, req)
        timestamp = time.time()
        self.store.submit(
            {
                "kind": "start",
                "run_id": run_id,
                "span_id": str(getattr(getattr(event, "trace", None), "span_id", "")),
                "session_id": self.serializer.identity(event.unified_msg_origin),
                "sender_id": self.serializer.identity(event.get_sender_id()),
                "sender_name": self.serializer.identity(event.get_sender_name()),
                "platform": str(event.get_platform_name()),
                "provider_id": metadata["provider_id"],
                "model": metadata["model"],
                "timestamp": timestamp,
            }
        )
        self._record(
            run_id,
            "llm.request",
            {"provider": metadata, "request": self._request_snapshot(req)},
        )

    @filter.on_agent_begin()
    async def capture_agent_begin(
        self, event: AstrMessageEvent, run_context: Any
    ) -> None:
        """Record the Agent context after the public request hook."""
        if not self.enabled:
            return
        run_id = self._run_id(event)
        if run_id:
            self._record(
                run_id,
                "agent.begin",
                {"messages": self.serializer.messages(run_context.messages)},
            )

    @filter.on_using_llm_tool()
    async def capture_tool_start(
        self,
        event: AstrMessageEvent,
        tool: Any,
        tool_args: dict | None,
    ) -> None:
        """Record a tool invocation before execution."""
        if not self.enabled:
            return
        run_id = self._run_id(event)
        if run_id:
            self._record(
                run_id,
                "tool.start",
                {
                    "name": getattr(tool, "name", type(tool).__name__),
                    "args": self.serializer.content(tool_args),
                    "stats": {
                        "args_chars": self.serializer.character_count(tool_args)
                    },
                },
            )

    @filter.on_llm_tool_respond()
    async def capture_tool_end(
        self,
        event: AstrMessageEvent,
        tool: Any,
        tool_args: dict | None,
        tool_result: Any,
    ) -> None:
        """Record a tool result after execution."""
        if not self.enabled:
            return
        run_id = self._run_id(event)
        if run_id:
            self._record(
                run_id,
                "tool.end",
                {
                    "name": getattr(tool, "name", type(tool).__name__),
                    "args": self.serializer.content(tool_args),
                    "result": self.serializer.content(tool_result),
                    "stats": {
                        "args_chars": self.serializer.character_count(tool_args),
                        "result_chars": self.serializer.character_count(tool_result),
                    },
                },
            )

    @filter.on_llm_response()
    async def capture_llm_response(
        self, event: AstrMessageEvent, resp: LLMResponse
    ) -> None:
        """Record the normalized final Provider response."""
        if not self.enabled:
            return
        run_id = self._run_id(event)
        if run_id:
            self._record(run_id, "llm.response", self._response_snapshot(resp))

    @filter.on_agent_done()
    async def capture_agent_done(
        self,
        event: AstrMessageEvent,
        run_context: Any,
        resp: LLMResponse,
    ) -> None:
        """Finish a trace after the Agent lifecycle is complete."""
        if not self.enabled:
            return
        run_id = self._run_id(event)
        if not run_id:
            return
        self._record(
            run_id,
            "agent.done",
            {
                "messages": self.serializer.messages(run_context.messages),
                "message_count": len(run_context.messages),
                "response_id": getattr(resp, "id", None),
            },
        )
        self.store.submit(
            {
                "kind": "finish",
                "run_id": run_id,
                "timestamp": time.time(),
                "status": "completed" if resp and resp.role != "err" else "error",
            }
        )
        self._sequence_by_run.pop(run_id, None)

    @filter.after_message_sent()
    async def cleanup_finished_event(self, event: AstrMessageEvent) -> None:
        """Release correlation state for requests stopped outside the Agent."""
        run_id = self._run_id(event)
        if run_id:
            self._sequence_by_run.pop(run_id, None)

    @filter.command_group("llmtrace")
    def llmtrace():
        """Manage LLM Trace capture."""

    @filter.permission_type(filter.PermissionType.ADMIN)
    @llmtrace.command("status")
    async def trace_status(self, event: AstrMessageEvent):
        """Show LLM Trace runtime status."""
        await self._ensure_ready()
        yield event.plain_result(
            f"LLM Trace: {'已启用' if self.enabled else '已停用'}\n"
            f"待写入: {self.store.queue.qsize()}\n"
            f"已丢弃: {self.store.dropped_records}\n"
            f"写入错误: {self.store.write_errors}"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @llmtrace.command("on")
    async def trace_on(self, event: AstrMessageEvent):
        """Enable LLM Trace capture."""
        self.enabled = True
        self.config["enabled"] = True
        self.config.save_config()
        yield event.plain_result("LLM Trace 已启用。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @llmtrace.command("off")
    async def trace_off(self, event: AstrMessageEvent):
        """Disable LLM Trace capture."""
        self.enabled = False
        self.config["enabled"] = False
        self.config.save_config()
        yield event.plain_result("LLM Trace 已停用。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @llmtrace.command("clear")
    async def trace_clear(self, event: AstrMessageEvent):
        """Delete all persisted LLM traces."""
        await self.store.clear()
        yield event.plain_result("LLM Trace 数据已清空。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @llmtrace.command("probe")
    async def trace_probe(self, event: AstrMessageEvent):
        """Show capture routing information for the current chat."""
        plugins = event.plugins_name
        included = plugins is None or "*" in plugins or PLUGIN_NAME in plugins
        try:
            provider = self.context.get_using_provider(umo=event.unified_msg_origin)
            provider_label = (
                f"{provider.meta().id} / {provider.get_model()}"
                if provider
                else "未选择"
            )
        except Exception as exc:
            provider_label = f"读取失败: {type(exc).__name__}: {exc}"
        yield event.plain_result(
            "LLM Trace 会话自检\n"
            f"UMO: {event.unified_msg_origin}\n"
            f"消息类型: {event.get_message_type().value}\n"
            f"群号: {event.get_group_id() or '-'}\n"
            f"平台: {event.get_platform_name()} / {event.get_platform_id()}\n"
            f"已唤醒: {event.is_wake_up()}\n"
            f"@或唤醒词: {event.is_at_or_wake_command}\n"
            f"默认LLM被禁止: {event.call_llm}\n"
            f"当前插件集: {plugins if plugins is not None else ['*']}\n"
            f"Trace在插件集中: {included}\n"
            f"采集开关: {self.enabled}\n"
            f"当前Provider: {provider_label}"
        )

    async def page_status(self):
        if request.method == "POST":
            payload = await request.json(default={})
            enabled = payload.get("enabled")
            if not isinstance(enabled, bool):
                return error_response("enabled must be a boolean", status_code=400)
            self.enabled = enabled
            self.config["enabled"] = enabled
            self.config.save_config()
        await self._ensure_ready()
        return json_response(
            {
                "enabled": self.enabled,
                "queue_size": self.store.queue.qsize(),
                "dropped_records": self.store.dropped_records,
                "write_errors": self.store.write_errors,
                "capture_content": self.serializer.capture_content,
                "hash_identity": self.serializer.hash_identity,
                "version": PLUGIN_VERSION,
            }
        )

    async def page_runs(self):
        await self._ensure_ready()
        if request.method == "DELETE":
            await self.store.clear()
            return json_response({"deleted": "all"})
        limit = request.query.get("limit", 50, type=int)
        offset = request.query.get("offset", 0, type=int)
        query = request.query.get("query", "", type=str)
        runs = await self.store.list_runs(limit=limit, offset=offset, query=query)
        return json_response({"items": runs, "limit": limit, "offset": offset})

    async def page_run_detail(self, run_id: str):
        await self._ensure_ready()
        if len(run_id) > 128:
            return error_response("invalid run id", status_code=400)
        if request.method == "DELETE":
            deleted = await self.store.delete_run(run_id)
            if not deleted:
                return error_response("trace not found", status_code=404)
            return json_response({"deleted": run_id})
        run = await self.store.get_run(run_id)
        if run is None:
            return error_response("trace not found", status_code=404)
        return json_response(run)

    async def page_stream(self):
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)

        async def events() -> AsyncGenerator[str, None]:
            try:
                yield "event: ready\ndata: {}\n\n"
                while not self._terminating:
                    try:
                        message = await asyncio.wait_for(queue.get(), timeout=15)
                        yield f"data: {message}\n\n"
                    except TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                self._subscribers.discard(queue)

        return stream_response(events())

    async def page_clear(self):
        await self.store.clear()
        logger.info("LLM Trace cleared all stored runs")
        return json_response({"deleted": "all"})

    async def page_delete_run(self, run_id: str):
        if len(run_id) > 128:
            return error_response("invalid run id", status_code=400)
        deleted = await self.store.delete_run(run_id)
        if not deleted:
            return error_response("trace not found", status_code=404)
        logger.info("LLM Trace deleted run %s", run_id)
        return json_response({"deleted": run_id})

    async def terminate(self) -> None:
        """Flush pending trace writes during plugin unload."""
        self._terminating = True
        self._subscribers.clear()
        await self.store.stop()
