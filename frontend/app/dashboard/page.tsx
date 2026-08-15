import { auth } from "@/auth";
import LogoutButton from "@/components/LogoutButton";
import Logo from "@/components/Logo";
import LiveMonitor from "@/components/dashboard/LiveMonitor";
import SessionPicker from "@/components/dashboard/SessionPicker";
import { authFetch } from "@/lib/api";

interface SessionListItem {
  id: string;
  video_original_filename: string;
  status: string;
  created_at: string;
}

function DashboardHeader({ email, role }: { email?: string | null; role?: string }) {
  return (
    <header className="flex items-center justify-between border-b border-cs-border px-8 py-5">
      <Logo />
      <div className="flex items-center gap-4 font-mono text-xs tracking-[0.1em] text-cs-muted uppercase">
        <span>
          {email} ({role})
        </span>
        <LogoutButton />
      </div>
    </header>
  );
}

export default async function DashboardPage({
  searchParams,
}: {
  searchParams: Promise<{ session?: string | string[] }>;
}) {
  const { session: sessionParam } = await searchParams;
  const session = await auth();
  const targetSessionId = Array.isArray(sessionParam) ? sessionParam[0] : sessionParam;

  if (!targetSessionId) {
    const res = await authFetch("/api/v1/sessions");
    const body = await res.json();
    const sessions: SessionListItem[] = res.ok && body.success ? body.data.items : [];

    return (
      <div className="flex min-h-full flex-1 flex-col bg-cs-bg text-cs-text">
        <DashboardHeader email={session?.user?.email} role={session?.role} />
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 p-8">
          <h1 className="text-xl font-semibold">Select a session</h1>
          <SessionPicker sessions={sessions} />
        </div>
      </div>
    );
  }

  const [detailRes, statusRes] = await Promise.all([
    authFetch(`/api/v1/sessions/${targetSessionId}`),
    authFetch(`/api/v1/sessions/${targetSessionId}/status`),
  ]);
  const detailBody = await detailRes.json();
  const statusBody = await statusRes.json();

  if (!detailRes.ok || !detailBody.success || !statusRes.ok || !statusBody.success) {
    return (
      <div className="flex min-h-full flex-1 flex-col bg-cs-bg text-cs-text">
        <DashboardHeader email={session?.user?.email} role={session?.role} />
        <div className="mx-auto max-w-3xl p-8">
          <p>Session not found.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-full flex-1 flex-col bg-cs-bg text-cs-text">
      <DashboardHeader email={session?.user?.email} role={session?.role} />
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-8">
        <h1 className="text-xl font-semibold">{detailBody.data.video_original_filename}</h1>
        <LiveMonitor
          sessionId={targetSessionId}
          videoId={detailBody.data.video_id}
          accessToken={session?.access_token ?? ""}
          initialStatus={statusBody.data}
        />
      </div>
    </div>
  );
}
