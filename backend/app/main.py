import asyncio
import glob
import json
import logging
import os
import shutil
import wave
from datetime import datetime
from typing import Dict

import redis
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.models import StreamControlRequest, StreamInfo, SubtitleMessage, TranslationRequest
from app.services.cache_service import CacheService
from app.services.edge_service import EdgeService
from app.services.subtitle_service import SubtitleService
from app.services.translation_service import TranslationService
from app.websocket_manager import WebSocketManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")
USE_MOCK_SUBTITLES = os.getenv("USE_MOCK_SUBTITLES", "false").lower() == "true"
STT_STREAM_ID = os.getenv("STT_STREAM_ID", "demo")
# translator가 번역 완료 후 모든 언어를 묶어서 publish하는 채널.
# 이전엔 backend가 stt:results를 직접 구독해 mock prefix로 가짜 번역을 만들었으나,
# 이제 translator의 Google 번역 결과를 그대로 받아 WebSocket으로 브로드캐스트한다.
SUBTITLE_TRANSLATED_CHANNEL = os.getenv("SUBTITLE_TRANSLATED_CHANNEL", "subtitle:translated")
CAPTION_INPUT_PATH = os.getenv("CAPTION_INPUT_PATH", "/sample/AWS.mp4")
CAPTION_LIVE_INPUT_URL = os.getenv("CAPTION_LIVE_INPUT_URL", "rtmp://mediamtx:1935/live/demo")
CAPTION_OUTPUT_DIR = os.getenv("CAPTION_OUTPUT_DIR", "/data/audio")
SEGMENT_DURATION = float(os.getenv("SEGMENT_DURATION", "2"))

