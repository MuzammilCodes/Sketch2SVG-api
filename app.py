import os
import re
import tempfile
import subprocess
import numpy as np
from PIL import Image
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)


# ─────────────────────────────────────────────
# DETECTION: is the image greyscale / B&W?
# ─────────────────────────────────────────────

def is_greyscale(img_path: str,
                 color_threshold: int = 20,
                 saturation_ratio: float = 0.02) -> bool:
    """
    Return True when the image is effectively black-and-white / greyscale.

    For every pixel we compute  max(R,G,B) - min(R,G,B)  ("colour spread").
    If fewer than `saturation_ratio` (default 2 %) of pixels have a spread
    above `color_threshold` (default 20), the image is treated as greyscale.

    Handles transparent backgrounds correctly by compositing onto white first.
    """
    img = Image.open(img_path)
    if img.mode == "RGBA":
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        bg.paste(img, mask=img.split()[3])
        img = bg
    rgb = np.array(img.convert("RGB"), dtype=np.int32)   # H×W×3
    spread = rgb.max(axis=2) - rgb.min(axis=2)           # H×W  (0 = grey)
    coloured_fraction = (spread > color_threshold).mean()
    return coloured_fraction < saturation_ratio


# ─────────────────────────────────────────────
# SHARED HELPERS
# ─────────────────────────────────────────────

def composite_on_white(img: Image.Image) -> Image.Image:
    """Flatten RGBA onto white, return RGB."""
    if img.mode == "RGBA":
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        bg.paste(img, mask=img.split()[3])
        return bg.convert("RGB")
    return img.convert("RGB")


def mask_to_pbm(mask: np.ndarray, pbm_path: str) -> None:
    """Write a boolean H×W mask as a P4 (binary) PBM file."""
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
    cmd = [
        "potrace", pbm_path,
        "--svg", "--output", svg_path,
        "--turdsize",     str(turdsize),
        "--alphamax",     str(alphamax),
        "--opttolerance", str(opttolerance),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"potrace error: {result.stderr}")


def extract_svg_metadata(svg_text: str) -> dict:
    """
    Pull width, height, viewBox, the <g> transform, and inner path markup
    out of a potrace-generated SVG.

    WHY: potrace scales coordinates ~10x internally (72 dpi default), so a
    1280 px image produces path data in the ~12 800 coordinate range.
    Using the raw pixel size as viewBox shows only the top-left corner.
    We must reuse potrace's own viewBox + transform verbatim.
    """
    vb    = re.search(r'<svg\b[^>]*\bviewBox=["\']([^"\']+)["\']', svg_text, re.I)
    w     = re.search(r'<svg\b[^>]*\bwidth=["\']([^"\']+)["\']',   svg_text, re.I)
    h     = re.search(r'<svg\b[^>]*\bheight=["\']([^"\']+)["\']',  svg_text, re.I)
    g     = re.search(r'<g\b([^>]*)>',                             svg_text, re.DOTALL)
    t     = re.search(r'\btransform=["\']([^"\']+)["\']', g.group(1) if g else "")
    inner = re.search(r'<g\b[^>]*>(.*?)</g>',                      svg_text, re.DOTALL)
    return {
        "width":     w.group(1)             if w     else None,
        "height":    h.group(1)             if h     else None,
        "viewbox":   vb.group(1)            if vb    else None,
        "transform": t.group(1)             if t     else None,
        "paths":     inner.group(1).strip() if inner else "",
    }


def rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"


# ─────────────────────────────────────────────
# PATH A — GREYSCALE / B&W  (original, proven)
# ─────────────────────────────────────────────

def to_pbm_bw(img_path: str, pbm_path: str, threshold: int = 128) -> None:
    """Convert any image to a 1-bit PBM for the B&W potrace path."""
    img = Image.open(img_path).convert("RGBA")
    bg  = Image.new("RGBA", img.size, (255, 255, 255, 255))
    bg.paste(img, mask=img.split()[3])
    gray   = bg.convert("L")
    arr    = np.array(gray)
    if arr.mean() < 127:          # auto-invert dark-background images
        arr = 255 - arr
    binary = (arr < threshold).astype(np.uint8)
    mask_to_pbm(binary, pbm_path)


