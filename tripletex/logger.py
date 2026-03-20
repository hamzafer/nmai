"""
Persistent logging for every submission.

Saves each /solve request as a timestamped JSON file under tripletex/logs/.
Each log contains: incoming request, LLM prompts/responses, API plan, execution results.
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

LOGS_DIR = Path(__file__).parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)


class SubmissionLog:
    """Accumulates data for one /solve submission, then saves to disk."""

    def __init__(self):
        self.ts = datetime.now(timezone.utc)
        self.data = {
            "timestamp": self.ts.isoformat(),
            "prompt": None,
            "files": [],
            "base_url": None,
            "llm_calls": [],
            "plan": [],
            "api_calls": [],
            "fix_plan": [],
            "fix_api_calls": [],
            "duration_s": None,
        }
        self._start = time.monotonic()

    def set_request(self, prompt: str, files: list, base_url: str):
        self.data["prompt"] = prompt
        self.data["files"] = [
            {"filename": f.get("filename"), "mime_type": f.get("mime_type"),
             "size_bytes": len(f.get("content_base64", ""))}
            for f in files
        ]
        self.data["base_url"] = base_url

    def add_llm_call(self, role: str, prompt_text: str, response_text: str):
        self.data["llm_calls"].append({
            "role": role,
            "prompt_length": len(prompt_text),
            "prompt": prompt_text[:5000],
            "response_length": len(response_text),
            "response": response_text,
        })

    def set_plan(self, plan: list):
        self.data["plan"] = plan

    def set_api_results(self, results: list):
        self.data["api_calls"] = results

    def set_fix_plan(self, plan: list):
        self.data["fix_plan"] = plan

    def set_fix_results(self, results: list):
        self.data["fix_api_calls"] = results

    def save(self):
        self.data["duration_s"] = round(time.monotonic() - self._start, 2)
        fname = self.ts.strftime("%Y%m%d_%H%M%S") + ".json"
        path = LOGS_DIR / fname
        path.write_text(json.dumps(self.data, indent=2, default=str, ensure_ascii=False))
        print(f"  Log saved: {path}")
        return path
