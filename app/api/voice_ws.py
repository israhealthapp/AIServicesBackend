"""
WebSocket endpoint for real-time voice chat.

Flow per connection:
  1. Client sends  {"type": "language_select", "language": "ur"|"en"}
  2. Server starts Deepgram Nova-3 live connection for the chosen language
  3. Client streams raw WebM/opus binary chunks → forwarded to Deepgram
  4. Server sends {"type": "partial", "text": "..."} as Deepgram transcribes
  5. When a partial is >15 chars, a background Gemini task is launched silently
  6. Client sends {"type": "stop"} → final transcript assembled
  7. Server awaits/reuses the background Gemini task, sends
     {"type": "final_response", "text": "..."}
  8. Transcript + response committed to in-memory chat_history for the session
"""

import asyncio
import json
import re
import threading
import subprocess
import tempfile
import os
from typing import Optional


def _contains_devanagari(text: str) -> bool:
    """Detect Hindi transcribed in Devanagari script — user most likely intended Urdu."""
    return bool(re.search(r'[ऀ-ॿ]', text))

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Depends, UploadFile, File

from app.core.config import get_settings
from app.core.logging import logger
from app.core.system_prompt import SYSTEM_PROMPT
from app.core.auth import _get_supabase
from app.db import history as history_db


async def validate_websocket_token(websocket: WebSocket) -> dict:
    """Extract and validate token from WebSocket query params."""
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4001, reason="Missing token")
        raise HTTPException(status_code=401, detail="Missing token")

    try:
        supabase = _get_supabase()
        response = supabase.auth.get_user(token)
        if not response.user:
            await websocket.close(code=4001, reason="Invalid token")
            raise HTTPException(status_code=401, detail="Invalid token")
        return response.user
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[WS] Token validation error: {e}")
        await websocket.close(code=4001, reason="Token verification failed")
        raise HTTPException(status_code=401, detail="Token verification failed")

# Deepgram imports (loaded dynamically to avoid missing key errors)
# from deepgram import DeepgramClient, PrerecordedOptions, LiveTranscriptionEvents, LiveOptions

router = APIRouter(tags=["voice"])


