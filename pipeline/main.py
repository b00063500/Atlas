"""Orchestre la tournée : énumère, persiste, télécharge, résume.
Deux secteurs, deux chaînes :
  - sanitaire : périmètre open data → crawl des fiches → PDF manquants
  - essms     : l'open data contient tout → import direct, pas de crawl

    python main.py                       → les deux secteurs
    python main.py sanitaire             → le sanitaire seul
    python main.py essms                 → l'ESSMS seul
    python main.py sanitaire --limite 30 → tour d'essai sur 30 fiches

Seul module qui importe les autres : c'est ici, et nulle part ailleurs,
que les données rencontrent la base, et que les échecs sont comptés."""
import argparse
import sys
import time

import requests

import db
import fiches
import opendata_essms
import perimetre
import telechargement


def log(message: str) -> None:
    """Progression horodatée, façon journal de bord :
    [11:49:00] fiche 8730/8987 660007261 : 1 rapport(s)"""
    print(f"[{time.strftime('%H:%M:%S')}] {message}")


# ---- chaîne sanitaire ------------------------------------------------

def tournee_des_fiches_sanitaire(cx, finess_cibles: list[str]) -> tuple[list[dict], list[dict]]:
    """Phase 1 : visite chaque fiche, enregistre ses rapports au catalogue,
    et renvoie (manquants, echecs). Un manquant = un rapport avec une URL,
    pas encore à l'inventaire. C'est ICI que se décide « quoi télécharger »."""
    doc_ids_connus = db.get_doc_id_inventored_sanitaire(cx)
    manquants, echecs = [], []

    for i, finess in enumerate(finess_cibles, 1):
        try:
            rapports = fiches.rapports_de_la_fiche_sanitaire(finess)
        except requests.RequestException as e:
            echecs.append({"etape": "fiche", "cle": finess, "erreur": str(e)})
            log(f"ECHEC fiche {finess} : {e}")
            continue                    # UNE fiche perdue, pas la tournée

        log(f"fiche {i}/{len(finess_cibles)} {finess} : {len(rapports)} rapport(s)")

        with cx:                        # une fiche = une transaction
            # L'ordre est imposé par les FK : l'établissement d'abord,
            # puis chaque rapport, puis son lien. Le `if rapports:` fixe le
            # sens de la table : « établissements dont la fiche cite au
            # moins une évaluation », pas « toutes les fiches visitées ».
            if rapports:
                db.ajouter_etablissement_sanitaire(cx, finess)
            for rapport in rapports:
                db.add_rapport_sanitaire(cx, rapport)
                db.ajouter_lien_sanitaire(cx, rapport["doc_id"], finess)

        for rapport in rapports:
            if rapport["doc_id"] not in doc_ids_connus and rapport["pdf_url"]:
                manquants.append(rapport)
                # Le même rapport peut être cité par une autre fiche plus loin
                # dans la tournée (certification portée par un autre FINESS) :
                # sans ceci, le second INSERT à l'inventaire planterait
                # (doc_id est clé primaire).
                doc_ids_connus.add(rapport["doc_id"])
                log(f"  url n°{len(manquants)} : {finess} -> {rapport['pdf_url']}")

    return manquants, echecs


def telecharger_les_manquants_sanitaire(cx, manquants: list[dict]) -> list[dict]:
    """Phase 2 : télécharge chaque PDF, puis l'inscrit à l'inventaire.
    L'inscription ne se fait QUE si le téléchargement a réussi."""
    echecs = []

    for j, rapport in enumerate(manquants, 1):
        doc_id = rapport["doc_id"]
        log(f"téléchargement {j}/{len(manquants)} : {doc_id}")
        # Mémorisé AVANT l'appel : après, le fichier existe forcément.
        deja_la = telechargement.chemin_pdf_sanitaire(doc_id).exists()
        try:
            chemin = telechargement.telecharger_pdf_sanitaire(doc_id, rapport["pdf_url"])
        except (requests.RequestException, ValueError) as e:
            echecs.append({"etape": "pdf", "cle": doc_id, "erreur": str(e)})
            log(f"  ECHEC pdf {doc_id} : {e}")
            continue

        taille_ko = chemin.stat().st_size // 1024
        log(f"  PDF {'déjà présent' if deja_la else 'écrit'} : {chemin.name} ({taille_ko} Ko)")

        with cx:
            db.ajouter_a_inventaire_sanitaire(cx, doc_id)

    return echecs


def purger_inventaire_sanitaire(cx) -> list[str]:
    """Maintenance : retire de l'inventaire les doc_id dont le PDF n'est plus
    sur le disque. Le catalogue (rapports) n'est pas touché : le rapport
    redeviendra « manquant » à la prochaine tournée et sera retéléchargé."""
    orphelins = [doc_id for doc_id in db.get_doc_id_inventored_sanitaire(cx)
                 if not telechargement.chemin_pdf_sanitaire(doc_id).exists()]
    with cx:
        db.supprimer_de_inventaire_sanitaire(cx, orphelins)
    return orphelins


