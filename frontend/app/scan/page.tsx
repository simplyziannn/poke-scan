"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type ChangeEvent } from "react";

type Candidate = {
  card_id: string;
  name: string;
  set_name?: string;
  number?: string;
  confidence: number;
  market_price_usd?: number;
  grade_9_price_usd?: number;
  psa_10_price_usd?: number;
  price_note?: string;
  price_source?: string;
  price_source_url?: string;
  price_updated_at?: string;
};

type IdentifyResponse = {
  extracted_number?: string;
  extracted_name?: string;
  raw_text: string;
  candidates: Candidate[];
};

const MAX_IMAGE_DIMENSION = 1600;
const JPEG_QUALITY = 0.82;
const SCAN_TIMEOUT_MS = 60000;

async function preprocessImage(file: File): Promise<File> {
  if (!file.type.startsWith("image/")) {
    return file;
  }

  const bitmap = await createImageBitmap(file);
  const sourceWidth = bitmap.width;
  const sourceHeight = bitmap.height;
  const maxDimension = Math.max(sourceWidth, sourceHeight);

  if (maxDimension <= MAX_IMAGE_DIMENSION && file.size < 1_500_000) {
    bitmap.close();
    return file;
  }

  const scale = MAX_IMAGE_DIMENSION / maxDimension;
  const targetWidth = Math.max(1, Math.round(sourceWidth * scale));
  const targetHeight = Math.max(1, Math.round(sourceHeight * scale));

  const canvas = document.createElement("canvas");
  canvas.width = targetWidth;
  canvas.height = targetHeight;
  const context = canvas.getContext("2d");
  if (!context) {
    bitmap.close();
    return file;
  }
  context.drawImage(bitmap, 0, 0, targetWidth, targetHeight);
  bitmap.close();

  const blob = await new Promise<Blob | null>((resolve) =>
    canvas.toBlob(resolve, "image/jpeg", JPEG_QUALITY),
  );
  if (!blob) {
    return file;
  }

  const safeBaseName = file.name.replace(/\.[a-zA-Z0-9]+$/, "");
  return new File([blob], `${safeBaseName}_compressed.jpg`, { type: "image/jpeg" });
}

