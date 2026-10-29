"""
スポーツ傷害リスク因子 システマティックレビュー自動化ツール
メインアプリケーション (Streamlit)
"""

import streamlit as st
import pandas as pd
import os
import sys
import tempfile
import json
import base64
import requests as _requests
import pickle
import pathlib

sys.path.insert(0, os.path.dirname(__file__))


# ─── セッション永続化（サーバー側ファイル） ───
# Streamlit Cloud でも /tmp は書き込み可能。セッションIDをクエリパラメータで持ち回す。
_SESSION_DIR = pathlib.Path(tempfile.gettempdir()) / "sr_sessions"
_SESSION_DIR.mkdir(exist_ok=True)

def _session_path(sid: str) -> pathlib.Path:
    return _SESSION_DIR / f"{sid}.pkl"

def _save_session(sid: str, state: dict):
    """session_state の保存可能なキーをpickleで保存"""
    saveable = {}
    skip = {"_is_running", "FormSubmitter"}
    for k, v in state.items():
        if k.startswith("_") or k in skip:
            continue
        try:
            pickle.dumps(v)   # シリアライズ可能かテスト
            saveable[k] = v
        except Exception:
            pass
    try:
        with open(_session_path(sid), "wb") as f:
            pickle.dump(saveable, f)
    except Exception:
        pass

def _load_session(sid: str) -> dict:
    p = _session_path(sid)
    if not p.exists():
        return {}
    try:
        with open(p, "rb") as f:
            return pickle.load(f)
    except Exception:
        return {}

def _get_or_create_sid() -> str:
    """URLクエリパラメータ sid= からセッションIDを取得、なければ新規作成"""
    import uuid
    params = st.query_params
    sid = params.get("sid", "")
    if not sid:
        sid = uuid.uuid4().hex
        st.query_params["sid"] = sid
    return sid


# ─── Unpaywall で無料PDF URLを取得 ───
def _get_free_pdf_url(doi: str, email: str = "minekoya0@gmail.com") -> str | None:
    if not doi:
        return None
    try:
        r = _requests.get(f"https://api.unpaywall.org/v2/{doi}",
                          params={"email": email}, timeout=8)
        if r.status_code != 200:
            return None
        data = r.json()
        best = data.get("best_oa_location") or {}
        return best.get("url_for_pdf") or best.get("url")
    except Exception:
        return None

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

# ─── セッション状態の初期化（ファイルから復元） ───
_defaults = {
    "step": 1,
    "articles_df": None,
    "pico": {},
    "query": "",
    "pubmed_done": False,
    "pubmed_articles": [],
    "import_articles": [],
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
    "_sid": "",
    "_loaded": False,
}

# セッションID取得
_sid = _get_or_create_sid()

# 初回ロード時だけファイルから復元
if not st.session_state.get("_loaded"):
    saved = _load_session(_sid)
    for k, v in _defaults.items():
        if k not in st.session_state:
            st.session_state[k] = saved.get(k, v)
    st.session_state["_sid"] = _sid
    st.session_state["_loaded"] = True
else:
    # 既にロード済み → デフォルトだけ補完
    for k, v in _defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

def _autosave():
    """重要な操作のたびに呼ぶ"""
    _save_session(st.session_state["_sid"], dict(st.session_state))


def _auto_reflect_extraction_to_meta():
    """
    extraction_results の確定済み（or 全件）エントリを meta_rows へ自動反映。
    - _direct=True: OR/RR/HR の直接値を持つ行
    - 2x2 データ: ee/ne/ec/nc を持つ行
    抽出済みで meta_rows にまだない論文のみ追加（重複しない）。
    """
    existing_ids = {r.get("study_id", "") for r in st.session_state.get("meta_rows", [])}

    added = 0
    for pmid, outcomes in st.session_state.get("extraction_results", {}).items():
        if not outcomes:
            continue

        # 論文タイトル/著者/年を取得
        study_label = f"PMID{pmid}"
        df_ref = st.session_state.get("search_results")
        if df_ref is not None and not df_ref.empty:
            hit = df_ref[df_ref["PMID"].astype(str) == pmid]
            if not hit.empty:
                yr   = str(hit.iloc[0].get("年", ""))
                auth = str(hit.iloc[0].get("著者", ""))
                first_auth = auth.split(",")[0].split(" ")[-1] if auth else ""
                study_label = f"{first_auth} {yr}".strip() or study_label

        if study_label in existing_ids:
            continue

        # outcomes の中から最初の有効行を代表として採用
        for o in outcomes:
            eff  = o.get("_effect")
            cil  = o.get("_ci_lower")
            ciu  = o.get("_ci_upper")
            ee   = o.get("ee") or o.get("events_exp")
            ne   = o.get("ne") or o.get("total_exp")
            ec   = o.get("ec") or o.get("events_ctrl")
            nc   = o.get("nc") or o.get("total_ctrl")

            if eff and cil and ciu:
                # 直接効果量
                new_row = {
                    "study_id": study_label,
                    "_direct": True,
                    "_effect": float(eff),
                    "_ci_lower": float(cil),
                    "_ci_upper": float(ciu),
                }
                st.session_state.meta_rows.append(new_row)
                existing_ids.add(study_label)
                added += 1
                break
            elif ee and ne and ec and nc:
                # 2x2 テーブルデータ
                new_row = {
                    "study_id": study_label,
                    "_direct": False,
                    "ee": str(ee),
                    "ne": str(ne),
                    "ec": str(ec),
                    "nc": str(nc),
                }
                st.session_state.meta_rows.append(new_row)
                existing_ids.add(study_label)
                added += 1
                break

    if added:
        st.toast(f"📊 {added}件の抽出データをメタアナリシスに反映しました", icon="✅")

# ─── タイトル ───
st.markdown("## 🏃 スポーツ傷害リスク因子 SR自動化ツール")
st.caption("PubMed検索 ＋ 手動インポート → スクリーニング → データ抽出 → メタアナリシス → 出力")

