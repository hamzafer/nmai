"""
Tripletex Agent — FastAPI server with /solve endpoint.

Usage:
    python -m tripletex.server
    # Then expose via: npx cloudflared tunnel --url http://localhost:8000
"""

import os
from fastapi import FastAPI, Request, Header
from fastapi.responses import JSONResponse
from .agent import solve_task

app = FastAPI(title="Tripletex AI Agent")

# Optional API key protection
API_KEY = os.environ.get("TRIPLETEX_AGENT_KEY")


@app.post("/solve")
async def solve(request: Request, authorization: str = Header(default=None)):
    # Check API key if configured
    if API_KEY and authorization:
        token = authorization.replace("Bearer ", "")
        if token != API_KEY:
            return JSONResponse({"error": "unauthorized"}, status_code=401)

    body = await request.json()

    prompt = body.get("prompt", "")
    files = body.get("files", [])
    creds = body.get("tripletex_credentials", {})
    base_url = creds.get("base_url", "")
    session_token = creds.get("session_token", "")

    if not prompt or not base_url or not session_token:
        return JSONResponse(
            {"error": "Missing prompt, base_url, or session_token"},
            status_code=400,
        )

    result = solve_task(prompt, files, base_url, session_token)
    return JSONResponse(result)


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    print(f"Starting Tripletex agent on port {port}")
    print(f"Expose via: npx cloudflared tunnel --url http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
