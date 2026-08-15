"use client";

import { useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL;
const POLL_INTERVAL_MS = 10000;

interface CheckResult {
  status: string;
  count?: number;
}

interface SystemStatusPayload {
  database: CheckResult;
  ollama: CheckResult;
  processing_sessions: CheckResult;
}

function StatusDot({ ok }: { ok: boolean }) {
  return (
    <span
      className={`inline-block h-1.5 w-1.5 flex-none rounded-full ${
        ok ? "bg-cs-teal" : "bg-cs-amber"
      }`}
    />
  );
}

export default function SystemStatusBadge({ accessToken }: { accessToken: string }) {
  const [data, setData] = useState<SystemStatusPayload | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const res = await fetch(`${API_URL}/api/v1/system/status`, {
          headers: { Authorization: `Bearer ${accessToken}` },
        });
        if (cancelled || !res.ok) return;
        const body = await res.json();
        if (body.success) setData(body.data);
      } catch {
        // A failed status-check fetch is itself informative but not
        // fatal — the widget just keeps showing its last-known state.
      }
    }

    poll();
    const interval = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [accessToken]);

  return (
    <div className="border border-cs-border bg-cs-panel p-5">
      <p className="mb-2 font-mono text-xs tracking-[0.15em] text-cs-muted uppercase">
        System Status
      </p>
      {!data ? (
        <p className="text-sm text-cs-muted">Checking…</p>
      ) : (
        <ul className="space-y-1.5 text-sm text-cs-text">
          <li className="flex items-center gap-2">
            <StatusDot ok={data.database.status === "ok"} /> Database
          </li>
          <li className="flex items-center gap-2">
            <StatusDot ok={data.ollama.status === "ok"} /> Vision / Reasoning (Ollama)
          </li>
          <li className="flex items-center gap-2">
            <StatusDot ok={data.processing_sessions.status === "ok"} /> Processing sessions:{" "}
            {data.processing_sessions.count ?? "—"}
          </li>
        </ul>
      )}
    </div>
  );
}
