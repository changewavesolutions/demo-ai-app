import base64
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from openai import OpenAI
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
INDEX_PATH = BASE_DIR / "index.html"
DATA_PATH = BASE_DIR / "catalog.json"

app = FastAPI(title="Image Catalog Demo")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is missing.")
    return OpenAI(api_key=api_key)


def get_model_candidates() -> List[str]:
    raw = os.getenv("OPENAI_MODEL_CANDIDATES", "gpt-4o-mini,gpt-4o")
    return [m.strip() for m in raw.split(",") if m.strip()]


def load_catalog() -> List[Dict[str, Any]]:
    if not DATA_PATH.exists():
        raise HTTPException(status_code=500, detail="catalog.json not found.")
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def score_match(record: Dict[str, Any], query: Dict[str, Any]) -> float:
    score = 0.0

    make = (query.get("possible_make") or "").lower().strip()
    model = (query.get("possible_model") or "").lower().strip()
    caliber = (query.get("possible_caliber") or "").lower().strip()
    item_type = (query.get("item_type") or "").lower().strip()
    markings = [m.lower().strip() for m in (query.get("visible_markings") or []) if m]

    if make and make in record.get("make", "").lower():
        score += 0.35
    if model and model in record.get("model", "").lower():
        score += 0.35
    if caliber and caliber in record.get("caliber", "").lower():
        score += 0.15
    if item_type and item_type in record.get("category", "").lower():
        score += 0.10

    aliases_text = " ".join(record.get("aliases") or []).lower()
    if model and model in aliases_text:
        score += 0.08
    if make and make in aliases_text:
        score += 0.04

    record_markings = " ".join(record.get("known_markings") or []).lower()
    for m in markings:
        if m and m in record_markings:
            score += 0.03

    return min(score, 1.0)


def search_catalog_local(query: Dict[str, Any], limit: int = 5) -> List[Dict[str, Any]]:
    results = []
    for record in load_catalog():
        score = score_match(record, query)
        if score > 0:
            results.append({**record, "score": round(score, 2)})
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:limit]


def extract_first_json_block(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("Model returned empty output.")

    try:
        return json.loads(text)
    except Exception:
        pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"Model returned non-JSON output: {text[:500]}")

    try:
        return json.loads(match.group(0))
    except Exception as exc:
        raise ValueError(f"Could not parse extracted JSON block: {exc}")


VISION_SCHEMA = {
    "type": "object",
    "properties": {
        "item_type": {"type": "string"},
        "possible_make": {"type": "string"},
        "possible_model": {"type": "string"},
        "possible_caliber": {"type": "string"},
        "visible_markings": {
            "type": "array",
            "items": {"type": "string"}
        },
        "confidence": {"type": "number"},
        "notes": {"type": "string"}
    },
    "required": [
        "item_type",
        "possible_make",
        "possible_model",
        "possible_caliber",
        "visible_markings",
        "confidence",
        "notes"
    ],
    "additionalProperties": False
}


def call_openai_for_analysis(client: OpenAI, model: str, image_content_type: str, image_b64: str) -> Dict[str, Any]:
    prompt = (
        "Analyze the uploaded product image and return only valid JSON matching the schema. "
        "Infer likely item type, likely make, likely model, likely caliber, visible markings, confidence, and concise notes. "
        "If uncertain, lower confidence and explain uncertainty in notes. "
        "Return JSON only."
    )

    response = client.responses.create(
        model=model,
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {
                        "type": "input_image",
                        "image_url": f"data:{image_content_type};base64,{image_b64}",
                        "detail": "low"
                    }
                ]
            }
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "catalog_extraction",
                "schema": VISION_SCHEMA,
                "strict": True
            }
        }
    )

    raw_text = getattr(response, "output_text", None)
    if not raw_text:
        raise ValueError("OpenAI returned no text output.")

    return extract_first_json_block(raw_text)


def analyze_with_fallbacks(client: OpenAI, image_content_type: str, image_b64: str) -> Dict[str, Any]:
    errors = []
    for model in get_model_candidates():
        try:
            extracted = call_openai_for_analysis(client, model, image_content_type, image_b64)
            return {"model_used": model, "extracted": extracted}
        except Exception as exc:
            errors.append(f"{model}: {exc}")

    raise ValueError("All model attempts failed.\n" + "\n".join(errors))


class ChatRequest(BaseModel):
    message: str
    extracted: Optional[Dict[str, Any]] = None
    matches: Optional[List[Dict[str, Any]]] = None


@app.get("/", response_class=HTMLResponse)
async def root() -> HTMLResponse:
    if not INDEX_PATH.exists():
        return HTMLResponse("<h1>index.html not found</h1>", status_code=500)
    return HTMLResponse(INDEX_PATH.read_text(encoding="utf-8"))


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "model_candidates": get_model_candidates(),
        "has_index_html": INDEX_PATH.exists(),
        "has_catalog_json": DATA_PATH.exists(),
    }


@app.get("/catalog")
async def catalog() -> JSONResponse:
    return JSONResponse(load_catalog())


@app.post("/api/analyze")
async def analyze(image: UploadFile = File(...)) -> Dict[str, Any]:
    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Please upload an image file.")

    content = await image.read()
    image_b64 = base64.b64encode(content).decode("utf-8")
    client = get_client()

    try:
        result = analyze_with_fallbacks(client, image.content_type, image_b64)
        extracted = result["extracted"]
        matches = search_catalog_local(extracted, limit=5)
        return {
            "model_used": result["model_used"],
            "extracted": extracted,
            "matches": matches,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/chat")
async def chat(payload: ChatRequest) -> Dict[str, Any]:
    client = get_client()
    models = get_model_candidates()
    errors = []

    system_text = (
        "You are a grounded catalog assistant. "
        "Use the extracted fields and provided catalog matches. "
        "Do not invent certainty."
    )

    user_context = {
        "user_message": payload.message,
        "current_extracted_fields": payload.extracted or {},
        "current_catalog_matches": payload.matches or [],
    }

    for model in models:
        try:
            response = client.responses.create(
                model=model,
                input=[
                    {"role": "system", "content": system_text},
                    {"role": "user", "content": json.dumps(user_context)}
                ]
            )
            answer = getattr(response, "output_text", None)
            if not answer:
                raise ValueError("No chat output returned.")
            return {"model_used": model, "answer": answer}
        except Exception as exc:
            errors.append(f"{model}: {exc}")

    raise HTTPException(status_code=500, detail="All chat model attempts failed.\n" + "\n".join(errors))
