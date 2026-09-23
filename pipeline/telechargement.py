"""Téléchargement des rapports PDF, rangés façon git par doc_id.
Module pur : il écrit des fichiers sur disque, il ne touche jamais la base."""
import io
import time
import zipfile
from pathlib import Path

from config import DOSSIER_SANITAIRE, DELAI, session


def chemin_pdf_sanitaire(doc_id: str) -> Path:
    """Rangement git-like : dossier = 4 premiers caractères du doc_id,
    nom du fichier = le reste. Ex : "p_3350129" → sanitaire/p_33/50129.pdf"""
    return DOSSIER_SANITAIRE / doc_id[:4] / (doc_id[4:] + ".pdf")


def _pdf_depuis_zip(doc_id: str, contenu: bytes) -> bytes:
    """Quelques rapports V2014 sont livrés en zip contenant un unique PDF :
    on l'extrait en mémoire. Toute anomalie (archive corrompue, zéro ou
    plusieurs PDF) devient une ValueError, que l'appelant sait déjà classer."""
    try:
        with zipfile.ZipFile(io.BytesIO(contenu)) as archive:
            pdfs = [nom for nom in archive.namelist() if nom.lower().endswith(".pdf")]
            if len(pdfs) != 1:
                raise ValueError(f"{doc_id} : zip sans PDF unique ({pdfs or 'aucun PDF'})")
            return archive.read(pdfs[0])
    except zipfile.BadZipFile as e:
        raise ValueError(f"{doc_id} : zip illisible ({e})") from e


def telecharger_pdf_sanitaire(doc_id: str, pdf_url: str) -> Path:
    """Télécharge un rapport et l'écrit à sa place. Renvoie le chemin du fichier.
    Si le fichier existe déjà, ne retélécharge pas. Un zip est déballé au vol :
    seul le PDF qu'il contient est écrit, au même chemin que les autres.
    Lève requests.RequestException (réseau, 4xx/5xx) ou ValueError (pas un PDF)."""
    chemin = chemin_pdf_sanitaire(doc_id)
    if chemin.exists():
        return chemin

    chemin.parent.mkdir(parents=True, exist_ok=True)
    time.sleep(DELAI)
    reponse = session.get(pdf_url, timeout=300)
    reponse.raise_for_status()

    contenu = reponse.content
    if contenu.startswith(b"PK\x03\x04"):      # signature d'une archive zip
        contenu = _pdf_depuis_zip(doc_id, contenu)

    # Un vrai PDF commence par les octets %PDF- ; sans ce contrôle, une page
    # d'erreur HTML renvoyée en 200 serait enregistrée comme un .pdf.
    if not contenu.startswith(b"%PDF-"):
        raise ValueError(f"{doc_id} : le contenu reçu n'est pas un PDF ({pdf_url})")

    chemin.write_bytes(contenu)
    return chemin