"""Scraping : d'une fiche établissement HAS aux rapports qu'elle cite.
Module pur : il renvoie des données, il ne touche jamais la base."""
import re
import time
import urllib.parse

from config import SITE, DELAI, session

# Un lien de rapport dans une fiche. Le préfixe JCMS varie (p_ récent, c_ vieux
# V2014), et "rapport-de-certification" n'est pas toujours en tête du slug.
RE_LIEN_RAPPORT = re.compile(
    r'href="(?:https://www\.has-sante\.fr)?/?'
    r'jcms/([a-z]+_\d+)/fr/'
    r'([a-z0-9\-]*rapport-(?:de-certification|de-non-certification|public-evaluation)'
    r'[a-z0-9\-]*)"')

# Tout fichier cité en clair dans la page (sous-dossier dir1/, dir6/… optionnel).
RE_FICHIER = re.compile(
    r'(upload/docs/application/(?:pdf|zip)/\d{4}-\d{2}/(?:[^"\'\s>]+?/)?[^"\'\s>]+?\.(?:pdf|zip))')

# La cible de la meta-refresh de doXiti.jsp — du HTML, aucun client ne la suit seul.
RE_REFRESH = re.compile(r"URL='([^']+)'", re.I)


def _normaliser(texte: str) -> str:
    return re.sub(r"[^a-z0-9]", "", texte.lower())


def chemin_depuis_fiche(html: str, slug: str) -> str | None:
    """Cherche dans le HTML le fichier dont le nom correspond au slug.
    Égalité STRICTE après normalisation — jamais un `in` : la lettre de
    décision porte le même numéro de démarche que le rapport."""
    cible = _normaliser(slug)
    for chemin in RE_FICHIER.findall(html):
        base = chemin.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if _normaliser(base) == cible:
            return SITE + "/" + chemin
    return None


def chemin_par_doxiti(doc_id: str) -> str | None:
    """Repli (~5 % des rapports) : la meta-refresh du redirecteur doXiti."""
    url = SITE + "/plugins/ModuleXitiKLEE/types/FileDocument/doXiti.jsp?id=" + doc_id
    time.sleep(DELAI)
    reponse = session.get(url, timeout=60)
    trouve = RE_REFRESH.search(reponse.text) if reponse.status_code == 200 else None
    return urllib.parse.urljoin(url, trouve.group(1)) if trouve else None


def rapports_de_la_fiche_sanitaire(finess: str) -> list[dict]:
    """Renvoie [{finess_fiche, doc_id, slug, pdf_url}, ...].
    pdf_url peut rester None si ni la fiche ni doXiti ne le donnent.
    Liste vide si pas de fiche publique (57 % des cas) — un résultat, pas une erreur.
    L'ESSMS n'a pas d'équivalent : ses évaluations arrivent par l'open data."""
    time.sleep(DELAI)
    reponse = session.get(SITE + "/fiche-etablissement/" + finess,
                          timeout=60, allow_redirects=True)
    if reponse.status_code != 200 or "non-present" in reponse.url:
        return []

    html = reponse.text
    sortie = []
    for doc_id, slug in dict(RE_LIEN_RAPPORT.findall(html)).items():
        pdf_url = chemin_depuis_fiche(html, slug)
        if pdf_url is None:
            pdf_url = chemin_par_doxiti(doc_id)    # repli
        sortie.append({
            "finess_fiche": finess,
            "doc_id": doc_id,
            "slug": slug,
            "pdf_url": pdf_url,
        })
    return sortie