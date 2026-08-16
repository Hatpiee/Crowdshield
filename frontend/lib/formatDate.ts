// Phase 23 bug fix: `new Date(iso).toLocaleString()` with no explicit
// locale/options uses the RUNNING PROCESS's default locale — the Node
// server (SSR) and the browser (hydration) can genuinely disagree on that
// default, producing a real React hydration mismatch on any Client
// Component that receives a datetime string as a prop from a Server
// Component (exactly IncidentTimeline.tsx's shape: page.tsx fetches
// `incident` server-side and passes it down). A fixed, explicit
// locale + options makes server and client always produce byte-identical
// output regardless of either environment's own default locale.
export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString("en-US", {
    dateStyle: "short",
    timeStyle: "medium",
  });
}
