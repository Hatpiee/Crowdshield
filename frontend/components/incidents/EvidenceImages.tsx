"use client";

import { useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL;

interface RegionBox {
  x_min: number;
  y_min: number;
  x_max: number;
  y_max: number;
}

interface ObservationForOverlay {
  id: string;
  category: string;
  region: RegionBox;
}

async function authedFetch(token: string, path: string): Promise<Response> {
  return fetch(`${API_URL}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
}

// Step 2 / Resolution 2: same signed-media-token pattern as VideoPlayer.tsx
// (Phase 21) and HeatmapViewer.tsx (Phase 22) — fetch one short-lived
// token, then use it as a `?token=` query param on both image routes
// (ONE evidence token is valid for both frame-image and roi-image).
export default function EvidenceImages({
  evidencePackageId,
  accessToken,
  observations,
}: {
  evidencePackageId: string;
  accessToken: string;
  observations: ObservationForOverlay[];
}) {
  const [images, setImages] = useState<{ frameSrc: string; roiSrc: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    authedFetch(accessToken, `/api/v1/evidence/${evidencePackageId}/access-token`)
      .then((res) => res.json())
      .then((body) => {
        if (cancelled) return;
        if (!body.success) {
          setError("Could not load evidence images.");
          return;
        }
        const token = body.data.token;
        setImages({
          frameSrc: `${API_URL}/api/v1/evidence/${evidencePackageId}/frame-image?token=${token}`,
          roiSrc: `${API_URL}/api/v1/evidence/${evidencePackageId}/roi-image?token=${token}`,
        });
      })
      .catch(() => {
        if (!cancelled) setError("Could not load evidence images.");
      });
    return () => {
      cancelled = true;
    };
  }, [evidencePackageId, accessToken]);

  if (error) {
    return <p className="text-sm text-cs-muted">{error}</p>;
  }

  if (!images) {
    return (
      <p className="font-mono text-xs tracking-[0.15em] text-cs-muted uppercase">
        Loading images…
      </p>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      <div>
        <p className="mb-1 font-mono text-[10px] tracking-[0.15em] text-cs-muted uppercase">
          Representative Frame
        </p>
        {/* Resolution 3's confirmed finding: VLM observation `region`
            coordinates are normalized fractions of THIS image (the full
            representative frame) — both minicpm_vlm.py's own system
            prompt ("CRITICAL FORMAT RULE ... FULL FRAME's dimensions")
            and NormalizedBoundingBox's own docstring already unambiguously
            say so. The overlay is drawn HERE, not on the ROI crop — the
            actual coordinate space, not the ROI-crop guess this phase's
            own brief floated as "most likely." A plain inline-block
            wrapper (not a forced aspect-ratio box) means the wrapper's
            box IS the image's own rendered box, so percentage-based
            positioning aligns exactly with no letterboxing offset to
            account for. */}
        <div className="relative inline-block max-w-full">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={images.frameSrc}
            alt="Representative frame"
            className="block max-w-full border border-cs-border bg-black"
          />
          {/* Numbered per observation (1-indexed), matching the SAME
              number rendered on each VLM Observation card below
              (IncidentTimeline.tsx) — with 2+ real observations, same-
              colored boxes alone give no persistent way to tell which box
              belongs to which card (a hover-only title= tooltip isn't a
              reliable substitute). A visible index badge is a small,
              real fix for that, not just cosmetic: multiple boxes were
              already rendering at genuinely distinct, correctly-scaled
              positions per observation before this — confirmed with real
              synthetic 2-observation data (non-overlapping regions
              produced two independently-positioned boxes matching their
              own region exactly) — this only adds the missing
              box-to-card correlation. */}
          {observations.map((obs, index) => (
            <div
              key={obs.id}
              className="absolute border-2 border-cs-amber"
              style={{
                left: `${obs.region.x_min * 100}%`,
                top: `${obs.region.y_min * 100}%`,
                width: `${(obs.region.x_max - obs.region.x_min) * 100}%`,
                height: `${(obs.region.y_max - obs.region.y_min) * 100}%`,
              }}
              title={`${index + 1}. ${obs.category}`}
            >
              <span className="absolute -top-0.5 -left-0.5 flex h-4 w-4 -translate-y-full items-center justify-center bg-cs-amber font-mono text-[10px] font-bold text-cs-bg">
                {index + 1}
              </span>
            </div>
          ))}
        </div>
      </div>
      <div>
        <p className="mb-1 font-mono text-[10px] tracking-[0.15em] text-cs-muted uppercase">
          ROI Crop (VLM zoom target)
        </p>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={images.roiSrc}
          alt="ROI crop"
          className="max-w-full border border-cs-border bg-black"
        />
      </div>
    </div>
  );
}
