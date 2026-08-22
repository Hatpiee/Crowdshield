"use client";

import { useEffect, useState } from "react";

import type { CrowdMetricsSnapshotItem } from "@/lib/types";
import { LIVE_POLL_INTERVAL_MS, TERMINAL_SESSION_STATUSES } from "@/lib/livePolling";
import { pickCurrentItem } from "@/lib/nearestSnapshot";
import { RISK_STATE_COLORS } from "@/lib/riskColors";

import AnalysisReport from "./AnalysisReport";
import CrowdMetricsStatCards from "./CrowdMetricsStatCards";
import HeatmapViewer from "./HeatmapViewer";
import IncidentsList from "./IncidentsList";
import RiskTrendChart from "./RiskTrendChart";
import SystemStatusBadge from "./SystemStatusBadge";
import VideoPlayer from "./VideoPlayer";

const API_URL = process.env.NEXT_PUBLIC_API_URL;

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
  isAdmin,
}: {
  sessionId: string;
  videoId: string;
  accessToken: string;
  initialStatus: SessionStatusPayload;
  isAdmin: boolean;
}) {
  const [status, setStatus] = useState<SessionStatusPayload>(initialStatus);
  const [timeseries, setTimeseries] = useState<CrowdMetricsSnapshotItem[]>([]);
  // The player's current playback timestamp, lifted here so every widget
  // that needs "the value as of now" can consume it via the shared
  // pickCurrentItem helper (Phase 22, Resolution 2) — current risk badge,
  // the 4 new stat cards, and the heatmap viewer all read this SAME state,
  // still a plain lifted useState (not React Context): still no more than
  // this one component's own subtree of consumers, so Context remains
  // premature.
  const [playbackTime, setPlaybackTime] = useState<number | null>(null);
  // Final Intelligence phase (Phase G): "click a timeline/event/incident
  // entry -> seek the video" — see VideoPlayer.tsx's own seekRequest prop
  // docstring for why a nonce is needed alongside the target timestamp.
  const [seekRequest, setSeekRequest] = useState<{ seconds: number; nonce: number } | null>(null);
  const seekTo = (seconds: number) =>
    setSeekRequest((prev) => ({ seconds, nonce: (prev?.nonce ?? 0) + 1 }));

  const isTerminal = TERMINAL_SESSION_STATUSES.has(status.status);

  useEffect(() => {
    if (isTerminal) return;
    let cancelled = false;
    const interval = setInterval(async () => {
      const res = await authedFetch(accessToken, `/api/v1/sessions/${sessionId}/status`);
      if (cancelled || !res.ok) return;
      const body = await res.json();
      if (body.success) setStatus(body.data);
    }, LIVE_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [isTerminal, sessionId, accessToken]);

  // Fetched once on mount, then re-fetched on the SAME live cadence as
  // every other widget while non-terminal (Phase 22 retrofit — Phase 21
  // only fetched this once-on-mount/once-on-terminal-flip, sufficient for
  // a chart that didn't need per-tick freshness; this phase's new stat
  // cards and heatmap "current value" derivation DO need it, since there
  // is no lighter-weight endpoint for those fields and Resolution 2
  // forbids adding one — see DECISIONS.md).
  useEffect(() => {
    let cancelled = false;

    async function fetchTimeseries() {
      const res = await authedFetch(
        accessToken,
        `/api/v1/sessions/${sessionId}/crowd-metrics-timeseries`
      );
      if (cancelled || !res.ok) return;
      const body = await res.json();
      if (body.success) setTimeseries(body.data.items);
    }

    fetchTimeseries();
    if (isTerminal) return () => {
      cancelled = true;
    };
    const interval = setInterval(fetchTimeseries, LIVE_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [sessionId, accessToken, isTerminal]);

  // Decision #3, generalized (Resolution 2): ONE derivation, shared by the
  // risk badge below and CrowdMetricsStatCards — "most recent" while live,
  // "nearest to playback position" once terminal.
  const currentMetrics = pickCurrentItem(timeseries, isTerminal, playbackTime);
  const currentRiskScore = currentMetrics?.risk_score ?? null;
  const currentRiskState = currentMetrics?.risk_state ?? null;
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
          <VideoPlayer
            videoId={videoId}
            accessToken={accessToken}
            onTimeUpdate={setPlaybackTime}
            seekRequest={seekRequest}
          />
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

      <CrowdMetricsStatCards current={currentMetrics} />

      <AnalysisReport sessionId={sessionId} accessToken={accessToken} isTerminal={isTerminal} onSeek={seekTo} />

      <HeatmapViewer
        sessionId={sessionId}
        accessToken={accessToken}
        isTerminal={isTerminal}
        playbackTime={playbackTime}
      />

      <div className="border border-cs-border bg-cs-panel p-5">
        <p className="mb-4 font-mono text-xs tracking-[0.15em] text-cs-muted uppercase">
          Risk Trend
        </p>
        <RiskTrendChart data={timeseries} />
      </div>

      <IncidentsList
        sessionId={sessionId}
        accessToken={accessToken}
        isTerminal={isTerminal}
        isAdmin={isAdmin}
      />
    </div>
  );
}
