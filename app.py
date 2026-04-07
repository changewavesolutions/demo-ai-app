from flask import Flask, request, jsonify, render_template_string
from werkzeug.utils import secure_filename
from PIL import Image, ImageOps, ImageFilter
import pytesseract
import os
import re
import traceback

app = Flask(__name__)

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024


HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Image Catalog Demo</title>
  <style>
    body { font-family: Arial, sans-serif; max-width: 960px; margin: 40px auto; padding: 20px; }
    .error { margin: 12px 0; padding: 12px; border: 1px solid #e0b4b4; background: #fff6f6; color: #9f3a38; border-radius: 8px; display: none; }
    .field { margin: 12px 0; }
    input[type="text"], textarea { width: 100%; padding: 10px; box-sizing: border-box; }
    img { max-width: 320px; margin-top: 10px; display: none; }
    button { padding: 10px 18px; margin-right: 10px; }
    pre { background: #f7f7f7; padding: 12px; border-radius: 8px; overflow-x: auto; }
  </style>
</head>
<body>
  <h1>Image Catalog Demo</h1>
  <p>Upload an image and extract visible text plus likely identifiers.</p>

  <div id="errorBox" class="error"></div>

  <input type="file" id="imageInput" accept="image/*">
  <br>
  <img id="preview">

  <div style="margin-top: 16px;">
    <button id="analyzeBtn">Analyze</button>
    <button id="clearBtn" type="button">Clear</button>
  </div>

  <div class="field">
    <label>Detected Serial / Identifier</label>
    <input id="serial_number" type="text" readonly>
  </div>

  <div class="field">
    <label>All OCR Text</label>
    <textarea id="ocr_text" rows="8" readonly></textarea>
  </div>

  <div class="field">
    <label>Matched Record</label>
    <pre id="matched_record"></pre>
  </div>

  <script>
    const imageInput = document.getElementById("imageInput");
    const preview = document.getElementById("preview");
    const analyzeBtn = document.getElementById("analyzeBtn");
    const clearBtn = document.getElementById("clearBtn");
    const errorBox = document.getElementById("errorBox");

    const serialField = document.getElementById("serial_number");
    const ocrField = document.getElementById("ocr_text");
    const recordField = document.getElementById("matched_record");

    function showError(msg) {
      errorBox.textContent = msg;
      errorBox.style.display = "block";
    }

    function clearError() {
      errorBox.textContent = "";
      errorBox.style.display = "none";
    }

    function clearFields() {
      serialField.value = "";
      ocrField.value = "";
      recordField.textContent = "";
    }

    imageInput.addEventListener("change", () => {
      clearError();
      clearFields();

      const file = imageInput.files[0];
      if (!file) {
        preview.style.display = "none";
        preview.src = "";
        return;
      }

      const reader = new FileReader();
      reader.onload = e => {
        preview.src = e.target.result;
        preview.style.display = "block";
      };
      reader.readAsDataURL(file);
    });

    clearBtn.addEventListener("click", () => {
      imageInput.value = "";
      preview.src = "";
      preview.style.display = "none";
      clearFields();
      clearError();
    });

    analyzeBtn.addEventListener("click", async () => {
      clearError();
      clearFields();

      const file = imageInput.files[0];
      if (!file) {
        showError("Please select an image first.");
        return;
      }

      const formData = new FormData();
      formData.append("image", file);

      try {
        const response = await fetch("/analyze", {
          method: "POST",
          body: formData
        });

        const rawText = await response.text();
        if (!rawText.trim()) {
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

        serialField.value = data.result.serial_number || "";
        ocrField.value = data.result.ocr_text || "";
        recordField.textContent = JSON.stringify(data.result.matched_record || {}, null, 2);

      } catch (err) {
        showError(err.message || "Something went wrong.");
      }
    });
  </script>
</body>
</html>
"""


def preprocess_image(image_path: str) -> Image.Image:
    """
    Basic OCR preprocessing:
    - grayscale
    - autocontrast
    - slight sharpen
    - optional upscale
    """
    img = Image.open(image_path).convert("RGB")
    gray = ImageOps.grayscale(img)
    gray = ImageOps.autocontrast(gray)
    gray = gray.filter(ImageFilter.SHARPEN)

    # Upscale to help OCR on small engraved text
    width, height = gray.size
    scale = 2
    gray = gray.resize((width * scale, height * scale))

    return gray


def extract_text(image_path: str) -> str:
    """
    Run OCR and return all visible text.
    """
    processed = preprocess_image(image_path)

    # Page segmentation mode 6: assume a block of text
    config = "--oem 3 --psm 6"
    text = pytesseract.image_to_string(processed, config=config)

    return text.strip()


def normalize_text(text: str) -> str:
    """
    Collapse whitespace and normalize separators.
    """
    text = text.replace("\\n", " ")
    text = re.sub(r"\\s+", " ", text)
    return text.strip()


def extract_identifier_candidates(text: str) -> list[str]:
    """
    Pull candidate alphanumeric identifiers from OCR text.

    This is intentionally generic. Adjust the regex to match the ID formats
    used in your own inventory or partner systems.
    """
    normalized = normalize_text(text).upper()

    # Prefer patterns appearing after common labels
    labeled_patterns = [
        r"(?:SERIAL|SERIAL NO|SERIAL NUMBER|S/N|SN)[:#\\- ]+([A-Z0-9\\-]{4,20})",
        r"(?:ID|ITEM ID|ASSET ID)[:#\\- ]+([A-Z0-9\\-]{4,20})",
    ]

    candidates = []
    for pattern in labeled_patterns:
        matches = re.findall(pattern, normalized, flags=re.IGNORECASE)
        candidates.extend(matches)

    # Fallback: generic standalone alphanumeric tokens
    generic = re.findall(r"\\b[A-Z0-9\\-]{5,20}\\b", normalized)
    candidates.extend(generic)

    # Deduplicate while preserving order
    seen = set()
    cleaned = []
    for c in candidates:
        c = c.strip("- ")
        if c and c not in seen:
            seen.add(c)
            cleaned.append(c)

    return cleaned


def choose_best_identifier(candidates: list[str]) -> str | None:
    """
    Heuristic ranking: discard obvious OCR junk and choose the best candidate.
    """
    if not candidates:
        return None

    def score(token: str) -> int:
        s = 0
        if any(ch.isdigit() for ch in token):
            s += 3
        if any(ch.isalpha() for ch in token):
            s += 2
        if 6 <= len(token) <= 14:
            s += 2
        if token.count("-") <= 2:
            s += 1
        if token in {"MODEL", "MADE", "USA", "PATENT", "WARNING"}:
            s -= 10
        return s

    ranked = sorted(candidates, key=score, reverse=True)
    return ranked[0] if ranked else None


def lookup_record_by_identifier(identifier: str | None) -> dict:
    """
    Replace this with your real lookup:
    - database query
    - internal API
    - spreadsheet-backed catalog
    - external compliance/inventory service

    Example returns mocked data.
    """
    if not identifier:
        return {}

    mock_catalog = {
        "ABC12345": {
            "status": "found",
            "brand": "Example Brand",
            "model": "Example Model",
            "description": "Sample catalog record",
            "notes": "Replace with real database/API lookup."
        }
    }

    return mock_catalog.get(identifier, {
        "status": "not_found",
        "identifier": identifier,
        "notes": "No matching record found in current data source."
    })


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

        ocr_text = extract_text(file_path)
        candidates = extract_identifier_candidates(ocr_text)
        identifier = choose_best_identifier(candidates)
        matched_record = lookup_record_by_identifier(identifier)

        return jsonify({
            "success": True,
            "result": {
                "serial_number": identifier or "",
                "all_candidates": candidates,
                "ocr_text": ocr_text,
                "matched_record": matched_record
            }
        }), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": f"Server exception: {str(e)}"
        }), 500


@app.errorhandler(404)
def not_found(e):
    return jsonify({"success": False, "error": "Route not found"}), 404


@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({"success": False, "error": "Method not allowed"}), 405


@app.errorhandler(413)
def file_too_large(e):
    return jsonify({"success": False, "error": "Uploaded file is too large"}), 413


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
