import Link from "next/link";

import AppHeader from "@/components/AppHeader";
import { authFetch } from "@/lib/api";

import UploadForm from "./UploadForm";

interface VideoItem {
  id: string;
  original_filename: string;
  file_size_bytes: number;
  mime_type: string;
  uploaded_by_email: string;
  created_at: string;
  duration_seconds: number | null;
  width: number | null;
  height: number | null;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes;
  let unitIndex = -1;
  do {
    value /= 1024;
    unitIndex += 1;
  } while (value >= 1024 && unitIndex < units.length - 1);
  return `${value.toFixed(1)} ${units[unitIndex]}`;
}

function formatDuration(seconds: number): string {
  const totalSeconds = Math.round(seconds);
  const mm = Math.floor(totalSeconds / 60);
  const ss = totalSeconds % 60;
  return `${mm}:${ss.toString().padStart(2, "0")}`;
}

export default async function VideosPage() {
  const res = await authFetch("/api/v1/videos");
  const body = await res.json();
  const videos: VideoItem[] = res.ok && body.success ? body.data.items : [];

  return (
    <div className="flex min-h-full flex-1 flex-col bg-cs-bg text-cs-text">
      <AppHeader active="videos" />
      <div className="mx-auto flex w-full max-w-2xl flex-col gap-8 p-8">
        <div>
          <h1 className="text-xl font-semibold">Videos</h1>
          <p className="mt-1 text-sm text-cs-muted">
            Upload footage here, or use{" "}
            <Link href="/analyze/new" className="text-cs-teal hover:opacity-80">
              + New Analysis
            </Link>{" "}
            to upload and start monitoring in one step.
          </p>
        </div>

        <section className="border border-cs-border bg-cs-panel p-5">
          <h2 className="mb-4 font-mono text-xs tracking-[0.15em] text-cs-muted uppercase">
            Upload video
          </h2>
          <UploadForm />
        </section>

        <section>
          <h2 className="mb-3 font-mono text-xs tracking-[0.15em] text-cs-muted uppercase">
            Uploaded videos
          </h2>
          {videos.length === 0 ? (
            <div className="border border-cs-border bg-cs-panel p-10 text-center">
              <p className="text-cs-muted">No videos uploaded yet.</p>
            </div>
          ) : (
            <ul className="divide-y divide-cs-border border border-cs-border bg-cs-panel">
              {videos.map((video) => (
                <li key={video.id} className="p-4">
                  <div className="text-cs-text">{video.original_filename}</div>
                  <div className="mt-0.5 font-mono text-xs tracking-[0.1em] text-cs-muted">
                    {formatBytes(video.file_size_bytes)}
                    {video.duration_seconds !== null && (
                      <> · {formatDuration(video.duration_seconds)}</>
                    )}
                    {video.width !== null && video.height !== null && (
                      <>
                        {" "}
                        · {video.width}×{video.height}
                      </>
                    )}{" "}
                    · {video.uploaded_by_email} · {new Date(video.created_at).toLocaleString()}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
