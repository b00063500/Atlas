"""Socle des tests : aucun accès réseau, aucune écriture hors du dossier
temporaire de pytest. Les modules sont importés tels quels (imports nus,
comme quand on lance `python pipeline/main.py`)."""
import sys
from pathlib import Path

import pytest
import requests

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "pipeline"))

import config as cfg    # noqa: E402  (après le sys.path ; alias : pytest a son propre `config`)
import db               # noqa: E402
import fiches           # noqa: E402
import opendata_essms   # noqa: E402
import perimetre        # noqa: E402
import telechargement   # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def pytest_addoption(parser):
    parser.addoption("--reseau", action="store_true", default=False,
                     help="exécute aussi les tests qui touchent réellement la HAS (2 requêtes)")


def pytest_collection_modifyitems(config, items):   # `config` : celui de pytest, pas le nôtre
    if config.getoption("--reseau"):
        return
    saut = pytest.mark.skip(reason="réseau désactivé (lancer avec --reseau)")
    for item in items:
        if "reseau" in item.keywords:
            item.add_marker(saut)


# ---- une session HTTP factice ----------------------------------------

class ReponseFactice:
    def __init__(self, status_code=200, content=b"", url="", headers=None):
        self.status_code = status_code
        self.content = content
        self.url = url
        self.headers = headers or {}

    @property
    def text(self):
        return self.content.decode("utf-8", errors="replace")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} pour {self.url}", response=self)


class SessionFactice:
    """`reponses` : {url → ReponseFactice}. Toute URL inconnue → 404.
    `appels` garde la trace de chaque GET (url, en-têtes) pour les assertions."""

    def __init__(self, reponses=None):
        self.reponses = dict(reponses or {})
        self.appels = []

    def get(self, url, headers=None, **_):
        self.appels.append((url, headers or {}))
        reponse = self.reponses.get(url)
        if reponse is None:
            return ReponseFactice(404, b"not found", url)
        if not reponse.url:
            reponse.url = url
        return reponse


@pytest.fixture
def http(monkeypatch):
    """Remplace la session partagée dans TOUS les modules qui l'ont importée
    par `from config import session`, et coupe les pauses de politesse."""
    factice = SessionFactice()
    for module in (cfg, fiches, telechargement):
        monkeypatch.setattr(module, "session", factice)
    monkeypatch.setattr(fiches, "DELAI", 0)
    monkeypatch.setattr(telechargement, "DELAI", 0)
    return factice


@pytest.fixture
def donnees(tmp_path, monkeypatch):
    """Redirige tous les chemins de données vers un dossier temporaire."""
    dossier = tmp_path / "data"
    dossier.mkdir()
    monkeypatch.setattr(cfg, "DONNEES", dossier)
    monkeypatch.setattr(cfg, "DB", dossier / "data.db")
    monkeypatch.setattr(cfg, "INDEX_SANITAIRE", dossier / "criteres-qualiscope-finess.xlsx")
    monkeypatch.setattr(cfg, "INDEX_ESSMS", dossier / "open_data_par_essms.xlsx")
    monkeypatch.setattr(cfg, "DOSSIER_SANITAIRE", dossier / "documents" / "sanitaire")
    monkeypatch.setattr(perimetre, "INDEX_SANITAIRE", cfg.INDEX_SANITAIRE)
    monkeypatch.setattr(opendata_essms, "INDEX_ESSMS", cfg.INDEX_ESSMS)
    monkeypatch.setattr(telechargement, "DOSSIER_SANITAIRE", cfg.DOSSIER_SANITAIRE)
    return dossier


@pytest.fixture
def cx(donnees):
    """Une base neuve, schéma créé, fermée en fin de test."""
    connexion = db.connect(cfg.DB)
    db.init_db(connexion)
    yield connexion
    connexion.close()


# ---- fabriques de fichiers open data ---------------------------------

def ecrire_xlsx(chemin: Path, entete: list[str], lignes: list[list]) -> Path:
    import openpyxl
    classeur = openpyxl.Workbook()
    feuille = classeur.active
    feuille.append(entete)
    for ligne in lignes:
        feuille.append(ligne)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    classeur.save(chemin)
    return chemin


def ligne_essms(**surcharges) -> dict:
    """Une ligne ESSMS complète (toutes les colonnes que le schéma attend),
    avec des valeurs plausibles ; `surcharges` en modifie certaines."""
    import datetime
    ligne = {c: None for c in ("finess_geo", "eval_date_fin")
             + db.COLONNES_ETABLISSEMENT_ESSMS + db.COLONNES_EVALUATION_ESSMS}
    ligne.update({
        "finess_geo": "600106041", "raison_sociale": "FV SOURCE FROISSY",
        "essms_statut_juridique": "Privé commercial", "region_code": "32",
        "region_libelle": "HAUTS-DE-FRANCE", "departement_code": "60",
        "departement_libelle": "OISE", "latitude": 49.57, "longitude": 2.22,
        "essms_categ_finess_code": "382", "essms_categ_finess_libelle": "Foyer de Vie",
        "essms_secteur": "Social", "essms_type_structure": "Etablissement",
        "essms_PA": 0, "essms_PHA": 1, "essms_PHE": 0, "essms_AHI": 0,
        "essms_PDS": 0, "essms_PE/PJJ": 0,
        "eval_code": "EVAL-92005",
        "eval_date_debut": datetime.datetime(2023, 12, 20),
        "eval_date_fin": datetime.datetime(2023, 12, 21),
        "eval_date_cloture_tech": datetime.datetime(2024, 1, 17),
        "multi_essms": False, "oe_nom": "EIRL Formation Conseil",
        "oe_numero_accreditation": "3-2049", "nb_at": 3,
        "cotation_chapitre_1": 3.03, "cotation_chapitre_2": 2.47, "cotation_chapitre_3": 2.62,
        "cotation_thematique_1.1": 4, "cotation_objectif_1.1": 4,
        "nb_criteres_evalues_objectif_1.1": 1, "cotation_critere_imperatif_2.2.1": 3,
        "3.6.2_nc": False, "nb_criteres_hors310": 121, "somme_ponderee_hors310": 301.3,
        "moy_objectifs_hors310": 2.49, "moy_objectifs_100": 47.2, "nb_ci": 17,
        "nb_ci_sup_3_5": 2, "taux_ci_sup_3_5": 11.76, "indice_qualite": "D",
        "nb_ci_atteints": 2,
    })
    ligne.update(surcharges)
    return ligne


def ecrire_xlsx_essms(chemin: Path, lignes: list[dict]) -> Path:
    entete = list(lignes[0].keys())
    return ecrire_xlsx(chemin, entete, [[l[c] for c in entete] for l in lignes])