# ─── タブナビゲーション ───
TAB_LABELS = [
    "① PICO & 検索",
    "② スクリーニング",
    "③ バイアス評価",
    "④ データ抽出",
    "⑤ メタアナリシス",
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
            population = st.text_input(
                "集団の特性",
                value="",
                placeholder="例: professional, youth, recreational, elite",
                help="対象集団の特性を英語で入力（例: professional athletes, youth players）"
            )

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
                    _autosave()
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
            _autosave()
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
        _autosave()
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
                            st.session_state.articles_df.at[idx, "AIスコア"] = str(res["score"])
                            st.session_state.articles_df.at[idx, "AI理由"] = res["reason"]
                            if dec == "除外":
                                st.session_state.articles_df.at[idx, "除外理由"] = res["reason"]
                    st.session_state.ai_screening_done = True
                _autosave()
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
                    _autosave()
                    st.rerun()
                if bc2.button("❌ 除外", key=f"pr_exc_{idx}"):
                    st.session_state.articles_df.at[idx, "一次スクリーニング"] = "除外"
                    st.session_state.articles_df.at[idx, "除外理由"] = reason
                    _autosave()
                    st.rerun()
                if bc3.button("↩️ AI判定に戻す", key=f"pr_rst_{idx}"):
                    orig = ai_d if ai_d in ("採択","除外") else "採択"
                    st.session_state.articles_df.at[idx, "一次スクリーニング"] = orig
                    st.session_state.articles_df.at[idx, "除外理由"] = ai_r if orig=="除外" else ""
                    _autosave()
                    st.rerun()

        # CSV エクスポート
        csv = st.session_state.articles_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
        st.download_button("📥 CSVをダウンロード", data=csv,
                           file_name=f"primary_screening.csv", mime="text/csv")

    # ── 二次スクリーニング ──
    with sub2:
        from ai_screening import batch_ft_screen

        st.header("二次スクリーニング（フルテキスト）")
        df_ft_base = st.session_state.articles_df
        ft_df = df_ft_base[df_ft_base["一次スクリーニング"] == "採択"].copy()

        if ft_df.empty:
            st.info("一次スクリーニングで採択された文献がありません。")
            st.stop()

        # 列初期化
        for col, default in [("FTステータス",""), ("PDFパス",""),
                              ("FT判定",""), ("FT判定理由",""), ("FTスコア",""),
                              ("AI判定",""), ("AIスコア",""), ("AI理由","")]:
            if col not in st.session_state.articles_df.columns:
                st.session_state.articles_df[col] = default

        ft_total  = len(ft_df)
        ft_ready  = (st.session_state.articles_df.loc[ft_df.index, "FTステータス"] == "PDF済み").sum()
        ft_na     = (st.session_state.articles_df.loc[ft_df.index, "FTステータス"] == "入手不可").sum()
        ft_pend   = ft_total - ft_ready - ft_na
        ft_inc    = (st.session_state.articles_df.loc[ft_df.index, "FT判定"] == "採択").sum()
        ft_exc    = (st.session_state.articles_df.loc[ft_df.index, "FT判定"] == "除外").sum()
        ft_judged = (st.session_state.articles_df.loc[ft_df.index, "FT判定"] != "").sum()

        mc1, mc2, mc3, mc4, mc5 = st.columns(5)
        mc1.metric("FT対象", ft_total)
        mc2.metric("📄 本文取得済み", ft_ready)
        mc3.metric("❌ 入手不可", ft_na)
        mc4.metric("✅ FT採択", ft_inc)
        mc5.metric("🚫 FT除外", ft_exc)

        st.divider()

        # ── STEP 1: 本文PDF取得 ──
        st.subheader("STEP 1：本文PDFの取得")
        st.caption("オープンアクセス論文は自動取得。それ以外は手動でアップロード、または「入手不可」にしてください。")

        email_ft = st.session_state.get("pico", {}).get("email", "minekoya0@gmail.com")

        col_auto, col_info = st.columns([2, 3])
        with col_auto:
            if st.button("🌐 オープンアクセスPDFを一括自動取得", type="primary"):
                prog = st.progress(0)
                status_ph = st.empty()
                n_ok = 0
                targets_idx = [
                    i for i in ft_df.index
                    if st.session_state.articles_df.at[i, "FTステータス"] == ""
                ]
                total_t = len(targets_idx)
                for i, idx2 in enumerate(targets_idx):
                    row2 = st.session_state.articles_df.loc[idx2]
                    doi2 = str(row2.get("DOI",""))
                    status_ph.info(f"🔍 [{i+1}/{total_t}] {str(row2['タイトル'])[:50]}... を検索中")
                    pdf_url = _get_free_pdf_url(doi2, email_ft)
                    if pdf_url:
                        try:
                            status_ph.info(f"⬇️ ダウンロード中: {str(row2['タイトル'])[:50]}...")
                            resp = _requests.get(pdf_url, timeout=30,
                                                 headers={"User-Agent":"Mozilla/5.0"})
                            if resp.status_code == 200 and b"%PDF" in resp.content[:10]:
                                tmp_dir = tempfile.mkdtemp()
                                sp = os.path.join(tmp_dir, f"{row2['PMID']}.pdf")
                                with open(sp, "wb") as f:
                                    f.write(resp.content)
                                st.session_state.articles_df.at[idx2, "FTステータス"] = "PDF済み"
                                st.session_state.articles_df.at[idx2, "PDFパス"] = sp
                                n_ok += 1
                        except Exception:
                            pass
                    prog.progress((i+1)/max(total_t,1))
                status_ph.empty()
                prog.empty()
                _autosave()
                st.success(f"✅ 自動取得: {n_ok}/{total_t}件 取得（残りは手動アップロード or 入手不可）")
                st.rerun()
        with col_info:
            st.info(f"📄 本文取得済み: {ft_ready}件 ／ ❌ 入手不可: {ft_na}件 ／ ⏳ 未処理: {ft_pend}件")

        st.divider()

        # ── STEP 2: AI二次スクリーニング ──
        st.subheader("STEP 2：AI二次スクリーニング（フルテキスト判定）")
        st.caption("PDFがある場合はその内容で、ない場合は抄録で自動判定します。")

        pico = st.session_state.get("pico", {})

        if ft_judged == 0 or ft_judged < ft_total:
            btn_label = "🤖 AI二次スクリーニングを実行" if ft_judged == 0 else f"🔄 未判定 {ft_total - ft_judged} 件を追加判定"
            if st.button(btn_label, type="primary"):
                # 未判定のもののみ対象
                pending_ft = ft_df[
                    st.session_state.articles_df.loc[ft_df.index, "FT判定"] == ""
                ]
                with st.spinner(f"AIが {len(pending_ft)} 件を判定中..."):
                    results = batch_ft_screen(
                        pending_ft,
                        sport=pico.get("sport",""),
                        injury=pico.get("injury",""),
                        population=pico.get("population",""),
                        extraction_results=st.session_state.get("extraction_results", {}),
                    )
                    for res in results:
                        pmid = res["pmid"]
                        mask = st.session_state.articles_df["PMID"].astype(str) == pmid
                        for midx in st.session_state.articles_df[mask].index:
                            dec = res["decision"]
                            mapped = "採択" if dec in ("採択", "要確認") else "除外"
                            st.session_state.articles_df.at[midx, "FT判定"] = mapped
                            st.session_state.articles_df.at[midx, "FT判定理由"] = res["reason"]
                            st.session_state.articles_df.at[midx, "FTスコア"] = str(res["score"])
                            # 入手不可文献は自動的に除外
                            if st.session_state.articles_df.at[midx, "FTステータス"] == "入手不可":
                                st.session_state.articles_df.at[midx, "FT判定"] = "除外"
                                st.session_state.articles_df.at[midx, "除外理由"] = "Full text not available"
                _autosave()
                st.rerun()
        else:
            c1, c2 = st.columns([3, 1])
            c1.success(f"✅ AI二次判定完了 — 採択:{ft_inc}件 / 除外:{ft_exc}件")
            if c2.button("🔄 全件再実行"):
                st.session_state.articles_df.loc[ft_df.index, "FT判定"] = ""
                st.session_state.articles_df.loc[ft_df.index, "FT判定理由"] = ""
                _autosave()
                st.rerun()

        st.divider()

        # ── 結果一覧 ──
        st.subheader("判定結果の確認・手動修正")
        ft_filter = st.selectbox("表示フィルター",
            ["全件", "FT採択", "FT除外", "要確認", "本文PDF取得済み", "入手不可・未取得"])
        disp_ft = ft_df.copy()
        cur_ft_status  = st.session_state.articles_df.loc[ft_df.index, "FTステータス"]
        cur_ft_decision = st.session_state.articles_df.loc[ft_df.index, "FT判定"]
        if ft_filter == "FT採択":
            disp_ft = disp_ft[cur_ft_decision == "採択"]
        elif ft_filter == "FT除外":
            disp_ft = disp_ft[cur_ft_decision == "除外"]
        elif ft_filter == "要確認":
            disp_ft = disp_ft[cur_ft_decision == "要確認"]
        elif ft_filter == "本文PDF取得済み":
            disp_ft = disp_ft[cur_ft_status == "PDF済み"]
        elif ft_filter == "入手不可・未取得":
            disp_ft = disp_ft[cur_ft_status != "PDF済み"]

        st.write(f"{len(disp_ft)}件を表示中")

        for idx, row in disp_ft.iterrows():
            ft_status   = st.session_state.articles_df.at[idx, "FTステータス"]
            ft_dec      = st.session_state.articles_df.at[idx, "FT判定"]
            ft_reason   = st.session_state.articles_df.at[idx, "FT判定理由"]
            ft_score    = st.session_state.articles_df.at[idx, "FTスコア"]

            dec_icon = {"採択":"✅","除外":"🚫","要確認":"⚠️"}.get(ft_dec, "⬜")
            pdf_icon = "📄" if ft_status == "PDF済み" else ("🔒" if ft_status == "入手不可" else "📭")
            score_str = f" [{ft_score}]" if ft_score != "" else ""

            with st.expander(f"{dec_icon}{pdf_icon} [{row['年']}] {row['タイトル'][:75]}{score_str}"):
                st.write(f"**著者:** {row['著者']}  |  **PMID:** {row['PMID']}")
                if row.get("DOI"):
                    doi_url = f"https://doi.org/{row['DOI']}"
                    st.markdown(f"**DOI:** [{doi_url}]({doi_url})")

                # AI判定結果表示
                if ft_dec:
                    color = {"採択":"success","除外":"error","要確認":"warning"}.get(ft_dec,"info")
                    getattr(st, color)(f"🤖 AI判定: **{ft_dec}**  |  理由: {ft_reason}")

                # 手動上書きボタン
                bc1, bc2, bc3 = st.columns(3)
                if bc1.button("✅ 採択（確定）", key=f"ft_inc_{idx}"):
                    st.session_state.articles_df.at[idx, "FT判定"] = "採択"
                    st.session_state.articles_df.at[idx, "除外理由"] = ""
                    _autosave(); st.rerun()
                if bc2.button("🚫 除外", key=f"ft_exc_{idx}"):
                    st.session_state.articles_df.at[idx, "FT判定"] = "除外"
                    _autosave(); st.rerun()
                if bc3.button("❌ 入手不可", key=f"ft_na_{idx}"):
                    st.session_state.articles_df.at[idx, "FTステータス"] = "入手不可"
                    st.session_state.articles_df.at[idx, "FT判定"] = "除外"
                    st.session_state.articles_df.at[idx, "除外理由"] = "Full text not available"
                    _autosave(); st.rerun()

                # PDF アップロード（未取得の場合）
                if ft_status != "PDF済み":
                    with st.expander("📎 PDFを手動でアップロード"):
                        uploaded_pdf = st.file_uploader(
                            "PDFをアップロード", type=["pdf"], key=f"pdf_up_{idx}"
                        )
                        if uploaded_pdf is not None:
                            with st.spinner(f"⏳ {uploaded_pdf.name} をアップロード中..."):
                                tmp_dir = tempfile.mkdtemp()
                                save_path = os.path.join(tmp_dir, f"{row['PMID']}.pdf")
                                with open(save_path, "wb") as f:
                                    f.write(uploaded_pdf.read())
                                st.session_state.articles_df.at[idx, "FTステータス"] = "PDF済み"
                                st.session_state.articles_df.at[idx, "PDFパス"] = save_path
                                st.session_state.articles_df.at[idx, "FT判定"] = ""  # 再判定促す
                            _autosave()
                            st.success(f"✅ アップロード完了。AI判定を再実行してください。")
                            st.rerun()

                # PDF インラインビューア（取得済みの場合）
                if ft_status == "PDF済み":
                    st.success("✅ 本文PDF取得済み")
                    pdf_path = st.session_state.articles_df.at[idx, "PDFパス"]
                    if pdf_path and os.path.exists(str(pdf_path)):
                        with open(pdf_path, "rb") as f:
                            pdf_bytes = f.read()
                        st.download_button("⬇️ PDFをダウンロード", data=pdf_bytes,
                            file_name=f"{row['PMID']}.pdf", mime="application/pdf",
                            key=f"dl_{idx}")
                        b64 = base64.b64encode(pdf_bytes).decode()
                        st.markdown(
                            f'<iframe src="data:application/pdf;base64,{b64}'
                            f'#toolbar=1&navpanes=0" width="100%" height="680px" '
                            f'style="border:1px solid #ccc;border-radius:6px;" '
                            f'type="application/pdf"></iframe>',
                            unsafe_allow_html=True)
                    else:
                        if st.button("↩️ PDFをリセット", key=f"ft_rst_{idx}"):
                            st.session_state.articles_df.at[idx, "FTステータス"] = ""
                            st.session_state.articles_df.at[idx, "PDFパス"] = ""
                            _autosave(); st.rerun()
                        st.warning("⚠️ PDFが見つかりません（サーバー再起動でファイルが消えた可能性）")


# ════════════════════════════════════════════════════
# TAB 3 : バイアス評価  ← 旧TAB5をここへ移動
# ════════════════════════════════════════════════════
with tabs[2]:
    import pymupdf
    from bias_assessment import (
        auto_assess_bias, compute_overall,
        QUIPS_DOMAINS, RATING_OPTIONS, RATING_ICONS,
    )

    st.header("③ バイアスリスク評価（QUIPS）")
    st.caption("採択文献のPDFをAIが自動評価。確認・上書き後に次のデータ抽出へ進めます。")

    df = st.session_state.articles_df
    if df is None:
        st.info("文献がありません。")
    else:
        # 採択かつPDF取得済みの文献
        if "FTステータス" in df.columns:
            pdf_df_bias = df[(df["一次スクリーニング"] == "採択") & (df["FTステータス"] == "PDF済み")]
        else:
            pdf_df_bias = df[df["一次スクリーニング"] == "採択"]

        total_p = len(pdf_df_bias)
        assessed = len(st.session_state.bias_results)
        confirmed = sum(st.session_state.bias_confirmed.values())

        # ── JBI SUMARI スタイル統計バー ──
        mc1, mc2, mc3 = st.columns(3)
        mc1.metric("対象論文", total_p)
        mc2.metric("AI評価済み", assessed)
        mc3.metric("確認完了", confirmed)

        # Export ボタン（右寄せ）
        _bc1, _bc2 = st.columns([6, 1])
        # （DOCX export は出力タブで）

        st.markdown("---")

        if total_p == 0:
            st.info("PDFがアップロードされた採択文献がありません。スクリーニングタブでPDFを取得してください。")
        else:
            # 🤖 AI一括評価ボタン
            if st.button("🤖 AI一括評価（全論文）", type="primary"):
                prog_b = st.progress(0)
                stat_b = st.empty()
                for i, (idx, row) in enumerate(pdf_df_bias.iterrows()):
                    pmid = str(row["PMID"])
                    pp = row.get("PDFパス", "")
                    stat_b.text(f"評価中 ({i+1}/{total_p}): {row['タイトル'][:50]}...")
                    if pp and os.path.exists(pp):
                        try:
                            doc = pymupdf.open(pp)
                            text = "".join(p.get_text() for p in doc)
                            doc.close()
                            br = auto_assess_bias(
                                text, pmid=pmid, title=row["タイトル"],
                                study_design=row.get("研究デザイン", ""),
                            )
                            st.session_state.bias_results[pmid] = br
                            st.session_state.bias_ratings.setdefault(pmid, {})
                        except Exception as e:
                            st.warning(f"PMID {pmid}: {e}")
                    else:
                        st.warning(f"PMID {pmid}: PDFが見つかりません")
                    prog_b.progress((i+1)/total_p)
                stat_b.text("✅ AI評価完了")
                _autosave()
                st.rerun()

            st.divider()

            # ── 論文一覧（JBI SUMARI Appraisal スタイル） ──
            for idx, row in pdf_df_bias.iterrows():
                pmid = str(row["PMID"])
                pp = row.get("PDFパス", "")
                br = st.session_state.bias_results.get(pmid)
                is_conf = st.session_state.bias_confirmed.get(pmid, False)
                conf_icon = "✅" if is_conf else "⏳"

                # 総合評価バッジ
                if br:
                    ov = br.overall
                    ov_icon = RATING_ICONS.get(ov, "⚪")
                    ov_label = f"{ov_icon} {ov}"
                else:
                    ov_label = "未評価"

                with st.expander(
                    f"{conf_icon}  **{row['タイトル'][:65]}**　　{row['年']}　　{ov_label}"
                ):
                    # 著者
                    if row.get("著者"):
                        st.caption(str(row.get("著者",""))[:80])

                    left_b, right_b = st.columns([1, 1])

                    with left_b:
                        st.markdown("**📄 PDF ビューア**")
                        if pp and os.path.exists(pp):
                            pk = f"page_{pmid}"
                            st.session_state.setdefault(pk, 0)
                            try:
                                doc_v = pymupdf.open(pp)
                                total_pg = doc_v.page_count
                                cur_pg = st.session_state[pk]
                                n1, n2, n3 = st.columns([1, 2, 1])
                                if n1.button("◀", key=f"prev_b_{pmid}") and cur_pg > 0:
                                    st.session_state[pk] -= 1; st.rerun()
                                n2.markdown(
                                    f"<div style='text-align:center'>{cur_pg+1}/{total_pg}</div>",
                                    unsafe_allow_html=True,
                                )
                                if n3.button("▶", key=f"next_b_{pmid}") and cur_pg < total_pg - 1:
                                    st.session_state[pk] += 1; st.rerun()
                                page_v = doc_v.load_page(cur_pg)
                                pix = page_v.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5))
                                st.image(pix.tobytes("png"), use_container_width=True)
                                doc_v.close()
                            except Exception as e:
                                st.error(f"PDF表示エラー: {e}")
                        else:
                            st.info("PDFが見つかりません。スクリーニングタブでPDFを取得してください。")

                    with right_b:
                        st.markdown("**📋 QUIPS バイアス評価**")
                        if br is None:
                            st.info("「🤖 AI一括評価」ボタンを押してください。")
                        else:
                            ov_map = st.session_state.bias_ratings.get(pmid, {})
                            for domain in QUIPS_DOMAINS:
                                d_id = domain["id"]
                                dr = next((d for d in br.domains if d.domain_id == d_id), None)
                                if not dr:
                                    continue
                                cur_r = ov_map.get(d_id, dr.rating)
                                icon = RATING_ICONS.get(cur_r, "⚪")
                                st.markdown(f"**{icon} {domain['label']}**")
                                st.caption(f"AI判定: {dr.rating} — {dr.reason}")
                                new_r = st.selectbox(
                                    "評価", RATING_OPTIONS,
                                    index=RATING_OPTIONS.index(cur_r) if cur_r in RATING_OPTIONS else 0,
                                    key=f"rating_{pmid}_{d_id}",
                                    label_visibility="collapsed",
                                )
                                if new_r != cur_r:
                                    st.session_state.bias_ratings.setdefault(pmid, {})[d_id] = new_r
                                    dr.override = new_r
                                st.markdown("---")

                            overall = compute_overall(br)
                            ov_icon2 = RATING_ICONS.get(overall, "⚪")
                            st.markdown(f"### {ov_icon2} 総合評価: **{overall}**")
                            br.overall = overall

                            if is_conf:
                                st.success("✅ 確認完了")
                                if st.button("✏️ 再編集", key=f"reedit_b_{pmid}"):
                                    st.session_state.bias_confirmed[pmid] = False
                                    st.rerun()
                            else:
                                if st.button("✅ この論文の評価を確定", key=f"confirm_b_{pmid}", type="primary"):
                                    st.session_state.bias_confirmed[pmid] = True
                                    br.confirmed = True
                                    _autosave()
                                    st.rerun()

        # 次のステップへ
        if confirmed > 0:
            st.divider()
            if st.button("▶ データ抽出へ →", type="primary"):
                st.session_state.step = 4
                st.rerun()


