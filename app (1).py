"""
スポーツ傷害リスク因子 システマティックレビュー自動化ツール
メインアプリケーション (Streamlit)
"""

import streamlit as st
import pandas as pd
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))

# ─── ページ設定（最初に呼ぶ） ───
st.set_page_config(
    page_title="SR自動化ツール – スポーツ傷害リスク因子",
    page_icon="🏃",
    layout="wide",
)

# ─── グローバル CSS ───
st.markdown("""
<style>
/* タブ */
div[data-testid="stTabs"] button {
    font-size: 0.95rem;
    font-weight: 600;
    padding: 0.45rem 1.1rem;
}
/* カード風メトリクス */
div[data-testid="metric-container"] {
    background: #f8f9fa;
    border-radius: 8px;
    padding: 0.6rem 1rem;
    border: 1px solid #e0e0e0;
}
/* ヘッダー */
h1 { font-size: 1.6rem !important; }
h2 { font-size: 1.2rem !important; border-bottom: 2px solid #1a73e8; padding-bottom: 4px; }
</style>
""", unsafe_allow_html=True)

# ─── セッション状態の初期化 ───
_defaults = {
    "step": 1,
    "articles_df": None,      # 全文献 DataFrame（PubMed + インポート統合後）
    "pico": {},
    "query": "",
    "pubmed_done": False,
    "import_articles": [],    # RIS/XML からインポートした Article リスト
    "n_pubmed": 0,
    "n_import": 0,
    "n_removed_dup": 0,
    "ai_screening_done": False,
    "substep": "primary",
    "extraction_results": {},
    "bias_results": {},
    "bias_ratings": {},
    "bias_confirmed": {},
    "meta_result": None,
    "meta_studies": [],
    "sa_exclusions": {},
    "sa_result": None,
    "sa_included": [],
}
for k, v in _defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ─── タイトル ───
st.markdown("## 🏃 スポーツ傷害リスク因子 SR自動化ツール")
st.caption("PubMed検索 ＋ 手動インポート → スクリーニング → データ抽出 → メタアナリシス → 出力")

# ─── タブナビゲーション ───
TAB_LABELS = [
    "① PICO & 検索",
    "② スクリーニング",
    "③ データ抽出",
    "④ メタアナリシス",
    "⑤ バイアス評価",
    "⑥ 出力",
]
# step (1-6) → tab index (0-5)
active_tab = st.session_state.step - 1

tabs = st.tabs(TAB_LABELS)

