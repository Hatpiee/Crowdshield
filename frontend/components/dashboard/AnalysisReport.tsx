"use client";

import { useEffect, useState } from "react";

import { LIVE_POLL_INTERVAL_MS } from "@/lib/livePolling";
import { RISK_STATE_COLORS } from "@/lib/riskColors";

import OperatorCopilot from "./OperatorCopilot";
import Panel from "./Panel";
import { SeverityBadge, StatusBadge } from "./SeverityBadge";

const API_URL = process.env.NEXT_PUBLIC_API_URL;

interface RiskOverview {
  current_state: string | null;
  current_score: number | null;
  trend: string;
  trend_delta: number | null;
  trend_window_seconds: number | null;
  primary_contributors: string[];
  latest_snapshot_timestamp: number | null;
  snapshot_count: number;
}

interface EventSummary {
  evidence_package_id: string;
  timestamp_seconds: number;
  trigger_type: string;
  trigger_reason: string;
  status: string;
  event_classification: string | null;
  duration_seconds: number | null;
  severity_tag: string;
  confidence: number;
  location_description: string;
  description: string;
  observation_categories: string[];
  recommendation: string | null;
  incident_id: string | null;
}

interface TimelineEntry {
  timestamp_seconds: number;
  kind: string;
  description: string;
  evidence_package_id: string | null;
}

interface SessionReport {
  session_id: string;
  session_status: string;
  investigated_event_count: number;
  confirmed_incident_count: number;
  risk_overview: RiskOverview;
  events: EventSummary[];
  timeline: TimelineEntry[];
  overview_summary: string;
  incidents_summary: string;
  behavioral_analysis: string;
  spatial_analysis: string;
  top_recommendation: string | null;
}

async function authedFetch(token: string, path: string): Promise<Response> {
  return fetch(`${API_URL}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
}

const TREND_ARROW: Record<string, string> = {
  RISING: "↑",
  FALLING: "↓",
  STABLE: "→",
  UNKNOWN: "",
};

export default function AnalysisReport({
  sessionId,
  accessToken,
  isTerminal,
  onSeek,
}: {
  sessionId: string;
  accessToken: string;
  isTerminal: boolean;
  onSeek: (seconds: number) => void;
}) {
  const [report, setReport] = useState<SessionReport | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function fetchReport() {
      const res = await authedFetch(accessToken, `/api/v1/sessions/${sessionId}/report`);
      if (cancelled || !res.ok) return;
      const body = await res.json();
      if (body.success) setReport(body.data);
    }
    fetchReport();
    if (isTerminal) return () => {
      cancelled = true;
    };
    const interval = setInterval(fetchReport, LIVE_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [sessionId, accessToken, isTerminal]);

  if (!report) {
    return (
      <Panel label="Analysis Report">
        <p className="text-sm text-cs-muted">Loading…</p>
      </Panel>
    );
  }

  const risk = report.risk_overview;
  const riskColor = risk.current_state ? RISK_STATE_COLORS[risk.current_state] : undefined;

  return (
    <div className="flex flex-col gap-4">
      <Panel label="Overview">
        <p className="text-sm text-cs-text">{report.overview_summary}</p>

        <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div>
            <p className="font-mono text-[10px] tracking-[0.15em] text-cs-muted uppercase">
              Events Investigated
            </p>
            <p className="mt-1 text-xl font-bold text-cs-text">{report.investigated_event_count}</p>
          </div>
          <div>
            <p className="font-mono text-[10px] tracking-[0.15em] text-cs-muted uppercase">
              Confirmed Incidents
            </p>
            <p className="mt-1 text-xl font-bold text-cs-text">{report.confirmed_incident_count}</p>
          </div>
          {risk.current_state && (
            <div>
              <p className="font-mono text-[10px] tracking-[0.15em] text-cs-muted uppercase">
                Risk Trend
              </p>
              <p className="mt-1 text-xl font-bold" style={{ color: riskColor }}>
                {TREND_ARROW[risk.trend]} {risk.trend}
              </p>
            </div>
          )}
          {risk.primary_contributors.length > 0 && (
            <div>
              <p className="font-mono text-[10px] tracking-[0.15em] text-cs-muted uppercase">
                Primary Contributors
              </p>
              <p className="mt-1 text-sm text-cs-text">{risk.primary_contributors.join(", ")}</p>
            </div>
          )}
        </div>

        {report.top_recommendation && (
          <p className="mt-4 text-sm text-cs-text">
            Recommendation:{" "}
            <span className="text-cs-amber">{report.top_recommendation.replace(/_/g, " ")}</span>
          </p>
        )}
      </Panel>

      <Panel label={`Activity Timeline (${report.timeline.length})`}>
        {report.timeline.length === 0 ? (
          <p className="text-sm text-cs-muted">No timeline entries yet.</p>
        ) : (
          <ol className="flex max-h-72 flex-col gap-1 overflow-y-auto">
            {report.timeline.map((entry, index) => (
              <li key={index}>
                <button
                  type="button"
                  onClick={() => onSeek(entry.timestamp_seconds)}
                  className="flex w-full gap-3 border-l-2 border-cs-border px-3 py-1.5 text-left text-sm hover:border-cs-teal hover:bg-cs-bg/40"
                >
                  <span className="shrink-0 font-mono text-xs text-cs-muted">
                    {entry.timestamp_seconds.toFixed(1)}s
                  </span>
                  <span className="text-cs-text">{entry.description}</span>
                </button>
              </li>
            ))}
          </ol>
        )}
      </Panel>

      <Panel label={`Detected Events (${report.events.length})`}>
        {report.events.length === 0 ? (
          <p className="text-sm text-cs-muted">No significant events were identified in this analysis.</p>
        ) : (
          <ul className="flex flex-col gap-3">
            {report.events.map((event) => (
              <li key={event.evidence_package_id} className="border border-cs-border p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      onClick={() => onSeek(event.timestamp_seconds)}
                      className="font-mono text-xs text-cs-teal underline-offset-2 hover:underline"
                    >
                      t={event.timestamp_seconds.toFixed(2)}s
                    </button>
                    <StatusBadge status={event.status} />
                    <SeverityBadge severity={event.severity_tag} />
                    {event.event_classification && (
                      <span className="border border-cs-amber px-1.5 py-0.5 text-[10px] text-cs-amber">
                        {event.event_classification.replace(/_/g, " ")}
                      </span>
                    )}
                  </div>
                  {event.incident_id && (
                    <a
                      href={`/incidents/${event.incident_id}`}
                      className="text-xs text-cs-teal underline-offset-2 hover:underline"
                    >
                      View incident →
                    </a>
                  )}
                </div>
                <p className="mt-2 text-sm text-cs-text">{event.description}</p>
                <p className="mt-1 text-xs text-cs-muted">
                  {event.trigger_type} trigger · {event.location_description} · confidence{" "}
                  {(event.confidence * 100).toFixed(0)}%
                  {event.recommendation ? ` · recommends ${event.recommendation.replace(/_/g, " ")}` : ""}
                </p>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Panel label="Behavioral Analysis">
          <p className="text-sm text-cs-text">{report.behavioral_analysis}</p>
        </Panel>
        <Panel label="Spatial Analysis">
          <p className="text-sm text-cs-text">{report.spatial_analysis}</p>
        </Panel>
      </div>

      <Panel label="Confirmed Incidents">
        <p className="text-sm text-cs-text">{report.incidents_summary}</p>
      </Panel>

      <OperatorCopilot sessionId={sessionId} accessToken={accessToken} onSeek={onSeek} />
    </div>
  );
}