# ════════════════════════════════════════════════════
# TAB 4 : データ抽出  （JBI SUMARI Extraction スタイル）
# ════════════════════════════════════════════════════
with tabs[3]:
    from data_extraction import extract_from_pdf

    # ヘッダー行 + Export ボタン
    _hc1, _hc2 = st.columns([5, 1])
    _hc1.header("④ データ抽出")
    # Export CSV
    _export_ready = bool(st.session_state.extraction_results)

    df = st.session_state.articles_df
    if df is None or df.empty:
        st.info("文献がありません。まずスクリーニングを完了してください。")
    else:
        if "FTステータス" in df.columns:
            pdf_df_ex = df[(df["一次スクリーニング"] == "採択") & (df["FTステータス"] == "PDF済み")]
        else:
            pdf_df_ex = df[df["一次スクリーニング"] == "採択"]

        total_pdfs = len(pdf_df_ex)
        extracted_count = len(st.session_state.extraction_results)
        confirmed_ex = sum(
            1 for pmid, outs in st.session_state.extraction_results.items()
            if outs and any(o.get("confirmed") for o in outs)
        )

        # 統計メトリクス
        em1, em2, em3 = st.columns(3)
        em1.metric("対象論文", total_pdfs)
        em2.metric("AI抽出済み", extracted_count)
        em3.metric("確認完了", confirmed_ex)

        # フィルタ
        ex_filter = st.text_input("", placeholder="Filter extractions...", label_visibility="collapsed")

        if total_pdfs == 0:
            st.info("PDFがアップロードされた採択文献がありません。スクリーニングタブでPDFを取得してください。")
        else:
            # 🤖 AI一括抽出ボタン
            if st.button("🤖 AI一括自動抽出（全論文）", type="primary"):
                prog_ex = st.progress(0)
                stat_ex = st.empty()
                _results_log = []   # (pmid, title, status, detail)
                _cnt_ok = _cnt_skip = _cnt_err = 0

                for i, (idx, row) in enumerate(pdf_df_ex.iterrows()):
                    pmid = str(row["PMID"])
                    title_short = str(row["タイトル"])[:45]
                    pdf_path = row.get("PDFパス", "")

                    # すでに抽出済みはスキップ
                    if pmid in st.session_state.extraction_results:
                        prog_ex.progress((i+1)/total_pdfs)
                        stat_ex.text(f"⏭️ スキップ ({i+1}/{total_pdfs}): {title_short}…（抽出済み）")
                        _results_log.append((pmid, title_short, "skip", "抽出済み"))
                        _cnt_skip += 1
                        continue

                    if pdf_path and os.path.exists(pdf_path):
                        stat_ex.text(f"🔍 抽出中 ({i+1}/{total_pdfs}): {title_short}…")
                        try:
                            data = extract_from_pdf(pdf_path, pmid=pmid, title=row["タイトル"])
                            st.session_state.extraction_results[pmid] = data.outcomes
                            n_out = len(data.outcomes) if data.outcomes else 0
                            _results_log.append((pmid, title_short, "ok", f"{n_out}件抽出"))
                            _cnt_ok += 1
                        except Exception as e:
                            st.session_state.extraction_results[pmid] = []
                            _results_log.append((pmid, title_short, "err", str(e)[:80]))
                            _cnt_err += 1
                    else:
                        _results_log.append((pmid, title_short, "err", "PDFが見つかりません"))
                        _cnt_err += 1

                    prog_ex.progress((i+1)/total_pdfs)

                # ── 完了サマリー
                stat_ex.empty()
                prog_ex.progress(1.0)
                st.success(
                    f"✅ AI抽出完了 — 成功: {_cnt_ok}件 ／ スキップ: {_cnt_skip}件 ／ 失敗: {_cnt_err}件"
                )

                # 詳細ログ（expander）
                with st.expander("📋 抽出ログ詳細", expanded=(_cnt_err > 0)):
                    for pmid_l, ttl_l, st_l, det_l in _results_log:
                        icon = {"ok":"✅","skip":"⏭️","err":"❌"}[st_l]
                        st.markdown(f"{icon} **PMID {pmid_l}** — {ttl_l}  \n　→ {det_l}")

                # ── meta_rows へ自動反映
                _auto_reflect_extraction_to_meta()
                _autosave()
                st.rerun()

            st.markdown("---")

            # ── 論文リスト（JBI SUMARI Extraction スタイル） ──
            all_rows = []
            for idx, row in pdf_df_ex.iterrows():
                pmid = str(row["PMID"])
                title = row["タイトル"]

                # フィルタ適用
                if ex_filter and ex_filter.lower() not in title.lower():
                    continue

                outcomes = st.session_state.extraction_results.get(pmid)

                # ステータス判定
                if outcomes is None:
                    status_lbl = "Start"
                    status_color = "#2563EB"  # blue
                elif outcomes and any(o.get("confirmed") for o in outcomes):
                    status_lbl = "Completed"
                    status_color = "#16A34A"  # green
                else:
                    status_lbl = "In Progress"
                    status_color = "#2563EB"

                # カード形式 — トグル用フラグをボタンとは別キーで管理
                _open_key = f"_ex_panel_{pmid}"
                st.session_state.setdefault(_open_key, False)

                with st.container(border=True):
                    row_c1, row_c2 = st.columns([5, 1])
                    with row_c1:
                        st.markdown(f"**{title}**")
                        st.caption(f"{row.get('年','')}　　{str(row.get('著者',''))[:60]}")
                    with row_c2:
                        def _toggle_ex(k=_open_key):
                            st.session_state[k] = not st.session_state[k]
                        st.button(status_lbl, key=f"ex_btn_{pmid}",
                                  type="secondary", on_click=_toggle_ex)

                # 詳細パネル（展開時）
                if st.session_state.get(_open_key, False):
                    with st.container(border=True):
                        st.markdown(f"**{title}**")
                        st.caption(f"{row.get('年','')} — {str(row.get('著者',''))[:80]}")

                        # ── Groups（サンプル記述）
                        st.markdown("**Groups**")
                        grp_key = f"grp_{pmid}"
                        grp_val = st.session_state.get(grp_key, row.get("抄録","")[:200] if outcomes is None else "")
                        with st.container(border=True):
                            st.markdown("Group description and sample")
                            new_grp = st.text_area("", value=grp_val, key=f"grp_ta_{pmid}",
                                                   label_visibility="collapsed", height=80)
                            st.session_state[grp_key] = new_grp

                        # ── AI抽出結果 / 手動追加
                        st.markdown("**抽出データ（危険因子・効果量・CI）**")

                        if outcomes is None:
                            st.warning("未抽出 — 上の「AI一括自動抽出」で処理されます")
                        else:
                            # 手動追加フォーム
                            with st.form(key=f"add_ex_{pmid}"):
                                fc1, fc2, fc3, fc4, fc5 = st.columns([3,1,1,1,1])
                                nf = fc1.text_input("危険因子", key=f"nf_ex_{pmid}",
                                                    placeholder="previous injury")
                                nm = fc2.selectbox("種類", ["RR","OR","HR","IRR","SMD"], key=f"nm_ex_{pmid}")
                                nv = fc3.number_input("効果量", min_value=0.0, value=1.0,
                                                      step=0.01, key=f"nv_ex_{pmid}")
                                nl = fc4.number_input("CI下限", min_value=0.0, value=0.0,
                                                      step=0.01, key=f"nl_ex_{pmid}")
                                nu = fc5.number_input("CI上限", min_value=0.0, value=0.0,
                                                      step=0.01, key=f"nu_ex_{pmid}")
                                if st.form_submit_button("＋ Add"):
                                    st.session_state.extraction_results.setdefault(pmid, []).append({
                                        "risk_factor": nf, "measure_type": nm, "value": nv,
                                        "ci_lower": nl if nl>0 else None,
                                        "ci_upper": nu if nu>0 else None,
                                        "has_ci": nl>0 and nu>0,
                                        "context": "手動入力", "confirmed": True,
                                    })
                                    _autosave()
                                    st.rerun()

                            for oi, outcome in enumerate(outcomes):
                                ci_str = (f"{outcome['ci_lower']}–{outcome['ci_upper']}"
                                          if outcome.get("has_ci") else "CI不明")
                                ec1, ec2, ec3, ec4 = st.columns([3,2,2,1])
                                new_f = ec1.text_input(
                                    "危険因子", value=outcome.get("risk_factor",""),
                                    key=f"efactor_{pmid}_{oi}", placeholder="危険因子名",
                                    label_visibility="collapsed",
                                )
                                ec2.write(f"**{outcome['measure_type']}** = {outcome['value']}")
                                ec3.write(f"95%CI: {ci_str}")
                                if ec4.button("🗑️", key=f"edel_{pmid}_{oi}"):
                                    st.session_state.extraction_results[pmid].pop(oi)
                                    _autosave()
                                    st.rerun()
                                st.session_state.extraction_results[pmid][oi]["risk_factor"] = new_f
                                all_rows.append({
                                    "PMID": pmid, "タイトル": title[:50], "年": row["年"],
                                    "危険因子": new_f, "効果量の種類": outcome["measure_type"],
                                    "効果量": outcome["value"],
                                    "CI下限": outcome.get("ci_lower",""),
                                    "CI上限": outcome.get("ci_upper",""),
                                })

                        # ── Outcomes / Description of main results
                        st.markdown("**Outcomes measured**")
                        st.text_area("", key=f"outcomes_ta_{pmid}", label_visibility="collapsed", height=60)
                        st.markdown("**Description of main results**")
                        st.text_area("", key=f"results_ta_{pmid}", label_visibility="collapsed", height=80)

                        # Save / Back ボタン
                        def _save_ex(p=pmid, k=_open_key):
                            for o in st.session_state.extraction_results.get(p, []):
                                o["confirmed"] = True
                            _autosave()
                            st.session_state[k] = False
                        def _back_ex(k=_open_key):
                            st.session_state[k] = False
                        sv1, sv2 = st.columns([1, 5])
                        sv1.button("💾 Save", key=f"save_ex_{pmid}",
                                   type="primary", on_click=_save_ex)
                        sv2.button("← Back", key=f"back_ex_{pmid}",
                                   on_click=_back_ex)

            # まとめてCSV出力
            if all_rows:
                result_df_ex = pd.DataFrame(all_rows)
                st.session_state.extraction_df = result_df_ex
                st.divider()
                st.subheader("抽出データ一覧")
                st.dataframe(result_df_ex, use_container_width=True)
                _ec1, _ec2 = st.columns([1,1])
                csv_ex = result_df_ex.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
                _ec1.download_button("📥 CSV ダウンロード", data=csv_ex,
                                     file_name="extracted_data.csv", mime="text/csv")
                if _ec2.button("▶ メタアナリシスへ →", type="primary"):
                    st.session_state.step = 5
                    st.rerun()


