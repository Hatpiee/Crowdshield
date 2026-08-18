import Link from "next/link";

import AppHeader from "@/components/AppHeader";
import { authFetch } from "@/lib/api";

import CreateSessionForm from "./CreateSessionForm";
import SessionActions from "./SessionActions";

interface ModelConfigSummary {
  detector_model: string;
  detector_runtime: string;
  vlm_model: string;
  llm_model: string;
}

interface SessionItem {
  id: string;
  video_id: string;
  video_original_filename: string;
  model_config: ModelConfigSummary;
  status: string;
  created_by_email: string;
  created_at: string;
}

interface VideoOption {
  id: string;
  original_filename: string;
}

export default async function SessionsPage() {
  const [sessionsRes, videosRes] = await Promise.all([
    authFetch("/api/v1/sessions"),
    authFetch("/api/v1/videos"),
  ]);

  const sessionsBody = await sessionsRes.json();
  const videosBody = await videosRes.json();

  const sessions: SessionItem[] =
    sessionsRes.ok && sessionsBody.success ? sessionsBody.data.items : [];
  const videos: VideoOption[] =
    videosRes.ok && videosBody.success ? videosBody.data.items : [];

  return (
    <div className="flex min-h-full flex-1 flex-col bg-cs-bg text-cs-text">
      <AppHeader active="sessions" />
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-8 p-8">
        <div>
          <h1 className="text-xl font-semibold">Sessions</h1>
          <p className="mt-1 text-sm text-cs-muted">
            Create sessions here, or use{" "}
            <Link href="/analyze/new" className="text-cs-teal hover:opacity-80">
              + New Analysis
            </Link>{" "}
            to upload/select a video and start monitoring in one step.
          </p>
        </div>

        <section className="border border-cs-border bg-cs-panel p-5">
          <h2 className="mb-4 font-mono text-xs tracking-[0.15em] text-cs-muted uppercase">
            Create analysis session
          </h2>
          <CreateSessionForm videos={videos} />
        </section>

        <section>
          <h2 className="mb-3 font-mono text-xs tracking-[0.15em] text-cs-muted uppercase">
            All sessions
          </h2>
          {sessions.length === 0 ? (
            <div className="border border-cs-border bg-cs-panel p-10 text-center">
              <p className="text-cs-muted">No sessions created yet.</p>
            </div>
          ) : (
            <ul className="divide-y divide-cs-border border border-cs-border bg-cs-panel">
              {sessions.map((session) => (
                <li key={session.id} className="flex items-center justify-between gap-4 p-4">
                  <div className="min-w-0">
                    <p className="truncate text-cs-text">
                      {session.video_original_filename}{" "}
                      <span className="font-mono text-xs tracking-[0.1em] text-cs-muted uppercase">
                        — {session.status}
                      </span>
                    </p>
                    <p className="mt-0.5 font-mono text-xs tracking-[0.1em] text-cs-muted">
                      {session.model_config.detector_model}/{session.model_config.detector_runtime}
                      {" · "}
                      {new Date(session.created_at).toLocaleString()}
                    </p>
                  </div>
                  <SessionActions sessionId={session.id} status={session.status} />
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
