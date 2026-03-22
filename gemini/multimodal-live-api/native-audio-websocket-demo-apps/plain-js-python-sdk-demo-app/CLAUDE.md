# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is a demo application for the Gemini Live API using the Google GenAI Python SDK backend with a vanilla JavaScript frontend. It demonstrates real-time multimodal interaction with audio, video, and text capabilities.

## Running the Application

**Always run within the venv environment:**

```bash
./venv/bin/python main.py
```

Then open http://localhost:8000 in your browser.

## Authentication

The app uses Google Cloud authentication via the `google-genai` SDK. Configure one of:
- `gcloud auth application-default login` (user credentials)
- `GOOGLE_APPLICATION_CREDENTIALS` environment variable pointing to a service account JSON key file

## Architecture

```
main.py              # FastAPI server with WebSocket /ws endpoint
gemini_live.py       # GeminiLive class wrapping the genai.Client for session management
frontend/
  index.html         # UI with connect/app-section/session-end-section states
  main.js            # Application logic, UI state machine
  gemini-client.js    # WebSocket client wrapper
  media-handler.js   # Audio/Video capture via MediaDevices API
  pcm-processor.js   # AudioWorklet for PCM audio processing
```

### Backend Flow
1. Browser connects via WebSocket to `/ws`
2. `main.py` creates `GeminiLive` client and three async queues (audio, video, text)
3. `gemini_live.py` manages the live session with the Gemini API, bidirectional audio/video/text streaming
4. Responses (transcriptions, audio data) are forwarded back via WebSocket as JSON or binary frames

### Frontend State Machine
- **auth-section** visible → Connect button → **app-section** visible
- WebSocket closes → **session-end-section** visible → Start New Session → **auth-section** visible

## Configuration

Environment variables (or defaults in `main.py`):
- `PROJECT_ID` - Google Cloud project (required)
- `LOCATION` - defaults to `us-central1`
- `MODEL` - defaults to `gemini-live-2.5-flash-native-audio`
- `PORT` - defaults to `8000`

When using service account authentication with a path like `~/...`, use `os.path.expanduser()` to expand the tilde, as raw strings do not expand `~`.

跟我说中文，不管是中间过程还是结果。
