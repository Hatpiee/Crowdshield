import Link from "next/link";

interface SessionListItem {
  id: string;
  video_original_filename: string;
  status: string;
  created_at: string;
}

export default function SessionPicker({ sessions }: { sessions: SessionListItem[] }) {
  if (sessions.length === 0) {
    return (
      <div className="border border-cs-border bg-cs-panel p-10 text-center">
        <p className="text-cs-text">No sessions yet.</p>
        <Link
          href="/sessions"
          className="mt-4 inline-block font-mono text-xs tracking-[0.15em] text-cs-teal uppercase hover:opacity-80"
        >
          Create a session →
        </Link>
      </div>
    );
  }

  return (
    <ul className="divide-y divide-cs-border border border-cs-border bg-cs-panel">
      {sessions.map((session) => (
        <li key={session.id}>
          <Link
            href={`/dashboard?session=${session.id}`}
            className="flex items-center justify-between gap-4 p-4 transition-colors hover:bg-cs-bg"
          >
            <div>
              <p className="text-cs-text">{session.video_original_filename}</p>
              <p className="mt-0.5 font-mono text-xs tracking-[0.1em] text-cs-muted">
                {new Date(session.created_at).toLocaleString()}
              </p>
            </div>
            <span className="font-mono text-xs tracking-[0.15em] text-cs-teal uppercase">
              {session.status}
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}
