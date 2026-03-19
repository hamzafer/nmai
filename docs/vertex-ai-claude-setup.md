# Using Claude Models via Google Cloud Vertex AI

## Overview

Anthropic's Claude models (including Claude Opus 4.6) are available through Google Cloud's **Vertex AI Model Garden** — no Anthropic API key required. Authentication uses Google Cloud OAuth2 (not API keys).

**Console**: https://console.cloud.google.com/vertex-ai/publishers/anthropic/model-garden/claude-opus-4-6

## Prerequisites

- Google Cloud account with Vertex AI API enabled
- `gcloud` CLI installed
- Project with access to Anthropic models in the Model Garden

## 1. Multi-Account gcloud Setup

If you already have a default gcloud config (e.g. for work), create a separate named configuration:

```bash
# Create a new named config
gcloud config configurations create gcplab

# Authenticate (opens browser)
gcloud auth login

# Set the project
gcloud config set project ai-nm26osl-1717
```

### Switching Between Configs

```bash
# List all configs
gcloud config configurations list

# Switch to gcplab
gcloud config configurations activate gcplab

# Switch back to default (e.g. deepinsight)
gcloud config configurations activate default
```

Example output of `gcloud config configurations list`:

```
NAME     IS_ACTIVE  ACCOUNT                 PROJECT
default  False      hamza@deepinsight.io    platform-dev-371409
gcplab   True       devstar17171@gcplab.me  ai-nm26osl-1717
```

## 2. API Endpoint Format

```
POST https://{REGION}-aiplatform.googleapis.com/v1/projects/{PROJECT_ID}/locations/{REGION}/publishers/anthropic/models/{MODEL}:rawPredict
```

| Parameter | Value |
|-----------|-------|
| **Region** | `us-east5` (check model card for available regions) |
| **Project ID** | `ai-nm26osl-1717` |
| **Publisher** | `anthropic` |
| **Model** | `claude-opus-4-6` |
| **Auth** | OAuth2 Bearer token via `gcloud auth print-access-token` |

## 3. Test with curl

```bash
curl -s \
  -X POST \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  "https://us-east5-aiplatform.googleapis.com/v1/projects/ai-nm26osl-1717/locations/us-east5/publishers/anthropic/models/claude-opus-4-6:rawPredict" \
  -d '{
    "anthropic_version": "vertex-2023-10-16",
    "max_tokens": 100,
    "messages": [{"role": "user", "content": "Say hi in one word"}]
  }'
```

### Successful Response

```json
{
  "model": "claude-opus-4-6",
  "id": "msg_vrtx_...",
  "type": "message",
  "role": "assistant",
  "content": [{"type": "text", "text": "Hi"}],
  "stop_reason": "end_turn",
  "usage": {
    "input_tokens": 12,
    "output_tokens": 4
  }
}
```

## 4. Key Differences: Gemini API vs Vertex AI

| | Gemini API | Vertex AI |
|--|-----------|-----------|
| **Endpoint** | `generativelanguage.googleapis.com` | `{region}-aiplatform.googleapis.com` |
| **Auth** | API key (`AIzaSy...`) | OAuth2 Bearer token (`gcloud auth print-access-token`) |
| **Models** | Google models only | Google + Anthropic + others |
| **Request format** | Google's `contents` format | Anthropic's native `messages` format |

## 5. Available Claude Models on Vertex AI

| Model | ID |
|-------|-----|
| Claude Opus 4.6 | `claude-opus-4-6` |
| Claude Sonnet 4.6 | `claude-sonnet-4-6` |
| Claude Haiku 4.5 | `claude-haiku-4-5` |

Check the [Model Garden](https://console.cloud.google.com/vertex-ai/publishers/anthropic) for the latest availability.

## 6. Request Body Reference

```json
{
  "anthropic_version": "vertex-2023-10-16",
  "max_tokens": 4096,
  "messages": [
    {"role": "user", "content": "Your prompt here"}
  ],
  "system": "Optional system prompt",
  "temperature": 0.7,
  "top_p": 0.9
}
```

### Required Fields

- `anthropic_version`: Always `"vertex-2023-10-16"`
- `max_tokens`: Maximum output tokens (up to 128,000 for Opus 4.6)
- `messages`: Array of `{role, content}` objects

### Claude Opus 4.6 Limits

- **Input**: 1M tokens
- **Output**: 128,000 tokens
- **Supported inputs**: Text, Image, PDF
