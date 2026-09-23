"""perimetre.py : du xlsx Qualiscope aux FINESS à visiter."""
import config
import perimetre
from conftest import ReponseFactice, ecrire_xlsx


def test_filtre_has_certif_et_dedoublonnage(donnees):
    ecrire_xlsx(config.INDEX_SANITAIRE,
                ["num_finess_et", "raison_sociale", "has_certif"],
                [["010000024", "CH A", True],
                 ["010000032", "CH B", False],       # jamais certifié : pas de fiche utile
                 ["010000024", "CH A bis", True],    # doublon : une seule visite
                 ["020000000", "CH C", 1],           # 1 vaut True
                 [None, "vide", True],               # sans FINESS : ignoré
                 ["2A0000001", "Corse", True]])      # FINESS non numérique : gardé en texte

    assert perimetre.perimetre_sanitaire() == ["010000024", "020000000", "2A0000001"]


def test_index_telecharge_si_absent(donnees, http):
    http.reponses[config.URL_INDEX_SANITAIRE] = ReponseFactice(
        200, _octets_xlsx(["num_finess_et", "has_certif"], [["330000241", True]]))

    assert not config.INDEX_SANITAIRE.exists()
    assert perimetre.perimetre_sanitaire() == ["330000241"]
    assert config.INDEX_SANITAIRE.exists()
    assert len(http.appels) == 1


def test_index_conditionnel(donnees, http):
    """Un fichier déjà là → GET avec If-Modified-Since ; 304 → rien réécrit."""
    config.INDEX_SANITAIRE.write_bytes(b"ancien")
    http.reponses[config.URL_INDEX_SANITAIRE] = ReponseFactice(304)

    assert perimetre.telecharger_index() is False
    assert config.INDEX_SANITAIRE.read_bytes() == b"ancien"
    (_, entetes), = http.appels
    assert "If-Modified-Since" in entetes


def _octets_xlsx(entete, lignes) -> bytes:
    import io
    import openpyxl
    classeur = openpyxl.Workbook()
    classeur.active.append(entete)
    for ligne in lignes:
        classeur.active.append(ligne)
    tampon = io.BytesIO()
    classeur.save(tampon)
    return tampon.getvalue()