def update_db_sanitaire(cx, limite: int | None = None) -> dict:
    """La routine complète : purge, périmètre, tournée, téléchargements.
    Renvoie un bilan — c'est l'appelant qui décide quoi en afficher.
    Seuls les affichages de progression restent dans les phases."""
    # L'inventaire promet « ce PDF est sur le disque » : on le fait
    # redevenir vrai avant la tournée, pour que les fichiers disparus
    # soient retéléchargés.
    orphelins = purger_inventaire_sanitaire(cx)
    print(f"purge inventaire   : {len(orphelins)} doc_id sans PDF retirés")

    # Pas de try ici : si l'open data est injoignable, il n'y a rien
    # à continuer — le programme meurt avec la vraie erreur.
    index_nouveau = perimetre.telecharger_index()
    print(f"index Qualiscope   : {'nouvelle version' if index_nouveau else 'inchangé (304)'}")
    finess_cibles = perimetre.perimetre_sanitaire()[:limite]
    print(f"périmètre          : {len(finess_cibles)} FINESS certifiés")

    t0 = time.perf_counter()
    manquants, echecs = tournee_des_fiches_sanitaire(cx, finess_cibles)
    duree_fiches = time.perf_counter() - t0
    print(f"PDF manquants      : {len(manquants)}")
    print(f"tournée des fiches : {duree_fiches:.1f} s")

    t0 = time.perf_counter()
    echecs += telecharger_les_manquants_sanitaire(cx, manquants)
    duree_pdf = time.perf_counter() - t0

    return {
        "périmètre (FINESS)": len(finess_cibles),
        "index open data": "nouvelle version" if index_nouveau else "inchangé",
        "purge inventaire": len(orphelins),
        "PDF manquants": len(manquants),
        "tournée des fiches": duree_fiches,
        "téléchargements": duree_pdf,
        "echecs": echecs,
    }


# ---- chaîne ESSMS ----------------------------------------------------
# Pas de crawl, pas de PDF : le xlsx open data contient l'identité des
# établissements ET les cotations de chaque évaluation. La tournée se
# résume à « le fichier a-t-il changé ? si oui, on le relit en entier ».

COLONNES_ESSMS = (("finess_geo", "eval_date_fin")
                  + db.COLONNES_ETABLISSEMENT_ESSMS + db.COLONNES_EVALUATION_ESSMS)


def importer_open_data_essms(cx, limite: int | None = None) -> tuple[int, int]:
    """Relit le xlsx et upserte chaque ligne. Renvoie (importées, ignorées).
    Une ligne sans FINESS ou sans date de fin n'a pas d'identité : ignorée.
    Une seule transaction : le fichier est atomique, la base l'est aussi."""
    importees = ignorees = 0
    with cx:
        for ligne in opendata_essms.lire_evaluations(COLONNES_ESSMS):
            if limite is not None and importees >= limite:
                break
            if not ligne["finess_geo"] or not ligne["eval_date_fin"]:
                ignorees += 1
                continue
            # Le FINESS est un identifiant, pas un nombre : zéros de tête gardés.
            ligne["finess_geo"] = str(ligne["finess_geo"]).strip()
            db.upsert_etablissement_essms(cx, ligne)
            db.upsert_evaluation_essms(cx, ligne)
            importees += 1
            if importees % 5000 == 0:
                log(f"open data ESSMS : {importees} lignes importées")
    return importees, ignorees


def update_db_essms(cx, limite: int | None = None) -> dict:
    t0 = time.perf_counter()
    index_nouveau = opendata_essms.telecharger_index()
    print(f"index open data    : {'nouvelle version' if index_nouveau else 'inchangé (304)'}")

    # Fichier inchangé ET base déjà remplie : rien à relire. Mais une base
    # vide (premier passage, base reconstruite) s'importe même sur 304.
    deja = db.compter_essms(cx)
    if index_nouveau or deja["evaluations"] == 0:
        importees, ignorees = importer_open_data_essms(cx, limite)
    else:
        importees = ignorees = 0
    apres = db.compter_essms(cx)

    return {
        "index open data": "nouvelle version" if index_nouveau else "inchangé",
        "lignes importées": importees,
        "lignes ignorées": ignorees,
        "établissements": apres["etablissements"],
        "évaluations": apres["evaluations"],
        "nouvelles évaluations": apres["evaluations"] - deja["evaluations"],
        "durée": time.perf_counter() - t0,
        "echecs": [],
    }


# ---- point d'entrée --------------------------------------------------

ROUTINES = {"sanitaire": update_db_sanitaire, "essms": update_db_essms}


def main(argv: list[str] | None = None) -> int:
    parseur = argparse.ArgumentParser(
        description="Tournée mensuelle HAS : sanitaire (crawl des fiches, PDF manquants) "
                    "et ESSMS (import de l'open data).")
    parseur.add_argument("secteurs", nargs="*", choices=list(ROUTINES),
                         help="sanitaire, essms — les deux si omis")
    parseur.add_argument("--limite", type=int, default=None,
                         help="tour d'essai : ne traiter que N fiches (ou N lignes ESSMS)")
    args = parseur.parse_args(argv)
    secteurs = args.secteurs or list(ROUTINES)

    bilans = {}
    cx = db.connect()
    try:
        db.init_db(cx)
        for secteur in secteurs:
            print(f"=== {secteur} ===")
            bilans[secteur] = ROUTINES[secteur](cx, limite=args.limite)
    finally:
        cx.close()

    # TOUT l'affichage du résumé vit ici, et nulle part ailleurs.
    echecs_total = []
    for secteur, bilan in bilans.items():
        print(f"=== bilan {secteur} ===")
        for cle, valeur in bilan.items():
            if cle == "echecs":
                continue
            if isinstance(valeur, float):
                valeur = f"{valeur:.1f} s"
            print(f"{cle:<22}: {valeur}")
        print(f"{len(bilan['echecs'])} échec(s)")
        for e in bilan["echecs"]:
            print(f"  [{e['etape']}] {e['cle']} : {e['erreur']}")
        echecs_total += bilan["echecs"]
    return 1 if echecs_total else 0


if __name__ == "__main__":
    sys.exit(main())
