#!/usr/bin/env bash
# Start the local dev stack (backend + frontend) with dev-auth-bypass

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "=== Starting Stand-Alone Analyzer local stack ==="
echo ""

# Check prerequisites
if ! psql -h 127.0.0.1 -U houkjang -d saa_test -c "SELECT 1;" > /dev/null 2>&1; then
  echo "ERROR: PostgreSQL database 'saa_test' is not accessible."
  echo "Make sure PostgreSQL is running and the database exists."
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "ERROR: .venv not found. Run 'uv venv && uv pip install -e .' first."
  exit 1
fi

if [ ! -d "web/node_modules" ]; then
  echo "ERROR: web/node_modules not found. Run 'cd web && npm install' first."
  exit 1
fi

# Start backend
echo "Starting backend (uvicorn)..."
source .venv/bin/activate
SAA_AUTH_DEV_BYPASS=1 \
SAA_S3_BUCKET=qpress-scans-private \
SAA_DB_URL=postgresql+asyncpg://houkjang@127.0.0.1:5432/saa_test \
nohup uvicorn flake_analysis.api.main:app --host 127.0.0.1 --port 8000 --reload > /tmp/saa-backend.log 2>&1 &
BACKEND_PID=$!
echo "Backend started (PID: $BACKEND_PID, logs: /tmp/saa-backend.log)"
echo "Backend URL: http://127.0.0.1:8000"
echo ""

# Wait for backend to be ready
echo "Waiting for backend to be ready..."
for i in {1..30}; do
  if curl -s http://127.0.0.1:8000/api/v1/auth/me > /dev/null 2>&1; then
    echo "Backend ready!"
    break
  fi
  if [ $i -eq 30 ]; then
    echo "ERROR: Backend did not start within 30 seconds."
    echo "Check logs: tail -f /tmp/saa-backend.log"
    exit 1
  fi
  sleep 1
done
echo ""

# Start frontend
echo "Starting frontend (vite)..."
cd web
nohup npm run dev > /tmp/saa-frontend.log 2>&1 &
FRONTEND_PID=$!
cd ..
echo "Frontend started (PID: $FRONTEND_PID, logs: /tmp/saa-frontend.log)"
echo ""

# Wait for frontend to be ready
echo "Waiting for frontend to be ready..."
for i in {1..30}; do
  if curl -s http://localhost:5173 > /dev/null 2>&1; then
    echo "Frontend ready!"
    break
  fi
  if [ $i -eq 30 ]; then
    echo "ERROR: Frontend did not start within 30 seconds."
    echo "Check logs: tail -f /tmp/saa-frontend.log"
    exit 1
  fi
  sleep 1
done
echo ""

echo "=== Local stack is ready! ==="
echo ""
echo "Open in browser: http://localhost:5173"
echo ""
echo "You'll be automatically logged in as 'local@dev' (admin)."
echo ""
echo "To test the 8-GPU SAM workflow:"
echo "  1. Open http://localhost:5173 in your browser"
echo "  2. Click the 'test' project (or create a new one)"
echo "  3. Click '+ New scan' to upload images"
echo "  4. Fill in scan name and material, drop a folder of images"
echo "  5. Click 'Start upload' then 'Finalize scan'"
echo "  6. Once upload completes, click 'Run SAM' in the Single-step fallback section"
echo "  7. Watch the SSE progress (GPU launching, ready, progress, done)"
echo ""
echo "To stop the stack:"
echo "  pkill -f 'uvicorn flake_analysis.api.main:app'"
echo "  pkill -f 'vite'"
echo ""
echo "Logs:"
echo "  Backend: tail -f /tmp/saa-backend.log"
echo "  Frontend: tail -f /tmp/saa-frontend.log"
