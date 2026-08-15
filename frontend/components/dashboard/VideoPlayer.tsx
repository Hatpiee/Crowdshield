"use client";

import { useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL;

export default function VideoPlayer({
  videoId,
  accessToken,
  onTimeUpdate,
}: {
  videoId: string;
  accessToken: string;
  onTimeUpdate: (seconds: number) => void;
}) {
  const [src, setSrc] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Gap 2: on mount, obtain a short-lived stream token via a normal
  // authenticated call, then set the <video> src to the streaming route
  // with that token appended as a query param — the ONLY way a plain
  // browser <video> element (which cannot attach a Bearer header) can
  // reach an authenticated media route.
  useEffect(() => {
    let cancelled = false;
    fetch(`${API_URL}/api/v1/videos/${videoId}/stream-token`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    })
      .then((res) => res.json())
      .then((body) => {
        if (cancelled) return;
        if (!body.success) {
          setError("Could not load video.");
          return;
        }
        setSrc(`${API_URL}/api/v1/videos/${videoId}/stream?token=${body.data.stream_token}`);
      })
      .catch(() => {
        if (!cancelled) setError("Could not load video.");
      });
    return () => {
      cancelled = true;
    };
  }, [videoId, accessToken]);

  if (error) {
    return (
      <div className="flex aspect-video items-center justify-center border border-cs-border bg-cs-panel text-sm text-cs-muted">
        {error}
      </div>
    );
  }

  if (!src) {
    return (
      <div className="flex aspect-video items-center justify-center border border-cs-border bg-cs-panel font-mono text-xs tracking-[0.15em] text-cs-muted uppercase">
        Loading video…
      </div>
    );
  }

  return (
    <video
      src={src}
      controls
      className="aspect-video w-full border border-cs-border bg-black"
      onTimeUpdate={(event) => onTimeUpdate(event.currentTarget.currentTime)}
    />
  );
}
