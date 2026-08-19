"use client";

import { useEffect, useState } from "react";

import { LIVE_POLL_INTERVAL_MS } from "@/lib/livePolling";
import { pickCurrentItem } from "@/lib/nearestSnapshot";
import type { HeatmapSnapshotItem, HeatmapType } from "@/lib/types";

const API_URL = process.env.NEXT_PUBLIC_API_URL;

// §24's real HeatmapType values (Phase 12) plus this widget's own display
// label — RISK is the default selected tab (the operator's primary
// "act-on-this" view, per Phase 12's own established framing).
const TYPE_TABS: { type: HeatmapType; label: string }[] = [
  { type: "DENSITY", label: "Density" },
  { type: "PRESSURE", label: "Pressure" },
  { type: "FLOW_CONGESTION", label: "Flow / Congestion" },
  { type: "RISK", label: "Risk" },
  { type: "PREDICTIVE", label: "Predictive" },
];
const DEFAULT_TYPE: HeatmapType = "RISK";

async function authedFetch(token: string, path: string): Promise<Response> {
  return fetch(`${API_URL}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export default function HeatmapViewer({
  sessionId,
  accessToken,
  isTerminal,
  playbackTime,
}: {
  sessionId: string;
  accessToken: string;
  isTerminal: boolean;
  playbackTime: number | null;
}) {
  const [selectedType, setSelectedType] = useState<HeatmapType>(DEFAULT_TYPE);
  const [heatmaps, setHeatmaps] = useState<HeatmapSnapshotItem[]>([]);
  // Resolution 4 (heatmap black-bar/letterboxing fix): the container
  // previously used a hardcoded `aspect-video` (16:9) Tailwind class —
  // heatmap images are actually rendered at the SOURCE VIDEO's real
  // width/height (Phase 5/12), which is frequently NOT 16:9, so
  // `object-contain` inside a wrongly-shaped box produced large black
  // bars. Rather than plumbing width/height through via a new API call,
  // this reads the REAL rendered image's own natural dimensions once it
  // loads and sizes the container to match exactly — always correct,
  // whatever the source video's actual aspect ratio turns out to be, with
  // no extra backend round trip. Falls back to 16:9 only before any image
  // has ever loaded (nothing to be wrong about yet).
  const [naturalAspect, setNaturalAspect] = useState<number | null>(null);
  // Keyed by heatmap id (not two separate useState calls) specifically so
  // this effect never needs to synchronously reset state when `current`
  // changes — every setState call below happens inside an async
  // fetch/catch callback instead, and the render below derives "is this
  // result still for the currently-selected heatmap" by comparing ids.
  const [imageResult, setImageResult] = useState<
    { id: string; src: string } | { id: string; error: string } | null
  >(null);

  // Resolution 2: fetch the FULL heatmap list (all 5 types) ONCE, then
  // re-fetch on the SAME live-polling cadence/lifecycle as every other
  // widget while the session is still non-terminal (§24 frozen decision —
  // reusing LIVE_POLL_INTERVAL_MS/TERMINAL_SESSION_STATUSES exactly, not a
  // second differently-timed mechanism). "Nearest of type X" is then
  // derived CLIENT-SIDE below via the shared pickCurrentItem helper — no
  // new backend lookup route.
  useEffect(() => {
    let cancelled = false;

    async function fetchHeatmaps() {
      const res = await authedFetch(accessToken, `/api/v1/sessions/${sessionId}/heatmaps`);
      if (cancelled || !res.ok) return;
      const body = await res.json();
      if (body.success) setHeatmaps(body.data.items);
    }

    fetchHeatmaps();
    if (isTerminal) return () => {
      cancelled = true;
    };
    const interval = setInterval(fetchHeatmaps, LIVE_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [sessionId, accessToken, isTerminal]);

  // The list route orders by created_at DESC (Phase 12) — pickCurrentItem
  // requires ASCENDING timestamp_seconds order, so filter-then-sort before
  // handing it off, rather than special-casing the shared helper itself.
  const filtered = heatmaps
    .filter((h) => h.heatmap_type === selectedType)
    .sort((a, b) => a.timestamp_seconds - b.timestamp_seconds);
  const current = pickCurrentItem(filtered, isTerminal, playbackTime);

  useEffect(() => {
    if (!current) return;
    const heatmapId = current.id;
    let cancelled = false;
    authedFetch(accessToken, `/api/v1/heatmaps/${heatmapId}/access-token`)
      .then((res) => res.json())
      .then((body) => {
        if (cancelled) return;
        if (!body.success) {
          setImageResult({ id: heatmapId, error: "Could not load heatmap image." });
          return;
        }
        setImageResult({
          id: heatmapId,
          src: `${API_URL}/api/v1/heatmaps/${heatmapId}/image?token=${body.data.token}`,
        });
      })
      .catch(() => {
        if (!cancelled) {
          setImageResult({ id: heatmapId, error: "Could not load heatmap image." });
        }
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current?.id, accessToken]);

  // Only trust imageResult if it's still for the CURRENTLY selected
  // heatmap — guards against a stale in-flight fetch from a previous
  // selection resolving after the operator has already switched type/time.
  const activeResult = imageResult && current && imageResult.id === current.id ? imageResult : null;
  const imageSrc = activeResult && "src" in activeResult ? activeResult.src : null;
  const imageError = activeResult && "error" in activeResult ? activeResult.error : null;

  return (
    <div className="border border-cs-border bg-cs-panel p-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <p className="font-mono text-xs tracking-[0.15em] text-cs-muted uppercase">Heatmap</p>
        <div className="flex flex-wrap gap-1">
          {TYPE_TABS.map(({ type, label }) => (
            <button
              key={type}
              onClick={() => setSelectedType(type)}
              className={`border px-2 py-1 font-mono text-[10px] tracking-[0.1em] uppercase transition-colors ${
                selectedType === type
                  ? "border-cs-teal text-cs-teal"
                  : "border-cs-border text-cs-muted hover:text-cs-text"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* Heatmap Rendering Rewrite: the legend/units/timestamp/type label
          now lives IN the image itself (burned in by heatmap_rendering.py)
          — this caption is deliberately just the ONE piece of context that
          image doesn't already carry: which snapshot (by playback time)
          is currently selected, not a second copy of the legend. */}
      {current && (
        <p className="mb-2 font-mono text-[10px] tracking-[0.1em] text-cs-muted">
          Showing snapshot at t={current.timestamp_seconds.toFixed(2)}s
        </p>
      )}

      {imageError ? (
        <div className="flex aspect-video items-center justify-center border border-cs-border bg-cs-bg text-sm text-cs-muted">
          {imageError}
        </div>
      ) : !current || !imageSrc ? (
        <div className="flex aspect-video items-center justify-center border border-cs-border bg-cs-bg font-mono text-xs tracking-[0.15em] text-cs-muted uppercase">
          Not yet available
        </div>
      ) : (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={imageSrc}
          alt={`${selectedType} heatmap at t=${current.timestamp_seconds.toFixed(2)}s`}
          className="w-full border border-cs-border bg-black object-contain"
          style={{ aspectRatio: naturalAspect ?? 16 / 9 }}
          onLoad={(event) => {
            const img = event.currentTarget;
            if (img.naturalWidth > 0 && img.naturalHeight > 0) {
              setNaturalAspect(img.naturalWidth / img.naturalHeight);
            }
          }}
        />
      )}
    </div>
  );
}
