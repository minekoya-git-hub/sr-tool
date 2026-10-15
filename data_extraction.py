"""
ステップ4: データ抽出モジュール
PDFからOR/RR/HR/SMDと95%CIを自動抽出する
"""

import re
import pymupdf
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ExtractedData:
    """抽出されたデータを保持するデータクラス"""
    pmid: str
    title: str

    # 研究デザイン
    study_design: str = ""          # cohort / case-control / cross-sectional
    sample_size: str = ""           # 総サンプル数
    follow_up: str = ""             # 追跡期間

    # 対象集団
    sport: str = ""
    population: str = ""
    mean_age: str = ""
    sex: str = ""                   # male / female / mixed

    # アウトカム（複数ある場合はリスト）
    outcomes: list = field(default_factory=list)
    # 例: [{"factor": "previous injury", "measure": "RR", "value": 2.7, "ci_lower": 1.8, "ci_upper": 4.0}]

    # バイアス評価（後で付与）
    bias_score: str = ""
    bias_tool: str = ""             # QUIPS / ROBINS-I / Downs&Black


@dataclass
class Outcome:
    """個別のアウトカム（危険因子ごと）"""
    risk_factor: str                # 例: "previous hamstring injury"
    measure_type: str               # RR / OR / HR / SMD / IRR
    value: float
    ci_lower: float
    ci_upper: float
    p_value: str = ""
    adjusted: bool = False          # 調整済みか否か
    notes: str = ""


# ===== テキスト抽出 =====

def extract_text_from_pdf(pdf_path: str) -> str:
    """PDFからテキストを抽出する"""
    doc = pymupdf.open(pdf_path)
    text = ""
    for page in doc:
        text += page.get_text()
    doc.close()
    return text


def extract_text_by_section(pdf_path: str) -> dict:
    """PDFをセクション別にテキスト抽出する"""
    full_text = extract_text_from_pdf(pdf_path)

    sections = {
        "abstract": "",
        "methods": "",
        "results": "",
        "discussion": "",
        "full": full_text
    }

    # セクション境界を正規表現で検出
    section_patterns = {
        "abstract": r"(?i)(abstract|summary)\s*\n",
        "methods": r"(?i)(methods?|materials?\s+and\s+methods?|patients?\s+and\s+methods?)\s*\n",
        "results": r"(?i)(results?|findings?)\s*\n",
        "discussion": r"(?i)(discussion|conclusions?)\s*\n",
    }

    positions = {}
    for name, pattern in section_patterns.items():
        match = re.search(pattern, full_text)
        if match:
            positions[name] = match.start()

    # 各セクションのテキストを切り出す
    sorted_sections = sorted(positions.items(), key=lambda x: x[1])
    for i, (name, start) in enumerate(sorted_sections):
        end = sorted_sections[i + 1][1] if i + 1 < len(sorted_sections) else len(full_text)
        sections[name] = full_text[start:end]

    return sections


# ===== 統計値抽出 =====

