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
    <div className="mx-auto flex max-w-3xl flex-col gap-6 p-8">
      <h1 className="text-xl font-semibold">Create analysis session</h1>
      <CreateSessionForm videos={videos} />

      <h2 className="text-lg font-semibold">Sessions</h2>
      {sessions.length === 0 ? (
        <p className="text-sm text-gray-500">No sessions created yet.</p>
      ) : (
        <ul className="divide-y divide-gray-200">
          {sessions.map((session) => (
            <li
              key={session.id}
              className="flex items-center justify-between gap-4 py-3 text-sm"
            >
              <div>
                <div className="font-medium">
                  {session.video_original_filename}{" "}
                  <span className="font-normal text-gray-500">— {session.status}</span>
                </div>
                <div className="text-gray-500">
                  {session.model_config.detector_model}/{session.model_config.detector_runtime}
                  {" · "}
                  {new Date(session.created_at).toLocaleString()}
                </div>
              </div>
              <SessionActions sessionId={session.id} status={session.status} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
