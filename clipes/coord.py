"""Coordenação entre PCs via MongoDB: lease de dono por live, jobs por janela, heartbeat e comandos.

O Mongo só decide quem grava o quê; nenhum vídeo passa por ele. Nunca loga nem guarda a URI.
"""

import time
from datetime import datetime, timezone

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError


def mask_uri(uri: str) -> str:
    """Esconde usuário, senha e host: `mongodb+srv://***@***`."""
    return uri.split("://", 1)[0] + "://***@***" if "://" in uri else "***"


class Coord:
    def __init__(self, db, worker_id: str, *, now=time.time, lease_s: float = 90):
        self.db, self.worker_id, self._now, self.lease_s = db, worker_id, now, lease_s

    def _dt(self, offset: float = 0.0) -> datetime:
        """Instante atual (relógio injetável) como datetime UTC ingênuo, o que o Mongo guarda."""
        return datetime.fromtimestamp(self._now() + offset, timezone.utc).replace(tzinfo=None)

    def ensure_indexes(self) -> None:
        self.db.jobs.create_index([("status", 1), ("priority", -1)])
        self.db.lives.create_index([("status", 1), ("lease_until", 1)])
        self.db.commands.create_index([("worker_id", 1), ("status", 1)])

    def ensure_live(self, live_id: str, **meta) -> None:
        doc = {"status": "live", "owner": None, "lease_until": None, "created_at": self._dt(), "ended_at": None, **meta}
        try:
            self.db.lives.update_one({"_id": live_id}, {"$setOnInsert": doc}, upsert=True)
        except DuplicateKeyError:
            pass  # outro PC criou no mesmo instante: já existe

    def claim_live(self, live_id: str) -> bool:
        now = self._dt()
        got = self.db.lives.find_one_and_update(
            {
                "_id": live_id,
                "status": "live",
                "$or": [{"owner": None}, {"lease_until": {"$lt": now}}, {"owner": self.worker_id}],
            },
            {"$set": {"owner": self.worker_id, "lease_until": self._dt(self.lease_s)}},
            return_document=ReturnDocument.BEFORE,
        )
        return got is not None

    def renew(self, live_id: str) -> bool:
        res = self.db.lives.update_one(
            {"_id": live_id, "owner": self.worker_id}, {"$set": {"lease_until": self._dt(self.lease_s)}}
        )
        return res.matched_count == 1

    def release(self, live_id: str, ended: bool) -> None:
        fields = {"owner": None, "lease_until": None}
        if ended:
            fields.update(status="ended", ended_at=self._dt())
        self.db.lives.update_one({"_id": live_id, "owner": self.worker_id}, {"$set": fields})
