from flask import Flask, request, jsonify, render_template_string
from werkzeug.utils import secure_filename
from openai import OpenAI
import base64
import json
import os
import traceback
import requests
from urllib.parse import quote

app = Flask(__name__)
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

WIKIPEDIA_SUMMARY_API = "https://en.wikipedia.org/api/rest_v1/page/summary/{}"
WIKIPEDIA_SEARCH_API = (
    "https://en.wikipedia.org/w/rest.php/v1/search/title?q={query}&limit=5"
)

HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Image Catalog Demo</title>
  <style>
    body {
      font-family: Arial, sans-serif;
      max-width: 980px;
      margin: 40px auto;
      padding: 20px;
      color: #1f2937;
    }
    h1 { font-size: 40px; margin-bottom: 10px; }
    p { color: #4b5563; font-size: 18px; }
    .card {
      border: 1px solid #e5e7eb;
      border-radius: 16px;
      padding: 24px;
      background: #fafafa;
      margin-top: 24px;
    }
    .error {
      display: none;
      margin-bottom: 16px;
      padding: 14px;
      border-radius: 10px;
      border: 1px solid #f5c2c7;
      background: #fef2f2;
      color: #991b1b;
    }
    .preview-box {
      border: 2px dashed #cbd5e1;
      border-radius: 14px;
      min-height: 280px;
      display: flex;
      align-items: center;
      justify-content: center;
      background: white;
      overflow: hidden;
      margin: 16px 0 20px 0;
    }
    .preview-box img {
      max-width: 100%;
      max-height: 420px;
      display: none;
    }
    .button-row {
      display: flex;
      gap: 12px;
      margin: 16px 0 20px 0;
    }
    button {
      font-size: 18px;
      padding: 12px 22px;
      border-radius: 12px;
      border: 1px solid #d1d5db;
      background: white;
      cursor: pointer;
    }
    button.primary {
      background: #2563eb;
      color: white;
      border: none;
    }
    button:disabled {
      opacity: 0.6;
      cursor: not-allowed;
    }
    .grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 16px;
    }
    .field { margin-bottom: 16px; }
    .field label {
      display: block;
      font-size: 13px;
      font-weight: bold;
      color: #6b7280;
      margin-bottom: 8px;
      text-transform: uppercase;
      letter-spacing: 0.8px;
    }
    .field input, .field textarea, .field pre {
      width: 100%;
      box-sizing: border-box;
      padding: 14px;
      border: 1px solid #d1d5db;
      border-radius: 12px;
      background: white;
      font-size: 16px;
    }
    .field textarea {
      resize: vertical;
      min-height: 100px;
    }
    .field pre {
      overflow-x: auto;
      white-space: pre-wrap;
      word-wrap: break-word;
      margin: 0;
    }
  </style>
