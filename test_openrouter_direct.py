import os
from dotenv import load_dotenv
from openai import OpenAI

# Load .env
load_dotenv()

# Get API key
key = os.getenv("OPENROUTER_API_KEY", "").strip()

if not key:
    print("❌ API key NOT found")
    exit()

print("✅ API key found")
print("Key starts with:", key[:10])

# Create OpenRouter client
client = OpenAI(
    api_key=key,
    base_url="https://openrouter.ai/api/v1"
)

# Send test request
try:
    response = client.chat.completions.create(
        model="meta-llama/llama-3.3-70b-instruct:free",
        messages=[
            {
                "role": "user",
                "content": "Say hello in one short sentence."
            }
        ]
    )

    print("\n✅ OPENROUTER WORKING!")
    print(response.choices[0].message.content)

except Exception as e:
    print("\n❌ OPENROUTER FAILED")
    print(type(e).__name__)
    print(e)