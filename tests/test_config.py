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


def test_slots_non_strict_cai_no_padrao(monkeypatch):
    monkeypatch.setenv("CLIPES_SLOTS", "invalido")
    config._avisados.discard("CLIPES_SLOTS")
    assert config.slots(strict=False) == {"twitch": 1, "kick": 1, "youtube": 1, "busca": 1}


def test_config_schema_tem_45_variaveis_e_zero_segredos():
    assert len(config.CONFIG_SCHEMA) == 54
    grupos = {meta["grupo"] for meta in config.CONFIG_SCHEMA.values()}
    assert grupos == {"lives", "render", "ia", "publicacao", "temperatura", "antiflop"}
    for key, meta in config.CONFIG_SCHEMA.items():
        assert not config._is_forbidden_key(key)
        assert "default" in meta
        assert "tipo" in meta
        assert "descricao" in meta


def test_validate_config_payload_aceita_validos_e_omite_vazios():
    payload = {
        "CLIPES_SLOTS": "twitch=2,kick=0,youtube=1,busca=0",
        "CLIPES_CPU_THREADS": "8",
        "CLIPES_MIN_FREE_GB": "10.5",
        "CLIPES_SPLIT_BAR_H": "120",
        "CLIPES_BLUR_BG": "0",
        "CLIPES_HOOK_TOP": True,
        "CLIPES_WHISPER_MODEL": "large-v3-turbo",
        "CLIPES_AGENDA_HORARIOS": "09:00,18:30",
        "CLIPES_TZ": "America/Sao_Paulo",
        "CLIPES_BRAND_NAME": "Canal Teste",
        "CLIPES_BRAND_LOGO": "assets/logo.png",
        "CLIPES_RETENCAO_H": "",
        "CLIPES_BAR": None,
    }
    clean, err = config.validate_config_payload(payload)
    assert err is None
    assert clean == {
        "CLIPES_SLOTS": "twitch=2,kick=0,youtube=1,busca=0",
        "CLIPES_CPU_THREADS": "8",
        "CLIPES_MIN_FREE_GB": "10.5",
        "CLIPES_SPLIT_BAR_H": "120",
        "CLIPES_BLUR_BG": "0",
        "CLIPES_HOOK_TOP": "1",
        "CLIPES_WHISPER_MODEL": "large-v3-turbo",
        "CLIPES_AGENDA_HORARIOS": "09:00,18:30",
        "CLIPES_TZ": "America/Sao_Paulo",
        "CLIPES_BRAND_NAME": "Canal Teste",
        "CLIPES_BRAND_LOGO": "assets/logo.png",
    }
    assert "CLIPES_RETENCAO_H" not in clean
    assert "CLIPES_BAR" not in clean


def test_validate_config_payload_rejeita_segredos_e_desconhecidas():
    segredos = [
        "CLIPES_MONGO_URI",
        "PAINEL_MONGO_URI",
        "PAINEL_SENHA",
        "DISCORD_BOT_TOKEN",
        "DISCORD_GUILD_ID",
        "DISCORD_CHANNEL_ID",
        "DISCORD_ALLOWED_USER_IDS",
        "GEMINI_API_KEYS",
        "GROQ_API_KEY",
        "YOUTUBE_CLIENT_SECRET",
        "GOOGLE_CLIENT_ID",
        "TIKTOK_CLIENT_SECRET",
        "TWITCH_CLIENT_SECRET",
        "QUALQUER_TOKEN_NOVO",
        "VARIAVEL_DESCONHECIDA",
    ]
    for chave in segredos:
        clean, err = config.validate_config_payload({chave: "x"})
        assert clean == {}
        assert err == f"variavel nao permitida: {chave}"


def test_validate_config_payload_rejeita_valores_invalidos():
    invalidos = [
        ("CLIPES_SLOTS", "tiktok=1"),
        ("CLIPES_SLOTS", "twitch=abc"),
        ("CLIPES_CPU_THREADS", "0"),
        ("CLIPES_CPU_THREADS", "65"),
        ("CLIPES_CPU_THREADS", "3.5"),
        ("CLIPES_SPLIT_BAR_H", "121"),  # impar
        ("CLIPES_BAR_H", "999"),
        ("CLIPES_BEAT_S", "1"),
        ("CLIPES_LAYOUT", "inexistente"),
        ("CLIPES_BLUR_BG", "talvez"),
        ("CLIPES_AGENDA_HORARIOS", "25:99"),
        ("CLIPES_TZ", "Mars/Olympus"),
        ("CLIPES_BRAND_LOGO", "../segredo.png"),
        ("CLIPES_BRAND_NAME", "linha1\nlinha2"),
    ]
    for chave, valor in invalidos:
        clean, err = config.validate_config_payload({chave: valor})
        assert clean == {}
        assert err is not None and err.startswith(f"valor invalido para {chave}")


