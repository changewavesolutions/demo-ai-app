# Client Demo Live

This is a ready-to-run client demo with:
- image upload UI
- OpenAI image analysis
- strict structured extraction
- local catalog matching
- grounded follow-up chat

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export OPENAI_API_KEY=your_key_here
export OPENAI_MODEL=gpt-5.4
uvicorn app:app --reload
```

Open:
- http://127.0.0.1:8000

## Deploy
This folder includes `render.yaml` so you can deploy to Render quickly.