# ════════════════════════════════════════════════════
# TAB 1 : PICO & 検索 ＋ インポート
# ════════════════════════════════════════════════════
with tabs[0]:
    from pubmed_search import build_search_query, search_pubmed, fetch_articles
    from import_refs import (
        parse_ris, parse_pubmed_xml, deduplicate,
        articles_to_dataframe, Article as ImportArticle,
    )

    st.header("① PICO 設定 ＆ 文献収集")

    # ── PICO フォーム ──
    with st.expander("🔧 PICO 設定", expanded=True):
        col1, col2 = st.columns(2)

        with col1:
            st.subheader("P（対象集団 / スポーツ）")
            sport_options = [
                "soccer / football（サッカー）",
                "rugby（ラグビー）",
                "basketball（バスケットボール）",
                "american football（アメフト）",
                "athletics（陸上競技）",
                "swimming（水泳）",
                "tennis（テニス）",
                "baseball（野球）",
                "volleyball（バレーボール）",
                "softball（ソフトボール）",
                "その他（自由入力）",
            ]
            sport_label = st.selectbox("スポーツ種目", sport_options, key="sport_select")
            if sport_label == "その他（自由入力）":
                sport = st.text_input("スポーツ種目（英語）", placeholder="例: gymnastics, judo")
            else:
                sport = sport_label.split("（")[0].split(" / ")[0].strip()
                st.caption(f"検索語: `{sport}`")
            population = st.text_input("対象集団", value="athlete")

        with col2:
            st.subheader("O（アウトカム）")
            injury_options = [
                "hamstring strain（ハムストリング損傷）",
                "ACL injury（前十字靭帯損傷）",
                "その他（自由入力）",
            ]
            injury_label = st.selectbox("傷害の種類", injury_options, key="injury_select")
            if injury_label == "その他（自由入力）":
                injury = st.text_input("傷害の種類（英語）", placeholder="例: rotator cuff tendinopathy")
            else:
                injury = injury_label.split("（")[0].strip()
                st.caption(f"検索語: `{injury}`")

        st.subheader("研究デザイン（複数選択可）")
        d_cols = st.columns(4)
        study_designs = []
        design_options = {
            "コホート研究": "cohort study",
            "ケースコントロール研究": "case-control study",
            "前向き研究": "prospective study",
            "後ろ向き研究": "retrospective study",
        }
        for col, (lbl, val) in zip(d_cols, design_options.items()):
            if col.checkbox(lbl, value=True, key=f"design_{val}"):
                study_designs.append(val)

        email = st.text_input("メールアドレス（NCBI API用）", value="minekoya0@gmail.com",
                              help="PubMed APIの利用規約に必要です")

        if st.button("🔍 クエリをプレビュー"):
            q = build_search_query(sport, injury, population, study_designs or None)
            st.code(q, language="text")

    st.divider()

    # ── PubMed 検索 ──
    col_pub, col_imp = st.columns([1, 1])

    with col_pub:
        st.subheader("📚 PubMed 検索")
        can_search = bool(sport) and bool(injury) and len(study_designs) > 0
        if st.button("▶ PubMed を検索", type="primary", disabled=not can_search):
            st.session_state.pico = {
                "sport": sport, "injury": injury,
                "population": population, "study_designs": study_designs,
                "email": email,
            }
            query = build_search_query(sport, injury, population, study_designs)
            st.session_state.query = query

            with st.spinner("PubMedを検索中..."):
                try:
                    pmids = search_pubmed(query, max_results=9999, email=email)
                    n_pmids = len(pmids)
                    articles_list = []
                    if n_pmids > 0:
                        prog = st.progress(0)
                        status = st.empty()
                        batch_size = 20
                        for i in range(0, n_pmids, batch_size):
                            batch = pmids[i:i+batch_size]
                            arts = fetch_articles(batch, email=email, batch_size=batch_size)
                            articles_list.extend(arts)
                            prog.progress(min((i+batch_size)/n_pmids, 1.0))
                            status.text(f"{min(i+batch_size, n_pmids)}/{n_pmids}件取得中...")
                        prog.empty()
                        status.empty()

                    # Article → ImportArticle に変換して保存
                    import_arts = [
                        ImportArticle(
                            pmid=a.pmid, title=a.title, abstract=a.abstract,
                            authors=a.authors, journal=a.journal, year=a.year,
                            doi=a.doi, source="pubmed",
                        ) for a in articles_list
                    ]
                    st.session_state.pubmed_articles = import_arts
                    st.session_state.n_pubmed = len(import_arts)
                    st.session_state.pubmed_done = True
                    st.success(f"✅ PubMed: **{len(import_arts)}件** 取得完了")
                except Exception as e:
                    st.error(f"PubMed検索エラー: {e}")

        if st.session_state.pubmed_done:
            st.info(f"PubMed取得済み: **{st.session_state.n_pubmed}件**")
            st.code(st.session_state.query[:200] + ("..." if len(st.session_state.query)>200 else ""), language="text")

    # ── 手動インポート ──
    with col_imp:
        st.subheader("📂 手動インポート（RIS / PubMed XML）")
        st.caption("他のデータベース（Embase, Cochrane 等）から書き出したファイルをここでインポート")

        uploaded_files = st.file_uploader(
            "RIS / XML ファイルをアップロード（複数可）",
            type=["ris", "txt", "xml"],
            accept_multiple_files=True,
            key="import_uploader",
        )

        if uploaded_files and st.button("📥 インポートを実行"):
            imported = []
            for uf in uploaded_files:
                raw = uf.read().decode("utf-8", errors="replace")
                ext = uf.name.lower()
                if ext.endswith(".xml"):
                    arts = parse_pubmed_xml(raw)
                    label = f"XML:{uf.name}"
                else:
                    arts = parse_ris(raw, source_label=f"RIS:{uf.name}")
                    label = f"RIS:{uf.name}"
                imported.extend(arts)
                st.caption(f"  {label} → {len(arts)}件")

            st.session_state.import_articles = imported
            st.session_state.n_import = len(imported)
            st.success(f"✅ インポート完了: **{len(imported)}件**")

        if st.session_state.n_import > 0:
            st.info(f"インポート済み: **{st.session_state.n_import}件**")

    st.divider()

    # ── 統合 & 重複除去 → スクリーニング開始 ──
    pubmed_arts = st.session_state.get("pubmed_articles", [])
    import_arts = st.session_state.get("import_articles", [])
    total_before = len(pubmed_arts) + len(import_arts)

    st.subheader("🔄 統合 & 重複除去")
    mc1, mc2, mc3 = st.columns(3)
    mc1.metric("PubMed", f"{len(pubmed_arts)}件")
    mc2.metric("手動インポート", f"{len(import_arts)}件")
    mc3.metric("合計（除去前）", f"{total_before}件")

    can_screen = total_before > 0
    if st.button("✅ 重複を除去してスクリーニングを開始", type="primary", disabled=not can_screen):
        all_arts = pubmed_arts + import_arts
        unique_arts, n_removed = deduplicate(all_arts)
        df = articles_to_dataframe(unique_arts)
        st.session_state.articles_df = df
        st.session_state.n_removed_dup = n_removed
        st.session_state.ai_screening_done = False
        st.session_state.pico = st.session_state.get("pico") or {
            "sport": sport, "injury": injury, "population": population,
            "study_designs": study_designs, "email": email,
        }
        st.success(f"✅ 重複除去: {n_removed}件 → 残り **{len(unique_arts)}件** → スクリーニングタブへ")
        st.session_state.step = 2
        st.rerun()

    if st.session_state.articles_df is not None:
        n_df = len(st.session_state.articles_df)
        n_rem = st.session_state.n_removed_dup
        st.info(f"統合済み: **{n_df}件**（重複除去済み {n_rem}件）")


