# Poke Scan (MVP)

Local-first Pokemon card scanner demo with:
- `frontend/`: Next.js (TypeScript, App Router), mobile-friendly upload/capture UI
- `backend/`: FastAPI + Tesseract OCR (`pytesseract`) + fuzzy card matching (`rapidfuzz`)
- Catalog source: PriceCharting Nihil Zero set page (no per-card hardcoding in match logic)

## 1) Install OCR on macOS

```bash
brew install tesseract
brew install tesseract-lang
```

## 2) Run backend (port 8000)

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r app/requirements.txt
chmod +x run.sh
./run.sh
```

Health check:

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{"ok":true}
```

## 3) Run frontend (port 3000)

In a new terminal:

```bash
cd frontend
npm install
npm run dev
```

## 4) Test scanning flow

1. Open `http://localhost:3000/scan` on your phone or desktop browser.
2. Use camera/file input to select a card image.
3. The app uploads image data to `http://localhost:8000/identify`.
4. View extracted number/name, top 3 candidates, and raw OCR text.
5. Backend syncs the Pokemon Japanese Nihil Zero set catalog from PriceCharting and matches against the full set by collector number + name.
6. Pricing panel shows ungraded market price from the matched catalog entry when available.

## API summary

- `GET /health` -> `{ "ok": true }`
- `GET /catalog/status` -> count of synced Nihil Zero cards
- `POST /identify` (multipart form-data, field name: `image`) -> OCR + candidates payload

This repo uses no API keys. Catalog and pricing data are parsed from PriceCharting HTML for the Nihil Zero set and cached locally after the first successful sync.
