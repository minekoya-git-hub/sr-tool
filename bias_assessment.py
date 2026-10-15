"""
バイアスリスク評価モジュール
QUIPS（コホート研究）/ ROBINS-I（観察研究）対応
PDFテキストからAIが自動判定し、研究者がverifyする
"""

import re
from dataclasses import dataclass, field


# ===== バイアス評価項目定義 =====

QUIPS_DOMAINS = [
    {
        "id": "participation",
        "label": "研究参加（Study Participation）",
        "description": "対象集団の代表性、選択バイアス",
        "keywords_low": ["consecutive", "all eligible", "random sample", "prospective"],
        "keywords_high": ["convenience sample", "volunteer", "self-selected", "retrospective selection"],
    },
    {
        "id": "attrition",
        "label": "研究の脱落（Study Attrition）",
        "description": "追跡率、脱落の偏り",
        "keywords_low": ["complete follow-up", "100% follow", "no loss", "full cohort"],
        "keywords_high": ["lost to follow", "dropout", "attrition", "incomplete follow"],
    },
    {
        "id": "prognostic_factor",
        "label": "予後因子の測定（Prognostic Factor Measurement）",
        "description": "危険因子の測定方法の妥当性",
        "keywords_low": ["validated", "reliable", "objective measure", "isokinetic", "MRI"],
        "keywords_high": ["self-report", "questionnaire", "recall", "subjective"],
    },
    {
        "id": "outcome",
        "label": "アウトカムの測定（Outcome Measurement）",
        "description": "傷害診断の基準と盲検化",
        "keywords_low": ["blinded", "independent", "clinical diagnosis", "imaging confirmed"],
        "keywords_high": ["self-reported injury", "not blinded", "unclear diagnosis"],
    },
    {
        "id": "confounding",
        "label": "交絡因子（Study Confounding）",
        "description": "重要な交絡因子の調整",
        "keywords_low": ["adjusted", "multivariate", "regression", "controlled for"],
        "keywords_high": ["unadjusted", "no adjustment", "univariate only"],
    },
    {
        "id": "statistical",
        "label": "統計解析（Statistical Analysis）",
        "description": "統計手法の適切性",
        "keywords_low": ["cox regression", "logistic regression", "survival analysis", "95% ci"],
        "keywords_high": ["no confidence interval", "p-value only", "inappropriate"],
    },
]

RATING_OPTIONS = ["Low", "Moderate", "High", "Unclear"]
RATING_COLORS = {
    "Low":      "#22C55E",  # 緑
    "Moderate": "#F59E0B",  # 黄
    "High":     "#EF4444",  # 赤
    "Unclear":  "#9CA3AF",  # グレー
}
RATING_ICONS = {
    "Low": "🟢", "Moderate": "🟡", "High": "🔴", "Unclear": "⚪"
}


@dataclass
class DomainResult:
    """1ドメインの評価結果"""
    domain_id: str
    label: str
    rating: str          # Low / Moderate / High / Unclear
    reason: str          # AI判定の根拠
    confirmed: bool = False
    override: str = ""   # 研究者が変更した場合


@dataclass
class BiasResult:
    """1論文のバイアス評価結果"""
    pmid: str
    title: str
    study_design: str
    domains: list[DomainResult] = field(default_factory=list)
    overall: str = ""    # Low / Moderate / High（ドメインから自動算出）
    confirmed: bool = False


# ===== AI自動判定 =====

def auto_assess_domain(domain: dict, text: str) -> tuple[str, str]:
    """
    1ドメインをテキストから自動判定する

    Returns
    -------
    (rating, reason)
    """
    text_lower = text.lower()

    low_hits = [kw for kw in domain["keywords_low"] if kw in text_lower]
    high_hits = [kw for kw in domain["keywords_high"] if kw in text_lower]

    if low_hits and not high_hits:
        rating = "Low"
        reason = f"低バイアスを示す記述あり: {', '.join(low_hits[:2])}"
    elif high_hits and not low_hits:
        rating = "High"
        reason = f"高バイアスを示す記述あり: {', '.join(high_hits[:2])}"
    elif low_hits and high_hits:
        rating = "Moderate"
        reason = f"低バイアス（{low_hits[0]}）と高バイアス（{high_hits[0]}）の両方の記述あり"
    else:
        rating = "Unclear"
        reason = "判定に必要な記述が見つかりませんでした"

    return rating, reason


def auto_assess_bias(text: str, pmid: str = "", title: str = "",
                     study_design: str = "") -> BiasResult:
    """
    論文テキストからバイアスリスクを自動評価する

    Parameters
    ----------
    text         : PDFから抽出したテキスト
    pmid         : PMID
    title        : 論文タイトル
    study_design : 研究デザイン

    Returns
    -------
    BiasResult オブジェクト
    """
    result = BiasResult(pmid=pmid, title=title, study_design=study_design)

    for domain in QUIPS_DOMAINS:
        rating, reason = auto_assess_domain(domain, text)
        result.domains.append(DomainResult(
            domain_id=domain["id"],
            label=domain["label"],
            rating=rating,
            reason=reason,
        ))

    # 総合評価（最悪ドメインで決定）
    ratings = [d.rating for d in result.domains]
    if "High" in ratings:
        result.overall = "High"
    elif "Moderate" in ratings:
        result.overall = "Moderate"
    elif "Unclear" in ratings:
        result.overall = "Unclear"
    else:
        result.overall = "Low"

    return result


def get_confirmed_rating(domain: DomainResult) -> str:
    """研究者の確認済み評価を返す（上書きがあればそちら）"""
    return domain.override if domain.override else domain.rating


def compute_overall(bias_result: BiasResult) -> str:
    """確認済み評価から総合評価を再計算"""
    ratings = [get_confirmed_rating(d) for d in bias_result.domains]
    if "High" in ratings:
        return "High"
    elif "Moderate" in ratings:
        return "Moderate"
    elif "Unclear" in ratings:
        return "Unclear"
    return "Low"


if __name__ == "__main__":
    # テスト
    sample_text = """
    This prospective cohort study included consecutive professional soccer players.
    Injuries were diagnosed by team physicians using clinical criteria and MRI confirmation.
    Multivariate logistic regression was used adjusting for age, previous injury, and position.
    Complete follow-up was achieved for 95% of participants.
    95% confidence intervals are reported for all estimates.
    """

    result = auto_assess_bias(
        sample_text,
        pmid="12345",
        title="Test Study",
        study_design="prospective cohort"
    )

    print(f"=== バイアス評価結果: {result.title} ===")
    print(f"総合: {RATING_ICONS[result.overall]} {result.overall}\n")
    for d in result.domains:
        print(f"{RATING_ICONS[d.rating]} {d.label}: {d.rating}")
        print(f"   根拠: {d.reason}")
