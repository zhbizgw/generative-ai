"""
GeminiLive - Gemini Live API 交互封装类

本模块封装了 Google Gemini Live API 的核心交互逻辑，支持：
- 实时音频流双向传输
- 视频帧流传输
- 文本消息发送
- 工具调用（Function Calling）
- 会话恢复（Session Resumption）

主要架构：
- 使用 asyncio 实现异步并发处理
- 三个独立的发送任务（音频/视频/文本）并行运行
- 一个接收循环处理服务端所有响应
- 通过 asyncio.Queue 在任务间传递数据
"""

import asyncio
import inspect
from google import genai
from google.genai import types
from websockets.exceptions import ConnectionClosed


class GeminiLive:
    """
    Gemini Live API 客户端封装类

    负责管理与 Gemini Live API 的完整会话生命周期，包括：
    - 建立 WebSocket 连接
    - 并行处理音频/视频/文本输入
    - 处理服务端返回的音频响应、文本转录、工具调用等
    - 支持会话恢复以处理连接中断

    Attributes:
        project_id (str): Google Cloud 项目 ID，用于认证和计费
        location (str): Google Cloud 区域（如 "us-central1"）
        model (str):  Gemini 模型名称（如 "gemini-live-2.5-flash-native-audio"）
        input_sample_rate (int): 输入音频的采样率（通常为 16000 Hz）
        tools (list): 启用的工具列表，用于 Function Calling
        tool_mapping (dict): 工具名称到实际函数对象的映射
        session_handle (str): 会话恢复句柄，用于中断后恢复对话
        client: Google GenAI 客户端实例
    """

    def __init__(self, project_id, location, model, input_sample_rate, tools=None, tool_mapping=None, session_handle=None, skill_mapping=None):
        """
        初始化 GeminiLive 客户端

        Args:
            project_id (str): Google Cloud 项目 ID
            location (str): Google Cloud 区域
            model (str): 模型名称
            input_sample_rate (int): 音频采样率（Hz）
            tools (list, optional): 工具配置列表，传递给 Gemini 的 tools 参数
            tool_mapping (dict, optional): 工具名 -> 函数的映射，用于处理工具调用
            session_handle (str, optional): 会话恢复句柄，用于在断开后恢复相同会话
            skill_mapping (dict, optional): skill_name -> Skill对象，用于按需获取指令
        """
        # 存储基本配置参数
        self.project_id = project_id
        self.location = location
        self.model = model
        self.input_sample_rate = input_sample_rate

        # 创建 Google GenAI 客户端
        # vertexai=True 表示使用 Vertex AI 平台（而非直接的 Gemini API）
        self.client = genai.Client(vertexai=True, project=project_id, location=location)

        # 工具配置：支持 Function Calling
        # tools: 传给 Gemini 的工具定义列表
        # tool_mapping: 本地函数映射，当 Gemini 调用工具时执行对应函数
        self.tools = tools or []
        self.tool_mapping = tool_mapping or {}

        # Skill 映射：skill_name -> Skill对象
        # 用于渐进式加载：AI 需要时通过 get_skill_instruction 获取完整指令
        self.skill_mapping = skill_mapping or {}

        # 会话恢复句柄
        # 如果提供了句柄，将尝试恢复之前的会话状态
        self.session_handle = session_handle

    async def start_session(
        self,
        audio_input_queue,
        video_input_queue,
        text_input_queue,
        audio_output_callback,
        audio_interrupt_callback=None,
        connection_closing=None
    ):
        """
        启动 Gemini Live 会话

        这是核心方法，创建一个完整的双向通信会话：
        1. 建立与 Gemini Live API 的 WebSocket 连接
        2. 启动三个并行发送任务（音频/视频/文本）
        3. 启动接收循环处理服务端响应
        4. 通过生成器（generator）yield 所有事件给调用者

        Args:
            audio_input_queue (asyncio.Queue): 音频数据队列，元素为原始 PCM 字节数据
            video_input_queue (asyncio.Queue): 视频帧队列，元素为 JPEG 格式的字节数据
            text_input_queue (asyncio.Queue): 文本消息队列，元素为字符串
            audio_output_callback (callable): 音频输出回调，接收字节数据用于播放
            audio_interrupt_callback (callable, optional): 中断回调，当用户打断时调用
            connection_closing (asyncio.Event, optional): 连接关闭事件，用于优雅退出

        Yields:
            dict: 事件字典，包含以下类型：
                - {"type": "user", "text": str}: 用户输入的语音转文本
                - {"type": "gemini", "text": str}: Gemini 输出的语音转文本
                - {"type": "turn_complete"}: 一轮对话完成
                - {"type": "interrupted"}: 对话被用户打断
                - {"type": "tool_call", "name": str, "args": dict, "result": any}: 工具调用事件
                - {"type": "session_resumption", "handle": str}: 会话恢复句柄更新
                - {"type": "goAway", "time_left": int}: 服务器即将断开的警告
                - {"type": "error", "error": str}: 错误事件
        """
        # ===================================================================
        # 第一步：配置会话参数
        # ===================================================================

        # 会话恢复配置
        # 即使不恢复会话（handle=None），也必须设置此配置
        # 这样才能收到 session_resumption_update 消息
        session_resumption_config = types.SessionResumptionConfig(handle=self.session_handle)

        # 构建系统指令：基础指令 + 可用 Skills 列表（渐进式）
        base_instruction = """你是一个友好的 AI 助手。

## 可用工具

### 1. get_skill_instruction - 获取 Skill 完整指令
当需要使用某个 Skill 前，**先调用此工具获取其完整使用说明**（SKILL.md 内容）。
然后根据完整指令决定如何执行操作。

### 2. cli_call - 执行 CLI 命令
执行 Skill 指令中描述的 CLI 命令。
- `obsidian search query="关键词"` → `cli_call("obsidian search query=关键词")`
- `defuddle https://example.com --md` → `cli_call("defuddle https://example.com --md")`

### 3. Google 搜索
用于回答实时信息和公开知识问题。请简要说明信息来源。

## 可用 Skills（渐进式加载）

当需要某个 Skill 时，先调用 get_skill_instruction 获取完整指令："""

        # 动态注入可用 Skills 列表（简短描述）
        if self.skill_mapping:
            skill_list = []
            for name, skill in self.skill_mapping.items():
                desc = skill.description[:100] + "..." if len(skill.description) > 100 else skill.description
                skill_list.append(f"- **{name}**: {desc}")
            skill_section = "\n".join(skill_list)
            full_instruction = f"{base_instruction}\n\n{skill_section}\n\n请先使用 get_skill_instruction 获取 Skill 完整指令。"
        else:
            full_instruction = base_instruction

        # 构建 LiveConnectConfig，定义会话的所有行为
        config = types.LiveConnectConfig(
            # 响应模态：只接收音频（也可以包含 TEXT，但会增加延迟）
            response_modalities=[types.Modality.AUDIO],

            # 语音配置：选择预建的语音角色
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Leda"  # 语音名称，可选：Leda, Puck, Orus 等
                    )
                )
            ),

            # 系统指令：使用动态构建的 full_instruction
            system_instruction=types.Content(
                parts=[types.Part(text=full_instruction)]
            ),

            # 输入音频转录配置：将用户语音转为文字
            input_audio_transcription=types.AudioTranscriptionConfig(),

            # 输出音频转录配置：将 AI 语音转为文字（便于调试和显示）
            output_audio_transcription=types.AudioTranscriptionConfig(),

            # 主动音频配置：允许 AI 主动开始说话
            proactivity=types.ProactivityConfig(proactive_audio=True),

            # 工具配置：启用 Function Calling
            tools=self.tools,

            # 会话恢复配置
            session_resumption=session_resumption_config,
        )

        # ===================================================================
        # 第二步：建立 WebSocket 连接
        # ===================================================================

        # client.aio.live.connect() 建立与 Gemini Live API 的双向 WebSocket 连接
        # context manager 确保连接正确关闭
        async with self.client.aio.live.connect(model=self.model, config=config) as session:
            import logging
            logger = logging.getLogger(__name__)
            logger.info("已连接 Gemini Live 会话")

            # ===================================================================
            # 第三步：定义发送函数（并行执行）
            # ===================================================================

            async def send_audio():
                """
                音频发送循环

                从 audio_input_queue 不断获取 PCM 音频数据
                通过 session.send_realtime_input() 发送给 Gemini

                音频格式：原始 PCM 字节，mime_type 包含采样率信息
                例如：audio/pcm;rate=16000
                """
                import time
                last_send_time = time.time()

                try:
                    while True:
                        # 阻塞等待队列中的音频数据
                        chunk = await audio_input_queue.get()

                        # 使用 send_realtime_input 发送实时音频输入
                        # Blob 包含原始音频字节和 MIME 类型
                        await session.send_realtime_input(
                            audio=types.Blob(
                                data=chunk,
                                mime_type=f"audio/pcm;rate={self.input_sample_rate}"
                            )
                        )

                        # 每 5 秒打印一次日志，用于监控连接状态
                        now = time.time()
                        if now - last_send_time > 5:
                            logger.info(f"音频发送间隔: {now - last_send_time:.1f}秒")
                        last_send_time = now

                except asyncio.CancelledError:
                    # 任务被取消时优雅退出
                    pass

            async def send_video():
                """
                视频发送循环

                从 video_input_queue 获取视频帧（JPEG 格式）
                发送给 Gemini 用于实时视觉理解

                注意：视频帧应该以合理频率发送（如 1-5 FPS）
                过高帧率会增加带宽消耗
                """
                try:
                    while True:
                        # 等待视频帧数据
                        chunk = await video_input_queue.get()

                        # 发送视频帧，类型为 JPEG 图像
                        await session.send_realtime_input(
                            video=types.Blob(
                                data=chunk,
                                mime_type="image/jpeg"
                            )
                        )
                except asyncio.CancelledError:
                    pass

            async def send_text():
                """
                文本消息发送循环

                从 text_input_queue 获取文本消息
                使用 send_client_content 发送结构化文本内容

                与音频不同，文本消息使用 Content/Part 结构
                """
                try:
                    while True:
                        text = await text_input_queue.get()

                        # send_client_content 用于发送结构化内容（文本/数据）
                        # 而不是实时媒体（音频/视频）
                        await session.send_client_content(
                            turns=types.Content(
                                role='user',
                                parts=[types.Part(text=text)]
                            )
                        )
                except asyncio.CancelledError:
                    pass

            # ===================================================================
            # 第四步：定义事件队列和接收循环
            # ===================================================================

            # 事件队列：用于在不同任务间传递事件
            event_queue = asyncio.Queue()

            async def receive_loop():
                """
                接收循环 - 处理所有服务端响应

                这是最重要的函数，负责处理 Gemini 返回的所有消息：
                1. server_content: 服务器内容（音频、转录、打断等）
                2. tool_call: 工具调用请求
                3. session_resumption_update: 会话恢复信息
                4. go_away: 连接即将断开的警告

                异常处理：
                - ConnectionClosed: WebSocket 意外关闭
                - 其他异常：记录并通过队列传递错误
                """
                import logging
                logger = logging.getLogger(__name__)
                should_exit = False

                try:
                    while True:
                        # 检查是否正在关闭连接
                        if connection_closing and connection_closing.is_set():
                            break

                        try:
                            # session.receive() 是一个异步迭代器
                            # 持续接收服务端的所有消息
                            async for response in session.receive():
                                # 提取响应中的各个字段
                                server_content = response.server_content
                                tool_call = response.tool_call
                                session_resumption_update = response.session_resumption_update

                                # 调试：检查 grounding 元数据（用于搜索增强）
                                if hasattr(response, 'grounding_metadata') and response.grounding_metadata:
                                    logger.info(f"grounding_metadata: {response.grounding_metadata}")

                                # ------------------------------------------------------
                                # 处理会话恢复更新
                                # ------------------------------------------------------
                                if session_resumption_update:
                                    # 当连接中断后，服务器会发送新的会话句柄
                                    # 可以用它来恢复对话
                                    if (session_resumption_update.resumable and
                                        session_resumption_update.new_handle):
                                        await event_queue.put({
                                            "type": "session_resumption",
                                            "handle": session_resumption_update.new_handle
                                        })

                                # ------------------------------------------------------
                                # 处理连接即将断开的警告
                                # ------------------------------------------------------
                                go_away = response.go_away
                                if go_away:
                                    # 服务器通知即将断开，time_left 表示剩余时间（秒）
                                    await event_queue.put({
                                        "type": "goAway",
                                        "time_left": go_away.time_left
                                    })

                                # ------------------------------------------------------
                                # 处理服务端内容
                                # ------------------------------------------------------
                                if server_content:
                                    # model_turn: Gemini 的回复回合
                                    if server_content.model_turn:
                                        for part in server_content.model_turn.parts:
                                            # inline_data: 音频输出数据
                                            if part.inline_data:
                                                try:
                                                    # 调用音频输出回调播放音频
                                                    if inspect.iscoroutinefunction(audio_output_callback):
                                                        await audio_output_callback(part.inline_data.data)
                                                    else:
                                                        audio_output_callback(part.inline_data.data)
                                                except Exception as e:
                                                    logger.error(f"audio_output_callback 执行错误: {e}")
                                                    should_exit = True
                                                    break

                                    # input_transcription: 用户输入的语音转文字
                                    if (server_content.input_transcription and
                                        server_content.input_transcription.text):
                                        await event_queue.put({
                                            "type": "user",
                                            "text": server_content.input_transcription.text
                                        })

                                    # output_transcription: AI 输出的语音转文字
                                    if (server_content.output_transcription and
                                        server_content.output_transcription.text):
                                        await event_queue.put({
                                            "type": "gemini",
                                            "text": server_content.output_transcription.text
                                        })

                                    # turn_complete: 一轮对话完成
                                    # 表示 Gemini 已经说完，可以发送下一条消息
                                    if server_content.turn_complete:
                                        await event_queue.put({"type": "turn_complete"})

                                    # interrupted: 对话被用户打断
                                    # 通常发生在用户开始说话时
                                    if server_content.interrupted:
                                        if audio_interrupt_callback:
                                            if inspect.iscoroutinefunction(audio_interrupt_callback):
                                                await audio_interrupt_callback()
                                            else:
                                                audio_interrupt_callback()
                                        await event_queue.put({"type": "interrupted"})

                                # ------------------------------------------------------
                                # 处理工具调用
                                # ------------------------------------------------------
                                if tool_call:
                                    logger.info(
                                        f"收到 tool_call: name={tool_call.function_calls[0].name if tool_call.function_calls else 'unknown'}"
                                    )

                                    function_responses = []

                                    # 遍历所有函数调用
                                    for fc in tool_call.function_calls:
                                        func_name = fc.name  # 函数名称
                                        args = fc.args or {}  # 函数参数

                                        if func_name in self.tool_mapping:
                                            # 本地定义的工具：执行并返回结果
                                            try:
                                                tool_func = self.tool_mapping[func_name]

                                                # 检查是否是 Skill 对象（Agent Skills）
                                                if hasattr(tool_func, 'call'):
                                                    result = tool_func.call(**args)
                                                elif inspect.iscoroutinefunction(tool_func):
                                                    result = await tool_func(**args)
                                                else:
                                                    # 同步函数需要在线程池中执行
                                                    loop = asyncio.get_running_loop()
                                                    result = await loop.run_in_executor(
                                                        None,
                                                        lambda f=tool_func: f(**args)
                                                    )
                                            except Exception as e:
                                                result = f"Error: {e}"

                                            # 构建函数响应
                                            function_responses.append(types.FunctionResponse(
                                                name=func_name,
                                                id=fc.id,
                                                response={"result": result}
                                            ))

                                            # 通过队列通知调用者
                                            await event_queue.put({
                                                "type": "tool_call",
                                                "name": func_name,
                                                "args": args,
                                                "result": result
                                            })
                                        else:
                                            # 内置工具（如 google_search）不在 tool_mapping 中
                                            # 这些由 Google 自动处理，我们发送空响应
                                            function_responses.append(types.FunctionResponse(
                                                name=func_name,
                                                id=fc.id,
                                                response={"result": None}
                                            ))
                                            await event_queue.put({
                                                "type": "tool_call",
                                                "name": func_name,
                                                "args": args,
                                                "result": "[内置工具，由 Google 自动处理]"
                                            })

                                    # 发送工具响应给 Gemini
                                    # 这样 Gemini 才能知道工具执行结果并继续对话
                                    await session.send_tool_response(
                                        function_responses=function_responses
                                    )

                                if should_exit:
                                    break

                        except ConnectionClosed:
                            # WebSocket 连接意外关闭
                            logger.warning("连接意外关闭，退出接收循环")
                            break

                except Exception as e:
                    # 捕获所有未预期的异常
                    logger.error(f"receive_loop 异常: {e}", exc_info=True)
                    await event_queue.put({"type": "error", "error": str(e)})
                finally:
                    # 发送 None 表示接收循环结束
                    await event_queue.put(None)

            # ===================================================================
            # 第五步：启动所有任务
            # ===================================================================

            # 创建并行任务
            # asyncio.create_task() 立即开始执行函数
            send_audio_task = asyncio.create_task(send_audio())
            send_video_task = asyncio.create_task(send_video())
            send_text_task = asyncio.create_task(send_text())
            receive_task = asyncio.create_task(receive_loop())

            # ===================================================================
            # 第六步：主循环 - yield 事件给调用者
            # ===================================================================

            try:
                while True:
                    # 从事件队列获取事件
                    event = await event_queue.get()

                    # None 表示接收循环已结束
                    if event is None:
                        break

                    # 错误事件：直接 yield 并退出
                    if isinstance(event, dict) and event.get("type") == "error":
                        yield event
                        break

                    # 普通事件：yield 给调用者
                    yield event

            finally:
                # ===================================================================
                # 第七步：清理 - 取消所有任务
                # ===================================================================

                # 取消所有后台任务
                send_audio_task.cancel()
                send_video_task.cancel()
                send_text_task.cancel()
                receive_task.cancel()

                # 注意：这里没有 await task取消，因为它们可能在等待 I/O
                # 实际取消等待在 asyncio.gather() 中处理
