# CLAUDE.md

此文件为 Claude Code (claude.ai/code) 在此代码仓库中工作时提供指导。

## 语言
全过程使用中文沟通。

## 项目概述

基于 Google Gen AI Python SDK (`google-genai`) 和原生 JavaScript 前端实现的 Gemini Live API 演示应用。展示实时多模态流媒体功能，支持音频、视频和文本与 Gemini 进行交互。

参考文档：https://docs.cloud.google.com/vertex-ai/generative-ai/docs/live-api/start-manage-session?hl=zh-cn

## 环境说明

- **所有 Python 操作必须在 venv 环境中执行**
- 激活方式：`source venv/bin/activate`

## 运行应用

```bash
# 激活虚拟环境
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 启动服务器
python main.py
```

然后在浏览器中打开 http://localhost:8000。

## 认证方式

使用 Service Account 进行认证（已在代码中配置）：

```python
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.expanduser("~/workspace/tmp/key.json")
```

## 配置

项目 ID 已预配置为 `avatr-aispeech-voice`（在 `main.py` 中设置）。

可选环境变量：
- `LOCATION`（默认：`us-central1`）
- `MODEL`（默认：`gemini-live-2.5-flash-native-audio`）
- `PORT`（默认：`8000`）

## Architecture

### Backend (Python)

- **`main.py`**: FastAPI server. Hosts WebSocket endpoint at `/ws`, serves static frontend files, and manages the connection lifecycle between the frontend and Gemini Live.
- **`gemini_live.py`**: `GeminiLive` class wraps the `genai.Client` from `google-genai`. Manages the Live API session with three input queues (audio, video, text) and a receive loop that handles responses, transcriptions, tool calls, and audio output via callbacks.

The WebSocket protocol: raw audio bytes are sent directly, JSON messages with `{"type": "image", "data": "<base64>"}` for video, and plain text for text messages. Server sends raw audio bytes or JSON events (transcription, turn_complete, interrupted, tool_call).

### Frontend (Vanilla JS)

- **`main.js`**: Application logic, UI event handlers, and message routing.
- **`gemini-client.js`**: WebSocket client for backend communication.
- **`media-handler.js`**: Audio/Video capture using MediaDevices API and playback.
- **`pcm-processor.js`**: AudioWorklet for PCM audio processing.
- **`index.html`**: Single-page UI with controls for mic, camera, screen share, and text input.
- **`style.css`**: Styling for the interface.

## Key Implementation Details

- Audio is captured as PCM at 16kHz and sent as raw bytes over WebSocket
- Video is captured as JPEG base64-encoded frames
- The `GeminiLive` class uses `client.aio.live.connect()` for async bidirectional streaming
- Tool calls are handled via `tool_mapping` dictionary in `GeminiLive`

## 提交说明

本项目用于测试修改。如果需要提交（说"提交"），请把修改 merge 到 fork 项目：

```
/Users/z/workspace/claude/vertexai/generative-ai-fork/gemini/multimodal-live-api/native-audio-websocket-demo-apps/plain-js-python-sdk-demo-app
```

然后在 fork 项目中提交并 push 到 GitHub。

## Obsidian CLI 注意事项

使用 obsidian CLI 创建笔记时，`path` 参数是相对于 vault 根目录的路径，不要包含 vault 路径。

**正确用法**：
```bash
obsidian create path="daily/20260331" content="笔记内容"
```

这会在 vault 的 `daily/` 目录下创建 `20260331.md` 文件。

**错误用法**：
```bash
# 错误：包含完整路径会导致创建奇怪的目录结构
obsidian create path="/Users/z/workspace/memory/daily/20260331.md" ...
```
