"""
ステップ5: メタアナリシスモジュール
REML/DL ランダム効果モデル、M-H法、IV法、Peto法
フォレストプロット生成
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional
import warnings
warnings.filterwarnings("ignore")


@dataclass
class StudyData:
    """個別研究のデータ"""
    study_id: str           # PMID or 著者年
    effect: float           # OR / RR / SMD
    ci_lower: float
    ci_upper: float
    weight: float = 0.0     # メタアナリシス後に付与
    measure_type: str = "RR"


@dataclass
class MetaResult:
    """メタアナリシス結果"""
    pooled_effect: float
    ci_lower: float
    ci_upper: float
    i2: float               # 異質性 I²（%）
    tau2: float             # 研究間分散 τ²
    q_stat: float           # Cochran's Q
    q_pvalue: float         # Q統計量のp値
    n_studies: int
    method: str             # 使用手法
    variance_method: str    # REML / DL / fixed
    measure_type: str
    auto_selected: bool = True
    selection_reason: str = ""


# ===== 手法自動選択 =====

def auto_select_method(studies: list[StudyData]) -> tuple[str, str, str]:
    """
    データに基づいて最適な統計手法を自動選択する

    Returns
    -------
    (method, variance_method, reason)
    method: "MH" / "IV" / "Peto"
    variance_method: "REML" / "DL" / "fixed"
    reason: 選択理由の説明文
    """
    if not studies:
        return "MH", "REML", "データなし"

    measure_types = set(s.measure_type for s in studies)
    n = len(studies)

    # 効果量の種類で判定
    binary_measures = {"OR", "RR", "HR", "IRR"}
    continuous_measures = {"SMD", "MD"}

    has_binary = bool(measure_types & binary_measures)
    has_continuous = bool(measure_types & continuous_measures)

    reasons = []

    # 手法選択
    if has_continuous and not has_binary:
        method = "IV"
        reasons.append("連続アウトカム（SMD/MD）のためInverse Variance法を選択")
    elif has_binary:
        # イベント数が少ない場合はPeto
        # （実際のイベント数がないため、効果量の極端さで代替判断）
        very_large_effects = [s for s in studies if s.effect > 10 or s.effect < 0.1]
        if len(very_large_effects) > len(studies) * 0.3:
            method = "Peto"
            reasons.append("効果量が極端に大きい/小さい研究が多いためPeto法を選択")
        else:
            method = "MH"
            reasons.append("二値アウトカム（OR/RR）のためMantel-Haenszel法を選択")
    else:
        method = "MH"
        reasons.append("デフォルトとしてMantel-Haenszel法を選択")

    # 分散推定法の選択（研究数と異質性の予備推定）
    if n < 3:
        variance_method = "DL"
        reasons.append("研究数が少ない（n<3）ためDerSimonian-Laird法を使用")
    else:
        variance_method = "REML"
        reasons.append("REMLは不偏推定量で信頼性が高いため推奨")

    # 研究数が多く均質性が見込まれる場合
    log_effects = [np.log(s.effect) for s in studies if s.effect > 0]
    if len(log_effects) >= 3:
        cv = np.std(log_effects) / (abs(np.mean(log_effects)) + 1e-6)
        if cv < 0.2:
            reasons.append("効果量のばらつきが小さいため固定効果モデルも検討可")

    return method, variance_method, "。".join(reasons)


# ===== 統計計算 =====

def compute_log_se(study: StudyData) -> tuple[float, float]:
    """log(効果量)と標準誤差を計算"""
    log_effect = np.log(study.effect)
    # CI から SE を逆算: SE = (log(upper) - log(lower)) / (2 * 1.96)
    log_se = (np.log(study.ci_upper) - np.log(study.ci_lower)) / (2 * 1.96)
    return log_effect, log_se


def run_fixed_effects_iv(studies: list[StudyData]) -> MetaResult:
    """Fixed effects (Inverse Variance) メタアナリシス"""
    log_effects = []
    log_ses = []

    for s in studies:
        le, lse = compute_log_se(s)
        log_effects.append(le)
        log_ses.append(lse)

    log_effects = np.array(log_effects)
    log_ses = np.array(log_ses)
    weights = 1 / log_ses**2

    pooled_log = np.sum(weights * log_effects) / np.sum(weights)
    pooled_se = np.sqrt(1 / np.sum(weights))

    # Cochran's Q と I²
    q = np.sum(weights * (log_effects - pooled_log)**2)
    df = len(studies) - 1
    i2 = max(0, (q - df) / q * 100) if q > 0 else 0
    from scipy import stats
    q_pvalue = 1 - stats.chi2.cdf(q, df)

    for i, s in enumerate(studies):
        s.weight = weights[i] / np.sum(weights) * 100

    return MetaResult(
        pooled_effect=np.exp(pooled_log),
        ci_lower=np.exp(pooled_log - 1.96 * pooled_se),
        ci_upper=np.exp(pooled_log + 1.96 * pooled_se),
        i2=i2,
        tau2=0.0,
        q_stat=q,
        q_pvalue=q_pvalue,
        n_studies=len(studies),
        method="IV",
        variance_method="fixed",
        measure_type=studies[0].measure_type,
    )


def run_random_effects(
    studies: list[StudyData],
    method: str = "MH",
    variance_method: str = "REML"
) -> MetaResult:
    """
    ランダム効果メタアナリシス

    Parameters
    ----------
    method          : "MH" / "IV" / "Peto"
    variance_method : "REML" / "DL"
    """
    if len(studies) < 2:
        raise ValueError("メタアナリシスには2件以上の研究が必要です")

    log_effects = []
    log_ses = []

    for s in studies:
        if s.ci_lower is None or s.ci_upper is None:
            continue  # CIなしの研究はスキップ
        le, lse = compute_log_se(s)
        log_effects.append(le)
        log_ses.append(lse)

    if len(log_effects) < 2:
        raise ValueError("95%CIが揃っている研究が2件未満です")

    log_effects = np.array(log_effects)
    log_ses = np.array(log_ses)
    within_var = log_ses**2

    # τ²（研究間分散）の推定
    fixed_weights = 1 / within_var
    pooled_fixed = np.sum(fixed_weights * log_effects) / np.sum(fixed_weights)
    q = np.sum(fixed_weights * (log_effects - pooled_fixed)**2)
    df = len(log_effects) - 1

    # DerSimonian-Laird推定
    c = np.sum(fixed_weights) - np.sum(fixed_weights**2) / np.sum(fixed_weights)
    tau2_dl = max(0, (q - df) / c)

    if variance_method == "REML":
        # REML反復推定
        tau2 = tau2_dl  # 初期値
        for _ in range(100):
            rand_weights = 1 / (within_var + tau2)
            pooled = np.sum(rand_weights * log_effects) / np.sum(rand_weights)
            p = rand_weights**2
            gradient = 0.5 * (np.sum(p * (log_effects - pooled)**2) / np.sum(rand_weights) - 1)
            hessian = -0.5 * np.sum(p**2) / np.sum(rand_weights)**2
            delta = gradient / (-hessian + 1e-8)
            tau2 = max(0, tau2 + delta)
            if abs(delta) < 1e-6:
                break
    else:
        tau2 = tau2_dl

    # ランダム効果重み
    rand_weights = 1 / (within_var + tau2)
    pooled_log = np.sum(rand_weights * log_effects) / np.sum(rand_weights)
    pooled_se = np.sqrt(1 / np.sum(rand_weights))

    # I²
    i2 = max(0, (q - df) / q * 100) if q > 0 else 0

    from scipy import stats
    q_pvalue = 1 - stats.chi2.cdf(q, df)

    # 重みを%で各研究に付与
    weight_sum = np.sum(rand_weights)
    ci_studies = [s for s in studies if s.ci_lower is not None and s.ci_upper is not None]
    for i, s in enumerate(ci_studies):
        s.weight = rand_weights[i] / weight_sum * 100

    return MetaResult(
        pooled_effect=np.exp(pooled_log),
        ci_lower=np.exp(pooled_log - 1.96 * pooled_se),
        ci_upper=np.exp(pooled_log + 1.96 * pooled_se),
        i2=round(i2, 1),
        tau2=round(tau2, 4),
        q_stat=round(q, 2),
        q_pvalue=round(q_pvalue, 4),
        n_studies=len(log_effects),
        method=method,
        variance_method=variance_method,
        measure_type=studies[0].measure_type,
    )


def run_meta_analysis(
    studies: list[StudyData],
    method: str = None,
    variance_method: str = None,
    force_fixed: bool = False
) -> tuple[MetaResult, str, str, str]:
    """
    メインのメタアナリシス実行関数

    Returns
    -------
    (result, method, variance_method, reason)
    """
    auto_selected = method is None or variance_method is None
    auto_method, auto_variance, reason = auto_select_method(studies)

    method = method or auto_method
    variance_method = variance_method or auto_variance

    if force_fixed:
        result = run_fixed_effects_iv(studies)
        result.variance_method = "fixed"
    else:
        result = run_random_effects(studies, method=method, variance_method=variance_method)

    result.auto_selected = auto_selected
    result.selection_reason = reason
    return result, method, variance_method, reason


# ===== 異質性の解釈 =====

def interpret_heterogeneity(i2: float) -> tuple[str, str]:
    """I²値を解釈する"""
    if i2 < 25:
        return "低", "🟢"
    elif i2 < 50:
        return "中程度", "🟡"
    elif i2 < 75:
        return "高", "🟠"
    else:
        return "非常に高い", "🔴"


def heterogeneity_recommendation(i2: float) -> str:
    """I²に基づく推奨コメント"""
    if i2 < 25:
        return "研究間の異質性は低く、固定効果モデルも検討できます。"
    elif i2 < 50:
        return "中程度の異質性があります。ランダム効果モデルが適切です。サブグループ解析を検討してください。"
    elif i2 < 75:
        return "高い異質性があります。プール推定値の解釈には注意が必要です。サブグループ解析・感度分析を実施してください。"
    else:
        return "非常に高い異質性があります。プール推定値の解釈は困難です。サブグループ解析または定性的統合を検討してください。"


if __name__ == "__main__":
    # テストデータ（Green et al. 2020 より）
    test_studies = [
        StudyData("Study1_PreviousHSI", 2.7, 1.8, 4.0, measure_type="RR"),
        StudyData("Study2_RecentHSI",   4.8, 2.9, 7.9, measure_type="RR"),
        StudyData("Study3_Age",         2.46, 1.23, 4.92, measure_type="RR"),
        StudyData("Study4_ACL",         1.7, 1.2, 2.4, measure_type="RR"),
        StudyData("Study5_Calf",        1.5, 1.1, 2.0, measure_type="RR"),
    ]

    print("=== メタアナリシス テスト ===\n")
    result, method, var_method, reason = run_meta_analysis(test_studies)

    print(f"手法: {method} + {var_method}")
    print(f"選択理由: {reason}\n")
    print(f"プール効果量: {result.pooled_effect:.2f}")
    print(f"95%CI: {result.ci_lower:.2f} – {result.ci_upper:.2f}")
    print(f"I²: {result.i2}%  τ²: {result.tau2}")
    print(f"Q = {result.q_stat}, p = {result.q_pvalue}")

    label, icon = interpret_heterogeneity(result.i2)
    print(f"異質性: {icon} {label}")
    print(f"推奨: {heterogeneity_recommendation(result.i2)}")
