"""Deux requêtes réelles vers la HAS — désactivées par défaut.
    python -m pytest tests/test_reseau.py --reseau
Ce n'est PAS la pipeline : une fiche connue, et un HEAD sur l'open data."""
import pytest

import config
import fiches

pytestmark = pytest.mark.reseau


def test_une_fiche_reelle_cite_des_rapports():
    """La fiche 500020383 (Écalgrain, gérontopsy) cite 4 rapports V2014,
    tous résolus par doXiti — vérifié le 02/09/2026. Si ce test casse,
    c'est le site qui a changé de gabarit (ou l'IP qui est bloquée)."""
    rapports = fiches.rapports_de_la_fiche_sanitaire("500020383")
    assert len(rapports) >= 1
    assert all(r["doc_id"].split("_")[0] in ("p", "c") for r in rapports)
    assert any(r["pdf_url"] and r["pdf_url"].endswith(".pdf") for r in rapports)


def test_open_data_accepte_le_get_conditionnel():
    reponse = config.session.head(config.URL_INDEX_ESSMS, timeout=60)
    assert reponse.status_code == 200
    assert "Last-Modified" in reponse.headers