app = FastAPI(
    title="Cloud Multilingual Streaming API",
    version="1.0.0",
    description="Real-time multilingual subtitle pipeline for a cloud streaming term project.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN, "http://127.0.0.1:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/media", StaticFiles(directory="/sample"), name="media")

cache = CacheService(REDIS_URL)
translator = TranslationService()
subtitle_service = SubtitleService(cache, translator)
manager = WebSocketManager()
edge_service = EdgeService()

streams: Dict[str, StreamInfo] = {
    "demo": StreamInfo(
        stream_id="demo",
        title="Global Live Demo",
        is_active=True,
        started_at=datetime.utcnow(),
        viewers=0,
    )
}
stream_tasks: Dict[str, asyncio.Task] = {}
caption_demo_tasks: Dict[str, asyncio.Task] = {}
subtitle_listener_task: asyncio.Task | None = None


@app.on_event("startup")
async def startup_event():
    await cache.set_stream("demo", streams["demo"].model_dump(mode="json"))
    global subtitle_listener_task
    subtitle_listener_task = asyncio.create_task(translated_subtitle_listener())


@app.on_event("shutdown")
async def shutdown_event():
    if subtitle_listener_task:
        subtitle_listener_task.cancel()


@app.get("/")
async def root():
    return {
        "status": "healthy",
        "service": "Cloud Multilingual Streaming API",
        "redis": cache.is_redis_enabled,
    }


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "redis": "enabled" if cache.is_redis_enabled else "memory-fallback",
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.get("/api/edges/status")
async def edges_status():
    """프론트가 polling해 엣지별 running 상태를 갱신한다."""
    return {"available": edge_service.available, "edges": edge_service.status_all()}


@app.post("/api/edges/{edge_id}/stop")
async def edges_stop(edge_id: str):
    from fastapi import HTTPException
    from docker.errors import NotFound
    try:
        return edge_service.stop(edge_id)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/edges/{edge_id}/start")
async def edges_start(edge_id: str):
    from fastapi import HTTPException
    from docker.errors import NotFound
    try:
        return edge_service.start(edge_id)
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/streams/{stream_id}", response_model=StreamInfo)
async def get_stream(stream_id: str):
    if stream_id not in streams:
        streams[stream_id] = StreamInfo(
            stream_id=stream_id,
            title=f"Live Stream {stream_id}",
            is_active=True,
            started_at=datetime.utcnow(),
        )
    stream = streams[stream_id]
    stream.viewers = manager.viewer_count(stream_id)
    return stream


@app.post("/streams/{stream_id}/start", response_model=StreamInfo)
async def start_stream(stream_id: str, request: StreamControlRequest):
    stream = StreamInfo(
        stream_id=stream_id,
        title=request.title or f"Live Stream {stream_id}",
        is_active=True,
        started_at=datetime.utcnow(),
        source_language=request.source_language,
        target_languages=request.target_languages,
    )
    streams[stream_id] = stream
    await cache.set_stream(stream_id, stream.model_dump(mode="json"))
    await manager.broadcast(stream_id, {"type": "stream_started", "data": stream.model_dump(mode="json")})
    return stream


@app.post("/streams/{stream_id}/stop")
async def stop_stream(stream_id: str):
    stream = await get_stream(stream_id)
    stream.is_active = False
    streams[stream_id] = stream
    task = stream_tasks.pop(stream_id, None)
    if task:
        task.cancel()
    await manager.broadcast(stream_id, {"type": "stream_stopped", "stream_id": stream_id})
    return {"status": "stopped", "stream_id": stream_id}


@app.post("/translate")
async def translate(request: TranslationRequest):
    translations = await translator.translate(request.text, request.target_langs, request.source_lang)
    return {
        "original": request.text,
        "source_lang": request.source_lang,
        "translations": translations,
    }


@app.get("/subtitles/{stream_id}/recent")
async def recent_subtitles(stream_id: str, limit: int = 20):
    return {
        "stream_id": stream_id,
        "items": await subtitle_service.recent(stream_id, limit),
    }


@app.post("/streams/{stream_id}/caption-demo/start")
async def start_caption_demo(
    stream_id: str,
    start_at: float = Query(0, ge=0),
    reset: bool = Query(True),
    source: str = Query("file", pattern="^(file|live)$"),
):
    task = caption_demo_tasks.get(stream_id)
    if task and not task.done():
        if not reset:
            return {"status": "already_running", "stream_id": stream_id}

        task.cancel()
        await clear_stt_queue()

    if stream_id not in streams:
        streams[stream_id] = StreamInfo(
            stream_id=stream_id,
            title=f"Live Stream {stream_id}",
            is_active=True,
            started_at=datetime.utcnow(),
        )

    if source == "live":
        task = asyncio.create_task(run_live_caption_demo(stream_id, reset=reset))
    else:
        task = asyncio.create_task(run_caption_demo(stream_id, start_at=start_at, reset=reset))
    caption_demo_tasks[stream_id] = task
    return {
        "status": "started",
        "stream_id": stream_id,
        "source": source,
        "start_at": start_at,
        "reset": reset,
    }


@app.post("/streams/{stream_id}/caption-demo/stop")
async def stop_caption_demo(stream_id: str):
    task = caption_demo_tasks.pop(stream_id, None)
    if task and not task.done():
        task.cancel()

    await clear_stt_queue()
    await manager.broadcast(stream_id, {"type": "caption_demo_stopped", "stream_id": stream_id})
    return {"status": "stopped", "stream_id": stream_id}


async def subtitle_loop(stream_id: str):
    logger.info("Subtitle loop started for %s", stream_id)
    try:
        while True:
            stream = streams.get(stream_id)
            if not stream or not stream.is_active:
                await asyncio.sleep(1)
                continue

            subtitle = await subtitle_service.generate_mock_subtitle(
                stream_id=stream_id,
                target_langs=stream.target_languages,
                source_lang=stream.source_language,
            )
            await manager.broadcast(
                stream_id,
                {
                    "type": "subtitle",
                    "stream_id": stream_id,
                    "data": subtitle.model_dump(mode="json"),
                },
            )
            await asyncio.sleep(2)
    except asyncio.CancelledError:
        logger.info("Subtitle loop cancelled for %s", stream_id)
        raise


def ensure_subtitle_loop(stream_id: str):
    if not USE_MOCK_SUBTITLES:
        return
    task = stream_tasks.get(stream_id)
    if task is None or task.done():
        stream_tasks[stream_id] = asyncio.create_task(subtitle_loop(stream_id))


async def read_pubsub_message(pubsub):
    return await asyncio.to_thread(
        pubsub.get_message,
        ignore_subscribe_messages=True,
        timeout=1.0,
    )


def wav_duration(path: str) -> float:
    with wave.open(path, "rb") as wf:
        return wf.getnframes() / wf.getframerate()


def segment_index(path: str) -> int:
    filename = os.path.basename(path)
    number = os.path.splitext(filename)[0].replace("segment_", "")
    return int(number)


def cleanup_audio_segments():
    os.makedirs(CAPTION_OUTPUT_DIR, exist_ok=True)
    for path in glob.glob(os.path.join(CAPTION_OUTPUT_DIR, "segment_*.wav")):
        os.remove(path)
    for path in glob.glob(os.path.join(CAPTION_OUTPUT_DIR, "run-*")):
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)


