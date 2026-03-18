"""
Figma UI/UX Analysis Tool
Figma APIからデザインデータを取得し、Gemini AIでUI/UX・アクセシビリティ分析を実施
"""
import os
import json
from typing import Any, Dict
import requests
from dotenv import load_dotenv
import google.generativeai as genai
import urllib3
import ssl

# SSL警告を抑制（企業ネットワーク環境用）
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# SSL検証を無効化（グローバル設定）
ssl._create_default_https_context = ssl._create_unverified_context

# 環境変数でSSL検証を無効化
os.environ['PYTHONHTTPSVERIFY'] = '0'
os.environ['CURL_CA_BUNDLE'] = ''
os.environ['REQUESTS_CA_BUNDLE'] = ''


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


def collect_contrast_issues(node: Dict[str, Any], parent_bg: tuple | None = None) -> list[dict]:
    """
    ノードツリーを再帰的に走査し、TEXTノードのWCAGコントラスト比を計算して返す
    """
    issues = []
    # このノード自身の背景色（SOLIDフィルがあれば更新）
    bg = parent_bg
    if node.get("fills"):
        color = get_solid_color(node["fills"])
        if color is not None:
            bg = color

    if node.get("type") == "TEXT" and bg is not None:
        text_color = get_solid_color(node.get("fills", []))
        if text_color is not None:
            style = node.get("style", {})
            font_size   = style.get("fontSize")
            font_weight = style.get("fontWeight")
            ratio = contrast_ratio(text_color, bg)
            level = wcag_level(ratio, font_size, font_weight)
            issues.append({
                "name":       node.get("name", ""),
                "text":       node.get("characters", "")[:40],
                "font_size":  font_size,
                "ratio":      round(ratio, 2),
                "level":      level,
                "text_color": "#{:02X}{:02X}{:02X}".format(
                    int(text_color[0]*255), int(text_color[1]*255), int(text_color[2]*255)),
                "bg_color":   "#{:02X}{:02X}{:02X}".format(
                    int(bg[0]*255), int(bg[1]*255), int(bg[2]*255)),
            })

    for child in node.get("children", []):
        issues.extend(collect_contrast_issues(child, bg))

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
    Gemini AIを使用してデザインデータを分析し、改善レポートを生成
    コントラスト比はPythonで事前計算済みの値を渡す（LLMに推測させない）
    
    Args:
        design_json: 軽量化されたFigmaデザインデータ
        api_key: Gemini APIキー
        contrast_issues: Python計算済みのWCAGコントラスト比リスト
    
    Returns:
        str: Markdown形式の分析レポート
    
    Raises:
        SystemExit: Gemini APIの呼び出しに失敗した場合
    """
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-1.5-pro")
        
        system_instruction = "あなたは熟練の UI/UX デザイナー兼アクセシビリティの専門家です。"
        
        design_json_str = json.dumps(design_json, indent=2, ensure_ascii=False)
        contrast_json_str = json.dumps(contrast_issues, indent=2, ensure_ascii=False)
        
        user_prompt = f"""以下のFigmaデザインデータをJSON形式で提供します。このデータを分析し、UI/UXおよびアクセシビリティの観点から改善レポートをMarkdown形式で作成してください。

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
"""
        
        print("Gemini AIで分析中...")
        print(f"プロンプトサイズ: {len(user_prompt)} 文字")
        
        response = model.generate_content(
            [system_instruction, user_prompt],
            generation_config=genai.GenerationConfig(
                temperature=0,
            )
        )
        
        print("Gemini APIからレスポンスを受信しました")
        
        if not response.text:
            print("エラー: Geminiからのレスポンスが空です")
            raise SystemExit(1)
        
        print("分析が完了しました")
        return response.text
        
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
    
    # ユーザー入力（または定数で定義）
    # コメントアウトを切り替えて使用方法を選択可能
    
    # 方法1: ユーザー入力
    file_key = input("Figma File Key を入力してください: ").strip()
    node_id = input("Node ID を入力してください: ").strip()
    
    # 方法2: 定数で定義（テスト用）
    # file_key = "YOUR_FILE_KEY_HERE"
    # node_id = "YOUR_NODE_ID_HERE"
    
    if not file_key or not node_id:
        print("エラー: file_keyとnode_idを入力してください")
        raise SystemExit(1)
    
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
