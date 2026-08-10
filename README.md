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
