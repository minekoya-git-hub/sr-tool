"""
一次スクリーニングモジュール
タイトル・抄録のキーワードマッチングで採択/除外を自動判定する
"""

import re
import os


# ===== 除外基準キーワード =====

EXCLUDE_STUDY_DESIGN = [
    "systematic review", "meta-analysis", "review article",
    "narrative review", "scoping review", "editorial", "letter to the editor",
    "case report", "case series", "commentary",
]

EXCLUDE_POPULATION = [
    "animal", "rat ", "mouse", "mice", "rodent", "rabbit", "canine",
    "cadaver", "cadaveric", "in vitro", "in vivo model",
]

EXCLUDE_OUTCOME = [
    "randomized controlled", "randomised controlled",
    "rehabilitation program", "prevention program", "intervention group",
    "surgery", "surgical outcome", "return to sport protocol",
    "exercise program", "training program", "warm-up program",
]

# 採択を強く支持するキーワード
INCLUDE_STUDY_DESIGN = [
    "cohort", "prospective", "retrospective", "longitudinal",
    "case-control", "cross-sectional", "observational",
]

INCLUDE_OUTCOME = [
    "risk factor", "risk factors", "injury risk", "incidence",
    "odds ratio", "relative risk", "hazard ratio", "injury rate",
    "predictor", "association", "predict",
]


def _normalize(text: str) -> str:
    return text.lower() if text else ""


def auto_screen(
    title: str,
    abstract: str,
    sport: str,
    injury: str,
    population: str = "athlete",
) -> tuple[str, str, float]:
    """
    タイトル・抄録から採択/除外を自動判定する

    Returns
    -------
    (decision, reason, score)
    decision : "採択" / "除外" / "要確認"
    reason   : 判定理由
    score    : 採択スコア 0.0〜1.0
    """
    text = _normalize(f"{title} {abstract}")
    reasons = []
    score = 0.5  # ベーススコア

    # --- 除外チェック（優先） ---

    # 研究デザイン除外
    for kw in EXCLUDE_STUDY_DESIGN:
        if kw in text:
            return "除外", f"除外デザイン: '{kw}'", 0.0

    # 動物・in vitro研究
    for kw in EXCLUDE_POPULATION:
        if kw in text:
            return "除外", f"対象外集団: '{kw}'", 0.0

    # --- 採択スコア計算 ---

    # スポーツ種目のマッチ
    sport_kws = [sport.lower()] + sport.lower().split()
    sport_hits = [kw for kw in sport_kws if kw and kw in text]
    if sport_hits:
        score += 0.15
        reasons.append(f"スポーツ一致: {sport_hits[0]}")
    else:
        score -= 0.1
        reasons.append(f"スポーツ記述なし({sport})")

    # 傷害種類のマッチ
    injury_kws = [injury.lower()] + injury.lower().split()
    injury_hits = [kw for kw in injury_kws if kw and kw in text]
    if injury_hits:
        score += 0.2
        reasons.append(f"傷害一致: {injury_hits[0]}")
    else:
        score -= 0.15
        reasons.append(f"傷害記述なし({injury})")

    # 研究デザイン採択
    design_hits = [kw for kw in INCLUDE_STUDY_DESIGN if kw in text]
    if design_hits:
        score += 0.15
        reasons.append(f"適切なデザイン: {design_hits[0]}")
    else:
        score -= 0.05

    # アウトカム採択
    outcome_hits = [kw for kw in INCLUDE_OUTCOME if kw in text]
    if outcome_hits:
        score += 0.15
        reasons.append(f"リスク因子研究: {outcome_hits[0]}")
    else:
        score -= 0.1

    # 対象集団
    pop_kws = [population.lower()] + ["athlete", "player", "footballer",
               "runner", "swimmer", "sportsman", "sportspeople"]
    pop_hits = [kw for kw in pop_kws if kw in text]
    if pop_hits:
        score += 0.1
        reasons.append(f"対象集団一致: {pop_hits[0]}")

    # 介入研究フラグ（スコアを下げるが除外はしない）
    for kw in EXCLUDE_OUTCOME:
        if kw in text:
            score -= 0.08
            reasons.append(f"介入要素あり: {kw}")
            break

    # --- 判定 ---
    score = max(0.0, min(1.0, score))
    reason_str = " | ".join(reasons[:3])

    if score >= 0.65:
        return "採択", reason_str, round(score, 2)
    elif score >= 0.40:
        return "要確認", reason_str, round(score, 2)
    else:
        return "除外", reason_str, round(score, 2)


