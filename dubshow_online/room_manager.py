"""Gerenciamento em memória de salas e conexões WebSocket."""

from __future__ import annotations

import asyncio
import secrets
import shutil
import string
from pathlib import Path
from time import time
from typing import Any

from fastapi import WebSocket

from models import PlayerState, RoomState


class RoomError(RuntimeError):
    pass


class RoomManager:
    def __init__(self, runtime_dir: Path) -> None:
        self.runtime_dir = runtime_dir
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.rooms: dict[str, RoomState] = {}
        self.sockets: dict[str, dict[str, WebSocket]] = {}
        self.lock = asyncio.Lock()

    @staticmethod
    def _new_code() -> str:
        alphabet = string.ascii_uppercase + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(6))

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}_{secrets.token_urlsafe(8)}"

    async def create_room(self, player_name: str, max_players: int) -> tuple[RoomState, PlayerState]:
        player_name = self._validate_name(player_name)
        if max_players < 1 or max_players > 5:
            raise RoomError("A sala deve aceitar entre 1 e 5 jogadores.")

        async with self.lock:
            code = self._new_code()
            while code in self.rooms:
                code = self._new_code()
            root = self.runtime_dir / code
            root.mkdir(parents=True, exist_ok=True)
            player = PlayerState(
                id=self._new_id("p"),
                name=player_name,
                token=secrets.token_urlsafe(24),
                is_host=True,
            )
            room = RoomState(
                code=code,
                host_player_id=player.id,
                max_players=max_players,
                root_dir=root,
                players={player.id: player},
            )
            self.rooms[code] = room
            self.sockets[code] = {}
            return room, player

    async def join_room(self, code: str, player_name: str) -> tuple[RoomState, PlayerState]:
        code = code.strip().upper()
        player_name = self._validate_name(player_name)
        async with self.lock:
            room = self.rooms.get(code)
            if room is None:
                raise RoomError("Sala não encontrada ou encerrada.")
            if len(room.players) >= room.max_players:
                raise RoomError("A sala já está cheia.")
            if room.phase not in {"lobby", "media_ready"}:
                raise RoomError("A rodada já começou. Entre na próxima rodada.")
            player = PlayerState(
                id=self._new_id("p"),
                name=player_name,
                token=secrets.token_urlsafe(24),
            )
            room.players[player.id] = player
            room.touch()
            return room, player

    def get_room(self, code: str) -> RoomState:
        room = self.rooms.get(code.strip().upper())
        if room is None:
            raise RoomError("Sala não encontrada ou encerrada.")
        room.touch()
        return room

    def authenticate(self, room: RoomState, player_id: str, token: str, host_only: bool = False) -> PlayerState:
        player = room.players.get(player_id)
        if player is None or not secrets.compare_digest(player.token, token):
            raise RoomError("Sessão inválida. Entre novamente na sala.")
        if host_only and not player.is_host:
            raise RoomError("Apenas o host pode realizar esta ação.")
        return player

    async def connect(self, room: RoomState, player: PlayerState, websocket: WebSocket) -> None:
        await websocket.accept()
        self.sockets.setdefault(room.code, {})[player.id] = websocket
        player.connected = True
        room.touch()
        await self.broadcast_state(room)

    async def disconnect(self, room: RoomState, player_id: str) -> None:
        self.sockets.get(room.code, {}).pop(player_id, None)
        player = room.players.get(player_id)
        if player:
            player.connected = False
        room.touch()
        await self.broadcast_state(room)

    async def broadcast_state(self, room: RoomState) -> None:
        await self.broadcast(room, {"type": "state", "room": room.public_dict()})

    async def broadcast(self, room: RoomState, payload: dict[str, Any]) -> None:
        dead: list[str] = []
        for player_id, socket in list(self.sockets.get(room.code, {}).items()):
            try:
                await socket.send_json(payload)
            except Exception:
                dead.append(player_id)
        for player_id in dead:
            self.sockets.get(room.code, {}).pop(player_id, None)
            if player_id in room.players:
                room.players[player_id].connected = False

    async def cleanup_loop(self, max_idle_seconds: int = 4 * 60 * 60) -> None:
        while True:
            await asyncio.sleep(15 * 60)
            cutoff = time() - max_idle_seconds
            stale = [code for code, room in self.rooms.items() if room.last_activity < cutoff]
            async with self.lock:
                for code in stale:
                    room = self.rooms.pop(code, None)
                    self.sockets.pop(code, None)
                    if room:
                        shutil.rmtree(room.root_dir, ignore_errors=True)

    @staticmethod
    def _validate_name(name: str) -> str:
        clean = " ".join(name.strip().split())
        if len(clean) < 2:
            raise RoomError("Digite um nome com pelo menos 2 caracteres.")
        if len(clean) > 28:
            raise RoomError("O nome pode ter no máximo 28 caracteres.")
        return clean