# ════════════════════════════════════════════════════
# TAB 2 : スクリーニング
# ════════════════════════════════════════════════════
with tabs[1]:
    from ai_screening import batch_screen

    df = st.session_state.articles_df
    pico = st.session_state.get("pico", {})

    if df is None or df.empty:
        st.info("まず「① PICO & 検索」タブで文献を収集してください。")
        st.stop()

    # ── サブタブ: 一次 / 二次 ──
    sub1, sub2 = st.tabs(["📋 一次スクリーニング（タイトル・抄録）", "📄 二次スクリーニング（フルテキスト）"])

    # ── 一次スクリーニング ──
    with sub1:
        st.header("一次スクリーニング")

        total = len(df)
        n_inc = (df["一次スクリーニング"] == "採択").sum()
        n_exc = (df["一次スクリーニング"] == "除外").sum()
        n_pend = (df["一次スクリーニング"] == "").sum()

        mc1, mc2, mc3, mc4 = st.columns(4)
        mc1.metric("総件数", total)
        mc2.metric("採択", n_inc)
        mc3.metric("除外", n_exc)
        mc4.metric("未処理", n_pend)

        # AI スクリーニング
        if not st.session_state.ai_screening_done or n_pend == total:
            if st.button("🤖 AI スクリーニングを実行（全件）", type="primary"):
                with st.spinner(f"AIが{total}件を解析中..."):
                    results = batch_screen(
                        df,
                        sport=pico.get("sport", ""),
                        injury=pico.get("injury", ""),
                        population=pico.get("population", "athlete"),
                    )
                    for res in results:
                        pmid = res["pmid"]
                        mask = st.session_state.articles_df["PMID"].astype(str) == pmid
                        for idx in st.session_state.articles_df[mask].index:
                            dec = res["decision"]
                            mapped = "採択" if dec in ("採択", "要確認") else "除外"
                            st.session_state.articles_df.at[idx, "一次スクリーニング"] = mapped
                            st.session_state.articles_df.at[idx, "AI判定"] = dec
                            st.session_state.articles_df.at[idx, "AIスコア"] = res["score"]
                            st.session_state.articles_df.at[idx, "AI理由"] = res["reason"]
                            if dec == "除外":
                                st.session_state.articles_df.at[idx, "除外理由"] = res["reason"]
                    st.session_state.ai_screening_done = True
                st.rerun()
        else:
            ai_col1, ai_col2 = st.columns([3, 1])
            ai_col1.success(f"✅ AI判定完了 — 採択:{n_inc}件 / 除外:{n_exc}件")
            if ai_col2.button("🔄 再実行"):
                st.session_state.articles_df["一次スクリーニング"] = ""
                st.session_state.articles_df["除外理由"] = ""
                st.session_state.ai_screening_done = False
                st.rerun()

        st.divider()
        st.subheader("結果の確認・手動修正")

        # フィルター
        fc1, fc2, fc3 = st.columns(3)
        show_st = fc1.selectbox("表示", ["全件", "採択", "除外", "要確認"], key="pri_filter")
        search_q = fc2.text_input("タイトル検索", key="pri_search")
        sort_k = fc3.selectbox("並び順", ["AIスコア（低い順）", "AIスコア（高い順）", "年（新しい順）"])

        disp = st.session_state.articles_df.copy()
        if show_st == "採択":
            disp = disp[disp["一次スクリーニング"] == "採択"]
        elif show_st == "除外":
            disp = disp[disp["一次スクリーニング"] == "除外"]
        elif show_st == "要確認":
            if "AI判定" in disp.columns:
                disp = disp[disp["AI判定"] == "要確認"]
        if search_q:
            disp = disp[disp["タイトル"].str.contains(search_q, case=False, na=False)]
        if "AIスコア" in disp.columns:
            if sort_k == "AIスコア（低い順）":
                disp = disp.sort_values("AIスコア")
            elif sort_k == "AIスコア（高い順）":
                disp = disp.sort_values("AIスコア", ascending=False)
        if sort_k == "年（新しい順）":
            disp = disp.sort_values("年", ascending=False)

        st.write(f"{len(disp)}件を表示中（最大100件）")

        for idx, row in disp.head(100).iterrows():
            cur = st.session_state.articles_df.at[idx, "一次スクリーニング"]
            ai_d = st.session_state.articles_df.at[idx, "AI判定"] if "AI判定" in st.session_state.articles_df.columns else ""
            ai_s = st.session_state.articles_df.at[idx, "AIスコア"] if "AIスコア" in st.session_state.articles_df.columns else ""
            ai_r = st.session_state.articles_df.at[idx, "AI理由"] if "AI理由" in st.session_state.articles_df.columns else ""
            icon = {"採択": "✅", "除外": "❌"}.get(cur, "⬜")
            warn = " ⚠️" if ai_d == "要確認" else ""
            sc_s = f" [{ai_s}]" if ai_s != "" else ""
            src_s = f" [{row.get('ソース','pubmed')}]" if 'ソース' in row else ""

            with st.expander(f"{icon}{warn} [{row['年']}] {row['タイトル'][:80]}{sc_s}{src_s}"):
                st.write(f"**著者:** {row['著者']}  |  **雑誌:** {row['雑誌']}  |  **PMID:** {row['PMID']}")
                if row.get('DOI'):
                    st.write(f"**DOI:** https://doi.org/{row['DOI']}")
                if ai_r:
                    st.caption(f"🤖 AI判定理由: {ai_r}")
                st.write(f"**抄録:** {row['抄録']}")
                reason = st.text_input("除外理由", value=row.get("除外理由",""), key=f"pr_reason_{idx}")
                bc1, bc2, bc3 = st.columns(3)
                if bc1.button("✅ 採択", key=f"pr_inc_{idx}"):
                    st.session_state.articles_df.at[idx, "一次スクリーニング"] = "採択"
                    st.session_state.articles_df.at[idx, "除外理由"] = ""
                    st.rerun()
                if bc2.button("❌ 除外", key=f"pr_exc_{idx}"):
                    st.session_state.articles_df.at[idx, "一次スクリーニング"] = "除外"
                    st.session_state.articles_df.at[idx, "除外理由"] = reason
                    st.rerun()
                if bc3.button("↩️ AI判定に戻す", key=f"pr_rst_{idx}"):
                    orig = ai_d if ai_d in ("採択","除外") else "採択"
                    st.session_state.articles_df.at[idx, "一次スクリーニング"] = orig
                    st.session_state.articles_df.at[idx, "除外理由"] = ai_r if orig=="除外" else ""
                    st.rerun()

        # CSV エクスポート
        csv = st.session_state.articles_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
        st.download_button("📥 CSVをダウンロード", data=csv,
                           file_name=f"primary_screening.csv", mime="text/csv")

    # ── 二次スクリーニング ──
    with sub2:
        st.header("二次スクリーニング（フルテキスト）")
        df_ft_base = st.session_state.articles_df
        ft_df = df_ft_base[df_ft_base["一次スクリーニング"] == "採択"].copy()

        if ft_df.empty:
            st.info("一次スクリーニングで採択された文献がありません。")
            st.stop()

        if "FTステータス" not in st.session_state.articles_df.columns:
            st.session_state.articles_df["FTステータス"] = ""
            st.session_state.articles_df["PDFパス"] = ""

        ft_total = len(ft_df)
        ft_ready = (st.session_state.articles_df.loc[ft_df.index, "FTステータス"] == "PDF済み").sum()
        ft_na    = (st.session_state.articles_df.loc[ft_df.index, "FTステータス"] == "入手不可").sum()
        ft_pend  = ft_total - ft_ready - ft_na

        mc1, mc2, mc3, mc4 = st.columns(4)
        mc1.metric("FT対象", ft_total)
        mc2.metric("⏳ 保留中", ft_pend)
        mc3.metric("✅ PDF済み", ft_ready)
        mc4.metric("❌ 入手不可", ft_na)

        st.divider()

        ft_filter = st.selectbox("表示フィルター", ["全件", "保留中のみ", "PDF済み", "入手不可"])
        disp_ft = ft_df.copy()
        if ft_filter == "保留中のみ":
            disp_ft = disp_ft[st.session_state.articles_df.loc[ft_df.index, "FTステータス"] == ""]
        elif ft_filter == "PDF済み":
            disp_ft = disp_ft[st.session_state.articles_df.loc[ft_df.index, "FTステータス"] == "PDF済み"]
        elif ft_filter == "入手不可":
            disp_ft = disp_ft[st.session_state.articles_df.loc[ft_df.index, "FTステータス"] == "入手不可"]

        st.write(f"{len(disp_ft)}件を表示中")

        for idx, row in disp_ft.iterrows():
            ft_status = st.session_state.articles_df.at[idx, "FTステータス"]
            st_icon = {"PDF済み":"✅","入手不可":"❌"}.get(ft_status, "⏳")

            with st.expander(f"{st_icon} [{row['年']}] {row['タイトル'][:80]}"):
                st.write(f"**著者:** {row['著者']}  |  **PMID:** {row['PMID']}")
                if row.get('DOI'):
                    doi_url = f"https://doi.org/{row['DOI']}"
                    st.markdown(f"**DOI:** [{doi_url}]({doi_url})")
                    # PMC無料リンク
                    st.caption("💡 無料PDF: [PubMed Central](https://www.ncbi.nlm.nih.gov/pmc/) または [Unpaywall](https://unpaywall.org/) で検索")

                uploaded_pdf = st.file_uploader(
                    "PDFをアップロード", type=["pdf"], key=f"pdf_up_{idx}"
                )
                if uploaded_pdf is not None:
                    # /tmp を使用（Streamlit Cloud で書き込み可能）
                    tmp_dir = tempfile.mkdtemp()
                    save_path = os.path.join(tmp_dir, f"{row['PMID']}.pdf")
                    with open(save_path, "wb") as f:
                        f.write(uploaded_pdf.read())
                    st.session_state.articles_df.at[idx, "FTステータス"] = "PDF済み"
                    st.session_state.articles_df.at[idx, "PDFパス"] = save_path
                    st.success(f"✅ アップロード完了: {uploaded_pdf.name}")
                    st.rerun()

                bc1, bc2 = st.columns(2)
                if ft_status == "PDF済み":
                    bc1.success("✅ PDF済み")
                    if bc2.button("↩️ リセット", key=f"ft_rst_{idx}"):
                        st.session_state.articles_df.at[idx, "FTステータス"] = ""
                        st.session_state.articles_df.at[idx, "PDFパス"] = ""
                        st.rerun()
                elif ft_status == "入手不可":
                    bc1.error("❌ 入手不可")
                    if bc2.button("↩️ リセット", key=f"ft_rst2_{idx}"):
                        st.session_state.articles_df.at[idx, "FTステータス"] = ""
                        st.rerun()
                else:
                    if bc1.button("❌ 入手不可（除外）", key=f"ft_na_{idx}"):
                        st.session_state.articles_df.at[idx, "FTステータス"] = "入手不可"
                        st.session_state.articles_df.at[idx, "除外理由"] = "Full text not available"
                        st.rerun()


