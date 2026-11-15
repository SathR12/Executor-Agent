import os

from dotenv import load_dotenv
from google import genai

from prompts import SYSTEM_PROMPT

load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


def generate_code(prompt: str, tools: list) -> str:
    tool_descriptions = ""

    for tool in tools:
        tool_descriptions += (
            f"Name: {tool.name}\n"
            f"Description: {tool.description}\n"
            f"Parameters: {tool.input_schema}\n\n"
        )

    system_prompt = SYSTEM_PROMPT.format(tools=tool_descriptions)

    response = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=prompt,
        config={
            "system_instruction": system_prompt,
            "temperature": 0
        }
    )

    if not response.text:
        raise RuntimeError("Gemini did not return any code")

    return response.text.strip()

if __name__ == "__main__":
    code = generate_code("Write Python code to print 'hello world!'", [])
    print(code)

