"""main.py : la logique de la tournée — QUOI télécharger, et pourquoi.
Le scraping et le téléchargement sont remplacés par des doublures ; la
base, elle, est réelle (fichier temporaire)."""
import requests

import config
import db
import fiches
import main
import opendata_essms
import perimetre
import telechargement
from conftest import ecrire_xlsx, ecrire_xlsx_essms, ligne_essms

PDF = b"%PDF-1.4 fake"


def rapport(doc_id, finess, url="https://www.has-sante.fr/upload/x.pdf"):
    return {"finess_fiche": finess, "doc_id": doc_id, "slug": "s-" + doc_id, "pdf_url": url}


def doublure_site(monkeypatch, fiches_par_finess: dict):
    """Le site HAS en carton : finess → rapports (ou exception)."""
    def rapports_de_la_fiche(finess):
        resultat = fiches_par_finess[finess]
        if isinstance(resultat, Exception):
            raise resultat
        return resultat
    monkeypatch.setattr(fiches, "rapports_de_la_fiche_sanitaire", rapports_de_la_fiche)

    telecharges = []
    def telecharger(doc_id, pdf_url):
        chemin = telechargement.chemin_pdf_sanitaire(doc_id)
        if not chemin.exists():
            chemin.parent.mkdir(parents=True, exist_ok=True)
            chemin.write_bytes(PDF)
            telecharges.append(doc_id)
        return chemin
    monkeypatch.setattr(telechargement, "telecharger_pdf_sanitaire", telecharger)
    return telecharges


def test_la_tournee_ne_prend_que_ce_qui_manque(cx, monkeypatch):
    # État de départ : le PDF A est déjà possédé (inventaire + fichier).
    with cx:
        db.ajouter_a_inventaire_sanitaire(cx, "p_A")
    telecharges = doublure_site(monkeypatch, {
        "F1": [rapport("p_A", "F1"), rapport("p_B", "F1")],
        "F2": [rapport("p_B", "F2"), rapport("p_C", "F2", url=None)],   # B partagé, C sans URL
        "F3": requests.ConnectionError("réseau coupé"),
        "F4": [],                                                          # pas de fiche publique
    })

    manquants, echecs = main.tournee_des_fiches_sanitaire(cx, ["F1", "F2", "F3", "F4"])

    # QUOI télécharger : B seulement. A est déjà là, C n'a pas d'URL,
    # et B — cité par deux fiches — n'entre qu'UNE fois dans la liste.
    assert [m["doc_id"] for m in manquants] == ["p_B"]
    assert echecs == [{"etape": "fiche", "cle": "F3", "erreur": "réseau coupé"}]

    # Le catalogue garde TOUT, y compris C (sans URL) et le double lien de B.
    assert {l["doc_id"] for l in cx.execute("SELECT doc_id FROM rapports")} == {"p_A", "p_B", "p_C"}
    liens = {(l["doc_id"], l["finess"]) for l in
             cx.execute("SELECT doc_id, finess FROM lien_evaluation_etablissement_sanitaire")}
    assert liens == {("p_A", "F1"), ("p_B", "F1"), ("p_B", "F2"), ("p_C", "F2")}
    # F4 n'a cité aucun rapport : il n'entre pas dans la table des établissements.
    assert {l["finess"] for l in cx.execute("SELECT finess FROM etablissements_sanitaires")} == {"F1", "F2"}

    echecs += main.telecharger_les_manquants_sanitaire(cx, manquants)
    assert telecharges == ["p_B"]
    assert db.get_doc_id_inventored_sanitaire(cx) == {"p_A", "p_B"}


def test_deuxieme_tournee_ne_fait_rien(cx, monkeypatch):
    site = {"F1": [rapport("p_A", "F1")]}
    telecharges = doublure_site(monkeypatch, site)
    manquants, _ = main.tournee_des_fiches_sanitaire(cx, ["F1"])
    main.telecharger_les_manquants_sanitaire(cx, manquants)
    assert telecharges == ["p_A"]

    manquants, echecs = main.tournee_des_fiches_sanitaire(cx, ["F1"])
    assert manquants == [] and echecs == []
    assert telecharges == ["p_A"]                      # rien de plus


