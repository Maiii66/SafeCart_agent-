from backend import config
from google import genai
from google.genai import types

k = config.GEMINI_API_KEY
client = genai.Client(api_key=k)

for model in ["gemini-2.5-flash", "gemini-flash-latest", "gemini-2.0-flash"]:
    try:
        r = client.models.generate_content(
            model=model,
            contents="Say OK",
            config=types.GenerateContentConfig(
                http_options=types.HttpOptions(timeout=20000)
            ),
        )
        print(model, "-> WORKS:", r.text.strip())
    except Exception as e:
        print(model, "-> FAILED:", type(e).__name__, str(e)[:200].replace(k, "***"))