def trace_bw(img_path: str, out_path: str,
             threshold: int = 128,
             turdsize: int = 2,
             alphamax: float = 1.0,
             opttolerance: float = 0.2) -> None:
    """
    Original single-pass potrace for greyscale/B&W images.
    Potrace writes the complete SVG itself, so the viewBox is always correct.
    This is the path that worked perfectly before the colour changes.
    """
    with tempfile.TemporaryDirectory() as tmp:
        pbm_path = os.path.join(tmp, "input.pbm")
        to_pbm_bw(img_path, pbm_path, threshold=threshold)
        run_potrace(pbm_path, out_path, turdsize, alphamax, opttolerance)


# ─────────────────────────────────────────────
# PATH B — COLOUR  (multi-layer)
# ─────────────────────────────────────────────

def quantize_colors(rgb_img: Image.Image, n_colors: int):
    """Quantize RGB image to n_colors. Returns (label_map H×W, palette list)."""
    quant     = rgb_img.quantize(colors=n_colors, dither=0)
    label_map = np.array(quant, dtype=np.uint8)
    raw       = quant.getpalette()
    palette   = [(raw[i*3], raw[i*3+1], raw[i*3+2]) for i in range(n_colors)]
    return label_map, palette


def trace_color(img_path: str, out_path: str,
                n_colors: int = 16,
                turdsize: int = 2,
                alphamax: float = 1.0,
                opttolerance: float = 0.2) -> None:
    """
    Multi-layer colour SVG conversion:
      1. Quantize to n_colors dominant colours.
      2. For each colour, build a binary mask → run potrace → extract paths.
      3. Stack coloured <g> layers using potrace's own viewBox + transform.

    KEY FIX: potrace coordinates are ~10× pixel size internally.
    We extract viewBox/transform from potrace's own output and reuse them,
    never using raw pixel dimensions as the viewBox.

    Layers are ordered light → dark so darker content paints on top.
    Near-white pixels (luminance > 245) are skipped as background.
    """
    img = Image.open(img_path)
    img = composite_on_white(img)

    label_map, palette = quantize_colors(img, n_colors)
    present            = [i for i in range(n_colors) if np.any(label_map == i)]

    def luminance(rgb):
        return 0.299*rgb[0] + 0.587*rgb[1] + 0.114*rgb[2]

    present.sort(key=lambda i: -luminance(palette[i]))   # light first → dark last

    path_blocks = []
    svg_meta    = None

    with tempfile.TemporaryDirectory() as tmp:
        for idx in present:
            color = palette[idx]

            if luminance(color) > 245:   # skip near-white background
                continue

            mask = (label_map == idx)
            if mask.sum() < max(turdsize, 1):
                continue

            pbm_path  = os.path.join(tmp, f"layer_{idx}.pbm")
            layer_svg = os.path.join(tmp, f"layer_{idx}.svg")

            mask_to_pbm(mask, pbm_path)
            try:
                run_potrace(pbm_path, layer_svg, turdsize, alphamax, opttolerance)
            except RuntimeError:
                continue

            with open(layer_svg) as f:
                svg_text = f.read()

            meta = extract_svg_metadata(svg_text)
            if not meta["paths"]:
                continue

            if svg_meta is None:
                svg_meta = meta     # capture canvas geometry from first layer

            hex_color = rgb_to_hex(*color)
            t_attr    = f' transform="{meta["transform"]}"' if meta["transform"] else ""
            path_blocks.append(
                f'  <g fill="{hex_color}" stroke="none"{t_attr}>\n'
                f'    {meta["paths"]}\n'
                f'  </g>'
            )

    if not path_blocks or svg_meta is None:
        raise RuntimeError("No colour layers could be traced.")

    # Use potrace's own coordinate space — never raw pixel dimensions
    svg_w  = svg_meta["width"]   or "100%"
    svg_h  = svg_meta["height"]  or "100%"
    svg_vb = svg_meta["viewbox"] or f"0 0 {svg_w} {svg_h}"

    with open(out_path, "w") as f:
        f.write(
            f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{svg_w}" height="{svg_h}" viewBox="{svg_vb}">\n'
            + "\n".join(path_blocks)
            + "\n</svg>\n"
        )