def test_un_pdf_supprime_du_disque_est_retelecharge(cx, monkeypatch):
    telecharges = doublure_site(monkeypatch, {"F1": [rapport("p_A", "F1")]})
    manquants, _ = main.tournee_des_fiches_sanitaire(cx, ["F1"])
    main.telecharger_les_manquants_sanitaire(cx, manquants)

    telechargement.chemin_pdf_sanitaire("p_A").unlink()          # disque corrompu, purge...

    assert main.purger_inventaire_sanitaire(cx) == ["p_A"]
    assert db.get_doc_id_inventored_sanitaire(cx) == set()
    manquants, _ = main.tournee_des_fiches_sanitaire(cx, ["F1"])
    main.telecharger_les_manquants_sanitaire(cx, manquants)
    assert telecharges == ["p_A", "p_A"]


def test_echec_de_telechargement_ne_touche_pas_a_l_inventaire(cx, monkeypatch):
    def telecharger(doc_id, pdf_url):
        raise ValueError(f"{doc_id} : le contenu reçu n'est pas un PDF")
    monkeypatch.setattr(telechargement, "telecharger_pdf_sanitaire", telecharger)

    echecs = main.telecharger_les_manquants_sanitaire(cx, [rapport("p_A", "F1")])
    assert echecs[0]["etape"] == "pdf" and echecs[0]["cle"] == "p_A"
    assert db.get_doc_id_inventored_sanitaire(cx) == set()      # il restera « manquant »


def test_update_db_sanitaire_de_bout_en_bout(cx, monkeypatch):
    ecrire_xlsx(config.INDEX_SANITAIRE, ["num_finess_et", "has_certif"],
                [["F1", True], ["F2", True], ["F3", False]])
    monkeypatch.setattr(perimetre, "telecharger_index", lambda: False)
    telecharges = doublure_site(monkeypatch, {"F1": [rapport("p_A", "F1")], "F2": []})

    bilan = main.update_db_sanitaire(cx)

    assert bilan["périmètre (FINESS)"] == 2
    assert bilan["PDF manquants"] == 1
    assert bilan["echecs"] == []
    assert telecharges == ["p_A"]

    # --limite : un tour d'essai ne visite que les N premières fiches.
    assert main.update_db_sanitaire(cx, limite=1)["périmètre (FINESS)"] == 1


def test_update_db_essms_importe_puis_saute_le_304(cx, monkeypatch):
    ecrire_xlsx_essms(config.INDEX_ESSMS, [
        ligne_essms(),
        ligne_essms(eval_code="EVAL-2", eval_date_fin=__import__("datetime").datetime(2025, 3, 3)),
        ligne_essms(finess_geo=None),                    # sans identité : ignorée
    ])
    monkeypatch.setattr(opendata_essms, "telecharger_index", lambda: True)
    bilan = main.update_db_essms(cx)
    assert (bilan["lignes importées"], bilan["lignes ignorées"]) == (2, 1)
    assert (bilan["établissements"], bilan["évaluations"]) == (1, 2)
    assert bilan["nouvelles évaluations"] == 2

    # Mois suivant, la HAS n'a rien republié : on ne relit même pas le fichier.
    monkeypatch.setattr(opendata_essms, "telecharger_index", lambda: False)
    monkeypatch.setattr(opendata_essms, "lire_evaluations",
                        lambda *_: (_ for _ in ()).throw(AssertionError("ne doit pas relire")))
    bilan = main.update_db_essms(cx)
    assert bilan["lignes importées"] == 0 and bilan["nouvelles évaluations"] == 0


def test_main_cli_et_code_de_sortie(donnees, monkeypatch, capsys):
    monkeypatch.setattr(main, "ROUTINES", {
        "sanitaire": lambda cx, limite=None: {"PDF manquants": 0, "echecs": []},
        "essms": lambda cx, limite=None: {"echecs": [{"etape": "x", "cle": "y", "erreur": "z"}]},
    })
    assert main.main(["sanitaire"]) == 0
    assert main.main([]) == 1                     # un échec quelque part → code 1
    sortie = capsys.readouterr().out
    assert "=== bilan essms ===" in sortie and "[x] y : z" in sortie
