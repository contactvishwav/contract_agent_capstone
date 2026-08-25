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

Also covers the later lazy-loading fix (real root cause of a memory-
pressure cold-start confirmed on 3 consecutive production deployments -
see docker-compose.prod.yml's worker.depends_on comment and
_LazyModelMap's docstring): ChatOpenAI/ChatAnthropic/ChatMistralAI are
now local imports inside LLMManager._construct_raw, not module-level, so
these tests patch the real SDK classes at their own source module
(langchain_openai.ChatOpenAI etc.) rather than on llm_manager itself -
the standard technique for intercepting a deferred `from X import Y`
statement. They also explicitly index into `raw_llms[...]` to force the
lazy build, since only DEFAULT_MODEL (gemini-2.5-flash) is still built
eagerly at LLMManager() construction time.
"""

import unittest
from unittest.mock import MagicMock, patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    import backend.llm_manager as llm_manager_module


class AnthropicConstructionOmitsTemperatureTests(unittest.TestCase):
    def test_chat_anthropic_is_constructed_without_a_temperature_kwarg(self):
        with patch("langchain_anthropic.ChatAnthropic") as MockChatAnthropic, \
             patch.object(llm_manager_module, "get_agent", return_value=MagicMock()):
            MockChatAnthropic.return_value = MagicMock()
            manager = llm_manager_module.LLMManager()
            # claude-sonnet-5 is fallback-only, not DEFAULT_MODEL - force
            # the real lazy build instead of asserting on an untouched mock.
            manager.raw_llms["claude-sonnet-5"]

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
             patch("langchain_openai.ChatOpenAI") as MockOpenAI, \
             patch.object(llm_manager_module, "get_agent", return_value=MagicMock()):
            MockGoogle.return_value = MagicMock()
            MockOpenAI.return_value = MagicMock()
            manager = llm_manager_module.LLMManager()
            # gpt-4o is fallback-only too - force its lazy build.
            manager.raw_llms["gpt-4o"]

        # gemini-2.5-flash is DEFAULT_MODEL, built eagerly - no manual force needed.
        MockGoogle.assert_called_once()
        _, google_kwargs = MockGoogle.call_args
        self.assertEqual(google_kwargs.get("temperature"), 0)

        MockOpenAI.assert_called_once()
        _, openai_kwargs = MockOpenAI.call_args
        self.assertEqual(openai_kwargs.get("temperature"), 0)


class LazyLoadingDefersNonDefaultProvidersTests(unittest.TestCase):
    """The actual regression these tests exist to prevent: OpenAI/
    Anthropic/Mistral must NOT be constructed at LLMManager() time -
    only DEFAULT_MODEL (gemini-2.5-flash) should be eager. This is the
    real fix for a memory-pressure cold-start that hit 3 consecutive
    production deployments (both backend and worker independently
    constructing all 3 configured providers simultaneously)."""

    def test_openai_and_anthropic_are_not_constructed_at_init_time(self):
        with patch.object(llm_manager_module, "ChatGoogleGenerativeAI") as MockGoogle, \
             patch("langchain_openai.ChatOpenAI") as MockOpenAI, \
             patch("langchain_anthropic.ChatAnthropic") as MockAnthropic, \
             patch.object(llm_manager_module, "get_agent", return_value=MagicMock()):
            MockGoogle.return_value = MagicMock()
            MockOpenAI.return_value = MagicMock()
            MockAnthropic.return_value = MagicMock()
            llm_manager_module.LLMManager()

        MockGoogle.assert_called_once()
        MockOpenAI.assert_not_called()
        MockAnthropic.assert_not_called()

    def test_openai_is_constructed_lazily_on_first_real_access_and_cached(self):
        with patch.object(llm_manager_module, "ChatGoogleGenerativeAI") as MockGoogle, \
             patch("langchain_openai.ChatOpenAI") as MockOpenAI, \
             patch.object(llm_manager_module, "get_agent", return_value=MagicMock()):
            MockGoogle.return_value = MagicMock()
            MockOpenAI.return_value = MagicMock()
            manager = llm_manager_module.LLMManager()

            MockOpenAI.assert_not_called()
            first = manager.get_raw_model_by_name("gpt-4o")
            MockOpenAI.assert_called_once()
            second = manager.get_raw_model_by_name("gpt-4o")
            # Still only built once - the second access reuses the cached
            # instance, doesn't reconstruct the client every call.
            MockOpenAI.assert_called_once()
            self.assertIs(first, second)

    def test_membership_and_keys_reflect_configured_models_before_any_build(self):
        """The whole point of _LazyModelMap over a sparse dict: `in`/
        `keys()` must report true availability immediately, not just
        whatever happens to have been built so far."""
        with patch.object(llm_manager_module, "ChatGoogleGenerativeAI") as MockGoogle, \
             patch("langchain_openai.ChatOpenAI") as MockOpenAI, \
             patch("langchain_anthropic.ChatAnthropic") as MockAnthropic, \
             patch.object(llm_manager_module, "get_agent", return_value=MagicMock()):
            MockGoogle.return_value = MagicMock()
            MockOpenAI.return_value = MagicMock()
            MockAnthropic.return_value = MagicMock()
            manager = llm_manager_module.LLMManager()

        self.assertIn("gpt-4o", manager.raw_llms)
        self.assertIn("claude-sonnet-5", manager.raw_llms)
        self.assertIn("gemini-2.5-flash", manager.raw_llms)
        self.assertEqual(
            set(manager.raw_llms.keys()),
            {"gemini-2.5-flash", "gpt-4o", "claude-sonnet-5"},
        )
        # None of that membership/keys check should have triggered a build.
        MockOpenAI.assert_not_called()
        MockAnthropic.assert_not_called()


if __name__ == "__main__":
    unittest.main()
