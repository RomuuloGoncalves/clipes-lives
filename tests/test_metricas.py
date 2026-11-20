import json

import pytest

from clipes import metricas
from clipes.registro import Registro


def _reg(tmp_path):
    reg = Registro(tmp_path)
    clips = {
        "c1": {"publicacoes": {"youtube": {"id": "YT111", "url": "https://youtu.be/YT111"},
                               "tiktok": {"id": "", "url": "https://tiktok.com/@u/video/777"}}},
        "c2": {"publicacoes": {"youtube": {"id": "YT222", "url": "https://youtu.be/YT222"}}},
    }
    (tmp_path / "registro.json").write_text(json.dumps({"version": 1, "clips": clips}), encoding="utf-8")
    return reg


def _met(tmp_path, cid):
    return json.loads((tmp_path / "registro.json").read_text())["clips"][cid].get("metricas", {})


def test_csv_pt_bom_ponto_e_virgula(tmp_path):
    reg = _reg(tmp_path)
    f = tmp_path / "pt.csv"
    f.write_text(
        "Conteúdo;Visualizações;Tempo médio de visualização;Porcentagem média visualizada;Curtidas\n"
        "YT111;1.234;12,5;45,5%;10\nZZZ999;5;1;1%;0\n",
        encoding="utf-8-sig",
    )
    casadas, nao = metricas.importar(reg, f, "youtube")
    assert casadas == 1 and nao == ["ZZZ999"]
    m = _met(tmp_path, "c1")["youtube"]
    assert m["views"] == 1234 and m["tempo_medio_s"] == 12.5 and m["completo_pct"] == 45.5
    assert m["curtidas"] == 10 and m["fonte"] == "csv"
    assert "metricas" not in json.loads((tmp_path / "registro.json").read_text())["clips"]["c2"]


def test_csv_en_virgula_e_url(tmp_path):
    reg = _reg(tmp_path)
    f = tmp_path / "en.csv"
    f.write_text('Video URL,Views,Average view duration,Shares\n"https://tiktok.com/@u/video/777","1,234.5",3.2,7\n', encoding="utf-8")
    casadas, nao = metricas.importar(reg, f, "tiktok")
    assert (casadas, nao) == (1, [])
    m = _met(tmp_path, "c1")["tiktok"]
    assert m["views"] == 1234 and m["tempo_medio_s"] == 3.2 and m["compartilhamentos"] == 7


def test_col_override(tmp_path):
    reg = _reg(tmp_path)
    f = tmp_path / "x.csv"
    f.write_text("codigo,qtd\nYT222,99\n", encoding="utf-8")
    assert metricas.importar(reg, f, "youtube", {"id": "codigo", "views": "qtd"}) == (1, [])
    assert _met(tmp_path, "c2")["youtube"]["views"] == 99


def test_sem_coluna_id(tmp_path):
    f = tmp_path / "x.csv"
    f.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError):
        metricas.importar(_reg(tmp_path), f, "youtube")


def test_registrar_nao_apaga_outra_plataforma(tmp_path, capsys):
    _reg(tmp_path)
    base = ["--out-dir", str(tmp_path), "registrar", "c1"]
    assert metricas.main(base + ["--plataforma", "youtube", "--views", "100"]) == 0
    assert metricas.main(base + ["--plataforma", "tiktok", "--views", "50", "--completo", "30"]) == 0
    m = _met(tmp_path, "c1")
    assert m["youtube"]["views"] == 100 and m["youtube"]["fonte"] == "manual"
    assert m["tiktok"]["views"] == 50 and m["tiktok"]["completo_pct"] == 30 and "atualizado_em" in m["tiktok"]
    assert metricas.main(["--out-dir", str(tmp_path), "registrar", "nope", "--plataforma", "youtube", "--views", "1"]) == 2


def test_despacho_main(tmp_path):
    from clipes.__main__ import main
    _reg(tmp_path)
    assert main(["metricas", "--out-dir", str(tmp_path), "registrar", "c1", "--plataforma", "youtube", "--views", "1"]) == 0
