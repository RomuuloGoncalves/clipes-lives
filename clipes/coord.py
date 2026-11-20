"""Coordenação entre PCs via MongoDB: lease de dono por live, jobs por janela, heartbeat e comandos.

O Mongo só decide quem grava o quê; nenhum vídeo passa por ele. Nunca loga nem guarda a URI.
"""

import os
import re
import time
from datetime import datetime, timezone

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .config import chaves_globais, config_global

ACOES = {"liberar"}  # lista branca de comandos do painel
_SECRET_ENV_HINTS = ("SECRET", "TOKEN", "KEY", "PASS", "URI")
_URI_CREDS_RE = re.compile(r"(https?://)([^/\s@:]+:[^/\s@]+@)")
_MONGO_URI_RE = re.compile(r"mongodb(?:\+srv)?://[^\s,;'\"]+")


def mask_uri(uri: str) -> str:
    """Esconde usuário, senha e host: `mongodb+srv://***@***`."""
    return uri.split("://", 1)[0] + "://***@***" if "://" in uri else "***"


def _sanitize_erro(erro: str | None) -> str | None:
    if erro is None:
        return None
    msg = str(erro)
    msg = _MONGO_URI_RE.sub(lambda m: mask_uri(m.group(0)), msg)
    msg = _URI_CREDS_RE.sub(r"\1***@", msg)
    for name, value in os.environ.items():
        val = value.strip()
        if len(val) >= 6 and (name == "DISCORD_BOT_TOKEN" or any(h in name.upper() for h in _SECRET_ENV_HINTS)):
            msg = msg.replace(val, "***")
    return msg


