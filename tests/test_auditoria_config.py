"""Onda 4 (config): env invalida nao derruba, policy/out_dir unicos, flags, retencao."""

import json
from pathlib import Path

from clipes import acompanhar, config, monitor, perfil, publicacao, registro, render


def _limpa(monkeypatch):
    config._avisados.clear()
    for k in ("CLIPES_POLICY_FILE", "OUT_DIR", "CLIPES_LEASE_S", "CLIPES_BEAT_S", "CLIPES_MAX_LIVES",
              "CLIPES_MIN_FREE_GB", "CLIPES_BAR", "CLIPES_BRAND_NAME", "CLIPES_HOOK_TOP", "CLIPES_BLUR_BG"):
        monkeypatch.delenv(k, raising=False)


def test_lease_e_beat_invalidos_nao_derrubam(monkeypatch):
    _limpa(monkeypatch)
    monkeypatch.setenv("CLIPES_LEASE_S", "abc")
    monkeypatch.setenv("CLIPES_BEAT_S", "xyz")
    assert monitor._effective_lease_s() == 90.0
    monkeypatch.setenv("CLIPES_LEASE_S", "-5")
    assert monitor._effective_lease_s() == 90.0


def test_min_free_gb_invalido_usa_padrao(monkeypatch, tmp_path):
    _limpa(monkeypatch)
    from types import SimpleNamespace

    monkeypatch.setenv("CLIPES_MIN_FREE_GB", "muito")
    out = tmp_path / "out"
    out.mkdir()
    sess = acompanhar.LiveSession(
        acompanhar.parse_alvo("gaules"), out, monitor.DiskQueue(out / "queue"), None,
        lambda d, o: {"clips": []}, is_live=lambda u: True, now=lambda: 0.0,
        disk_usage=lambda p: SimpleNamespace(free=1e12), min_free_gb=None,
    )
    assert sess.min_free == 5.0


def test_max_lives_parse_unico(monkeypatch, tmp_path):
    _limpa(monkeypatch)
    for raw, esperado in (("3", 3), ("3.0", 3), ("0", None), ("abc", None)):
        monkeypatch.setenv("CLIPES_MAX_LIVES", raw)
        a = acompanhar.resolve_max_lives(tmp_path, 4, max_lives_fn=lambda *_: 99)
        p = perfil.max_lives(tmp_path, 4)
        if esperado is None:
            assert a == 99 and p == 1
        else:
            assert a == p == esperado


def test_policy_unica_entre_monitor_acompanhar_publicacao(monkeypatch, tmp_path):
    _limpa(monkeypatch)
    out = tmp_path / "out"
    out.mkdir()
    (out / "policy.json").write_text(json.dumps({"blocked": ["xuxa"]}), encoding="utf-8")
    monkeypatch.setenv("OUT_DIR", str(out))
    monkeypatch.chdir(tmp_path)
    assert config.resolve_policy_path(out) == (out / "policy.json").resolve()
    pol = publicacao._resolve_policy(None, None, out)
    assert not pol.permits("xuxa")
    # acompanhar main usa o mesmo caminho: sem --policy-file nao le ./policy.json do cwd
    (tmp_path / "policy.json").write_text(json.dumps({"blocked": ["outro"]}), encoding="utf-8")
    assert config.resolve_policy_path(out, legacy=Path("policy.json")) == (out / "policy.json").resolve()


def test_policy_env_vence_e_legado_so_sem_padrao(monkeypatch, tmp_path):
    _limpa(monkeypatch)
    out = tmp_path / "out"
    out.mkdir()
    legado = tmp_path / "policy.json"
    legado.write_text("{}", encoding="utf-8")
    assert config.resolve_policy_path(out, legacy=legado) == legado.resolve()
    monkeypatch.chdir(tmp_path)
    legado.write_text(json.dumps({"blocked": ["xuxa"]}), encoding="utf-8")
    assert not publicacao._resolve_policy(None, None, out).permits("xuxa")  # via legado (cwd)
    legado.unlink()
    env_pol = tmp_path / "env_policy.json"
    env_pol.write_text(json.dumps({"blocked": ["ana"]}), encoding="utf-8")
    monkeypatch.setenv("CLIPES_POLICY_FILE", str(env_pol))
    assert not publicacao._resolve_policy(None, None, out).permits("ana")  # via CLIPES_POLICY_FILE


def test_out_dir_resolvido_em_publicacao_e_perfil(monkeypatch, tmp_path):
    _limpa(monkeypatch)
    monkeypatch.setenv("OUT_DIR", str(tmp_path / "x"))
    assert publicacao._resolve_quota_path(None, None) == (tmp_path / "x" / ".cotas_publicacao.json").resolve()
    assert perfil._default_out_dir() == (tmp_path / "x").resolve()


def test_flags_render(monkeypatch):
    _limpa(monkeypatch)
    assert render.is_hook_top_enabled() is True
    monkeypatch.setenv("CLIPES_HOOK_TOP", "nao")
    assert render.is_hook_top_enabled() is False
    monkeypatch.setenv("CLIPES_HOOK_TOP", "lixo")
    assert render.is_hook_top_enabled() is True
    assert render.is_blur_bg_enabled() is True
    monkeypatch.setenv("CLIPES_BLUR_BG", "0")
    assert render.is_blur_bg_enabled() is False
    monkeypatch.setenv("CLIPES_BLUR_BG", "sim")
    assert render.is_blur_bg_enabled() is True


def test_bar_tri_estado(monkeypatch):
    _limpa(monkeypatch)
    assert render.bar_config() is None
    monkeypatch.setenv("CLIPES_BRAND_NAME", "canal")
    assert render.bar_config() is not None
    monkeypatch.setenv("CLIPES_BAR", "0")
    assert render.bar_config() is None
    monkeypatch.setenv("CLIPES_BAR", "lixo")
    assert render.bar_config() is not None
    monkeypatch.delenv("CLIPES_BRAND_NAME")
    monkeypatch.setenv("CLIPES_BAR", "1")
    assert render.bar_config() is not None


def test_retencao_invalida_avisa(tmp_path, capsys):
    reg = registro.Registro(tmp_path)
    assert reg.liberar("abc") == []
    assert reg.liberar("-1") == []
    out = capsys.readouterr().out
    assert out.count("CLIPES_RETENCAO_H") == 2