def test_pc_alias_prioridade(monkeypatch, tmp_path):
    monkeypatch.setenv("CLIPES_PC_ALIAS", "PC-PRINCIPAL")
    monkeypatch.setenv("CLIPES_ALIAS", "PC-ANTIGO")
    assert config.pc_alias(tmp_path) == "PC-PRINCIPAL"

    monkeypatch.delenv("CLIPES_PC_ALIAS")
    assert config.pc_alias(tmp_path) == "PC-ANTIGO"

    monkeypatch.delenv("CLIPES_ALIAS")
    wid = config.pc_alias(tmp_path)
    assert wid.startswith("w-")


def test_apply_remote_config_precedencia_restauracao_e_cache(monkeypatch, tmp_path):
    monkeypatch.setitem(config._BOOT_ENV, "CLIPES_WHISPER_MODEL", "small")
    monkeypatch.delitem(config._BOOT_ENV, "CLIPES_LAYOUT", raising=False)
    monkeypatch.setenv("CLIPES_WHISPER_MODEL", "small")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "segredo-local")

    applied = config.apply_remote_config(
        {
            "CLIPES_WHISPER_MODEL": "large-v3-turbo",
            "CLIPES_LAYOUT": "full",
            "DISCORD_BOT_TOKEN": "tentativa-invasao",
            "CLIPES_BAR_H": "999",  # invalido -> ignorado
        },
        out_root=tmp_path,
        alias="PC-1",
    )
    assert applied == {"CLIPES_WHISPER_MODEL": "large-v3-turbo", "CLIPES_LAYOUT": "full"}
    assert config.os.environ["CLIPES_WHISPER_MODEL"] == "large-v3-turbo"
    assert config.os.environ["CLIPES_LAYOUT"] == "full"
    assert config.os.environ["DISCORD_BOT_TOKEN"] == "segredo-local"
    assert "CLIPES_BAR_H" not in config.os.environ

    # Cache salvo e legivel
    assert config.load_cached_remote_config(out_root=tmp_path, alias="PC-1") == applied

    # Limpar override restaura _BOOT_ENV ou remove
    applied2 = config.apply_remote_config({}, out_root=tmp_path, alias="PC-1")
    assert applied2 == {}
    assert config.os.environ["CLIPES_WHISPER_MODEL"] == "small"
    assert "CLIPES_LAYOUT" not in config.os.environ


# ---- ajustes-nov20 T12: grupo temperatura, min<max e padrao 10 MB (VIG-01, VIG-02, DSC-01)

def test_temperatura_defaults_e_faixas_no_schema():
    vigia = config.CONFIG_SCHEMA["CLIPES_VIGIA"]
    assert (vigia["grupo"], vigia["tipo"], vigia["default"]) == ("temperatura", "bool", "1")
    tmax = config.CONFIG_SCHEMA["CLIPES_TEMP_MAX"]
    assert (tmax["grupo"], tmax["tipo"], tmax["default"], tmax["min"], tmax["max"]) == ("temperatura", "int", "85", 60, 100)
    tmin = config.CONFIG_SCHEMA["CLIPES_TEMP_MIN"]
    assert (tmin["grupo"], tmin["tipo"], tmin["default"], tmin["min"], tmin["max"]) == ("temperatura", "int", "75", 40, 95)
    assert config.CONFIG_SCHEMA["CLIPES_DISCORD_MAX_MB"]["default"] == "10"


def test_validate_rejeita_min_maior_ou_igual_max():
    assert config.validate_config_payload({"CLIPES_TEMP_MIN": "80", "CLIPES_TEMP_MAX": "80"})[1] is not None
    assert config.validate_config_payload({"CLIPES_TEMP_MIN": "90", "CLIPES_TEMP_MAX": "85"})[1] is not None


def test_validate_usa_min_padrao_quando_so_max_informado():
    # min padrao = 75: MAX=70 fica abaixo do minimo efetivo
    assert config.validate_config_payload({"CLIPES_TEMP_MAX": "70"})[1] is not None
    assert config.validate_config_payload({"CLIPES_TEMP_MAX": "80"}) == ({"CLIPES_TEMP_MAX": "80"}, None)


def test_validate_faixas_de_temperatura():
    assert config.validate_config_payload({"CLIPES_TEMP_MAX": "101"})[1] is not None
    assert config.validate_config_payload({"CLIPES_TEMP_MAX": "59"})[1] is not None
    assert config.validate_config_payload({"CLIPES_TEMP_MIN": "39"})[1] is not None
    assert config.validate_config_payload({"CLIPES_TEMP_MIN": "96"})[1] is not None
    assert config.validate_config_payload({"CLIPES_VIGIA": "0"}) == ({"CLIPES_VIGIA": "0"}, None)


# ---- ajustes-nov20 T19: worker grava <OUT_DIR>/.vigia com valores efetivos (VIG-03)

