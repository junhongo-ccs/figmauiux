"""
Figma UI/UX Analysis Tool
Figma APIからデザインデータを取得し、Gemini AIでUI/UX・アクセシビリティ分析を実施
"""
import os
import json
import pathlib
import re
from urllib.parse import parse_qs, urlparse
from typing import Any, Dict
import requests
from dotenv import load_dotenv
import google.generativeai as genai
import urllib3
import ssl
import certifi

# SSL警告を抑制（企業ネットワーク環境用）
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# SSL検証を無効化（グローバル設定）
ssl._create_default_https_context = ssl._create_unverified_context

# Zscaler CA証明書 + certifiのバンドルを結合してすべてのHTTPSクライアントに適用
_ZSCALER_CA = pathlib.Path.home() / "zscaler-ca.pem"
_COMBINED_CA = pathlib.Path(__file__).parent / "_combined_ca.pem"

def _build_combined_ca() -> str:
    """Zscaler CA証明書をcertifiのバンドルに追加した結合ファイルを作成"""
    certifi_bundle = pathlib.Path(certifi.where()).read_bytes()
    zscaler_pem = _ZSCALER_CA.read_bytes() if _ZSCALER_CA.exists() else b""
    _COMBINED_CA.write_bytes(certifi_bundle + b"\n" + zscaler_pem)
    return str(_COMBINED_CA)

_CA_BUNDLE = _build_combined_ca()

# requests / urllib3 / gRPC すべてに同じCA束を適用
os.environ['PYTHONHTTPSVERIFY'] = '0'
os.environ['REQUESTS_CA_BUNDLE'] = _CA_BUNDLE
os.environ['SSL_CERT_FILE'] = _CA_BUNDLE
os.environ['CURL_CA_BUNDLE'] = _CA_BUNDLE
os.environ['GRPC_DEFAULT_SSL_ROOTS_FILE_PATH'] = _CA_BUNDLE


def load_env_vars() -> tuple[str, str]:
    """
    環境変数を読み込み、必要なAPIキーを取得
    
    Returns:
        tuple[str, str]: (FIGMA_ACCESS_TOKEN, GEMINI_API_KEY)
    
    Raises:
        SystemExit: 環境変数が未設定の場合
    """
    load_dotenv()
    
    figma_token = os.getenv("FIGMA_ACCESS_TOKEN")
    gemini_key = os.getenv("GEMINI_API_KEY")
    
    if not figma_token:
        print("エラー: FIGMA_ACCESS_TOKEN が .env に設定されていません")
        raise SystemExit(1)
    
    if not gemini_key:
        print("エラー: GEMINI_API_KEY が .env に設定されていません")
        raise SystemExit(1)
    
    return figma_token, gemini_key


def normalize_node_id(node_id: str) -> str:
    """
    FigmaのNode ID表記をAPI向けに正規化する

    Args:
        node_id: ユーザー入力またはURL由来のNode ID

    Returns:
        str: API呼び出しに使用できるNode ID
    """
    normalized = node_id.strip().replace("：", ":")
    if "-" in normalized and ":" not in normalized:
        normalized = normalized.replace("-", ":", 1)
    return normalized


def extract_figma_ids_from_url(figma_url: str) -> tuple[str | None, str | None]:
    """
    Figma URLからfile_keyとnode_idを抽出する

    Args:
        figma_url: Figmaの共有URL

    Returns:
        tuple[str | None, str | None]: (file_key, node_id)
    """
    parsed = urlparse(figma_url.strip())
    if not parsed.netloc or "figma.com" not in parsed.netloc:
        return None, None

    path_parts = [part for part in parsed.path.split("/") if part]
    file_key = None
    if len(path_parts) >= 2 and path_parts[0] in {"design", "file"}:
        file_key = path_parts[1]
    elif len(path_parts) >= 3 and path_parts[0] == "proto":
        file_key = path_parts[2]

    query = parse_qs(parsed.query)
    raw_node_id = query.get("node-id", [None])[0]
    node_id = normalize_node_id(raw_node_id) if raw_node_id else None
    return file_key, node_id


