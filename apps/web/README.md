# DeepSearch Web Frontend

## Role of this directory
This directory contains the standalone web frontend for the DeepSearch project, providing a clean operator UI for the existing backend workflow.

## Running Locally

### 1. Configure Environment
Create a `.env` file in this directory if you need to override the default API URL. By default, it expects the local orchestrator at `http://127.0.0.1:8000`.

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
```

### 2. Install Dependencies
```bash
npm install
```

### 3. Start Development Server
```bash
npm run dev
```

The default dev server binds to `127.0.0.1`. If you are SSH'd into a Linux server from a Mac, the URL printed by Vite is local to the server and will not open directly in the Mac browser unless you forward the port.

### Recommended SSH Tunnel From A Mac

From the Mac, open a separate terminal:

```bash
ssh -N \
  -L 5173:127.0.0.1:5173 \
  -L 8000:127.0.0.1:8000 \
  user@server-host
```

On the Linux server, run the backend and frontend with loopback binds:

```bash
python3 -m uvicorn services.orchestrator.app.main:app --host 127.0.0.1 --port 8000
cd apps/web
npm run dev
```

Then open `http://127.0.0.1:5173` on the Mac. The frontend API URL `http://127.0.0.1:8000` will also be forwarded to the server backend through the SSH tunnel.

### Direct Server-IP Access

Use this only on a trusted network or with firewall rules limited to your Mac's IP:

```bash
python3 -m uvicorn services.orchestrator.app.main:app --host 0.0.0.0 --port 8000
cd apps/web
VITE_API_BASE_URL=http://SERVER_IP:8000 npm run dev:remote
```

Then open `http://SERVER_IP:5173` from the Mac. If it still does not load, check the server firewall or cloud security group for ports `5173` and `8000`.

### Supported Routes
Currently implemented and connected to the backend:
- `/tasks/new` - Create a research task
- `/tasks/:taskId` - View task summary and navigation
- `/tasks/:taskId/sources` - Inspect source documents and chunks
- `/tasks/:taskId/claims` - Inspect drafted claims, evidence, and verification state
- `/tasks/:taskId/report` - View the generated markdown report

## Architecture and Design
This frontend is intentionally simple and adheres strictly to the backend contracts provided in `docs/api.md`. 
- **Tech Stack:** Vite, React, TypeScript, React Router.
- **State Management:** Local React state and simple custom hooks (e.g., `src/features/tasks/hooks.ts`), with no complex global state frameworks.
- **API Client:** Lightweight fetch wrapper located in `src/lib/http.ts`.