def _isolar_config_remota(monkeypatch):
    monkeypatch.setattr(config, "_BOOT_ENV", {})
    monkeypatch.setattr(config, "_APPLIED_REMOTE_KEYS", set())
    for k in ("CLIPES_VIGIA", "CLIPES_TEMP_MAX", "CLIPES_TEMP_MIN"):
        monkeypatch.setenv(k, "")  # registra o undo: apply_remote_config escreve em os.environ direto
        monkeypatch.delenv(k)


def test_vigia_grava_valores_efetivos_do_remoto(monkeypatch, tmp_path):
    _isolar_config_remota(monkeypatch)
    config.apply_remote_config({"CLIPES_TEMP_MAX": "90", "CLIPES_VIGIA": "0"}, out_root=tmp_path, alias="PC-1")
    assert (tmp_path / ".vigia").read_text(encoding="utf-8") == "0 90 75\n"
    assert not (tmp_path / ".vigia.tmp").exists()


def test_vigia_sem_remoto_usa_padroes(monkeypatch, tmp_path):
    _isolar_config_remota(monkeypatch)
    config.apply_remote_config(None, out_root=tmp_path, alias="PC-1")
    assert (tmp_path / ".vigia").read_text(encoding="utf-8") == "1 85 75\n"


def test_vigia_remover_chave_remota_volta_ao_padrao(monkeypatch, tmp_path):
    _isolar_config_remota(monkeypatch)
    config.apply_remote_config({"CLIPES_TEMP_MAX": "90", "CLIPES_VIGIA": "0"}, out_root=tmp_path, alias="PC-1")
    assert (tmp_path / ".vigia").read_text(encoding="utf-8") == "0 90 75\n"
    config.apply_remote_config({}, out_root=tmp_path, alias="PC-1")
    assert (tmp_path / ".vigia").read_text(encoding="utf-8") == "1 85 75\n"


def test_vigia_erro_de_disco_e_ignorado(monkeypatch, tmp_path):
    _isolar_config_remota(monkeypatch)
    arquivo = tmp_path / "nao_e_pasta"
    arquivo.write_text("x", encoding="utf-8")
    applied = config.apply_remote_config({"CLIPES_VIGIA": "0"}, out_root=arquivo, alias="PC-1")
    assert applied == {"CLIPES_VIGIA": "0"}


# ---- antiflop-v2 T1: chaves globais, DUR_MIN < DUR_MAX, config_global (DUR-01, MET-03, MET-06)

def test_antiflop_chaves_globais_e_faixas():
    g = config.chaves_globais()
    esperadas = {
        "CLIPES_GATE", "CLIPES_HOOK_MAX_ATRASO", "CLIPES_DUR_MIN", "CLIPES_DUR_MAX", "CLIPES_CARTAO_FINAL",
        "CLIPES_HASHTAGS", "CLIPES_DEDUP_PHASH", "CLIPES_LAYOUT_FALLBACK", "CLIPES_VISIBILIDADE", "CLIPES_LAYOUT",
    }
    assert g == esperadas
    s = config.CONFIG_SCHEMA
    assert (s["CLIPES_GATE"]["opcoes"], s["CLIPES_GATE"]["default"]) == (["bloquear", "avisar", "off"], "bloquear")
    assert (s["CLIPES_DUR_MIN"]["min"], s["CLIPES_DUR_MIN"]["max"], s["CLIPES_DUR_MIN"]["default"]) == (8, 60, "15")
    assert (s["CLIPES_DUR_MAX"]["min"], s["CLIPES_DUR_MAX"]["max"], s["CLIPES_DUR_MAX"]["default"]) == (15, 120, "45")
    assert s["CLIPES_VISIBILIDADE"]["grupo"] == "publicacao" and s["CLIPES_LAYOUT_FALLBACK"]["grupo"] == "render"
    assert "CLIPES_CPU_THREADS" not in g


def test_dur_min_deve_ser_menor_que_dur_max():
    assert config.validate_config_payload({"CLIPES_DUR_MIN": "30", "CLIPES_DUR_MAX": "30"})[1] is not None
    assert config.validate_config_payload({"CLIPES_DUR_MAX": "15"})[1] is not None  # MIN efetivo 15
    assert config.validate_config_payload({"CLIPES_DUR_MIN": "20", "CLIPES_DUR_MAX": "40"})[1] is None
    assert config.validate_config_payload({"CLIPES_DUR_MIN": "50"})[1] is not None  # MAX efetivo 45
    assert config.validate_config_payload({"CLIPES_GATE": "talvez"})[1] is not None


def test_config_global_le_so_chaves_globais_validas():
    import mongomock

    db = mongomock.MongoClient()["t"]
    assert config.config_global(None) == {}
    assert config.config_global(db) == {}
    db.config_global.insert_one({"_id": "global", "config": {"CLIPES_DUR_MAX": "60", "CLIPES_CPU_THREADS": "4"}})
    assert config.config_global(db) == {"CLIPES_DUR_MAX": "60"}
    db.config_global.update_one({"_id": "global"}, {"$set": {"config": {"CLIPES_GATE": "invalido"}}})
    assert config.config_global(db) == {}
