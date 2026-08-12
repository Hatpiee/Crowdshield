# CrowdShield

CPU-first, modular, evidence-driven crowd intelligence and decision-support system.

## Setup

1. Clone the repo:
   ```
   git clone <repo-url>
   cd crowdshield
   ```

2. Create the backend virtual environment:
   ```
   cd backend && python3.11 -m venv .venv
   ```

3. Activate the virtual environment:

   macOS/Linux:
   ```
   source .venv/bin/activate
   ```

   Windows:
   ```
   .venv\Scripts\activate
   ```

4. Install backend dependencies:
   ```
   pip install -r requirements.txt
   ```

5. Return to the repo root:
   ```
   cd ..
   ```

6. Install root dependencies:
   ```
   npm install
   ```

7. Install frontend dependencies:
   ```
   cd frontend && npm install && cd ..
   ```

8. Create your local environment file:
   ```
   cp .env.example .env
   ```

9. Start both the frontend and backend from the repo root:
   ```
   npm run dev
   ```

10. Expected result:
    - Frontend: http://localhost:3000
    - Backend health check: http://localhost:8000/health

## Troubleshooting

**`npm run dev` behaves unexpectedly after a previous session** — stale routes,
unexpected 404s on endpoints you know exist, or port conflicts on 3000/8000.

The most likely cause is an orphaned process from an earlier session: on
Windows, stopping `npm run dev` (e.g. Ctrl+C, or a task runner killing the
top-level process) doesn't always terminate the child processes it spawned
(the uvicorn reloader/worker, the Turbopack dev server). Those can keep
running in the background, still bound to ports 3000/8000, silently serving
old code to anything that hits those ports.

A `predev` script now runs automatically before every `npm run dev` and
force-frees ports 3000 and 8000 first, so this shouldn't come up in normal
use. If it still does (or you just want to clean up manually), from
PowerShell:

```powershell
Get-Process python,node -ErrorAction SilentlyContinue
Stop-Process -Name python,node -Force -ErrorAction SilentlyContinue
```

The first line shows what's currently running so you can sanity-check before
killing anything; the second force-stops all `python.exe`/`node.exe`
processes system-wide, not just this project's — close any other Node/Python
work first if you have some running.

**A feature added in a recent phase behaves strangely, or a config value
seems to have no effect** — before assuming it's a code bug, check whether
your local `.env` is simply out of date. Every phase that adds a new
config variable documents it in `.env.example`, but your own `.env` (created
once via `cp .env.example .env` and never auto-updated) does NOT pick up
later changes automatically. Because pydantic-settings reads your real
`.env` BEFORE falling back to `config.py`'s own defaults, a stale key in
your `.env` silently overrides a newer, intentional default — this has
caused real, reproduced bugs three separate times in this project's history
(Phase 6, Phase 13, Phase 14 — see `DECISIONS.md`'s "Implementation-
Discovered Constraints" entries).

Run this after pulling any phase's changes that touched `.env.example`,
before assuming your `.env` is up to date:

```
python scripts/check_env_drift.py
```

It prints every config key your `.env` and `.env.example` have in common,
side by side, flagging any that differ, plus any key `.env.example` has that
your `.env` doesn't yet. A difference isn't automatically wrong (your own
`DATABASE_URL` password, for example, is supposed to differ) — the point is
just to make staleness visible so you can decide. It's a plain manual
script (stdlib only, no venv activation needed) — not run automatically by
any hook or CI.
