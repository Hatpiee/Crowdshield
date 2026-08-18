"use client";

import { signOut } from "next-auth/react";

export default function LogoutButton() {
  return (
    <button
      onClick={() => signOut({ redirectTo: "/login" })}
      className="border border-cs-border px-3 py-1.5 font-mono text-xs tracking-[0.1em] text-cs-muted uppercase transition-colors hover:border-cs-amber hover:text-cs-amber"
    >
      Log out
    </button>
  );
}
