"use server";

import { authFetch } from "@/lib/api";

export type ActionResult = { success: true } | { success: false; message: string };

async function parseActionResponse(res: Response): Promise<ActionResult> {
  const body = await res.json();
  if (!res.ok || !body.success) {
    return {
      success: false,
      message: body?.error?.message ?? `Request failed (status ${res.status})`,
    };
  }
  return { success: true };
}

async function postIncidentAction(incidentId: string, actionPath: string): Promise<ActionResult> {
  const res = await authFetch(`/api/v1/incidents/${incidentId}/${actionPath}`, {
    method: "POST",
  });
  return parseActionResponse(res);
}

// §20/§21's real, established Server Action pattern (client component ->
// "use server" function -> authFetch server-side), same shape as
// app/sessions/actions.ts's startSession/cancelSession since Phase 4 —
// reused here rather than a new pattern for incident actions.

export async function acknowledgeIncident(incidentId: string): Promise<ActionResult> {
  return postIncidentAction(incidentId, "acknowledge");
}

export async function dismissIncident(incidentId: string): Promise<ActionResult> {
  return postIncidentAction(incidentId, "dismiss");
}

export async function resolveIncident(incidentId: string): Promise<ActionResult> {
  return postIncidentAction(incidentId, "resolve");
}

export async function markIncidentFalsePositive(incidentId: string): Promise<ActionResult> {
  return postIncidentAction(incidentId, "false-positive");
}

export async function escalateIncident(incidentId: string): Promise<ActionResult> {
  // Frozen decision (§20): the REAL ADMIN-only enforcement is entirely
  // server-side (require_role(Role.ADMIN) on POST /incidents/{id}/escalate,
  // Phase 19). This Server Action does no role check of its own and simply
  // forwards the call — the frontend hiding/disabling the Escalate button
  // for non-ADMIN users (IncidentsList.tsx) is a UX courtesy ONLY. A
  // non-ADMIN request that somehow still reaches this action gets a real
  // 403 from the backend, not a client-side illusion of security.
  return postIncidentAction(incidentId, "escalate");
}
