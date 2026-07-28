"""DubShow Online — FastAPI, WebSocket, gravação no navegador e FFmpeg."""

from __future__ import annotations

import asyncio
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from media_service import MAX_UPLOAD_BYTES, MediaError, MediaService
from room_manager import RoomError, RoomManager
from scoring import ScoreError, compare_audio

BASE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = Path(os.environ.get("DUBSHOW_RUNTIME_DIR", BASE_DIR / "runtime"))
STATIC_DIR = BASE_DIR / "static"
manager = RoomManager(RUNTIME_DIR)
media_service: MediaService | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global media_service
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    media_service = MediaService()
    cleanup_task = asyncio.create_task(manager.cleanup_loop())
    try:
        yield
    finally:
        cleanup_task.cancel()


app = FastAPI(title="DubShow Online", version="2.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class CreateRoomRequest(BaseModel):
    name: str = Field(min_length=2, max_length=28)
    max_players: int = Field(default=5, ge=1, le=5)


class JoinRoomRequest(BaseModel):
    name: str = Field(min_length=2, max_length=28)


class AuthRequest(BaseModel):
    player_id: str
    token: str


class YoutubeRequest(AuthRequest):
    url: str
    clip_start: float = 0.0
    clip_duration: float = 12.0


class ActionRequest(AuthRequest):
    action: str


class RenderRequest(AuthRequest):
    target_player_id: str


def _error(exc: Exception, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail=str(exc))


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/rooms")
async def create_room(payload: CreateRoomRequest, request: Request) -> dict:
    try:
        room, player = await manager.create_room(payload.name, payload.max_players)
    except RoomError as exc:
        raise _error(exc) from exc
    invite_url = str(request.base_url).rstrip("/") + f"/?room={room.code}"
    return {
        "room": room.public_dict(),
        "player_id": player.id,
        "token": player.token,
        "invite_url": invite_url,
    }


@app.post("/api/rooms/{code}/join")
async def join_room(code: str, payload: JoinRoomRequest, request: Request) -> dict:
    try:
        room, player = await manager.join_room(code, payload.name)
        await manager.broadcast_state(room)
    except RoomError as exc:
        raise _error(exc) from exc
    return {
        "room": room.public_dict(),
        "player_id": player.id,
        "token": player.token,
        "invite_url": str(request.base_url).rstrip("/") + f"/?room={room.code}",
    }


@app.get("/api/rooms/{code}")
async def room_state(code: str) -> dict:
    try:
        return manager.get_room(code).public_dict()
    except RoomError as exc:
        raise _error(exc, 404) from exc


@app.websocket("/ws/{code}/{player_id}")
async def websocket_room(websocket: WebSocket, code: str, player_id: str, token: str) -> None:
    try:
        room = manager.get_room(code)
        player = manager.authenticate(room, player_id, token)
        await manager.connect(room, player, websocket)
        while True:
            message = await websocket.receive_json()
            if message.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
            elif message.get("type") == "ready":
                player.ready = bool(message.get("ready"))
                await manager.broadcast_state(room)
    except (RoomError, WebSocketDisconnect):
        try:
            room = manager.get_room(code)
            await manager.disconnect(room, player_id)
        except Exception:
            pass
    except Exception:
        try:
            await websocket.close(code=1011)
        except Exception:
            pass


async def _save_upload(upload: UploadFile, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    with destination.open("wb") as output:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                output.close()
                destination.unlink(missing_ok=True)
                raise MediaError("O arquivo excede o limite de 160 MB.")
            output.write(chunk)


@app.post("/api/rooms/{code}/media/upload")
async def upload_media(
    code: str,
    background_tasks: BackgroundTasks,
    player_id: Annotated[str, Form()],
    token: Annotated[str, Form()],
    clip_start: Annotated[float, Form()] = 0.0,
    clip_duration: Annotated[float, Form()] = 12.0,
    file: UploadFile = File(...),
) -> JSONResponse:
    try:
        room = manager.get_room(code)
        manager.authenticate(room, player_id, token, host_only=True)
        if not file.filename:
            raise MediaError("Selecione um arquivo de vídeo.")
        assert media_service is not None
        filename = media_service.safe_filename(file.filename)
        source = room.root_dir / filename
        room.media.status = "uploading"
        room.media.message = "Recebendo o vídeo..."
        room.error_message = ""
        await manager.broadcast_state(room)
        await _save_upload(file, source)
        room.media.status = "processing"
        room.media.message = "Convertendo e separando a referência..."
        await manager.broadcast_state(room)
        background_tasks.add_task(_prepare_upload_task, room.code, source, clip_start, clip_duration)
        return JSONResponse({"ok": True}, status_code=202)
    except (RoomError, MediaError) as exc:
        raise _error(exc) from exc


def _prepare_upload_task(code: str, source: Path, start: float, duration: float) -> None:
    room = manager.rooms.get(code)
    if room is None:
        return
    try:
        assert media_service is not None
        room.media = media_service.prepare_uploaded(source, room.root_dir, start, duration)
        room.phase = "media_ready"
        room.error_message = ""
    except Exception as exc:
        room.media.status = "error"
        room.media.message = str(exc)
        room.error_message = str(exc)
    room.touch()


@app.post("/api/rooms/{code}/media/youtube")
async def youtube_media(code: str, payload: YoutubeRequest, background_tasks: BackgroundTasks) -> JSONResponse:
    try:
        room = manager.get_room(code)
        manager.authenticate(room, payload.player_id, payload.token, host_only=True)
        room.media.status = "processing"
        room.media.message = "Baixando o trecho do YouTube..."
        room.error_message = ""
        await manager.broadcast_state(room)
        background_tasks.add_task(
            _prepare_youtube_task, room.code, payload.url, payload.clip_start, payload.clip_duration
        )
        return JSONResponse({"ok": True}, status_code=202)
    except RoomError as exc:
        raise _error(exc) from exc


def _prepare_youtube_task(code: str, url: str, start: float, duration: float) -> None:
    room = manager.rooms.get(code)
    if room is None:
        return
    try:
        assert media_service is not None
        room.media = media_service.download_youtube(url, room.root_dir, start, duration)
        room.phase = "media_ready"
        room.error_message = ""
    except Exception as exc:
        room.media.status = "error"
        room.media.message = str(exc)
        room.error_message = str(exc)
    room.touch()


@app.post("/api/rooms/{code}/action")
async def room_action(code: str, payload: ActionRequest) -> dict:
    try:
        room = manager.get_room(code)
        manager.authenticate(room, payload.player_id, payload.token, host_only=True)
        action = payload.action
        if action == "play_original":
            if room.media.status != "ready":
                raise RoomError("Prepare um vídeo antes de reproduzir.")
            await manager.broadcast(room, {"type": "play_original", "delay_ms": 900})
        elif action == "open_recording":
            if room.media.status != "ready":
                raise RoomError("Prepare um vídeo antes de abrir as gravações.")
            room.phase = "recording"
            for player in room.players.values():
                player.recording_status = "waiting"
        elif action == "show_ranking":
            if not any(p.score for p in room.players.values()):
                raise RoomError("Ainda não há pontuações nesta rodada.")
            room.phase = "ranking"
        elif action == "new_round":
            room.round_number += 1
            room.phase = "lobby"
            room.media = type(room.media)()
            for player in room.players.values():
                player.ready = False
                player.recording_status = "waiting"
                player.take_path = None
                player.score = None
                player.result_video_path = None
            for item in room.root_dir.iterdir():
                if item.is_file():
                    item.unlink(missing_ok=True)
        else:
            raise RoomError("Ação desconhecida.")
        room.touch()
        await manager.broadcast_state(room)
        return {"ok": True}
    except RoomError as exc:
        raise _error(exc) from exc


@app.post("/api/rooms/{code}/takes")
async def upload_take(
    code: str,
    background_tasks: BackgroundTasks,
    player_id: Annotated[str, Form()],
    token: Annotated[str, Form()],
    file: UploadFile = File(...),
) -> JSONResponse:
    try:
        room = manager.get_room(code)
        player = manager.authenticate(room, player_id, token)
        if room.phase not in {"recording", "ranking"}:
            raise RoomError("O host ainda não abriu as gravações.")
        if room.media.status != "ready" or not room.media.reference_path:
            raise RoomError("A referência da rodada não está pronta.")
        raw = room.root_dir / f"take_{player.id}.webm"
        wav = room.root_dir / f"take_{player.id}.wav"
        result = room.root_dir / f"result_{player.id}.mp4"
        result.unlink(missing_ok=True)
        await _save_upload(file, raw)
        player.recording_status = "analyzing"
        await manager.broadcast_state(room)
        background_tasks.add_task(_analyze_take_task, room.code, player.id, raw, wav)
        return JSONResponse({"ok": True}, status_code=202)
    except (RoomError, MediaError) as exc:
        raise _error(exc) from exc


def _analyze_take_task(code: str, player_id: str, raw: Path, wav: Path) -> None:
    room = manager.rooms.get(code)
    if room is None or player_id not in room.players:
        return
    player = room.players[player_id]
    try:
        assert media_service is not None
        media_service.convert_take(raw, wav, room.media.clip_duration)
        score = compare_audio(room.media.reference_path, wav)  # type: ignore[arg-type]
        if player.score is None:
            player.rounds_played += 1
        else:
            player.total_score -= player.score.total
        player.take_path = wav
        player.score = score
        player.total_score += score.total
        player.recording_status = "done"
        player.result_video_path = None
        room.error_message = ""
    except Exception as exc:
        player.recording_status = "error"
        room.error_message = f"Falha na gravação de {player.name}: {exc}"
    room.touch()


@app.post("/api/rooms/{code}/render")
async def render_result(code: str, payload: RenderRequest, background_tasks: BackgroundTasks) -> JSONResponse:
    try:
        room = manager.get_room(code)
        requester = manager.authenticate(room, payload.player_id, payload.token)
        target = room.players.get(payload.target_player_id)
        if target is None:
            raise RoomError("Jogador não encontrado.")
        if requester.id != target.id and not requester.is_host:
            raise RoomError("Você só pode gerar o próprio resultado.")
        if not target.take_path or not target.take_path.is_file():
            raise RoomError("Esse jogador ainda não enviou uma gravação.")
        target.recording_status = "rendering"
        await manager.broadcast_state(room)
        background_tasks.add_task(_render_task, room.code, target.id)
        return JSONResponse({"ok": True}, status_code=202)
    except RoomError as exc:
        raise _error(exc) from exc


def _render_task(code: str, player_id: str) -> None:
    room = manager.rooms.get(code)
    if room is None or player_id not in room.players:
        return
    player = room.players[player_id]
    try:
        assert media_service is not None and player.take_path is not None
        output = room.root_dir / f"result_{player.id}.mp4"
        player.result_video_path = media_service.render_result(room.media, player.take_path, output)
        player.recording_status = "done"
        room.error_message = ""
    except Exception as exc:
        player.recording_status = "error"
        room.error_message = f"Falha ao gerar o resultado de {player.name}: {exc}"
    room.touch()


@app.get("/api/rooms/{code}/media")
async def get_media(code: str) -> FileResponse:
    try:
        room = manager.get_room(code)
        if not room.media.clip_path or not room.media.clip_path.is_file():
            raise RoomError("Vídeo não encontrado.")
        return FileResponse(room.media.clip_path, media_type="video/mp4", filename="dubshow_original.mp4")
    except RoomError as exc:
        raise _error(exc, 404) from exc


@app.get("/api/rooms/{code}/results/{player_id}")
async def get_result(code: str, player_id: str) -> FileResponse:
    try:
        room = manager.get_room(code)
        player = room.players.get(player_id)
        if not player or not player.result_video_path or not player.result_video_path.is_file():
            raise RoomError("Resultado ainda não foi gerado.")
        return FileResponse(player.result_video_path, media_type="video/mp4", filename=f"DubShow_{player.name}.mp4")
    except RoomError as exc:
        raise _error(exc, 404) from exc


@app.exception_handler(MediaError)
async def media_error_handler(_: Request, exc: MediaError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=400)