def create_caption_run_dir() -> str:
    os.makedirs(CAPTION_OUTPUT_DIR, exist_ok=True)
    run_dir = os.path.join(CAPTION_OUTPUT_DIR, f"run-{int(datetime.utcnow().timestamp() * 1000)}")
    os.makedirs(run_dir, exist_ok=True)
    return run_dir


async def clear_stt_queue():
    def delete_queue():
        client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        client.delete("stt:queue")

    await asyncio.to_thread(delete_queue)


async def run_caption_demo(stream_id: str, start_at: float = 0, reset: bool = True):
    process = None
    try:
        client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        if reset:
            client.delete(f"stream:{stream_id}:subtitles", "stt:queue")
            await manager.broadcast(stream_id, {"type": "subtitle_reset", "stream_id": stream_id})
        else:
            client.delete("stt:queue")

        run_dir = create_caption_run_dir()
        start_segment = int(start_at // SEGMENT_DURATION)
        index = start_segment
        pushed = 0

        logger.info("Caption demo realtime extraction input=%s start=%.2fs", CAPTION_INPUT_PATH, start_at)
        while True:
            pts = index * SEGMENT_DURATION
            path = os.path.join(run_dir, f"segment_{index:03d}.wav")

            # Wait until this audio interval would have actually played.
            await asyncio.sleep(SEGMENT_DURATION)

            command = [
                "ffmpeg",
                "-y",
                "-ss",
                str(pts),
                "-i",
                CAPTION_INPUT_PATH,
                "-t",
                str(SEGMENT_DURATION),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                path,
            ]

            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await process.communicate()
            if process.returncode != 0:
                logger.info("Caption demo reached end or ffmpeg stopped index=%s", index)
                return

            duration = await asyncio.to_thread(wav_duration, path)
            if duration < SEGMENT_DURATION - 0.1:
                logger.info("Caption demo reached final short segment=%s duration=%.3fs", index, duration)
                return

            payload = {
                "segment_num": index,
                "audio_path": path,
                "pts": pts,
                "ingested_at": datetime.utcnow().timestamp(),
            }
            client.rpush("stt:queue", json.dumps(payload))
            logger.info("Caption demo queued segment=%s", index)
            pushed += 1
            index += 1

        logger.info("Caption demo completed stream=%s segments=%s", stream_id, pushed)
    except asyncio.CancelledError:
        await clear_stt_queue()
        logger.info("Caption demo cancelled stream=%s", stream_id)
        raise
    finally:
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except asyncio.TimeoutError:
                process.kill()
        task = caption_demo_tasks.get(stream_id)
        if task is asyncio.current_task():
            caption_demo_tasks.pop(stream_id, None)


async def wait_for_complete_wav(path: str) -> float | None:
    for _ in range(10):
        try:
            duration = await asyncio.to_thread(wav_duration, path)
            if abs(duration - SEGMENT_DURATION) <= 0.1:
                return duration
        except (EOFError, wave.Error, FileNotFoundError):
            pass
        await asyncio.sleep(0.2)
    return None


async def run_live_caption_demo(stream_id: str, reset: bool = True):
    process = None
    try:
        client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        if reset:
            client.delete(f"stream:{stream_id}:subtitles", "stt:queue")
            await manager.broadcast(stream_id, {"type": "subtitle_reset", "stream_id": stream_id})
        else:
            client.delete("stt:queue")

        next_segment_num = 0
        while True:
            run_dir = create_caption_run_dir()
            output_pattern = os.path.join(run_dir, "segment_%03d.wav")
            command = [
                "ffmpeg",
                "-y",
                "-i",
                CAPTION_LIVE_INPUT_URL,
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-f",
                "segment",
                "-segment_time",
                str(SEGMENT_DURATION),
                "-reset_timestamps",
                "1",
                output_pattern,
            ]

            logger.info("Live caption demo extracting audio input=%s", CAPTION_LIVE_INPUT_URL)
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )

            queued: set[str] = set()
            while True:
                if process.returncode is not None:
                    if process.returncode != 0:
                        logger.warning(
                            "Live caption ffmpeg stopped returncode=%s; retrying in 1s",
                            process.returncode,
                        )
                    else:
                        logger.info("Live caption ffmpeg stopped; retrying in 1s")
                    await asyncio.sleep(1)
                    break

                for path in sorted(glob.glob(os.path.join(run_dir, "segment_*.wav"))):
                    if path in queued:
                        continue

                    duration = await wait_for_complete_wav(path)
                    if duration is None:
                        continue

                    segment_num = next_segment_num
                    next_segment_num += 1
                    payload = {
                        "segment_num": segment_num,
                        "audio_path": path,
                        "pts": segment_num * SEGMENT_DURATION,
                        "ingested_at": datetime.utcnow().timestamp(),
                    }
                    client.rpush("stt:queue", json.dumps(payload))
                    queued.add(path)
                    logger.info("Live caption queued segment=%s duration=%.3fs", segment_num, duration)

                await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        await clear_stt_queue()
        logger.info("Live caption demo cancelled stream=%s", stream_id)
        raise
    finally:
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except asyncio.TimeoutError:
                process.kill()
        task = caption_demo_tasks.get(stream_id)
        if task is asyncio.current_task():
            caption_demo_tasks.pop(stream_id, None)


