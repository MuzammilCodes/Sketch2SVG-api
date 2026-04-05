# SVGConverter --- Backend API

Flask + Potrace image-to-SVG conversion API. Deployable on Render.

## API Endpoints

### `GET /`

Health check. Returns `{"status": "ok"}`.

### `POST /convert`

Convert an image to SVG.

**Form fields:** \| Field \| Type \| Default \| Description \|
\|-------\|------\|---------\|-------------\| \| `image` \| file \|
required \| PNG, JPG, BMP, GIF \| \| `mode` \| string \| `logo` \|
`logo` (potrace) or `text` (OCR) \| \| `threshold` \| int \| `128` \|
0--255 darkness cutoff \| \| `turdsize` \| int \| `2` \| Speckle filter
(pixels) \| \| `alphamax` \| float \| `1.0` \| Corner smoothing 0--1.33
\| \| `opttolerance` \| float \| `0.2` \| Curve accuracy 0.1--0.5 \|

**Response:** SVG file (`image/svg+xml`)

------------------------------------------------------------------------

## Local Development

``` bash
# 1. Install potrace (Linux/Mac)
sudo apt-get install potrace      # Ubuntu/Debian
brew install potrace              # macOS

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

``` bash
curl -X POST http://localhost:5000/convert \
  -F "image=@your_logo.png" \
  -F "threshold=128" \
  -o output.svg
```

------------------------------------------------------------------------

## Deploy to Render

### Step 1 --- Push backend to GitHub

``` bash
cd svgconverter/backend
git init
git add .
git commit -m "feat: svgconverter backend"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/svgconverter-backend.git
git push -u origin main
```

### Step 2 --- Create Render Web Service

1.  Go to https://render.com and sign in\
2.  Click **"New +" → Web Service**\
3.  Connect your GitHub repository\
4.  Select your `svgconverter-backend` repo

### Step 3 --- Configure the service

-   Environment: Docker (recommended) or Python\

-   Build Command:

        pip install -r requirements.txt

-   Start Command:

        gunicorn app:app

### Step 4 --- Add environment variables

    PORT=5000

Make sure your Flask app uses:

``` python
import os
port = int(os.environ.get("PORT", 5000))
app.run(host="0.0.0.0", port=port)
```

### Step 5 --- Access your API

Example:

    https://svgconverter-backend.onrender.com

### Step 6 --- Connect to frontend

    VITE_API_URL=https://your-render-url.onrender.com

------------------------------------------------------------------------

## Docker (local test before deploying)

``` bash
docker build -t svgconverter-backend .
docker run -p 5000:5000 svgconverter-backend
```

------------------------------------------------------------------------

## Project Structure

    backend/
    ├── app.py
    ├── requirements.txt
    ├── Dockerfile
    ├── railway.toml
    └── README.md

------------------------------------------------------------------------

## Notes

-   Free tier may sleep after inactivity\

-   Ensure potrace is installed in Dockerfile:

        RUN apt-get update && apt-get install -y potrace

-   Use gunicorn for production
