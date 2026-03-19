"""Run the Tripletex agent server."""
import uvicorn
import os

port = int(os.environ.get("PORT", 8000))
print(f"Starting Tripletex agent on port {port}")
print(f"Expose via: npx cloudflared tunnel --url http://localhost:{port}")
uvicorn.run("tripletex.server:app", host="0.0.0.0", port=port, reload=True)
