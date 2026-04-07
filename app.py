from flask import Flask, request, jsonify, render_template_string
from werkzeug.utils import secure_filename
from openai import OpenAI
import base64
import json
import os
import traceback

app = Flask(__name__)
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

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
    body { font-family: Arial, sans-serif; max-width: 900px; margin: 40px auto; padding: 20px; }
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
  <p>Upload an image and extract a description and serial number.</p>

  <div id="errorBox" class="error"></div>

  <input type="file" id="imageInput" accept="image/*">
  <br>
  <img id="preview">

  <div style="margin-top: 16px;">
    <button id="analyzeBtn">Analyze</button>
    <button id="clearBtn" type="button">Clear</button>
  </div>

  <div class="field">
    <label>Description</label>
    <textarea id="description" rows="4" readonly></textarea>
  </div>

  <div class="field">
    <label>Serial Number</label>
    <input id="serial_number" type="text" readonly>
  </div>

  <div class="field">
    <label>Brand / Make</label>
    <input id="brand_make" type="text" readonly>
  </div>

  <div class="field">
    <label>Model</label>
    <input id="model" type="text" readonly>
  </div>

  <div class="field">
    <label>Raw AI JSON</label>
    <pre id="raw_json"></pre>
  </div>

  <script>
    const imageInput = document.getElementById("imageInput");
    const preview = document.getElementById("preview");
    const analyzeBtn = document.getElementById("analyzeBtn");
    const clearBtn = document.getElementById("clearBtn");
    const errorBox = document.getElementById("errorBox");

    const descriptionField = document.getElementById("description");
    const serialField = document.getElementById("serial_number");
    const brandField = document.getElementById("brand_make");
    const modelField = document.getElementById("model");
    const rawJsonField = document.getElementById("raw_json");

    function showError(msg) {
      errorBox.textContent = msg;
      errorBox.style.display = "block";
    }

    function clearError() {
      errorBox.textContent = "";
      errorBox.style.display = "none";
    }

    function clearFields() {
      descriptionField.value = "";
      serialField.value = "";
      brandField.value = "";
      modelField.value = "";
      rawJsonField.textContent = "";
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

        const data = JSON.parse(rawText);

        if (!response.ok || !data.success) {
          throw new Error(data.error || "Request failed.");
        }

        const result = data.result || {};
        descriptionField.value = result.description || "";
        serialField.value = result.serial_number || "";
        brandField.value = result.brand_make || "";
        modelField.value = result.model || "";
        rawJsonField.textContent = JSON.stringify(result, null, 2);

      } catch (err) {
        showError(err.message || "Something went wrong.");
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

def extract_with_openai(file_path: str) -> dict:
    image_data_url = image_file_to_data_url(file_path)

    response = client.responses.create(
        model="gpt-4.1",
        input=[{
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": (
                        "Analyze this uploaded item image and return ONLY valid JSON with this exact schema: "
                        "{"
                        "\"description\":\"short plain-English description\","
                        "\"serial_number\":\"best visible serial number or empty string\","
                        "\"brand_make\":\"brand or maker if visible, else empty string\","
                        "\"model\":\"model if visible, else empty string\""
                        "} "
                        "Do not include markdown. Do not include extra text."
                    )
                },
                {
                    "type": "input_image",
                    "image_url": image_data_url
                }
            ]
        }]
    )

    raw_text = response.output_text.strip()
    return json.loads(raw_text)

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

        extracted = extract_with_openai(file_path)

        return jsonify({
            "success": True,
            "result": extracted
        }), 200

    except json.JSONDecodeError:
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": "AI response was not valid JSON."
        }), 500

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
