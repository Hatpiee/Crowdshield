import { NextResponse } from "next/server";

import { auth } from "@/auth";

// Named `proxy.ts`, not `middleware.ts` — Next.js 16 deprecated and renamed
// the middleware.js file convention to proxy.js. Behavior is identical; see
// node_modules/next/dist/docs/01-app/03-api-reference/03-file-conventions/proxy.md.
// "/" is the new public marketing landing page — safe to add here as an
// EXACT match only: the existing prefix check below tests
// `pathname.startsWith("${path}/")`, i.e. `pathname.startsWith("//")` for
// "/", which no real Next.js route ever satisfies. So "/" here does NOT
// accidentally make every path public — only the exact root route is
// affected; "/dashboard", "/videos", "/sessions", etc. remain protected.
const PUBLIC_PATHS = ["/login", "/"];

export default auth((req) => {
  const { pathname } = req.nextUrl;
  const isPublicPath = PUBLIC_PATHS.some(
    (path) => pathname === path || pathname.startsWith(`${path}/`)
  );

  if (!req.auth && !isPublicPath) {
    return NextResponse.redirect(new URL("/login", req.url));
  }
});

export const config = {
  // Excludes Next.js's own static/image assets, Auth.js's route handler
  // (which must stay reachable to actually perform sign-in/sign-out), and
  // root-level static files served from frontend/public/ (favicon.ico plus
  // any file with a common static asset extension, e.g. the default *.svg
  // icons) — these were previously only partially excluded (favicon.ico
  // only), so unauthenticated requests for e.g. /next.svg were incorrectly
  // redirected to /login instead of being served.
  matcher: [
    "/((?!api/auth|_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)",
  ],
};
