from clipes.ingest import out_dir_for


def test_nome_legivel_com_data_e_id(tmp_path):
    d = out_dir_for(tmp_path, "UBv07VGeD24", 'BISTECONE & GABRIEL: "Coxinha" / Podpah #1049', "20261012")
    assert d.name == "2026-10-12 - BISTECONE GABRIEL Coxinha Podpah 1049 [UBv07VGeD24]"
    assert "/" not in d.name and d.parent == tmp_path


def test_sem_data_nem_titulo_usa_so_o_id(tmp_path):
    assert out_dir_for(tmp_path, "abc123", "///", None).name == "abc123"


def test_reaproveita_pasta_existente_pelo_id_mesmo_com_titulo_novo(tmp_path):
    antiga = tmp_path / "2026-10-12 - Titulo antigo [abc123]"
    antiga.mkdir()
    assert out_dir_for(tmp_path, "abc123", "Titulo novo", "20261013") == antiga


def test_reaproveita_pasta_legada_so_com_id(tmp_path):
    (tmp_path / "abc123").mkdir()
    assert out_dir_for(tmp_path, "abc123", "Qualquer", "20261013") == tmp_path / "abc123"


def test_titulo_longo_e_cortado(tmp_path):
    d = out_dir_for(tmp_path, "x1", "a" * 300, None)
    assert len(d.name) <= 70 + len(" [x1]")
