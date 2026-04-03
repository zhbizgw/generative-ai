import asyncio
import base64
import json
import logging
import os
import re
import shlex
import subprocess
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google.genai import types
from gemini_live import GeminiLive
from plugins import load_plugins

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
# 内置工具定义
# ============================================

# skill_mapping 将在 load_plugins 后被设置
# 格式: {skill_name: Skill对象}
skill_mapping = {}


def get_skill_instruction(skill_name: str) -> str:
    """
    获取 Skill 的完整指令内容

    渐进式加载：当 AI 需要使用某个 Skill 时，先调用此工具获取完整指令，
    然后再决定如何执行操作。

    Args:
        skill_name: Skill 名称（如 "obsidian-cli", "defuddle" 等）

    Returns:
        Skill 的完整 SKILL.md 内容，包含所有使用指令和示例
    """
    if skill_name in skill_mapping:
        skill = skill_mapping[skill_name]
        return f"# {skill.name}\n\n{skill.get_instruction()}"
    else:
        return f"Skill '{skill_name}' not found. Available skills: {', '.join(skill_mapping.keys())}"


def cli_call(command: str, skill_name: str = None) -> str:
    """
    执行 CLI 命令

    通用 CLI 调用工具，用于处理所有 Agent Skills 的 CLI 操作。
    AI 根据 SKILL.md 指令调用此工具，传入完整的 CLI 命令字符串。

    Args:
        command: 完整的 CLI 命令字符串
        skill_name: 可选，Skill 名称（如 "obsidian-cli", "defuddle"）
                   用于确定命令前缀和特殊处理逻辑

    支持格式：
    - obsidian create name="Note" content="..."
    - defuddle https://example.com --md
    - npm run build
    等等
    """
    # 处理 daily 相关命令（保存到本地 liveapi/ 目录）
    if 'daily:append' in command or 'daily:read' in command:
        return _handle_daily_command(command)

    # 修复 obsidian create 命令中的 file 参数（AI 常用 file= 而非 name=）
    if skill_name in ('obsidian-cli', 'obsidian') or 'obsidian' in command:
        command = _fix_obsidian_create(command)

    # 执行命令
    try:
        result = subprocess.run(
            shlex.split(command),
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "PATH": os.environ.get("PATH", "")}
        )
        if result.returncode == 0:
            return result.stdout.strip() or "命令执行成功"
        else:
            return f"Error: {result.stderr.strip() or 'Command failed'}"
    except Exception as e:
        return f"Command execution failed: {str(e)}"


def _fix_obsidian_create(command: str) -> str:
    """修复 obsidian create 命令格式

    AI 经常发送：
    - obsidian create content="..." file="path/name.md"
    - obsidian create "content" file="path/name.md"

    正确格式：
    - obsidian create name="name" path="folder" content="..."
    """
    if 'create' not in command or 'obsidian' not in command:
        return command

    name = None
    path = None
    content = None

    # 提取 file= 参数
    file_match = re.search(r'file="([^"]+)"', command)
    if file_match:
        file_path = file_match.group(1)
        if '/' in file_path:
            path_parts = file_path.rsplit('/', 1)
            path = path_parts[0]
            name = path_parts[1].replace('.md', '')
        else:
            name = file_path.replace('.md', '')

    # 提取 content
    content_match = re.search(r'content="([^"]*)"', command)
    if content_match:
        content = content_match.group(1)

    if name:
        new_cmd = f'obsidian create name="{name}"'
        if path:
            new_cmd += f' path="{path}"'
        if content:
            new_cmd += f' content="{content}"'
        return new_cmd

    return command


def _handle_daily_command(command: str) -> str:
    """处理 daily 命令，保存到 liveapi/ 目录"""
    today = datetime.now().strftime("%Y%m%d")
    save_path = Path("/Users/z/workspace/memory/liveapi") / f"{today}.md"
    save_path.parent.mkdir(parents=True, exist_ok=True)

    if 'daily:append' in command:
        content_match = re.search(r'content="([^"]*)"', command)
        content = content_match.group(1) if content_match else ""

        if save_path.exists():
            with open(save_path, "a", encoding="utf-8") as f:
                f.write(f"\n{content}")
            return f'Appended to {save_path}'
        else:
            with open(save_path, "w", encoding="utf-8") as f:
                f.write(f"# {today}\n\n{content}")
            return f'Created {save_path}'

    elif 'daily:read' in command:
        if save_path.exists():
            with open(save_path, "r", encoding="utf-8") as f:
                return f.read()
        else:
            return "No daily notes yet"

    return command


# 内置工具映射
builtin_tool_mapping = {
    "get_skill_instruction": get_skill_instruction,
    "cli_call": cli_call,
}

# 内置工具配置
builtin_tools = [
    types.Tool(
        function_declarations=[
            {
                "name": "get_skill_instruction",
                "description": "获取 Skill 的完整指令内容。当需要使用某个 Skill 前，先调用此工具获取其完整使用说明（SKILL.md 内容），然后再决定如何执行操作。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill_name": {
                            "type": "string",
                            "description": "Skill 名称，如 'obsidian-cli'、'defuddle'、'json-canvas' 等"
                        }
                    },
                    "required": ["skill_name"],
                },
            },
            {
                "name": "cli_call",
                "description": "执行 CLI 命令。用于所有 Agent Skills 的 CLI 调用（如 obsidian、defuddle 等）。传入完整的命令字符串，如 'obsidian create name=\"Note\" content=\"...\"'、'defuddle https://example.com --md'。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "完整的 CLI 命令字符串，包含命令名和所有参数。如 'obsidian search query=\"关键词\"'、'defuddle https://example.com --md'、'npm run build'"
                        },
                        "skill_name": {
                            "type": "string",
                            "description": "可选，Skill 名称（如 'obsidian-cli'），用于确定命令前缀和特殊处理逻辑"
                        }
                    },
                    "required": ["command"],
                },
            },
        ]
    ),
    # Google 搜索工具 - 启用基于 Google 搜索结果的回答
    # {'google_search': {}},
]

# ============================================
# 插件加载（Python 函数 + Agent Skills）
# ============================================

# load_plugins() 返回:
# - py_tool_mapping: Python 函数名 -> 函数对象
# - py_tools: types.Tool 列表（Python 函数工具）
# - skill_mapping: dict {skill_name: Skill对象}
plugin_tool_mapping, plugin_tools, skill_mapping = load_plugins()

# 合并工具：插件工具 + 内置工具（后者优先级更高）
tool_mapping = {**plugin_tool_mapping, **builtin_tool_mapping}
tools = plugin_tools + builtin_tools

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
            skill_mapping=skill_mapping,
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
