import os
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_anthropic import ChatAnthropic
from langchain_mistralai import ChatMistralAI
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

from backend.contract_chat_agent import get_agent

from typing import Any


class LLMManager:
    agents = {}

    def __init__(self):
        self.init_agents()

    def init_agents(self):
        if os.getenv("OPENAI_API_KEY"):
            self.agents["gpt-4o"] = get_agent(ChatOpenAI(model="gpt-4o", temperature=0))

        if os.getenv("GOOGLE_API_KEY"):
            # request_timeout/max_retries: without these, this client relies
            # entirely on the SDK's own defaults (no timeout at all, 6
            # retries with growing exponential backoff on a 429) - see
            # backend/agents/llm_extraction_service.py's get_default_llm for
            # the same fix and its full rationale (found via live testing
            # and an overnight benchmark run that hung for ~3 hours on a
            # stalled connection).
            self.agents["gemini-2.5-pro"] = get_agent(
                ChatGoogleGenerativeAI(
                    model="gemini-2.5-pro",
                    temperature=0,
                    request_timeout=120,
                    max_retries=1,
                )
            )
            self.agents["gemini-2.5-flash"] = get_agent(
                ChatGoogleGenerativeAI(
                    model="gemini-2.5-flash",
                    temperature=0,
                    request_timeout=120,
                    max_retries=1,
                )
            )


        if os.getenv("ANTHROPIC_API_KEY"):
            self.agents["sonnet-3.5"] = get_agent(
                ChatAnthropic(
                    model="claude-3-5-sonnet-latest",
                    temperature=0,
                )
            )

        if os.getenv("MISTRAL_API_KEY"):
            self.agents["mistral-large"] = get_agent(
                ChatMistralAI(
                    model="mistral-large-latest",
                )
            )
        logger.info(f"Loaded {len(self.agents)} llms.")

    def get_model_by_name(self, name: str):
        try:
            return self.agents[name]
        except KeyError:
            raise ValueError(f"The model {name} wasn't initiated")
