"""
スポーツ傷害リスク因子 システマティックレビュー自動化ツール
メインアプリケーション (Streamlit)
"""

import streamlit as st
import pandas as pd
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from pubmed_search import build_search_query, search_pubmed, fetch_articles, remove_duplicates, articles_to_dataframe

# ページ設定
st.set_page_config(
    page_title="SR自動化ツール - スポーツ傷害リスク因子",
    page_icon="🏃",
    layout="wide"
)

# セッション状態の初期化
if "step" not in st.session_state:
    st.session_state.step = 1
if "articles" not in st.session_state:
    st.session_state.articles = []
if "df" not in st.session_state:
    st.session_state.df = None
if "query" not in st.session_state:
    st.session_state.query = ""

# ヘッダー
st.title("🏃 スポーツ傷害リスク因子 SR自動化ツール")
st.caption("PubMed検索 → AIスクリーニング → データ抽出 → メタアナリシス")

# プログレスバー
steps = ["① PICO入力", "② PubMed検索", "③ スクリーニング", "④ データ抽出", "⑤ メタアナリシス", "⑥ バイアス評価", "⑦ 出力"]
current_step = st.session_state.step
cols = st.columns(len(steps))
for i, (col, label) in enumerate(zip(cols, steps)):
    if i + 1 < current_step:
        col.success(label)
    elif i + 1 == current_step:
        col.info(f"**{label}**")
    else:
        col.write(label)

st.divider()

# ===== ステップ1: PICO入力 =====
if st.session_state.step == 1:
    st.header("① PICO形式で検索条件を入力")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("P（対象集団）")
        sport_options = [
            "soccer / football（サッカー）",
            "running（ランニング）",
            "rugby（ラグビー）",
            "basketball（バスケットボール）",
            "american football（アメフト）",
            "athletics（陸上競技）",
            "swimming（水泳）",
            "tennis（テニス）",
            "baseball（野球）",
            "volleyball（バレーボール）",
            "その他（自由入力）",
        ]
        sport_label = st.selectbox("スポーツ種目", sport_options)
        if sport_label == "その他（自由入力）":
            sport = st.text_input("スポーツ種目を入力（英語）", placeholder="例: gymnastics, judo, cycling")
        else:
            sport = sport_label.split("（")[0].split(" / ")[0].strip()
            st.caption(f"検索語: {sport}")

        population = st.text_input("対象集団", value="athlete", help="例: athlete, elite athlete, youth athlete")

    with col2:
        st.subheader("O（アウトカム）")
        injury_options = [
            "hamstring（ハムストリング損傷）",
            "ACL（前十字靭帯損傷）",
            "ankle sprain（足関節捻挫）",
            "groin（鼠径部損傷）",
            "calf（下腿損傷）",
            "knee（膝損傷）",
            "shoulder（肩損傷）",
            "stress fracture（疲労骨折）",
            "concussion（脳震盪）",
            "その他（自由入力）",
        ]
        injury_label = st.selectbox("傷害の種類", injury_options)
        if injury_label == "その他（自由入力）":
            injury = st.text_input("傷害の種類を入力（英語）", placeholder="例: rotator cuff, plantar fasciitis")
        else:
            injury = injury_label.split("（")[0].strip()
            st.caption(f"検索語: {injury}")

    st.subheader("研究デザイン（複数選択可）")
    design_cols = st.columns(4)
    study_designs = []
    design_options = {
        "コホート研究": "cohort study",
        "ケースコントロール研究": "case-control study",
        "前向き研究": "prospective study",
        "後ろ向き研究": "retrospective study",
    }
    for col, (label, value) in zip(design_cols, design_options.items()):
        if col.checkbox(label, value=True):
            study_designs.append(value)

    st.subheader("検索オプション")
    opt_col1, opt_col2 = st.columns(2)
    max_results = opt_col1.number_input("最大取得件数", min_value=10, max_value=500, value=200, step=10)
    email = opt_col2.text_input("メールアドレス（NCBI API用）", value="minekoya0@gmail.com")

    # クエリプレビュー
    if st.button("🔍 クエリをプレビュー"):
        preview_query = build_search_query(sport, injury, population, study_designs if study_designs else None)
        st.code(preview_query, language="text")

    st.divider()
    can_start = len(study_designs) > 0 and bool(sport) and bool(injury)
    if st.button("▶ PubMed検索を開始", type="primary", disabled=not can_start):
        st.session_state.pico = {
            "sport": sport,
            "injury": injury,
            "population": population,
            "study_designs": study_designs,
            "max_results": max_results,
            "email": email,
        }
        st.session_state.step = 2
        st.rerun()

    if len(study_designs) == 0:
        st.warning("研究デザインを1つ以上選択してください。")
    if not sport:
        st.warning("スポーツ種目を入力してください。")
    if not injury:
        st.warning("傷害の種類を入力してください。")


# ===== ステップ2: PubMed検索 =====
elif st.session_state.step == 2:
    st.header("② PubMed検索中...")

    pico = st.session_state.pico
    query = build_search_query(
        pico["sport"], pico["injury"], pico["population"], pico["study_designs"]
    )
    st.session_state.query = query

    st.subheader("生成された検索クエリ")
    st.code(query, language="text")

    with st.spinner("PubMedを検索中..."):
        try:
            pmids = search_pubmed(query, max_results=pico["max_results"], email=pico["email"])
            st.success(f"✅ {len(pmids)}件のPMIDを取得しました")

            if pmids:
                progress_bar = st.progress(0)
                status_text = st.empty()

                articles_list = []
                batch_size = 20
                for i in range(0, len(pmids), batch_size):
                    batch = pmids[i:i + batch_size]
                    batch_articles = fetch_articles(batch, email=pico["email"], batch_size=batch_size)
                    articles_list.extend(batch_articles)
                    progress = min((i + batch_size) / len(pmids), 1.0)
                    progress_bar.progress(progress)
                    status_text.text(f"{min(i + batch_size, len(pmids))}/{len(pmids)}件取得中...")

                status_text.text("重複を除去中...")
                articles_list = remove_duplicates(articles_list)
                st.session_state.articles = articles_list

                df = articles_to_dataframe(articles_list)
                st.session_state.df = df

                st.success(f"✅ 重複除去後: {len(articles_list)}件")
                st.session_state.step = 3
                st.rerun()

        except Exception as e:
            st.error(f"エラーが発生しました: {e}")
            if st.button("← 戻る"):
                st.session_state.step = 1
                st.rerun()