async def translated_subtitle_listener():
    """translator가 publish한 subtitle:translated 메시지를 받아 WebSocket으로 브로드캐스트.

    translator 메시지 포맷:
      {segment_num, original_text, translations: {en, ja, zh, ...},
       start_pts, end_pts, subtitle_delay, ingested_at}
    """
    try:
        client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        client.ping()
        pubsub = client.pubsub()
        pubsub.subscribe(SUBTITLE_TRANSLATED_CHANNEL)
        logger.info("Translated subtitle listener subscribed channel=%s", SUBTITLE_TRANSLATED_CHANNEL)
    except Exception as exc:
        logger.warning("Translated subtitle listener disabled: %s", exc)
        return

    try:
        while True:
            message = await read_pubsub_message(pubsub)
            if not message or message.get("type") != "message":
                continue

            payload = json.loads(message["data"])
            text = (payload.get("original_text") or "").strip()
            translations = payload.get("translations") or {}
            if not text:
                continue

            streams.setdefault(
                STT_STREAM_ID,
                StreamInfo(
                    stream_id=STT_STREAM_ID,
                    title=f"Live Stream {STT_STREAM_ID}",
                    is_active=True,
                    started_at=datetime.utcnow(),
                ),
            )
            start_pts = float(payload.get("start_pts", 0.0))
            end_pts = float(payload.get("end_pts", start_pts + 2.0))
            segment_num = payload.get("segment_num", int(start_pts // SEGMENT_DURATION))

            # translator의 진짜 번역 결과를 그대로 사용 — mock 호출 없음.
            subtitle = SubtitleMessage(
                id=f"{STT_STREAM_ID}-stt-{segment_num}-{int(start_pts * 1000)}",
                stream_id=STT_STREAM_ID,
                timestamp=start_pts,
                duration=max(end_pts - start_pts, 0.1),
                original_text=text,
                translations=translations,
                created_at=datetime.utcnow(),
            )
            await cache.append_subtitle(STT_STREAM_ID, subtitle.model_dump(mode="json"))
            await manager.broadcast(
                STT_STREAM_ID,
                {
                    "type": "subtitle",
                    "stream_id": STT_STREAM_ID,
                    "data": subtitle.model_dump(mode="json"),
                },
            )
            logger.info(
                "Broadcast translated subtitle stream=%s segment=%s langs=%s",
                STT_STREAM_ID, segment_num, list(translations.keys()),
            )
    except asyncio.CancelledError:
        pubsub.close()
        raise


@app.websocket("/ws/stream/{stream_id}")
async def websocket_stream(websocket: WebSocket, stream_id: str):
    await manager.connect(stream_id, websocket)
    if stream_id not in streams:
        streams[stream_id] = StreamInfo(
            stream_id=stream_id,
            title=f"Live Stream {stream_id}",
            is_active=True,
            started_at=datetime.utcnow(),
        )

    ensure_subtitle_loop(stream_id)
    await manager.broadcast(
        stream_id,
        {
            "type": "viewer_update",
            "stream_id": stream_id,
            "data": {"viewers": manager.viewer_count(stream_id)},
        },
    )

    try:
        while websocket.client_state == WebSocketState.CONNECTED:
            await websocket.receive_text()
    except (WebSocketDisconnect, RuntimeError):
        manager.disconnect(stream_id, websocket)
    finally:
        await manager.broadcast(
            stream_id,
            {
                "type": "viewer_update",
                "stream_id": stream_id,
                "data": {"viewers": manager.viewer_count(stream_id)},
            },
        )
