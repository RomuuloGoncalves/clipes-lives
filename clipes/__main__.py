"""Module entrypoint for `python -m clipes` (`monitor` and `bench-live` subcommands)."""

import argparse
import os
import sys
from pathlib import Path


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
    if argv[:1] == ["monitor"]:
        from .monitor import main as monitor_main  # keeps ASR/render imports out of the IRC process

        return monitor_main(argv[1:])
    if argv[:1] == ["bench-live"]:
        return _bench_live(argv[1:])
    from .cli import main as cli_main

    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