# ════════════════════════════════════════════════════
# TAB 3 : データ抽出
# ════════════════════════════════════════════════════
with tabs[2]:
    from data_extraction import extract_from_pdf

    st.header("③ データ抽出")

    df = st.session_state.articles_df
    if df is None or df.empty:
        st.info("文献がありません。")
        st.stop()

    pdf_df = df[(df["一次スクリーニング"] == "採択") & (df.get("FTステータス", "") == "PDF済み")] \
        if "FTステータス" in df.columns else df[df["一次スクリーニング"] == "採択"]

    total_pdfs = len(pdf_df)
    extracted_count = len(st.session_state.extraction_results)

    mc1, mc2 = st.columns(2)
    mc1.metric("PDF対象", total_pdfs)
    mc2.metric("抽出済み", extracted_count)

    if total_pdfs == 0:
        st.info("PDFがアップロードされた採択文献がありません。二次スクリーニングでPDFをアップロードしてください。")
        st.stop()

    if st.button("🤖 全PDFから自動抽出", type="primary"):
        prog = st.progress(0)
        status = st.empty()
        for i, (idx, row) in enumerate(pdf_df.iterrows()):
            pmid = str(row["PMID"])
            pdf_path = row.get("PDFパス", "")
            if pmid in st.session_state.extraction_results:
                prog.progress((i+1)/total_pdfs)
                continue
            if pdf_path and os.path.exists(pdf_path):
                status.text(f"抽出中: {row['タイトル'][:50]}...")
                try:
                    data = extract_from_pdf(pdf_path, pmid=pmid, title=row["タイトル"])
                    st.session_state.extraction_results[pmid] = data.outcomes
                except Exception as e:
                    st.session_state.extraction_results[pmid] = []
                    st.warning(f"PMID {pmid}: エラー ({e})")
            prog.progress((i+1)/total_pdfs)
        status.text("✅ 自動抽出完了")
        st.rerun()

    st.divider()
    all_rows = []
    for idx, row in pdf_df.iterrows():
        pmid = str(row["PMID"])
        outcomes = st.session_state.extraction_results.get(pmid)
        with st.expander(f"📄 [{row['年']}] {row['タイトル'][:70]}"):
            if outcomes is None:
                st.warning("未抽出")
                continue
            with st.form(key=f"add_{pmid}"):
                fc1, fc2, fc3, fc4, fc5 = st.columns([3,1,1,1,1])
                nf = fc1.text_input("危険因子", key=f"nf_{pmid}", placeholder="previous hamstring injury")
                nm = fc2.selectbox("種類", ["RR","OR","HR","IRR","SMD"], key=f"nm_{pmid}")
                nv = fc3.number_input("効果量", min_value=0.0, value=1.0, step=0.01, key=f"nv_{pmid}")
                nl = fc4.number_input("CI下限", min_value=0.0, value=0.0, step=0.01, key=f"ncil_{pmid}")
                nu = fc5.number_input("CI上限", min_value=0.0, value=0.0, step=0.01, key=f"nciu_{pmid}")
                if st.form_submit_button("＋ 追加"):
                    st.session_state.extraction_results.setdefault(pmid, []).append({
                        "risk_factor": nf, "measure_type": nm, "value": nv,
                        "ci_lower": nl if nl>0 else None,
                        "ci_upper": nu if nu>0 else None,
                        "has_ci": nl>0 and nu>0, "context": "手動入力", "confirmed": True,
                    })
                    st.rerun()
            for oi, outcome in enumerate(outcomes):
                ci_str = f"{outcome['ci_lower']}–{outcome['ci_upper']}" if outcome.get("has_ci") else "CI不明"
                c1, c2, c3, c4 = st.columns([3,2,2,1])
                new_f = c1.text_input("危険因子", value=outcome.get("risk_factor",""),
                                      key=f"factor_{pmid}_{oi}", placeholder="危険因子名")
                c2.write(f"**{outcome['measure_type']}** = {outcome['value']}")
                c3.write(f"95%CI: {ci_str}")
                if c4.button("🗑️", key=f"del_{pmid}_{oi}"):
                    st.session_state.extraction_results[pmid].pop(oi)
                    st.rerun()
                st.session_state.extraction_results[pmid][oi]["risk_factor"] = new_f
                all_rows.append({
                    "PMID": pmid, "タイトル": row["タイトル"][:50], "年": row["年"],
                    "危険因子": new_f, "効果量の種類": outcome["measure_type"],
                    "効果量": outcome["value"], "CI下限": outcome.get("ci_lower",""),
                    "CI上限": outcome.get("ci_upper",""),
                })

    if all_rows:
        result_df = pd.DataFrame(all_rows)
        st.subheader("抽出データ一覧")
        st.dataframe(result_df, use_container_width=True)
        csv = result_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
        st.download_button("📥 CSVをダウンロード", data=csv, file_name="extracted_data.csv", mime="text/csv")
        if st.button("▶ メタアナリシスへ", type="primary"):
            st.session_state.extraction_df = result_df
            st.session_state.step = 4
            st.rerun()