# ===== ステップ3: スクリーニング =====
elif st.session_state.step == 3:
    pico = st.session_state.pico
    df = st.session_state.df

    # サブステップ管理
    if "substep" not in st.session_state:
        st.session_state.substep = "primary"  # primary / fulltext

    substep = st.session_state.substep

    # --- サブヘッダー ---
    if substep == "primary":
        st.header("③-A タイトル・抄録スクリーニング（一次）")
    else:
        st.header("③-B フルテキスト スクリーニング（二次）")

    # --- メトリクス ---
    total = len(df)
    if substep == "primary":
        screened = df["一次スクリーニング"].isin(["採択", "除外"]).sum()
        pending = total - screened
        included = (df["一次スクリーニング"] == "採択").sum()
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("総件数", f"{total}件")
        m2.metric("処理済み", f"{screened}件")
        m3.metric("未処理", f"{pending}件")
        m4.metric("採択", f"{included}件")
    else:
        ft_df = df[df["一次スクリーニング"] == "採択"].copy()
        ft_total = len(ft_df)
        ft_pending = (ft_df["FTステータス"] == "").sum() if "FTステータス" in ft_df.columns else ft_total
        ft_ready = (ft_df["FTステータス"] == "PDF済み").sum() if "FTステータス" in ft_df.columns else 0
        ft_na = (ft_df["FTステータス"] == "入手不可").sum() if "FTステータス" in ft_df.columns else 0
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("FT確認対象", f"{ft_total}件")
        m2.metric("⏳ 保留中", f"{ft_pending}件")
        m3.metric("✅ PDF済み", f"{ft_ready}件")
        m4.metric("❌ 入手不可", f"{ft_na}件")

    st.divider()

    # ===== 一次スクリーニング =====
    if substep == "primary":
        from ai_screening import batch_screen

        st.subheader("🤖 AI自動スクリーニング")

        # AI未実行の場合は自動で実行
        ai_done = "ai_screening_done" in st.session_state and st.session_state.ai_screening_done
        pending = (df["一次スクリーニング"] == "").sum()

        if not ai_done or pending == len(df):
            if st.button("🤖 AIスクリーニングを実行", type="primary"):
                with st.spinner(f"AIが{len(df)}件を解析中..."):
                    results = batch_screen(
                        df,
                        sport=pico["sport"],
                        injury=pico["injury"],
                        population=pico.get("population", "athlete"),
                    )
                    for res in results:
                        pmid = res["pmid"]
                        mask = st.session_state.df["PMID"].astype(str) == pmid
                        idx_list = st.session_state.df[mask].index
                        for idx in idx_list:
                            decision = res["decision"]
                            # 「要確認」は「採択」として保留（研究者が確認）
                            mapped = "採択" if decision in ("採択", "要確認") else "除外"
                            st.session_state.df.at[idx, "一次スクリーニング"] = mapped
                            st.session_state.df.at[idx, "AI判定"] = decision
                            st.session_state.df.at[idx, "AIスコア"] = res["score"]
                            st.session_state.df.at[idx, "AI理由"] = res["reason"]
                            if decision == "除外":
                                st.session_state.df.at[idx, "除外理由"] = res["reason"]
                    st.session_state.ai_screening_done = True
                st.rerun()
        else:
            # AI実行済みサマリー
            df_ref = st.session_state.df
            n_inc = (df_ref["一次スクリーニング"] == "採択").sum()
            n_exc = (df_ref["一次スクリーニング"] == "除外").sum()
            n_check = (df_ref.get("AI判定", pd.Series(dtype=str)) == "要確認").sum() \
                if "AI判定" in df_ref.columns else 0
            st.success(f"✅ AI判定完了 — 採択: {n_inc}件 / 除外: {n_exc}件"
                       + (f" / 要確認（採択に分類）: {n_check}件" if n_check else ""))
            if st.button("🔄 AI判定をリセットして再実行"):
                st.session_state.df["一次スクリーニング"] = ""
                st.session_state.df["除外理由"] = ""
                st.session_state.ai_screening_done = False
                st.rerun()

        st.divider()
        st.subheader("結果の確認・手動修正")
        st.caption("AI判定を確認し、必要に応じて変更してください。採択/除外ボタンで手動上書きできます。")

        filter_col1, filter_col2, filter_col3 = st.columns(3)
        show_status = filter_col1.selectbox("表示フィルター", ["全件", "採択", "除外", "要確認"])
        search_text = filter_col2.text_input("タイトル検索", "")
        sort_by = filter_col3.selectbox("並び順", ["AIスコア（低い順）", "AIスコア（高い順）", "年（新しい順）"])

        display_df = df.copy()
        if show_status == "採択":
            display_df = display_df[display_df["一次スクリーニング"] == "採択"]
        elif show_status == "除外":
            display_df = display_df[display_df["一次スクリーニング"] == "除外"]
        elif show_status == "要確認":
            if "AI判定" in display_df.columns:
                display_df = display_df[display_df["AI判定"] == "要確認"]
        if search_text:
            display_df = display_df[display_df["タイトル"].str.contains(search_text, case=False, na=False)]

        if "AIスコア" in display_df.columns:
            if sort_by == "AIスコア（低い順）":
                display_df = display_df.sort_values("AIスコア", ascending=True)
            elif sort_by == "AIスコア（高い順）":
                display_df = display_df.sort_values("AIスコア", ascending=False)
        if sort_by == "年（新しい順）":
            display_df = display_df.sort_values("年", ascending=False)

        st.write(f"{len(display_df)}件を表示中（最大50件）")

        for idx, row in display_df.head(50).iterrows():
            current = st.session_state.df.at[idx, "一次スクリーニング"]
            ai_dec = st.session_state.df.at[idx, "AI判定"] if "AI判定" in st.session_state.df.columns else ""
            ai_score = st.session_state.df.at[idx, "AIスコア"] if "AIスコア" in st.session_state.df.columns else ""
            ai_reason = st.session_state.df.at[idx, "AI理由"] if "AI理由" in st.session_state.df.columns else ""

            status_icon = {"採択": "✅", "除外": "❌"}.get(current, "⬜")
            warn_icon = " ⚠️" if ai_dec == "要確認" else ""
            score_str = f" [スコア: {ai_score}]" if ai_score != "" else ""

            with st.expander(f"{status_icon}{warn_icon} [{row['年']}] {row['タイトル'][:75]}{score_str}"):
                st.write(f"**著者:** {row['著者']}")
                st.write(f"**雑誌:** {row['雑誌']} | **PMID:** {row['PMID']}")
                if row['DOI']:
                    st.write(f"**DOI:** https://doi.org/{row['DOI']}")
                if ai_reason:
                    st.caption(f"🤖 AI判定理由: {ai_reason}")
                st.write(f"**抄録:** {row['抄録']}")

                reason = st.text_input("除外理由", value=row["除外理由"], key=f"reason_{idx}")
                dc1, dc2, dc3 = st.columns(3)
                if dc1.button("✅ 採択", key=f"inc_{idx}"):
                    st.session_state.df.at[idx, "一次スクリーニング"] = "採択"
                    st.session_state.df.at[idx, "除外理由"] = ""
                    st.rerun()
                if dc2.button("❌ 除外", key=f"exc_{idx}"):
                    st.session_state.df.at[idx, "一次スクリーニング"] = "除外"
                    st.session_state.df.at[idx, "除外理由"] = reason
                    st.rerun()
                if dc3.button("↩️ AI判定に戻す", key=f"rst_{idx}"):
                    original = ai_dec if ai_dec in ("採択", "除外") else "採択"
                    st.session_state.df.at[idx, "一次スクリーニング"] = original
                    st.session_state.df.at[idx, "除外理由"] = ai_reason if original == "除外" else ""
                    st.rerun()

        st.divider()
        # エクスポート
        csv_data = df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
        st.download_button("📥 CSVをダウンロード", data=csv_data,
                           file_name=f"primary_screening_{pico['sport']}_{pico['injury']}.csv",
                           mime="text/csv")

        # 一次スクリーニング完了チェック
        pending_count = (df["一次スクリーニング"] == "").sum()
        included_count = (df["一次スクリーニング"] == "採択").sum()

        st.divider()
        if pending_count > 0:
            st.warning(f"⏳ 未処理が{pending_count}件あります。全件処理するとFTスクリーニングへ進めます。")
        elif included_count == 0:
            st.warning("採択文献が0件です。")
        else:
            st.success(f"✅ 一次スクリーニング完了。採択{included_count}件 → FTスクリーニングへ進めます。")

            # FTステータス列を初期化
            if "FTステータス" not in st.session_state.df.columns:
                st.session_state.df["FTステータス"] = ""
                st.session_state.df["PDFパス"] = ""

            if st.button(f"▶ フルテキスト スクリーニングへ（{included_count}件）", type="primary"):
                st.session_state.substep = "fulltext"
                st.rerun()

    # ===== フルテキストスクリーニング =====
    else:
        # FTステータス列の確保
        if "FTステータス" not in st.session_state.df.columns:
            st.session_state.df["FTステータス"] = ""
            st.session_state.df["PDFパス"] = ""

        ft_df = st.session_state.df[st.session_state.df["一次スクリーニング"] == "採択"].copy()

        ft_filter = st.selectbox("表示フィルター", ["保留中のみ", "全件", "PDF済み", "入手不可"])

        display_ft = ft_df.copy()
        if ft_filter == "保留中のみ":
            display_ft = display_ft[display_ft["FTステータス"] == ""]
        elif ft_filter in ["PDF済み", "入手不可"]:
            display_ft = display_ft[display_ft["FTステータス"] == ft_filter]

        st.write(f"{len(display_ft)}件を表示中")

        for idx, row in display_ft.iterrows():
            ft_status = st.session_state.df.at[idx, "FTステータス"]
            status_icon = {"PDF済み": "✅", "入手不可": "❌"}.get(ft_status, "⏳")
            with st.expander(f"{status_icon} [{row['年']}] {row['タイトル'][:80]}"):
                st.write(f"**著者:** {row['著者']}")
                st.write(f"**雑誌:** {row['雑誌']} | **PMID:** {row['PMID']}")
                if row['DOI']:
                    st.write(f"**DOI:** https://doi.org/{row['DOI']}")

                uploaded_pdf = st.file_uploader(
                    "PDFをアップロード",
                    type=["pdf"],
                    key=f"pdf_{idx}"
                )

                if uploaded_pdf is not None:
                    save_dir = f"/home/user/sr_tool/pdfs"
                    os.makedirs(save_dir, exist_ok=True)
                    save_path = f"{save_dir}/{row['PMID']}.pdf"
                    with open(save_path, "wb") as f:
                        f.write(uploaded_pdf.read())
                    st.session_state.df.at[idx, "FTステータス"] = "PDF済み"
                    st.session_state.df.at[idx, "PDFパス"] = save_path
                    st.success(f"✅ アップロード完了: {uploaded_pdf.name}")
                    st.rerun()

                bc1, bc2 = st.columns(2)
                if ft_status == "PDF済み":
                    bc1.success("✅ PDF済み")
                    if bc2.button("↩️ リセット", key=f"ft_rst_{idx}"):
                        st.session_state.df.at[idx, "FTステータス"] = ""
                        st.session_state.df.at[idx, "PDFパス"] = ""
                        st.rerun()
                elif ft_status == "入手不可":
                    bc1.error("❌ 入手不可（除外）")
                    if bc2.button("↩️ リセット", key=f"ft_rst2_{idx}"):
                        st.session_state.df.at[idx, "FTステータス"] = ""
                        st.rerun()
                else:
                    if bc2.button("❌ 入手不可", key=f"ft_na_{idx}"):
                        st.session_state.df.at[idx, "FTステータス"] = "入手不可"
                        st.session_state.df.at[idx, "除外理由"] = "Full text not available"
                        st.rerun()

        st.divider()

        # ゲートチェック：全件処理済みか
        ft_pending = (st.session_state.df[st.session_state.df["一次スクリーニング"] == "採択"]["FTステータス"] == "").sum()
        ft_ready = (st.session_state.df[st.session_state.df["一次スクリーニング"] == "採択"]["FTステータス"] == "PDF済み").sum()

        nav_col1, nav_col2 = st.columns(2)
        if nav_col1.button("← 一次スクリーニングに戻る"):
            st.session_state.substep = "primary"
            st.rerun()

        if ft_pending > 0:
            st.warning(f"⏳ 保留中が{ft_pending}件あります。全件「PDF済み」または「入手不可」にするとデータ抽出へ進めます。")
        elif ft_ready == 0:
            st.warning("PDF済みの文献が0件です。")
        else:
            st.success(f"✅ FTスクリーニング完了。PDF済み{ft_ready}件 → データ抽出へ進めます。")
            if nav_col2.button(f"▶ データ抽出・メタアナリシスへ（{ft_ready}件）", type="primary"):
                st.session_state.step = 4
                st.rerun()