@router.websocket("/ws/voice-command")
async def voice_command_ws(websocket: WebSocket):
    """
    WebSocket endpoint for voice commands (intent parsing).
    Streams Deepgram transcription + parses intent via Gemini.

    Flow:
      1. Client sends {"type": "language_select", "language": "ur"|"en"}
      2. Server starts Deepgram Nova-3 live connection
      3. Client streams binary audio chunks
      4. Server sends {"type": "partial", "text": "..."} in real-time
      5. Client sends {"type": "stop"} when done
      6. Server parses intent with Gemini
      7. Server sends {"type": "final_command", "text": "...", "action": "...", "params": {...}}
    """
    await websocket.accept()
    settings = get_settings()
    print("[WS-CMD] Client connected - DIRECT PRINT", flush=True)
    logger.info("[WS-CMD] Client connected")

    # Validate token from query params
    try:
        user = await validate_websocket_token(websocket)
        logger.info(f"[WS-CMD] Token validated for user: {user.id}")
    except HTTPException:
        return

    if not settings.DEEPGRAM_API_KEY:
        logger.error("[WS-CMD] DEEPGRAM_API_KEY not configured")
        await websocket.send_json({"type": "error", "message": "DEEPGRAM_API_KEY not configured"})
        await websocket.close()
        return

    loop = asyncio.get_event_loop()
    result_q: asyncio.Queue = asyncio.Queue()

    try:
        from deepgram import DeepgramClient, LiveTranscriptionEvents, LiveOptions
    except ImportError:
        try:
            from deepgram.listen import LiveTranscriptionEvents, LiveOptions
            from deepgram import DeepgramClient
        except ImportError:
            logger.error("[WS-CMD] deepgram-sdk not installed")
            await websocket.send_json({"type": "error", "message": "Deepgram SDK unavailable"})
            await websocket.close()
            return

    # Auto-detect language with Deepgram's 'multi' mode
    selected_language = "multi"
    logger.info("[WS-CMD] Starting with auto-detect language mode (multi)")
    await websocket.send_json(
        {"type": "language_selected", "language": selected_language}
    )

    # Start Deepgram live connection
    dg = DeepgramClient(settings.DEEPGRAM_API_KEY)
    dg_conn = dg.listen.websocket.v("1")

    completed_utterances: list[str] = []
    latest_transcript: str = ""
    final_sent: bool = False

    def on_transcript(self, result, **kwargs):
        nonlocal latest_transcript
        alt = result.channel.alternatives[0]
        transcript = alt.transcript.strip()

        logger.info(
            f"[Deepgram-CMD] is_final={result.is_final} text='{transcript[:60]}'"
        )

        if not transcript:
            return

        if result.is_final and transcript not in completed_utterances:
            logger.info(f"[Deepgram-CMD] Completed: '{transcript[:60]}'")
            completed_utterances.append(transcript)

        latest_transcript = transcript
        asyncio.run_coroutine_threadsafe(
            result_q.put({"type": "partial", "text": transcript}), loop
        )

    def on_error(self, error, **kwargs):
        logger.error(f"[Deepgram-CMD] Error: {error}")
        asyncio.run_coroutine_threadsafe(
            result_q.put({"type": "error", "message": str(error)}), loop
        )

    dg_conn.on(LiveTranscriptionEvents.Transcript, on_transcript)
    dg_conn.on(LiveTranscriptionEvents.Error, on_error)

    started = dg_conn.start(
        LiveOptions(
            model="nova-3",
            language=selected_language,
            encoding="linear16",
            sample_rate=16000,
            channels=1,
            smart_format=True,
            interim_results=True,
            endpointing=False,
            punctuate=True,
        )
    )

    if not started:
        logger.error("[Deepgram-CMD] Failed to start live connection")
        await websocket.send_json({"type": "error", "message": "Deepgram failed to connect"})
        await websocket.close()
        return

    logger.info("[Deepgram-CMD] Live connection started")

    async def process_results():
        while True:
            msg = await result_q.get()
            if msg is None:
                break
            await websocket.send_json(msg)

            if msg["type"] == "final":
                break

    results_task = asyncio.create_task(process_results())

    try:
        while True:
            data = await websocket.receive()

            if data.get("type") == "websocket.disconnect":
                break

            if "bytes" in data and data["bytes"]:
                audio_chunk = data["bytes"]
                # Native module sends pure PCM (16kHz, mono, 16-bit)
                # Send directly to Deepgram - no conversion needed
                dg_conn.send(audio_chunk)
                logger.debug(f"[Audio-CMD] Relayed PCM chunk: {len(audio_chunk)} bytes")

            elif "text" in data and data["text"]:
                msg = json.loads(data["text"])

                if msg.get("type") == "stop":
                    logger.info("[WS-CMD] Stop signal received")
                    dg_conn.finish()

                    final_text = ""
                    if completed_utterances:
                        final_text = " ".join(completed_utterances)
                        if latest_transcript and latest_transcript not in final_text:
                            final_text += " " + latest_transcript
                    elif latest_transcript:
                        final_text = latest_transcript

                    if final_text.strip():
                        logger.info(f"[WS-CMD] Final text: '{final_text[:80]}'")

                        # Parse intent with Gemini
                        _action = "unknown"
                        _params: dict = {}
                        try:
                            from app.services.intent_service import intent_service
                            intent_result = intent_service.parse(final_text.strip(), selected_language)
                            logger.info(f"[WS-CMD] Intent parsed: action={intent_result.get('action')}, params={intent_result.get('params')}")
                            _action = intent_result.get("action", "unknown")
                            _params = intent_result.get("params", {})

                            # Don't send transcribed text if it's Hindi (Devanagari script)
                            display_text = "" if _contains_devanagari(final_text) else final_text.strip()

                            await websocket.send_json({
                                "type": "final_command",
                                "text": display_text,
                                "language": selected_language,
                                "action": _action,
                                "params": _params,
                            })
                        except Exception as e:
                            error_msg = str(e).lower()
                            logger.error(f"[WS-CMD] Intent parsing error: {e}", exc_info=True)
                            # Handle API quota/model errors with generic message
                            if any(x in error_msg for x in ['quota', 'rate limit', 'unavailable', 'overloaded']):
                                await websocket.send_json({
                                    "type": "error",
                                    "message": "Model not available. Please try again later.",
                                })
                            else:
                                await websocket.send_json({
                                    "type": "final_command",
                                    "text": "",
                                    "language": selected_language,
                                    "action": "unknown",
                                    "params": {},
                                })

                        # Persist to DB (fire-and-forget)
                        import json as _json
                        _sess = history_db.create_session(
                            user.id, "voice_command",
                            language=selected_language if selected_language != "multi" else None,
                            title=final_text.strip()[:80],
                        )
                        if _sess:
                            _umid = history_db.save_message(_sess, user.id, "user", final_text.strip(), sequence_number=1)
                            if _umid:
                                history_db.save_voice_metadata(_umid, "deepgram", detected_language=selected_language if selected_language != "multi" else None)
                            _amid = history_db.save_message(_sess, user.id, "assistant", _json.dumps({"action": _action, "params": _params}), sequence_number=2)
                            if _amid:
                                history_db.save_intent_result(_amid, user.id, _action, _params, source="voice_ws")
                            history_db.close_session(_sess)

                        final_sent = True
                    else:
                        logger.info("[WS-CMD] Stop but no transcript")

                    completed_utterances.clear()
                    break

    except WebSocketDisconnect:
        logger.info("[WS-CMD] Client disconnected")

    finally:
        logger.info("[Deepgram-CMD] Closing…")
        dg_conn.finish()
        await asyncio.sleep(0.5)

        # Flush final transcript if not already sent
        if not final_sent:
            final_text = ""
            if completed_utterances:
                final_text = " ".join(completed_utterances)
                if latest_transcript and latest_transcript not in final_text:
                    final_text += " " + latest_transcript
            elif latest_transcript:
                final_text = latest_transcript

            if final_text.strip():
                logger.info(f"[Deepgram-CMD] Flushing: '{final_text[:80]}'")
                _action = "unknown"
                _params: dict = {}
                try:
                    from app.services.intent_service import intent_service
                    intent_result = intent_service.parse(final_text.strip(), selected_language)
                    _action = intent_result.get("action", "unknown")
                    _params = intent_result.get("params", {})

                    # Don't send transcribed text if it's Hindi (Devanagari script)
                    display_text = "" if _contains_devanagari(final_text) else final_text.strip()

                    await websocket.send_json({
                        "type": "final_command",
                        "text": display_text,
                        "language": selected_language,
                        "action": _action,
                        "params": _params,
                    })
                except Exception as e:
                    error_msg = str(e).lower()
                    logger.error(f"[WS-CMD] Intent parsing error on flush: {e}", exc_info=True)
                    # Handle API quota/model errors with generic message
                    if any(x in error_msg for x in ['quota', 'rate limit', 'unavailable', 'overloaded']):
                        await websocket.send_json({
                            "type": "error",
                            "message": "Model not available. Please try again later.",
                        })
                    else:
                        await websocket.send_json({
                            "type": "final_command",
                            "text": "",
                            "language": selected_language,
                            "action": "unknown",
                            "params": {},
                        })

                import json as _json
                _sess = history_db.create_session(
                    user.id, "voice_command",
                    language=selected_language if selected_language != "multi" else None,
                    title=final_text.strip()[:80],
                )
                if _sess:
                    _umid = history_db.save_message(_sess, user.id, "user", final_text.strip(), sequence_number=1)
                    if _umid:
                        history_db.save_voice_metadata(_umid, "deepgram", detected_language=selected_language if selected_language != "multi" else None)
                    _amid = history_db.save_message(_sess, user.id, "assistant", _json.dumps({"action": _action, "params": _params}), sequence_number=2)
                    if _amid:
                        history_db.save_intent_result(_amid, user.id, _action, _params, source="voice_ws")
                    history_db.close_session(_sess)

        await result_q.put(None)
        await results_task
        logger.info("[WS-CMD] Session closed")


