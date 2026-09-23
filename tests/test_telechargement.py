"""telechargement.py : de l'URL au PDF sur le disque. Zéro réseau."""
import io
import zipfile

import pytest
import requests

import telechargement
from conftest import ReponseFactice

PDF = b"%PDF-1.4\n%fake\n"
URL = "https://www.has-sante.fr/upload/docs/application/pdf/2026-02/x.pdf"


def _zip(*fichiers: tuple[str, bytes]) -> bytes:
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w") as archive:
        for nom, contenu in fichiers:
            archive.writestr(nom, contenu)
    return tampon.getvalue()


def test_rangement_git_like(donnees):
    chemin = telechargement.chemin_pdf_sanitaire("p_3350129")
    assert chemin == donnees / "documents" / "sanitaire" / "p_33" / "50129.pdf"


def test_pdf_ecrit_a_sa_place(donnees, http):
    http.reponses[URL] = ReponseFactice(200, PDF)
    chemin = telechargement.telecharger_pdf_sanitaire("p_3350129", URL)
    assert chemin.read_bytes() == PDF
    assert chemin.name == "50129.pdf"


def test_fichier_present_ne_retelecharge_pas(donnees, http):
    chemin = telechargement.chemin_pdf_sanitaire("p_1")
    chemin.parent.mkdir(parents=True)
    chemin.write_bytes(PDF)
    assert telechargement.telecharger_pdf_sanitaire("p_1", URL) == chemin
    assert http.appels == []


def test_page_html_en_200_est_refusee(donnees, http):
    """Une page d'erreur servie en 200 ne doit jamais devenir un .pdf."""
    http.reponses[URL] = ReponseFactice(200, b"<html>Service indisponible</html>")
    with pytest.raises(ValueError, match="n'est pas un PDF"):
        telechargement.telecharger_pdf_sanitaire("p_2", URL)
    assert not telechargement.chemin_pdf_sanitaire("p_2").exists()


def test_erreur_http_remonte_en_request_exception(donnees, http):
    http.reponses[URL] = ReponseFactice(500, b"boom")
    with pytest.raises(requests.RequestException):
        telechargement.telecharger_pdf_sanitaire("p_3", URL)


def test_zip_avec_un_seul_pdf_est_deballe(donnees, http):
    http.reponses[URL] = ReponseFactice(200, _zip(("31145_rac1_vd.pdf", PDF), ("lisez-moi.txt", b"x")))
    chemin = telechargement.telecharger_pdf_sanitaire("c_2789064", URL)
    assert chemin.read_bytes() == PDF
    assert chemin.suffix == ".pdf"


@pytest.mark.parametrize("archive", [
    _zip(("a.pdf", PDF), ("b.pdf", PDF)),   # deux PDF : lequel ?
    _zip(("notes.txt", b"x")),               # aucun PDF
    b"PK\x03\x04corrompu",                  # signature zip, contenu illisible
])
def test_zip_anormal_est_une_value_error(donnees, http, archive):
    http.reponses[URL] = ReponseFactice(200, archive)
    with pytest.raises(ValueError):
        telechargement.telecharger_pdf_sanitaire("c_9", URL)
