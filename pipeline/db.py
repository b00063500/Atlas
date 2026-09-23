"""Connexion, schéma et insertions. Toutes les fonctions reçoivent la connexion :
c'est l'appelant (main) qui ouvre, committe (via `with cx:`) et ferme."""
import sqlite3
from collections.abc import Iterable
from pathlib import Path

import config


def connect(chemin: Path = config.DB) -> sqlite3.Connection:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    cx = sqlite3.connect(chemin)
    cx.row_factory = sqlite3.Row          # lignes lisibles par clé : r["doc_id"]
    cx.execute("PRAGMA foreign_keys = ON")  # pas de lignes orphelines
    cx.execute("PRAGMA journal_mode = WAL")  # un lecteur ne bloque pas l'écrivain
    return cx


# ---- schéma sanitaire ------------------------------------------------
# La table des rapports sanitaires s'appelle `rapports` : la renommer
# exigerait une migration de la base déjà remplie — le suffixe du secteur
# ne vit que dans les noms de fonctions.

SCHEMA_ETABLISSEMENTS_SANITAIRES = """
CREATE TABLE IF NOT EXISTS etablissements_sanitaires (
    finess          TEXT NOT NULL PRIMARY KEY
) STRICT;
"""

SCHEMA_RAPPORTS_SANITAIRES = """
CREATE TABLE IF NOT EXISTS rapports (
    doc_id          TEXT NOT NULL PRIMARY KEY,
    slug            TEXT,
    pdf_url         TEXT,
    date_chargement TEXT NOT NULL DEFAULT (datetime('now'))
) STRICT;
"""

SCHEMA_LIEN_EVALUATION_ETABLISSEMENT = """
CREATE TABLE IF NOT EXISTS lien_evaluation_etablissement_sanitaire (
    doc_id  TEXT NOT NULL REFERENCES rapports(doc_id),
    finess  TEXT NOT NULL REFERENCES etablissements_sanitaires(finess),
    PRIMARY KEY (doc_id, finess)
) STRICT;
"""

SCHEMA_INVENTAIRE_SANITAIRE = """
CREATE TABLE IF NOT EXISTS inventaire_sanitaire (
    doc_id              TEXT NOT NULL PRIMARY KEY,
    date_telechargement TEXT NOT NULL DEFAULT (datetime('now'))
) STRICT;
"""

# ---- schéma ESSMS ----------------------------------------------------
# L'open data ESSMS contient TOUT (identité + cotations) : pas de catalogue
# de rapports ni d'inventaire de PDF — deux tables suffisent, remplies
# directement depuis le xlsx.
#
# Ces listes de colonnes sont LA référence : le CREATE TABLE et l'INSERT
# en dérivent tous les deux, ils ne peuvent donc pas diverger. Et
# `opendata_essms.lire_evaluations` vérifie que le xlsx les contient
# toujours avant d'insérer quoi que ce soit.

COLONNES_ETABLISSEMENT_ESSMS = (
    "raison_sociale", "essms_statut_juridique",
    "region_code", "region_libelle", "departement_code", "departement_libelle",
    "latitude", "longitude",
    "essms_categ_finess_code", "essms_categ_finess_libelle",
    "essms_secteur", "essms_type_structure",
    # Les six publics accueillis (0/1) : personnes âgées, handicap adulte et
    # enfant, accueil-hébergement-insertion, protection de l'enfance...
    "essms_PA", "essms_PHA", "essms_PHE", "essms_AHI", "essms_PDS", "essms_PE/PJJ",
)

# Les 18 critères impératifs du référentiel — numérotation irrégulière,
# donc énumérés plutôt que générés.
CRITERES_IMPERATIFS = (
    "2.2.1", "2.2.2", "2.2.3", "2.2.4", "2.2.5", "2.2.6", "2.2.7",
    "3.6.2",
    "3.11.1", "3.11.2",
    "3.12.1", "3.12.2", "3.12.3",
    "3.13.1", "3.13.2", "3.13.3",
    "3.14.1", "3.14.2",
)

# Le référentiel est régulier : 7 thématiques au chapitre 1, 7 au 2, 8 au 3 ;
# 17, 10 et 15 objectifs. Les noms sont générés depuis ces bornes.
_THEMATIQUES = ((1, 7), (2, 7), (3, 8))
_OBJECTIFS = ((1, 17), (2, 10), (3, 15))

