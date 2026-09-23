"""fiches.py : d'une page HTML aux rapports qu'elle cite. Zéro réseau."""
import pytest

import fiches
from conftest import FIXTURES, ReponseFactice

HTML = (FIXTURES / "fiche_etablissement.html").read_text(encoding="utf-8")
URL_FICHE = fiches.SITE + "/fiche-etablissement/500020383"
URL_DOXITI = fiches.SITE + "/plugins/ModuleXitiKLEE/types/FileDocument/doXiti.jsp?id="


def test_regex_ne_garde_que_les_rapports():
    """Le lien « qualité rapportée par le patient » n'est pas un rapport ;
    le lien cité deux fois n'est compté qu'une fois."""
    trouves = dict(fiches.RE_LIEN_RAPPORT.findall(HTML))
    assert set(trouves) == {"p_3880538", "c_2608487", "p_3297720"}
    assert trouves["p_3880538"] == "rapport-de-certification-cqss-30171"


def test_chemin_depuis_fiche_egalite_stricte():
    """Le rapport est trouvé par son slug ; la lettre de décision, qui porte
    le même numéro de démarche, ne doit JAMAIS être prise à sa place."""
    url = fiches.chemin_depuis_fiche(HTML, "rapport-de-certification-cqss-30171")
    assert url.endswith("/rapport_de_certification_cqss_-_30171.pdf")
    assert fiches.chemin_depuis_fiche(HTML, "rapport-de-certification-v2014-30060") is None


def test_fiche_complete_avec_repli_doxiti(http):
    http.reponses[URL_FICHE] = ReponseFactice(200, HTML.encode("utf-8"), URL_FICHE)
    # Le V2014 passe par doXiti : une page HTML avec une meta-refresh.
    http.reponses[URL_DOXITI + "c_2608487"] = ReponseFactice(
        200, b"<meta http-equiv='refresh' content=\"0; URL='/upload/docs/application/pdf/2020-01/30060_rac1_vd.pdf'\">")
    # Le rapport de non-certification : doXiti ne répond rien d'utile → None.
    http.reponses[URL_DOXITI + "p_3297720"] = ReponseFactice(200, b"<html>rien</html>")

    rapports = fiches.rapports_de_la_fiche_sanitaire("500020383")

    par_id = {r["doc_id"]: r for r in rapports}
    assert set(par_id) == {"p_3880538", "c_2608487", "p_3297720"}
    assert all(r["finess_fiche"] == "500020383" for r in rapports)
    assert par_id["p_3880538"]["pdf_url"] == (
        "https://www.has-sante.fr/upload/docs/application/pdf/2026-02/dir1/"
        "rapport_de_certification_cqss_-_30171.pdf")
    assert par_id["c_2608487"]["pdf_url"] == (
        "https://www.has-sante.fr/upload/docs/application/pdf/2020-01/30060_rac1_vd.pdf")
    assert par_id["p_3297720"]["pdf_url"] is None      # un résultat, pas une erreur
    # 1 fiche + 2 doXiti : le rapport trouvé dans la page ne coûte rien de plus.
    assert len(http.appels) == 3


@pytest.mark.parametrize("statut, url_finale", [
    (404, URL_FICHE),
    (200, fiches.SITE + "/fiche-etablissement/non-present"),   # redirection « pas de fiche »
])
def test_fiche_absente_est_une_liste_vide(http, statut, url_finale):
    http.reponses[URL_FICHE] = ReponseFactice(statut, b"<html></html>", url_finale)
    assert fiches.rapports_de_la_fiche_sanitaire("500020383") == []