# ===== ステップ4: データ抽出 =====
elif st.session_state.step == 4:
    from data_extraction import extract_from_pdf, extracted_data_to_rows

    st.header("④ データ抽出")

    df = st.session_state.df
    pdf_df = df[(df["一次スクリーニング"] == "採択") & (df.get("FTステータス", "") == "PDF済み")] \
        if "FTステータス" in df.columns else df[df["一次スクリーニング"] == "採択"]

    total_pdfs = len(pdf_df)

    # 抽出結果の保存先
    if "extraction_results" not in st.session_state:
        st.session_state.extraction_results = {}  # pmid -> list of outcome dicts

    extracted_count = len(st.session_state.extraction_results)
    pending_count = total_pdfs - extracted_count

    m1, m2, m3 = st.columns(3)
    m1.metric("PDF対象", f"{total_pdfs}件")
    m2.metric("抽出済み", f"{extracted_count}件")
    m3.metric("未抽出", f"{pending_count}件")

    st.divider()

    # 一括自動抽出ボタン
    if st.button("🤖 全PDFから自動抽出", type="primary"):
        progress = st.progress(0)
        status = st.empty()
        for i, (idx, row) in enumerate(pdf_df.iterrows()):
            pmid = str(row["PMID"])
            pdf_path = row.get("PDFパス", "")
            if pmid in st.session_state.extraction_results:
                continue
            if pdf_path and os.path.exists(pdf_path):
                status.text(f"抽出中: {row['タイトル'][:50]}...")
                try:
                    data = extract_from_pdf(pdf_path, pmid=pmid, title=row["タイトル"])
                    st.session_state.extraction_results[pmid] = data.outcomes
                except Exception as e:
                    st.session_state.extraction_results[pmid] = []
                    st.warning(f"PMID {pmid}: 抽出エラー ({e})")
            progress.progress((i + 1) / total_pdfs)
        status.text("✅ 自動抽出完了")
        st.rerun()

    st.divider()
    st.subheader("抽出結果の確認・編集")
    st.info("💡 自動抽出された効果量を確認し、危険因子の名称を入力してください。不要な行は削除できます。")

    all_rows = []

    for idx, row in pdf_df.iterrows():
        pmid = str(row["PMID"])
        outcomes = st.session_state.extraction_results.get(pmid, None)

        with st.expander(f"📄 [{row['年']}] {row['タイトル'][:70]}"):
            if outcomes is None:
                st.warning("未抽出です。上の「全PDFから自動抽出」を実行してください。")
                continue
            elif len(outcomes) == 0:
                st.warning("効果量が自動抽出できませんでした。手動で追加してください。")

            # 手動追加フォーム
            with st.form(key=f"add_{pmid}"):
                fc1, fc2, fc3, fc4, fc5 = st.columns([3, 1, 1, 1, 1])
                new_factor = fc1.text_input("危険因子", key=f"nf_{pmid}", placeholder="例: previous hamstring injury")
                new_measure = fc2.selectbox("種類", ["RR", "OR", "HR", "IRR", "SMD"], key=f"nm_{pmid}")
                new_value = fc3.number_input("効果量", min_value=0.0, value=1.0, step=0.01, key=f"nv_{pmid}")
                new_cil = fc4.number_input("CI下限", min_value=0.0, value=0.0, step=0.01, key=f"ncil_{pmid}")
                new_ciu = fc5.number_input("CI上限", min_value=0.0, value=0.0, step=0.01, key=f"nciu_{pmid}")
                if st.form_submit_button("＋ 追加"):
                    st.session_state.extraction_results[pmid].append({
                        "risk_factor": new_factor,
                        "measure_type": new_measure,
                        "value": new_value,
                        "ci_lower": new_cil if new_cil > 0 else None,
                        "ci_upper": new_ciu if new_ciu > 0 else None,
                        "has_ci": new_cil > 0 and new_ciu > 0,
                        "context": "手動入力",
                        "confirmed": True,
                    })
                    st.rerun()

            # 抽出結果一覧
            for oi, outcome in enumerate(outcomes):
                ci_str = f"{outcome['ci_lower']}–{outcome['ci_upper']}" if outcome.get("has_ci") else "CI不明"
                col1, col2, col3, col4 = st.columns([3, 2, 2, 1])

                new_factor_val = col1.text_input(
                    "危険因子", value=outcome.get("risk_factor", ""),
                    key=f"factor_{pmid}_{oi}",
                    placeholder="危険因子名を入力"
                )
                col2.write(f"**{outcome['measure_type']}** = {outcome['value']}")
                col3.write(f"95%CI: {ci_str}")

                if col4.button("🗑️", key=f"del_{pmid}_{oi}"):
                    st.session_state.extraction_results[pmid].pop(oi)
                    st.rerun()

                # 危険因子名を保存
                st.session_state.extraction_results[pmid][oi]["risk_factor"] = new_factor_val

                if outcome.get("context"):
                    with st.expander("文脈を表示", expanded=False):
                        st.caption(f"...{outcome['context'][:200]}...")

                # 集計用に追加
                all_rows.append({
                    "PMID": pmid,
                    "タイトル": row["タイトル"][:50],
                    "年": row["年"],
                    "危険因子": new_factor_val,
                    "効果量の種類": outcome["measure_type"],
                    "効果量": outcome["value"],
                    "CI下限": outcome.get("ci_lower", ""),
                    "CI上限": outcome.get("ci_upper", ""),
                })

    st.divider()

    # エクスポート
    if all_rows:
        import pandas as pd
        result_df = pd.DataFrame(all_rows)
        st.subheader("抽出データ一覧")
        st.dataframe(result_df, use_container_width=True)

        csv_data = result_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
        pico = st.session_state.pico
        st.download_button(
            "📥 抽出データをCSVでダウンロード",
            data=csv_data,
            file_name=f"extracted_data_{pico['sport']}_{pico['injury']}.csv",
            mime="text/csv"
        )

    nav1, nav2 = st.columns(2)
    if nav1.button("← スクリーニングに戻る"):
        st.session_state.step = 3
        st.rerun()
    if all_rows and nav2.button("▶ メタアナリシスへ", type="primary"):
        st.session_state.extraction_df = pd.DataFrame(all_rows)
        st.session_state.step = 5
        st.rerun()


