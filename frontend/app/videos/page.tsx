import { authFetch } from "@/lib/api";

import UploadForm from "./UploadForm";

interface VideoItem {
  id: string;
  original_filename: string;
  file_size_bytes: number;
  mime_type: string;
  uploaded_by_email: string;
  created_at: string;
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

export default async function VideosPage() {
  const res = await authFetch("/api/v1/videos");
  const body = await res.json();
  const videos: VideoItem[] = res.ok && body.success ? body.data.items : [];

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-6 p-8">
      <h1 className="text-xl font-semibold">Upload video</h1>
      <UploadForm />

      <h2 className="text-lg font-semibold">Uploaded videos</h2>
      {videos.length === 0 ? (
        <p className="text-sm text-gray-500">No videos uploaded yet.</p>
      ) : (
        <ul className="divide-y divide-gray-200">
          {videos.map((video) => (
            <li key={video.id} className="py-2 text-sm">
              <div className="font-medium">{video.original_filename}</div>
              <div className="text-gray-500">
                {formatBytes(video.file_size_bytes)} · {video.uploaded_by_email} ·{" "}
                {new Date(video.created_at).toLocaleString()}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