# ════════════════════════════════════════════════════
# TAB 4 : メタアナリシス
# ════════════════════════════════════════════════════
with tabs[3]:
    from meta_analysis import (
        StudyData, run_meta_analysis, interpret_heterogeneity,
        heterogeneity_recommendation, auto_select_method,
    )
    from forest_plot import forest_plot as make_forest_plot

    st.header("④ メタアナリシス")

    extraction_df = st.session_state.get("extraction_df", pd.DataFrame())
    pico = st.session_state.get("pico", {})

    if extraction_df.empty:
        st.info("データ抽出が完了していません。")
        st.stop()

    has_ci = extraction_df["CI下限"].apply(lambda x: bool(x) and float(x)>0 if x else False)
    ci_df = extraction_df[has_ci].copy()

    if ci_df.empty:
        st.warning("95%CI付きデータがありません。")
        st.stop()

    risk_factors = [r for r in ci_df["危険因子"].unique().tolist() if str(r).strip()]
    if not risk_factors:
        st.warning("危険因子名を入力してください（データ抽出タブ）。")
        st.stop()

    selected_factor = st.selectbox("分析する危険因子を選択", risk_factors)
    factor_df = ci_df[ci_df["危険因子"] == selected_factor]
    st.write(f"**{selected_factor}** を報告した研究: {len(factor_df)}件")

    if len(factor_df) < 2:
        st.warning("2件以上の研究が必要です。")
        st.stop()

    studies = []
    for _, row in factor_df.iterrows():
        try:
            studies.append(StudyData(
                study_id=f"{row['タイトル'][:30]} ({row['年']})",
                effect=float(row["効果量"]),
                ci_lower=float(row["CI下限"]),
                ci_upper=float(row["CI上限"]),
                measure_type=str(row["効果量の種類"]),
            ))
        except Exception:
            continue

    if len(studies) < 2:
        st.warning("有効データが2件未満です。")
        st.stop()

    auto_method, auto_var, auto_reason = auto_select_method(studies)
    st.info(f"**自動選択** — 手法: {auto_method}  分散: {auto_var}  理由: {auto_reason}")
    method_code = auto_method
    var_code = auto_var
    force_fixed = False
    with st.expander("🔧 手法を手動で変更"):
        mc1, mc2, mc3 = st.columns(3)
        manual_method = mc1.selectbox("統合手法", ["MH（Mantel-Haenszel）","IV（Inverse Variance）","Peto"],
                                      index=["MH","IV","Peto"].index(auto_method))
        manual_var = mc2.selectbox("分散推定法", ["REML（推奨）","DL（DerSimonian-Laird）","fixed（固定効果）"],
                                   index=["REML","DL","fixed"].index(auto_var))
        force_fixed = mc3.checkbox("固定効果モデルを強制")
        method_code = manual_method.split("（")[0].strip()
        var_code = manual_var.split("（")[0].strip()

    if st.button("▶ メタアナリシスを実行", type="primary"):
        try:
            result, _, _, _ = run_meta_analysis(studies, method=method_code,
                                                variance_method=var_code, force_fixed=force_fixed)
            st.session_state.meta_result = result
            st.session_state.meta_studies = studies
            st.session_state.selected_factor = selected_factor
        except Exception as e:
            st.error(f"エラー: {e}")

    if st.session_state.meta_result:
        result = st.session_state.meta_result
        studies_w = st.session_state.meta_studies

        r1,r2,r3,r4 = st.columns(4)
        r1.metric("プール効果量", f"{result.pooled_effect:.2f}")
        r2.metric("95%CI", f"{result.ci_lower:.2f}–{result.ci_upper:.2f}")
        lbl, icon = interpret_heterogeneity(result.i2)
        r3.metric("I²", f"{result.i2}%  {icon} {lbl}")
        r4.metric("研究数", result.n_studies)
        st.write(f"Q={result.q_stat}  p={result.q_pvalue}  τ²={result.tau2}")
        st.info(heterogeneity_recommendation(result.i2))

        weight_rows = [{"研究": s.study_id, "効果量": s.effect,
                        "CI下限": s.ci_lower, "CI上限": s.ci_upper,
                        "重み(%)": f"{s.weight:.1f}%"} for s in studies_w]
        st.dataframe(pd.DataFrame(weight_rows), use_container_width=True)

        # フォレストプロット
        st.subheader("🌲 フォレストプロット")
        ci_studies = [s for s in studies_w if s.ci_lower and s.ci_upper]
        buf = make_forest_plot(
            study_ids=[s.study_id for s in ci_studies],
            effects=[s.effect for s in ci_studies],
            ci_lowers=[s.ci_lower for s in ci_studies],
            ci_uppers=[s.ci_upper for s in ci_studies],
            weights=[s.weight for s in ci_studies],
            pooled_effect=result.pooled_effect,
            pooled_ci_lower=result.ci_lower,
            pooled_ci_upper=result.ci_upper,
            measure_type=result.measure_type,
            title=f"{selected_factor} — {result.measure_type}",
            i2=result.i2, tau2=result.tau2,
        )
        st.image(buf, use_container_width=True)
        buf.seek(0)
        st.download_button("📥 PNG ダウンロード", data=buf,
                           file_name=f"forest_{selected_factor}.png", mime="image/png")

        # 感度分析
        st.divider()
        st.subheader("🔍 感度分析")
        bias_map = st.session_state.get("bias_results", {})

        df_for_sa = st.session_state.get("extraction_df", pd.DataFrame())
        sa_rows = []
        for s in studies_w:
            matched_overall = "未評価"
            for _, ex_row in df_for_sa.iterrows():
                if str(ex_row.get("タイトル",""))[:30] in s.study_id:
                    pmid_m = str(ex_row["PMID"])
                    if pmid_m in bias_map:
                        matched_overall = bias_map[pmid_m].overall
                    break
            if s.study_id not in st.session_state.sa_exclusions:
                st.session_state.sa_exclusions[s.study_id] = (matched_overall == "High")
            sa_rows.append({"study": s, "bias": matched_overall,
                             "excluded": st.session_state.sa_exclusions[s.study_id]})

        hc1, hc2, hc3, hc4 = st.columns([4,2,2,1])
        hc1.markdown("**研究**"); hc2.markdown("**効果量 (95%CI)**")
        hc3.markdown("**バイアス**"); hc4.markdown("**含める**")
        for row_d in sa_rows:
            s = row_d["study"]
            bias_icon = {"Low":"🟢","Moderate":"🟡","High":"🔴","Unclear":"⚪"}.get(row_d["bias"],"—")
            c1,c2,c3,c4 = st.columns([4,2,2,1])
            c1.write(s.study_id[:45])
            c2.write(f"{s.effect:.2f} ({s.ci_lower:.2f}–{s.ci_upper:.2f})")
            c3.write(f"{bias_icon} {row_d['bias']}")
            inc = c4.checkbox("含める", value=not row_d["excluded"],
                              key=f"sa_{s.study_id}", label_visibility="collapsed")
            st.session_state.sa_exclusions[s.study_id] = not inc

        inc_studies = [r["study"] for r in sa_rows if not st.session_state.sa_exclusions[r["study"].study_id]]
        if st.button("▶ 感度分析を実行", key="run_sa"):
            if len(inc_studies) < 2:
                st.error("2件以上必要です。")
            else:
                try:
                    sa_res, *_ = run_meta_analysis(inc_studies, method=method_code,
                                                   variance_method=var_code, force_fixed=force_fixed)
                    st.session_state.sa_result = sa_res
                    st.session_state.sa_included = inc_studies
                except Exception as e:
                    st.error(str(e))

        if st.session_state.sa_result:
            sa_res = st.session_state.sa_result
            comp = {
                "": ["全研究（メイン）", f"感度分析（n={len(st.session_state.sa_included)}）"],
                "効果量": [f"{result.pooled_effect:.2f}", f"{sa_res.pooled_effect:.2f}"],
                "95%CI": [f"{result.ci_lower:.2f}–{result.ci_upper:.2f}",
                          f"{sa_res.ci_lower:.2f}–{sa_res.ci_upper:.2f}"],
                "I²": [f"{result.i2:.1f}%", f"{sa_res.i2:.1f}%"],
            }
            st.dataframe(pd.DataFrame(comp), use_container_width=True, hide_index=True)
            m_sig = not(result.ci_lower<=1.0<=result.ci_upper)
            s_sig = not(sa_res.ci_lower<=1.0<=sa_res.ci_upper)
            if m_sig == s_sig:
                st.success("✅ 感度分析にロバスト（結論不変）")
            else:
                st.warning("⚠️ Highバイアス除外で統計的有意性が変化しました")


