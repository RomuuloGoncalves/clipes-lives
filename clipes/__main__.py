"""Module entrypoint for `python -m clipes` (`servico`, `bot`, `auth`, and `<url>`)."""

import argparse
import os
import sys
from pathlib import Path

REMOVED_CMD_MSG = "removido: use ./clipar servico (e /live adicionar no Discord)"
_REMOVED_CMDS = {"monitor", "acompanhar", "bench-live"}


def _auth(argv: list[str]) -> int:
    from . import oauth

    p = argparse.ArgumentParser(
        prog="clipes auth",
        description="Autoriza o YouTube ou TikTok via OAuth local e salva o token em /config/tokens.json.",
    )
    p.add_argument("plataforma", help="Plataforma OAuth (youtube ou tiktok)")
    p.add_argument(
        "--bind",
        default=None,
        help="Endereco de bind do callback local (padrao: 127.0.0.1; use 0.0.0.0 dentro do Docker)",
    )
    args = p.parse_args(argv)

    plat = args.plataforma.strip().lower()
    if plat not in ("youtube", "tiktok"):
        print(
            f"[auth] erro: plataforma invalida {args.plataforma!r} (use youtube ou tiktok)",
            file=sys.stderr,
            flush=True,
        )
        return 2

    try:
        if args.bind is not None:
            oauth.auth_flow(plat, bind_host=args.bind)
        else:
            oauth.auth_flow(plat)
    except (oauth.OAuthError, ValueError) as exc:
        print(f"[auth] erro: {exc}", file=sys.stderr, flush=True)
        return 2
    return 0


def _bench_live(argv: list[str]) -> int:
    from . import perfil

    p = argparse.ArgumentParser(
        prog="clipes bench-live",
        description="Mede a razão de processamento em uma janela de live gravada (>= 300 s).",
    )
    p.add_argument("job_dir", type=Path, help="Pasta de job com source.mkv (>= 300 s)")
    p.add_argument("--out-dir", default=None, help="Diretório raiz de saída (padrão: OUT_DIR ou ./out)")
    args = p.parse_args(argv)

    out_dir = Path(args.out_dir or os.environ.get("OUT_DIR") or "out").expanduser().resolve()
    try:
        res = perfil.medir(args.job_dir, out_dir=out_dir)
    except (ValueError, FileNotFoundError) as exc:
        print(f"[bench-live] erro: {exc}", file=sys.stderr, flush=True)
        return 2

    lives = perfil.max_lives(out_dir, cpus=res["cpus"])
    print(
        f"[bench-live] razao={res['razao']:.3f} cpus={res['cpus']} max_lives={lives}",
        flush=True,
    )
    return 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["servico"]:
        from .acompanhar import main as acompanhar_main

        return acompanhar_main(argv[1:])
    if argv[:1] and argv[0] in _REMOVED_CMDS:
        print(REMOVED_CMD_MSG, file=sys.stderr, flush=True)
        return 2
    if argv[:1] == ["bot"]:
        from .bot import main as bot_main

        return bot_main(argv[1:])
    if argv[:1] == ["auth"]:
        return _auth(argv[1:])
    from .cli import main as cli_main

    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
