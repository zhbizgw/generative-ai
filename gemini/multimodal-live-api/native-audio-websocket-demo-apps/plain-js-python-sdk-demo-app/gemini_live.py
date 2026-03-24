import asyncio
import inspect
from google import genai
from google.genai import types

class GeminiLive:
    """
    Handles the interaction with the Gemini Live API.
    """
    def __init__(self, project_id, location, model, input_sample_rate, tools=None, tool_mapping=None, session_handle=None):
        """
        Initializes the GeminiLive client.

        Args:
            project_id (str): The Google Cloud Project ID.
            location (str): The Google Cloud Location (e.g., "us-central1").
            model (str): The model name to use.
            input_sample_rate (int): The sample rate for audio input.
            tools (list, optional): List of tools to enable. Defaults to None.
            tool_mapping (dict, optional): Mapping of tool names to functions. Defaults to None.
            session_handle (str, optional): Session handle to resume a previous session. Defaults to None.
        """
        self.project_id = project_id
        self.location = location
        self.model = model
        self.input_sample_rate = input_sample_rate
        self.client = genai.Client(vertexai=True, project=project_id, location=location)
        self.tools = tools or []
        self.tool_mapping = tool_mapping or {}
        self.session_handle = session_handle

    async def start_session(self, audio_input_queue, video_input_queue, text_input_queue, audio_output_callback, audio_interrupt_callback=None, connection_closing=None):
        # Always set session_resumption config, even when handle is None
        # This is required to receive session_resumption_update messages
        session_resumption_config = types.SessionResumptionConfig(handle=self.session_handle)

        config = types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Puck"
                    )
                )
            ),
            system_instruction=types.Content(parts=[types.Part(text="You are a helpful AI assistant. Keep your responses concise. Speak in a friendly Irish accent.")]),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            proactivity=types.ProactivityConfig(proactive_audio=True),
            tools=self.tools,
            session_resumption=session_resumption_config,
        )

        async with self.client.aio.live.connect(model=self.model, config=config) as session:
            import logging
            logger = logging.getLogger(__name__)
            logger.info("Connected to Gemini Live session")

            async def send_audio():
                try:
                    while True:
                        chunk = await audio_input_queue.get()
                        await session.send_realtime_input(
                            audio=types.Blob(data=chunk, mime_type=f"audio/pcm;rate={self.input_sample_rate}")
                        )
                except asyncio.CancelledError:
                    pass

            async def send_video():
                try:
                    while True:
                        chunk = await video_input_queue.get()
                        await session.send_realtime_input(
                            video=types.Blob(data=chunk, mime_type="image/jpeg")
                        )
                except asyncio.CancelledError:
                    pass

            async def send_text():
                try:
                    while True:
                        text = await text_input_queue.get()
                        await session.send_client_content(
                            turns=types.Content(role='user', parts=[types.Part(text=text)]))
                except asyncio.CancelledError:
                    pass

            event_queue = asyncio.Queue()

            async def receive_loop():
                import logging
                logger = logging.getLogger(__name__)
                should_exit = False
                try:
                    while True:
                        # Check if connection is closing before waiting for response
                        if connection_closing and connection_closing.is_set():
                            break
                        async for response in session.receive():
                            server_content = response.server_content
                            tool_call = response.tool_call
                            session_resumption_update = response.session_resumption_update

                            # Handle session_resumption_update
                            if session_resumption_update:
                                if session_resumption_update.resumable and session_resumption_update.new_handle:
                                    await event_queue.put({"type": "session_resumption", "handle": session_resumption_update.new_handle})

                            # Handle goAway notification for session extension
                            go_away = response.go_away
                            if go_away:
                                await event_queue.put({"type": "goAway", "time_left": go_away.time_left})

                            if server_content:
                                if server_content.model_turn:
                                    for part in server_content.model_turn.parts:
                                        if part.inline_data:
                                            try:
                                                if inspect.iscoroutinefunction(audio_output_callback):
                                                    await audio_output_callback(part.inline_data.data)
                                                else:
                                                    audio_output_callback(part.inline_data.data)
                                            except Exception as e:
                                                logger.error(f"Error in audio_output_callback: {e}")
                                                should_exit = True
                                                break

                                if server_content.input_transcription and server_content.input_transcription.text:
                                    await event_queue.put({"type": "user", "text": server_content.input_transcription.text})

                                if server_content.output_transcription and server_content.output_transcription.text:
                                    await event_queue.put({"type": "gemini", "text": server_content.output_transcription.text})

                                if server_content.turn_complete:
                                    await event_queue.put({"type": "turn_complete"})

                                if server_content.interrupted:
                                    if audio_interrupt_callback:
                                        if inspect.iscoroutinefunction(audio_interrupt_callback):
                                            await audio_interrupt_callback()
                                        else:
                                            audio_interrupt_callback()
                                    await event_queue.put({"type": "interrupted"})

                            if tool_call:
                                function_responses = []
                                for fc in tool_call.function_calls:
                                    func_name = fc.name
                                    args = fc.args or {}

                                    if func_name in self.tool_mapping:
                                        try:
                                            tool_func = self.tool_mapping[func_name]
                                            if inspect.iscoroutinefunction(tool_func):
                                                result = await tool_func(**args)
                                            else:
                                                loop = asyncio.get_running_loop()
                                                result = await loop.run_in_executor(None, lambda: tool_func(**args))
                                        except Exception as e:
                                            result = f"Error: {e}"

                                        function_responses.append(types.FunctionResponse(
                                            name=func_name,
                                            id=fc.id,
                                            response={"result": result}
                                        ))
                                        await event_queue.put({"type": "tool_call", "name": func_name, "args": args, "result": result})

                                await session.send_tool_response(function_responses=function_responses)

                            if should_exit:
                                break

                except Exception as e:
                    logger.error(f"receive_loop exception: {e}")
                    await event_queue.put({"type": "error", "error": str(e)})
                finally:
                    await event_queue.put(None)

            send_audio_task = asyncio.create_task(send_audio())
            send_video_task = asyncio.create_task(send_video())
            send_text_task = asyncio.create_task(send_text())
            receive_task = asyncio.create_task(receive_loop())

            try:
                while True:
                    event = await event_queue.get()
                    if event is None:
                        break
                    if isinstance(event, dict) and event.get("type") == "error":
                        # Just yield the error event, don't raise to keep the stream alive if possible or let caller handle
                        yield event
                        break
                    yield event
            finally:
                send_audio_task.cancel()
                send_video_task.cancel()
                send_text_task.cancel()
                receive_task.cancel()