# ─────────────────────────────────────────────
# PATH C — TEXT / OCR  (unchanged)
# ─────────────────────────────────────────────

def image_to_svg_ocr(img_path: str, out_path: str) -> None:
    try:
        import pytesseract
        import svgwrite
    except ImportError:
        raise RuntimeError("pytesseract or svgwrite not installed for OCR mode")
    img  = Image.open(img_path)
    w, h = img.size
    dwg  = svgwrite.Drawing(out_path, size=(w, h))
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    for i, word in enumerate(data["text"]):
        if word.strip():
            dwg.add(dwg.text(
                word,
                insert=(data["left"][i], data["top"][i] + data["height"][i]),
                font_size=max(data["height"][i], 8),
            ))
    dwg.save()


# ─────────────────────────────────────────────
# FLASK ROUTES
# ─────────────────────────────────────────────

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "svgconverter-api"})


@app.route("/convert", methods=["POST"])
def convert():
    if "image" not in request.files:
        return jsonify({"error": "No image file provided"}), 400

    file = request.files["image"]
    if not file.filename:
        return jsonify({"error": "Empty filename"}), 400

    # ── parameters ──────────────────────────────────────────────────
    # mode: "auto" (default) | "bw" | "color" | "text"
    mode         = request.form.get("mode", "auto")
    threshold    = max(0,   min(255,  int(  request.form.get("threshold",    128))))
    turdsize     = max(0,   min(100,  int(  request.form.get("turdsize",       2))))
    alphamax     = max(0.0, min(1.33, float(request.form.get("alphamax",     1.0))))
    opttolerance = max(0.1, min(0.5,  float(request.form.get("opttolerance", 0.2))))
    n_colors     = max(2,   min(64,   int(  request.form.get("n_colors",      16))))

    with tempfile.TemporaryDirectory() as tmp:
        ext      = os.path.splitext(file.filename)[1] or ".png"
        img_path = os.path.join(tmp, f"input{ext}")
        out_path = os.path.join(tmp, "output.svg")
        file.save(img_path)

        try:
            if mode == "text":
                # OCR-based SVG
                image_to_svg_ocr(img_path, out_path)

            elif mode == "bw":
                # Force B&W single-pass potrace
                trace_bw(img_path, out_path,
                         threshold=threshold, turdsize=turdsize,
                         alphamax=alphamax, opttolerance=opttolerance)

            elif mode == "color":
                # Force multi-layer colour potrace
                trace_color(img_path, out_path,
                            n_colors=n_colors, turdsize=turdsize,
                            alphamax=alphamax, opttolerance=opttolerance)

            else:
                # ── AUTO-DETECT (default) ────────────────────────────
                # Analyse pixel colour spread to decide which pipeline to use:
                #   • Greyscale / B&W image  →  trace_bw()    (original, proven)
                #   • Colour image           →  trace_color() (multi-layer)
                if is_greyscale(img_path):
                    trace_bw(img_path, out_path,
                             threshold=threshold, turdsize=turdsize,
                             alphamax=alphamax, opttolerance=opttolerance)
                else:
                    trace_color(img_path, out_path,
                                n_colors=n_colors, turdsize=turdsize,
                                alphamax=alphamax, opttolerance=opttolerance)

        except RuntimeError as e:
            return jsonify({"error": str(e)}), 500

        with open(out_path) as f:
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