# ===== ステップ5: メタアナリシス =====
elif st.session_state.step == 5:
    from meta_analysis import (
        StudyData, run_meta_analysis, interpret_heterogeneity,
        heterogeneity_recommendation, auto_select_method
    )

    st.header("⑤ メタアナリシス")

    extraction_df = st.session_state.get("extraction_df", pd.DataFrame())
    if extraction_df.empty:
        st.warning("抽出データがありません。データ抽出ステップに戻ってください。")
        if st.button("← 戻る"):
            st.session_state.step = 4
            st.rerun()
        st.stop()

    # CI付きデータのみ使用可能
    has_ci = extraction_df["CI下限"].apply(lambda x: x != "" and x is not None and float(x) > 0 if x else False)
    ci_df = extraction_df[has_ci].copy()
    no_ci_df = extraction_df[~has_ci].copy()

    st.subheader("危険因子グループの選択")
    st.info("同じ危険因子を報告した研究をグループ化してメタアナリシスを実行します。")

    if ci_df.empty:
        st.warning("95%CI付きのデータがありません。メタアナリシスを実行できません。")
        st.stop()

    # 危険因子でグループ化
    risk_factors = ci_df["危険因子"].unique().tolist()
    risk_factors = [r for r in risk_factors if r.strip()]

    if not risk_factors:
        st.warning("危険因子名が入力されていません。データ抽出画面で危険因子名を入力してください。")
        st.stop()

    selected_factor = st.selectbox("分析する危険因子を選択", risk_factors)
    factor_df = ci_df[ci_df["危険因子"] == selected_factor].copy()

    st.write(f"**{selected_factor}** を報告した研究: {len(factor_df)}件")

    if len(factor_df) < 2:
        st.warning("メタアナリシスには2件以上の研究が必要です。")
        st.stop()

    # StudyDataに変換
    studies = []
    for _, row in factor_df.iterrows():
        try:
            studies.append(StudyData(
                study_id=f"{row['タイトル'][:30]} ({row['年']})",
                effect=float(row["効果量"]),
                ci_lower=float(row["CI下限"]),
                ci_upper=float(row["CI上限"]),
                measure_type=str(row["効果量の種類"])
            ))
        except (ValueError, TypeError):
            continue

    if len(studies) < 2:
        st.warning("有効なデータが2件未満です。")
        st.stop()

    # 自動選択
    auto_method, auto_var, auto_reason = auto_select_method(studies)

    st.divider()
    st.subheader("統計手法")

    # 自動選択の表示
    st.info(f"""
**自動選択結果**
- 統合手法: **{auto_method}**（{"Mantel-Haenszel" if auto_method=="MH" else "Inverse Variance" if auto_method=="IV" else "Peto"}）
- 分散推定: **{auto_var}**
- 選択理由: {auto_reason}
""")

    # 手動変更オプション
    with st.expander("🔧 手法を手動で変更する"):
        mc1, mc2, mc3 = st.columns(3)
        manual_method = mc1.selectbox(
            "統合手法",
            ["MH（Mantel-Haenszel）", "IV（Inverse Variance）", "Peto"],
            index=["MH", "IV", "Peto"].index(auto_method)
        )
        manual_var = mc2.selectbox(
            "分散推定法",
            ["REML（推奨）", "DL（DerSimonian-Laird）", "fixed（固定効果）"],
            index=["REML", "DL", "fixed"].index(auto_var)
        )
        force_fixed = mc3.checkbox("固定効果モデルを強制")

        method_code = manual_method.split("（")[0].strip()
        var_code = manual_var.split("（")[0].strip()
        use_manual = True

    if "use_manual" not in dir():
        method_code = auto_method
        var_code = auto_var
        force_fixed = False

    # メタアナリシス実行
    st.divider()
    if st.button("▶ メタアナリシスを実行", type="primary"):
        try:
            result, method, var_method, reason = run_meta_analysis(
                studies,
                method=method_code,
                variance_method=var_code,
                force_fixed=force_fixed
            )
            st.session_state.meta_result = result
            st.session_state.meta_studies = studies
        except Exception as e:
            st.error(f"エラー: {e}")

    # 結果表示
    if "meta_result" in st.session_state:
        result = st.session_state.meta_result
        studies_with_weights = st.session_state.meta_studies

        st.divider()
        st.subheader("📊 結果")

        r1, r2, r3, r4 = st.columns(4)
        r1.metric("プール効果量", f"{result.pooled_effect:.2f}")
        r2.metric("95%CI", f"{result.ci_lower:.2f}–{result.ci_upper:.2f}")
        label, icon = interpret_heterogeneity(result.i2)
        r3.metric("I²（異質性）", f"{result.i2}%", delta=f"{icon} {label}")
        r4.metric("研究数", f"{result.n_studies}件")

        st.write(f"**Q統計量** = {result.q_stat}  (p = {result.q_pvalue})")
        st.write(f"**τ²** = {result.tau2}")

        st.info(heterogeneity_recommendation(result.i2))

        # 個別研究の重み表示
        st.divider()
        st.subheader("個別研究の寄与")
        weight_rows = []
        for s in studies_with_weights:
            weight_rows.append({
                "研究": s.study_id,
                "効果量": s.effect,
                "CI下限": s.ci_lower,
                "CI上限": s.ci_upper,
                "重み(%)": f"{s.weight:.1f}%"
            })
        st.dataframe(pd.DataFrame(weight_rows), use_container_width=True)

        # I²が高い場合の警告
        if result.i2 >= 75:
            st.warning("⚠️ 異質性が非常に高いため、感度分析またはサブグループ解析を実施することを推奨します。")

        # ===== フォレストプロット =====
        st.divider()
        st.subheader("🌲 フォレストプロット")

        from forest_plot import forest_plot as make_forest_plot

        ci_studies = [s for s in studies_with_weights
                      if s.ci_lower is not None and s.ci_upper is not None]

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
            title=f"{selected_factor}  —  {result.measure_type}, {result.method} + {result.variance_method}",
            i2=result.i2,
            tau2=result.tau2,
        )

        st.image(buf, use_container_width=True)

        # PNGダウンロード
        buf.seek(0)
        pico = st.session_state.pico
        st.download_button(
            "📥 フォレストプロットをダウンロード（PNG）",
            data=buf,
            file_name=f"forest_plot_{pico['sport']}_{selected_factor.replace(' ', '_')}.png",
            mime="image/png"
        )

        # ===== 感度分析：バイアスリスクによる除外 =====
        st.divider()
        st.subheader("🔍 感度分析：バイアスリスクによる除外")
        st.caption("バイアスリスクが「High」と評価された論文を除外して再計算します。各論文を手動で含める/除外するかを切り替えられます。")

        bias_results_map = st.session_state.get("bias_results", {})

        # 各研究のバイアス評価を取得し、除外状態を管理
        if "sa_exclusions" not in st.session_state:
            st.session_state.sa_exclusions = {}  # study_id -> bool (True=除外)

        # 研究ごとにバイアス評価を対応付ける（PMIDとstudy_idを紐付け）
        df_for_sa = st.session_state.get("extraction_df", pd.DataFrame())

        # 各研究の行 + バイアス評価情報を表示
        sa_table_rows = []
        for s in studies_with_weights:
            # study_idからPMIDを逆引き（タイトルの先頭30文字で照合）
            matched_pmid = None
            matched_overall = "未評価"
            for _, ex_row in df_for_sa.iterrows():
                if str(ex_row.get("タイトル", ""))[:30] in s.study_id:
                    matched_pmid = str(ex_row["PMID"])
                    break
            if matched_pmid and matched_pmid in bias_results_map:
                matched_overall = bias_results_map[matched_pmid].overall

            # デフォルト: Highなら除外
            if s.study_id not in st.session_state.sa_exclusions:
                st.session_state.sa_exclusions[s.study_id] = (matched_overall == "High")

            sa_table_rows.append({
                "study": s,
                "bias_overall": matched_overall,
                "excluded": st.session_state.sa_exclusions[s.study_id],
            })

        # 除外管理テーブル
        sa_cols = st.columns([4, 2, 2, 1])
        sa_cols[0].markdown("**研究**")
        sa_cols[1].markdown("**効果量 (95%CI)**")
        sa_cols[2].markdown("**バイアス**")
        sa_cols[3].markdown("**含める**")

        for row_data in sa_table_rows:
            s = row_data["study"]
            bias_ov = row_data["bias_overall"]
            excluded = row_data["excluded"]
            bias_icon = {"Low": "🟢", "Moderate": "🟡", "High": "🔴", "Unclear": "⚪"}.get(bias_ov, "—")

            c1, c2, c3, c4 = st.columns([4, 2, 2, 1])
            label_style = "~~" if excluded else ""  # 除外中は打ち消し線
            c1.markdown(f"{label_style}{s.study_id[:45]}{label_style}")
            c2.markdown(f"{s.effect:.2f} ({s.ci_lower:.2f}–{s.ci_upper:.2f})")
            c3.markdown(f"{bias_icon} {bias_ov}")
            include = c4.checkbox(
                "含める", value=not excluded,
                key=f"sa_include_{s.study_id}",
                label_visibility="collapsed"
            )
            st.session_state.sa_exclusions[s.study_id] = not include

        # 感度分析実行
        included_studies = [
            row_data["study"]
            for row_data in sa_table_rows
            if not st.session_state.sa_exclusions[row_data["study"].study_id]
        ]
        excluded_count = len(sa_table_rows) - len(included_studies)

        st.markdown(f"**含める研究: {len(included_studies)}件 / 除外: {excluded_count}件**")

        if st.button("▶ 感度分析を実行", key="run_sa"):
            if len(included_studies) < 2:
                st.error("感度分析には2件以上の研究が必要です。")
            else:
                try:
                    sa_result, sa_method, sa_var, _ = run_meta_analysis(
                        included_studies,
                        method=method_code,
                        variance_method=var_code,
                        force_fixed=force_fixed,
                    )
                    st.session_state.sa_result = sa_result
                    st.session_state.sa_included = included_studies
                except Exception as e:
                    st.error(f"感度分析エラー: {e}")

        # 感度分析結果の表示
        if "sa_result" in st.session_state and "sa_included" in st.session_state:
            sa_res = st.session_state.sa_result
            sa_inc = st.session_state.sa_included

            st.markdown("#### 感度分析結果")

            # メインvs感度分析の比較表
            comp_data = {
                "": ["全研究（メイン）", f"Low/Moderate/Unclearのみ（n={len(sa_inc)}）"],
                "効果量": [f"{result.pooled_effect:.2f}", f"{sa_res.pooled_effect:.2f}"],
                "95%CI": [
                    f"{result.ci_lower:.2f}–{result.ci_upper:.2f}",
                    f"{sa_res.ci_lower:.2f}–{sa_res.ci_upper:.2f}",
                ],
                "I²": [f"{result.i2:.1f}%", f"{sa_res.i2:.1f}%"],
                "研究数": [f"{result.n_studies}", f"{sa_res.n_studies}"],
            }
            st.dataframe(pd.DataFrame(comp_data), use_container_width=True, hide_index=True)

            # 結論の変化チェック
            main_sig = not (result.ci_lower <= 1.0 <= result.ci_upper) if result.measure_type in ["RR","OR","HR"] else not (result.ci_lower <= 0.0 <= result.ci_upper)
            sa_sig = not (sa_res.ci_lower <= 1.0 <= sa_res.ci_upper) if sa_res.measure_type in ["RR","OR","HR"] else not (sa_res.ci_lower <= 0.0 <= sa_res.ci_upper)

            if main_sig == sa_sig:
                st.success("✅ Highバイアス論文を除外しても結論は変わりません（感度分析にロバスト）。")
            else:
                st.warning("⚠️ Highバイアス論文の除外により統計的有意性が変化しました。結果の解釈に注意が必要です。")

            # 感度分析フォレストプロット
            sa_ci_studies = [s for s in sa_inc if s.ci_lower is not None and s.ci_upper is not None]
            if len(sa_ci_studies) >= 2:
                sa_buf = make_forest_plot(
                    study_ids=[s.study_id for s in sa_ci_studies],
                    effects=[s.effect for s in sa_ci_studies],
                    ci_lowers=[s.ci_lower for s in sa_ci_studies],
                    ci_uppers=[s.ci_upper for s in sa_ci_studies],
                    weights=[s.weight for s in sa_ci_studies],
                    pooled_effect=sa_res.pooled_effect,
                    pooled_ci_lower=sa_res.ci_lower,
                    pooled_ci_upper=sa_res.ci_upper,
                    measure_type=sa_res.measure_type,
                    title=f"[感度分析] {selected_factor}  —  High バイアス除外",
                    i2=sa_res.i2,
                    tau2=sa_res.tau2,
                )
                st.image(sa_buf, use_container_width=True)

                sa_buf.seek(0)
                st.download_button(
                    "📥 感度分析フォレストプロットをダウンロード（PNG）",
                    data=sa_buf,
                    file_name=f"forest_plot_sensitivity_{pico['sport']}_{selected_factor.replace(' ', '_')}.png",
                    mime="image/png",
                    key="dl_sa_fp"
                )

    st.divider()
    col_back, col_next = st.columns(2)
    if col_back.button("← データ抽出に戻る"):
        st.session_state.step = 4
        st.rerun()
    if "meta_result" in st.session_state:
        if col_next.button("▶ バイアス評価へ", type="primary"):
            st.session_state.step = 6
            st.rerun()


