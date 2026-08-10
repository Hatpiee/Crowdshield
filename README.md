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
