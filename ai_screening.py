"""
一次スクリーニングモジュール
タイトル・抄録のキーワードマッチングで採択/除外を自動判定する
"""

import re


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
