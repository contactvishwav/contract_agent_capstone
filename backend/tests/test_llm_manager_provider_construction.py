"""
Real, confirmed bug found live: LLMManager.init_agents() passed
temperature=0 to every ChatAnthropic construction. Anthropic deprecated
the temperature/top_p/top_k sampling parameters entirely for Claude Opus
4.7+ and Claude Sonnet 5 (shipped 2026-06-30) - the parameter's mere
presence in the request now returns a real 400, "`temperature` is
deprecated for this model," confirmed via a direct API call independent of
this engagement's other work. This broke every Contract Chat turn on
claude-sonnet-5, text or image, predating the image-attachment feature
entirely.

Fixed by omitting `temperature` from the ChatAnthropic constructor call
(Anthropic's own migration guidance: omit it, don't substitute another
value - the deprecation triggers on presence, not value). Every other
provider is unaffected and still gets a real temperature=0 override.
"""

import unittest
from unittest.mock import MagicMock, patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    import backend.llm_manager as llm_manager_module


class AnthropicConstructionOmitsTemperatureTests(unittest.TestCase):
    def test_chat_anthropic_is_constructed_without_a_temperature_kwarg(self):
        with patch.object(llm_manager_module, "ChatAnthropic") as MockChatAnthropic, \
             patch.object(llm_manager_module, "get_agent", return_value=MagicMock()):
            MockChatAnthropic.return_value = MagicMock()
            llm_manager_module.LLMManager()

        # ANTHROPIC_API_KEY is genuinely configured in this dev environment
        # - if that ever stops being true, this test should be revisited,
        # not silently pass on an unconstructed mock.
        MockChatAnthropic.assert_called_once()
        _, kwargs = MockChatAnthropic.call_args
        self.assertNotIn(
            "temperature", kwargs,
            "temperature must never be passed to ChatAnthropic - Anthropic 400s on its mere presence "
            "for Claude Opus 4.7+/Sonnet 5, regardless of the value given",
        )
        self.assertEqual(kwargs.get("model"), "claude-sonnet-5")

    def test_other_providers_still_receive_a_real_temperature_override(self):
        """Regression guard: this fix must be Anthropic-only - Gemini/
        OpenAI/Mistral construction is unaffected and unchanged."""
        with patch.object(llm_manager_module, "ChatGoogleGenerativeAI") as MockGoogle, \
             patch.object(llm_manager_module, "ChatOpenAI") as MockOpenAI, \
             patch.object(llm_manager_module, "get_agent", return_value=MagicMock()):
            MockGoogle.return_value = MagicMock()
            MockOpenAI.return_value = MagicMock()
            llm_manager_module.LLMManager()

        MockGoogle.assert_called_once()
        _, google_kwargs = MockGoogle.call_args
        self.assertEqual(google_kwargs.get("temperature"), 0)

        MockOpenAI.assert_called_once()
        _, openai_kwargs = MockOpenAI.call_args
        self.assertEqual(openai_kwargs.get("temperature"), 0)


if __name__ == "__main__":
    unittest.main()
