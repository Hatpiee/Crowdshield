"use client";

import { useEffect, useState } from "react";

import {
  acknowledgeIncident,
  dismissIncident,
  escalateIncident,
  markIncidentFalsePositive,
  resolveIncident,
  type ActionResult,
} from "@/app/dashboard/actions";
import { LIVE_POLL_INTERVAL_MS } from "@/lib/livePolling";

const API_URL = process.env.NEXT_PUBLIC_API_URL;

// Diagram 9's real transition graph (Phase 19): only DETECTED/ACTIVE
// incidents are eligible for any lifecycle-transition action —
// RESOLVED/FALSE_POSITIVE are terminal. Mirrors the exact same "don't show
// invalid actions for the current state" pattern already established for
// session Start/Cancel buttons since Phase 4.
const ACTIONABLE_STATUSES = new Set(["DETECTED", "ACTIVE"]);

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
  created_at: string;
  updated_at: string;
}

async function authedFetch(token: string, path: string): Promise<Response> {
  return fetch(`${API_URL}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
}

function ActionButton({
  label,
  pending,
  onClick,
}: {
  label: string;
  pending: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      disabled={pending}
      className="border border-cs-border px-2 py-1 font-mono text-[10px] tracking-[0.1em] text-cs-text uppercase transition-colors hover:border-cs-teal hover:text-cs-teal disabled:opacity-50"
    >
      {pending ? "…" : label}
    </button>
  );
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
  const [error, setError] = useState<string | null>(null);
  const [pendingKey, setPendingKey] = useState<string | null>(null);

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

  async function runAction(
    incidentId: string,
    actionKey: string,
    action: (id: string) => Promise<ActionResult>,
    confirmMessage?: string
  ) {
    // A brief native confirm() for the more consequential (permanently
    // closing or priority-changing) actions — documented UX choice: simple
    // and sufficient for this phase, no custom modal component warranted
    // for a single yes/no gate.
    if (confirmMessage && !window.confirm(confirmMessage)) return;

    setError(null);
    setPendingKey(`${incidentId}:${actionKey}`);
    const result = await action(incidentId);
    setPendingKey(null);

    if (!result.success) {
      setError(result.message);
      return;
    }
    // A local re-fetch, not router.refresh(): this widget is a leaf inside
    // the live-monitor client tree — router.refresh() would re-render the
    // whole server-rendered dashboard page, remounting VideoPlayer and
    // losing playback position for no reason.
    fetchIncidents();
  }

  return (
    <div className="border border-cs-border bg-cs-panel p-5">
      <p className="mb-4 font-mono text-xs tracking-[0.15em] text-cs-muted uppercase">
        Incidents {incidents.length > 0 && `(${incidents.length})`}
      </p>
      {error && <p className="mb-3 text-xs text-cs-amber">{error}</p>}

      {incidents.length === 0 ? (
        <p className="text-sm text-cs-muted">No incidents for this session.</p>
      ) : (
        <ul className="flex flex-col gap-3">
          {incidents.map((incident) => {
            const isActionable = ACTIONABLE_STATUSES.has(incident.lifecycle_status);
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
                    <span
                      className={incident.priority === "ELEVATED" ? "text-cs-amber" : "text-cs-text"}
                    >
                      {incident.lifecycle_status}
                    </span>
                    {incident.closure_reason && (
                      <span className="text-cs-muted">({incident.closure_reason})</span>
                    )}
                    {incident.priority === "ELEVATED" && (
                      <span className="text-cs-amber">· ELEVATED</span>
                    )}
                    {incident.acknowledged && <span className="text-cs-teal">· ACKNOWLEDGED</span>}
                  </div>
                  <span className="text-[11px] normal-case text-cs-muted">
                    detected {new Date(incident.created_at).toLocaleString()}
                    {incident.updated_at !== incident.created_at &&
                      ` · updated ${new Date(incident.updated_at).toLocaleString()}`}
                  </span>
                </div>

                {incident.latest_recommendation && (
                  <p className="mt-2 text-sm text-cs-text">
                    Recommendation:{" "}
                    <span className="text-cs-muted">{incident.latest_recommendation}</span>
                  </p>
                )}

                {isActionable && (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {!incident.acknowledged && (
                      <ActionButton
                        label="Acknowledge"
                        pending={pendingKey === `${incident.id}:acknowledge`}
                        onClick={() =>
                          runAction(incident.id, "acknowledge", acknowledgeIncident)
                        }
                      />
                    )}
                    <ActionButton
                      label="Dismiss"
                      pending={pendingKey === `${incident.id}:dismiss`}
                      onClick={() => runAction(incident.id, "dismiss", dismissIncident)}
                    />
                    <ActionButton
                      label="Resolve"
                      pending={pendingKey === `${incident.id}:resolve`}
                      onClick={() =>
                        runAction(
                          incident.id,
                          "resolve",
                          resolveIncident,
                          "Resolve this incident? This closes it permanently."
                        )
                      }
                    />
                    <ActionButton
                      label="Mark False Positive"
                      pending={pendingKey === `${incident.id}:false-positive`}
                      onClick={() =>
                        runAction(
                          incident.id,
                          "false-positive",
                          markIncidentFalsePositive,
                          "Mark this incident as a false positive? This closes it permanently."
                        )
                      }
                    />
                    {isAdmin && (
                      <ActionButton
                        label="Escalate"
                        pending={pendingKey === `${incident.id}:escalate`}
                        onClick={() =>
                          runAction(
                            incident.id,
                            "escalate",
                            escalateIncident,
                            "Escalate this incident to ELEVATED priority?"
                          )
                        }
                      />
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
