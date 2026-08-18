import AppHeader from "@/components/AppHeader";
import { authFetch } from "@/lib/api";

import NewAnalysisFlow from "./NewAnalysisFlow";

interface VideoOption {
  id: string;
  original_filename: string;
  created_at: string;
}

// Resolution 2: the ONE guided "upload -> analyze -> monitor" flow.
// Reuses GET /videos (existing, already-tested) for the "pick an existing
// video" list; the interactive upload/select/create/start chaining itself
// lives in NewAnalysisFlow (a Client Component — needs useState/router).
export default async function NewAnalysisPage() {
  const res = await authFetch("/api/v1/videos");
  const body = await res.json();
  const videos: VideoOption[] = res.ok && body.success ? body.data.items : [];

  return (
    <div className="flex min-h-full flex-1 flex-col bg-cs-bg text-cs-text">
      <AppHeader />
      <div className="mx-auto flex w-full max-w-2xl flex-col gap-8 p-8">
        <div>
          <h1 className="text-xl font-semibold">New Analysis</h1>
          <p className="mt-1 text-sm text-cs-muted">
            Upload a new video or choose one you&apos;ve already uploaded — analysis starts
            immediately and you&apos;ll land on the live monitor.
          </p>
        </div>
        <NewAnalysisFlow videos={videos} />
      </div>
    </div>
  );
}
