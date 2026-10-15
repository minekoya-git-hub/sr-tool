"""
フォレストプロット生成モジュール
matplotlib を使用（Streamlit埋め込み用）
"""

import numpy as np
import matplotlib
import matplotlib.ticker
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.lines import Line2D
import io
from dataclasses import dataclass

# ===== カラー定義（アクセシブル・ニュートラル系） =====
COLOR = {
    "study_box":    "#2563EB",   # 個別研究のボックス（青）
    "study_ci":     "#2563EB",   # CIライン（同色）
    "pooled_box":   "#1E3A5F",   # プール推定のダイヤモンド（濃紺）
    "null_line":    "#6B7280",   # null line（グレー）
    "subgroup":     "#D97706",   # サブグループ行（アンバー）
    "text_primary": "#111827",
    "text_muted":   "#6B7280",
    "grid":         "#E5E7EB",
    "bg":           "#FFFFFF",
}


def forest_plot(
    study_ids: list[str],
    effects: list[float],
    ci_lowers: list[float],
    ci_uppers: list[float],
    weights: list[float],
    pooled_effect: float,
    pooled_ci_lower: float,
    pooled_ci_upper: float,
    measure_type: str = "RR",
    title: str = "Forest Plot",
    i2: float = None,
    tau2: float = None,
    figsize: tuple = (12, None),
) -> io.BytesIO:
    """
    フォレストプロットを生成してBytesIOで返す

    Parameters
    ----------
    study_ids    : 研究ラベルのリスト
    effects      : 効果量のリスト
    ci_lowers    : CI下限のリスト
    ci_uppers    : CI上限のリスト
    weights      : 重み（%）のリスト
    pooled_effect: プール効果量
    pooled_ci_lower / upper: プールCI
    measure_type : "RR" / "OR" / "HR" / "SMD"
    title        : グラフタイトル
    i2           : I²値（%）
    tau2         : τ²値
    figsize      : (幅, 高さ) ← 高さはNoneで自動
    """
    n = len(study_ids)
    row_height = 1.1
    header_height = 2.2
    footer_height = 2.5
    fig_height = figsize[1] or (n * row_height + header_height + footer_height)

    fig, ax = plt.subplots(figsize=(figsize[0], fig_height), facecolor=COLOR["bg"])
    plt.rcParams.update({"font.size": 13})
    ax.set_facecolor(COLOR["bg"])

    # y座標（上から順に）
    y_positions = list(range(n, 0, -1))  # [n, n-1, ..., 1]
    pooled_y = 0  # プール推定は一番下

    # ===== x軸スケール =====
    all_vals = effects + ci_lowers + ci_uppers + [pooled_effect, pooled_ci_lower, pooled_ci_upper]
    all_vals = [v for v in all_vals if v > 0]
    if measure_type == "SMD":
        spread = max(abs(min(ci_lowers + [pooled_ci_lower])), abs(max(ci_uppers + [pooled_ci_upper])))
        xmin = -spread * 1.4
        xmax =  spread * 1.4
        null_x = 0.0
        log_scale = False
    else:
        # null_x=1を中心に、左右を対称な対数距離で設定
        max_log_dist = max(
            abs(np.log(v) - np.log(1.0))
            for v in all_vals if v > 0
        ) * 1.4
        xmin = np.exp(np.log(1.0) - max_log_dist)
        xmax = np.exp(np.log(1.0) + max_log_dist)
        xmin = max(xmin, 0.05)
        null_x = 1.0
        log_scale = True

    # ===== Null line（RR/OR=1 or SMD=0）=====
    ax.axvline(x=null_x, color=COLOR["null_line"], linewidth=1.0,
               linestyle="--", alpha=0.7, zorder=1)

    # ===== グリッド =====
    ax.yaxis.grid(False)
    ax.xaxis.grid(True, color=COLOR["grid"], linewidth=0.5, zorder=0)

    # ===== 個別研究のプロット =====
    max_weight = max(weights) if weights else 1
    for i, (study_id, effect, ci_lo, ci_hi, weight, y) in enumerate(
        zip(study_ids, effects, ci_lowers, ci_uppers, weights, y_positions)
    ):
        # CIライン
        ax.plot([ci_lo, ci_hi], [y, y],
                color=COLOR["study_ci"], linewidth=1.2, zorder=2)

        # ボックスサイズ（重みに比例）
        box_size = 0.08 + 0.22 * (weight / max_weight)
        ax.plot(effect, y,
                marker="s",
                markersize=box_size * 35,
                color=COLOR["study_box"],
                zorder=3)

        # CI端のキャップ
        cap_h = 0.12
        for x_cap in [ci_lo, ci_hi]:
            ax.plot([x_cap, x_cap], [y - cap_h, y + cap_h],
                    color=COLOR["study_ci"], linewidth=1.2, zorder=2)

    # ===== プール推定のダイヤモンド =====
    diamond_h = 0.35
    diamond_x = [pooled_ci_lower, pooled_effect, pooled_ci_upper, pooled_effect]
    diamond_y = [pooled_y, pooled_y + diamond_h, pooled_y, pooled_y - diamond_h]
    ax.fill(diamond_x, diamond_y, color=COLOR["pooled_box"], zorder=3)

    # 区切り線（プール推定の上）
    ax.axhline(y=pooled_y + diamond_h + 0.2,
               color=COLOR["null_line"], linewidth=0.8, alpha=0.5)

    # ===== 軸設定 =====
    if log_scale:
        ax.set_xscale("log")

    ax.set_xlim(xmin, xmax)
    ax.set_ylim(pooled_y - diamond_h - 0.5, n + 0.8)

    # x軸の目盛り（最大7個、重ならないよう間引き）
    if log_scale:
        tick_candidates = [0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0]
        ticks = [t for t in tick_candidates if xmin <= t <= xmax]
        # null_x=1.0 は必ず含める
        if 1.0 not in ticks:
            ticks = sorted(ticks + [1.0])
        ax.set_xticks(ticks)
        ax.set_xticklabels([str(t) for t in ticks],
                           fontsize=12, color=COLOR["text_muted"])
        # minor ticksと指数表記を完全に非表示
        ax.xaxis.set_minor_locator(matplotlib.ticker.NullLocator())
        ax.xaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    else:
        ax.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(nbins=6, prune="both"))
        ax.tick_params(axis="x", labelsize=8, colors=COLOR["text_muted"])

    ax.set_yticks([])
    ax.spines["left"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["top"].set_visible(False)
    ax.spines["bottom"].set_color(COLOR["grid"])

    # ===== テキストラベル（axes座標で配置して重なりを防ぐ） =====
    # axes座標: x=0が左端, x=1が右端
    # プロット領域を左35% / 中央35% / 右30% に分割
    COL_STUDY  = -0.02   # 研究名: axes座標 transform で右揃え
    COL_EFFECT =  1.02   # 効果量＋CI: axes右端の外側
    COL_WEIGHT =  1.30   # 重み: さらに右

    trans = ax.get_yaxis_transform()  # x=axes座標, y=data座標

    for i, (study_id, effect, ci_lo, ci_hi, weight, y) in enumerate(
        zip(study_ids, effects, ci_lowers, ci_uppers, weights, y_positions)
    ):
        # 研究名（左余白、右揃え）
        # 研究名（左余白、右揃え）
        ax.text(COL_STUDY, y, study_id[:38],
                transform=trans,
                va="center", ha="right",
                fontsize=13, color=COLOR["text_primary"])

        # 効果量とCI（右外側）
        ax.text(COL_EFFECT, y,
                f"{effect:.2f} ({ci_lo:.2f}–{ci_hi:.2f})",
                transform=trans,
                va="center", ha="left",
                fontsize=13, color=COLOR["text_primary"])

        # 重み
        ax.text(COL_WEIGHT, y, f"{weight:.1f}%",
                transform=trans,
                va="center", ha="left",
                fontsize=13, color=COLOR["text_muted"])

    # プール推定のラベル
    ax.text(COL_STUDY, pooled_y, "Pooled",
            transform=trans,
            va="center", ha="right",
            fontsize=14, fontweight="bold", color=COLOR["text_primary"])
    ax.text(COL_EFFECT, pooled_y,
            f"{pooled_effect:.2f} ({pooled_ci_lower:.2f}–{pooled_ci_upper:.2f})",
            transform=trans,
            va="center", ha="left",
            fontsize=14, fontweight="bold", color=COLOR["text_primary"])

    # ヘッダー
    header_y = n + 0.6
    ax.text(COL_STUDY, header_y, "Study",
            transform=trans,
            va="center", ha="right",
            fontsize=13, fontweight="bold", color=COLOR["text_muted"])
    ax.text(COL_EFFECT, header_y, f"{measure_type} (95% CI)",
            transform=trans,
            va="center", ha="left",
            fontsize=13, fontweight="bold", color=COLOR["text_muted"])
    ax.text(COL_WEIGHT, header_y, "Weight",
            transform=trans,
            va="center", ha="left",
            fontsize=13, fontweight="bold", color=COLOR["text_muted"])

    # ===== フッター（統計情報）=====
    footer_parts = [f"n={n} studies"]
    if i2 is not None:
        footer_parts.append(f"I² = {i2:.1f}%")
    if tau2 is not None:
        footer_parts.append(f"τ² = {tau2:.4f}")
    footer_text = "  |  ".join(footer_parts)

    ax.text(null_x, pooled_y - diamond_h - 0.35,
            footer_text,
            va="top", ha="center",
            fontsize=12, color=COLOR["text_muted"],
            style="italic")

    # Null lineのラベル
    ax.text(null_x, pooled_y - diamond_h - 0.85,
            f"{'RR=1' if measure_type in ['RR','OR','HR'] else 'SMD=0'}",
            va="top", ha="center",
            fontsize=11, color=COLOR["null_line"])

    # ===== タイトル =====
    ax.set_title(title, fontsize=14, fontweight="bold",
                 color=COLOR["text_primary"], pad=10, loc="left")

    plt.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                facecolor=COLOR["bg"])
    plt.close(fig)
    buf.seek(0)
    return buf


if __name__ == "__main__":
    # テスト実行
    study_ids = [
        "Green et al. 2020",
        "Freckleton & Pizzari 2013",
        "Opar et al. 2015",
        "Ruddy et al. 2018",
        "Van der Horst 2017",
    ]
    effects   = [2.70, 3.10, 2.20, 1.90, 2.50]
    ci_lowers = [1.80, 1.90, 1.40, 1.20, 1.60]
    ci_uppers = [4.00, 5.10, 3.40, 3.00, 3.90]
    weights   = [32.1, 21.4, 18.7, 15.2, 12.6]

    buf = forest_plot(
        study_ids=study_ids,
        effects=effects,
        ci_lowers=ci_lowers,
        ci_uppers=ci_uppers,
        weights=weights,
        pooled_effect=2.32,
        pooled_ci_lower=1.61,
        pooled_ci_upper=3.35,
        measure_type="RR",
        title="Risk of Hamstring Injury: Previous HSI history",
        i2=78.3,
        tau2=0.1232,
    )

    with open("/tmp/test_forest_plot.png", "wb") as f:
        f.write(buf.read())
    print("✅ フォレストプロット生成完了: /tmp/test_forest_plot.png")
