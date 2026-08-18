"use client";

import { useRef, useState, useTransition, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { Loader2, TriangleAlert, UploadCloud, Video } from "lucide-react";

import { uploadVideo } from "@/app/videos/actions";
import { createSession, startSession } from "@/app/sessions/actions";
import { formatDateTime } from "@/lib/formatDate";

interface VideoOption {
  id: string;
  original_filename: string;
  created_at: string;
}

// Resolution 2's own explicit stages — never a silent gap where nothing
// visibly happens between clicking a video and landing on the monitor.
type Stage = "idle" | "uploading" | "creating" | "starting";

const STAGE_LABELS: Record<Exclude<Stage, "idle">, string> = {
  uploading: "Uploading video…",
  creating: "Creating session…",
  starting: "Starting analysis…",
};

export default function NewAnalysisFlow({ videos }: { videos: VideoOption[] }) {
  const router = useRouter();
  const formRef = useRef<HTMLFormElement>(null);
  const [stage, setStage] = useState<Stage>("idle");
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  const busy = isPending || stage !== "idle";

  // Step 2 (automatic, no separate page): create -> start -> redirect
  // straight to the live monitor. Reuses POST /sessions and
  // POST /sessions/{id}/start exactly as CreateSessionForm/SessionActions
  // already do — no new backend routes.
  async function createAndStart(videoId: string) {
    setStage("creating");
    const formData = new FormData();
    formData.set("video_id", videoId);
    const createResult = await createSession(formData);
    if (!createResult.success) {
      setError(createResult.message);
      setStage("idle");
      return;
    }

    setStage("starting");
    const startResult = await startSession(createResult.sessionId);
    if (!startResult.success) {
      setError(startResult.message);
      setStage("idle");
      return;
    }

    router.push(`/dashboard?session=${createResult.sessionId}`);
  }

  function handleSelectExisting(videoId: string) {
    setError(null);
    startTransition(() => createAndStart(videoId));
  }

  function handleUploadSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);
    setError(null);
    setStage("uploading");
    startTransition(async () => {
      const uploadResult = await uploadVideo(formData);
      if (!uploadResult.success) {
        setError(uploadResult.message);
        setStage("idle");
        return;
      }
      formRef.current?.reset();
      await createAndStart(uploadResult.videoId);
    });
  }

  return (
    <div className="flex flex-col gap-8">
      {stage !== "idle" && (
        <div className="flex items-center gap-3 border border-cs-teal bg-cs-panel px-4 py-3">
          <Loader2 className="h-4 w-4 animate-spin text-cs-teal" />
          <p className="font-mono text-xs tracking-[0.1em] text-cs-teal uppercase">
            {STAGE_LABELS[stage]}
          </p>
        </div>
      )}
      {error && (
        <div className="flex items-start gap-3 border border-cs-amber bg-cs-panel px-4 py-3">
          <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-cs-amber" />
          <p className="text-sm text-cs-text">{error}</p>
        </div>
      )}

      <section className="border border-cs-border bg-cs-panel p-5">
        <div className="mb-4 flex items-center gap-2">
          <UploadCloud className="h-4 w-4 text-cs-teal" />
          <h2 className="font-mono text-xs tracking-[0.15em] text-cs-muted uppercase">
            Upload a new video
          </h2>
        </div>
        <form ref={formRef} onSubmit={handleUploadSubmit} className="flex flex-col gap-3">
          <input
            type="file"
            name="file"
            accept="video/mp4,.mp4"
            required
            disabled={busy}
            className="block text-sm text-cs-text file:mr-3 file:border file:border-cs-border file:bg-cs-bg file:px-3 file:py-1.5 file:font-mono file:text-xs file:tracking-[0.1em] file:text-cs-muted file:uppercase disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={busy}
            className="w-fit bg-cs-amber px-4 py-2 font-mono text-xs tracking-[0.1em] text-cs-bg uppercase transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            Upload &amp; Start Analysis
          </button>
        </form>
      </section>

      <div className="flex items-center gap-3">
        <div className="h-px flex-1 bg-cs-border" />
        <span className="font-mono text-xs tracking-[0.15em] text-cs-muted uppercase">Or</span>
        <div className="h-px flex-1 bg-cs-border" />
      </div>

      <section className="border border-cs-border bg-cs-panel p-5">
        <div className="mb-4 flex items-center gap-2">
          <Video className="h-4 w-4 text-cs-teal" />
          <h2 className="font-mono text-xs tracking-[0.15em] text-cs-muted uppercase">
            Choose an existing video
          </h2>
        </div>
        {videos.length === 0 ? (
          <p className="text-sm text-cs-muted">No videos uploaded yet — upload one above.</p>
        ) : (
          <ul className="divide-y divide-cs-border border border-cs-border">
            {videos.map((video) => (
              <li key={video.id}>
                <button
                  onClick={() => handleSelectExisting(video.id)}
                  disabled={busy}
                  className="flex w-full items-center justify-between gap-4 p-4 text-left transition-colors hover:bg-cs-bg disabled:opacity-50"
                >
                  <div>
                    <p className="text-cs-text">{video.original_filename}</p>
                    <p className="mt-0.5 font-mono text-xs tracking-[0.1em] text-cs-muted">
                      {formatDateTime(video.created_at)}
                    </p>
                  </div>
                  <span className="font-mono text-xs tracking-[0.15em] text-cs-teal uppercase">
                    Analyze →
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
