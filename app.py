from flask import Flask, request, jsonify, render_template_string
from werkzeug.utils import secure_filename
import os
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
    body { font-family: Arial, sans-serif; max-width: 900px; margin: 40px auto; padding: 20px; }
    .error { margin: 12px 0; padding: 12px; border: 1px solid #e0b4b4; background: #fff6f6; color: #9f3a38; border-radius: 8px; display: none; }
    .field { margin: 12px 0; }
    input[type="text"] { width: 100%; padding: 10px; }
    img { max-width: 300px; margin-top: 10px; display: none; }
    button { padding: 10px 18px; margin-right: 10px; }
  </style>
</head>
<body>
  <h1>Image Catalog Demo</h1>
  <p>Upload an image, get structured AI suggestions, see likely catalog matches, and ask follow-up questions.</p>

  <div id="errorBox" class="error"></div>

  <input type="file" id="imageInput" accept="image/*">
  <br>
  <img id="preview">

  <div style="margin-top: 16px;">
    <button id="analyzeBtn">Analyze</button>
    <button id="clearBtn" type="button">Clear</button>
  </div>

  <div class="field">
    <label>TYPE</label>
    <input id="type" type="text" readonly>
  </div>

  <div class="field">
    <label>BRAND / MAKE</label>
    <input id="brand_make" type="text" readonly>
  </div>

  <div class="field">
    <label>MODEL</label>
    <input id="model" type="text" readonly>
  </div>

  <div class="field">
    <label>CALIBER</label>
    <input id="caliber" type="text" readonly>
  </div>

  <script>
    const imageInput = document.getElementById("imageInput");
    const preview = document.getElementById("preview");
    const analyzeBtn = document.getElementById("analyzeBtn");
    const clearBtn = document.getElementById("clearBtn");
    const errorBox = document.getElementById("errorBox");

    const typeField = document.getElementById("type");
    const brandMakeField = document.getElementById("brand_make");
    const modelField = document.getElementById("model");
    const caliberField = document.getElementById("caliber");

    function showError(msg) {
      errorBox.textContent = msg;
      errorBox.style.display = "block";
    }

    function clearError() {
      errorBox.textContent = "";
      errorBox.style.display = "none";
    }

    function clearFields() {
      typeField.value = "";
      brandMakeField.value = "";
      modelField.value = "";
      caliberField.value = "";
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

        const contentType = response.headers.get("content-type") || "";
        const rawText = await response.text();

        console.log("STATUS:", response.status);
        console.log("CONTENT-TYPE:", contentType);
        console.log("RAW RESPONSE:", rawText);

        if (!rawText || rawText.trim() === "") {
          throw new Error("Server returned an empty response.");
        }

        if (!contentType.includes("application/json")) {
          throw new Error("Server returned non-JSON response: " + rawText.slice(0, 200));
        }

        let data;
        try {
          data = JSON.parse(rawText);
        } catch (e) {
          throw new Error("Invalid JSON returned by server: " + rawText.slice(0, 200));
        }

        if (!response.ok || !data.success) {
          throw new Error(data.error || "Request failed.");
        }

        typeField.value = data.result.type || "";
        brandMakeField.value = data.result.brand_make || "";
        modelField.value = data.result.model || "";
        caliberField.value = data.result.caliber || "";

      } catch (err) {
        console.error(err);
        showError(err.message);
      }
    });
  </script>
</body>
</html>
"""


@app.route("/", methods=["GET"])
def index():
    return render_template_string(HTML)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "success": True,
        "message": "Server is running"
    }), 200


@app.route("/analyze", methods=["POST"])
def analyze():
    try:
        if "image" not in request.files:
            return jsonify({
                "success": False,
                "error": "No image file was uploaded."
            }), 400

        image = request.files["image"]

        if image.filename == "":
            return jsonify({
                "success": False,
                "error": "No file selected."
            }), 400

        filename = secure_filename(image.filename)
        path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        image.save(path)

        return jsonify({
            "success": True,
            "result": {
                "type": "Handgun",
                "brand_make": "Smith & Wesson",
                "model": "J-Frame",
                "caliber": ".38 Special"
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
    return jsonify({
        "success": False,
        "error": "Route not found"
    }), 404


@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({
        "success": False,
        "error": "Method not allowed"
    }), 405


@app.errorhandler(413)
def file_too_large(e):
    return jsonify({
        "success": False,
        "error": "Uploaded file is too large"
    }), 413


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