def extract_effect_sizes(text: str) -> list[dict]:
    """
    テキストからOR/RR/HR/SMD と95%CIを抽出する

    対応パターン例:
    - RR = 2.7 (95% CI: 1.8-4.0)     ← CI付き
    - OR 1.5 (95%CI 1.1 to 2.0)
    - HR: 2.46 [95% CI 1.23-4.92]
    - hazard ratio of 2.46 (1.23-4.92)
    - RR=2.7, p<0.001                  ← CI省略（p値のみ）
    - RR=4.8                           ← 数値のみ
    """
    results = []

    measure_map = {
        "risk ratio": "RR", "relative risk": "RR",
        "odds ratio": "OR",
        "hazard ratio": "HR",
        "incidence rate ratio": "IRR",
        "standardized mean difference": "SMD",
        "md": "MD",
    }

    # パターン1: 略語 + 数値 + CI（括弧・ブラケット）
    pattern1 = re.compile(
        r'\b(RR|OR|HR|IRR|SMD|MD)\s*[=:]\s*'
        r'(\d+\.?\d*)'
        r'\s*[\(\[\{]'
        r'\s*(?:95\s*%?\s*CI\s*[=:]?\s*)?'
        r'(\d+\.?\d*)\s*[-–to]+\s*(\d+\.?\d*)'
        r'\s*[\)\]\}]',
        re.IGNORECASE
    )

    # パターン2: フルワード + 数値 + CI
    pattern2 = re.compile(
        r'\b(risk ratio|odds ratio|hazard ratio|incidence rate ratio|'
        r'relative risk|standardized mean difference)\s+(?:of|was|were|=)?\s*'
        r'(\d+\.?\d*)'
        r'\s*[\(\[\{]'
        r'\s*(?:95\s*%?\s*CI\s*[=:]?\s*)?'
        r'(\d+\.?\d*)\s*[-–to]+\s*(\d+\.?\d*)'
        r'\s*[\)\]\}]',
        re.IGNORECASE
    )

    # パターン3: CI省略（RR=2.7 のみ、またはRR=2.7, p<0.001）
    pattern3 = re.compile(
        r'\b(RR|OR|HR|IRR|SMD|MD)\s*[=:]\s*(\d+\.?\d*)'
        r'(?:\s*,\s*p\s*[=<>]\s*[\d.]+)?'
        r'(?!\s*[\(\[\{])',  # CI括弧が続かない場合のみ
        re.IGNORECASE
    )

    # CI付きパターンを先に処理
    for pattern in [pattern1, pattern2]:
        for match in pattern.finditer(text):
            measure_raw = match.group(1).lower()
            measure = measure_map.get(measure_raw, measure_raw.upper())
            try:
                value = float(match.group(2))
                ci_lower = float(match.group(3))
                ci_upper = float(match.group(4))
                if ci_lower <= value <= ci_upper and value > 0:
                    start = max(0, match.start() - 150)
                    context = text[start:match.end() + 50]
                    results.append({
                        "measure_type": measure,
                        "value": value,
                        "ci_lower": ci_lower,
                        "ci_upper": ci_upper,
                        "has_ci": True,
                        "context": context.replace("\n", " ").strip(),
                        "raw_match": match.group(0)
                    })
            except ValueError:
                continue

    # CI省略パターン（CI付きで取得済みの値は除外）
    ci_values = {(r["measure_type"], r["value"]) for r in results}
    for match in pattern3.finditer(text):
        measure_raw = match.group(1).upper()
        try:
            value = float(match.group(2))
            if (measure_raw, value) not in ci_values and 0 < value < 100:
                start = max(0, match.start() - 150)
                context = text[start:match.end() + 80]
                results.append({
                    "measure_type": measure_raw,
                    "value": value,
                    "ci_lower": None,
                    "ci_upper": None,
                    "has_ci": False,
                    "context": context.replace("\n", " ").strip(),
                    "raw_match": match.group(0)
                })
        except ValueError:
            continue

    # 重複除去
    seen = set()
    unique_results = []
    for r in results:
        key = (r["measure_type"], r["value"], r["ci_lower"], r["ci_upper"])
        if key not in seen:
            seen.add(key)
            unique_results.append(r)

    # CI付きを優先してソート
    unique_results.sort(key=lambda x: (not x["has_ci"]))
    return unique_results


