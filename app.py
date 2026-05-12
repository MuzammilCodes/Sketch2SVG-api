import os
import re
import tempfile
import subprocess
import numpy as np
from PIL import Image
from flask import Flask, request, send_file, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # allows requests from your Vercel frontend


# ── image helpers ─────────────────────────────────────────────────

def composite_on_white(img: Image.Image) -> Image.Image:
    """Flatten RGBA onto a white background, return RGB."""
    if img.mode == "RGBA":
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        bg.paste(img, mask=img.split()[3])
        return bg.convert("RGB")
    return img.convert("RGB")


def quantize_colors(rgb_img: Image.Image, n_colors: int) -> tuple[np.ndarray, list[tuple]]:
    """
    Quantize an RGB image to at most n_colors using PIL's median-cut quantizer.
    Returns:
        label_map  – (H, W) int array where each pixel = color index
        palette    – list of (R, G, B) tuples, one per color index
    """
    # PIL quantize works on P-mode; dither=0 keeps hard edges
    quant = rgb_img.quantize(colors=n_colors, dither=0)
    label_map = np.array(quant, dtype=np.uint8)          # (H, W) palette indices
    raw_palette = quant.getpalette()                     # flat list [R,G,B, R,G,B, ...]
    palette = [
        (raw_palette[i * 3], raw_palette[i * 3 + 1], raw_palette[i * 3 + 2])
        for i in range(n_colors)
    ]
    return label_map, palette


def mask_to_pbm(mask: np.ndarray, pbm_path: str) -> None:
    """Write a boolean (H,W) mask as a P4 PBM file (1 = foreground)."""
    h, w = mask.shape
    padded_w = (w + 7) // 8 * 8
    padded = np.zeros((h, padded_w), dtype=np.uint8)
    padded[:, :w] = mask.astype(np.uint8)
    with open(pbm_path, "wb") as f:
        f.write(f"P4\n{w} {h}\n".encode())
        for row in padded:
            f.write(np.packbits(row).tobytes())


def run_potrace(pbm_path: str, svg_path: str,
                turdsize: int, alphamax: float, opttolerance: float) -> None:
    """Run potrace and raise on failure."""
    cmd = [
        "potrace", pbm_path,
        "--svg",
        "--output", svg_path,
        "--turdsize",     str(turdsize),
        "--alphamax",     str(alphamax),
        "--opttolerance", str(opttolerance),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"potrace error: {result.stderr}")


def extract_paths_from_svg(svg_text: str) -> str:
    """
    Pull out just the <path .../> elements from a potrace SVG.
    Potrace always emits exactly one <g> with <path> children.
    """
    # Grab everything inside the <g ...> block
    match = re.search(r'<g\b[^>]*>(.*?)</g>', svg_text, re.DOTALL)
    return match.group(1).strip() if match else ""


def rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"


# ── main color conversion ──────────────────────────────────────────

def trace_color_image(img_path: str, out_path: str,
                      n_colors: int = 16,
                      turdsize: int = 2,
                      alphamax: float = 1.0,
                      opttolerance: float = 0.2) -> None:
    """
    Full color SVG conversion:
      1. Quantize image to n_colors dominant colors
      2. For each color, trace a binary mask with potrace
      3. Assemble all colored paths into one SVG (back-to-front by darkness)
    """
    img = Image.open(img_path)
    img = composite_on_white(img)
    w, h = img.size

    label_map, palette = quantize_colors(img, n_colors)

    # Find which color indices actually appear
    present_indices = [i for i in range(n_colors) if np.any(label_map == i)]

    # Sort back-to-front: lightest colors first (they form the background)
    def luminance(rgb):
        return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]

    present_indices.sort(key=lambda i: -luminance(palette[i]))  # light → dark

    all_path_blocks = []

    with tempfile.TemporaryDirectory() as tmp:
        for idx in present_indices:
            color = palette[idx]
            hex_color = rgb_to_hex(*color)

            # Skip pure white (background)
            if color == (255, 255, 255):
                continue

            # Build binary mask: 1 = this color, 0 = everything else
            mask = (label_map == idx)
            pixel_count = mask.sum()
            if pixel_count < turdsize:
                continue  # too tiny, skip

            pbm_path = os.path.join(tmp, f"layer_{idx}.pbm")
            svg_path = os.path.join(tmp, f"layer_{idx}.svg")

            mask_to_pbm(mask, pbm_path)
            try:
                run_potrace(pbm_path, svg_path, turdsize, alphamax, opttolerance)
            except RuntimeError:
                continue  # skip layers that fail

            with open(svg_path, "r") as f:
                svg_text = f.read()

            paths_inner = extract_paths_from_svg(svg_text)
            if not paths_inner:
                continue

            # Wrap in a group with this color as fill
            block = f'  <g fill="{hex_color}" stroke="none">\n    {paths_inner}\n  </g>'
            all_path_blocks.append(block)

    if not all_path_blocks:
        raise RuntimeError("No color layers could be traced.")

    # Assemble final SVG
    svg_out = (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}">\n'
        + "\n".join(all_path_blocks)
        + "\n</svg>\n"
    )

    with open(out_path, "w") as f:
        f.write(svg_out)


