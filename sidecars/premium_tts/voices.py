"""Cloned-voice registry with a consent gate.

Design (borrowed from the user's Voice Forge package):
  - Every reference clip is stored with its SHA-256 and a consent record.
  - A cloned voice is UNUSABLE for synthesis until consent has been
    explicitly acknowledged — uploading alone is not enough.
  - The registry is a single JSON file next to the reference audio so the
    whole voice store is one portable directory.

Layout::

    <data_dir>/premium-voices/
        voices.json          # registry (see _load/_save)
        <voice_id>.wav       # reference clips (original bytes, any format)
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

_LOCK = threading.Lock()

# Reference clips: 1 s of audio is useless, 5 min is abuse — Chatterbox
# conditions well on 5–30 s of clean speech.
MIN_REF_BYTES = 16 * 1024          # ~1 s of 16-bit 16 kHz mono
MAX_REF_BYTES = 25 * 1024 * 1024   # 25 MB

CONSENT_STATEMENT = (
    "I confirm that I am the speaker in this recording, or that I have the "
    "speaker's explicit permission to clone their voice, and that generated "
    "audio will not be used to impersonate anyone without their consent."
)


@dataclass
class ConsentRecord:
    acknowledged: bool = False
    statement: str = ""
    acknowledged_at: float | None = None


@dataclass
class ClonedVoice:
    id: str
    name: str
    file: str                    # filename inside the store dir
    sha256: str
    size_bytes: int
    created_at: float
    consent: ConsentRecord = field(default_factory=ConsentRecord)

    @property
    def usable(self) -> bool:
        return bool(self.consent.acknowledged)

    def public(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "created_at": self.created_at,
            "consent_acknowledged": self.consent.acknowledged,
            "consent_acknowledged_at": self.consent.acknowledged_at,
            "usable": self.usable,
        }


class VoiceStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._registry = self.root / "voices.json"

    # ── registry IO ──────────────────────────────────────────────────────────

    def _load(self) -> dict[str, ClonedVoice]:
        try:
            raw = json.loads(self._registry.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        out: dict[str, ClonedVoice] = {}
        for vid, rec in raw.items():
            try:
                consent = ConsentRecord(**rec.pop("consent", {}))
                out[vid] = ClonedVoice(consent=consent, **rec)
            except TypeError:
                continue  # corrupt entry — skip, never crash the sidecar
        return out

    def _save(self, voices: dict[str, ClonedVoice]) -> None:
        payload = {vid: asdict(v) for vid, v in voices.items()}
        tmp = self._registry.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self._registry)

    # ── public API ───────────────────────────────────────────────────────────

    def list(self) -> list[dict]:
        with _LOCK:
            voices = self._load()
        return [v.public() for v in sorted(voices.values(), key=lambda v: v.created_at)]

    def get(self, vid: str) -> ClonedVoice | None:
        with _LOCK:
            return self._load().get(vid)

    def ref_path(self, voice: ClonedVoice) -> Path:
        return self.root / voice.file

    def create(
        self,
        name: str,
        audio: bytes,
        *,
        consent_ack: bool,
        consent_statement: str,
    ) -> ClonedVoice:
        if len(audio) < MIN_REF_BYTES:
            raise ValueError("reference clip too short — record at least a few seconds of clean speech")
        if len(audio) > MAX_REF_BYTES:
            raise ValueError("reference clip too large (max 25 MB)")
        name = re.sub(r"\s+", " ", name).strip()[:80]
        if not name:
            raise ValueError("voice name must not be empty")
        sha = hashlib.sha256(audio).hexdigest()
        vid = uuid.uuid4().hex[:12]
        fname = f"{vid}.ref"
        consent = ConsentRecord(
            acknowledged=bool(consent_ack),
            statement=(consent_statement or CONSENT_STATEMENT) if consent_ack else "",
            acknowledged_at=time.time() if consent_ack else None,
        )
        voice = ClonedVoice(
            id=vid, name=name, file=fname, sha256=sha,
            size_bytes=len(audio), created_at=time.time(), consent=consent,
        )
        with _LOCK:
            voices = self._load()
            # Same clip already registered → refuse the duplicate loudly.
            for v in voices.values():
                if v.sha256 == sha:
                    raise ValueError(f"this clip is already registered as voice '{v.name}'")
            (self.root / fname).write_bytes(audio)
            voices[vid] = voice
            self._save(voices)
        return voice

    def acknowledge_consent(self, vid: str, statement: str = "") -> ClonedVoice:
        with _LOCK:
            voices = self._load()
            voice = voices.get(vid)
            if voice is None:
                raise KeyError(vid)
            voice.consent = ConsentRecord(
                acknowledged=True,
                statement=statement or CONSENT_STATEMENT,
                acknowledged_at=time.time(),
            )
            self._save(voices)
        return voice

    def delete(self, vid: str) -> bool:
        with _LOCK:
            voices = self._load()
            voice = voices.pop(vid, None)
            if voice is None:
                return False
            try:
                (self.root / voice.file).unlink(missing_ok=True)
            except OSError:
                pass
            self._save(voices)
        return True