# ════════════════════════════════════════════════════
# TAB 5 : メタアナリシス  （JBI SUMARI スタイル）
# ════════════════════════════════════════════════════
with tabs[4]:
    from meta_analysis import (
        StudyData, run_meta_analysis, interpret_heterogeneity,
        heterogeneity_recommendation, auto_select_method,
    )
    from forest_plot import forest_plot as make_forest_plot

    st.header("④ メタアナリシス")

    # セッション初期化
    if "meta_rows" not in st.session_state:
        st.session_state.meta_rows = []   # [{study, ee, ne, ec, nc}, ...]
    if "meta_params" not in st.session_state:
        st.session_state.meta_params = {
            "method": "Mantel-Haenszel",
            "model": "Fixed",
            "measure": "Odds Ratio",
            "ci": "95%",
        }

    pico = st.session_state.get("pico", {})

    # ── Parameters セクション ──────────────────────────
    with st.expander("⚙️ Parameters", expanded=True):
        pc1, pc2, pc3, pc4 = st.columns(4)
        method_sel = pc1.selectbox(
            "Statistical Method",
            ["Mantel-Haenszel", "Inverse Variance", "Peto Odds Ratio",
             "DerSimonian and Laird (IV)", "Restricted Maximum Likelihood"],
            index=["Mantel-Haenszel", "Inverse Variance", "Peto Odds Ratio",
                   "DerSimonian and Laird (IV)", "Restricted Maximum Likelihood"]
                  .index(st.session_state.meta_params["method"]),
            key="meta_method",
        )
        model_sel = pc2.selectbox(
            "Model",
            ["Fixed", "Random"],
            index=["Fixed", "Random"].index(st.session_state.meta_params["model"]),
            key="meta_model",
        )
        measure_sel = pc3.selectbox(
            "Effect Measure",
            ["Odds Ratio", "Risk Ratio", "Hazard Ratio", "Mean Difference", "SMD"],
            index=["Odds Ratio", "Risk Ratio", "Hazard Ratio", "Mean Difference", "SMD"]
                  .index(st.session_state.meta_params["measure"]),
            key="meta_measure",
        )
        ci_sel = pc4.selectbox(
            "Confidence Interval",
            ["95%", "90%", "99%"],
            index=["95%", "90%", "99%"].index(st.session_state.meta_params["ci"]),
            key="meta_ci",
        )
        st.session_state.meta_params = {
            "method": method_sel, "model": model_sel,
            "measure": measure_sel, "ci": ci_sel,
        }

    # ── データ入力テーブル ────────────────────────────
    st.markdown("---")

    # データ抽出タブの結果を自動インポート
    extraction_df = st.session_state.get("extraction_df", pd.DataFrame())
    if not extraction_df.empty and not st.session_state.meta_rows:
        # extraction_df から2x2データを持つ行を自動インポート試行
        pass   # 手動入力が基本

    # テーブルヘッダー（JBI SUMARI スタイル）
    is_binary = measure_sel in ("Odds Ratio", "Risk Ratio", "Peto Odds Ratio")
    if is_binary:
        col_labels = ["Study", "Events (Exp)", "Total (Exp)", "Events (Ctrl)", "Total (Ctrl)"]
    else:
        col_labels = ["Study", "Mean (Exp)", "SD (Exp)", "n (Exp)", "Mean (Ctrl)", "SD (Ctrl)", "n (Ctrl)"]

    # ヘッダー行
    if is_binary:
        hc = st.columns([3, 1.2, 1.2, 1.2, 1.2, 0.8])
    else:
        hc = st.columns([2.5, 1, 1, 0.8, 1, 1, 0.8, 0.8])
    for h, lbl in zip(hc, col_labels):
        h.markdown(f"**{lbl}**")

    st.markdown('<hr style="margin:4px 0">', unsafe_allow_html=True)

    # 既存行の表示・編集
    rows_to_delete = []
    for ri, row_d in enumerate(st.session_state.meta_rows):
        if is_binary:
            c = st.columns([3, 1.2, 1.2, 1.2, 1.2, 0.8])
            row_d["study"] = c[0].text_input("", value=row_d.get("study",""), key=f"ms_{ri}",
                                              label_visibility="collapsed")
            row_d["ee"] = c[1].number_input("", value=int(row_d.get("ee",0)), min_value=0,
                                             key=f"ee_{ri}", label_visibility="collapsed")
            row_d["ne"] = c[2].number_input("", value=int(row_d.get("ne",0)), min_value=0,
                                             key=f"ne_{ri}", label_visibility="collapsed")
            row_d["ec"] = c[3].number_input("", value=int(row_d.get("ec",0)), min_value=0,
                                             key=f"ec_{ri}", label_visibility="collapsed")
            row_d["nc"] = c[4].number_input("", value=int(row_d.get("nc",0)), min_value=0,
                                             key=f"nc_{ri}", label_visibility="collapsed")
            if c[5].button("✕", key=f"del_{ri}"):
                rows_to_delete.append(ri)
        else:
            c = st.columns([2.5, 1, 1, 0.8, 1, 1, 0.8, 0.8])
            row_d["study"]  = c[0].text_input("", value=row_d.get("study",""), key=f"ms_{ri}", label_visibility="collapsed")
            row_d["me"]     = c[1].number_input("", value=float(row_d.get("me",0.0)), key=f"me_{ri}", label_visibility="collapsed")
            row_d["sde"]    = c[2].number_input("", value=float(row_d.get("sde",0.0)), min_value=0.0, key=f"sde_{ri}", label_visibility="collapsed")
            row_d["ne"]     = c[3].number_input("", value=int(row_d.get("ne",0)), min_value=0, key=f"ne_{ri}", label_visibility="collapsed")
            row_d["mc"]     = c[4].number_input("", value=float(row_d.get("mc",0.0)), key=f"mc_{ri}", label_visibility="collapsed")
            row_d["sdc"]    = c[5].number_input("", value=float(row_d.get("sdc",0.0)), min_value=0.0, key=f"sdc_{ri}", label_visibility="collapsed")
            row_d["nc"]     = c[6].number_input("", value=int(row_d.get("nc",0)), min_value=0, key=f"nc_{ri}", label_visibility="collapsed")
            if c[7].button("✕", key=f"del_{ri}"):
                rows_to_delete.append(ri)

    for ri in reversed(rows_to_delete):
        st.session_state.meta_rows.pop(ri)
        st.rerun()

    # ＋ Add 行
    if is_binary:
        ac = st.columns([3, 1.2, 1.2, 1.2, 1.2, 0.8])
        new_study = ac[0].text_input("", placeholder="Select or type citation...",
                                     key="new_study", label_visibility="collapsed")
        new_ee = ac[1].number_input("", value=0, min_value=0, key="new_ee", label_visibility="collapsed")
        new_ne = ac[2].number_input("", value=0, min_value=0, key="new_ne", label_visibility="collapsed")
        new_ec = ac[3].number_input("", value=0, min_value=0, key="new_ec", label_visibility="collapsed")
        new_nc = ac[4].number_input("", value=0, min_value=0, key="new_nc", label_visibility="collapsed")
        if ac[5].button("＋ Add", type="primary"):
            if new_study:
                st.session_state.meta_rows.append({
                    "study": new_study, "ee": new_ee, "ne": new_ne,
                    "ec": new_ec, "nc": new_nc,
                })
                st.rerun()
    else:
        ac = st.columns([2.5, 1, 1, 0.8, 1, 1, 0.8, 0.8])
        new_study = ac[0].text_input("", placeholder="Study...", key="new_study", label_visibility="collapsed")
        new_me  = ac[1].number_input("", value=0.0, key="new_me",  label_visibility="collapsed")
        new_sde = ac[2].number_input("", value=0.0, key="new_sde", label_visibility="collapsed", min_value=0.0)
        new_ne2 = ac[3].number_input("", value=0,   key="new_ne2", label_visibility="collapsed", min_value=0)
        new_mc  = ac[4].number_input("", value=0.0, key="new_mc",  label_visibility="collapsed")
        new_sdc = ac[5].number_input("", value=0.0, key="new_sdc", label_visibility="collapsed", min_value=0.0)
        new_nc  = ac[6].number_input("", value=0,   key="new_nc",  label_visibility="collapsed", min_value=0)
        if ac[7].button("＋ Add", type="primary"):
            if new_study:
                st.session_state.meta_rows.append({
                    "study": new_study, "me": new_me, "sde": new_sde, "ne": new_ne2,
                    "mc": new_mc, "sdc": new_sdc, "nc": new_nc,
                })
                st.rerun()

    # OR/RR用に meta_rows から extraction_df 互換データも取り込む
    if not extraction_df.empty:
        with st.expander("📥 データ抽出タブの結果からインポート"):
            st.caption("データ抽出タブで入力した効果量・CIを直接メタアナリシスに使います。")
            risk_factors_avail = [r for r in extraction_df["危険因子"].unique() if str(r).strip()]
            if risk_factors_avail:
                sel_rf = st.selectbox("危険因子を選択", risk_factors_avail, key="meta_import_rf")
                if st.button("インポート", key="do_import_rf"):
                    rf_rows = extraction_df[extraction_df["危険因子"] == sel_rf]
                    for _, rr in rf_rows.iterrows():
                        try:
                            st.session_state.meta_rows.append({
                                "study": f"{str(rr['タイトル'])[:25]} ({rr['年']})",
                                "_effect": float(rr["効果量"]),
                                "_ci_lower": float(rr["CI下限"]) if rr["CI下限"] else None,
                                "_ci_upper": float(rr["CI上限"]) if rr["CI上限"] else None,
                                "_measure": str(rr["効果量の種類"]),
                                "_direct": True,
                            })
                        except Exception:
                            pass
                    st.rerun()

    st.markdown("---")

    # ── Plot セクション ────────────────────────────────
    st.markdown("### 📊 Plot")
    pg1, pg2 = st.columns([1, 6])

    if pg1.button("📊 Generate", type="primary"):
        rows = st.session_state.meta_rows
        if len(rows) < 2:
            st.error("2件以上の研究を追加してください。")
        else:
            # 「_direct」フラグがある行（効果量直接入力）と2x2テーブル行を区別
            studies = []
            measure_type = measure_sel.split()[0]  # "Odds" → "OR" 等に変換
            _mt_map = {"Odds": "OR", "Risk": "RR", "Hazard": "HR", "Mean": "MD", "SMD": "SMD"}
            mt = _mt_map.get(measure_type, measure_type)

            import math
            for r in rows:
                try:
                    if r.get("_direct"):
                        # 効果量直接入力
                        eff = r["_effect"]
                        cil = r.get("_ci_lower") or 0.0
                        ciu = r.get("_ci_upper") or 0.0
                        # log変換（OR/RR/HR用）
                        if mt in ("OR","RR","HR") and eff > 0:
                            log_e = math.log(eff)
                            se = (math.log(ciu) - math.log(cil)) / (2*1.96) if (cil>0 and ciu>0) else 0.1
                        else:
                            log_e = eff
                            se = (ciu - cil) / (2*1.96) if (ciu > cil) else 0.1
                        studies.append(StudyData(
                            study_id=r["study"], effect=log_e,
                            ci_lower=cil, ci_upper=ciu,
                            measure_type=mt,
                        ))
                    elif is_binary:
                        # 2x2テーブルから効果量を計算
                        ee, ne, ec, nc = int(r["ee"]), int(r["ne"]), int(r["ec"]), int(r["nc"])
                        if ne > 0 and nc > 0 and ee <= ne and ec <= nc:
                            if mt in ("OR","MH"):
                                if ee > 0 and ec > 0 and (ne-ee) > 0 and (nc-ec) > 0:
                                    or_val = (ee*(nc-ec)) / (ec*(ne-ee))
                                    log_or = math.log(or_val)
                                    se = math.sqrt(1/ee + 1/(ne-ee) + 1/ec + 1/(nc-ec))
                                    ci_l = math.exp(log_or - 1.96*se)
                                    ci_u = math.exp(log_or + 1.96*se)
                                    studies.append(StudyData(
                                        study_id=r["study"], effect=or_val,
                                        ci_lower=ci_l, ci_upper=ci_u, measure_type="OR",
                                    ))
                            elif mt == "RR":
                                if ee > 0 and ne > 0 and ec > 0 and nc > 0:
                                    rr_val = (ee/ne) / (ec/nc)
                                    se = math.sqrt(1/ee - 1/ne + 1/ec - 1/nc)
                                    ci_l = math.exp(math.log(rr_val) - 1.96*se)
                                    ci_u = math.exp(math.log(rr_val) + 1.96*se)
                                    studies.append(StudyData(
                                        study_id=r["study"], effect=rr_val,
                                        ci_lower=ci_l, ci_upper=ci_u, measure_type="RR",
                                    ))
                    else:
                        # 連続変数（MD/SMD）
                        me, sde, ne2 = float(r["me"]), float(r["sde"]), int(r["ne"])
                        mc2, sdc, nc2 = float(r["mc"]), float(r["sdc"]), int(r["nc"])
                        if ne2 > 0 and nc2 > 0 and sde > 0 and sdc > 0:
                            md = me - mc2
                            se = math.sqrt(sde**2/ne2 + sdc**2/nc2)
                            ci_l = md - 1.96*se
                            ci_u = md + 1.96*se
                            studies.append(StudyData(
                                study_id=r["study"], effect=md,
                                ci_lower=ci_l, ci_upper=ci_u, measure_type="MD",
                            ))
                except Exception:
                    continue

            if len(studies) < 2:
                st.error("有効なデータが2件未満です（数値を確認してください）。")
            else:
                _method_map = {
                    "Mantel-Haenszel": "MH", "Inverse Variance": "IV",
                    "Peto Odds Ratio": "Peto", "DerSimonian and Laird (IV)": "IV",
                    "Restricted Maximum Likelihood": "IV",
                }
                _var_map = {
                    "Mantel-Haenszel": "fixed", "Inverse Variance": "REML",
                    "Peto Odds Ratio": "fixed", "DerSimonian and Laird (IV)": "DL",
                    "Restricted Maximum Likelihood": "REML",
                }
                force_fixed = (model_sel == "Fixed")
                try:
                    result, _, _, _ = run_meta_analysis(
                        studies,
                        method=_method_map.get(method_sel, "MH"),
                        variance_method=_var_map.get(method_sel, "REML"),
                        force_fixed=force_fixed,
                    )
                    st.session_state.meta_result = result
                    st.session_state.meta_studies = studies
                except Exception as e:
                    st.error(f"計算エラー: {e}")

    # ── 結果表示（JBI SUMARI スタイル） ─────────────────
    if st.session_state.meta_result:
        result    = st.session_state.meta_result
        studies_w = st.session_state.meta_studies
        rows_w    = st.session_state.meta_rows[:len(studies_w)]

        ci_studies = [s for s in studies_w if s.ci_lower and s.ci_upper]
        ci_rows    = rows_w[:len(ci_studies)]

        # 2x2 データ列（あれば渡す）
        _ee = [int(r.get("ee",0)) for r in ci_rows if not r.get("_direct")] or None
        _ne = [int(r.get("ne",0)) for r in ci_rows if not r.get("_direct")] or None
        _ec = [int(r.get("ec",0)) for r in ci_rows if not r.get("_direct")] or None
        _nc = [int(r.get("nc",0)) for r in ci_rows if not r.get("_direct")] or None

        # method ラベル（JBI SUMARI 風）
        _abbrev = {"Mantel-Haenszel":"M-H","Inverse Variance":"IV",
                   "Peto Odds Ratio":"Peto","DerSimonian and Laird (IV)":"DL",
                   "Restricted Maximum Likelihood":"REML"}
        method_abbr = _abbrev.get(method_sel, method_sel[:4])
        method_label = f"{method_abbr}, {model_sel}, {ci_sel}"

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
            method_label=method_label,
            title=f"{st.session_state.get('selected_factor','') or ''} — {method_label}",
            i2=result.i2, tau2=result.tau2,
            q_stat=result.q_stat, q_pvalue=result.q_pvalue,
            n_studies=result.n_studies,
            events_exp=_ee, total_exp=_ne,
            events_ctrl=_ec, total_ctrl=_nc,
        )

        # プロット表示 + Export（Generate ボタン横に Export）
        pg2.download_button("⬇️ Export PNG", data=buf,
                            file_name="forest_plot.png", mime="image/png")
        buf.seek(0)
        st.image(buf, use_container_width=True)

        # 統計サマリー
        lbl_het, icon_het = interpret_heterogeneity(result.i2)
        st.caption(
            f"Heterogeneity: τ²={result.tau2:.2f}; χ²={result.q_stat:.2f}, "
            f"df={result.n_studies-1} (P={result.q_pvalue:.3f}); I²={result.i2:.0f}%  "
            f"{icon_het} {lbl_het}"
        )
        st.info(heterogeneity_recommendation(result.i2))

        # 感度分析
        st.markdown("---")
        st.subheader("🔍 感度分析（Sensitivity Analysis）")
        bias_map = st.session_state.get("bias_results", {})
        sa_rows = []
        for s in studies_w:
            matched_overall = "未評価"
            df_for_sa = st.session_state.get("extraction_df", pd.DataFrame())
            for _, ex_row in df_for_sa.iterrows():
                if str(ex_row.get("タイトル",""))[:25] in s.study_id:
                    pmid_m = str(ex_row["PMID"])
                    if pmid_m in bias_map:
                        matched_overall = bias_map[pmid_m].overall
                    break
            if s.study_id not in st.session_state.sa_exclusions:
                st.session_state.sa_exclusions[s.study_id] = (matched_overall == "High")
            sa_rows.append({"study": s, "bias": matched_overall,
                             "excluded": st.session_state.sa_exclusions[s.study_id]})

        hc = st.columns([4, 2, 2, 1])
        hc[0].markdown("**Study**"); hc[1].markdown("**Effect (95%CI)**")
        hc[2].markdown("**Risk of Bias**"); hc[3].markdown("**Include**")
        for row_d in sa_rows:
            s = row_d["study"]
            bias_icon = {"Low":"🟢","Moderate":"🟡","High":"🔴","Unclear":"⚪"}.get(row_d["bias"],"—")
            c1,c2,c3,c4 = st.columns([4,2,2,1])
            c1.write(s.study_id[:45])
            c2.write(f"{s.effect:.2f} ({s.ci_lower:.2f}–{s.ci_upper:.2f})")
            c3.write(f"{bias_icon} {row_d['bias']}")
            inc = c4.checkbox("", value=not row_d["excluded"],
                              key=f"sa_{s.study_id}", label_visibility="collapsed")
            st.session_state.sa_exclusions[s.study_id] = not inc

        inc_studies = [r["study"] for r in sa_rows
                       if not st.session_state.sa_exclusions[r["study"].study_id]]
        if st.button("▶ 感度分析を実行", key="run_sa"):
            if len(inc_studies) < 2:
                st.error("2件以上必要です。")
            else:
                try:
                    sa_res, *_ = run_meta_analysis(
                        inc_studies,
                        method=_method_map.get(method_sel,"MH"),
                        variance_method=_var_map.get(method_sel,"REML"),
                        force_fixed=(model_sel=="Fixed"),
                    )
                    st.session_state.sa_result = sa_res
                    st.session_state.sa_included = inc_studies
                except Exception as e:
                    st.error(str(e))

        if st.session_state.sa_result:
            sa_res = st.session_state.sa_result
            comp = {
                "": ["Main analysis", f"Sensitivity (n={len(st.session_state.sa_included)})"],
                "Effect": [f"{result.pooled_effect:.2f}", f"{sa_res.pooled_effect:.2f}"],
                f"{ci_sel} CI": [f"{result.ci_lower:.2f}–{result.ci_upper:.2f}",
                                  f"{sa_res.ci_lower:.2f}–{sa_res.ci_upper:.2f}"],
                "I²": [f"{result.i2:.1f}%", f"{sa_res.i2:.1f}%"],
            }
            st.dataframe(pd.DataFrame(comp), use_container_width=True, hide_index=True)
            m_sig = not(result.ci_lower<=1.0<=result.ci_upper)
            s_sig = not(sa_res.ci_lower<=1.0<=sa_res.ci_upper)
            if m_sig == s_sig:
                st.success("✅ Robust to sensitivity analysis")
            else:
                st.warning("⚠️ Statistical significance changed after excluding high-bias studies")


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
