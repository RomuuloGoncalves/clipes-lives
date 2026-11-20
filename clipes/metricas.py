"""CLI `metricas registrar|importar`: grava metricas.<plataforma> no Registro."""

import argparse
import csv
import io
import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

from .registro import Registro, _read_json

PLATAFORMAS = ("youtube", "tiktok")

# campo -> apelidos (normalizados: minusculo, sem acento, so alfanumerico)
APELIDOS: dict[str, tuple[str, ...]] = {
    "id": ("id", "videoid", "idvideo", "contentid", "conteudo", "video", "url", "link", "videourl"),
    "views": ("views", "visualizacoes", "visualizacao", "videoviews", "exibicoes", "plays"),
    "tempo_medio_s": (
        "tempomedio", "tempomediodevisualizacao", "duracaomediadavisualizacao", "averageviewduration",
        "avgviewduration", "averagewatchtime", "tempomediodeassistencia", "tempomediodeexibicao",
    ),
    "completo_pct": (
        "completo", "porcentagemmediavisualizada", "averagepercentageviewed", "watchedfullvideo",
        "assistiuovideocompleto", "completionrate", "taxadeconclusao",
    ),
    "curtidas": ("curtidas", "likes", "gostei", "likesmais"),
    "compartilhamentos": ("compartilhamentos", "shares", "compartilhados"),
    "comentarios": ("comentarios", "comments"),
}
_FLAGS = {"id": "col_id", "views": "col_views", "tempo_medio_s": "col_tempo",
          "completo_pct": "col_completo", "curtidas": "col_curtidas",
          "compartilhamentos": "col_compartilhamentos", "comentarios": "col_comentarios"}
_INTS = {"views", "curtidas", "compartilhamentos", "comentarios"}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


def parse_num(raw: Any) -> float | None:
    """'1.234,5' / '1,234.5' / '45,5%' / '1.234' (milhar) -> float; vazio/invalido -> None."""
    s = re.sub(r"[%\s]", "", str(raw or ""))
    if not s:
        return None
    if "," in s and "." in s:
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        s = s.replace("." if dec == "," else ",", "").replace(dec, ".")
    elif "," in s or "." in s:
        sep = "," if "," in s else "."
        partes = s.split(sep)
        if len(partes) > 2 or (len(partes) == 2 and len(partes[1]) == 3 and partes[0] not in ("", "0")):
            s = "".join(partes)  # separador de milhar
        else:
            s = s.replace(sep, ".")
    try:
        return float(s)
    except ValueError:
        return None


def _num(campo: str, raw: Any) -> int | float | None:
    v = parse_num(raw)
    if v is None:
        return None
    return int(round(v)) if campo in _INTS else v


def _ler_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    texto = path.read_text(encoding="utf-8-sig")
    try:
        delim = csv.Sniffer().sniff(texto[:4096], delimiters=",;").delimiter
    except csv.Error:
        delim = ";" if texto.split("\n", 1)[0].count(";") > texto.split("\n", 1)[0].count(",") else ","
    rd = csv.DictReader(io.StringIO(texto), delimiter=delim)
    return list(rd.fieldnames or []), [r for r in rd]


def _resolver_colunas(cab: list[str], over: dict[str, str | None]) -> dict[str, str]:
    por_norm = {_norm(c): c for c in cab}
    out: dict[str, str] = {}
    for campo, apelidos in APELIDOS.items():
        if over.get(campo):
            if over[campo] not in cab:
                raise ValueError(f"coluna {over[campo]!r} nao existe no CSV (cabecalhos: {cab})")
            out[campo] = over[campo]  # type: ignore[assignment]
            continue
        for a in apelidos:
            if a in por_norm:
                out[campo] = por_norm[a]
                break
    return out


def _casa(vid: str, pub: dict[str, Any]) -> bool:
    pid, url = str(pub.get("id") or ""), str(pub.get("url") or "")
    return bool(vid) and (vid in (pid, url) or (pid and pid in vid) or (url and url in vid))


def importar(reg: Registro, caminho: Path, plataforma: str, over: dict[str, str | None] | None = None) -> tuple[int, list[str]]:
    cab, linhas = _ler_csv(caminho)
    cols = _resolver_colunas(cab, over or {})
    if "id" not in cols:
        raise ValueError(f"coluna de id nao encontrada (cabecalhos: {cab}); use --col-id")
    data = _read_json(reg.path, {}) or {}
    clips = data.get("clips") if isinstance(data.get("clips"), dict) else {}
    casadas, nao = 0, []
    for ln in linhas:
        vid = (ln.get(cols["id"]) or "").strip()
        cid = next((c for c, r in clips.items()
                    if isinstance(r, dict) and _casa(vid, (r.get("publicacoes") or {}).get(plataforma) or {})), None)
        if cid is None:
            nao.append(vid)
            continue
        dados = {c: v for c in cols if c != "id" and (v := _num(c, ln.get(cols[c]))) is not None}
        reg.registrar_metricas(cid, plataforma, dados, "csv")
        casadas += 1
    return casadas, nao


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="clipes metricas", description="Registra metricas pos-publicacao.")
    p.add_argument("--out-dir", default=None, help="Diretorio raiz de saida (padrao: OUT_DIR ou ./out)")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("registrar", help="Grava metricas manualmente para um clip_id")
    r.add_argument("clip_id")
    r.add_argument("--plataforma", required=True, choices=PLATAFORMAS)
    r.add_argument("--views", type=int, required=True)
    r.add_argument("--tempo-medio", type=float, dest="tempo_medio_s")
    r.add_argument("--completo", type=float, dest="completo_pct")
    r.add_argument("--curtidas", type=int)
    r.add_argument("--compartilhamentos", type=int)
    i = sub.add_parser("importar", help="Importa metricas de um CSV exportado do Studio")
    i.add_argument("csv", type=Path)
    i.add_argument("--plataforma", required=True, choices=PLATAFORMAS)
    for campo, flag in _FLAGS.items():
        i.add_argument("--" + flag.replace("_", "-"), dest=flag, default=None)
    a = p.parse_args(argv)

    reg = Registro(Path(a.out_dir or os.environ.get("OUT_DIR") or "out").expanduser().resolve())
    if a.cmd == "registrar":
        dados = {k: getattr(a, k) for k in ("views", "tempo_medio_s", "completo_pct", "curtidas", "compartilhamentos")
                 if getattr(a, k) is not None}
        if reg.registrar_metricas(a.clip_id, a.plataforma, dados, "manual") is None:
            print(f"[metricas] erro: clip {a.clip_id!r} nao encontrado", file=sys.stderr, flush=True)
            return 2
        print(f"[metricas] {a.clip_id} {a.plataforma}: {dados}", flush=True)
        return 0
    try:
        casadas, nao = importar(reg, a.csv, a.plataforma, {c: getattr(a, f) for c, f in _FLAGS.items()})
    except (OSError, ValueError) as exc:
        print(f"[metricas] erro: {exc}", file=sys.stderr, flush=True)
        return 2
    print(f"[metricas] casadas={casadas} nao_casadas={len(nao)}", flush=True)
    for v in nao:
        print(f"[metricas] sem casamento: {v}", flush=True)
    return 0
