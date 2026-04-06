import base64
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE_DIR / "public"
DATA_PATH = BASE_DIR / "data" / "catalog.json"

app = FastAPI(title="Client Demo Live")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/public", StaticFiles(directory=PUBLIC_DIR), name="public")

def get_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is missing.")
    return OpenAI(api_key=api_key)

def load_catalog() -> List[Dict[str, Any]]:
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))

def score_match(record: Dict[str, Any], query: Dict[str, Any]) -> float:
    score = 0.0
    make = (query.get("possible_make") or "").lower().strip()
    model = (query.get("possible_model") or "").lower().strip()
    caliber = (query.get("possible_caliber") or "").lower().strip()
    item_type = (query.get("item_type") or "").lower().strip()
    markings = [m.lower().strip() for m in (query.get("visible_markings") or []) if m]

    if make and make in record["make"].lower():
        score += 0.35
    if model and model in record["model"].lower():
        score += 0.35
    if caliber and caliber in (record.get("caliber") or "").lower():
        score += 0.15
    if item_type and item_type in (record.get("category") or "").lower():
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

VISION_SCHEMA = {
    "type": "object",
    "properties": {
        "item_type": {"type": "string"},
        "possible_make": {"type": "string"},
        "possible_model": {"type": "string"},
        "possible_caliber": {"type": "string"},
        "visible_markings": {"type": "array", "items": {"type": "string"}},
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

@app.get("/")
async def root() -> FileResponse:
    return FileResponse(PUBLIC_DIR / "index.html")

@app.post("/api/analyze")
async def analyze(image: UploadFile = File(...)) -> Dict[str, Any]:
    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Please upload an image file.")
    content = await image.read()
    image_b64 = base64.b64encode(content).decode("utf-8")
    client = get_client()

    prompt = (
        "Analyze the uploaded image and return only valid JSON matching the schema. "
        "Provide a likely item type, likely make, likely model, likely caliber, visible markings, confidence, and concise notes. "
        "If the image is unclear, lower confidence and explain why."
    )

    try:
        response = client.responses.create(
            model=os.getenv("OPENAI_MODEL", "gpt-5.4"),
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {
                        "type": "input_image",
                        "image_url": f"data:{image.content_type};base64,{image_b64}",
                        "detail": "low"
                    }
                ]
            }],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "catalog_extraction",
                    "schema": VISION_SCHEMA,
                    "strict": True
                }
            }
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"OpenAI analyze call failed: {exc}")

    try:
        extracted = json.loads(response.output_text)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not parse model JSON: {exc}")

    matches = search_catalog_local(extracted, limit=5)
    return {"extracted": extracted, "matches": matches}

class ChatRequest(BaseModel):
    message: str
    extracted: Optional[Dict[str, Any]] = None
    matches: Optional[List[Dict[str, Any]]] = None

def tool_search_catalog(arguments_json: str) -> Dict[str, Any]:
    args = json.loads(arguments_json or "{}")
    query = {
        "item_type": args.get("item_type", ""),
        "possible_make": args.get("possible_make", ""),
        "possible_model": args.get("possible_model", ""),
        "possible_caliber": args.get("possible_caliber", ""),
        "visible_markings": args.get("visible_markings", []),
    }
    return {"results": search_catalog_local(query, limit=5)}

@app.post("/api/chat")
async def chat(payload: ChatRequest) -> Dict[str, Any]:
    client = get_client()
    system_text = (
        "You are a grounded catalog assistant. "
        "Use the extracted fields and provided catalog matches. "
        "If needed, call the search_catalog tool instead of guessing. "
        "Be clear when identification is uncertain."
    )
    user_context = {
        "user_message": payload.message,
        "current_extracted_fields": payload.extracted or {},
        "current_catalog_matches": payload.matches or [],
    }

    tools = [{
        "type": "function",
        "name": "search_catalog",
        "description": "Search the local catalog for likely matching records.",
        "parameters": {
            "type": "object",
            "properties": {
                "item_type": {"type": "string"},
                "possible_make": {"type": "string"},
                "possible_model": {"type": "string"},
                "possible_caliber": {"type": "string"},
                "visible_markings": {"type": "array", "items": {"type": "string"}}
            },
            "additionalProperties": False
        }
    }]

    try:
        first = client.responses.create(
            model=os.getenv("OPENAI_MODEL", "gpt-5.4"),
            input=[
                {"role": "system", "content": system_text},
                {"role": "user", "content": json.dumps(user_context)}
            ],
            tools=tools,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"OpenAI chat call failed: {exc}")

    tool_calls = [item for item in first.output if item.type == "function_call" and item.name == "search_catalog"]
    if not tool_calls:
        return {"answer": first.output_text}

    followup_inputs = []
    for call in tool_calls:
        result = tool_search_catalog(call.arguments)
        followup_inputs.append({
            "type": "function_call_output",
            "call_id": call.call_id,
            "output": json.dumps(result),
        })

    try:
        second = client.responses.create(
            model=os.getenv("OPENAI_MODEL", "gpt-5.4"),
            previous_response_id=first.id,
            input=followup_inputs,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"OpenAI tool follow-up failed: {exc}")

    return {"answer": second.output_text}
