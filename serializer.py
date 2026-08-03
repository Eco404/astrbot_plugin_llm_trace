from __future__ import annotations

import dataclasses
import enum
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


class TraceSerializer:
    """Convert AstrBot runtime objects into bounded, redacted JSON values."""

    _SENSITIVE_KEY = re.compile(
        r"(?:^|[_-])(?:api[_-]?key|authorization|cookie|password|passwd|secret|"
        r"access[_-]?token|refresh[_-]?token|credential)(?:$|[_-])|^token$",
        re.IGNORECASE,
    )
    _DATA_URL = re.compile(r"^data:([^;,]+)?(?:;[^,]*)?,", re.IGNORECASE)

    def __init__(
        self,
        *,
        capture_content: bool = True,
        hash_identity: bool = True,
        max_text_chars: int = 20000,
    ) -> None:
        self.capture_content = capture_content
        self.hash_identity = hash_identity
        self.max_text_chars = max(256, min(int(max_text_chars), 1_000_000))

    def identity(self, value: Any) -> str:
        """Return a display-safe identity.

        Args:
            value: Session or sender identity.

        Returns:
            The original identity or a stable shortened SHA-256 digest.
        """
        text = str(value or "")
        if not text or not self.hash_identity:
            return text
        return f"sha256:{hashlib.sha256(text.encode()).hexdigest()[:16]}"

    def convert(
        self,
        value: Any,
        *,
        key: str = "",
        _seen: set[int] | None = None,
    ) -> Any:
        """Convert an arbitrary runtime value to JSON-compatible data.

        Args:
            value: Runtime value to convert.
            key: Parent field name, used for redaction.
            _seen: Object identifiers already traversed.

        Returns:
            A bounded JSON-compatible representation.
        """
        if key and self._SENSITIVE_KEY.search(key):
            return "[REDACTED]"
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return self._string(value)
        if isinstance(value, bytes):
            return {
                "type": "bytes",
                "size": len(value),
                "sha256": hashlib.sha256(value).hexdigest(),
            }
        if isinstance(value, enum.Enum):
            return self.convert(value.value, key=key, _seen=_seen)
        if isinstance(value, Path):
            return self._string(str(value))

        seen = _seen if _seen is not None else set()
        obj_id = id(value)
        if obj_id in seen:
            return "[CYCLE]"
        seen.add(obj_id)
        try:
            if hasattr(value, "model_dump"):
                try:
                    value = value.model_dump(mode="json")
                except TypeError:
                    value = value.model_dump()
            elif dataclasses.is_dataclass(value) and not isinstance(value, type):
                value = {
                    field.name: getattr(value, field.name)
                    for field in dataclasses.fields(value)
                }

            if isinstance(value, Mapping):
                return {
                    str(item_key): self.convert(
                        item_value,
                        key=str(item_key),
                        _seen=seen,
                    )
                    for item_key, item_value in value.items()
                }
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                return [self.convert(item, _seen=seen) for item in value]
            if hasattr(value, "__dict__"):
                return self.convert(vars(value), key=key, _seen=seen)
            return self._string(repr(value))
        except Exception as exc:
            return f"[UNSERIALIZABLE: {type(exc).__name__}]"
        finally:
            seen.discard(obj_id)

    def messages(self, messages: Any) -> Any:
        """Serialize messages while honoring the content capture switch.

        Args:
            messages: Provider contexts or Agent messages.

        Returns:
            Serialized messages or metadata-only summaries.
        """
        converted = self.convert(messages)
        if self.capture_content:
            return converted
        if not isinstance(converted, list):
            return {"count": 0, "value_type": type(messages).__name__}
        summaries = []
        for message in converted:
            if not isinstance(message, dict):
                summaries.append({"type": type(message).__name__})
                continue
            content = message.get("content")
            encoded = json.dumps(content, ensure_ascii=False, default=str).encode()
            summaries.append(
                {
                    "role": message.get("role"),
                    "content_chars": len(encoded.decode(errors="replace")),
                    "content_sha256": hashlib.sha256(encoded).hexdigest(),
                }
            )
        return summaries

    def content(self, value: Any) -> Any:
        """Serialize potentially sensitive content according to capture policy.

        Args:
            value: Message body, prompt, tool result, or raw model response.

        Returns:
            Full converted content or a length and hash summary.
        """
        converted = self.convert(value)
        if self.capture_content or value is None:
            return converted
        encoded = json.dumps(converted, ensure_ascii=False, default=str).encode()
        return {
            "type": "content_summary",
            "bytes": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
        }

    def character_count(self, value: Any, _seen: set[int] | None = None) -> int:
        """Count characters contained in a runtime value before truncation.

        Args:
            value: Message content, model object, mapping, or sequence.
            _seen: Object identifiers already traversed.

        Returns:
            Sum of all string character counts in the value.
        """
        if value is None or isinstance(value, (bool, int, float, enum.Enum)):
            return 0
        if isinstance(value, str):
            return len(value)
        if isinstance(value, bytes):
            return len(value)

        seen = _seen if _seen is not None else set()
        obj_id = id(value)
        if obj_id in seen:
            return 0
        seen.add(obj_id)
        try:
            if hasattr(value, "model_dump"):
                try:
                    value = value.model_dump(mode="python")
                except TypeError:
                    value = value.model_dump()
            elif dataclasses.is_dataclass(value) and not isinstance(value, type):
                value = {
                    field.name: getattr(value, field.name)
                    for field in dataclasses.fields(value)
                }
            if isinstance(value, Mapping):
                return sum(
                    self.character_count(item, seen) for item in value.values()
                )
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                return sum(self.character_count(item, seen) for item in value)
            if hasattr(value, "__dict__"):
                return self.character_count(vars(value), seen)
            return 0
        except Exception:
            return 0
        finally:
            seen.discard(obj_id)

    def message_character_count(self, messages: Any) -> int:
        """Count user-visible and model-visible text inside message contexts.

        Args:
            messages: Provider context dictionaries or Agent Message objects.

        Returns:
            Approximate character count excluding role/type metadata.
        """
        if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
            return self.character_count(messages)

        def count_message_value(value: Any) -> int:
            if isinstance(value, str):
                return len(value)
            if isinstance(value, Mapping):
                return sum(
                    count_message_value(item)
                    for key, item in value.items()
                    if key not in {"role", "type", "id", "tool_call_id"}
                )
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                return sum(count_message_value(item) for item in value)
            return self.character_count(value)

        total = 0
        for message in messages:
            if hasattr(message, "model_dump"):
                try:
                    message = message.model_dump(mode="python")
                except TypeError:
                    message = message.model_dump()
            if not isinstance(message, Mapping):
                total += self.character_count(message)
                continue
            for field in (
                "content",
                "reasoning_content",
                "tool_calls",
                "function_call",
            ):
                total += count_message_value(message.get(field))
        return total

    def _string(self, value: str) -> Any:
        match = self._DATA_URL.match(value)
        if match:
            return {
                "type": "data_url",
                "media_type": match.group(1) or "unknown",
                "chars": len(value),
                "sha256": hashlib.sha256(value.encode()).hexdigest(),
            }
        if len(value) <= self.max_text_chars:
            return value
        return {
            "type": "truncated_text",
            "text": value[: self.max_text_chars],
            "original_chars": len(value),
            "sha256": hashlib.sha256(value.encode()).hexdigest(),
        }