COLONNES_EVALUATION_ESSMS = (
    "eval_code", "eval_date_debut", "eval_date_cloture_tech", "multi_essms",
    "oe_nom", "oe_numero_accreditation", "nb_at",
    "cotation_chapitre_1", "cotation_chapitre_2", "cotation_chapitre_3",
    *(f"cotation_thematique_{ch}.{n}"
      for ch, nb in _THEMATIQUES for n in range(1, nb + 1)),
    *(f"cotation_globale_thematique_0.{n}" for n in range(1, 10)),
    *(f"cotation_objectif_{ch}.{n}"
      for ch, nb in _OBJECTIFS for n in range(1, nb + 1)),
    *(f"nb_criteres_evalues_objectif_{ch}.{n}"
      for ch, nb in _OBJECTIFS for n in range(1, nb + 1)),
    *(f"cotation_critere_imperatif_{c}" for c in CRITERES_IMPERATIFS),
    "3.6.2_nc", "nb_criteres_hors310", "somme_ponderee_hors310",
    "moy_objectifs_hors310", "moy_objectifs_100",
    "nb_ci", "nb_ci_sup_3_5", "taux_ci_sup_3_5", "indice_qualite", "nb_ci_atteints",
)


def _type_sql(nom: str) -> str:
    """Le type STRICT d'une colonne, déduit de son nom."""
    if nom.startswith(("cotation", "taux", "somme", "moy")) or nom in ("latitude", "longitude"):
        return "REAL"
    if nom.startswith(("nb_", "essms_P")) or nom == "multi_essms" or nom.endswith("_nc"):
        return "INTEGER"
    return "TEXT"


def _colonnes_sql(colonnes: tuple[str, ...]) -> str:
    # Les guillemets sont obligatoires : des noms comme "cotation_thematique_1.1"
    # ou "essms_PE/PJJ" contiennent des caractères interdits nus.
    return ",\n    ".join(f'"{nom}" {_type_sql(nom)}' for nom in colonnes)


SCHEMA_ETABLISSEMENTS_ESSMS = f"""
CREATE TABLE IF NOT EXISTS etablissements_essms (
    finess          TEXT NOT NULL PRIMARY KEY,
    {_colonnes_sql(COLONNES_ETABLISSEMENT_ESSMS)}
) STRICT;
"""

SCHEMA_EVALUATIONS_ESSMS = f"""
CREATE TABLE IF NOT EXISTS evaluations_essms (
    finess          TEXT NOT NULL REFERENCES etablissements_essms(finess),
    eval_date_fin   TEXT NOT NULL,
    {_colonnes_sql(COLONNES_EVALUATION_ESSMS)},
    date_chargement TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (finess, eval_date_fin)
) STRICT;
"""

# L'ordre compte : les tables qui portent une FK (lien, evaluations_essms)
# doivent venir après celles qu'elles référencent.
liste_SCHEMA = (SCHEMA_ETABLISSEMENTS_SANITAIRES, SCHEMA_RAPPORTS_SANITAIRES,
                SCHEMA_LIEN_EVALUATION_ETABLISSEMENT, SCHEMA_INVENTAIRE_SANITAIRE,
                SCHEMA_ETABLISSEMENTS_ESSMS, SCHEMA_EVALUATIONS_ESSMS)


def init_db(cx: sqlite3.Connection) -> None:
    for schema in liste_SCHEMA:
        cx.executescript(schema)
    # Rattrape une etablissements_essms créée par une version antérieure du
    # schéma (finess seul) : ALTER ADD ne touche pas aux lignes existantes.
    _completer_colonnes(cx, "etablissements_essms", COLONNES_ETABLISSEMENT_ESSMS)


def _completer_colonnes(cx: sqlite3.Connection, table: str,
                        colonnes: tuple[str, ...]) -> None:
    existantes = {ligne["name"]
                  for ligne in cx.execute(f"PRAGMA table_info({table})")}
    for nom in colonnes:
        if nom not in existantes:
            cx.execute(f'ALTER TABLE {table} ADD COLUMN "{nom}" {_type_sql(nom)}')


# ---- insertions sanitaire --------------------------------------------

def ajouter_etablissement_sanitaire(cx: sqlite3.Connection, finess: str) -> None:
    # OR IGNORE : un finess déjà présent (clé primaire) n'est pas une erreur.
    cx.execute(
        "INSERT OR IGNORE INTO etablissements_sanitaires (finess) VALUES (?)",
        (finess,),
    )


def ajouter_a_inventaire_sanitaire(cx: sqlite3.Connection, doc_id: str) -> None:
    # Pas de OR IGNORE : un doc_id déjà inventorié est une anomalie, on veut l'erreur.
    # date_telechargement est remplie par le DEFAULT du schéma.
    cx.execute(
        "INSERT INTO inventaire_sanitaire (doc_id) VALUES (?)",
        (doc_id,),
    )


