"""Test which free OpenRouter models actually work."""
import os
from dotenv import load_dotenv

load_dotenv("backend/.env")
key = os.environ.get("OPENROUTER_API_KEY", "").strip()
print(f"Key loaded: {key[:15]}...")

from openai import OpenAI

client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key)

# List of free models to try
free_models = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "meta-llama/llama-3.1-8b-instruct:free",
    "google/gemini-2.0-flash-exp:free",
    "google/gemini-2.0-flash-lite-preview-02-05:free",
    "mistralai/mistral-7b-instruct:free",
    "qwen/qwen-2.5-7b-instruct:free",
    "deepseek/deepseek-chat-v3-0324:free",
    "openai/gpt-4o-mini:free",
    "anthropic/claude-3.5-haiku:free",
    "meta-llama/llama-3.2-3b-instruct:free",
]

for model in free_models:
    try:
        response = client.chat.completions.create(
            model=model,
            max_tokens=50,
            messages=[{"role": "user", "content": "Say hello in one word."}],
        )
        print(f"✅ {model}: SUCCESS - {response.choices[0].message.content}")
    except Exception as e:
        err = str(e)[:120]
        print(f"❌ {model}: {err}")