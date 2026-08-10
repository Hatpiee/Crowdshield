"use server";

import { authFetch } from "@/lib/api";

export type UploadResult = { success: true } | { success: false; message: string };

export async function uploadVideo(formData: FormData): Promise<UploadResult> {
  const file = formData.get("file");
  if (!(file instanceof File) || file.size === 0) {
    return { success: false, message: "Choose a file to upload." };
  }

  const uploadData = new FormData();
  uploadData.set("file", file);

  const res = await authFetch("/api/v1/videos", {
    method: "POST",
    body: uploadData,
  });

  const body = await res.json();
  if (!res.ok || !body.success) {
    return {
      success: false,
      message: body?.error?.message ?? `Upload failed (status ${res.status})`,
    };
  }

  return { success: true };
}
