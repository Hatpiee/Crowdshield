"use client";

import { useRef, useState, useTransition, type FormEvent } from "react";
import { useRouter } from "next/navigation";

import { uploadVideo } from "./actions";

export default function UploadForm() {
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
      const result = await uploadVideo(formData);
      if (result.success) {
        setMessage({ type: "success", text: "Video uploaded successfully." });
        formRef.current?.reset();
        router.refresh();
      } else {
        setMessage({ type: "error", text: result.message });
      }
    });
  }

  return (
    <form ref={formRef} onSubmit={handleSubmit} className="flex flex-col gap-3">
      <input
        type="file"
        name="file"
        accept="video/mp4,.mp4"
        required
        className="block text-sm text-cs-text file:mr-3 file:border file:border-cs-border file:bg-cs-bg file:px-3 file:py-1.5 file:font-mono file:text-xs file:tracking-[0.1em] file:text-cs-muted file:uppercase"
      />
      <button
        type="submit"
        disabled={isPending}
        className="w-fit bg-cs-amber px-3 py-2 font-mono text-xs tracking-[0.1em] text-cs-bg uppercase transition-opacity hover:opacity-90 disabled:opacity-50"
      >
        {isPending ? "Uploading…" : "Upload"}
      </button>
      {message && (
        <p
          className={`font-mono text-xs ${
            message.type === "success" ? "text-cs-teal" : "text-cs-amber"
          }`}
        >
          {message.text}
        </p>
      )}
    </form>
  );
}