@router.post("/api/voice-transcript")
async def process_voice_transcript(request: dict, settings = Depends(get_settings)):
    """
    Receive final transcript from frontend and generate response via Gemini.
    Frontend gets transcript from Deepgram directly, then sends to backend for AI response.
    """
    try:
        transcript = request.get("text", "")
        language = request.get("language", "en")
        chat_history = request.get("chat_history", [])

        if not transcript.strip():
            return {"error": "Empty transcript"}

        logger.info(f"[Voice Transcript] Processing: '{transcript[:60]}' ({language})")

        # Call Gemini with chat history
        from google import genai
        from google.genai import types

        if _contains_devanagari(transcript):
            lang_instruction = (
                "CONTEXT: The user spoke in Urdu but the speech recognizer transcribed it "
                "in Hindi/Devanagari script. Treat this as Urdu speech.\n"
                "You MUST reply with ONLY a valid JSON object — no markdown, no extra text:\n"
                '{"detected_language":"hi",'
                '"converted_to_urdu":"<rewrite the message in Urdu Arabic script>",'
                '"response":"<your helpful response in Urdu Arabic script>"}'
            )
        else:
            lang_instruction = {
                "ur": "Reply ONLY in Urdu script (اردو). Do not use Roman Urdu or English.",
                "en": "Reply ONLY in English. Do not use Urdu or any other language.",
            }.get(language, "")

        contents = [
            types.Content(
                role="model" if m["role"] == "assistant" else "user",
                parts=[types.Part(text=m["content"])],
            )
            for m in chat_history
        ]

        user_message = f"[{language.upper()}] {transcript}"
        if lang_instruction:
            user_message = f"{user_message}\n\n{lang_instruction}"

        contents.append(
            types.Content(role="user", parts=[types.Part(text=user_message)])
        )

        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        full_response = ""

        for chunk in client.models.generate_content_stream(
            model=settings.GEMINI_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                max_output_tokens=settings.GEMINI_MAX_TOKENS,
            ),
        ):
            if chunk.text:
                full_response += chunk.text

        logger.info(f"[Gemini] Response ready ({len(full_response)} chars)")
        return {"text": full_response}

    except Exception as e:
        logger.error(f"[Voice Transcript] Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/voice-transcribe")