def resolve_figma_input(raw_input: str) -> tuple[str | None, str | None]:
    """
    ユーザー入力からfile_keyとnode_idを解決する

    Args:
        raw_input: file_key / URL / node_id のいずれか

    Returns:
        tuple[str | None, str | None]: (file_key, node_id)
    """
    text = raw_input.strip()
    if not text:
        return None, None

    file_key, node_id = extract_figma_ids_from_url(text)
    if file_key or node_id:
        return file_key, node_id

    normalized = normalize_node_id(text)
    if re.fullmatch(r"\d+:\d+", normalized):
        return None, normalized

    return text, None


def fetch_figma_data(file_key: str, node_id: str, access_token: str) -> dict:
    """
    Figma APIから指定されたノードのデータを取得
    
    Args:
        file_key: FigmaファイルのキーID
        node_id: 取得対象のノードID
        access_token: Figma APIアクセストークン
    
    Returns:
        dict: 指定されたノードのdocumentデータ
    
    Raises:
        SystemExit: APIリクエストが失敗した場合
    """
    url = f"https://api.figma.com/v1/files/{file_key}/nodes"
    headers = {
        "X-Figma-Token": access_token
    }
    params = {
        "ids": node_id
    }
    
    print(f"Figma APIにリクエスト中... (file_key: {file_key}, node_id: {node_id})")
    
    try:
        response = requests.get(url, headers=headers, params=params, verify=False)
        
        if response.status_code != 200:
            print(f"エラー: Figma APIリクエストが失敗しました")
            print(f"ステータスコード: {response.status_code}")
            print(f"レスポンス本文: {response.text}")
            raise SystemExit(1)
        
        response_json = response.json()
        
        # 指定されたノードのdocumentを取得
        if "nodes" not in response_json or node_id not in response_json["nodes"]:
            print(f"エラー: レスポンスに指定されたノード (node_id: {node_id}) が含まれていません")
            print(f"レスポンス: {json.dumps(response_json, indent=2, ensure_ascii=False)}")
            raise SystemExit(1)
        
        document = response_json["nodes"][node_id].get("document")
        
        if not document:
            print(f"エラー: ノードにdocumentフィールドが存在しません")
            raise SystemExit(1)
        
        print("Figma データの取得に成功しました")
        return document
        
    except requests.exceptions.RequestException as e:
        print(f"エラー: HTTPリクエストに失敗しました: {e}")
        raise SystemExit(1)


def get_solid_color(fills: list) -> tuple[float, float, float] | None:
    """fillsリストから最初のSOLIDカラーのRGB(0-1)を返す"""
    for fill in fills:
        if fill.get("type") == "SOLID" and fill.get("visible", True):
            c = fill.get("color", {})
            r, g, b = c.get("r", 0), c.get("g", 0), c.get("b", 0)
            return (r, g, b)
    return None


def linearize(c: float) -> float:
    """sRGB値(0-1)を線形化（WCAG2.1準拠）"""
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(r: float, g: float, b: float) -> float:
    """WCAG2.1の相対輝度を計算"""
    return 0.2126 * linearize(r) + 0.7152 * linearize(g) + 0.0722 * linearize(b)


def contrast_ratio(color1: tuple, color2: tuple) -> float:
    """2色間のWCAG2.1コントラスト比を計算（1〜21の範囲）"""
    l1 = relative_luminance(*color1)
    l2 = relative_luminance(*color2)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def wcag_level(ratio: float, font_size: float | None, font_weight: float | None) -> str:
    """コントラスト比からWCAG達成レベルを判定"""
    # ラージテキスト判定: 18pt(24px)以上 or 14pt(18.67px)以上かつBold
    is_large = (font_size is not None and font_size >= 24) or \
               (font_size is not None and font_size >= 18.67 and font_weight is not None and font_weight >= 700)
    threshold_aa  = 3.0 if is_large else 4.5
    threshold_aaa = 4.5 if is_large else 7.0
    if ratio >= threshold_aaa:
        return "AAA ✅"
    elif ratio >= threshold_aa:
        return "AA ✅"
    else:
        return "不合格 ❌"


