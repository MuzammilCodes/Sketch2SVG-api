import os
import struct
import tempfile
import subprocess
import numpy as np
from PIL import Image
from flask import Flask, request, send_file, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # allows requests from your Vercel frontend


# ── image helpers ─────────────────────────────────────────────────

def to_pbm(img_path, pbm_path, threshold=128):
    """Convert any image to a 1-bit PBM file for potrace."""
    img = Image.open(img_path).convert("RGBA")
    r, g, b, a = img.split()

    # Composite transparent images onto white background
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    bg.paste(img, mask=a)
    gray = bg.convert("L")

    arr = np.array(gray)

    # Auto-invert if image is light-on-dark (logo on dark bg)
    if arr.mean() < 127:
        arr = 255 - arr

    binary = (arr < threshold).astype(np.uint8)

    h, w = binary.shape
    with open(pbm_path, "wb") as f:
        f.write(f"P4\n{w} {h}\n".encode())
        padded_w = (w + 7) // 8 * 8
        padded = np.zeros((h, padded_w), dtype=np.uint8)
        padded[:, :w] = binary
        for row in padded:
            bits = np.packbits(row)
            f.write(bits.tobytes())


def trace_with_potrace(img_path, out_path, threshold=128,
                       turdsize=2, alphamax=1.0, opttolerance=0.2):
    """Run potrace on the image and write an SVG."""
    with tempfile.TemporaryDirectory() as tmp:
        pbm_path = os.path.join(tmp, "input.pbm")
        to_pbm(img_path, pbm_path, threshold=threshold)

        cmd = [
            "potrace",
            pbm_path,
            "--svg",
            "--output", out_path,
            "--turdsize",     str(turdsize),
            "--alphamax",     str(alphamax),
            "--opttolerance", str(opttolerance),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"potrace error: {result.stderr}")

    return out_path


def image_to_svg_ocr(img_path, out_path):
    """OCR-based SVG for text images (requires pytesseract)."""
    try:
        import pytesseract
        import svgwrite
    except ImportError:
        raise RuntimeError("pytesseract or svgwrite not installed for OCR mode")

    img = Image.open(img_path)
    w, h = img.size
    dwg = svgwrite.Drawing(out_path, size=(w, h))
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    for i, word in enumerate(data["text"]):
        if word.strip():
            dwg.add(dwg.text(
                word,
                insert=(data["left"][i], data["top"][i] + data["height"][i]),
                font_size=max(data["height"][i], 8)
            ))
    dwg.save()
    return out_path


# ── routes ────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "vectorizer-api"})


@app.route("/convert", methods=["POST"])
def convert():
    if "image" not in request.files:
        return jsonify({"error": "No image file provided"}), 400

    file = request.files["image"]
    if file.filename == "":
        return jsonify({"error": "Empty filename"}), 400

    # Parse params with safe defaults
    mode         = request.form.get("mode", "logo")
    threshold    = int(request.form.get("threshold", 128))
    turdsize     = int(request.form.get("turdsize", 2))
    alphamax     = float(request.form.get("alphamax", 1.0))
    opttolerance = float(request.form.get("opttolerance", 0.2))

    # Validate ranges
    threshold    = max(0, min(255, threshold))
    turdsize     = max(0, min(100, turdsize))
    alphamax     = max(0.0, min(1.33, alphamax))
    opttolerance = max(0.1, min(0.5, opttolerance))

    with tempfile.TemporaryDirectory() as tmp:
        # Save uploaded file
        ext = os.path.splitext(file.filename)[1] or ".png"
        img_path = os.path.join(tmp, f"input{ext}")
        out_path = os.path.join(tmp, "output.svg")
        file.save(img_path)

        try:
            if mode == "text":
                image_to_svg_ocr(img_path, out_path)
            else:
                trace_with_potrace(
                    img_path, out_path,
                    threshold=threshold,
                    turdsize=turdsize,
                    alphamax=alphamax,
                    opttolerance=opttolerance
                )
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 500

        # Read SVG and return it
        with open(out_path, "r") as f:
            svg_content = f.read()

    base_name = os.path.splitext(file.filename)[0]
    return (
        svg_content,
        200,
        {
            "Content-Type": "image/svg+xml",
            "Content-Disposition": f'attachment; filename="{base_name}.svg"',
        }
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
