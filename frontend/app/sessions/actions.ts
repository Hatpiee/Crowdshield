"use server";

import { authFetch } from "@/lib/api";

export type ActionResult = { success: true } | { success: false; message: string };

async function parseActionResponse(res: Response): Promise<ActionResult> {
  const body = await res.json();
  if (!res.ok || !body.success) {
    return {
      success: false,
      message: body?.error?.message ?? `Request failed (status ${res.status})`,
    };
  }
  return { success: true };
}

export async function createSession(formData: FormData): Promise<ActionResult> {
  const videoId = formData.get("video_id");
  if (typeof videoId !== "string" || !videoId) {
    return { success: false, message: "Choose a video." };
  }

  const res = await authFetch("/api/v1/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ video_id: videoId }),
  });
  return parseActionResponse(res);
}

export async function startSession(sessionId: string): Promise<ActionResult> {
  const res = await authFetch(`/api/v1/sessions/${sessionId}/start`, { method: "POST" });
  return parseActionResponse(res);
}

export async function cancelSession(sessionId: string): Promise<ActionResult> {
  const res = await authFetch(`/api/v1/sessions/${sessionId}/cancel`, { method: "POST" });
  return parseActionResponse(res);
}