# ===== ステップ6: バイアスリスク評価 =====
elif st.session_state.step == 6:
    from bias_assessment import (
        auto_assess_bias, compute_overall, get_confirmed_rating,
        QUIPS_DOMAINS, RATING_OPTIONS, RATING_COLORS, RATING_ICONS
    )
    import pymupdf

    st.header("⑥ バイアスリスク評価（QUIPS）")
    st.info("💡 AIがPDFテキストから各ドメインを自動判定します。PDFを確認しながら評価を確定してください。")

    df = st.session_state.df
    pdf_df = df[(df["一次スクリーニング"] == "採択") & (df["FTステータス"] == "PDF済み")] \
        if "FTステータス" in df.columns else pd.DataFrame()

    if pdf_df.empty:
        st.warning("PDFがアップロードされた論文がありません。")
        st.stop()

    # バイアス評価結果のセッション管理
    if "bias_results" not in st.session_state:
        st.session_state.bias_results = {}   # pmid -> BiasResult
    if "bias_ratings" not in st.session_state:
        st.session_state.bias_ratings = {}   # pmid -> {domain_id -> rating (override)}
    if "bias_confirmed" not in st.session_state:
        st.session_state.bias_confirmed = {} # pmid -> bool

    # 一括AI評価ボタン
    total_pdfs = len(pdf_df)
    assessed = len(st.session_state.bias_results)
    confirmed = sum(st.session_state.bias_confirmed.values())

    m1, m2, m3 = st.columns(3)
    m1.metric("対象論文", f"{total_pdfs}件")
    m2.metric("AI評価済み", f"{assessed}件")
    m3.metric("確認完了", f"{confirmed}件")

    if st.button("🤖 全PDFをAI自動評価", type="primary"):
        prog = st.progress(0)
        for i, (idx, row) in enumerate(pdf_df.iterrows()):
            pmid = str(row["PMID"])
            pdf_path = row.get("PDFパス", "")
            if pdf_path and os.path.exists(pdf_path):
                try:
                    doc_pdf = pymupdf.open(pdf_path)
                    text = "".join(page.get_text() for page in doc_pdf)
                    doc_pdf.close()
                    bias_res = auto_assess_bias(
                        text, pmid=pmid, title=row["タイトル"],
                        study_design=row.get("研究デザイン", "")
                    )
                    st.session_state.bias_results[pmid] = bias_res
                    if pmid not in st.session_state.bias_ratings:
                        st.session_state.bias_ratings[pmid] = {}
                except Exception as e:
                    st.warning(f"PMID {pmid}: 評価エラー ({e})")
            prog.progress((i + 1) / total_pdfs)
        st.rerun()

    st.divider()

    # 論文ごとの評価UI
    for idx, row in pdf_df.iterrows():
        pmid = str(row["PMID"])
        pdf_path = row.get("PDFパス", "")
        bias_res = st.session_state.bias_results.get(pmid)
        is_confirmed = st.session_state.bias_confirmed.get(pmid, False)

        confirm_icon = "✅" if is_confirmed else "⏳"
        with st.expander(f"{confirm_icon} [{row['年']}] {row['タイトル'][:70]}"):

            # レイアウト: 左=PDF, 右=チェックリスト
            left_col, right_col = st.columns([1, 1])

            # ===== 左パネル: PDFビューア =====
            with left_col:
                st.markdown("**📄 PDFビューア**")
                if pdf_path and os.path.exists(pdf_path):
                    page_key = f"pdf_page_{pmid}"
                    if page_key not in st.session_state:
                        st.session_state[page_key] = 0

                    try:
                        doc_pdf = pymupdf.open(pdf_path)
                        total_pages = doc_pdf.page_count
                        cur_page = st.session_state[page_key]

                        # ページナビゲーション
                        nav1, nav2, nav3 = st.columns([1, 2, 1])
                        if nav1.button("◀", key=f"prev_{pmid}") and cur_page > 0:
                            st.session_state[page_key] -= 1
                            st.rerun()
                        nav2.markdown(f"<div style='text-align:center'>{cur_page+1} / {total_pages}</div>",
                                      unsafe_allow_html=True)
                        if nav3.button("▶", key=f"next_{pmid}") and cur_page < total_pages - 1:
                            st.session_state[page_key] += 1
                            st.rerun()

                        # ページをPNG画像として描画
                        page = doc_pdf.load_page(cur_page)
                        mat = pymupdf.Matrix(1.5, 1.5)  # 解像度 x1.5
                        pix = page.get_pixmap(matrix=mat)
                        img_bytes = pix.tobytes("png")
                        doc_pdf.close()
                        st.image(img_bytes, use_container_width=True)

                    except Exception as e:
                        st.error(f"PDF表示エラー: {e}")
                else:
                    st.warning("PDFが見つかりません。")

            # ===== 右パネル: QUIPSチェックリスト =====
            with right_col:
                st.markdown("**📋 QUIPS バイアス評価**")

                if bias_res is None:
                    st.warning("AI評価未実行。「全PDFをAI自動評価」を実行してください。")
                else:
                    ratings_override = st.session_state.bias_ratings.get(pmid, {})

                    for domain in QUIPS_DOMAINS:
                        d_id = domain["id"]
                        domain_result = next((d for d in bias_res.domains if d.domain_id == d_id), None)
                        if not domain_result:
                            continue

                        ai_rating = domain_result.rating
                        current_rating = ratings_override.get(d_id, ai_rating)
                        icon = RATING_ICONS.get(current_rating, "⚪")

                        st.markdown(f"**{icon} {domain['label']}**")
                        st.caption(f"AI判定: {ai_rating} — {domain_result.reason}")

                        new_rating = st.selectbox(
                            "評価",
                            RATING_OPTIONS,
                            index=RATING_OPTIONS.index(current_rating),
                            key=f"rating_{pmid}_{d_id}",
                            label_visibility="collapsed"
                        )
                        if new_rating != ai_rating:
                            if pmid not in st.session_state.bias_ratings:
                                st.session_state.bias_ratings[pmid] = {}
                            st.session_state.bias_ratings[pmid][d_id] = new_rating
                            domain_result.override = new_rating
                        st.markdown("---")

                    # 総合評価（自動算出）
                    from bias_assessment import compute_overall
                    overall = compute_overall(bias_res)
                    overall_icon = RATING_ICONS.get(overall, "⚪")
                    st.markdown(f"### {overall_icon} 総合評価: **{overall}**")
                    bias_res.overall = overall

                    # 確認ボタン
                    if is_confirmed:
                        st.success("✅ 確認完了")
                        if st.button("✏️ 再編集", key=f"reedit_{pmid}"):
                            st.session_state.bias_confirmed[pmid] = False
                            st.rerun()
                    else:
                        if st.button("✅ 確認完了", key=f"confirm_{pmid}", type="primary"):
                            st.session_state.bias_confirmed[pmid] = True
                            bias_res.confirmed = True
                            st.rerun()

    st.divider()

    # 全件確認チェック
    pdf_pmids = [str(row["PMID"]) for _, row in pdf_df.iterrows()]
    all_confirmed = all(st.session_state.bias_confirmed.get(p, False) for p in pdf_pmids)
    confirmed_count = sum(st.session_state.bias_confirmed.get(p, False) for p in pdf_pmids)

    col_back2, col_next2 = st.columns(2)
    if col_back2.button("← メタアナリシスに戻る"):
        st.session_state.step = 5
        st.rerun()

    if not all_confirmed:
        st.warning(f"⏳ {total_pdfs - confirmed_count}件の確認が残っています。全件確認すると出力へ進めます。")
    else:
        st.success(f"✅ 全{confirmed_count}件のバイアス評価が確定しました。")
        if col_next2.button("▶ 出力へ", type="primary"):
            st.session_state.step = 7
            st.rerun()


