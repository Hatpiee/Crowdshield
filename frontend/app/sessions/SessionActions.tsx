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
          className="bg-cs-amber px-2 py-1 font-mono text-[10px] tracking-[0.1em] text-cs-bg uppercase transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          {isPending ? "…" : "Start Analysis"}
        </button>
      )}
      {canCancel && (
        <button
          onClick={handleCancel}
          disabled={isPending}
          className="border border-cs-border px-2 py-1 font-mono text-[10px] tracking-[0.1em] text-cs-muted uppercase transition-colors hover:border-cs-amber hover:text-cs-amber disabled:opacity-50"
        >
          {isPending ? "…" : "Cancel"}
        </button>
      )}
      {error && <span className="font-mono text-[10px] text-cs-amber">{error}</span>}
    </div>
  );
}
