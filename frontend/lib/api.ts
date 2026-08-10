import { auth } from "@/auth";

export async function getHealth(): Promise<unknown> {
  const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/health`);
  if (!res.ok) {
    throw new Error(`Health check failed with status ${res.status}`);
  }
  return res.json();
}

export async function authFetch(
  path: string,
  options: RequestInit = {}
): Promise<Response> {
  const session = await auth();
  const headers = new Headers(options.headers);
  if (session?.access_token) {
    headers.set("Authorization", `Bearer ${session.access_token}`);
  }
  return fetch(`${process.env.NEXT_PUBLIC_API_URL}${path}`, {
    ...options,
    headers,
  });
}