class Coord:
    def __init__(self, db, worker_id: str, *, now=time.time, lease_s: float = 90):
        self.db, self.worker_id, self._now, self.lease_s = db, worker_id, now, lease_s
        self._avisou_global_pc: set[str] = set()

    def _dt(self, offset: float = 0.0) -> datetime:
        """Instante atual (relógio injetável) como datetime UTC ingênuo, o que o Mongo guarda."""
        return datetime.fromtimestamp(self._now() + offset, timezone.utc).replace(tzinfo=None)

    def ensure_indexes(self) -> None:
        self.db.jobs.create_index([("status", 1), ("priority", -1)])
        self.db.lives.create_index([("status", 1), ("lease_until", 1)])
        self.db.alvos.create_index([("estado", 1), ("tipo", 1), ("plataforma", 1)])
        self.db.commands.create_index([("worker_id", 1), ("status", 1)])
        self.db.commands.create_index(
            [("worker_id", 1)],
            unique=True,
            partialFilterExpression={"status": "pending"},
        )

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

    def release(self, live_id: str, ended: bool, *, discord_client=None) -> None:
        fields = {"owner": None, "lease_until": None}
        if ended:
            fields.update(status="ended", ended_at=self._dt())
        res = self.db.lives.update_one({"_id": live_id, "owner": self.worker_id}, {"$set": fields})
        if ended and res.matched_count == 1:
            from .notificar import encerrar_cartao_live

            encerrar_cartao_live(self.db, live_id, discord_client=discord_client)

    def count_owned_live(self, exclude: "set[str] | list[str] | tuple[str, ...]" = ()) -> int:
        """Lives `live` deste PC com lease ainda válida (as órfãs de um crash não contam no max_lives)."""
        q: dict = {"owner": self.worker_id, "status": "live", "lease_until": {"$gte": self._dt()}}
        if exclude:
            q["_id"] = {"$nin": list(exclude)}
        return self.db.lives.count_documents(q)

    def release_expired_own(self) -> int:
        """Solta as lives deste PC com lease vencida (sobras de kill -9/OOM). Devolve quantas soltou."""
        res = self.db.lives.update_many(
            {"owner": self.worker_id, "status": "live", "lease_until": {"$lt": self._dt()}},
            {"$set": {"owner": None, "lease_until": None}},
        )
        return res.modified_count

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
        now = self._dt()
        owner_filter = {"$or": [{"owner": None}, {"owner": self.worker_id}, {"lease_until": {"$lt": now}}]}
        got = self.db.jobs.find_one_and_update(
            {"_id": job_id, "status": {"$in": ["pending", "processing"]}, "attempts": {"$lt": 3}, **owner_filter},
            {"$inc": {"attempts": 1},
             "$set": {"status": "processing", "owner": self.worker_id,
                      "lease_until": self._dt(self.lease_s), "updated_at": now}},
            return_document=ReturnDocument.AFTER,
        )
        if got is not None:
            return True
        self.db.jobs.update_one(
            {"_id": job_id, "status": {"$in": ["pending", "processing"]}, "attempts": {"$gte": 3}, **owner_filter},
            {"$set": {"status": "failed", "updated_at": now}},
        )
        return False

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

    def mark_job_lost(self, job_id: str) -> None:
        self.db.jobs.update_one(
            {"_id": job_id, "owner": self.worker_id, "status": {"$in": ["pending", "processing"]}},
            {"$set": {"status": "lost", "updated_at": self._dt()}},
        )

    # ---- alvos (lives e VODs) ----

    def own_alvos(self) -> list[dict]:
        """Alvos de live ativos ainda sob lease valida deste PC (retomada pos-reinicio)."""
        return list(
            self.db.alvos.find({
                "owner": self.worker_id,
                "estado": "ativo",
                "tipo": "live",
                "lease_until": {"$gte": self._dt()},
            })
        )

    def claim_alvo(
        self,
        plataforma: str,
        alias: str | None = None,
        *,
        exclude_ids: "set[str] | list[str] | tuple[str, ...]" = (),
    ) -> dict | None:
        """Toma atomicamente um alvo de live ativo da plataforma compatível com `pc_fixo`."""
        now = self._dt()
        lease_until = self._dt(self.lease_s)
        pc_vals = [None] if alias is None else [None, alias]
        q: dict = {
            "estado": "ativo",
            "tipo": "live",
            "plataforma": plataforma,
            "pc_fixo": {"$in": pc_vals},
            "$or": [{"owner": None}, {"lease_until": {"$lt": now}}, {"owner": self.worker_id}],
        }
        if exclude_ids:
            q["_id"] = {"$nin": list(exclude_ids)}
        got = self.db.alvos.find_one_and_update(
            q,
            {"$set": {"owner": self.worker_id, "lease_until": lease_until}},
            return_document=ReturnDocument.BEFORE,
        )
        if got is None:
            return None
        prev_owner = got.get("owner")
        doc = {**got, "owner": self.worker_id, "lease_until": lease_until}
        if prev_owner not in (None, self.worker_id):
            doc["previous_owner"] = prev_owner
        return doc

    def renew_alvos(self, fases_por_id) -> set[str]:
        """Renova `lease_until` (e `fase`, se dict) dos alvos ativos deste PC; devolve os IDs renovados."""
        lease_until = self._dt(self.lease_s)
        renewed: set[str] = set()
        if isinstance(fases_por_id, dict):
            items = list(fases_por_id.items())
        else:
            items = [(aid, None) for aid in fases_por_id]
        for alvo_id, fase in items:
            fields: dict = {"lease_until": lease_until}
            if fase is not None:
                fields["fase"] = fase
            res = self.db.alvos.update_one(
                {"_id": alvo_id, "owner": self.worker_id, "estado": "ativo"},
                {"$set": fields},
            )
            if res.matched_count == 1:
                renewed.add(alvo_id)
        return renewed

    def release_alvo(self, alvo_id: str) -> None:
        """Solta a lease do alvo deste PC, voltando `fase` para `sem vaga` se ainda estiver ativo."""
        res = self.db.alvos.update_one(
            {"_id": alvo_id, "owner": self.worker_id, "estado": "ativo"},
            {"$set": {"owner": None, "lease_until": None, "fase": "sem vaga"}},
        )
        if res.matched_count == 0:
            self.db.alvos.update_one(
                {"_id": alvo_id, "owner": self.worker_id},
                {"$set": {"owner": None, "lease_until": None}},
            )

    def alvos_ativos(self) -> set[str]:
        """Conjunto de chaves/canais de todos os alvos de live ativos (qualquer PC)."""
        chaves: set[str] = set()
        for doc in self.db.alvos.find({"tipo": "live", "estado": "ativo"}):
            aid = doc.get("_id")
            if isinstance(aid, str) and aid:
                chaves.add(aid)
                if ":" in aid:
                    _, resto = aid.split(":", 1)
                    if resto:
                        chaves.add(resto.lower())
            canal = doc.get("canal")
            if isinstance(canal, str) and canal.strip():
                chaves.add(canal.strip().lower())
        return chaves

    def pegar_vod(self, alias: str | None = None) -> dict | None:
        """Toma atomicamente o VOD ativo mais antigo (ordenado por `criado_em` ascendente)."""
        now = self._dt()
        lease_until = self._dt(self.lease_s)
        pc_vals = [None] if alias is None else [None, alias]
        return self.db.alvos.find_one_and_update(
            {
                "estado": "ativo",
                "tipo": "vod",
                "pc_fixo": {"$in": pc_vals},
                "$or": [{"owner": None}, {"lease_until": {"$lt": now}}, {"owner": self.worker_id}],
            },
            {"$set": {"owner": self.worker_id, "lease_until": lease_until, "fase": "processando"}},
            sort=[("criado_em", 1)],
            return_document=ReturnDocument.AFTER,
        )

    def fim_vod(self, alvo_id: str, estado: str, erro: str | None = None) -> bool:
        """Grava estado final (`feito` ou `falhou`) de um VOD deste PC com erro sanitizado."""
        if estado not in ("feito", "falhou"):
            raise ValueError(f"estado de VOD invalido: {estado!r}")
        fase = "concluido" if estado == "feito" else "falhou"
        res = self.db.alvos.update_one(
            {"_id": alvo_id, "tipo": "vod", "owner": self.worker_id, "estado": "ativo"},
            {"$set": {
                "estado": estado,
                "fase": fase,
                "erro": _sanitize_erro(erro),
                "owner": None,
                "lease_until": None,
                "finalizado_em": self._dt(),
            }},
        )
        return res.matched_count == 1

    def promover_para_vod(self, alvo_id: str, url: str) -> str:
        """Promove alvo live (YouTube video_id) deste PC para VOD na fila.

        Retorna:
          - "promovido": atualizado atomicamente in-place para tipo='vod'
          - "duplicado": ja existia VOD ativo para a mesma URL; live marcado 'removido'
          - "ignorado": alvo nao pertence mais a este PC ou nao esta ativo
        """
        # Verifica se ja existe outro VOD ativo para a mesma URL (evita duplicatas)
        existente = self.db.alvos.find_one({
            "tipo": "vod",
            "estado": "ativo",
            "url": url,
            "_id": {"$ne": alvo_id},
        })
        if existente:
            self.db.alvos.update_one(
                {"_id": alvo_id, "tipo": "live", "estado": "ativo", "owner": self.worker_id},
                {"$set": {"estado": "removido", "fase": "cancelado", "owner": None, "lease_until": None}},
            )
            return "duplicado"

        res = self.db.alvos.update_one(
            {"_id": alvo_id, "tipo": "live", "estado": "ativo", "owner": self.worker_id},
            {"$set": {
                "tipo": "vod",
                "url": url,
                "entrada": url,
                "fase": "na fila",
                "owner": None,
                "lease_until": None,
                "erro": None,
                "auto_vod": False,
            }},
        )
        return "promovido" if res.matched_count == 1 else "ignorado"

    # ---- heartbeat e config por alias ----

    def _com_global(self, cfg: dict) -> dict:
        """Config global (`config_global/_id=global`) sempre vence a por-PC nas chaves globais do schema."""
        cfg = dict(cfg)
        globais = chaves_globais()
        for key in [k for k in cfg if k in globais]:
            cfg.pop(key)
            if key not in self._avisou_global_pc:
                self._avisou_global_pc.add(key)
                print(f"[config] {key} por PC ignorado: use a config global do painel", flush=True)
        cfg.update(config_global(self.db))
        return cfg

    def reconcile_alias(self, alias: str) -> dict:
        return self._com_global(self._reconcile_alias(alias))

    def _reconcile_alias(self, alias: str) -> dict:
        """Unifica documentos antigos/pre-criados com o mesmo `alias` em `self.worker_id` e retorna o `config`."""
        alias_clean = (alias or "").strip()
        if not alias_clean:
            doc = self.db.workers.find_one({"_id": self.worker_id}) or {}
            return dict(doc.get("config") or {})

        docs = list(
            self.db.workers.find(
                {"$or": [{"alias": alias_clean}, {"_id": alias_clean}, {"_id": self.worker_id}]}
            )
        )
        if not docs:
            return {}

        def _sort_key(d: dict) -> tuple[datetime, datetime, int]:
            cfg_ts = d.get("config_updated_at")
            hb_ts = d.get("heartbeat_at")
            has_cfg = 1 if isinstance(d.get("config"), dict) and d.get("config") else 0
            return (
                cfg_ts.replace(tzinfo=None) if isinstance(cfg_ts, datetime) else datetime.min,
                hb_ts.replace(tzinfo=None) if isinstance(hb_ts, datetime) else datetime.min,
                has_cfg,
            )

        docs.sort(key=_sort_key)
        newest = docs[-1]
        merged_config = dict(newest.get("config") or {})
        update_fields: dict = {"alias": alias_clean, "config": merged_config}
        if isinstance(newest.get("config_updated_at"), datetime):
            update_fields["config_updated_at"] = newest["config_updated_at"]

        self.db.workers.update_one(
            {"_id": self.worker_id},
            {"$set": update_fields},
            upsert=True,
        )
        self.db.workers.delete_many(
            {"$or": [{"alias": alias_clean}, {"_id": alias_clean}], "_id": {"$ne": self.worker_id}}
        )
        return merged_config

    def beat(self, alias: str, capacity: dict, job_atual: str | None, max_lives: int, razao: float,
             beat_s: float | None = None, *, slots: dict[str, int] | None = None,
             ocupados: dict[str, int] | None = None, fila: dict[str, int] | None = None) -> dict:
        now = self._dt()
        fields: dict = {"alias": alias, "capacity": capacity, "job_atual": job_atual,
                        "max_lives": max_lives, "razao": razao, "heartbeat_at": now}
        if beat_s is not None:
            fields["beat_s"] = float(beat_s)  # o painel calcula `online` com o intervalo real do worker
        if slots is not None:
            fields["slots"] = dict(slots)
        if ocupados is not None:
            fields["ocupados"] = dict(ocupados)
        if fila is not None:
            fields["fila"] = dict(fila)
        doc = self.db.workers.find_one_and_update(
            {"_id": self.worker_id},
            {"$set": fields,
             "$push": {"history": {"$each": [{"t": now, "cpu_pct": capacity.get("cpu_pct"),
                                              "temp_c": capacity.get("temp_c")}], "$slice": -180}}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return self._com_global(dict((doc or {}).get("config") or {}))

    # ---- comandos do painel ----

    def pending_command(self) -> dict | None:
        """Comando `pending` mais antigo do próprio PC; ação fora da lista branca vira `rejeitado`
        e comando com `expira_em` no passado vira `expirado` (não dispara dias depois)."""
        while (c := self.db.commands.find_one({"worker_id": self.worker_id, "status": "pending"},
                                              sort=[("criado_em", 1)])) is not None:
            if c.get("acao") not in ACOES:
                self.mark_command(c["_id"], "rejeitado")
                continue
            exp = c.get("expira_em")
            if isinstance(exp, datetime) and exp.replace(tzinfo=None) < self._dt():
                self.mark_command(c["_id"], "expirado")
                continue
            return c
        return None

    def run_pending_command(self, liberar) -> str | None:
        """Tratamento comum (monitor e acompanhar) do comando do painel.

        Lê o comando pendente; para `liberar`, marca `executando`, chama `liberar()` (parar gravadores,
        matar filhos, devolver o job em andamento) e marca `feito`. Devolve a ação executada ou None.
        O chamador decide como sair (o monitor e o acompanhar saem com SystemExit(75)).
        """
        cmd = self.pending_command()
        if not cmd or cmd.get("acao") != "liberar":
            return None
        self.mark_command(cmd["_id"], "executando")
        liberar()
        self.mark_command(cmd["_id"], "feito")
        return "liberar"

    def mark_command(self, command_id, status: str) -> None:
        fields = {"status": status}
        if status == "feito":
            fields["executado_em"] = self._dt()
        self.db.commands.update_one({"_id": command_id, "worker_id": self.worker_id}, {"$set": fields})