</head>
<body>
  <h1>Image Catalog Demo</h1>
  <p>Upload an image to extract visible details and enrich them from public sources.</p>

  <div class="card">
    <div id="errorBox" class="error"></div>

    <input type="file" id="imageInput" accept="image/*" />

    <div class="preview-box">
      <img id="previewImage" alt="Preview" />
      <span id="previewPlaceholder">No image selected</span>
    </div>

    <div class="button-row">
      <button id="analyzeBtn" class="primary">Analyze</button>
      <button id="clearBtn" type="button">Clear</button>
    </div>

    <div class="field">
      <label>Description</label>
      <textarea id="description" readonly></textarea>
    </div>

    <div class="grid">
      <div class="field">
        <label>Name</label>
        <input id="name" type="text" readonly />
      </div>
      <div class="field">
        <label>Brand / Make</label>
        <input id="brand_make" type="text" readonly />
      </div>
      <div class="field">
        <label>Model</label>
        <input id="model" type="text" readonly />
      </div>
      <div class="field">
        <label>Type</label>
        <input id="type" type="text" readonly />
      </div>
      <div class="field">
        <label>Visible Specifications</label>
        <input id="visible_specs" type="text" readonly />
      </div>
      <div class="field">
        <label>Visible Markings</label>
        <input id="visible_markings" type="text" readonly />
      </div>
    </div>

    <div class="field">
      <label>Enriched Summary</label>
      <textarea id="enriched_summary" readonly></textarea>
    </div>

    <div class="field">
      <label>Source URL</label>
      <input id="source_url" type="text" readonly />
    </div>

    <div class="field">
      <label>Raw JSON</label>
      <pre id="raw_json"></pre>
    </div>
  </div>

  <script>
    const imageInput = document.getElementById("imageInput");
    const previewImage = document.getElementById("previewImage");
    const previewPlaceholder = document.getElementById("previewPlaceholder");
    const analyzeBtn = document.getElementById("analyzeBtn");
    const clearBtn = document.getElementById("clearBtn");
    const errorBox = document.getElementById("errorBox");

    const descriptionField = document.getElementById("description");
    const nameField = document.getElementById("name");
    const brandField = document.getElementById("brand_make");
    const modelField = document.getElementById("model");
    const typeField = document.getElementById("type");
    const specsField = document.getElementById("visible_specs");
    const markingsField = document.getElementById("visible_markings");
    const enrichedSummaryField = document.getElementById("enriched_summary");
    const sourceUrlField = document.getElementById("source_url");
    const rawJsonField = document.getElementById("raw_json");

    function showError(message) {
      errorBox.textContent = message;
      errorBox.style.display = "block";
    }

    function clearError() {
      errorBox.textContent = "";
      errorBox.style.display = "none";
    }

    function clearResults() {
      descriptionField.value = "";
      nameField.value = "";
      brandField.value = "";
      modelField.value = "";
      typeField.value = "";
      specsField.value = "";
      markingsField.value = "";
      enrichedSummaryField.value = "";
      sourceUrlField.value = "";
      rawJsonField.textContent = "";
    }

    function clearAll() {
      imageInput.value = "";
      previewImage.src = "";
      previewImage.style.display = "none";
      previewPlaceholder.style.display = "inline";
      clearError();
      clearResults();
    }

    imageInput.addEventListener("change", () => {
      clearError();
      clearResults();

      const file = imageInput.files[0];
      if (!file) {
        previewImage.src = "";
        previewImage.style.display = "none";
        previewPlaceholder.style.display = "inline";
        return;
      }

      const reader = new FileReader();
      reader.onload = (e) => {
        previewImage.src = e.target.result;
        previewImage.style.display = "block";
        previewPlaceholder.style.display = "none";
      };
      reader.readAsDataURL(file);
    });

    clearBtn.addEventListener("click", clearAll);

    analyzeBtn.addEventListener("click", async () => {
      clearError();
      clearResults();

      const file = imageInput.files[0];
      if (!file) {
        showError("Please select an image first.");
        return;
      }

      const formData = new FormData();
      formData.append("image", file);

      analyzeBtn.disabled = true;
      analyzeBtn.textContent = "Analyzing...";

      try {
        const response = await fetch("/analyze", {
          method: "POST",
          body: formData
        });

        const rawText = await response.text();

        if (!rawText || !rawText.trim()) {
          throw new Error("Server returned an empty response.");
        }

        let data;
        try {
          data = JSON.parse(rawText);
        } catch {
          throw new Error("Server did not return valid JSON.");
        }

        if (!response.ok || !data.success) {
          throw new Error(data.error || "Request failed.");
        }

        const result = data.result || {};
        const extracted = result.extracted || {};
        const enriched = result.enriched || {};

        descriptionField.value = extracted.description || "";
        nameField.value = extracted.name || "";
        brandField.value = extracted.brand_make || "";
        modelField.value = extracted.model || "";
        typeField.value = extracted.type || "";
        specsField.value = extracted.visible_specs || "";
        markingsField.value = extracted.visible_markings || "";
        enrichedSummaryField.value = enriched.summary || "";
        sourceUrlField.value = enriched.source_url || "";
        rawJsonField.textContent = JSON.stringify(result, null, 2);
      } catch (err) {
        showError(err.message || "Something went wrong.");
      } finally {
        analyzeBtn.disabled = false;
        analyzeBtn.textContent = "Analyze";
      }
    });
  </script>
</body>
</html>
"""

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

def extract_visible_details(file_path: str) -> dict:
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
            "visible_specs": {"type": "string"},
            "visible_markings": {"type": "string"},
            "confidence_notes": {"type": "string"},
        },
        "required": [
            "description",
            "name",
            "brand_make",
            "model",
            "type",
            "visible_specs",
            "visible_markings",
            "confidence_notes",
        ],
    }

    prompt = (
        "Analyze this uploaded item image and extract only non-sensitive visible catalog details. "
        "Do not guess unique identifiers. "
        "Return empty strings for unclear fields."
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

def search_wikipedia_title(query: str) -> dict:
    if not query.strip():
        return {}

    url = WIKIPEDIA_SEARCH_API.format(query=quote(query))
    headers = {"User-Agent": "demo-ai-app/1.0"}
    resp = requests.get(url, headers=headers, timeout=10)

    if not resp.ok:
        return {}

    data = resp.json()
    pages = data.get("pages", [])
    return pages[0] if pages else {}

def get_wikipedia_summary(title: str) -> dict:
    if not title:
        return {}

    url = WIKIPEDIA_SUMMARY_API.format(quote(title))
    headers = {"User-Agent": "demo-ai-app/1.0"}
    resp = requests.get(url, headers=headers, timeout=10)

    if not resp.ok:
        return {}

    data = resp.json()
    return {
        "title": data.get("title", ""),
        "summary": data.get("extract", ""),
        "source_url": data.get("content_urls", {}).get("desktop", {}).get("page", ""),
    }

def enrich_extracted_details(extracted: dict) -> dict:
    search_terms = [
        " ".join(
            part for part in [
                extracted.get("brand_make", "").strip(),
                extracted.get("model", "").strip(),
            ] if part
        ),
        extracted.get("name", "").strip(),
        extracted.get("description", "").strip(),
    ]

    for term in search_terms:
        if not term:
            continue

        top_hit = search_wikipedia_title(term)
        if not top_hit:
            continue

        title = top_hit.get("title", "")
        summary = get_wikipedia_summary(title)
        if summary:
            return summary

    return {
        "title": "",
        "summary": "",
        "source_url": "",
    }

@app.route("/", methods=["GET"])
def index():
    return render_template_string(HTML)

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
        enriched = enrich_extracted_details(extracted)

        return jsonify({
            "success": True,
            "result": {
                "extracted": extracted,
                "enriched": enriched
            }
        }), 200

    except json.JSONDecodeError:
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": "AI response was not valid JSON."
        }), 500
    except requests.RequestException as e:
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": f"Lookup request failed: {str(e)}"
        }), 502
    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": f"Server exception: {str(e)}"
        }), 500

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
