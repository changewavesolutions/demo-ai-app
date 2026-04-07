from datetime import datetime
import base64
import json
import os
import re
import traceback
from urllib.parse import quote

import requests
from flask import Flask, jsonify, render_template, request
from openai import OpenAI
from werkzeug.utils import secure_filename

app = Flask(__name__)
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

WIKIPEDIA_SUMMARY_API = "https://en.wikipedia.org/api/rest_v1/page/summary/{}"
WIKIPEDIA_SEARCH_API = "https://en.wikipedia.org/w/rest.php/v1/search/title?q={}&limit=5"
USER_AGENT = "demo-ai-app/1.0"


def image_file_to_data_url(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()
    mime = "image/jpeg"
    if ext == ".png":
        mime = "image/png"
    elif ext == ".webp":
        mime = "image/webp"

    with open(file_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")

    return f"data:{mime};base64,{b64}"


def normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def extract_visible_details(file_path: str) -> dict:
    """
    Extract non-sensitive visible item details from the image.
    """
    image_data_url = image_file_to_data_url(file_path)

    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "description": {"type": "string"},
            "name": {"type": "string"},
            "brand_make": {"type": "string"},
            "model": {"type": "string"},
            "type": {"type": "string"},
            "caliber_gauge": {"type": "string"},
            "visible_specs": {"type": "string"},
            "visible_markings": {"type": "string"},
            "finish_material": {"type": "string"},
            "confidence_notes": {"type": "string"},
        },
        "required": [
            "description",
            "name",
            "brand_make",
            "model",
            "type",
            "caliber_gauge",
            "visible_specs",
            "visible_markings",
            "finish_material",
            "confidence_notes",
        ],
    }

    prompt = (
        "Analyze this uploaded image and return only visible, non-sensitive catalog details. "
        "Do not include any serial numbers, unique identifiers, owner information, or transaction details. "
        "Prefer empty strings when unclear. "
        "For caliber_gauge, return a value only if clearly visible or strongly supported by visible markings."
    )

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": image_data_url},
                ],
            }
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "visible_item_details",
                "schema": schema,
                "strict": True,
            }
        },
    )

    return json.loads(response.output_text)


def search_wikipedia_title(query: str) -> list[dict]:
    query = normalize_spaces(query)
    if not query:
        return []

    url = WIKIPEDIA_SEARCH_API.format(quote(query))
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=10)
    if not resp.ok:
        return []

    data = resp.json()
    return data.get("pages", [])


def get_wikipedia_summary(title: str) -> dict:
    title = normalize_spaces(title)
    if not title:
        return {}

    url = WIKIPEDIA_SUMMARY_API.format(quote(title))
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=10)
    if not resp.ok:
        return {}

    data = resp.json()
    return {
        "title": data.get("title", ""),
        "summary": data.get("extract", ""),
        "source_url": data.get("content_urls", {}).get("desktop", {}).get("page", ""),
    }


def pick_best_lookup_queries(extracted: dict) -> list[str]:
    queries = []

    brand = normalize_spaces(extracted.get("brand_make", ""))
    model = normalize_spaces(extracted.get("model", ""))
    item_name = normalize_spaces(extracted.get("name", ""))
    item_type = normalize_spaces(extracted.get("type", ""))
    specs = normalize_spaces(extracted.get("visible_specs", ""))

    if brand and model:
        queries.append(f"{brand} {model}")
    if item_name and model and item_name.lower() != model.lower():
        queries.append(f"{item_name} {model}")
    if brand and item_name:
        queries.append(f"{brand} {item_name}")
    if brand and item_type:
        queries.append(f"{brand} {item_type}")
    if brand:
        queries.append(brand)
    if model:
        queries.append(model)
    if specs and brand:
        queries.append(f"{brand} {specs}")

    seen = set()
    result = []
    for query in queries:
        key = query.lower()
        if key not in seen:
            seen.add(key)
            result.append(query)
    return result


def enrich_from_wikipedia(extracted: dict) -> dict:
    queries = pick_best_lookup_queries(extracted)

    for query in queries:
        hits = search_wikipedia_title(query)
        for hit in hits:
            title = hit.get("title", "")
            if not title:
                continue
            summary = get_wikipedia_summary(title)
            if summary.get("summary"):
                return summary

    return {
        "title": "",
        "summary": "",
        "source_url": "",
    }


def refine_with_ai(extracted: dict, wiki: dict) -> dict:
    """
    Merge visible extraction with public lookup text to improve
    non-sensitive fields like brand/model/caliber when the match is clear.
    """
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "description": {"type": "string"},
            "name": {"type": "string"},
            "brand_make": {"type": "string"},
            "model": {"type": "string"},
            "type": {"type": "string"},
            "caliber_gauge": {"type": "string"},
            "visible_specs": {"type": "string"},
            "visible_markings": {"type": "string"},
            "finish_material": {"type": "string"},
            "enriched_summary": {"type": "string"},
            "source_url": {"type": "string"},
            "confidence_notes": {"type": "string"},
        },
        "required": [
            "description",
            "name",
            "brand_make",
            "model",
            "type",
            "caliber_gauge",
            "visible_specs",
            "visible_markings",
            "finish_material",
            "enriched_summary",
            "source_url",
            "confidence_notes",
        ],
    }

    prompt = (
        "Refine this item catalog data. "
        "Use the visible extraction as the primary source. "
        "Use the public reference summary only to improve non-sensitive fields if it clearly matches. "
        "If the match is uncertain, preserve the extracted values and mention uncertainty in confidence_notes. "
        "Do not invent serial numbers or unique identifiers."
    )

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {
                        "type": "input_text",
                        "text": json.dumps(
                            {
                                "extracted": extracted,
                                "reference": wiki,
                            },
                            indent=2,
                        ),
                    },
                ],
            }
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "refined_item_details",
                "schema": schema,
                "strict": True,
            }
        },
    )

    return json.loads(response.output_text)


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"success": True, "message": "Server is running"}), 200


@app.route("/analyze", methods=["POST"])
def analyze():
    try:
        if "image" not in request.files:
            return jsonify({"success": False, "error": "No image file was uploaded."}), 400

        image = request.files["image"]
        if image.filename == "":
            return jsonify({"success": False, "error": "No file selected."}), 400

        filename = secure_filename(image.filename)
        file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        image.save(file_path)

        extracted = extract_visible_details(file_path)
        wiki = enrich_from_wikipedia(extracted)
        refined = refine_with_ai(extracted, wiki)

        return jsonify(
            {
                "success": True,
                "date_entered": datetime.now().strftime("%Y-%m-%d"),
                "result": refined,
                "raw_extracted": extracted,
                "lookup": wiki,
            }
        ), 200

    except json.JSONDecodeError:
        traceback.print_exc()
        return jsonify({"success": False, "error": "AI response was not valid JSON."}), 500
    except requests.RequestException as exc:
        traceback.print_exc()
        return jsonify({"success": False, "error": f"Lookup request failed: {str(exc)}"}), 502
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"success": False, "error": f"Server exception: {str(exc)}"}), 500


@app.errorhandler(404)
def not_found(_error):
    return jsonify({"success": False, "error": "Route not found."}), 404


@app.errorhandler(405)
def method_not_allowed(_error):
    return jsonify({"success": False, "error": "Method not allowed."}), 405


@app.errorhandler(413)
def file_too_large(_error):
    return jsonify({"success": False, "error": "Uploaded file is too large."}), 413


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
