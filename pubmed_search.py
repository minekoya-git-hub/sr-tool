"""
ステップ2: PubMed検索モジュール
スポーツ怪我リスク因子のシステマティックレビュー自動化ツール
"""

import requests
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd


@dataclass
class Article:
    """文献情報を保持するデータクラス"""
    pmid: str
    title: str
    abstract: str
    authors: list[str]
    journal: str
    year: str
    doi: str = ""
    study_design: str = ""  # スクリーニング後に付与


def build_search_query(
    sport: str,
    injury: str,
    population: str = "athlete",
    study_designs: list[str] = None
) -> str:
    """
    PICOに基づく検索クエリを自動生成する

    Parameters
    ----------
    sport       : スポーツ種目 (例: "soccer", "football", "running")
    injury      : 怪我の種類 (例: "hamstring", "ACL", "ankle sprain")
    population  : 対象集団 (デフォルト: "athlete")
    study_designs: 研究デザイン (デフォルト: コホート+ケースコントロール)

    Returns
    -------
    PubMed検索式 (文字列)
    """
    if study_designs is None:
        study_designs = [
            "cohort study",
            "case-control study",
            "prospective study",
            "retrospective study"
        ]

    # 怪我の検索語
    injury_terms = f'("{injury} injur*"[tiab] OR "{injury} strain*"[tiab] OR "{injury} tear*"[tiab])'

    # スポーツの検索語
    sport_terms = f'("{sport}"[tiab] OR "{sport} player*"[tiab] OR "{sport} athlete*"[tiab])'

    # 危険因子の検索語
    risk_terms = '("risk factor*"[tiab] OR "predictor*"[tiab] OR "odds ratio"[tiab] OR "relative risk"[tiab] OR "hazard ratio"[tiab])'

    # 研究デザインの検索語
    design_parts = " OR ".join([f'"{d}"[pt]' for d in study_designs])
    design_terms = f"({design_parts} OR 'observational study'[pt])"

    query = f"{injury_terms} AND {sport_terms} AND {risk_terms} AND {design_terms}"
    return query


def search_pubmed(query: str, max_results: int = 200, email: str = "minekoya0@gmail.com") -> list[str]:
    """
    PubMed APIでPMIDのリストを取得する

    Parameters
    ----------
    query       : 検索式
    max_results : 最大取得件数
    email       : NCBI APIに必要なメールアドレス

    Returns
    -------
    PMIDのリスト
    """
    base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"

    params = {
        "db": "pubmed",
        "term": query,
        "retmax": max_results,
        "retmode": "json",
        "email": email,
        "tool": "sports_injury_sr_tool"
    }

    resp = requests.get(f"{base_url}esearch.fcgi", params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    pmids = data.get("esearchresult", {}).get("idlist", [])
    total = int(data.get("esearchresult", {}).get("count", 0))
    print(f"  検索結果: {total}件中 {len(pmids)}件を取得")
    return pmids


def fetch_articles(pmids: list[str], email: str = "minekoya0@gmail.com", batch_size: int = 20) -> list[Article]:
    """
    PMIDリストから文献情報を一括取得する

    Parameters
    ----------
    pmids      : PMIDのリスト
    email      : NCBI APIに必要なメールアドレス
    batch_size : 一度に取得する件数（NCBIの推奨は最大200）

    Returns
    -------
    Articleオブジェクトのリスト
    """
    base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    articles = []

    for i in range(0, len(pmids), batch_size):
        batch = pmids[i:i + batch_size]
        params = {
            "db": "pubmed",
            "id": ",".join(batch),
            "retmode": "xml",
            "email": email,
            "tool": "sports_injury_sr_tool"
        }

        resp = requests.get(f"{base_url}efetch.fcgi", params=params, timeout=30)
        resp.raise_for_status()

        root = ET.fromstring(resp.content)

        for article_el in root.findall(".//PubmedArticle"):
            try:
                pmid = article_el.findtext(".//PMID", "")
                title = article_el.findtext(".//ArticleTitle", "タイトルなし")
                abstract_el = article_el.findall(".//AbstractText")
                abstract = " ".join([el.text or "" for el in abstract_el]) if abstract_el else ""

                authors = []
                for author in article_el.findall(".//Author"):
                    last = author.findtext("LastName", "")
                    fore = author.findtext("ForeName", "")
                    if last:
                        authors.append(f"{last} {fore}".strip())

                journal = article_el.findtext(".//Journal/Title", "")
                year = article_el.findtext(".//PubDate/Year", "")

                doi = ""
                for id_el in article_el.findall(".//ArticleId"):
                    if id_el.get("IdType") == "doi":
                        doi = id_el.text or ""

                articles.append(Article(
                    pmid=pmid,
                    title=title,
                    abstract=abstract,
                    authors=authors,
                    journal=journal,
                    year=year,
                    doi=doi
                ))
            except Exception as e:
                print(f"  警告: PMID {pmid} の取得でエラー: {e}")
                continue

        # NCBIのレートリミット対策（1秒待機）
        time.sleep(1)
        print(f"  {min(i + batch_size, len(pmids))}/{len(pmids)} 件取得完了")

    return articles


def remove_duplicates(articles: list[Article]) -> list[Article]:
    """PMIDとDOIで重複を除去する"""
    seen_pmids = set()
    seen_dois = set()
    unique = []

    for art in articles:
        if art.pmid in seen_pmids:
            continue
        if art.doi and art.doi in seen_dois:
            continue
        seen_pmids.add(art.pmid)
        if art.doi:
            seen_dois.add(art.doi)
        unique.append(art)

    removed = len(articles) - len(unique)
    if removed > 0:
        print(f"  重複除去: {removed}件を除外 → {len(unique)}件")
    return unique


def articles_to_dataframe(articles: list[Article]) -> pd.DataFrame:
    """ArticleリストをDataFrameに変換する"""
    rows = []
    for art in articles:
        rows.append({
            "PMID": art.pmid,
            "タイトル": art.title,
            "著者": "; ".join(art.authors[:3]) + (" et al." if len(art.authors) > 3 else ""),
            "雑誌": art.journal,
            "年": art.year,
            "DOI": art.doi,
            "抄録": art.abstract[:300] + "..." if len(art.abstract) > 300 else art.abstract,
            "研究デザイン": art.study_design,
            "一次スクリーニング": "",  # スクリーニングで記入
            "除外理由": ""
        })
    return pd.DataFrame(rows)


if __name__ == "__main__":
    # テスト実行: ハムストリング損傷（サッカー）
    print("=== PubMed検索モジュール テスト ===\n")

    print("① 検索クエリを生成中...")
    query = build_search_query(sport="soccer", injury="hamstring")
    print(f"  クエリ: {query[:100]}...\n")

    print("② PubMedを検索中...")
    pmids = search_pubmed(query, max_results=50)
    print(f"  取得PMID数: {len(pmids)}\n")

    if pmids:
        print("③ 文献情報を取得中...")
        articles = fetch_articles(pmids[:10])  # テストは10件のみ

        print("\n④ 重複を除去中...")
        articles = remove_duplicates(articles)

        print("\n⑤ データフレームに変換中...")
        df = articles_to_dataframe(articles)
        df.to_csv("/home/user/sr_tool/search_results_test.csv", index=False, encoding="utf-8-sig")
        print(f"  保存完了: search_results_test.csv ({len(df)}件)")

        print("\n--- 取得結果サンプル（3件）---")
        for _, row in df.head(3).iterrows():
            print(f"  [{row['年']}] {row['タイトル'][:60]}...")
            print(f"         著者: {row['著者']}")
            print()

    print("=== テスト完了 ===")
