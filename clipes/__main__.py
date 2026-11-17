"""Module entrypoint for `python -m clipes` (`python -m clipes monitor ...` runs the Twitch monitor)."""

import sys


def main(argv: list[str]) -> int:
    if argv[:1] == ["monitor"]:
        from .monitor import main as monitor_main  # keeps ASR/render imports out of the IRC process

        return monitor_main(argv[1:])
    from .cli import main as cli_main

    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