def batch_screen(
    articles_df,
    sport: str,
    injury: str,
    population: str = "athlete",
) -> list[dict]:
    """
    DataFrameの全論文をスクリーニングして結果リストを返す

    Returns
    -------
    list of {"pmid", "decision", "reason", "score"}
    """
    results = []
    for _, row in articles_df.iterrows():
        decision, reason, score = auto_screen(
            title=str(row.get("タイトル", "")),
            abstract=str(row.get("抄録", "")),
            sport=sport,
            injury=injury,
            population=population,
        )
        results.append({
            "pmid": str(row.get("PMID", "")),
            "decision": decision,
            "reason": reason,
            "score": score,
        })
    return results


# ──────────────────────────────────────────────────────
# 二次スクリーニング（フルテキスト）自動判定
# ──────────────────────────────────────────────────────

# 二次除外基準（フルテキストで確認すべき詳細基準）
FT_EXCLUDE = [
    # 介入研究・RCT
    "randomized controlled trial", "randomised controlled trial",
    "randomization", "randomisation",
    # 研究対象外
    "animal model", "animal study", "rat model", "mouse model",
    "cadaveric", "in vitro",
    # 不適切アウトカム
    "return to sport", "rehabilitation protocol", "prevention program",
    "training intervention", "warm-up intervention",
    # 不適切デザイン（一次で漏れたもの）
    "systematic review", "meta-analysis",
]

# 二次採択を強く支持するキーワード
FT_INCLUDE_STRONG = [
    "odds ratio", "relative risk", "hazard ratio", "risk ratio",
    "incidence rate", "injury rate", "injury incidence",
    "risk factor", "risk factors", "independent predictor",
    "multivariate", "multivariable", "logistic regression",
    "cox regression", "survival analysis",
]

FT_INCLUDE_DESIGN = [
    "prospective cohort", "retrospective cohort", "cohort study",
    "case-control", "longitudinal study", "observational study",
]


def ft_screen(
    title: str,
    abstract: str,
    fulltext: str,
    sport: str,
    injury: str,
    population: str = "",
) -> tuple[str, str, float]:
    """
    フルテキスト（または抄録）を使った二次スクリーニング自動判定

    Parameters
    ----------
    fulltext : PDFから抽出したテキスト（なければ空文字）

    Returns
    -------
    (decision, reason, score)  decision: "採択"/"除外"/"要確認"
    """
    # フルテキストがあればそちらを優先、なければ抄録で代替
    source_label = "フルテキスト" if fulltext.strip() else "抄録（PDF未取得）"
    base_text = _normalize(f"{title} {abstract} {fulltext}")

    reasons = [f"判定根拠: {source_label}"]
    score = 0.5

    # --- 除外チェック ---
    for kw in FT_EXCLUDE:
        if kw in base_text:
            return "除外", f"FT除外基準: '{kw}'", 0.0

    # --- スポーツ一致 ---
    sport_kws = [s.strip() for s in sport.lower().split() if len(s) > 2]
    if any(kw in base_text for kw in sport_kws):
        score += 0.15
        reasons.append(f"スポーツ一致({sport})")
    else:
        score -= 0.15
        reasons.append(f"スポーツ記述なし({sport})")

    # --- 傷害一致 ---
    injury_kws = [s.strip() for s in injury.lower().split() if len(s) > 2]
    if any(kw in base_text for kw in injury_kws):
        score += 0.20
        reasons.append(f"傷害一致({injury})")
    else:
        score -= 0.20
        reasons.append(f"傷害記述なし({injury})")

    # --- 適切なアウトカム ---
    hits = [kw for kw in FT_INCLUDE_STRONG if kw in base_text]
    if hits:
        score += 0.20
        reasons.append(f"リスク因子研究: {hits[0]}")
    else:
        score -= 0.10
        reasons.append("リスク因子の記述なし")

    # --- 適切な研究デザイン ---
    design_hits = [kw for kw in FT_INCLUDE_DESIGN if kw in base_text]
    if design_hits:
        score += 0.15
        reasons.append(f"適切なデザイン: {design_hits[0]}")

    # --- 集団特性 ---
    if population:
        pop_kws = [s.strip() for s in population.lower().split() if len(s) > 2]
        if any(kw in base_text for kw in pop_kws):
            score += 0.05
            reasons.append(f"集団特性一致({population})")

    # フルテキストがある場合はスコアを少し上げる（信頼性向上）
    if fulltext.strip():
        score += 0.05

    score = max(0.0, min(1.0, score))
    reason_str = " | ".join(reasons[:4])

    if score >= 0.65:
        return "採択", reason_str, round(score, 2)
    elif score >= 0.40:
        return "要確認", reason_str, round(score, 2)
    else:
        return "除外", reason_str, round(score, 2)


