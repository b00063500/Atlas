"""config.py : le garde-fou contre le blocage d'IP (hook de la session)."""
import pytest

import config
from conftest import ReponseFactice

SITE = "https://www.has-sante.fr/fiche-etablissement/1"
MINIO = "https://minio.data.has-sante.fr/bqss/prod/x.xlsx"


@pytest.fixture(autouse=True)
def compteurs_neufs(monkeypatch):
    monkeypatch.setattr(config, "_erreurs_403_consecutives", 0)
    config._horodatages.clear()
    yield
    config._horodatages.clear()


def test_trois_403_consecutifs_arretent_la_tournee():
    for _ in range(config.SEUIL_403 - 1):
        config._garde_fou(ReponseFactice(403, url=SITE))
    with pytest.raises(config.BlocageIP):
        config._garde_fou(ReponseFactice(403, url=SITE))


def test_un_200_remet_le_compteur_a_zero():
    for _ in range(10):
        config._garde_fou(ReponseFactice(403, url=SITE))
        config._garde_fou(ReponseFactice(200, url=SITE))   # jamais 3 de suite


def test_blocage_ip_n_est_pas_une_request_exception():
    """main attrape RequestException pour continuer la tournée ; BlocageIP
    doit passer au travers et tout arrêter."""
    import requests
    assert not issubclass(config.BlocageIP, requests.RequestException)


def test_minio_n_est_pas_sous_quota():
    for _ in range(5):
        config._garde_fou(ReponseFactice(403, url=MINIO))
    assert len(config._horodatages) == 0


def test_budget_horaire_declenche_une_pause(monkeypatch):
    pauses = []
    monkeypatch.setattr(config.time, "sleep", pauses.append)
    monkeypatch.setattr(config, "BUDGET_HORAIRE", 3)
    for _ in range(3):
        config._garde_fou(ReponseFactice(200, url=SITE))
    assert pauses == []
    config._garde_fou(ReponseFactice(200, url=SITE))       # la 4e de l'heure
    assert len(pauses) == 1 and 0 < pauses[0] <= 3600


def test_delai_par_defaut_est_prudent():
    """1 s ≈ 3 600 req/h, sous le seuil de blocage observé (~5 800/h)."""
    assert config.DELAI >= 1.0
    assert config.BUDGET_HORAIRE < 5800