# ════════════════════════════════════════════════════
# TAB 5 : バイアス評価
# ════════════════════════════════════════════════════
with tabs[4]:
    import pymupdf
    from bias_assessment import (
        auto_assess_bias, compute_overall,
        QUIPS_DOMAINS, RATING_OPTIONS, RATING_ICONS,
    )

    st.header("⑤ バイアスリスク評価（QUIPS）")

    df = st.session_state.articles_df
    if df is None:
        st.info("文献がありません。")
        st.stop()

    pdf_df = df[(df["一次スクリーニング"] == "採択") & (df.get("FTステータス","") == "PDF済み")] \
        if "FTステータス" in df.columns else pd.DataFrame()

    if pdf_df.empty:
        st.info("PDFがアップロードされた採択文献がありません。")
        st.stop()

    total_p = len(pdf_df)
    assessed = len(st.session_state.bias_results)
    confirmed = sum(st.session_state.bias_confirmed.values())
    mc1, mc2, mc3 = st.columns(3)
    mc1.metric("対象論文", total_p)
    mc2.metric("AI評価済み", assessed)
    mc3.metric("確認完了", confirmed)

    if st.button("🤖 全PDFをAI自動評価", type="primary"):
        prog = st.progress(0)
        for i, (idx, row) in enumerate(pdf_df.iterrows()):
            pmid = str(row["PMID"])
            pp = row.get("PDFパス","")
            if pp and os.path.exists(pp):
                try:
                    doc = pymupdf.open(pp)
                    text = "".join(p.get_text() for p in doc)
                    doc.close()
                    br = auto_assess_bias(text, pmid=pmid, title=row["タイトル"],
                                         study_design=row.get("研究デザイン",""))
                    st.session_state.bias_results[pmid] = br
                    st.session_state.bias_ratings.setdefault(pmid, {})
                except Exception as e:
                    st.warning(f"PMID {pmid}: {e}")
            prog.progress((i+1)/total_p)
        st.rerun()

    st.divider()

    for idx, row in pdf_df.iterrows():
        pmid = str(row["PMID"])
        pp = row.get("PDFパス","")
        br = st.session_state.bias_results.get(pmid)
        is_conf = st.session_state.bias_confirmed.get(pmid, False)
        conf_icon = "✅" if is_conf else "⏳"

        with st.expander(f"{conf_icon} [{row['年']}] {row['タイトル'][:70]}"):
            left, right = st.columns([1,1])

            with left:
                st.markdown("**📄 PDF ビューア**")
                if pp and os.path.exists(pp):
                    pk = f"page_{pmid}"
                    st.session_state.setdefault(pk, 0)
                    try:
                        doc = pymupdf.open(pp)
                        total_pg = doc.page_count
                        cur_pg = st.session_state[pk]
                        n1, n2, n3 = st.columns([1,2,1])
                        if n1.button("◀", key=f"prev_{pmid}") and cur_pg > 0:
                            st.session_state[pk] -= 1; st.rerun()
                        n2.markdown(f"<div style='text-align:center'>{cur_pg+1}/{total_pg}</div>",
                                    unsafe_allow_html=True)
                        if n3.button("▶", key=f"next_{pmid}") and cur_pg < total_pg-1:
                            st.session_state[pk] += 1; st.rerun()
                        page = doc.load_page(cur_pg)
                        pix = page.get_pixmap(matrix=pymupdf.Matrix(1.5,1.5))
                        st.image(pix.tobytes("png"), use_container_width=True)
                        doc.close()
                    except Exception as e:
                        st.error(f"PDF表示エラー: {e}")
                else:
                    st.info("PDFが見つかりません。")

            with right:
                st.markdown("**📋 QUIPS バイアス評価**")
                if br is None:
                    st.info("「🤖 全PDFをAI自動評価」を実行してください。")
                else:
                    ov_map = st.session_state.bias_ratings.get(pmid, {})
                    for domain in QUIPS_DOMAINS:
                        d_id = domain["id"]
                        dr = next((d for d in br.domains if d.domain_id == d_id), None)
                        if not dr: continue
                        cur_r = ov_map.get(d_id, dr.rating)
                        icon = RATING_ICONS.get(cur_r, "⚪")
                        st.markdown(f"**{icon} {domain['label']}**")
                        st.caption(f"AI: {dr.rating} — {dr.reason}")
                        new_r = st.selectbox("評価", RATING_OPTIONS,
                                             index=RATING_OPTIONS.index(cur_r),
                                             key=f"rating_{pmid}_{d_id}",
                                             label_visibility="collapsed")
                        if new_r != dr.rating:
                            st.session_state.bias_ratings.setdefault(pmid, {})[d_id] = new_r
                            dr.override = new_r
                        st.markdown("---")

                    overall = compute_overall(br)
                    ov_icon = RATING_ICONS.get(overall, "⚪")
                    st.markdown(f"### {ov_icon} 総合評価: **{overall}**")
                    br.overall = overall

                    if is_conf:
                        st.success("✅ 確認完了")
                        if st.button("✏️ 再編集", key=f"reedit_{pmid}"):
                            st.session_state.bias_confirmed[pmid] = False; st.rerun()
                    else:
                        if st.button("✅ 確認完了", key=f"confirm_{pmid}", type="primary"):
                            st.session_state.bias_confirmed[pmid] = True
                            br.confirmed = True; st.rerun()


