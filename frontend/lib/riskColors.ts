// Final Intelligence phase: extracted from LiveMonitor.tsx's own former
// inline RISK_STATE_COLORS constant so every widget that needs a risk-state
// color (the live "Current Risk" card, the stat-card grid's new Risk card,
// the Analysis Report's Risk Overview panel, and severity badges) agrees on
// exactly one mapping, instead of redeclaring the same literal colors
// per-file. Reasonable color mapping using the existing palette (documented
// choice): NORMAL stays teal ("calm/safe," matching the design tokens' own
// stated meaning for that accent); ELEVATED/CRITICAL/INCIDENT escalate
// through amber tones of increasing intensity.
export const RISK_STATE_COLORS: Record<string, string> = {
  NORMAL: "#2DD4BF",
  ELEVATED: "#F2A93B",
  CRITICAL: "#FF6B35",
  INCIDENT: "#FF4B35",
};

// Final Intelligence phase: severity tags derived server-side by
// session_report_service.py's _severity_tag() (LOW/MODERATE/HIGH/CRITICAL)
// — a separate, UI-heuristic vocabulary from RiskState, sharing the same
// escalating amber/red visual language for consistency.
export const SEVERITY_COLORS: Record<string, string> = {
  LOW: "#2DD4BF",
  MODERATE: "#F2A93B",
  HIGH: "#FF6B35",
  CRITICAL: "#FF4B35",
};

// Event/incident STATUS vocabulary (OBSERVED/WATCH/ABSTAINED/INCIDENT) —
// deliberately distinct from RiskState/severity: this is about how far an
// investigated event progressed through the decision pipeline, not how
// dangerous it was.
export const STATUS_COLORS: Record<string, string> = {
  OBSERVED: "#94A3B8",
  WATCH: "#F2A93B",
  ABSTAINED: "#94A3B8",
  INCIDENT: "#FF6B35",
};
