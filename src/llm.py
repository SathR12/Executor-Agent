"""Generate an async Python function body using host-side Gemini credentials."""

import asyncio
import os

from dotenv import load_dotenv
from google import genai
from google.genai import types

from prompts import SYSTEM_PROMPT

MODEL = "gemini-3.5-flash-lite"
GENERATION_TIMEOUT_SECONDS = 30


async def generate_code(prompt: str, tools: list) -> str:
    """Generate Python with a cancellable deadline and one HTTP request attempt."""
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing. Set it in src/.env.")
    tool_descriptions = ""

    for tool in tools:
        tool_descriptions += (
            f"Name: {tool.name}\n"
            f"Description: {tool.description}\n"
            f"Parameters: {tool.input_schema}\n\n"
        )

    system_prompt = SYSTEM_PROMPT.format(tools=tool_descriptions)

    http_options = types.HttpOptions(
        timeout=int(GENERATION_TIMEOUT_SECONDS * 1000),
        retry_options=types.HttpRetryOptions(attempts=1),
    )
    try:
        async with asyncio.timeout(GENERATION_TIMEOUT_SECONDS):
            async with genai.Client(
                api_key=api_key, http_options=http_options
            ).aio as client:
                response = await client.models.generate_content(
                    model=MODEL,
                    contents=prompt,
                    config={"system_instruction": system_prompt, "temperature": 0},
                )
    except TimeoutError as error:
        raise RuntimeError(
            f"Gemini generation timed out after {GENERATION_TIMEOUT_SECONDS} seconds. Try again."
        ) from error

    if not response.text or not response.text.strip():
        raise RuntimeError("Gemini did not return any code")

    return response.text.strip()


if __name__ == "__main__":
    code = asyncio.run(generate_code("Write Python code to print 'hello world!'", []))
    print(code)
