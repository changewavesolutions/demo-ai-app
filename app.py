from flask import Flask, render_template, request, jsonify
from werkzeug.utils import secure_filename
import os
import traceback

app = Flask(__name__)

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB max upload


@app.route("/")
def index():
    return render_template("index.html")


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
                "error": "No file was selected."
            }), 400

        filename = secure_filename(image.filename)
        file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        image.save(file_path)

        # ---- Replace this block with your real AI/catalog logic ----
        result = {
            "type": "Handgun",
            "brand_make": "Smith & Wesson",
            "model": "J-Frame Revolver",
            "caliber": ".38 Special",
            "notes": "This is a demo response. Replace with real model inference."
        }
        # ------------------------------------------------------------

        return jsonify({
            "success": True,
            "filename": filename,
            "result": result
        }), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": f"Server error: {str(e)}"
        }), 500


@app.errorhandler(413)
def file_too_large(_error):
    return jsonify({
        "success": False,
        "error": "File is too large. Maximum size is 16 MB."
    }), 413


@app.errorhandler(404)
def not_found(_error):
    return jsonify({
        "success": False,
        "error": "Endpoint not found."
    }), 404


@app.errorhandler(405)
def method_not_allowed(_error):
    return jsonify({
        "success": False,
        "error": "Method not allowed."
    }), 405


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
