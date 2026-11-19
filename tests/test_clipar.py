"""Unit tests for the clipar wrapper script (derived from CAP-01, TEMP-02, CMD-02)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

CLIPAR_SRC = Path(__file__).resolve().parent.parent / "clipar"


def _write_exec(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _prepare_clipar_env(
    tmp_path: Path,
    *,
    nproc_val: int = 16,
    mem_available_kb: int = 12 * 1024 * 1024,
    battery: bool = False,
    vigia_check_output: str = "72",
    docker_exit_codes: list[int] | None = None,
) -> tuple[Path, dict[str, str], Path, Path]:
    app_dir = tmp_path / "app"
    app_dir.mkdir(parents=True, exist_ok=True)
    clipar_copy = app_dir / "clipar"
    shutil.copy2(CLIPAR_SRC, clipar_copy)
    clipar_copy.chmod(0o755)

    _write_exec(
        app_dir / "vigia-temp",
        f"""#!/bin/sh
if [ "${{1:-}}" = "--check" ]; then
  printf '%s\\n' "{vigia_check_output}"
  exit 0
fi
trap 'exit 0' INT TERM
while :; do
  /bin/sleep 0.05
done
""",
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    _write_exec(bin_dir / "nproc", f"#!/bin/sh\necho {nproc_val}\n")

    sleep_log = tmp_path / "sleep.log"
    _write_exec(bin_dir / "sleep", f'#!/bin/sh\necho "$1" >> "{sleep_log}"\n')

    codes_file = tmp_path / "docker_codes.txt"
    codes = docker_exit_codes if docker_exit_codes is not None else [0]
    codes_file.write_text("\n".join(str(c) for c in codes) + "\n", encoding="utf-8")

    docker_log = tmp_path / "docker_runs.log"
    _write_exec(
        bin_dir / "docker",
        f"""#!/bin/sh
if [ "$1" = "ps" ] || [ "$1" = "unpause" ]; then
  exit 0
fi
if [ "$1" = "compose" ]; then
  printf 'ARGS=%s|CPUS=%s|MEM=%s|THREADS=%s\\n' "$*" "${{CLIPES_CPUS:-}}" "${{CLIPES_MEM:-}}" "${{CLIPES_CPU_THREADS:-}}" >> "{docker_log}"
  rc=0
  if [ -s "{codes_file}" ]; then
    rc=$(head -n 1 "{codes_file}")
    tail -n +2 "{codes_file}" > "{codes_file}.tmp" && mv "{codes_file}.tmp" "{codes_file}"
  fi
  exit "$rc"
