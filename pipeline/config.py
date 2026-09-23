"""Infrastructure partagée : chemins, constantes, session HTTP, garde-fou.

Règle d'imports du projet : tout le monde peut importer `config` ;
personne d'autre n'importe personne — sauf `main`, qui importe tout.
"""
import datetime
import os
import time
from collections import deque
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urlparse

import requests

# ---- chemins ---------------------------------------------------------
# Tous les chemins sont ancrés sur CE fichier : peu importe d'où tu lances
# le script, ils pointent toujours au même endroit.
RACINE = Path(__file__).resolve().parent.parent          # has-pipeline/
# Les données (base, xlsx, PDF) vivent hors du code, dans data/ — ignoré par
# git. La variable d'environnement HAS_PIPELINE_DATA permet de les mettre
# ailleurs (un disque dédié, un volume Docker...).
DONNEES = Path(os.environ.get("HAS_PIPELINE_DATA", RACINE / "data"))
INDEX_SANITAIRE = DONNEES / "criteres-qualiscope-finess.xlsx"
INDEX_ESSMS = DONNEES / "open_data_par_essms.xlsx"
DB = DONNEES / "data.db"
DOSSIER_SANITAIRE = DONNEES / "documents" / "sanitaire"

# ---- sources HAS -----------------------------------------------------
SITE = "https://www.has-sante.fr"
URL_INDEX_SANITAIRE = "https://minio.data.has-sante.fr/bqss/prod/criteres-qualiscope-finess.xlsx"
URL_INDEX_ESSMS = ("https://minio.data.has-sante.fr/synae/data/prod/open_data/"
                   "open_data_par_essms.xlsx")

NAVIGATEUR = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# ---- politesse envers www.has-sante.fr -------------------------------
# ⚠️  BLOCAGE D'IP : le 22/08/2026, ~5 800 requêtes en une heure sur
# www.has-sante.fr ont déclenché un blocage de l'adresse IP (403 sur tout
# le site, pendant plusieurs heures). Le blocage est cumulatif : les 1 400
# premières requêtes passaient, puis plus rien. Le crawl des fiches
# (8 987 requêtes pour le sanitaire) est donc à étaler, jamais à paralléliser.
#
# Trois protections, dans cet ordre :
#   1. DELAI : une pause avant CHAQUE requête vers le site (1 s ≈ 3 600/h,
#      soit 62 % du seuil observé). Ne passe pas à 0 pour une tournée complète.
#   2. BUDGET_HORAIRE : si malgré tout on dépasse ce nombre de requêtes sur
#      l'heure glissante, le garde-fou met la tournée en pause jusqu'à repasser
#      sous le seuil.
#   3. SEUIL_403 : trois 403 consécutifs = on est bloqué. On s'arrête net
#      (BlocageIP) plutôt que d'enchaîner des milliers de requêtes inutiles
#      qui prolongeraient le blocage — et qui, côté fiches, passeraient pour
#      « aucun rapport » en silence.
# L'hôte minio.data.has-sante.fr (open data) n'est PAS soumis à ce quota :
# autre serveur, quelques requêtes par tournée, jamais bloqué.
DELAI = 1.0
BUDGET_HORAIRE = 3000
SEUIL_403 = 3


class BlocageIP(Exception):
    """Le site refuse nos requêtes (403 répétés) : l'IP est très probablement
    bloquée. Ce n'est PAS une RequestException, volontairement : main ne doit
    pas la classer comme « une fiche perdue » et continuer — il faut arrêter
    la tournée et attendre plusieurs heures avant de reprendre."""


_horodatages: deque[float] = deque()     # les requêtes de l'heure glissante
_erreurs_403_consecutives = 0


def _est_le_site(url: str) -> bool:
    """Seul www.has-sante.fr est sous quota ; minio (open data) n'y est pas."""
    return urlparse(url).netloc == "www.has-sante.fr"


def _garde_fou(reponse: requests.Response, **_) -> None:
    """Hook `requests` exécuté après chaque réponse de la session.
    Compte les requêtes vers le site, détecte le blocage, freine si besoin."""
    global _erreurs_403_consecutives
    if not _est_le_site(reponse.url):
        return

    if reponse.status_code == 403:
        _erreurs_403_consecutives += 1
        if _erreurs_403_consecutives >= SEUIL_403:
            raise BlocageIP(f"{SEUIL_403} réponses 403 consécutives sur {reponse.url} : "
                            "IP probablement bloquée, tournée interrompue")
    else:
        _erreurs_403_consecutives = 0

    maintenant = time.monotonic()
    _horodatages.append(maintenant)
    while _horodatages and _horodatages[0] < maintenant - 3600:
        _horodatages.popleft()
    if len(_horodatages) > BUDGET_HORAIRE:
        attente = _horodatages[0] + 3600 - maintenant
        print(f"ATTENTION budget horaire atteint ({BUDGET_HORAIRE} requêtes/h) : "
              f"pause de {attente / 60:.0f} min pour ne pas se faire bloquer")
        time.sleep(attente)


session = requests.Session()
session.headers.update({"User-Agent": NAVIGATEUR})
session.hooks["response"].append(_garde_fou)


# ---- open data : téléchargement conditionnel -------------------------

def rafraichir(url: str, chemin: Path, timeout: int = 600) -> bool:
    """Met à jour un fichier open data. Renvoie True si une nouvelle version
    a été écrite, False si le serveur a répondu 304 (« tu as déjà la bonne »).

    Le GET est conditionnel sur la date du fichier local : minio honore
    If-Modified-Since — contrairement aux fiches du site, servies en no-store.
    C'est ce qui rend la tournée mensuelle automatique ET économe : les
    14 Mo ne sont rapatriés que quand la HAS a vraiment republié."""
    entetes = {}
    if chemin.exists():
        mtime = datetime.datetime.fromtimestamp(chemin.stat().st_mtime,
                                                tz=datetime.timezone.utc)
        entetes["If-Modified-Since"] = format_datetime(mtime, usegmt=True)

    reponse = session.get(url, headers=entetes, timeout=timeout)
    if reponse.status_code == 304:
        return False
    reponse.raise_for_status()
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_bytes(reponse.content)
    return True
