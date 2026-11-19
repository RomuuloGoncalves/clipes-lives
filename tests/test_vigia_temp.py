import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

VIGIA_PATH = Path(__file__).resolve().parent.parent / "vigia-temp"


def _write_file(path: Path, content: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if mode is not None:
        path.chmod(mode)


def _setup_fake_bin(tmp_path: Path, container_id: str = "cid123") -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    docker_calls = tmp_path / "docker_calls.log"
    _write_file(
        bin_dir / "docker",
        f"""#!/bin/sh
cmd="$1"
shift
if [ "$cmd" = "ps" ]; then
  echo "{container_id}"
  exit 0
fi
echo "$cmd $*" >> "{docker_calls}"
""",
        mode=0o755,
    )
    _write_file(
        bin_dir / "sleep",
        "#!/bin/sh\n/bin/sleep 0.05\n",
        mode=0o755,
    )
    return bin_dir, docker_calls


def _base_env(tmp_path: Path, sysfs_root: Path, bin_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["SYSFS_ROOT"] = str(sysfs_root)
    env["OUT_DIR"] = str(tmp_path / "out")
    env["LOG"] = str(tmp_path / "out" / ".temp.log")
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    return env


def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("timeout aguardando condicao no vigia-temp")


def test_check_le_maior_temp_de_hwmon_cpu_e_ignora_outros(tmp_path):
    sysfs = tmp_path / "sys"
    _write_file(sysfs / "class/hwmon/hwmon0/name", "nvme\n")
    _write_file(sysfs / "class/hwmon/hwmon0/temp1_input", "99000\n")
    _write_file(sysfs / "class/hwmon/hwmon1/name", "BAT0\n")
    _write_file(sysfs / "class/hwmon/hwmon1/temp1_input", "95000\n")
    _write_file(sysfs / "class/hwmon/hwmon2/name", "k10temp\n")
    _write_file(sysfs / "class/hwmon/hwmon2/temp1_input", "72000\n")
    _write_file(sysfs / "class/hwmon/hwmon2/temp2_input", "78000\n")
    _write_file(sysfs / "class/hwmon/hwmon3/name", "coretemp\n")
    _write_file(sysfs / "class/hwmon/hwmon3/temp1_input", "65000\n")

    bin_dir, docker_calls = _setup_fake_bin(tmp_path)
    res = subprocess.run(
        [str(VIGIA_PATH), "--check"],
        env=_base_env(tmp_path, sysfs, bin_dir),
        capture_output=True,
        text=True,
        check=False,
    )

    assert res.returncode == 0
    assert res.stdout.strip() == "78"
    assert not docker_calls.exists()


@pytest.mark.parametrize("chip", ["zenpower", "coretemp", "cpu_thermal"])
def test_check_aceita_chips_hwmon_cpu(tmp_path, chip):
    sysfs = tmp_path / "sys"
    _write_file(sysfs / "class/hwmon/hwmon0/name", f"{chip}\n")
    _write_file(sysfs / "class/hwmon/hwmon0/temp1_input", "64000\n")

    bin_dir, _ = _setup_fake_bin(tmp_path)
    res = subprocess.run(
        [str(VIGIA_PATH), "--check"],
        env=_base_env(tmp_path, sysfs, bin_dir),
        capture_output=True,
        text=True,
        check=False,
    )

    assert res.returncode == 0
    assert res.stdout.strip() == "64"


def test_check_fallback_thermal_zone_sem_hwmon_cpu(tmp_path):
    sysfs = tmp_path / "sys"
    _write_file(sysfs / "class/hwmon/hwmon0/name", "nvme\n")
    _write_file(sysfs / "class/hwmon/hwmon0/temp1_input", "95000\n")
    _write_file(sysfs / "class/thermal/thermal_zone0/temp", "71000\n")

    bin_dir, _ = _setup_fake_bin(tmp_path)
    res = subprocess.run(
        [str(VIGIA_PATH), "--check"],
        env=_base_env(tmp_path, sysfs, bin_dir),
        capture_output=True,
        text=True,
        check=False,
    )

    assert res.returncode == 0
    assert res.stdout.strip() == "71"


def test_sem_sensor_grava_dot_temp_e_log_sem_0c(tmp_path):
    sysfs = tmp_path / "sys"
    _write_file(sysfs / "class/hwmon/hwmon0/name", "nvme\n")
    _write_file(sysfs / "class/hwmon/hwmon0/temp1_input", "52000\n")
    _write_file(sysfs / "class/hwmon/hwmon1/name", "BAT0\n")
    _write_file(sysfs / "class/hwmon/hwmon1/temp1_input", "30000\n")

    bin_dir, docker_calls = _setup_fake_bin(tmp_path)
    env = _base_env(tmp_path, sysfs, bin_dir)

    check_res = subprocess.run(
        [str(VIGIA_PATH), "--check"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert check_res.returncode == 0
    assert check_res.stdout.strip() == "sem-sensor"

    proc = subprocess.Popen([str(VIGIA_PATH)], env=env)
    try:
        temp_file = Path(env["OUT_DIR"]) / ".temp"
        log_file = Path(env["LOG"])
        _wait_until(lambda: temp_file.exists() and log_file.exists())
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)

    assert temp_file.read_text(encoding="utf-8").strip() == "sem-sensor"
    log_text = log_file.read_text(encoding="utf-8")
    assert "sem-sensor" in log_text
    assert "0C" not in log_text
    calls = docker_calls.read_text(encoding="utf-8").splitlines() if docker_calls.exists() else []
    assert not any(line.startswith("pause ") for line in calls)


def test_loop_pausa_a_86_e_retoma_a_74_atualizando_dot_temp(tmp_path):
    sysfs = tmp_path / "sys"
    temp_input = sysfs / "class/hwmon/hwmon0/temp1_input"
    _write_file(sysfs / "class/hwmon/hwmon0/name", "k10temp\n")
    _write_file(temp_input, "86000\n")

    bin_dir, docker_calls = _setup_fake_bin(tmp_path, container_id="cid123")
    env = _base_env(tmp_path, sysfs, bin_dir)
    env["CLIPES_TEMP_MAX"] = "85"
    env["CLIPES_TEMP_MIN"] = "75"

    temp_file = Path(env["OUT_DIR"]) / ".temp"
    log_file = Path(env["LOG"])

    proc = subprocess.Popen([str(VIGIA_PATH)], env=env)
    try:
        _wait_until(
            lambda: docker_calls.exists()
            and "pause cid123" in docker_calls.read_text(encoding="utf-8")
            and temp_file.exists()
            and temp_file.read_text(encoding="utf-8").strip() == "86"
        )

        _write_file(temp_input, "74000\n")
        _wait_until(
            lambda: "unpause cid123" in docker_calls.read_text(encoding="utf-8")
            and temp_file.read_text(encoding="utf-8").strip() == "74"
        )
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)

    lines = docker_calls.read_text(encoding="utf-8").splitlines()
    assert lines.index("pause cid123") < lines.index("unpause cid123")
    log_text = log_file.read_text(encoding="utf-8")
    assert "86C pausado=0" in log_text
    assert "74C pausado=1" in log_text


def test_sensor_que_some_com_conteiner_pausado_despausa(tmp_path):
    sysfs = tmp_path / "sys"
    name_file = sysfs / "class/hwmon/hwmon0/name"
    temp_input = sysfs / "class/hwmon/hwmon0/temp1_input"
    _write_file(name_file, "k10temp\n")
    _write_file(temp_input, "86000\n")

    bin_dir, docker_calls = _setup_fake_bin(tmp_path, container_id="cid123")
    env = _base_env(tmp_path, sysfs, bin_dir)
    env["CLIPES_TEMP_MAX"] = "85"
    env["CLIPES_TEMP_MIN"] = "75"

    temp_file = Path(env["OUT_DIR"]) / ".temp"
    log_file = Path(env["LOG"])

    proc = subprocess.Popen([str(VIGIA_PATH)], env=env)
    try:
        _wait_until(
            lambda: docker_calls.exists()
            and "pause cid123" in docker_calls.read_text(encoding="utf-8")
            and temp_file.exists()
            and temp_file.read_text(encoding="utf-8").strip() == "86"
        )

        # Sensor desaparece enquanto pausado=1.
        temp_input.unlink()
        _wait_until(
            lambda: "unpause cid123" in docker_calls.read_text(encoding="utf-8")
            and temp_file.read_text(encoding="utf-8").strip() == "sem-sensor"
            and "sem-sensor pausado=0" in log_file.read_text(encoding="utf-8")
        )
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)

    lines = docker_calls.read_text(encoding="utf-8").splitlines()
    assert lines.index("pause cid123") < lines.index("unpause cid123")
    log_text = log_file.read_text(encoding="utf-8")
    assert "sem-sensor pausado=1" in log_text
    assert "sem-sensor pausado=0" in log_text


def test_vigia_temp_filtra_somente_service_clipes_sem_pausar_bot(tmp_path):
    sysfs = tmp_path / "sys"
    _write_file(sysfs / "class/hwmon/hwmon0/name", "k10temp\n")
    _write_file(sysfs / "class/hwmon/hwmon0/temp1_input", "88000\n")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    ps_log = tmp_path / "docker_ps.log"
    docker_calls = tmp_path / "docker_calls.log"
    _write_file(
        bin_dir / "docker",
        f"""#!/bin/sh
cmd="$1"
shift
if [ "$cmd" = "ps" ]; then
  echo "$*" >> "{ps_log}"
  case "$*" in
    *"label=com.docker.compose.service=clipes"*)
      echo "cid_clipes"
      ;;
    *)
      echo "cid_clipes cid_bot"
      ;;
  esac
  exit 0
fi
echo "$cmd $*" >> "{docker_calls}"
""",
        mode=0o755,
    )
    _write_file(bin_dir / "sleep", "#!/bin/sh\n/bin/sleep 0.05\n", mode=0o755)

    env = _base_env(tmp_path, sysfs, bin_dir)
    env["CLIPES_TEMP_MAX"] = "85"
    env["CLIPES_TEMP_MIN"] = "75"

    proc = subprocess.Popen([str(VIGIA_PATH)], env=env)
    try:
        _wait_until(
            lambda: docker_calls.exists()
            and "pause cid_clipes" in docker_calls.read_text(encoding="utf-8")
        )
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=5)

    ps_lines = ps_log.read_text(encoding="utf-8").splitlines()
    assert ps_lines
    for line in ps_lines:
        assert "label=com.docker.compose.project=clipes-lives" in line
        assert "label=com.docker.compose.service=clipes" in line
    calls = docker_calls.read_text(encoding="utf-8")
    assert "cid_bot" not in calls
    assert "pause cid_clipes" in calls
    assert "unpause cid_clipes" in calls
