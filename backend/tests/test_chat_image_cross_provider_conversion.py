"""
Cross-provider conversion regression test (ADR-008 Stage 2). This is the
test guarding this whole design's central guarantee: the SAME content-block
dict ({"type": "image", "base64": ..., "mime_type": ...} - langchain_core's
documented v1 standard) is fed through each of ChatGoogleGenerativeAI/
ChatOpenAI/ChatAnthropic's own real, currently-installed conversion
functions - not mocks, not this codebase's own code - and each must
produce its own correct real wire shape with the exact original bytes
intact. If a future langchain/langchain-google-genai/langchain-openai/
langchain-anthropic upgrade changes any of these three functions' behavior,
this is what catches it, not a silent, expensive-to-diagnose provider-side
image failure.

No API keys, no network calls: _convert_to_parts/_format_message_content/
_format_messages are pure conversion functions, called directly.
"""

import base64
import unittest

from langchain_core.messages import HumanMessage

# A real, minimal 1x1 PNG - same fixture bytes as
# test_chat_attachment_storage.py, kept independent (no cross-file import)
# since this file tests a different boundary (langchain's provider
# conversion, not our own storage layer).
PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844410000000100000001080600000"
    "01f15c4890000000a49444154789c6360000002000100dcba79820000"
    "000049454e44ae426082"
)
B64_PNG = base64.b64encode(PNG_1X1).decode("ascii")

CONTENT_BLOCKS = [
    {"type": "text", "text": "What is in this image?"},
    {"type": "image", "base64": B64_PNG, "mime_type": "image/png"},
]


class GeminiConversionTests(unittest.TestCase):
    def test_produces_inline_data_blob_with_the_exact_original_bytes(self):
        from langchain_google_genai.chat_models import _convert_to_parts

        parts = _convert_to_parts(CONTENT_BLOCKS)

        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[0].text, "What is in this image?")
        self.assertIsNotNone(parts[1].inline_data, "image block must become Gemini's real inline_data/Blob shape")
        self.assertEqual(parts[1].inline_data.mime_type, "image/png")
        self.assertEqual(parts[1].inline_data.data, PNG_1X1)


class OpenAiConversionTests(unittest.TestCase):
    def test_produces_image_url_data_uri_with_the_exact_original_bytes(self):
        from langchain_openai.chat_models.base import _format_message_content

        formatted = _format_message_content(CONTENT_BLOCKS)

        self.assertEqual(formatted[0], {"type": "text", "text": "What is in this image?"})
        image_block = formatted[1]
        self.assertEqual(image_block["type"], "image_url", "must become OpenAI's real Chat Completions image_url shape")
        url = image_block["image_url"]["url"]
        self.assertTrue(url.startswith("data:image/png;base64,"))
        self.assertEqual(base64.b64decode(url.split(",", 1)[1]), PNG_1X1)


class AnthropicConversionTests(unittest.TestCase):
    def test_produces_source_base64_block_with_the_exact_original_bytes(self):
        from langchain_anthropic.chat_models import _format_messages

        _, messages = _format_messages([HumanMessage(content=CONTENT_BLOCKS)])

        content = messages[0]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "What is in this image?"})
        image_block = content[1]
        self.assertEqual(image_block["type"], "image", "must become Claude's real source-based image shape")
        self.assertEqual(image_block["source"]["type"], "base64")
        self.assertEqual(image_block["source"]["media_type"], "image/png")
        self.assertEqual(base64.b64decode(image_block["source"]["data"]), PNG_1X1)


class SameContentBlockDictAcrossAllThreeProvidersTests(unittest.TestCase):
    """The single most important assertion in this file: literally the same
    content-block list survives all three real, independent conversions
    correctly - proving the codebase's own message-building code never
    needs to know or branch on which provider it's building for."""

    def test_one_shared_content_block_list_converts_correctly_everywhere(self):
        from langchain_google_genai.chat_models import _convert_to_parts
        from langchain_openai.chat_models.base import _format_message_content
        from langchain_anthropic.chat_models import _format_messages

        shared_blocks = [
            {"type": "text", "text": "Describe this image."},
            {"type": "image", "base64": B64_PNG, "mime_type": "image/png"},
        ]

        gemini_parts = _convert_to_parts(shared_blocks)
        self.assertEqual(gemini_parts[1].inline_data.data, PNG_1X1)

        openai_formatted = _format_message_content(shared_blocks)
        openai_data_uri = openai_formatted[1]["image_url"]["url"]
        self.assertEqual(base64.b64decode(openai_data_uri.split(",", 1)[1]), PNG_1X1)

        _, claude_messages = _format_messages([HumanMessage(content=shared_blocks)])
        claude_data = claude_messages[0]["content"][1]["source"]["data"]
        self.assertEqual(base64.b64decode(claude_data), PNG_1X1)

        # And the shared list object itself was never mutated by any of the
        # three conversions - a real risk if any of them mutated blocks
        # in place instead of copying, which would corrupt a second
        # provider's conversion of the same list.
        self.assertEqual(shared_blocks, [
            {"type": "text", "text": "Describe this image."},
            {"type": "image", "base64": B64_PNG, "mime_type": "image/png"},
        ])


if __name__ == "__main__":
    unittest.main()
