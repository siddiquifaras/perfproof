"""Kick off a one-agent crew repeatedly against a local fake model.

The model answers at once with a final answer, so the run time is
crewAI's own per-kickoff overhead, which the reference change cuts.
No network call is made. Prints the last result so both trees can be
compared for equal output.
"""

import sys
from typing import Any

from crewai import Agent, Crew, Task
from crewai.llms.base_llm import BaseLLM

KICKOFFS = 50


class FakeLLM(BaseLLM):
    """A model that always returns the same final answer."""

    def call(self, messages: Any, *args: Any, **kwargs: Any) -> str:
        """Return a fixed final answer."""
        return "Thought: I know the answer\nFinal Answer: 42"

    async def acall(self, messages: Any, *args: Any, **kwargs: Any) -> str:
        """Return a fixed final answer."""
        return self.call(messages)

    def supports_function_calling(self) -> bool:
        """Report no function calling."""
        return False

    def supports_stop_words(self) -> bool:
        """Report no stop words."""
        return False

    def get_context_window_size(self) -> int:
        """Report a fixed context window."""
        return 4096


llm = FakeLLM(model="fake-model")
result = None
for _ in range(KICKOFFS):
    agent = Agent(
        role="Answerer",
        goal="Answer the question",
        backstory="You answer questions.",
        llm=llm,
        verbose=False,
    )
    task = Task(
        description="What is six times seven?",
        expected_output="A number",
        agent=agent,
    )
    result = Crew(agents=[agent], tasks=[task], verbose=False).kickoff()
sys.stdout.write(f"{result}\n")