async def transcribe_audio(file: UploadFile = File(...), language: str = "en", settings = Depends(get_settings)):
    """
    Transcribe audio file using Deepgram Nova-3.
    Returns the transcription text.

    Input: audio file (m4a, mp4, wav, etc.), language (en, ur)
    Output: {"transcript": "..."}
    """
    try:
        logger.info(f"[Voice Transcribe] Processing audio file: {file.filename}, language: {language}")

        if not settings.DEEPGRAM_API_KEY:
            raise HTTPException(status_code=500, detail="Deepgram API key not configured")

        audio_bytes = await file.read()
        logger.info(f"[Voice Transcribe] Read {len(audio_bytes)} bytes")

        # Convert M4A to linear16 PCM for Deepgram
        pcm_bytes = convert_m4a_to_linear16(audio_bytes)
        logger.info(f"[Voice Transcribe] Converted to {len(pcm_bytes)} bytes of PCM")

        # Call Deepgram REST API for prerecorded audio
        import httpx

        # Build URL - omit language parameter for 'auto' to let Deepgram auto-detect
        base_params = "model=nova-3&smart_format=true"
        if language and language != "auto":
            deepgram_url = f"https://api.deepgram.com/v1/listen?{base_params}&language={language}"
        else:
            deepgram_url = f"https://api.deepgram.com/v1/listen?{base_params}"
        logger.info(f"[Voice Transcribe] Deepgram URL: {deepgram_url}")

        headers = {
            "Authorization": f"Token {settings.DEEPGRAM_API_KEY}",
            "Content-Type": "application/octet-stream",
        }

        async with httpx.AsyncClient(timeout=30) as client_http:
            response = await client_http.post(
                deepgram_url,
                content=pcm_bytes,
                headers=headers,
            )

        if response.status_code != 200:
            logger.error(f"[Voice Transcribe] Deepgram error: {response.text}")
            raise HTTPException(status_code=500, detail=f"Deepgram API error: {response.text}")

        result = response.json()
        transcript = result.get("results", {}).get("channels", [{}])[0].get("alternatives", [{}])[0].get("transcript", "")

        if not transcript:
            logger.error(f"[Voice Transcribe] No transcript in response: {result}")
            raise HTTPException(status_code=500, detail="No transcript returned from Deepgram")

        logger.info(f"[Voice Transcribe] Transcript: '{transcript}'")

        return {"transcript": transcript}

    except Exception as e:
        logger.error(f"[Voice Transcribe] Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


def convert_m4a_to_linear16(m4a_bytes: bytes) -> bytes:
    """
    Convert M4A audio chunk to linear16 PCM format that Deepgram understands.
    Uses ffmpeg for conversion. Returns raw PCM bytes suitable for Deepgram.
    """
    try:
        # Write input bytes to temp file
        with tempfile.NamedTemporaryFile(suffix=".m4a", delete=False) as tmp_in:
            tmp_in.write(m4a_bytes)
            tmp_in_path = tmp_in.name

        # Create temp output file path
        tmp_out_path = tmp_in_path.replace(".m4a", ".wav")

        try:
            # Use ffmpeg to convert M4A to WAV (linear16 PCM)
            # -acodec pcm_s16le = 16-bit signed linear PCM
            # -ar 16000 = 16kHz sample rate
            # -ac 1 = mono
            subprocess.run(
                [
                    "ffmpeg",
                    "-i", tmp_in_path,
                    "-acodec", "pcm_s16le",
                    "-ar", "16000",
                    "-ac", "1",
                    "-y",  # overwrite output
                    tmp_out_path
                ],
                capture_output=True,
                timeout=5,
                check=True
            )

            # Read converted WAV file
            with open(tmp_out_path, "rb") as f:
                wav_bytes = f.read()

            logger.debug(f"[Audio] Converted M4A ({len(m4a_bytes)} bytes) → WAV ({len(wav_bytes)} bytes)")
            return wav_bytes

        finally:
            # Clean up temp files
            if os.path.exists(tmp_in_path):
                os.unlink(tmp_in_path)
            if os.path.exists(tmp_out_path):
                os.unlink(tmp_out_path)

    except FileNotFoundError:
        logger.warning("[Audio] ffmpeg not found - ensure ffmpeg is installed. Sending M4A as-is.")
        return m4a_bytes
    except subprocess.TimeoutExpired:
        logger.error("[Audio] ffmpeg conversion timeout")
        return m4a_bytes
    except Exception as e:
        logger.error(f"[Audio] Conversion failed: {e}")
        return m4a_bytes


@router.websocket("/ws/voice")
async def voice_ws(websocket: WebSocket):
    await websocket.accept()
    settings = get_settings()
    logger.info("[WS] Client connected")

    # Validate token from query params
    try:
        user = await validate_websocket_token(websocket)
        logger.info(f"[WS] Token validated for user: {user.id}")
    except HTTPException:
        return

    ws_session_id: Optional[str] = history_db.create_session(user.id, "voice_chat")

    # ── Guard: validate required API keys at connection time ──────────────────
    if not settings.DEEPGRAM_API_KEY:
        logger.error("[WS] DEEPGRAM_API_KEY not configured")
        await websocket.send_json({"type": "error", "message": "DEEPGRAM_API_KEY not configured"})
        await websocket.close()
        return

    loop = asyncio.get_event_loop()
    result_q: asyncio.Queue = asyncio.Queue()
    chat_history: list[dict] = []          # In-memory, per-connection lifetime
    health_context: dict = {               # Health context for this session
        "recentHealthLogs": [],
        "todaysMedicines": {},
    }

    # ── Import Deepgram (with SDK-version fallback) ───────────────────────────
    try:
        from deepgram import DeepgramClient, LiveTranscriptionEvents, LiveOptions
    except ImportError:
        try:
            from deepgram import DeepgramClient
            from deepgram.listen import LiveTranscriptionEvents, LiveOptions
        except ImportError:
            logger.error("[WS] deepgram-sdk not installed or incompatible version")
            await websocket.send_json({"type": "error", "message": "Deepgram SDK unavailable"})
            await websocket.close()
            return

    # ── Step 1: Wait for health context and language selection ───────────────
    selected_language: Optional[str] = None
    logger.info("[WS] Awaiting health_context and language_select messages…")

    try:
        while selected_language is None:
            data = await websocket.receive()
            if "text" in data and data["text"]:
                msg = json.loads(data["text"])

                if msg.get("type") == "health_context":
                    health_context = msg.get("data", health_context)
                    logs_count = len(health_context.get('recentHealthLogs', []))
                    medicines_count = len(health_context.get('todaysMedicines', {}).get('medications', []))
                    logger.info(f"[WS] Health context received: {logs_count} logs, {medicines_count} medicines")
                    if logs_count > 0:
                        logger.debug(f"[WS] Sample health log: {health_context.get('recentHealthLogs', [])[0] if health_context.get('recentHealthLogs') else 'None'}")
                    if medicines_count > 0:
                        logger.debug(f"[WS] Sample medicine: {health_context.get('todaysMedicines', {}).get('medications', [])[0] if health_context.get('todaysMedicines', {}).get('medications') else 'None'}")

                elif msg.get("type") == "language_select":
                    selected_language = msg.get("language", "en")
                    logger.info(f"[WS] Language selected: {selected_language}")
                    await websocket.send_json(
                        {"type": "language_selected", "language": selected_language}
                    )
    except WebSocketDisconnect:
        logger.info("[WS] Client disconnected before language selection")
        return

    # ── Step 2: Start Deepgram live connection ────────────────────────────────
    dg = DeepgramClient(settings.DEEPGRAM_API_KEY)
    dg_conn = dg.listen.websocket.v("1")

    completed_utterances: list[str] = []
    latest_transcript: str = ""
    final_sent: bool = False

    def on_transcript(self, result, **kwargs):
        nonlocal latest_transcript
        alt = result.channel.alternatives[0]
        transcript = alt.transcript.strip()

        logger.info(
            f"[Deepgram] is_final={result.is_final} speech_final={result.speech_final} "
            f"text='{transcript[:80]}'"
        )
        if not transcript:
            return

        if result.is_final and transcript not in completed_utterances:
            logger.info(f"[Deepgram] Completed utterance: '{transcript[:60]}'")
            completed_utterances.append(transcript)

        latest_transcript = transcript

        asyncio.run_coroutine_threadsafe(
            result_q.put({"type": "partial", "text": transcript}), loop
        )

    def on_error(self, error, **kwargs):
        logger.error(f"[Deepgram] Error: {error}")
        asyncio.run_coroutine_threadsafe(
            result_q.put({"type": "error", "message": str(error)}), loop
        )

    dg_conn.on(LiveTranscriptionEvents.Transcript, on_transcript)
    dg_conn.on(LiveTranscriptionEvents.Error, on_error)

    started = dg_conn.start(
        LiveOptions(
            model="nova-3",
            language=selected_language,
            encoding="linear16",
            sample_rate=16000,
            channels=1,
            smart_format=True,
            interim_results=True,
            endpointing=False,
            punctuate=True,
        )
    )

    if not started:
        logger.error("[Deepgram] Failed to start live connection")
        await websocket.send_json({"type": "error", "message": "Deepgram failed to connect"})
        await websocket.close()
        return

    logger.info("[Deepgram] Live connection started")

    # ── Gemini background state ───────────────────────────────────────────────
    gemini_state: dict = {
        "gemini_task": None,
        "latest_used": "",
        "last_sent_response": None,
    }

    # ── Gemini runner ─────────────────────────────────────────────────────────
    async def run_gemini_background(transcript: str) -> str:
        """
        Call Gemini with the current chat_history + new transcript.
        Does NOT modify chat_history — caller commits after the final response
        is confirmed.  Returns the full response string.
        """
        from google import genai
        from google.genai import types
        from app.services.chat_service import chat_service

        # Log what health_context we have access to
        logs_count = len(health_context.get('recentHealthLogs', []))
        meds_data = health_context.get('todaysMedicines', {})
        logger.info(
            f"[Gemini] Starting call — transcript: '{transcript[:40]}' "
            f"| history length: {len(chat_history)}"
            f"| health context: {logs_count} logs"
        )

        contents = [
            types.Content(
                role="model" if m["role"] == "assistant" else "user",
                parts=[types.Part(text=m["content"])],
            )
            for m in chat_history
        ]

        if _contains_devanagari(transcript):
            # Deepgram transcribed Urdu speech as Hindi (Devanagari). Force Gemini
            # to treat it as Urdu and return the structured JSON the client expects.
            lang_instruction = (
                "CONTEXT: The user spoke in Urdu but the speech recognizer transcribed it "
                "in Hindi/Devanagari script. Treat this as Urdu speech.\n"
                "You MUST reply with ONLY a valid JSON object — no markdown, no extra text:\n"
                '{"detected_language":"hi",'
                '"converted_to_urdu":"<rewrite the message in Urdu Arabic script>",'
                '"response":"<your helpful response in Urdu Arabic script>"}'
            )
        else:
            lang_instruction = {
                "ur": "Reply ONLY in Urdu script (اردو). Do not use Roman Urdu or English.",
                "en": "Reply ONLY in English. Do not use Urdu or any other language.",
            }.get(selected_language, "")

        user_message = f"[{selected_language.upper()}] {transcript}"
        if lang_instruction:
            user_message = f"{user_message}\n\n{lang_instruction}"

        contents.append(
            types.Content(role="user", parts=[types.Part(text=user_message)])
        )

        # Format health context for Gemini
        health_context_text = chat_service._format_health_context(health_context)
        logger.info(f"[Gemini] Formatted health context ({len(health_context_text)} chars)")
        if health_context_text:
            logger.info(f"[Gemini] Health context preview: {health_context_text[:300]}")
        else:
            logger.info(f"[Gemini] No health context to include (empty after formatting)")
        full_system_prompt = SYSTEM_PROMPT + health_context_text

        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        token_q: asyncio.Queue = asyncio.Queue()
        full_response = ""

        def _run_sync():
            nonlocal full_response
            try:
                for chunk in client.models.generate_content_stream(
                    model=settings.GEMINI_MODEL,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=full_system_prompt,
                        max_output_tokens=settings.GEMINI_MAX_TOKENS,
                    ),
                ):
                    if chunk.text:
                        full_response += chunk.text
                asyncio.run_coroutine_threadsafe(
                    token_q.put(("done", full_response)), loop
                )
            except Exception as exc:
                error_msg = str(exc).lower()
                logger.error(f"[Gemini] Thread error: {exc}", exc_info=True)
                # Return generic message for API errors (quota, model unavailable, etc)
                if any(x in error_msg for x in ['quota', 'rate limit', 'unavailable', 'overloaded']):
                    generic_error = "Model not available. Please try again later."
                else:
                    generic_error = str(exc)
                asyncio.run_coroutine_threadsafe(
                    token_q.put(("error", generic_error)), loop
                )

        thread = threading.Thread(target=_run_sync, daemon=True)
        thread.start()

        result = await token_q.get()
        thread.join()

        if result[0] == "error":
            logger.error(f"[Gemini] Error response: {result[1]}")
            return f"Error: {result[1]}"

        response_text: str = result[1]
        logger.info(f"[Gemini] Complete — {len(response_text)} chars")
        return response_text

    # ── Send final response ───────────────────────────────────────────────────
    async def send_final_response(response_text: str):
        if response_text == gemini_state["last_sent_response"]:
            logger.info("[WS] Skipped duplicate final_response")
            return
        gemini_state["last_sent_response"] = response_text
        await websocket.send_json({"type": "final_response", "text": response_text})
        logger.info(f"[WS] Sent final_response ({len(response_text)} chars)")

    # ── Result processor (runs concurrently with audio receiver) ─────────────
    async def process_results():
        while True:
            msg = await result_q.get()
            if msg is None:          # shutdown sentinel
                break

            await websocket.send_json(msg)

            # Partial: start one background Gemini task on the first meaningful partial
            if msg["type"] == "partial" and len(msg["text"]) > 15:
                if not gemini_state["gemini_task"]:
                    logger.info(
                        f"[Gemini] Launching background task on partial: '{msg['text'][:40]}'"
                    )
                    gemini_state["latest_used"] = msg["text"]
                    gemini_state["gemini_task"] = asyncio.create_task(
                        run_gemini_background(msg["text"])
                    )

            # Final: resolve background task (reuse or rerun as needed)
            elif msg["type"] == "final" and msg.get("text"):
                final_text: str = msg["text"]
                logger.info(f"[Final] Processing: '{final_text[:80]}'")

                try:
                    if gemini_state["gemini_task"]:
                        text_delta = len(final_text) - len(gemini_state["latest_used"])

                        if gemini_state["gemini_task"].done():
                            if text_delta > 10:
                                logger.info(
                                    f"[Final] Text changed by {text_delta} chars — rerunning"
                                )
                                gemini_state["gemini_task"] = asyncio.create_task(
                                    run_gemini_background(final_text)
                                )
                            else:
                                logger.info(
                                    f"[Final] Reusing completed background result (delta: {text_delta})"
                                )
                        else:
                            logger.info("[Final] Waiting for in-progress background task")

                        response = await gemini_state["gemini_task"]
                    else:
                        logger.info("[Final] No background task — starting fresh")
                        gemini_state["gemini_task"] = asyncio.create_task(
                            run_gemini_background(final_text)
                        )
                        response = await gemini_state["gemini_task"]

                    logger.info(f"[Final] Response ready ({len(response)} chars)")
                    # Commit to session history only after the final answer is confirmed
                    chat_history.append({"role": "user", "content": final_text})
                    chat_history.append({"role": "assistant", "content": response})
                    await send_final_response(response)

                    # Persist this turn to DB (after response is already sent)
                    if ws_session_id:
                        user_seq = len(chat_history) - 1
                        asst_seq = len(chat_history)
                        _umid = history_db.save_message(
                            ws_session_id, user.id, "user", final_text,
                            sequence_number=user_seq,
                        )
                        history_db.save_message(
                            ws_session_id, user.id, "assistant", response,
                            sequence_number=asst_seq,
                            model_name=settings.GEMINI_MODEL,
                        )
                        if _umid:
                            history_db.save_voice_metadata(
                                _umid, "deepgram",
                                detected_language=selected_language if selected_language != "multi" else None,
                            )

                except Exception as exc:
                    error_msg = str(exc).lower()
                    logger.error(f"[Final] Unexpected error: {exc}", exc_info=True)
                    # Return generic message for API errors
                    if any(x in error_msg for x in ['quota', 'rate limit', 'unavailable', 'overloaded']):
                        await websocket.send_json({"type": "error", "message": "Model not available. Please try again later."})
                    else:
                        await websocket.send_json({"type": "error", "message": "Processing error. Please try again."})

    results_task = asyncio.create_task(process_results())

    # ── Audio receiver (main loop) ────────────────────────────────────────────
    try:
        while True:
            data = await websocket.receive()

            if data.get("type") == "websocket.disconnect":
                break

            if "bytes" in data and data["bytes"]:
                # Audio chunk received (M4A from Android, WAV from iOS)
                # Convert to linear16 PCM for Deepgram compatibility
                audio_chunk = data["bytes"]

                # Try to detect format and convert if needed
                if len(audio_chunk) > 4:
                    header = audio_chunk[:4]
                    # Check for M4A signature (ftyp) or WAV signature (RIFF)
                    if header[:2] == b'\xff\xfb' or header[:2] == b'\xff\xfa':
                        # MP3/AAC ADTS sync word - convert to linear16
                        logger.debug("[Audio] Detected AAC/M4A, converting to linear16")
                        audio_chunk = convert_m4a_to_linear16(audio_chunk)
                    elif header == b'ftyp' or (len(audio_chunk) > 8 and audio_chunk[4:8] == b'ftyp'):
                        # M4A format - convert to linear16
                        logger.debug("[Audio] Detected M4A container, converting to linear16")
                        audio_chunk = convert_m4a_to_linear16(audio_chunk)

                dg_conn.send(audio_chunk)

            elif "text" in data and data["text"]:
                msg = json.loads(data["text"])

                if msg.get("type") == "stop":
                    logger.info("[WS] Stop signal received")
                    dg_conn.finish()

                    # Assemble final transcript from completed utterances + latest partial
                    final_text = ""
                    if completed_utterances:
                        final_text = " ".join(completed_utterances)
                        if latest_transcript and latest_transcript not in final_text:
                            final_text += " " + latest_transcript
                    elif latest_transcript:
                        final_text = latest_transcript

                    if final_text.strip():
                        logger.info(f"[WS] Final text on stop: '{final_text[:80]}'")
                        await result_q.put({"type": "final", "text": final_text.strip()})
                        final_sent = True
                    else:
                        logger.info("[WS] Stop received but no transcript accumulated")

                    completed_utterances.clear()
                    break

    except WebSocketDisconnect:
        logger.info("[WS] Client disconnected mid-stream")

    finally:
        logger.info("[Deepgram] Closing connection…")
        dg_conn.finish()
        await asyncio.sleep(1.0)   # Allow Deepgram callbacks to fire after finish()

        # Flush any transcript that arrived after the audio loop ended (abrupt disconnect)
        if not final_sent:
            final_text = ""
            if completed_utterances:
                final_text = " ".join(completed_utterances)
                if latest_transcript and latest_transcript not in final_text:
                    final_text += " " + latest_transcript
            elif latest_transcript:
                final_text = latest_transcript

            if final_text.strip():
                logger.info(f"[Deepgram] Flushing transcript on disconnect: '{final_text[:80]}'")
                await result_q.put({"type": "final", "text": final_text.strip()})

        await result_q.put(None)   # Shut down process_results
        await results_task
        if ws_session_id:
            history_db.close_session(ws_session_id)
        logger.info("[WS] Session closed")