def extract_sample_size(text: str) -> str:
    """サンプルサイズを抽出する"""
    patterns = [
        r'(?:n\s*=\s*|total\s+of\s+|included\s+)(\d[\d,]+)\s*(?:athletes?|players?|participants?|subjects?)',
        r'(\d[\d,]+)\s*(?:athletes?|players?|participants?|subjects?)\s+(?:were|had|with)',
        r'sample\s+(?:size|of)\s+(?:n\s*=\s*)?(\d[\d,]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).replace(",", "")
    return ""


def extract_study_design(text: str) -> str:
    """研究デザインを判定する"""
    text_lower = text.lower()
    if "randomized controlled" in text_lower or "randomised controlled" in text_lower:
        return "RCT"
    elif "prospective cohort" in text_lower:
        return "prospective cohort"
    elif "retrospective cohort" in text_lower:
        return "retrospective cohort"
    elif "cohort" in text_lower:
        return "cohort"
    elif "case-control" in text_lower or "case control" in text_lower:
        return "case-control"
    elif "cross-sectional" in text_lower or "cross sectional" in text_lower:
        return "cross-sectional"
    elif "systematic review" in text_lower or "meta-analysis" in text_lower:
        return "systematic review / meta-analysis"
    return "不明"


def extract_sex(text: str) -> str:
    """対象集団の性別を判定する"""
    text_lower = text.lower()
    has_male = bool(re.search(r'\bmale\b|\bmen\b|\bman\b', text_lower))
    has_female = bool(re.search(r'\bfemale\b|\bwomen\b|\bwoman\b', text_lower))
    if has_male and has_female:
        return "mixed"
    elif has_male:
        return "male"
    elif has_female:
        return "female"
    return "不明"


# ===== メイン抽出関数 =====

def extract_from_pdf(pdf_path: str, pmid: str = "", title: str = "") -> ExtractedData:
    """
    PDFから全データを自動抽出するメイン関数

    Parameters
    ----------
    pdf_path : PDFファイルのパス
    pmid     : PMID（既知の場合）
    title    : タイトル（既知の場合）

    Returns
    -------
    ExtractedData オブジェクト
    """
    sections = extract_text_by_section(pdf_path)
    full_text = sections["full"]
    results_text = sections.get("results", full_text)

    data = ExtractedData(pmid=pmid, title=title)

    # 研究デザイン
    data.study_design = extract_study_design(full_text)

    # サンプルサイズ
    data.sample_size = extract_sample_size(full_text)

    # 性別
    data.sex = extract_sex(full_text)

    # 効果量（Resultsセクション優先、なければ全文）
    raw_outcomes = extract_effect_sizes(results_text)
    if not raw_outcomes:
        raw_outcomes = extract_effect_sizes(full_text)

    # Outcomeオブジェクトに変換
    for r in raw_outcomes:
        data.outcomes.append({
            "risk_factor": "",
            "measure_type": r["measure_type"],
            "value": r["value"],
            "ci_lower": r.get("ci_lower"),
            "ci_upper": r.get("ci_upper"),
            "has_ci": r.get("has_ci", False),
            "context": r["context"],
            "confirmed": False
        })

    return data


def extracted_data_to_rows(data: ExtractedData) -> list[dict]:
    """ExtractedDataをDataFrame行に変換する"""
    rows = []
    for outcome in data.outcomes:
        rows.append({
            "PMID": data.pmid,
            "タイトル": data.title,
            "研究デザイン": data.study_design,
            "サンプルサイズ": data.sample_size,
            "性別": data.sex,
            "危険因子": outcome.get("risk_factor", ""),
            "効果量の種類": outcome.get("measure_type", ""),
            "効果量": outcome.get("value", ""),
            "CI下限": outcome.get("ci_lower", ""),
            "CI上限": outcome.get("ci_upper", ""),
            "文脈": outcome.get("context", "")[:200],
            "確認済み": outcome.get("confirmed", False),
        })
    return rows


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        pdf_path = sys.argv[1]
        print(f"=== PDFデータ抽出テスト: {pdf_path} ===\n")
        data = extract_from_pdf(pdf_path)
        print(f"研究デザイン: {data.study_design}")
        print(f"サンプルサイズ: {data.sample_size}")
        print(f"性別: {data.sex}")
        print(f"抽出されたアウトカム数: {len(data.outcomes)}")
        print("\n--- 抽出結果 ---")
        for i, o in enumerate(data.outcomes[:10]):
            print(f"[{i+1}] {o['measure_type']} = {o['value']} "
                  f"(95%CI: {o['ci_lower']}-{o['ci_upper']})")
            print(f"     文脈: ...{o['context'][:100]}...")
    else:
        print("使い方: python data_extraction.py <PDFファイルパス>")
