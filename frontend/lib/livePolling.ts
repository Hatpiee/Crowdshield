// Phase 21 established this exact polling cadence + lifecycle
// (LiveMonitor.tsx's own status-poll effect: poll while PROCESSING/QUEUED,
// stop entirely at any terminal status). Phase 22's frozen decision (§24)
// requires every new live-updating widget (heatmap viewer, incidents list)
// to reuse this SAME cadence/lifecycle rather than inventing a second,
// differently-timed polling mechanism — importing these shared constants,
// rather than redeclaring the literal values per-widget, is what actually
// enforces "the same," not just visually matching numbers.
export const LIVE_POLL_INTERVAL_MS = 2500;
export const TERMINAL_SESSION_STATUSES = new Set(["COMPLETED", "FAILED", "CANCELLED"]);
