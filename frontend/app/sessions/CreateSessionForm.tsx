"use client";

import { useRef, useState, useTransition, type FormEvent } from "react";
import { useRouter } from "next/navigation";

import { createSession } from "./actions";

interface VideoOption {
  id: string;
  original_filename: string;
}

export default function CreateSessionForm({ videos }: { videos: VideoOption[] }) {
  const router = useRouter();
  const formRef = useRef<HTMLFormElement>(null);
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(
    null
  );
  const [isPending, startTransition] = useTransition();

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);
    setMessage(null);

    startTransition(async () => {
      const result = await createSession(formData);
      if (result.success) {
        setMessage({ type: "success", text: "Session created." });
        formRef.current?.reset();
        router.refresh();
      } else {
        setMessage({ type: "error", text: result.message });
      }
    });
  }

  return (
    <form ref={formRef} onSubmit={handleSubmit} className="flex flex-col gap-3">
      <select
        name="video_id"
        required
        defaultValue=""
        className="block w-fit rounded border border-gray-300 px-3 py-2 text-sm"
      >
        <option value="" disabled>
          Select a video…
        </option>
        {videos.map((video) => (
          <option key={video.id} value={video.id}>
            {video.original_filename}
          </option>
        ))}
      </select>
      <button
        type="submit"
        disabled={isPending || videos.length === 0}
        className="w-fit rounded bg-black px-3 py-2 text-sm text-white disabled:opacity-50"
      >
        {isPending ? "Creating…" : "Create Session"}
      </button>
      {videos.length === 0 && (
        <p className="text-sm text-gray-500">Upload a video first.</p>
      )}
      {message && (
        <p
          className={
            message.type === "success" ? "text-sm text-green-600" : "text-sm text-red-600"
          }
        >
          {message.text}
        </p>
      )}
    </form>
  );
}