def get_bbox(node: Dict[str, Any]) -> tuple[float, float, float, float] | None:
    """absoluteBoundingBox を (x1, y1, x2, y2) 形式で返す"""
    bbox = node.get("absoluteBoundingBox")
    if not bbox:
        return None
    x = bbox.get("x")
    y = bbox.get("y")
    w = bbox.get("width")
    h = bbox.get("height")
    if None in {x, y, w, h}:
        return None
    return (x, y, x + w, y + h)


def bbox_contains_point(
    bbox: tuple[float, float, float, float], point: tuple[float, float]
) -> bool:
    """矩形が点を含むか判定"""
    x1, y1, x2, y2 = bbox
    px, py = point
    return x1 <= px <= x2 and y1 <= py <= y2


def bbox_area(bbox: tuple[float, float, float, float]) -> float:
    """矩形面積を返す"""
    x1, y1, x2, y2 = bbox
    return max(0, x2 - x1) * max(0, y2 - y1)


def flatten_scene_nodes(node: Dict[str, Any], depth: int = 0) -> list[dict]:
    """ノードツリーを描画順に近い形でフラット化する"""
    nodes = [{"node": node, "depth": depth}]
    for child in node.get("children", []):
        nodes.extend(flatten_scene_nodes(child, depth + 1))
    return nodes


def find_background_color(
    text_entry: dict, painted_entries: list[dict]
) -> tuple[float, float, float] | None:
    """
    テキスト背後の背景色を、座標と描画順から推定する

    同じ親でなくても、背面にある塗りノードを候補にする。
    """
    bbox = get_bbox(text_entry["node"])
    if not bbox:
        return None

    x1, y1, x2, y2 = bbox
    center = ((x1 + x2) / 2, (y1 + y2) / 2)
    candidates = []

    for entry in painted_entries:
        if entry["index"] >= text_entry["index"]:
            continue

        candidate_bbox = get_bbox(entry["node"])
        if not candidate_bbox:
            continue

        if not bbox_contains_point(candidate_bbox, center):
            continue

        color = get_solid_color(entry["node"].get("fills", []))
        if color is None:
            continue

        candidates.append(
            {
                "color": color,
                "depth": entry["depth"],
                "index": entry["index"],
                "area": bbox_area(candidate_bbox),
            }
        )

    if not candidates:
        return None

    # なるべく前面にあり、かつ面積が小さい背景を優先する
    best = sorted(
        candidates,
        key=lambda item: (item["depth"], item["index"], -item["area"]),
        reverse=True,
    )[0]
    return best["color"]


def collect_contrast_issues(node: Dict[str, Any]) -> list[dict]:
    """
    ノードツリーを再帰的に走査し、TEXTノードのWCAGコントラスト比を計算して返す
    """
    scene_entries = flatten_scene_nodes(node)
    for index, entry in enumerate(scene_entries):
        entry["index"] = index

    painted_entries = [
        entry
        for entry in scene_entries
        if entry["node"].get("type") != "TEXT"
        and get_solid_color(entry["node"].get("fills", [])) is not None
    ]

    issues = []

    for entry in scene_entries:
        current = entry["node"]
        if current.get("type") != "TEXT":
            continue

        text_color = get_solid_color(current.get("fills", []))
        bg = find_background_color(entry, painted_entries)
        if text_color is None or bg is None:
            continue

        style = current.get("style", {})
        font_size = style.get("fontSize")
        font_weight = style.get("fontWeight")
        ratio = contrast_ratio(text_color, bg)
        level = wcag_level(ratio, font_size, font_weight)
        issues.append(
            {
                "name": current.get("name", ""),
                "text": current.get("characters", "")[:40],
                "font_size": font_size,
                "ratio": round(ratio, 2),
                "level": level,
                "text_color": "#{:02X}{:02X}{:02X}".format(
                    int(text_color[0] * 255),
                    int(text_color[1] * 255),
                    int(text_color[2] * 255),
                ),
                "bg_color": "#{:02X}{:02X}{:02X}".format(
                    int(bg[0] * 255), int(bg[1] * 255), int(bg[2] * 255)
                ),
            }
        )

    return issues


