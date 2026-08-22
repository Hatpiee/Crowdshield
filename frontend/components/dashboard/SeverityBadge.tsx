import { SEVERITY_COLORS, STATUS_COLORS } from "@/lib/riskColors";

// Final Intelligence phase: consolidates the ad-hoc per-file badge
// rendering the frontend audit flagged (RISK_STATE_COLORS/OUTCOME_COLORS/
// STATUS_COLORS each redeclared and rendered inline in different files) —
// one small, reusable badge for the two vocabularies this phase adds
// (event STATUS: OBSERVED/WATCH/ABSTAINED/INCIDENT; derived SEVERITY:
// LOW/MODERATE/HIGH/CRITICAL). Does not touch the pre-existing incident
// lifecycle-status rendering elsewhere — additive, not a forced migration.
export function StatusBadge({ status }: { status: string }) {
  const color = STATUS_COLORS[status] ?? "#94A3B8";
  return (
    <span
      className="border px-1.5 py-0.5 font-mono text-[10px] tracking-[0.1em] uppercase"
      style={{ color, borderColor: color }}
    >
      {status}
    </span>
  );
}

export function SeverityBadge({ severity }: { severity: string }) {
  const color = SEVERITY_COLORS[severity] ?? "#94A3B8";
  return (
    <span
      className="border px-1.5 py-0.5 font-mono text-[10px] tracking-[0.1em] uppercase"
      style={{ color, borderColor: color }}
    >
      {severity}
    </span>
  );
}
