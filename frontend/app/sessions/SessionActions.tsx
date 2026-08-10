"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";

import { cancelSession, startSession } from "./actions";

export default function SessionActions({
  sessionId,
  status,
}: {
  sessionId: string;
  status: string;
}) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  const canStart = status === "CREATED";
  const canCancel = status === "CREATED" || status === "QUEUED";

  function handleStart() {
    setError(null);
    startTransition(async () => {
      const result = await startSession(sessionId);
      if (result.success) {
        router.refresh();
      } else {
        setError(result.message);
      }
    });
  }

  function handleCancel() {
    setError(null);
    startTransition(async () => {
      const result = await cancelSession(sessionId);
      if (result.success) {
        router.refresh();
      } else {
        setError(result.message);
      }
    });
  }

  if (!canStart && !canCancel) {
    return null;
  }

  return (
    <div className="flex items-center gap-2">
      {canStart && (
        <button
          onClick={handleStart}
          disabled={isPending}
          className="rounded bg-black px-2 py-1 text-xs text-white disabled:opacity-50"
        >
          {isPending ? "…" : "Start"}
        </button>
      )}
      {canCancel && (
        <button
          onClick={handleCancel}
          disabled={isPending}
          className="rounded border border-gray-300 px-2 py-1 text-xs disabled:opacity-50"
        >
          {isPending ? "…" : "Cancel"}
        </button>
      )}
      {error && <span className="text-xs text-red-600">{error}</span>}
    </div>
  );
}