def simplify_node_data(node: Dict[str, Any]) -> Dict[str, Any]:
    """
    Figmaノードから必要な情報のみを抽出し、軽量化した辞書を作成
    
    Args:
        node: Figmaノードの辞書
    
    Returns:
        dict: 軽量化されたノードデータ
    """
    simplified = {}
    
    # 基本情報
    if "id" in node:
        simplified["id"] = node["id"]
    if "name" in node:
        simplified["name"] = node["name"]
    if "type" in node:
        simplified["type"] = node["type"]
    
    # 位置・サイズ情報
    if "absoluteBoundingBox" in node:
        bbox = node["absoluteBoundingBox"]
        simplified["absoluteBoundingBox"] = {
            "x": bbox.get("x"),
            "y": bbox.get("y"),
            "width": bbox.get("width"),
            "height": bbox.get("height")
        }
    
    # 塗りつぶし情報（背景色など）
    if "fills" in node:
        simplified["fills"] = node["fills"]
    
    # TEXTノードの場合
    if node.get("type") == "TEXT":
        if "characters" in node:
            simplified["characters"] = node["characters"]
        
        if "style" in node:
            style = node["style"]
            simplified["style"] = {
                "fontFamily": style.get("fontFamily"),
                "fontWeight": style.get("fontWeight"),
                "fontSize": style.get("fontSize"),
                "letterSpacing": style.get("letterSpacing"),
                "lineHeightPx": style.get("lineHeightPx")
            }
    
    # 子要素を再帰的に処理
    if "children" in node and node["children"]:
        simplified["children"] = [
            simplify_node_data(child) for child in node["children"]
        ]
    
    return simplified


def analyze_design_with_gemini(design_json: dict, api_key: str, contrast_issues: list[dict]) -> str:
    """
    Gemini REST APIを直接呼び出してデザインデータを分析し、改善レポートを生成。
    google-generativeai SDKを使わずrequestsで直接呼び出すことでZscaler SSL問題を回避。
    コントラスト比はPythonで事前計算済みの値を渡す（LLMに推測させない）
    """
    design_json_str = json.dumps(design_json, indent=2, ensure_ascii=False)
    contrast_json_str = json.dumps(contrast_issues, indent=2, ensure_ascii=False)

    prompt = f"""以下のFigmaデザインデータをJSON形式で提供します。このデータを分析し、UI/UXおよびアクセシビリティの観点から改善レポートをMarkdown形式で作成してください。

# デザインデータ（JSON）
```json
{design_json_str}
```

# コントラスト比（WCAG2.1準拠・Python計算済み・あなたが再計算する必要はありません）
```json
{contrast_json_str}
```

# 分析観点

## 1. アクセシビリティ
- コントラスト比: 上記の計算済みデータを使い、「不合格 ❌」の箇所のみ具体的に指摘してください。自分でコントラスト比を推測しないでください
- フォントサイズ: 14px未満のテキストがある場合は警告してください
- タッチターゲット: 幅または高さが44px未満の要素（ボタンやリンクなど）がある場合は警告してください

## 2. 一貫性
- 余白: absoluteBoundingBoxから推測される要素間の余白にばらつきがないか確認してください
- フォント: fontFamilyやfontWeightに不統一な箇所がないか確認してください

## 3. 改善提案
- 上記の問題点に対して、具体的な修正例を提示してください
  例: 「ボタンの高さを44px以上にする」「本文フォントサイズを16pxにする」など

# 出力形式
Markdown形式で、見出しや箇条書きを使って読みやすく構造化してください。
レポートの冒頭に必ず「WCAG 2.1 の基準」という凡例セクションを入れてください。
凡例には以下をそのまま分かりやすく記載してください。

## WCAG 2.1 の基準
- 通常テキスト: コントラスト比 4.5:1 以上で AA
- 大きい文字: 3.0:1 以上で AA
- 通常テキストの AAA: 7.0:1 以上
- 大きい文字の AAA: 4.5:1 以上
"""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
    payload = {
        "system_instruction": {"parts": [{"text": "あなたは熟練の UI/UX デザイナー兼アクセシビリティの専門家です。"}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0}
    }

    print("Gemini AIで分析中...")
    print(f"プロンプトサイズ: {len(prompt)} 文字")

    try:
        response = requests.post(url, json=payload, verify=False, timeout=120)
        if response.status_code != 200:
            print(f"エラー: Gemini APIリクエストが失敗しました (status={response.status_code})")
            print(response.text[:500])
            raise SystemExit(1)

        data = response.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]

        print("Gemini APIからレスポンスを受信しました")
        print("分析が完了しました")
        return text

    except requests.exceptions.RequestException as e:
        print(f"エラー: Gemini API呼び出し中に例外が発生しました: {e}")
        raise SystemExit(1)
        
    except Exception as e:
        print(f"エラー: Gemini API呼び出し中に例外が発生しました")
        print(f"例外の詳細: {e}")
        import traceback
        traceback.print_exc()
        raise SystemExit(1)


