import asyncio
import base64
import json
import logging
import os
from datetime import datetime

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google.genai import types
from gemini_live import GeminiLive

# service account
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.expanduser("~/workspace/tmp/key.json")

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
PROJECT_ID = os.getenv("PROJECT_ID", "avatr-aispeech-voice")
LOCATION = os.getenv("LOCATION", "us-central1")
MODEL = os.getenv("MODEL", "gemini-live-2.5-flash-native-audio")

# ============================================
# Simple Function Calling 示例
# ============================================

def get_current_time() -> str:
    """获取当前时间"""
    now = datetime.now()
    return now.strftime("%Y年%m月%d日 %H:%M:%S")

# Tool mapping: 函数名 -> 实际函数
tool_mapping = {
    "get_current_time": get_current_time,
}

# Tools 配置：定义 Gemini 可以调用的工具 schema
tools = [
    types.Tool(
        function_declarations=[
            {
                "name": "get_current_time",
                "description": "获取当前时间，返回格式化的日期时间字符串",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        ]
    ),
    # Google 搜索工具 - 启用基于 Google 搜索结果的回答
    {'google_search': {}},
]

# Initialize FastAPI
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files
app.mount("/static", StaticFiles(directory="frontend"), name="static")


@app.get("/")
async def root():
    return FileResponse("frontend/index.html")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for Gemini Live."""
    await websocket.accept()

    logger.info("WebSocket connection accepted")

    audio_input_queue = asyncio.Queue()
    video_input_queue = asyncio.Queue()
    text_input_queue = asyncio.Queue()

    # Session resumption handle from client
    current_resume_handle = None
    resume_handle_event = asyncio.Event()

    async def audio_output_callback(data):
        await websocket.send_bytes(data)

    async def audio_interrupt_callback():
        # The event queue handles the JSON message, but we might want to do something else here
        pass

    # Shared event to signal connection is closing
    connection_closing = asyncio.Event()

    async def receive_from_client():
        nonlocal current_resume_handle
        try:
            while True:
                message = await websocket.receive()

                if message.get("bytes"):
                    await audio_input_queue.put(message["bytes"])
                elif message.get("text"):
                    text = message["text"]
                    try:
                        payload = json.loads(text)
                        inner_payload = None

                        # Handle double-encoded JSON from frontend sendText()
                        # sendText() wraps text in {text: ...}, so if type is missing,
                        # the actual data is in payload["text"]
                        if isinstance(payload, dict) and payload.get("type") is None and payload.get("text"):
                            try:
                                inner_payload = json.loads(payload["text"])
                            except json.JSONDecodeError:
                                pass

                        if isinstance(inner_payload, dict) and inner_payload.get("type"):
                            if inner_payload.get("type") == "image":
                                image_data = base64.b64decode(inner_payload["data"])
                                await video_input_queue.put(image_data)
                                continue
                            elif inner_payload.get("type") == "resume_session":
                                current_resume_handle = inner_payload.get("handle")
                                logger.info(f"Received resume_session request with handle: {current_resume_handle[:30] if current_resume_handle else None}...")
                                resume_handle_event.set()
                                continue

                        # Regular text message (possibly wrapped by sendText)
                        if isinstance(payload, dict) and not payload.get("type"):
                            # Wrapped text: {"text": "hello"}
                            await text_input_queue.put(payload.get("text", ""))
                        elif isinstance(payload, str):
                            # Plain text without wrapper
                            await text_input_queue.put(payload)
                        else:
                            await text_input_queue.put(text)
                    except json.JSONDecodeError:
                        await text_input_queue.put(text)
        except WebSocketDisconnect:
            logger.info("WebSocket disconnected from client")
            connection_closing.set()
        except Exception as e:
            logger.error(f"Error receiving from client: {e}")
            connection_closing.set()

    receive_task = asyncio.create_task(receive_from_client())

    async def run_session():
        logger.info("run_session started")
        # Wait briefly for resume_session message to arrive (if any)
        try:
            await asyncio.wait_for(resume_handle_event.wait(), timeout=2.0)
            logger.info(f"Resume handle received: {current_resume_handle[:30] if current_resume_handle else None}...")
        except asyncio.TimeoutError:
            logger.info("No resume_session received within 2s, starting fresh session")

        logger.info("Creating GeminiLive client")
        gemini_client = GeminiLive(
            project_id=PROJECT_ID, location=LOCATION, model=MODEL, input_sample_rate=16000,
            session_handle=current_resume_handle,
            tools=tools,
            tool_mapping=tool_mapping,
        )
        logger.info("Calling start_session")
        try:
            async for event in gemini_client.start_session(
                audio_input_queue=audio_input_queue,
                video_input_queue=video_input_queue,
                text_input_queue=text_input_queue,
                audio_output_callback=audio_output_callback,
                audio_interrupt_callback=audio_interrupt_callback,
                connection_closing=connection_closing,
            ):
                if event:
                    await websocket.send_json(event)
            logger.info("start_session iteration completed")
        except Exception as e:
            logger.error(f"Exception in start_session iteration: {e}")
            raise

    session_task = asyncio.create_task(run_session())

    try:
        await session_task
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"Error in Gemini session: {e}")
    finally:
        # Signal connection is closing to stop receive_loop
        connection_closing.set()

        # Cancel tasks
        session_task.cancel()
        receive_task.cancel()

        # Wait for tasks to finish with timeout
        try:
            await asyncio.wait_for(
                asyncio.gather(session_task, receive_task, return_exceptions=True),
                timeout=2.0
            )
        except asyncio.TimeoutError:
            logger.warning("Task cleanup timed out")

        # Small delay to ensure WebSocket is not being used
        await asyncio.sleep(0.1)

        try:
            await websocket.close()
        except Exception as e:
            logger.warning(f"WebSocket close error (may already be closed): {e}")


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="localhost", port=port)