def ajouter_lien_sanitaire(cx: sqlite3.Connection, doc_id: str, finess: str) -> None:
    """Enregistre « la fiche de cet établissement cite cette évaluation ».
    OR IGNORE : revoir le même couple à la tournée suivante est normal.
    Les FK exigent que le rapport ET l'établissement existent déjà."""
    cx.execute(
        "INSERT OR IGNORE INTO lien_evaluation_etablissement_sanitaire "
        "(doc_id, finess) VALUES (?, ?)",
        (doc_id, finess),
    )


def add_rapport_sanitaire(cx: sqlite3.Connection, rapport: dict) -> None:
    """Insère un rapport au catalogue. Le finess n'y figure plus : le lien
    fiche ↔ rapport vit dans la table de lien — une seule source de vérité."""
    cx.execute(
        "INSERT OR IGNORE INTO rapports (doc_id, slug, pdf_url) VALUES (?, ?, ?)",
        (rapport["doc_id"], rapport["slug"], rapport["pdf_url"]),
    )


# ---- insertions ESSMS ------------------------------------------------
# Le xlsx est republié EN ENTIER à chaque mise à jour : l'écriture est un
# upsert (INSERT … ON CONFLICT DO UPDATE), pas un OR REPLACE — OR REPLACE
# supprime puis réinsère, ce que la FK des évaluations interdirait sur
# l'établissement.

def _sql_upsert(table: str, cles: tuple[str, ...], colonnes: tuple[str, ...]) -> str:
    toutes = cles + colonnes
    return (f'INSERT INTO {table} ({", ".join(f_quoted(toutes))}) '
            f'VALUES ({", ".join("?" for _ in toutes)}) '
            f'ON CONFLICT({", ".join(f_quoted(cles))}) DO UPDATE SET '
            + ", ".join(f'"{c}" = excluded."{c}"' for c in colonnes))


def f_quoted(noms: tuple[str, ...]) -> list[str]:
    return [f'"{nom}"' for nom in noms]


SQL_UPSERT_ETABLISSEMENT_ESSMS = _sql_upsert(
    "etablissements_essms", ("finess",), COLONNES_ETABLISSEMENT_ESSMS)

SQL_UPSERT_EVALUATION_ESSMS = _sql_upsert(
    "evaluations_essms", ("finess", "eval_date_fin"), COLONNES_EVALUATION_ESSMS)


def upsert_etablissement_essms(cx: sqlite3.Connection, ligne: dict) -> None:
    """`ligne` est une ligne du xlsx (opendata_essms.lire_evaluations).
    En cas de re-passage, l'identité est mise à jour — c'est la HAS qui fait foi."""
    valeurs = [ligne["finess_geo"]] + [ligne[c] for c in COLONNES_ETABLISSEMENT_ESSMS]
    cx.execute(SQL_UPSERT_ETABLISSEMENT_ESSMS, valeurs)


def upsert_evaluation_essms(cx: sqlite3.Connection, ligne: dict) -> None:
    """Une évaluation = (finess, date de fin). Revoir la même le mois suivant
    est normal (le fichier republie tout) ; ses cotations sont rafraîchies,
    date_chargement garde la date de la première insertion."""
    valeurs = ([ligne["finess_geo"], ligne["eval_date_fin"]]
               + [ligne[c] for c in COLONNES_EVALUATION_ESSMS])
    cx.execute(SQL_UPSERT_EVALUATION_ESSMS, valeurs)


# ---- suppressions ----------------------------------------------------

def supprimer_de_inventaire_sanitaire(cx: sqlite3.Connection,
                                      doc_ids: Iterable[str]) -> None:
    """Retire des doc_id de l'inventaire. Ne touche ni au catalogue (rapports)
    ni aux établissements — un doc_id absent de la liste est simplement ignoré."""
    cx.executemany(
        "DELETE FROM inventaire_sanitaire WHERE doc_id = ?",
        [(doc_id,) for doc_id in doc_ids],
    )


# ---- lectures --------------------------------------------------------

def get_doc_id_inventored_sanitaire(cx: sqlite3.Connection) -> set[str]:
    lignes = cx.execute("SELECT doc_id FROM inventaire_sanitaire").fetchall()
    return {ligne["doc_id"] for ligne in lignes}


def compter_essms(cx: sqlite3.Connection) -> dict:
    """Pour le bilan de la tournée : l'état de la base après import."""
    return {
        "etablissements": cx.execute(
            "SELECT count(*) AS n FROM etablissements_essms").fetchone()["n"],
        "evaluations": cx.execute(
            "SELECT count(*) AS n FROM evaluations_essms").fetchone()["n"],
    }