export default function ScanPage() {
  const rawApiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
  const apiBaseUrl = rawApiBaseUrl.startsWith("http://") || rawApiBaseUrl.startsWith("https://")
    ? rawApiBaseUrl
    : `https://${rawApiBaseUrl}`;
  const [isScanning, setIsScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<IdentifyResponse | null>(null);
  const [selectedFileName, setSelectedFileName] = useState<string>("No image selected");
  const [selectedImageUrl, setSelectedImageUrl] = useState<string | null>(null);
  const [inputMode, setInputMode] = useState<"camera" | "photos">("camera");
  const [uploadSizeLabel, setUploadSizeLabel] = useState<string | null>(null);
  const cameraInputRef = useRef<HTMLInputElement>(null);
  const photosInputRef = useRef<HTMLInputElement>(null);

  const onFileSelected = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }

    setSelectedFileName(file.name);
    setSelectedImageUrl((prev) => {
      if (prev) {
        URL.revokeObjectURL(prev);
      }
      return URL.createObjectURL(file);
    });
    setUploadSizeLabel(null);

    setIsScanning(true);
    setError(null);
    setResult(null);

    try {
      const processed = await preprocessImage(file);
      setUploadSizeLabel(`${(processed.size / 1024).toFixed(0)} KB`);

      const formData = new FormData();
      formData.append("image", processed);

      const abort = new AbortController();
      const timeoutId = setTimeout(() => abort.abort(), SCAN_TIMEOUT_MS);
      const response = await fetch(`${apiBaseUrl}/identify`, {
        method: "POST",
        body: formData,
        signal: abort.signal,
      });
      clearTimeout(timeoutId);

      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail ?? "Failed to identify card");
      }

      const payload = (await response.json()) as IdentifyResponse;
      setResult(payload);
    } catch (scanError) {
      const message =
        scanError instanceof Error
          ? scanError.name === "AbortError"
            ? "Scan timed out. Try PHOTOS mode or a smaller image."
            : scanError.message
          : "Unknown scan error";
      setError(message);
    } finally {
      setIsScanning(false);
    }
  };

  const clearResult = () => {
    setError(null);
    setResult(null);
    setSelectedFileName("No image selected");
    setUploadSizeLabel(null);
    setSelectedImageUrl((prev) => {
      if (prev) {
        URL.revokeObjectURL(prev);
      }
      return null;
    });
  };

  const shownCandidates = result?.candidates ?? [];
  const bestCandidate = shownCandidates[0];
  const topPricedCandidate = shownCandidates.find((candidate) => typeof candidate.market_price_usd === "number");

  useEffect(() => {
    return () => {
      if (selectedImageUrl) {
        URL.revokeObjectURL(selectedImageUrl);
      }
    };
  }, [selectedImageUrl]);

  return (
    <main>
      <section className="pokedex-shell scan-shell">
        <div className="pokedex-top">
          <div className="lights" aria-hidden>
            <span className="light blue" />
            <span className="light red" />
            <span className="light yellow" />
            <span className="light green" />
          </div>
          <span className="brand">CARD IDENTIFIER</span>
        </div>

        <div className="pokedex-body">
          <div className="screen">
            <div className="screen-inner screen-stack scan-screen">
              <h1>SCAN CARD</h1>
              <p className="muted">Use camera mode or upload a photo.</p>

              <div className="button-row">
                <button
                  type="button"
                  className={`btn mode-btn ${inputMode === "camera" ? "mode-active" : ""}`}
                  onClick={() => setInputMode("camera")}
                  disabled={isScanning}
                >
                  CAMERA
                </button>
                <button
                  type="button"
                  className={`btn mode-btn ${inputMode === "photos" ? "mode-active" : ""}`}
                  onClick={() => setInputMode("photos")}
                  disabled={isScanning}
                >
                  PHOTOS
                </button>
              </div>

              <div className="picker-shell">
                <button
                  type="button"
                  className="picker-cta"
                  onClick={() =>
                    inputMode === "camera"
                      ? cameraInputRef.current?.click()
                      : photosInputRef.current?.click()
                  }
                  disabled={isScanning}
                >
                  {inputMode === "camera" ? "Open Camera" : "Pick from Photos"}
                </button>
                <span className="picker-meta">
                  {selectedFileName === "No image selected" ? "No image selected" : selectedFileName}
                </span>
              </div>
              <input
                ref={cameraInputRef}
                className="hidden-input"
                type="file"
                accept="image/*"
                capture="environment"
                onChange={onFileSelected}
                disabled={isScanning || inputMode !== "camera"}
                aria-hidden
                tabIndex={-1}
              />
              <input
                ref={photosInputRef}
                className="hidden-input"
                type="file"
                accept="image/*"
                onChange={onFileSelected}
                disabled={isScanning || inputMode !== "photos"}
                aria-hidden
                tabIndex={-1}
              />

              <p className="muted">FILE: {selectedFileName}</p>
              {uploadSizeLabel ? <p className="muted">UPLOAD SIZE: {uploadSizeLabel}</p> : null}

              <div className="button-row">
                <Link href="/" className="btn secondary">
                  BACK
                </Link>
                <button type="button" className="btn" onClick={clearResult} disabled={isScanning}>
                  CLEAR
                </button>
                {isScanning ? (
                  <span className="chip chip-scan">
                    <span className="scan-dot" />
                    SCANNING
                  </span>
                ) : (
                  <span className="ready-pill">READY</span>
                )}
              </div>

              {isScanning ? (
                <div className="scanner-bar" aria-hidden>
                  <div className="scanner-beam" />
                </div>
              ) : null}

              {error && <p className="error">ERROR: {error}</p>}
            </div>
          </div>

          {bestCandidate ? (
            <section className="section">
              <h2>MATCHED CARD</h2>
              <div className="match-grid">
                {selectedImageUrl ? (
                  <img src={selectedImageUrl} alt="Uploaded card" className="card-preview" />
                ) : (
                  <div className="card-preview placeholder">No preview</div>
                )}
                <div className="screen-stack">
                  <p>
                    <strong>{bestCandidate.name}</strong>
                  </p>
                  <p className="muted">
                    #{bestCandidate.number ?? "?"} | conf {bestCandidate.confidence.toFixed(3)}
                  </p>
                  {bestCandidate.price_source_url ? (
                    <a href={bestCandidate.price_source_url} target="_blank" rel="noreferrer" className="muted">
                      Open source listing
                    </a>
                  ) : null}
                </div>
              </div>
            </section>
          ) : null}

          <section className="section pricing-panel">
            <h2>PRICING</h2>
            {topPricedCandidate ? (
              <>
                <p>
                  <strong>{topPricedCandidate.name}</strong>
                </p>
                <div className="price-grid" role="table" aria-label="Card price comparison">
                  <div className="price-row" role="row">
                    <span className="price-label" role="cell">
                      UNGRADED
                    </span>
                    <span className="price-value" role="cell">
                      {typeof topPricedCandidate.market_price_usd === "number"
                        ? `$${topPricedCandidate.market_price_usd.toFixed(2)}`
                        : "N/A"}
                    </span>
                  </div>
                  <div className="price-row" role="row">
                    <span className="price-label" role="cell">
                      GRADE 9
                    </span>
                    <span className="price-value" role="cell">
                      {typeof topPricedCandidate.grade_9_price_usd === "number"
                        ? `$${topPricedCandidate.grade_9_price_usd.toFixed(2)}`
                        : "N/A"}
                    </span>
                  </div>
                  <div className="price-row" role="row">
                    <span className="price-label" role="cell">
                      PSA 10
                    </span>
                    <span className="price-value" role="cell">
                      {typeof topPricedCandidate.psa_10_price_usd === "number"
                        ? `$${topPricedCandidate.psa_10_price_usd.toFixed(2)}`
                        : "N/A"}
                    </span>
                  </div>
                </div>
                {topPricedCandidate.price_source_url ? (
                  <p className="muted">
                    Source:{" "}
                    <a href={topPricedCandidate.price_source_url} target="_blank" rel="noreferrer">
                      {topPricedCandidate.price_source ?? "Price source"}
                    </a>
                  </p>
                ) : null}
                {topPricedCandidate.price_updated_at ? (
                  <p className="muted">Updated: {new Date(topPricedCandidate.price_updated_at).toLocaleString()}</p>
                ) : null}
              </>
            ) : (
              <p className="muted">No pricing yet. Scan a card with a confident match.</p>
            )}
          </section>
        </div>
        <div className="hinge" />
      </section>
    </main>
  );
}
