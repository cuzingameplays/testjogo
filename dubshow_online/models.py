"""Modelos de domínio da versão online do DubShow."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from time import time
from typing import Any


@dataclass(slots=True)
class AudioScore:
    total: float
    timbre: float
    chroma: float
    spectral: float
    rhythm: float
    pitch: float
    duration: float

    def to_dict(self) -> dict[str, float | str]:
        data: dict[str, float | str] = asdict(self)
        data["verdict"] = self.verdict
        return data

    @property
    def verdict(self) -> str:
        if self.total >= 88:
            return "Impressionante — muito próximo do original"
        if self.total >= 74:
            return "Muito parecido"
        if self.total >= 58:
            return "Boa tentativa — parcialmente parecido"
        if self.total >= 40:
            return "Diferente do original"
        return "Bem diferente do original"


@dataclass(slots=True)
class PlayerState:
    id: str
    name: str
    token: str
    is_host: bool = False
    connected: bool = True
    ready: bool = False
    recording_status: str = "waiting"
    take_path: Path | None = None
    score: AudioScore | None = None
    total_score: float = 0.0
    rounds_played: int = 0
    result_video_path: Path | None = None
    joined_at: float = field(default_factory=time)

    def public_dict(self, room_code: str) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "is_host": self.is_host,
            "connected": self.connected,
            "ready": self.ready,
            "recording_status": self.recording_status,
            "score": self.score.to_dict() if self.score else None,
            "total_score": round(self.total_score, 2),
            "rounds_played": self.rounds_played,
            "has_result": bool(self.result_video_path and self.result_video_path.is_file()),
            "result_url": (
                f"/api/rooms/{room_code}/results/{self.id}?v={int(self.result_video_path.stat().st_mtime)}"
                if self.result_video_path and self.result_video_path.is_file()
                else None
            ),
        }


@dataclass(slots=True)
class MediaState:
    status: str = "empty"
    title: str = ""
    source_kind: str = ""
    clip_start: float = 0.0
    clip_duration: float = 12.0
    source_path: Path | None = None
    clip_path: Path | None = None
    reference_path: Path | None = None
    background_path: Path | None = None
    mix_path: Path | None = None
    channel_count: int = 2
    message: str = ""

    def public_dict(self, room_code: str) -> dict[str, Any]:
        return {
            "status": self.status,
            "title": self.title,
            "source_kind": self.source_kind,
            "clip_start": self.clip_start,
            "clip_duration": self.clip_duration,
            "message": self.message,
            "video_url": (
                f"/api/rooms/{room_code}/media?v={int(self.clip_path.stat().st_mtime)}"
                if self.clip_path and self.clip_path.is_file()
                else None
            ),
        }


@dataclass
class RoomState:
    code: str
    host_player_id: str
    max_players: int
    root_dir: Path
    players: dict[str, PlayerState] = field(default_factory=dict)
    media: MediaState = field(default_factory=MediaState)
    phase: str = "lobby"
    round_number: int = 1
    created_at: float = field(default_factory=time)
    last_activity: float = field(default_factory=time)
    error_message: str = ""

    def touch(self) -> None:
        self.last_activity = time()

    def public_dict(self) -> dict[str, Any]:
        ranking = sorted(
            [p for p in self.players.values() if p.score is not None],
            key=lambda p: p.score.total if p.score else 0.0,
            reverse=True,
        )
        return {
            "code": self.code,
            "max_players": self.max_players,
            "phase": self.phase,
            "round_number": self.round_number,
            "players": [p.public_dict(self.code) for p in self.players.values()],
            "media": self.media.public_dict(self.code),
            "ranking": [p.id for p in ranking],
            "error_message": self.error_message,
        }
