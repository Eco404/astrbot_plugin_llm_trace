from __future__ import annotations

import json
import unittest
from dataclasses import dataclass

from astrbot_plugin_llm_trace.serializer import TraceSerializer


@dataclass
class SampleUsage:
    input_tokens: int
    output_tokens: int


class TraceSerializerTest(unittest.TestCase):
    def test_redacts_credentials_but_preserves_usage_fields(self) -> None:
        serializer = TraceSerializer()

        converted = serializer.convert(
            {
                "api_key": "secret-key",
                "authorization": "Bearer secret",
                "usage": SampleUsage(input_tokens=12, output_tokens=4),
            }
        )

        self.assertEqual(converted["api_key"], "[REDACTED]")
        self.assertEqual(converted["authorization"], "[REDACTED]")
        self.assertEqual(converted["usage"]["input_tokens"], 12)
        self.assertEqual(converted["usage"]["output_tokens"], 4)

    def test_summarizes_data_urls(self) -> None:
        serializer = TraceSerializer()

        converted = serializer.convert("data:image/png;base64,AAAA")

        self.assertEqual(converted["type"], "data_url")
        self.assertEqual(converted["media_type"], "image/png")
        self.assertNotIn("AAAA", json.dumps(converted))

    def test_summarizes_mutable_binary_buffers(self) -> None:
        serializer = TraceSerializer()

        converted = serializer.convert(
            {
                "buffer": bytearray(b"abc"),
                "view": memoryview(b"def"),
            }
        )

        self.assertEqual(converted["buffer"]["type"], "bytearray")
        self.assertEqual(converted["buffer"]["size"], 3)
        self.assertEqual(converted["view"]["type"], "memoryview")
        self.assertEqual(converted["view"]["size"], 3)
        self.assertEqual(serializer.character_count(bytearray(b"abc")), 3)
        self.assertEqual(serializer.character_count(memoryview(b"def")), 3)

    def test_truncates_oversized_sequences(self) -> None:
        serializer = TraceSerializer()

        converted = serializer.convert(
            list(range(TraceSerializer._MAX_COLLECTION_ITEMS + 1))
        )

        self.assertEqual(len(converted), TraceSerializer._MAX_COLLECTION_ITEMS + 1)
        self.assertEqual(converted[-1]["type"], "truncated_sequence")

    def test_metadata_mode_hides_message_content(self) -> None:
        serializer = TraceSerializer(capture_content=False)

        converted = serializer.messages(
            [{"role": "user", "content": "private message"}]
        )

        dumped = json.dumps(converted)
        self.assertEqual(converted[0]["role"], "user")
        self.assertNotIn("private message", dumped)
        self.assertIn("content_sha256", converted[0])

    def test_metadata_mode_summarizes_tool_content(self) -> None:
        serializer = TraceSerializer(capture_content=False)

        converted = serializer.content({"result": "private tool output"})

        self.assertEqual(converted["type"], "content_summary")
        self.assertNotIn("private tool output", json.dumps(converted))

    def test_truncates_large_text(self) -> None:
        serializer = TraceSerializer(max_text_chars=256)

        converted = serializer.convert("x" * 300)

        self.assertEqual(converted["type"], "truncated_text")
        self.assertEqual(len(converted["text"]), 256)
        self.assertEqual(converted["original_chars"], 300)

    def test_counts_message_content_without_role_metadata(self) -> None:
        serializer = TraceSerializer()

        count = serializer.message_character_count(
            [
                {"role": "user", "content": "你好"},
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "hello"}],
                    "reasoning_content": "abc",
                },
            ]
        )

        self.assertEqual(count, 2 + 5 + 3)


if __name__ == "__main__":
    unittest.main()
