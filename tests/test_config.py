"""clipes/config.py: parse de env tolerante e caminhos unicos (M-19, M-22, B-12, B-13, B-17)."""

from pathlib import Path

from clipes import config


def test_env_float_invalido_cai_no_padrao_e_avisa(monkeypatch, capsys):
    monkeypatch.setenv("X_BEAT", "20s")
    config._avisados.discard("X_BEAT")
    assert config.env_float("X_BEAT", 20.0) == 20.0
    assert "X_BEAT" in capsys.readouterr().out
    monkeypatch.setenv("X_BEAT", "30")
    assert config.env_float("X_BEAT", 20.0) == 30.0
    monkeypatch.setenv("X_BEAT", "-1")
    assert config.env_float("X_BEAT", 20.0, min_value=1) == 20.0
    for bad in ("nan", "inf", "-inf"):
        monkeypatch.setenv("X_BEAT", bad)
        assert config.env_float("X_BEAT", 20.0) == 20.0


def test_env_int_aceita_inteiro_e_rejeita_fracao_e_lixo(monkeypatch):
    monkeypatch.setenv("X_N", "3")
    assert config.env_int("X_N", 1) == 3
    monkeypatch.setenv("X_N", "3.0")
    assert config.env_int("X_N", 1) == 3
    for bad in ("3.5", "tres", "0"):
        monkeypatch.setenv("X_N", bad)
        assert config.env_int("X_N", 1, min_value=1) == 1
    monkeypatch.delenv("X_N")
    assert config.env_int("X_N", 7) == 7


def test_env_flag(monkeypatch):
    for raw, want in (("1", True), ("ON", True), ("sim", True), ("0", False), ("off", False), ("talvez", True)):
        monkeypatch.setenv("X_F", raw)
        assert config.env_flag("X_F", True) is want
    monkeypatch.delenv("X_F")
    assert config.env_flag("X_F", False) is False


def test_resolve_out_dir_expande_e_resolve(monkeypatch, tmp_path):
    monkeypatch.setenv("OUT_DIR", str(tmp_path / "a" / ".." / "b"))
    assert config.resolve_out_dir() == (tmp_path / "b").resolve()
    assert config.resolve_out_dir(tmp_path) == tmp_path.resolve()
    monkeypatch.delenv("OUT_DIR")
    assert config.resolve_out_dir() == Path("out").resolve()


def test_resolve_policy_path_env_out_dir_e_legado(monkeypatch, tmp_path, capsys):
    out = tmp_path / "out"
    out.mkdir()
    legacy = tmp_path / "policy.json"
    monkeypatch.delenv("CLIPES_POLICY_FILE", raising=False)
    assert config.resolve_policy_path(out) == (out / "policy.json").resolve()
    legacy.write_text("{}", encoding="utf-8")
    config._avisados.discard("policy.json:legado")
    assert config.resolve_policy_path(out, legacy=legacy) == legacy.resolve()  # padrao ausente: usa o legado
    assert "usando policy legada" in capsys.readouterr().out
    (out / "policy.json").write_text("{}", encoding="utf-8")
    assert config.resolve_policy_path(out, legacy=legacy) == (out / "policy.json").resolve()
    monkeypatch.setenv("CLIPES_POLICY_FILE", str(tmp_path / "x.json"))
    assert config.resolve_policy_path(out, legacy=legacy) == (tmp_path / "x.json").resolve()


def test_slots_padrao_sem_env_ou_vazia(monkeypatch):
    monkeypatch.delenv("CLIPES_SLOTS", raising=False)
    assert config.slots() == {"twitch": 1, "kick": 1, "youtube": 1, "busca": 1}
    monkeypatch.setenv("CLIPES_SLOTS", "   ")
    assert config.slots() == {"twitch": 1, "kick": 1, "youtube": 1, "busca": 1}


def test_slots_sobrescrita_parcial_completa_e_zero(monkeypatch):
    monkeypatch.setenv("CLIPES_SLOTS", "twitch=2,kick=0,busca=0")
    assert config.slots() == {"twitch": 2, "kick": 0, "youtube": 1, "busca": 0}
    monkeypatch.setenv("CLIPES_SLOTS", " Twitch = 3 , KICK = 1 , youtube = 0 , busca = 2 ")
    assert config.slots() == {"twitch": 3, "kick": 1, "youtube": 0, "busca": 2}


def test_slots_invalido_sai_codigo_2_com_trecho(monkeypatch, capsys):
    import pytest

    casos = [
        ("tiktok=1", "tiktok=1"),
        ("twitch=1,desconhecida=2", "desconhecida=2"),
        ("twitch", "twitch"),
        ("twitch=1.5", "twitch=1.5"),
        ("twitch=-1", "twitch=-1"),
        ("twitch=abc", "twitch=abc"),
        ("twitch=", "twitch="),
        ("twitch=1,,kick=1", ""),
    ]
    for raw, trecho in casos:
        monkeypatch.setenv("CLIPES_SLOTS", raw)
        with pytest.raises(SystemExit) as exc:
            config.slots()
        assert exc.value.code == 2
        captured = capsys.readouterr()
        msg = captured.err + captured.out
        assert f"CLIPES_SLOTS invalido: {trecho}" in msg
