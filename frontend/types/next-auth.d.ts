import type { DefaultSession } from "next-auth";

declare module "next-auth" {
  interface User {
    role: string;
    access_token: string;
  }

  interface Session extends DefaultSession {
    access_token: string;
    role: string;
    id: string;
  }
}

// `next-auth/jwt` re-exports `@auth/core/jwt` via a wildcard (`export *`),
// which does not support TS declaration-merging augmentation — the merge has
// to target `@auth/core/jwt` directly, where the JWT interface actually lives.
declare module "@auth/core/jwt" {
  interface JWT {
    access_token?: string;
    role?: string;
    id?: string;
  }
}