# ════════════════════════════════════════════════════
# TAB 6 : 出力
# ════════════════════════════════════════════════════
with tabs[5]:
    from word_export import export_bias_table, export_prisma_flow, export_meta_report
    from forest_plot import forest_plot as make_forest_plot

    st.header("⑥ Word 出力")
    pico = st.session_state.get("pico", {})
    df = st.session_state.articles_df

    if df is None:
        st.info("文献データがありません。")
        st.stop()

    sport_lbl = pico.get("sport","unknown")
    injury_lbl = pico.get("injury","unknown")

    st.divider()
    # バイアス評価表
    st.subheader("📋 バイアス評価表（QUIPS）")
    bias_list = list(st.session_state.get("bias_results",{}).values())
    if bias_list:
        try:
            buf_b = export_bias_table(bias_list)
            st.download_button("📥 バイアス評価表 (.docx)", data=buf_b,
                               file_name=f"bias_{sport_lbl}_{injury_lbl}.docx",
                               mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        except Exception as e:
            st.error(str(e))
    else:
        st.info("バイアス評価データがありません。")

    st.divider()
    # PRISMA
    st.subheader("🔄 PRISMA フロー図")
    total_id = len(df)
    n_dup = st.session_state.get("n_removed_dup", 0)
    n_exc_t = int((df["一次スクリーニング"]=="除外").sum())
    ft_all = df[df["一次スクリーニング"]=="採択"]
    n_ft = len(ft_all)
    n_exc_ft = int((ft_all.get("FTステータス", pd.Series(dtype=str))=="入手不可").sum()) \
        if "FTステータス" in ft_all.columns else 0
    n_inc = int((ft_all.get("FTステータス", pd.Series(dtype=str))=="PDF済み").sum()) \
        if "FTステータス" in ft_all.columns else n_ft

    pc1, pc2 = st.columns(2)
    with pc1:
        st.write(f"同定: **{total_id}** 件 / 重複除去: **{n_dup}** 件")
        st.write(f"タイトル・抄録スクリーニング: **{total_id - n_dup}** 件")
        st.write(f"除外（タイトル・抄録）: **{n_exc_t}** 件")
    with pc2:
        st.write(f"フルテキスト評価: **{n_ft}** 件")
        st.write(f"除外（フルテキスト）: **{n_exc_ft}** 件")
        st.write(f"最終採択: **{n_inc}** 件")

    try:
        buf_p = export_prisma_flow(
            n_identified=total_id, n_duplicates=n_dup,
            n_screened=total_id-n_dup, n_excluded_title=n_exc_t,
            n_fulltext=n_ft, n_excluded_fulltext=n_exc_ft,
            n_included=n_inc, pico=pico,
        )
        st.download_button("📥 PRISMA フロー図 (.docx)", data=buf_p,
                           file_name=f"prisma_{sport_lbl}_{injury_lbl}.docx",
                           mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    except Exception as e:
        st.error(str(e))

    st.divider()
    # メタアナリシスレポート
    st.subheader("📊 メタアナリシスレポート")
    meta_res = st.session_state.get("meta_result")
    meta_st = st.session_state.get("meta_studies",[])
    factor_name = st.session_state.get("selected_factor","")
    if meta_res and meta_st:
        ci_sts = [s for s in meta_st if s.ci_lower and s.ci_upper]
        try:
            fp_buf = make_forest_plot(
                study_ids=[s.study_id for s in ci_sts],
                effects=[s.effect for s in ci_sts],
                ci_lowers=[s.ci_lower for s in ci_sts],
                ci_uppers=[s.ci_upper for s in ci_sts],
                weights=[s.weight for s in ci_sts],
                pooled_effect=meta_res.pooled_effect,
                pooled_ci_lower=meta_res.ci_lower,
                pooled_ci_upper=meta_res.ci_upper,
                measure_type=meta_res.measure_type,
                title=f"{factor_name} — {meta_res.measure_type}",
                i2=meta_res.i2, tau2=meta_res.tau2,
            )
            buf_m = export_meta_report(
                meta_result=meta_res, studies=meta_st,
                selected_factor=factor_name, forest_plot_buf=fp_buf, pico=pico,
            )
            st.download_button("📥 メタアナリシスレポート (.docx)", data=buf_m,
                               file_name=f"meta_{sport_lbl}_{injury_lbl}.docx",
                               mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        except Exception as e:
            st.error(str(e))
    else:
        st.info("メタアナリシス結果がありません。")
