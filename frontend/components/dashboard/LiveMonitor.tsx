"use client";

import { useEffect, useState } from "react";

import RiskTrendChart from "./RiskTrendChart";
import SystemStatusBadge from "./SystemStatusBadge";
import VideoPlayer from "./VideoPlayer";

const API_URL = process.env.NEXT_PUBLIC_API_URL;
// Decision #4: poll every 2-3s ONLY while non-terminal; stop entirely once
// a terminal status is reached — no wasted requests against a finished
// session.
const STATUS_POLL_INTERVAL_MS = 2500;
const TERMINAL_STATUSES = new Set(["COMPLETED", "FAILED", "CANCELLED"]);

// Reasonable color mapping using the existing palette (documented choice):
// NORMAL stays teal ("calm/safe," matching the design tokens' own stated
// meaning for that accent); ELEVATED/CRITICAL/INCIDENT escalate through
// amber tones of increasing intensity, ending at the brand's own amber
// accent for CRITICAL and a slightly redder amber for INCIDENT (this
// dashboard shell never actually reaches INCIDENT — Phase 13's own
// RiskState.INCIDENT is structurally unreachable — included only for
// completeness/forward-compatibility, not because this phase exercises it).
const RISK_STATE_COLORS: Record<string, string> = {
  NORMAL: "#2DD4BF",
  ELEVATED: "#F2A93B",
  CRITICAL: "#FF6B35",
  INCIDENT: "#FF4B35",
};

interface ProcessingRunSummary {
  status: string;
  frames_processed: number | null;
  total_frames: number | null;
}

interface SessionStatusPayload {
  id: string;
  status: string;
  latest_processing_run: ProcessingRunSummary | null;
  latest_risk_score: number | null;
  latest_risk_state: string | null;
}

interface Snapshot {
  frame_number: number;
  timestamp_seconds: number;
  risk_score: number;
  risk_state: string;
}

async function authedFetch(token: string, path: string): Promise<Response> {
  return fetch(`${API_URL}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export default function LiveMonitor({
  sessionId,
  videoId,
  accessToken,
  initialStatus,
}: {
  sessionId: string;
  videoId: string;
  accessToken: string;
  initialStatus: SessionStatusPayload;
}) {
  const [status, setStatus] = useState<SessionStatusPayload>(initialStatus);
  const [timeseries, setTimeseries] = useState<Snapshot[]>([]);
  // The player's current playback timestamp, lifted here so the "current
  // risk" widget can consume it — decision #3's explicitly INCREMENTAL
  // scope for this phase: only THIS widget is wired to playback position.
  // A plain useState (not React Context) is enough for a single consumer;
  // promoting this to Context is straightforward once Phase 22's spatial
  // widgets need the same value.
  const [playbackTime, setPlaybackTime] = useState<number | null>(null);

  const isTerminal = TERMINAL_STATUSES.has(status.status);

  useEffect(() => {
    if (isTerminal) return;
    let cancelled = false;
    const interval = setInterval(async () => {
      const res = await authedFetch(accessToken, `/api/v1/sessions/${sessionId}/status`);
      if (cancelled || !res.ok) return;
      const body = await res.json();
      if (body.success) setStatus(body.data);
    }, STATUS_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [isTerminal, sessionId, accessToken]);

  // Fetched once on mount, and again whenever processing reaches a
  // terminal state, so the chart (and the nearest-timestamp lookup below)
  // reflect the FULL final dataset rather than only what existed at mount
  // time. Not re-fetched on every status poll — the chart doesn't need
  // per-2.5s-tick freshness the way the live risk badge does.
  useEffect(() => {
    let cancelled = false;
    authedFetch(accessToken, `/api/v1/sessions/${sessionId}/crowd-metrics-timeseries`).then(
      async (res) => {
        if (cancelled || !res.ok) return;
        const body = await res.json();
        if (body.success) setTimeseries(body.data.items);
      }
    );
    return () => {
      cancelled = true;
    };
  }, [sessionId, accessToken, isTerminal]);

  // Decision #3: "current risk" reads the MOST RECENT snapshot for a
  // still-PROCESSING session (the live-polled status payload already
  // carries this), or the snapshot nearest the video's CURRENT PLAYBACK
  // TIMESTAMP for a COMPLETED session — computed client-side from the
  // already-fetched timeseries array, so scrubbing doesn't trigger a
  // network request per timeupdate tick.
  let currentRiskScore = status.latest_risk_score;
  let currentRiskState = status.latest_risk_state;
  if (isTerminal && playbackTime !== null && timeseries.length > 0) {
    let nearest = timeseries[0];
    let bestDelta = Math.abs(nearest.timestamp_seconds - playbackTime);
    for (const snapshot of timeseries) {
      const delta = Math.abs(snapshot.timestamp_seconds - playbackTime);
      if (delta < bestDelta) {
        nearest = snapshot;
        bestDelta = delta;
      }
    }
    currentRiskScore = nearest.risk_score;
    currentRiskState = nearest.risk_state;
  }
  const riskColor = currentRiskState ? RISK_STATE_COLORS[currentRiskState] : undefined;

  const run = status.latest_processing_run;
  const progressFraction =
    run?.frames_processed != null && run?.total_frames
      ? Math.min(1, run.frames_processed / run.total_frames)
      : null;

  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <VideoPlayer videoId={videoId} accessToken={accessToken} onTimeUpdate={setPlaybackTime} />
        </div>

        <div className="flex flex-col gap-4">
          <div className="border border-cs-border bg-cs-panel p-5">
            <p className="font-mono text-xs tracking-[0.15em] text-cs-muted uppercase">
              Current Risk
            </p>
            {currentRiskScore != null && currentRiskState ? (
              <>
                <p className="mt-2 text-4xl font-bold" style={{ color: riskColor }}>
                  {currentRiskScore.toFixed(1)}
                </p>
                <p
                  className="mt-1 font-mono text-xs tracking-[0.15em] uppercase"
                  style={{ color: riskColor }}
                >
                  {currentRiskState}
                </p>
              </>
            ) : (
              <p className="mt-2 text-sm text-cs-muted">No data yet</p>
            )}
          </div>

          <div className="border border-cs-border bg-cs-panel p-5">
            <p className="font-mono text-xs tracking-[0.15em] text-cs-muted uppercase">
              Processing
            </p>
            <p className="mt-2 text-sm text-cs-text">{status.status}</p>
            {progressFraction !== null && (
              <div className="mt-3">
                <div className="h-1.5 w-full overflow-hidden bg-cs-bg">
                  <div
                    className="h-full bg-cs-teal transition-[width]"
                    style={{ width: `${progressFraction * 100}%` }}
                  />
                </div>
                <p className="mt-1 font-mono text-xs text-cs-muted">
                  {run?.frames_processed} / {run?.total_frames} frames
                </p>
              </div>
            )}
          </div>

          <SystemStatusBadge accessToken={accessToken} />
        </div>
      </div>

      <div className="border border-cs-border bg-cs-panel p-5">
        <p className="mb-4 font-mono text-xs tracking-[0.15em] text-cs-muted uppercase">
          Risk Trend
        </p>
        <RiskTrendChart data={timeseries} />
      </div>
    </div>
  );
}
