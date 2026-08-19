"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import IncidentActionButtons from "@/components/incidents/IncidentActionButtons";
import { formatDateTime } from "@/lib/formatDate";
import { LIVE_POLL_INTERVAL_MS } from "@/lib/livePolling";

const API_URL = process.env.NEXT_PUBLIC_API_URL;

interface IncidentItem {
  id: string;
  session_id: string;
  lifecycle_status: string;
  closure_reason: string | null;
  priority: string;
  acknowledged: boolean;
  acknowledged_at: string | null;
  acknowledged_by: string | null;
  latest_recommendation: string | null;
  // Acute-Hazard Trigger Phase: lets the operator see WHY an incident
  // exists (e.g. EXPLOSIVE_EVENT vs CROWD_CRUSH) without opening the full
  // drill-down page.
  latest_event_classification: string | null;
  created_at: string;
  updated_at: string;
}

async function authedFetch(token: string, path: string): Promise<Response> {
  return fetch(`${API_URL}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export default function IncidentsList({
  sessionId,
  accessToken,
  isTerminal,
  isAdmin,
}: {
  sessionId: string;
  accessToken: string;
  isTerminal: boolean;
  // Resolution's frozen decision: this ONLY controls whether the Escalate
  // button is rendered — a UX courtesy, never the real security boundary
  // (see actions.ts's escalateIncident for the same note on the other
  // side of the Server Action boundary).
  isAdmin: boolean;
}) {
  const [incidents, setIncidents] = useState<IncidentItem[]>([]);

  async function fetchIncidents() {
    const res = await authedFetch(accessToken, `/api/v1/sessions/${sessionId}/incidents`);
    if (!res.ok) return;
    const body = await res.json();
    if (body.success) setIncidents(body.data.items);
  }

  // Same live-polling cadence/lifecycle as every other widget (§24 frozen
  // decision) — reused via the shared LIVE_POLL_INTERVAL_MS constant.
  useEffect(() => {
    let cancelled = false;
    async function tick() {
      const res = await authedFetch(accessToken, `/api/v1/sessions/${sessionId}/incidents`);
      if (cancelled || !res.ok) return;
      const body = await res.json();
      if (body.success) setIncidents(body.data.items);
    }
    tick();
    if (isTerminal) return () => {
      cancelled = true;
    };
    const interval = setInterval(tick, LIVE_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [sessionId, accessToken, isTerminal]);

  return (
    <div className="border border-cs-border bg-cs-panel p-5">
      <p className="mb-4 font-mono text-xs tracking-[0.15em] text-cs-muted uppercase">
        Incidents {incidents.length > 0 && `(${incidents.length})`}
      </p>

      {incidents.length === 0 ? (
        <p className="text-sm text-cs-muted">No incidents for this session.</p>
      ) : (
        <ul className="flex flex-col gap-3">
          {incidents.map((incident) => {
            const isActionable = incident.lifecycle_status === "DETECTED" || incident.lifecycle_status === "ACTIVE";
            return (
              <li
                key={incident.id}
                // Resolved/false-positive incidents stay VISIBLE (never
                // filtered out of the list) but visually de-emphasized —
                // preserving auditability per this project's established
                // "never applied silently" philosophy (§20/§21), extended
                // here to "never hidden from the record."
                className={`border p-3 ${
                  isActionable ? "border-cs-border" : "border-cs-border/50 opacity-60"
                }`}
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2 font-mono text-xs tracking-[0.1em] uppercase">
                    {/* Step 6: the incidents-list widget's natural entry
                        point into the full drill-down page — a real
                        navigation, not a modal/expansion. The quick-action
                        buttons below remain HERE too, for fast triage
                        without needing to open the full page every time. */}
                    <Link
                      href={`/incidents/${incident.id}`}
                      className="text-cs-teal underline-offset-2 hover:underline"
                    >
                      {incident.lifecycle_status}
                    </Link>
                    {incident.closure_reason && (
                      <span className="text-cs-muted">({incident.closure_reason})</span>
                    )}
                    {incident.priority === "ELEVATED" && (
                      <span className="text-cs-amber">· ELEVATED</span>
                    )}
                    {incident.acknowledged && <span className="text-cs-teal">· ACKNOWLEDGED</span>}
                    {incident.latest_event_classification && (
                      <span className="border border-cs-amber px-1.5 py-0.5 text-[10px] text-cs-amber">
                        {incident.latest_event_classification.replace(/_/g, " ")}
                      </span>
                    )}
                  </div>
                  <span className="text-[11px] normal-case text-cs-muted">
                    detected {formatDateTime(incident.created_at)}
                    {incident.updated_at !== incident.created_at &&
                      ` · updated ${formatDateTime(incident.updated_at)}`}
                  </span>
                </div>

                {incident.latest_recommendation && (
                  <p className="mt-2 text-sm text-cs-text">
                    Recommendation:{" "}
                    <span className="text-cs-muted">{incident.latest_recommendation}</span>
                  </p>
                )}

                <div className="mt-3">
                  <IncidentActionButtons
                    incidentId={incident.id}
                    lifecycleStatus={incident.lifecycle_status}
                    acknowledged={incident.acknowledged}
                    isAdmin={isAdmin}
                    onActionComplete={fetchIncidents}
                  />
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
