"use client";

import { signOut } from "next-auth/react";

export default function LogoutButton() {
  return (
    <button
      onClick={() => signOut({ redirectTo: "/login" })}
      className="rounded border border-gray-300 px-3 py-2 text-sm"
    >
      Log out
    </button>
  );
}
