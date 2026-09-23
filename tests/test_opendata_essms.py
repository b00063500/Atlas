"""opendata_essms.py : lecture du xlsx et conversion des valeurs."""
import datetime

import pytest

import config
import opendata_essms
from conftest import ReponseFactice, ecrire_xlsx, ecrire_xlsx_essms, ligne_essms

COLONNES = ("finess_geo", "eval_date_fin")


def test_conversions(donnees):
    ecrire_xlsx_essms(config.INDEX_ESSMS, [ligne_essms(oe_nom="   ", multi_essms=True)])
    (ligne,) = list(opendata_essms.lire_evaluations(COLONNES))
    assert ligne["eval_date_fin"] == "2023-12-21"       # datetime → date ISO
    assert ligne["multi_essms"] == 1                    # bool → 0/1
    assert ligne["oe_nom"] is None                      # blanc → NULL
    assert ligne["cotation_chapitre_1"] == 3.03         # nombre inchangé
    assert ligne["finess_geo"] == "600106041"


def test_colonne_renommee_par_la_has_fait_echouer_avant_toute_lecture(donnees):
    ecrire_xlsx(config.INDEX_ESSMS, ["finess_geo", "date_de_fin"], [["1", "2"]])
    with pytest.raises(RuntimeError, match="eval_date_fin"):
        next(opendata_essms.lire_evaluations(COLONNES))


def test_telecharger_index_conditionnel(donnees, http):
    # 1er passage : rien en local → GET sans condition, fichier écrit.
    http.reponses[config.URL_INDEX_ESSMS] = ReponseFactice(200, b"PK-xlsx")
    assert opendata_essms.telecharger_index() is True
    assert config.INDEX_ESSMS.read_bytes() == b"PK-xlsx"
    assert "If-Modified-Since" not in http.appels[0][1]

    # 2e passage : la HAS n'a rien republié → 304, rien réécrit.
    http.reponses[config.URL_INDEX_ESSMS] = ReponseFactice(304)
    assert opendata_essms.telecharger_index() is False
    assert "If-Modified-Since" in http.appels[1][1]
    assert config.INDEX_ESSMS.read_bytes() == b"PK-xlsx"


def test_dates_de_la_ligne_reelle(donnees):
    """Une ligne calquée sur la première ligne du vrai fichier HAS."""
    ecrire_xlsx_essms(config.INDEX_ESSMS, [ligne_essms(
        eval_date_debut=datetime.datetime(2023, 12, 20), eval_date_fin=datetime.datetime(2023, 12, 21))])
    (ligne,) = list(opendata_essms.lire_evaluations(COLONNES))
    assert (ligne["eval_date_debut"], ligne["eval_date_fin"]) == ("2023-12-20", "2023-12-21")
