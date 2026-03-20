#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "=== Tripletex Agent Launcher ==="
echo ""

# 1. Setup venv and install deps
echo "[1/5] Setting up venv and installing dependencies..."
if [ ! -d ".venv" ]; then
    uv venv
fi
source .venv/bin/activate
uv pip install fastapi uvicorn requests

# 2. Check gcloud
echo "[2/5] Checking gcloud auth..."
gcloud config configurations activate gcplab 2>/dev/null || true
TOKEN=$(gcloud auth print-access-token 2>/dev/null)
if [ -z "$TOKEN" ]; then
    echo "ERROR: gcloud auth failed. Run: gcloud auth login"
    exit 1
fi
echo "  OK — token starts with ${TOKEN:0:10}..."

# 3. Test Claude
echo "[3/5] Testing Claude via Vertex AI..."
python3 -c "from tripletex.llm import call_claude; print('  OK —', call_claude('Say hi in 3 words', max_tokens=20))"

# 4. Start server in background
echo "[4/5] Starting FastAPI server on port 8000..."
python3 -m tripletex &
SERVER_PID=$!
sleep 2

# Check server is up
if ! curl -sf http://localhost:8000/health > /dev/null 2>&1; then
    echo "ERROR: Server failed to start"
    kill $SERVER_PID 2>/dev/null
    exit 1
fi
echo "  OK — server running (PID $SERVER_PID)"

# 5. Start cloudflared tunnel
echo "[5/5] Starting Cloudflare tunnel..."
echo ""
echo "========================================="
echo "  Copy the tunnel URL below and submit"
echo "  at: https://app.ainm.no/submit/tripletex"
echo "========================================="
echo ""
npx cloudflared tunnel --url http://localhost:8000

# Cleanup on exit
trap "kill $SERVER_PID 2>/dev/null; echo 'Server stopped.'" EXIT
