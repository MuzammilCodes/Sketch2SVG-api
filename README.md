# Vectorizer — Backend API

Flask + Potrace image-to-SVG conversion API. Deployable on Railway.

## API Endpoints

### `GET /`
Health check. Returns `{"status": "ok"}`.

### `POST /convert`
Convert an image to SVG.

**Form fields:**
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `image` | file | required | PNG, JPG, BMP, GIF |
| `mode` | string | `logo` | `logo` (potrace) or `text` (OCR) |
| `threshold` | int | `128` | 0–255 darkness cutoff |
| `turdsize` | int | `2` | Speckle filter (pixels) |
| `alphamax` | float | `1.0` | Corner smoothing 0–1.33 |
| `opttolerance` | float | `0.2` | Curve accuracy 0.1–0.5 |

**Response:** SVG file (`image/svg+xml`)

---

## Local Development

```bash
# 1. Install potrace (Linux/Mac)
sudo apt-get install potrace      # Ubuntu/Debian
brew install potrace               # macOS

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate           # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run dev server
python app.py
# → running on http://localhost:5000
```

**Test it:**
```bash
curl -X POST http://localhost:5000/convert \
  -F "image=@your_logo.png" \
  -F "threshold=128" \
  -o output.svg
```

---

## Deploy to Railway

### Step 1 — Push backend to GitHub

```bash
cd vectorizer/backend
git init
git add .
git commit -m "feat: vectorizer backend"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/vectorizer-backend.git
git push -u origin main
```

### Step 2 — Create Railway project

1. Go to [railway.app](https://railway.app) and sign in with GitHub
2. Click **"New Project"**
3. Choose **"Deploy from GitHub repo"**
4. Select your `vectorizer-backend` repo
5. Railway auto-detects the `Dockerfile` — click **Deploy**

### Step 3 — Get your public URL

1. In Railway dashboard → your service → **Settings** tab
2. Under **Networking** → click **"Generate Domain"**
3. Copy the URL — it looks like: `https://vectorizer-backend-production.up.railway.app`

### Step 4 — Set environment variables (optional)

In Railway → your service → **Variables** tab, you can add:
```
PORT=5000   # Railway sets this automatically, no action needed
```

### Step 5 — Use the URL in your frontend

In your React frontend, set:
```
VITE_API_URL=https://your-railway-url.up.railway.app
```

---

## Docker (local test before deploying)

```bash
docker build -t vectorizer-backend .
docker run -p 5000:5000 vectorizer-backend
```

---

## Project Structure

```
backend/
├── app.py            ← Flask app + all conversion logic
├── requirements.txt  ← Python dependencies
├── Dockerfile        ← Container definition (installs potrace)
├── railway.toml      ← Railway deployment config
└── README.md
```
