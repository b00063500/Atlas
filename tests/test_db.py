"""db.py : le schéma tient ses promesses (FK, N:N, upsert, détecteur d'anomalie)."""
import sqlite3

import pytest

import db
import opendata_essms
from conftest import ligne_essms


def ligne_convertie(**surcharges) -> dict:
    """Une ligne telle que main la reçoit : passée par opendata_essms._convertir."""
    return {c: opendata_essms._convertir(v) for c, v in ligne_essms(**surcharges).items()}


def test_schema_cree_les_six_tables(cx):
    tables = {l["name"] for l in cx.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"etablissements_sanitaires", "rapports",
            "lien_evaluation_etablissement_sanitaire", "inventaire_sanitaire",
            "etablissements_essms", "evaluations_essms"} <= tables
    assert cx.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_init_db_est_idempotent(cx):
    db.init_db(cx)
    db.init_db(cx)


def test_un_rapport_cite_par_deux_fiches(cx):
    """Le piège N:N : une certification portée par l'entité juridique est
    citée par plusieurs FINESS. Deux liens, un rapport, un seul PDF."""
    rapport = {"doc_id": "p_3505501", "slug": "s", "pdf_url": "u"}
    with cx:
        for finess in ("X_site_A", "340024553"):
            db.ajouter_etablissement_sanitaire(cx, finess)
            db.add_rapport_sanitaire(cx, rapport)
            db.ajouter_lien_sanitaire(cx, "p_3505501", finess)
            db.ajouter_lien_sanitaire(cx, "p_3505501", finess)   # revu : OR IGNORE

    assert cx.execute("SELECT count(*) FROM rapports").fetchone()[0] == 1
    liens = cx.execute("SELECT finess FROM lien_evaluation_etablissement_sanitaire "
                       "WHERE doc_id='p_3505501' ORDER BY finess").fetchall()
    assert [l["finess"] for l in liens] == ["340024553", "X_site_A"]


def test_lien_vers_rapport_inconnu_est_refuse(cx):
    with cx:
        db.ajouter_etablissement_sanitaire(cx, "F")
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        db.ajouter_lien_sanitaire(cx, "p_inconnu", "F")


def test_inventaire_refuse_le_doublon(cx):
    """Pas de OR IGNORE sur l'inventaire : c'est le détecteur d'anomalies."""
    with cx:
        db.ajouter_a_inventaire_sanitaire(cx, "p_1")
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        db.ajouter_a_inventaire_sanitaire(cx, "p_1")
    assert db.get_doc_id_inventored_sanitaire(cx) == {"p_1"}


def test_supprimer_de_inventaire(cx):
    with cx:
        db.ajouter_a_inventaire_sanitaire(cx, "p_1")
        db.ajouter_a_inventaire_sanitaire(cx, "p_2")
        db.supprimer_de_inventaire_sanitaire(cx, ["p_1", "p_absent"])
    assert db.get_doc_id_inventored_sanitaire(cx) == {"p_2"}


def test_upsert_essms_rafraichit_sans_dupliquer(cx):
    ligne = ligne_convertie()
    with cx:
        db.upsert_etablissement_essms(cx, ligne)
        db.upsert_evaluation_essms(cx, ligne)
    premiere_date = cx.execute("SELECT date_chargement FROM evaluations_essms").fetchone()[0]

    ligne["indice_qualite"] = "B"                  # la HAS a corrigé la note
    ligne["raison_sociale"] = "NOUVEAU NOM"
    with cx:
        db.upsert_etablissement_essms(cx, ligne)
        db.upsert_evaluation_essms(cx, ligne)

    assert db.compter_essms(cx) == {"etablissements": 1, "evaluations": 1}
    evaluation = cx.execute("SELECT * FROM evaluations_essms").fetchone()
    assert evaluation["indice_qualite"] == "B"
    assert evaluation["date_chargement"] == premiere_date     # la première insertion fait foi
    assert cx.execute("SELECT raison_sociale FROM etablissements_essms").fetchone()[0] == "NOUVEAU NOM"


def test_evaluation_sans_etablissement_est_refusee(cx):
    ligne = ligne_convertie(finess_geo="999999999")
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        db.upsert_evaluation_essms(cx, ligne)


def test_types_stricts_deduits_du_nom():
    assert db._type_sql("cotation_objectif_1.1") == "REAL"
    assert db._type_sql("latitude") == "REAL"
    assert db._type_sql("nb_ci") == "INTEGER"
    assert db._type_sql("essms_PA") == "INTEGER"
    assert db._type_sql("3.6.2_nc") == "INTEGER"
    assert db._type_sql("indice_qualite") == "TEXT"
