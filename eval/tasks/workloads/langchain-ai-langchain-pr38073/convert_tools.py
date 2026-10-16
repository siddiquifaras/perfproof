"""Convert a set of tools to the OpenAI tool format repeatedly.

Each conversion reads ``BaseTool.tool_call_schema`` and its JSON
schema, which the reference change memoizes. Prints the number of
schemas built so both trees can be compared for equal output.
"""

import sys

from langchain_core.tools import tool
from langchain_core.utils.function_calling import convert_to_openai_tool


def _make(i: int):
    @tool(f"tool_{i}")
    def fn(query: str, limit: int = 10, exact: bool = False) -> str:
        """Search the index and return the best matches.

        Args:
            query: What to search for.
            limit: How many results to return.
            exact: Whether to match the query exactly.
        """
        return f"{query} {limit} {exact}"

    return fn


tools = [_make(i) for i in range(20)]
count = 0
for _ in range(100):
    for t in tools:
        convert_to_openai_tool(t)
        count += 1
sys.stdout.write(f"{count}\n")
