"use strict";

// predev port-cleanup wrapper — see README's Troubleshooting section.
//
// REAL BUG THIS REPLACES: invoking the `kill-port` CLI directly from
// `predev` (`kill-port 3000 8000`) made npm's whole pre<script> -> script
// chain non-deterministically abort `npm run dev` before either server
// ever started. Root cause, confirmed by reading kill-port's own source
// (node_modules/kill-port/index.js): on win32, when NOTHING is listening
// on a port, its PID list is empty and it still runs
// `TaskKill /F /PID ` with no actual PID value — that invalid taskkill
// invocation fails, the underlying Promise rejects, and the kill-port CLI
// exits non-zero. npm treats any non-zero predev exit as fatal and never
// runs `dev` at all. Since the ports being ALREADY clean is the common
// case (not the exception), this silently broke `npm run dev` far more
// often than it worked.
//
// This wrapper calls kill-port PROGRAMMATICALLY per port, catches (not
// just logs and rethrows) any rejection from either port individually, and
// always exits 0 — port cleanup is deliberately best-effort, never a
// blocking step. A port kill-port could not act on (because it was
// already clean, or for any other reason) must never prevent the real dev
// servers from starting.

const killPort = require("kill-port");

const PORTS = [3000, 8000];

async function killAll() {
  for (const port of PORTS) {
    try {
      await killPort(port);
      console.log(`[kill-dev-ports] port ${port}: cleared (a process was killed, or nothing needed killing).`);
    } catch (err) {
      // Not necessarily a real failure — on this platform/kill-port
      // version, "nothing was listening" and "a real error occurred" can
      // both surface as a rejection here, and we cannot always tell them
      // apart from the error message alone. Either way, this must never
      // block `npm run dev`.
      console.log(`[kill-dev-ports] port ${port}: kill-port reported an issue (likely already clean) — continuing: ${err.message}`);
    }
  }
}

killAll()
  .catch((err) => {
    // Defense in depth — killAll() already catches per-port, so this
    // should be unreachable, but predev must NEVER fail regardless.
    console.log(`[kill-dev-ports] unexpected error, ignoring: ${err.message}`);
  })
  .finally(() => {
    process.exit(0);
  });
