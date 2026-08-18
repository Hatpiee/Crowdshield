import Link from "next/link";
import { Plus } from "lucide-react";

import SessionActions from "@/app/sessions/SessionActions";

interface SessionListItem {
  id: string;
  video_original_filename: string;
  status: string;
  created_at: string;
}

// Resolution 3: color-coded status badges using the established
// teal/amber accent language. FAILED reuses cs-amber too (this palette has
// no separate red) but as a solid-filled chip rather than plain text — a
// deliberately LOUDER treatment than PROCESSING/QUEUED's text+pulse, so
// the two amber states stay visually distinguishable at a glance.
function StatusBadge({ status }: { status: string }) {
  if (status === "COMPLETED") {
    return (
      <span className="font-mono text-xs tracking-[0.15em] text-cs-teal uppercase">
        {status}
      </span>
    );
  }
  if (status === "PROCESSING" || status === "QUEUED") {
    return (
      <span className="flex items-center gap-2 font-mono text-xs tracking-[0.15em] text-cs-amber uppercase">
        <span className="relative flex h-2 w-2">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-cs-amber opacity-75" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-cs-amber" />
        </span>
        {status}
      </span>
    );
  }
  if (status === "FAILED") {
    return (
      <span className="bg-cs-amber px-2 py-0.5 font-mono text-xs tracking-[0.15em] text-cs-bg uppercase">
        {status}
      </span>
    );
  }
  // CREATED / CANCELLED: muted/neutral treatment.
  return (
    <span className="font-mono text-xs tracking-[0.15em] text-cs-muted uppercase">{status}</span>
  );
}

function NewAnalysisCta() {
  return (
    <Link
      href="/analyze/new"
      className="flex w-fit items-center gap-1.5 bg-cs-amber px-4 py-2 font-mono text-xs tracking-[0.1em] text-cs-bg uppercase transition-opacity hover:opacity-90"
    >
      <Plus className="h-3.5 w-3.5" strokeWidth={2.5} />
      New Analysis
    </Link>
  );
}

export default function SessionPicker({ sessions }: { sessions: SessionListItem[] }) {
  if (sessions.length === 0) {
    return (
      <div className="flex flex-col items-center gap-4 border border-cs-border bg-cs-panel p-10 text-center">
        <p className="text-cs-text">No sessions yet.</p>
        <NewAnalysisCta />
      </div>
    );
  }

  // Judgment call (Resolution 3): rather than a delete/archive feature
  // (explicitly out of scope), clutter is solved with clearer PRESENTATION
  // — sessions actively in flight (PROCESSING/QUEUED) are genuinely more
  // urgent than historical rows, so they're surfaced in their own group
  // above everything else. Within each group, reverse-chronological
  // (already the API's own order) is preserved.
  const inProgress = sessions.filter((s) => s.status === "PROCESSING" || s.status === "QUEUED");
  const rest = sessions.filter((s) => s.status !== "PROCESSING" && s.status !== "QUEUED");

  function renderRow(session: SessionListItem) {
    return (
      <li key={session.id} className="flex items-center justify-between gap-4 p-4">
        <Link href={`/dashboard?session=${session.id}`} className="min-w-0 flex-1">
          <p className="truncate text-cs-text hover:text-cs-teal">
            {session.video_original_filename}
          </p>
          <p className="mt-0.5 font-mono text-xs tracking-[0.1em] text-cs-muted">
            {new Date(session.created_at).toLocaleString()}
          </p>
        </Link>
        <div className="flex shrink-0 items-center gap-3">
          {/* Resolution 3: a CREATED (never-started) session gets an
              inline, immediately-actionable Start button right in the
              row — reusing SessionActions' own existing, already-tested
              Server Action, exactly as /sessions already does, instead of
              silently rotting as a confusing stale entry. */}
          {session.status === "CREATED" && (
            <SessionActions sessionId={session.id} status={session.status} />
          )}
          <StatusBadge status={session.status} />
        </div>
      </li>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <p className="font-mono text-xs tracking-[0.15em] text-cs-muted uppercase">
          {sessions.length} session{sessions.length === 1 ? "" : "s"}
        </p>
        <NewAnalysisCta />
      </div>

      {inProgress.length > 0 && (
        <div>
          <p className="mb-2 font-mono text-xs tracking-[0.15em] text-cs-amber uppercase">
            In Progress
          </p>
          <ul className="divide-y divide-cs-border border border-cs-amber bg-cs-panel">
            {inProgress.map(renderRow)}
          </ul>
        </div>
      )}

      <div>
        {inProgress.length > 0 && (
          <p className="mb-2 font-mono text-xs tracking-[0.15em] text-cs-muted uppercase">
            All Sessions
          </p>
        )}
        <ul className="divide-y divide-cs-border border border-cs-border bg-cs-panel">
          {rest.map(renderRow)}
        </ul>
      </div>
    </div>
  );
}