def main():
    """
    メイン実行処理
    """
    print("=== Figma UI/UX Analysis Tool ===\n")
    
    # Step 1: 環境変数の読み込み
    figma_token, gemini_key = load_env_vars()
    print("環境変数の読み込みが完了しました\n")
    
    first_input = input(
        "Figma URL または File Key を入力してください: "
    ).strip()
    file_key, node_id = resolve_figma_input(first_input)

    if file_key and node_id:
        print("Figma URL から File Key と Node ID を自動取得しました")
    else:
        if not file_key:
            file_key = input("Figma File Key を入力してください: ").strip()
        if not node_id:
            second_input = input("Node ID または Figma URL を入力してください: ").strip()
            extra_file_key, extra_node_id = resolve_figma_input(second_input)
            file_key = file_key or extra_file_key
            node_id = extra_node_id

    if not file_key or not node_id:
        print("エラー: file_keyとnode_idを入力してください")
        raise SystemExit(1)
    
    # URLのハイフン区切りや全角コロンをAPIのNode ID形式に正規化
    node_id = normalize_node_id(node_id)
    
    print()
    
    # Step 2: Figmaデータの取得
    figma_node = fetch_figma_data(file_key, node_id, figma_token)
    print()
    print("デザインデータを軽量化中...")
    simplified_data = simplify_node_data(figma_node)
    print(f"軽量化完了 (元のキー数から必要な情報のみを抽出)")
    
    # デバッグ: 軽量化データのサイズを確認
    simplified_json_str = json.dumps(simplified_data, ensure_ascii=False)
    print(f"軽量化データサイズ: {len(simplified_json_str)} 文字\n")
    
    # Step 3: WCAGコントラスト比をPythonで正確に計算
    print("WCAGコントラスト比を計算中...")
    contrast_issues = collect_contrast_issues(figma_node)
    print(f"テキスト要素 {len(contrast_issues)} 件のコントラスト比を計算しました")
    failures = [c for c in contrast_issues if "❌" in c["level"]]
    print(f"  不合格: {len(failures)} 件 / AA以上: {len(contrast_issues) - len(failures)} 件\n")

    # Step 4: Gemini AIによる分析（コントラスト計算済みデータを渡す）
    print("Gemini AIによる分析を開始します...")
    report_markdown = analyze_design_with_gemini(simplified_data, gemini_key, contrast_issues)
    print()
    
    # Step 5: レポートをファイルに保存
    output_filename = "report.md"
    with open(output_filename, "w", encoding="utf-8") as f:
        f.write(report_markdown)
    
    print(f"✓ レポート作成が完了しました")
    print(f"  ファイル名: {output_filename}")


if __name__ == "__main__":
    main()
