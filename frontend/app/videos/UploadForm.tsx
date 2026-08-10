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
        className="block text-sm"
      />
      <button
        type="submit"
        disabled={isPending}
        className="w-fit rounded bg-black px-3 py-2 text-sm text-white disabled:opacity-50"
      >
        {isPending ? "Uploading…" : "Upload"}
      </button>
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
