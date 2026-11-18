"""Coordenação entre PCs via MongoDB: lease de dono por live, jobs por janela, heartbeat e comandos.

O Mongo só decide quem grava o quê; nenhum vídeo passa por ele. Nunca loga nem guarda a URI.
"""

import time
from datetime import datetime, timezone

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

ACOES = {"liberar"}  # lista branca de comandos do painel


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
        if got and got.get("owner") not in (None, self.worker_id):
            self.mark_lost(live_id, got["owner"])  # dono anterior caiu: janelas dele não voltam
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

    # ---- jobs ----

    def add_job(self, job_id: str, live_id: str, window: tuple, priority: float) -> bool:
        doc = {"_id": job_id, "live_id": live_id, "window_start": window[0], "window_end": window[1],
               "status": "pending", "owner": self.worker_id, "lease_until": None, "attempts": 0,
               "priority": priority, "result": None, "updated_at": self._dt()}
        try:
            self.db.jobs.insert_one(doc)
        except DuplicateKeyError:
            return False
        return True

    def start_job(self, job_id: str) -> bool:
        # ponytail: AC 8 ("atingir attempts >= 3") lido literal: a 3ª partida já marca failed (2 execuções reais).
        got = self.db.jobs.find_one_and_update(
            {"_id": job_id, "status": {"$in": ["pending", "processing"]},
             "$or": [{"owner": None}, {"owner": self.worker_id}, {"lease_until": {"$lt": self._dt()}}]},
            {"$inc": {"attempts": 1},
             "$set": {"status": "processing", "owner": self.worker_id,
                      "lease_until": self._dt(self.lease_s), "updated_at": self._dt()}},
            return_document=ReturnDocument.AFTER,
        )
        if got is None:
            return False
        if got["attempts"] >= 3:
            self.db.jobs.update_one({"_id": job_id}, {"$set": {"status": "failed", "updated_at": self._dt()}})
            return False
        return True

    def finish_job(self, job_id: str, status: str, result: dict) -> bool:
        res = self.db.jobs.update_one(
            {"_id": job_id, "owner": self.worker_id, "status": "processing"},
            {"$set": {"status": status, "result": result, "updated_at": self._dt()}},
        )
        return res.modified_count == 1

    def mark_lost(self, live_id: str, old_owner: str) -> None:
        self.db.jobs.update_many(
            {"live_id": live_id, "owner": old_owner, "status": {"$in": ["pending", "processing"]}},
            {"$set": {"status": "lost", "updated_at": self._dt()}},
        )

    # ---- heartbeat ----

    def beat(self, alias: str, capacity: dict, job_atual: str | None, max_lives: int, razao: float) -> None:
        now = self._dt()
        self.db.workers.update_one(
            {"_id": self.worker_id},
            {"$set": {"alias": alias, "capacity": capacity, "job_atual": job_atual,
                      "max_lives": max_lives, "razao": razao, "heartbeat_at": now},
             "$push": {"history": {"$each": [{"t": now, "cpu_pct": capacity.get("cpu_pct"),
                                              "temp_c": capacity.get("temp_c")}], "$slice": -180}}},
            upsert=True,
        )

    # ---- comandos do painel ----

    def pending_command(self) -> dict | None:
        """Comando `pending` mais antigo do próprio PC; ação fora da lista branca vira `rejeitado`."""
        while (c := self.db.commands.find_one({"worker_id": self.worker_id, "status": "pending"},
                                              sort=[("criado_em", 1)])) is not None:
            if c.get("acao") in ACOES:
                return c
            self.mark_command(c["_id"], "rejeitado")
        return None

    def mark_command(self, command_id, status: str) -> None:
        fields = {"status": status}
        if status == "feito":
            fields["executado_em"] = self._dt()
        self.db.commands.update_one({"_id": command_id, "worker_id": self.worker_id}, {"$set": fields})
