"""
文献インポートモジュール
RIS / PubMed XML / Endnote XML からArticleを生成し重複除去する
"""

import re
import xml.etree.ElementTree as ET
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Article:
    pmid: str
    title: str
    abstract: str
    authors: list
    journal: str
    year: str
    doi: str = ""
    study_design: str = ""
    source: str = ""   # "pubmed" / "ris" / "xml"


# ──────────────────────────────────────────
# RIS パーサー
# ──────────────────────────────────────────
def parse_ris(text: str, source_label: str = "RIS") -> list[Article]:
    """RIS形式テキストをArticleリストに変換"""
    articles = []
    current: dict = {}

    def flush(d: dict):
        if not d:
            return
        title = d.get("TI") or d.get("T1") or ""
        abstract = d.get("AB") or d.get("N2") or ""
        authors_raw = d.get("AU", [])
        if isinstance(authors_raw, str):
            authors_raw = [authors_raw]
        journal = d.get("JO") or d.get("JF") or d.get("T2") or ""
        year = d.get("PY") or d.get("Y1") or ""
        year = str(year).split("/")[0].strip()
        doi = d.get("DO") or d.get("L3") or ""
        pmid = d.get("AN") or d.get("ID") or ""
        # PMIDらしくない場合は空文字
        if pmid and not re.match(r"^\d+$", str(pmid)):
            pmid = ""
        articles.append(Article(
            pmid=str(pmid),
            title=title,
            abstract=abstract,
            authors=authors_raw,
            journal=journal,
            year=year,
            doi=doi,
            source=source_label,
        ))

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line.startswith("ER"):
            flush(current)
            current = {}
            continue
        if len(line) < 6 or line[2:4] != "  ":
            continue
        tag = line[:2].strip()
        value = line[6:].strip()
        if not tag or not value:
            continue
        if tag == "AU":
            current.setdefault("AU", []).append(value)
        else:
            current[tag] = value

    flush(current)  # 末尾ER なしの場合
    return articles


# ──────────────────────────────────────────
# PubMed XML パーサー  (efetch 形式)
# ──────────────────────────────────────────
def parse_pubmed_xml(text: str) -> list[Article]:
    """PubMed XML (efetch) をArticleリストに変換"""
    articles = []
    try:
        root = ET.fromstring(text.encode("utf-8") if isinstance(text, str) else text)
    except ET.ParseError:
        return articles

    for art_el in root.findall(".//PubmedArticle"):
        try:
            pmid = art_el.findtext(".//PMID", "")
            title = art_el.findtext(".//ArticleTitle", "")
            abstract_parts = art_el.findall(".//AbstractText")
            abstract = " ".join([e.text or "" for e in abstract_parts])
            authors = []
            for au in art_el.findall(".//Author"):
                last = au.findtext("LastName", "")
                fore = au.findtext("ForeName", "")
                if last:
                    authors.append(f"{last} {fore}".strip())
            journal = art_el.findtext(".//Journal/Title", "")
            year = art_el.findtext(".//PubDate/Year", "")
            doi = ""
            for id_el in art_el.findall(".//ArticleId"):
                if id_el.get("IdType") == "doi":
                    doi = id_el.text or ""
            articles.append(Article(
                pmid=pmid, title=title, abstract=abstract,
                authors=authors, journal=journal, year=year, doi=doi,
                source="pubmed_xml",
            ))
        except Exception:
            continue
    return articles


# ──────────────────────────────────────────
# 重複除去
# ──────────────────────────────────────────
def _normalize_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", t.lower())


def deduplicate(articles: list[Article]) -> tuple[list[Article], int]:
    """
    PMID → DOI → タイトル正規化の順で重複を除去。
    Returns (unique_list, n_removed)
    """
    seen_pmids: set = set()
    seen_dois: set = set()
    seen_titles: set = set()
    unique = []

    for art in articles:
        # PMID重複
        if art.pmid and art.pmid in seen_pmids:
            continue
        # DOI重複
        if art.doi and art.doi in seen_dois:
            continue
        # タイトル重複（完全一致）
        norm = _normalize_title(art.title)
        if norm and norm in seen_titles:
            continue

        if art.pmid:
            seen_pmids.add(art.pmid)
        if art.doi:
            seen_dois.add(art.doi)
        if norm:
            seen_titles.add(norm)
        unique.append(art)

    return unique, len(articles) - len(unique)


# ──────────────────────────────────────────
# DataFrame 変換
# ──────────────────────────────────────────
def articles_to_dataframe(articles: list[Article]) -> pd.DataFrame:
    rows = []
    for i, art in enumerate(articles):
        authors_str = "; ".join(art.authors[:3])
        if len(art.authors) > 3:
            authors_str += " et al."
        rows.append({
            "PMID": art.pmid or f"import_{i}",
            "タイトル": art.title,
            "著者": authors_str,
            "雑誌": art.journal,
            "年": art.year,
            "DOI": art.doi,
            "抄録": art.abstract[:500] + "..." if len(art.abstract) > 500 else art.abstract,
            "研究デザイン": art.study_design,
            "ソース": art.source,
            "一次スクリーニング": "",
            "除外理由": "",
        })
    return pd.DataFrame(rows)