def batch_ft_screen(
    articles_df,
    sport: str,
    injury: str,
    population: str = "",
    extraction_results: dict = None,
) -> list[dict]:
    """
    二次スクリーニング一括実行

    Parameters
    ----------
    extraction_results : {pmid: [{text:...}, ...]} データ抽出結果（フルテキスト）
    """
    if extraction_results is None:
        extraction_results = {}

    results = []
    for _, row in articles_df.iterrows():
        pmid = str(row.get("PMID", ""))

        # フルテキスト取得（data_extractionの結果 or PDFパスから直接読む）
        fulltext = ""
        ext = extraction_results.get(pmid, [])
        if ext:
            # data_extractionの結果からテキストを結合
            texts = [o.get("text", "") if isinstance(o, dict) else str(o) for o in ext]
            fulltext = " ".join(texts)

        # PDFパスから直接読む（フォールバック）
        if not fulltext:
            pdf_path = row.get("PDFパス", "")
            if pdf_path and os.path.exists(str(pdf_path)):
                try:
                    import fitz  # PyMuPDF
                    doc = fitz.open(str(pdf_path))
                    pages = [doc[i].get_text() for i in range(min(len(doc), 10))]
                    fulltext = " ".join(pages)
                    doc.close()
                except Exception:
                    pass

        decision, reason, score = ft_screen(
            title=str(row.get("タイトル", "")),
            abstract=str(row.get("抄録", "")),
            fulltext=fulltext,
            sport=sport,
            injury=injury,
            population=population,
        )
        results.append({
            "pmid": pmid,
            "decision": decision,
            "reason": reason,
            "score": score,
        })
    return results


if __name__ == "__main__":
    # テスト
    samples = [
        {
            "title": "Risk factors for hamstring injury in professional soccer players: a prospective cohort study",
            "abstract": "A prospective cohort study of 200 professional soccer players. Previous hamstring injury was associated with increased injury risk (RR=2.7, 95%CI 1.8-4.0). Older age and high training load were also significant predictors.",
        },
        {
            "title": "Effect of Nordic hamstring exercise on injury prevention in football",
            "abstract": "A randomized controlled trial examining the effect of Nordic hamstring exercise on injury prevention in football players. The intervention group showed reduced injury rates.",
        },
        {
            "title": "Hamstring injury in rats: biomechanical analysis",
            "abstract": "An in vitro study using rat muscle preparations to analyze hamstring injury mechanisms.",
        },
        {
            "title": "Systematic review of hamstring injury risk factors in soccer",
            "abstract": "A systematic review and meta-analysis of risk factors for hamstring injuries in soccer players.",
        },
    ]

    print("=== AIスクリーニングテスト ===\n")
    for s in samples:
        dec, reason, score = auto_screen(s["title"], s["abstract"], "soccer", "hamstring")
        icon = {"採択": "✅", "除外": "❌", "要確認": "🟡"}[dec]
        print(f"{icon} [{dec}] score={score}")
        print(f"   タイトル: {s['title'][:60]}")
        print(f"   理由: {reason}\n")
