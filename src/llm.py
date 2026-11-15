import os

from dotenv import load_dotenv
from google import genai
from prompts import SYSTEM_PROMPT


load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

def generate_code(prompt: str) -> str: 
    response = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=prompt,
        config={
            "system_instruction": SYSTEM_PROMPT
        }
    )

    return response.text

if __name__ == "__main__":
    code = generate_code("Write Python code to print 'hello world!'")
    print(code)

