"""
Claude Opus 4.6 via Vertex AI — thin wrapper using gcloud auth.
"""

import json
import subprocess
import requests


VERTEX_PROJECT = "ai-nm26osl-1717"
VERTEX_REGION = "us-east5"
VERTEX_MODEL = "claude-opus-4-6"
VERTEX_URL = (
    f"https://{VERTEX_REGION}-aiplatform.googleapis.com/v1/"
    f"projects/{VERTEX_PROJECT}/locations/{VERTEX_REGION}/"
    f"publishers/anthropic/models/{VERTEX_MODEL}:rawPredict"
)


def _get_access_token() -> str:
    """Get OAuth2 token from gcloud CLI."""
    result = subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gcloud auth failed: {result.stderr}")
    return result.stdout.strip()


def call_claude(prompt, system: str = "", max_tokens: int = 4096) -> str:
    """
    Call Claude Opus 4.6 via Vertex AI.
    prompt: str (text-only) or list of content blocks (multimodal).
    Returns the text response.
    """
    token = _get_access_token()

    messages = [{"role": "user", "content": prompt}]
    body = {
        "anthropic_version": "vertex-2023-10-16",
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system:
        body["system"] = system

    resp = requests.post(
        VERTEX_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=120,
    )

    if resp.status_code != 200:
        raise RuntimeError(f"Vertex AI error {resp.status_code}: {resp.text[:500]}")

    data = resp.json()
    return data["content"][0]["text"]


if __name__ == "__main__":
    print(call_claude("Say hello in exactly 3 words."))