# ===== ステップ7: Word出力 =====
elif st.session_state.step == 7:
    from word_export import export_bias_table, export_prisma_flow, export_meta_report

    st.header("⑦ Word出力")
    st.info("各ボタンをクリックしてWordファイルをダウンロードできます。")

    df = st.session_state.df
    pico = st.session_state.pico

    st.divider()

    # ===== ボタン1: バイアス評価表 =====
    st.subheader("📋 バイアス評価表（QUIPS）")
    bias_results_list = list(st.session_state.get("bias_results", {}).values())

    if bias_results_list:
        try:
            bias_buf = export_bias_table(bias_results_list)
            st.download_button(
                "📥 バイアス評価表をダウンロード（.docx）",
                data=bias_buf,
                file_name=f"bias_assessment_{pico['sport']}_{pico['injury']}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                key="dl_bias"
            )
        except Exception as e:
            st.error(f"出力エラー: {e}")
    else:
        st.warning("バイアス評価データがありません。バイアス評価ステップを完了してください。")

    st.divider()

    # ===== ボタン2: PRISMAフロー図 =====
    st.subheader("🔄 PRISMAフロー図（PRISMA 2020）")

    total_identified = len(df)
    n_dup = 0  # 重複除去数（検索時に処理済み）
    n_screened = total_identified
    n_excluded_title = (df["一次スクリーニング"] == "除外").sum()
    ft_df_all = df[df["一次スクリーニング"] == "採択"]
    n_fulltext = len(ft_df_all)
    n_excluded_ft = (ft_df_all.get("FTステータス", pd.Series(dtype=str)) == "入手不可").sum() \
        if "FTステータス" in ft_df_all.columns else 0
    n_included = (ft_df_all.get("FTステータス", pd.Series(dtype=str)) == "PDF済み").sum() \
        if "FTステータス" in ft_df_all.columns else n_fulltext

    # PRISMA数値プレビュー
    prisma_col1, prisma_col2 = st.columns(2)
    with prisma_col1:
        st.write(f"- 同定: **{total_identified}**件")
        st.write(f"- 重複除去: **{n_dup}**件")
        st.write(f"- タイトル・抄録スクリーニング: **{n_screened}**件")
        st.write(f"- タイトル・抄録除外: **{n_excluded_title}**件")
    with prisma_col2:
        st.write(f"- フルテキスト評価: **{n_fulltext}**件")
        st.write(f"- フルテキスト除外: **{n_excluded_ft}**件")
        st.write(f"- 最終採択: **{n_included}**件")

    try:
        prisma_buf = export_prisma_flow(
            n_identified=total_identified,
            n_duplicates=n_dup,
            n_screened=n_screened,
            n_excluded_title=int(n_excluded_title),
            n_fulltext=n_fulltext,
            n_excluded_fulltext=int(n_excluded_ft),
            n_included=int(n_included),
            pico=pico,
        )
        st.download_button(
            "📥 PRISMAフロー図をダウンロード（.docx）",
            data=prisma_buf,
            file_name=f"prisma_flow_{pico['sport']}_{pico['injury']}.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            key="dl_prisma"
        )
    except Exception as e:
        st.error(f"出力エラー: {e}")

    st.divider()

    # ===== ボタン3: メタアナリシスレポート =====
    st.subheader("📊 メタアナリシスレポート")

    meta_result = st.session_state.get("meta_result")
    meta_studies = st.session_state.get("meta_studies", [])
    selected_factor = st.session_state.get("extraction_df", pd.DataFrame())

    if meta_result and meta_studies:
        # フォレストプロットを再生成
        from forest_plot import forest_plot as make_forest_plot
        ci_studies = [s for s in meta_studies if s.ci_lower is not None and s.ci_upper is not None]

        factor_name = ""
        if not st.session_state.get("extraction_df", pd.DataFrame()).empty:
            risk_factors = st.session_state.extraction_df["危険因子"].unique().tolist()
            risk_factors = [r for r in risk_factors if r.strip()]
            if risk_factors:
                factor_name = risk_factors[0]

        try:
            fp_buf = make_forest_plot(
                study_ids=[s.study_id for s in ci_studies],
                effects=[s.effect for s in ci_studies],
                ci_lowers=[s.ci_lower for s in ci_studies],
                ci_uppers=[s.ci_upper for s in ci_studies],
                weights=[s.weight for s in ci_studies],
                pooled_effect=meta_result.pooled_effect,
                pooled_ci_lower=meta_result.ci_lower,
                pooled_ci_upper=meta_result.ci_upper,
                measure_type=meta_result.measure_type,
                title=f"{factor_name}  —  {meta_result.measure_type}",
                i2=meta_result.i2,
                tau2=meta_result.tau2,
            )

            meta_buf = export_meta_report(
                meta_result=meta_result,
                studies=meta_studies,
                selected_factor=factor_name,
                forest_plot_buf=fp_buf,
                pico=pico,
            )
            st.download_button(
                "📥 メタアナリシスレポートをダウンロード（.docx）",
                data=meta_buf,
                file_name=f"meta_report_{pico['sport']}_{pico['injury']}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                key="dl_meta"
            )
        except Exception as e:
            st.error(f"出力エラー: {e}")
    else:
        st.warning("メタアナリシス結果がありません。メタアナリシスステップを完了してください。")

    st.divider()
    if st.button("← バイアス評価に戻る"):
        st.session_state.step = 6
        st.rerun()