fi
exit 0
""",
    )

    sysfs = tmp_path / "sys"
    (sysfs / "class" / "power_supply").mkdir(parents=True, exist_ok=True)
    if battery:
        (sysfs / "class" / "power_supply" / "BAT0").mkdir(parents=True, exist_ok=True)

    proc = tmp_path / "proc"
    proc.mkdir(parents=True, exist_ok=True)
    (proc / "meminfo").write_text(
        f"MemTotal:       33554432 kB\nMemAvailable:   {mem_available_kb} kB\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["SYSFS_ROOT"] = str(sysfs)
    env["PROC_ROOT"] = str(proc)
    env["OUT_DIR"] = str(tmp_path / "out")
    env["CLIPES_SECRETS"] = str(tmp_path / "nonexistent_segredos.env")
    for var in ("CLIPES_CPUS", "CLIPES_MEM", "CLIPES_CPU_THREADS", "CLIPES_RESTART_S"):
        env.pop(var, None)

    return clipar_copy, env, docker_log, sleep_log


def test_clipar_auto_profile_battery_and_low_ram(tmp_path):
    clipar_bin, env, docker_log, _ = _prepare_clipar_env(
        tmp_path / "bat",
        nproc_val=12,
        mem_available_kb=12 * 1024 * 1024,
        battery=True,
    )
    res = subprocess.run([str(clipar_bin), "https://twitch.tv/teste"], env=env, capture_output=True, text=True)
    assert res.returncode == 0
    lines = docker_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert "CPUS=3|MEM=7372m|THREADS=3" in lines[0]

    # Low RAM (< 4 GB) without battery also uses nproc // 4
    clipar_low, env_low, log_low, _ = _prepare_clipar_env(
        tmp_path / "lowram",
        nproc_val=12,
        mem_available_kb=3 * 1024 * 1024,
        battery=False,
    )
    res_low = subprocess.run([str(clipar_low), "https://twitch.tv/teste"], env=env_low, capture_output=True, text=True)
    assert res_low.returncode == 0
    lines_low = log_low.read_text(encoding="utf-8").strip().splitlines()
    assert "CPUS=3|MEM=1843m|THREADS=3" in lines_low[0]


def test_clipar_auto_profile_desktop_cap_and_user_overrides(tmp_path):
    clipar_bin, env, docker_log, _ = _prepare_clipar_env(
        tmp_path / "desk12",
        nproc_val=16,
        mem_available_kb=12 * 1024 * 1024,
        battery=False,
    )
    res = subprocess.run([str(clipar_bin), "monitor"], env=env, capture_output=True, text=True)
    assert res.returncode == 0
    line = docker_log.read_text(encoding="utf-8").strip()
    assert "CPUS=8|MEM=7372m|THREADS=8" in line

    # 20 GB free caps CLIPES_MEM at 8 GB (8192m)
    clipar_20, env_20, log_20, _ = _prepare_clipar_env(
        tmp_path / "desk20",
        nproc_val=16,
        mem_available_kb=20 * 1024 * 1024,
        battery=False,
    )
    res_20 = subprocess.run([str(clipar_20), "monitor"], env=env_20, capture_output=True, text=True)
    assert res_20.returncode == 0
    assert "CPUS=8|MEM=8192m|THREADS=8" in log_20.read_text(encoding="utf-8").strip()

    # User-defined CLIPES_CPUS, CLIPES_MEM, CLIPES_CPU_THREADS are never overwritten
    clipar_ovr, env_ovr, log_ovr, _ = _prepare_clipar_env(
        tmp_path / "ovr",
        nproc_val=16,
        mem_available_kb=12 * 1024 * 1024,
        battery=True,
    )
    env_ovr["CLIPES_CPUS"] = "5"
    env_ovr["CLIPES_MEM"] = "4096m"
    env_ovr["CLIPES_CPU_THREADS"] = "2"
    res_ovr = subprocess.run([str(clipar_ovr), "monitor"], env=env_ovr, capture_output=True, text=True)
    assert res_ovr.returncode == 0
    assert "CPUS=5|MEM=4096m|THREADS=2" in log_ovr.read_text(encoding="utf-8").strip()


def test_clipar_sem_sensor_warns_without_changing_limits(tmp_path):
    clipar_bin, env, docker_log, _ = _prepare_clipar_env(
        tmp_path,
        nproc_val=16,
        mem_available_kb=12 * 1024 * 1024,
        battery=False,
        vigia_check_output="sem-sensor",
    )
    res = subprocess.run([str(clipar_bin), "https://twitch.tv/teste"], env=env, capture_output=True, text=True)
    assert res.returncode == 0
    assert "aviso: sem sensor de temperatura; vigia termico desligado" in res.stderr
    assert "CPUS=8|MEM=7372m|THREADS=8" in docker_log.read_text(encoding="utf-8").strip()


def test_clipar_restarts_on_exit_75_and_stops_on_other_exit_code(tmp_path):
    # Exit 75 then 0 -> 2 runs with same arguments after sleeping CLIPES_RESTART_S (default 60)
    clipar_75, env_75, log_75, sleep_log = _prepare_clipar_env(
        tmp_path / "r75",
        docker_exit_codes=[75, 0],
    )
    res_75 = subprocess.run([str(clipar_75), "https://twitch.tv/x", "--ate-acabar"], env=env_75, capture_output=True, text=True)
    assert res_75.returncode == 0
    runs = log_75.read_text(encoding="utf-8").strip().splitlines()
    assert len(runs) == 2
    assert all("ARGS=compose run --rm clipes https://twitch.tv/x --ate-acabar|" in r for r in runs)
    assert sleep_log.read_text(encoding="utf-8").strip().splitlines() == ["60"]

    # Exit 1 -> 1 run and exits with 1
    clipar_1, env_1, log_1, sleep_log_1 = _prepare_clipar_env(
        tmp_path / "r1",
        docker_exit_codes=[1],
    )
    res_1 = subprocess.run([str(clipar_1), "monitor"], env=env_1, capture_output=True, text=True)
    assert res_1.returncode == 1
    assert len(log_1.read_text(encoding="utf-8").strip().splitlines()) == 1
    assert not sleep_log_1.exists()


# ---- controle-discord T8: subcomando servico e comandos antigos removidos -----------------------


def test_entry_main_servico_auth_bot_url_e_comandos_removidos(monkeypatch, capsys):
    from clipes import __main__ as entry, acompanhar, bot, cli

    # 1) servico roteia para acompanhar.main(argv[1:]) com prog "clipes servico"
    seen_srv: list[list[str]] = []
    monkeypatch.setattr(acompanhar, "main", lambda argv: (seen_srv.append(argv), 0)[1])
    assert entry.main(["servico", "--out-dir", "/tmp/o"]) == 0
    assert seen_srv == [["--out-dir", "/tmp/o"]]

    monkeypatch.undo()
    try:
        entry.main(["servico", "--help"])
    except SystemExit as exc:
        assert exc.code == 0
    help_out = capsys.readouterr().out
    assert "clipes servico" in help_out

    # 2) Comandos removidos saem com codigo 2 e mensagem exata
    expected = "removido: use ./clipar servico (e /live adicionar no Discord)"
    for cmd in (
        ["monitor"],
        ["monitor", "--follow", "gaules"],
        ["acompanhar", "gaules"],
        ["bench-live", "/tmp/job"],
    ):
        rc = entry.main(cmd)
        cap = capsys.readouterr()
        assert rc == 2, f"esperado codigo 2 para {cmd}"
        assert expected in (cap.err + cap.out)

    # 3) auth, bot e <url> continuam funcionando normalmente
    seen_auth: list[list[str]] = []
    seen_bot: list[list[str]] = []
    seen_cli: list[list[str]] = []
    monkeypatch.setattr(entry, "_auth", lambda argv: (seen_auth.append(argv), 0)[1])
    monkeypatch.setattr(bot, "main", lambda argv: (seen_bot.append(argv), 0)[1])
    monkeypatch.setattr(cli, "main", lambda argv: (seen_cli.append(argv), 0)[1])

    assert entry.main(["auth", "youtube"]) == 0
    assert seen_auth == [["youtube"]]
    assert entry.main(["bot"]) == 0
    assert seen_bot == [[]]
    assert entry.main(["https://www.twitch.tv/videos/123"]) == 0
    assert seen_cli == [["https://www.twitch.tv/videos/123"]]
