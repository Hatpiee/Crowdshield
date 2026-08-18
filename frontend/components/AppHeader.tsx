import Link from "next/link";
import { Plus } from "lucide-react";

import { auth } from "@/auth";
import Logo from "@/components/Logo";
import LogoutButton from "@/components/LogoutButton";

// Resolution 1 (navigation/auth-confusion fix): the ONE shared header for
// every AUTHENTICATED page (/dashboard, /videos, /sessions,
// /incidents/[id], /analyze/new). Its logo links to /dashboard, never to
// "/" — landing an authenticated operator on the public marketing page
// (Logo.tsx's own default target) looked and felt exactly like being
// logged out, even though the session was still valid, because that page
// has no authenticated UI at all. The separate, intentionally-different
// public header used on "/" and "/login" is untouched — this component is
// never imported there.
//
// Calls auth() itself (rather than requiring every caller to fetch the
// session and prop-drill email/role/access_token down) so consuming pages
// just render `<AppHeader active="videos" />` with zero duplication —
// Next.js/next-auth dedupes repeated auth() calls within one request.
const NAV_LINKS = [
  { href: "/dashboard", label: "Dashboard", key: "dashboard" },
  { href: "/videos", label: "Videos", key: "videos" },
  { href: "/sessions", label: "Sessions", key: "sessions" },
] as const;

export default async function AppHeader({
  active,
}: {
  active?: "dashboard" | "videos" | "sessions";
}) {
  const session = await auth();

  return (
    <header className="flex flex-wrap items-center justify-between gap-4 border-b border-cs-border px-8 py-5">
      <div className="flex flex-wrap items-center gap-8">
        <Logo href="/dashboard" />
        <nav className="flex items-center gap-6">
          {NAV_LINKS.map((link) => (
            <Link
              key={link.key}
              href={link.href}
              className={`font-mono text-xs tracking-[0.15em] uppercase transition-colors ${
                active === link.key
                  ? "text-cs-teal"
                  : "text-cs-muted hover:text-cs-text"
              }`}
            >
              {link.label}
            </Link>
          ))}
        </nav>
      </div>

      <div className="flex items-center gap-4">
        <Link
          href="/analyze/new"
          className="flex items-center gap-1.5 bg-cs-amber px-3 py-1.5 font-mono text-xs tracking-[0.1em] text-cs-bg uppercase transition-opacity hover:opacity-90"
        >
          <Plus className="h-3.5 w-3.5" strokeWidth={2.5} />
          New Analysis
        </Link>
        <span className="hidden font-mono text-xs tracking-[0.1em] text-cs-muted uppercase sm:inline">
          {session?.user?.email} ({session?.role})
        </span>
        <LogoutButton />
      </div>
    </header>
  );
}
