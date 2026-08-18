import Link from "next/link";

import { auth } from "@/auth";
import AppHeader from "@/components/AppHeader";
import IncidentTimeline, { type IncidentDetailData } from "@/components/incidents/IncidentTimeline";
import { authFetch } from "@/lib/api";

// Phase 23, Step 5: a genuine new dynamic route (/incidents/[id]), NOT
// Phase 21's ?session= query-param pattern — this is a forensic/audit
// inspection view (§24's "WHY did the system generate this incident?"),
// not live-monitoring state tied to one currently-open session. It
// deserves a real, bookmarkable, linkable URL an operator can share or
// return to directly, unlike the dashboard's own live session view, which
// is inherently tied to "whatever I'm watching right now."

const STATUS_COLORS: Record<string, string> = {
  DETECTED: "#F2A93B",
  ACTIVE: "#FF6B35",
  RESOLVED: "#2DD4BF",
  FALSE_POSITIVE: "#94A3B8",
};

export default async function IncidentDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const session = await auth();

  const res = await authFetch(`/api/v1/incidents/${id}`);
  const body = await res.json();

  if (!res.ok || !body.success) {
    return (
      <div className="flex min-h-full flex-1 flex-col bg-cs-bg text-cs-text">
        <AppHeader />
        <div className="mx-auto max-w-3xl p-8">
          <p>Incident not found.</p>
        </div>
      </div>
    );
  }

  const incident: IncidentDetailData = body.data;
  const statusColor = STATUS_COLORS[incident.lifecycle_status] ?? "#94A3B8";

  return (
    <div className="flex min-h-full flex-1 flex-col bg-cs-bg text-cs-text">
      <AppHeader />
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-8">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="font-mono text-xs tracking-[0.15em] text-cs-muted uppercase">
              Incident {incident.id.slice(0, 8)}
            </p>
            <div className="mt-1 flex flex-wrap items-center gap-2">
              <span className="font-mono text-lg uppercase" style={{ color: statusColor }}>
                {incident.lifecycle_status}
              </span>
              {incident.closure_reason && (
                <span className="text-sm text-cs-muted">({incident.closure_reason})</span>
              )}
              {incident.priority === "ELEVATED" && (
                <span className="font-mono text-sm text-cs-amber">· ELEVATED</span>
              )}
              {incident.acknowledged && (
                <span className="font-mono text-sm text-cs-teal">· ACKNOWLEDGED</span>
              )}
            </div>
          </div>
          <Link
            href={`/dashboard?session=${incident.session_id}`}
            className="border border-cs-border px-3 py-1.5 font-mono text-xs tracking-[0.1em] text-cs-muted uppercase transition-colors hover:text-cs-teal"
          >
            ← Back to session
          </Link>
        </div>

        <IncidentTimeline
          incident={incident}
          accessToken={session?.access_token ?? ""}
          isAdmin={session?.role === "ADMIN"}
        />
      </div>
    </div>
  );
}
