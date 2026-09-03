from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any


ProbeStart = Callable[[Any, str, tuple[Any, ...], dict[str, Any]], str | None]
ProbeResponse = Callable[[str, Any], None]
ProbeFailure = Callable[[str, BaseException], None]
ProbeCancelled = Callable[[str, BaseException | None], None]


@dataclass
class _ProviderBinding:
    provider: Any
    text_chat: Callable[..., Awaitable[Any]]
    text_chat_stream: Callable[..., AsyncGenerator[Any, None]] | None
    wrapped_text_chat: Callable[..., Awaitable[Any]]
    wrapped_text_chat_stream: Callable[..., AsyncGenerator[Any, None]] | None


class ProviderProbe:
    """Experimental, reversible instrumentation for loaded chat providers."""

    def __init__(
        self,
        get_providers: Callable[[], list[Any]],
        on_start: ProbeStart,
        on_response: ProbeResponse,
        on_failure: ProbeFailure,
        on_cancelled: ProbeCancelled,
        logger: Any,
    ) -> None:
        self._get_providers = get_providers
        self._on_start = on_start
        self._on_response = on_response
        self._on_failure = on_failure
        self._on_cancelled = on_cancelled
        self._logger = logger
        self._bindings: dict[int, _ProviderBinding] = {}

    @property
    def binding_count(self) -> int:
        return len(self._bindings)

    def sync(self, *, enabled: bool) -> int:
        """Wrap current providers, and restore stale bindings when disabled."""
        if not enabled:
            self.restore()
            return 0

        try:
            providers = list(self._get_providers())
        except Exception as exc:
            self._logger.warning("LLM Trace provider probe scan failed: %s", exc)
            return self.binding_count

        active_ids = {id(provider) for provider in providers}
        for provider_id, binding in list(self._bindings.items()):
            if provider_id not in active_ids:
                self._restore_binding(binding)
                self._bindings.pop(provider_id, None)

        for provider in providers:
            provider_id = id(provider)
            if provider_id in self._bindings:
                continue
            binding = self._bind(provider)
            if binding is not None:
                self._bindings[provider_id] = binding
        return self.binding_count

    def restore(self) -> None:
        for binding in tuple(self._bindings.values()):
            self._restore_binding(binding)
        self._bindings.clear()

    def _bind(self, provider: Any) -> _ProviderBinding | None:
        original_chat = getattr(provider, "text_chat", None)
        if not callable(original_chat):
            return None
        original_stream = getattr(provider, "text_chat_stream", None)
        if not callable(original_stream):
            original_stream = None

        @wraps(original_chat)
        async def wrapped_text_chat(*args: Any, **kwargs: Any) -> Any:
            run_id = self._safe_start(provider, "text_chat", args, kwargs)
            if run_id is None:
                return await original_chat(*args, **kwargs)
            try:
                response = await original_chat(*args, **kwargs)
            except asyncio.CancelledError as exc:
                self._safe_cancelled(run_id, exc)
                raise
            except BaseException as exc:
                self._safe_failure(run_id, exc)
                raise
            self._safe_response(run_id, response)
            return response

        wrapped_stream: Callable[..., AsyncGenerator[Any, None]] | None = None
        if original_stream is not None:

            @wraps(original_stream)
            async def wrapped_text_chat_stream(
                *args: Any, **kwargs: Any
            ) -> AsyncGenerator[Any, None]:
                run_id = self._safe_start(provider, "text_chat_stream", args, kwargs)
                if run_id is None:
                    async for response in original_stream(*args, **kwargs):
                        yield response
                    return

                final_response: Any = None
                outcome_recorded = False
                try:
                    async for response in original_stream(*args, **kwargs):
                        final_response = response
                        yield response
                except asyncio.CancelledError as exc:
                    self._safe_cancelled(run_id, exc)
                    outcome_recorded = True
                    raise
                except GeneratorExit as exc:
                    self._safe_cancelled(run_id, exc)
                    outcome_recorded = True
                    raise
                except BaseException as exc:
                    self._safe_failure(run_id, exc)
                    outcome_recorded = True
                    raise
                else:
                    self._safe_response(run_id, final_response)
                    outcome_recorded = True
                finally:
                    if not outcome_recorded:
                        self._safe_cancelled(run_id, None)

            wrapped_stream = wrapped_text_chat_stream

        try:
            provider.text_chat = wrapped_text_chat
            if wrapped_stream is not None:
                provider.text_chat_stream = wrapped_stream
        except Exception as exc:
            self._logger.warning(
                "LLM Trace provider probe could not wrap %s: %s",
                type(provider).__name__,
                exc,
            )
            try:
                provider.text_chat = original_chat
            except Exception:
                pass
            return None

        return _ProviderBinding(
            provider=provider,
            text_chat=original_chat,
            text_chat_stream=original_stream,
            wrapped_text_chat=wrapped_text_chat,
            wrapped_text_chat_stream=wrapped_stream,
        )

    def _restore_binding(self, binding: _ProviderBinding) -> None:
        try:
            if (
                getattr(binding.provider, "text_chat", None)
                is binding.wrapped_text_chat
            ):
                binding.provider.text_chat = binding.text_chat
            if (
                binding.wrapped_text_chat_stream is not None
                and getattr(binding.provider, "text_chat_stream", None)
                is binding.wrapped_text_chat_stream
            ):
                binding.provider.text_chat_stream = binding.text_chat_stream
        except Exception as exc:
            self._logger.warning(
                "LLM Trace provider probe could not restore %s: %s",
                type(binding.provider).__name__,
                exc,
            )

    def _safe_start(
        self,
        provider: Any,
        call_kind: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str | None:
        try:
            return self._on_start(provider, call_kind, args, kwargs)
        except Exception as exc:
            self._logger.warning("LLM Trace provider probe start failed: %s", exc)
            return None

    def _safe_response(self, run_id: str, response: Any) -> None:
        try:
            self._on_response(run_id, response)
        except Exception as exc:
            self._logger.warning("LLM Trace provider probe response failed: %s", exc)

    def _safe_failure(self, run_id: str, error: BaseException) -> None:
        try:
            self._on_failure(run_id, error)
        except Exception as exc:
            self._logger.warning(
                "LLM Trace provider probe error handling failed: %s", exc
            )

    def _safe_cancelled(self, run_id: str, error: BaseException | None) -> None:
        try:
            self._on_cancelled(run_id, error)
        except Exception as exc:
            self._logger.warning(
                "LLM Trace provider probe cancellation failed: %s", exc
            )
