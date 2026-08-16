"use client";

import { useState } from "react";

import {
  acknowledgeIncident,
  dismissIncident,
  escalateIncident,
  markIncidentFalsePositive,
  resolveIncident,
  type ActionResult,
} from "@/app/dashboard/actions";

// Diagram 9's real transition graph (Phase 19): only DETECTED/ACTIVE
// incidents are eligible for any lifecycle-transition action —
// RESOLVED/FALSE_POSITIVE are terminal. Mirrors the exact same "don't show
// invalid actions for the current state" pattern already established for
// session Start/Cancel buttons since Phase 4.
const ACTIONABLE_STATUSES = new Set(["DETECTED", "ACTIVE"]);

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

// Phase 23, Step 5: extracted out of Phase 22's IncidentsList.tsx so the
// incident detail page can reuse the EXACT SAME real operator-action Server
// Actions and the SAME "don't show invalid actions for current state" /
// ADMIN-only-Escalate-visibility rules, rather than reimplementing them a
// second time for this page. IncidentsList.tsx now renders this same
// component instead of its own inline copy.
export default function IncidentActionButtons({
  incidentId,
  lifecycleStatus,
  acknowledged,
  isAdmin,
  onActionComplete,
}: {
  incidentId: string;
  lifecycleStatus: string;
  acknowledged: boolean;
  // Resolution's frozen decision: this ONLY controls whether the Escalate
  // button is rendered — a UX courtesy, never the real security boundary
  // (see actions.ts's escalateIncident for the same note on the other side
  // of the Server Action boundary — require_role(Role.ADMIN) remains
  // entirely server-side).
  isAdmin: boolean;
  onActionComplete: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [pendingKey, setPendingKey] = useState<string | null>(null);

  const isActionable = ACTIONABLE_STATUSES.has(lifecycleStatus);

  async function runAction(
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
    setPendingKey(actionKey);
    const result = await action(incidentId);
    setPendingKey(null);

    if (!result.success) {
      setError(result.message);
      return;
    }
    onActionComplete();
  }

  if (!isActionable) return null;

  return (
    <div className="flex flex-wrap items-center gap-2">
      {error && <p className="w-full text-xs text-cs-amber">{error}</p>}
      {!acknowledged && (
        <ActionButton
          label="Acknowledge"
          pending={pendingKey === "acknowledge"}
          onClick={() => runAction("acknowledge", acknowledgeIncident)}
        />
      )}
      <ActionButton
        label="Dismiss"
        pending={pendingKey === "dismiss"}
        onClick={() => runAction("dismiss", dismissIncident)}
      />
      <ActionButton
        label="Resolve"
        pending={pendingKey === "resolve"}
        onClick={() =>
          runAction("resolve", resolveIncident, "Resolve this incident? This closes it permanently.")
        }
      />
      <ActionButton
        label="Mark False Positive"
        pending={pendingKey === "false-positive"}
        onClick={() =>
          runAction(
            "false-positive",
            markIncidentFalsePositive,
            "Mark this incident as a false positive? This closes it permanently."
          )
        }
      />
      {isAdmin && (
        <ActionButton
          label="Escalate"
          pending={pendingKey === "escalate"}
          onClick={() =>
            runAction("escalate", escalateIncident, "Escalate this incident to ELEVATED priority?")
          }
        />
      )}
    </div>
  );
}