# ── legacy black-only conversion (kept for backward compat) ───────

def to_pbm(img_path: str, pbm_path: str, threshold: int = 128) -> None:
    img = Image.open(img_path).convert("RGBA")
    r, g, b, a = img.split()
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    bg.paste(img, mask=a)
    gray = bg.convert("L")
    arr = np.array(gray)
    if arr.mean() < 127:
        arr = 255 - arr
    binary = (arr < threshold).astype(np.uint8)
    h, w = binary.shape
    padded_w = (w + 7) // 8 * 8
    padded = np.zeros((h, padded_w), dtype=np.uint8)
    padded[:, :w] = binary
    with open(pbm_path, "wb") as f:
        f.write(f"P4\n{w} {h}\n".encode())
        for row in padded:
            f.write(np.packbits(row).tobytes())


def trace_with_potrace(img_path: str, out_path: str, threshold: int = 128,
                       turdsize: int = 2, alphamax: float = 1.0,
                       opttolerance: float = 0.2) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        pbm_path = os.path.join(tmp, "input.pbm")
        to_pbm(img_path, pbm_path, threshold=threshold)
        run_potrace(pbm_path, out_path, turdsize, alphamax, opttolerance)
    return out_path


def image_to_svg_ocr(img_path: str, out_path: str) -> str:
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
    return jsonify({"status": "ok", "service": "svgconverter-api"})


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
    n_colors     = int(request.form.get("n_colors", 16))   # NEW: color count param

    # Validate ranges
    threshold    = max(0,   min(255,  threshold))
    turdsize     = max(0,   min(100,  turdsize))
    alphamax     = max(0.0, min(1.33, alphamax))
    opttolerance = max(0.1, min(0.5,  opttolerance))
    n_colors     = max(2,   min(64,   n_colors))

    with tempfile.TemporaryDirectory() as tmp:
        ext      = os.path.splitext(file.filename)[1] or ".png"
        img_path = os.path.join(tmp, f"input{ext}")
        out_path = os.path.join(tmp, "output.svg")
        file.save(img_path)

        try:
            if mode == "text":
                image_to_svg_ocr(img_path, out_path)
            elif mode == "bw":
                # Explicit black-and-white mode (old behavior)
                trace_with_potrace(
                    img_path, out_path,
                    threshold=threshold,
                    turdsize=turdsize,
                    alphamax=alphamax,
                    opttolerance=opttolerance,
                )
            else:
                # Default: color mode (logo or any other value)
                trace_color_image(
                    img_path, out_path,
                    n_colors=n_colors,
                    turdsize=turdsize,
                    alphamax=alphamax,
                    opttolerance=opttolerance,
                )
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 500

        with open(out_path, "r") as f:
            svg_content = f.read()

    base_name = os.path.splitext(file.filename)[0]
    return (
        svg_content,
        200,
        {
            "Content-Type":        "image/svg+xml",
            "Content-Disposition": f'attachment; filename="{base_name}.svg"',
        },
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
