"""
Figma UI/UX Analysis Tool
Figma APIからデザインデータを取得し、Gemini AIでUI/UX・アクセシビリティ分析を実施
"""
import argparse
import os
import json
import sys
from typing import Any, Dict
import requests
from dotenv import load_dotenv
import google.generativeai as genai


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
        response = requests.get(url, headers=headers, params=params)
        
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


def analyze_design_with_gemini(design_json: dict, api_key: str) -> str:
    """
    Gemini AIを使用してデザインデータを分析し、改善レポートを生成
    
    Args:
        design_json: 軽量化されたFigmaデザインデータ
        api_key: Gemini APIキー
    
    Returns:
        str: Markdown形式の分析レポート
    
    Raises:
        SystemExit: Gemini APIの呼び出しに失敗した場合
    """
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.5-pro")
        
        # プロンプトの構築
        system_instruction = "あなたは熟練の UI/UX デザイナー兼アクセシビリティの専門家です。"
        
        design_json_str = json.dumps(design_json, indent=2, ensure_ascii=False)
        
        user_prompt = f"""以下のFigmaデザインデータをJSON形式で提供します。このデータを分析し、UI/UXおよびアクセシビリティの観点から改善レポートをMarkdown形式で作成してください。

# デザインデータ（JSON）
```json
{design_json_str}
```

# 分析観点

## 1. アクセシビリティ
- コントラスト比: 背景色と文字色のコントラストが低く、視認性に問題がありそうな箇所を指摘してください
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
        
        response = model.generate_content(
            [system_instruction, user_prompt],
            generation_config=genai.GenerationConfig(
                temperature=0,
            )
        )
        
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
    parser = argparse.ArgumentParser(description="Figma UI/UX Analysis Tool")
    parser.add_argument("--file-key", help="Figma File Key")
    parser.add_argument("--node-id", help="Node ID")
    parser.add_argument("--check", action="store_true", help="構文チェックのみを実行（CI用）")
    args = parser.parse_args()
    
    # 構文チェックモード（CI用）
    if args.check:
        print("構文チェック完了")
        return
    
    print("=== Figma UI/UX Analysis Tool ===\n")
    
    # Step 1: 環境変数の読み込み
    figma_token, gemini_key = load_env_vars()
    print("環境変数の読み込みが完了しました\n")
    
    # コマンドライン引数、環境変数、またはユーザー入力からfile_keyとnode_idを取得
    file_key = args.file_key or os.getenv("FIGMA_FILE_KEY")
    node_id = args.node_id or os.getenv("FIGMA_NODE_ID")
    
    # コマンドライン引数や環境変数が設定されていない場合はユーザー入力を求める
    if not file_key and sys.stdin.isatty():
        file_key = input("Figma File Key を入力してください: ").strip()
    if not node_id and sys.stdin.isatty():
        node_id = input("Node ID を入力してください: ").strip()
    
    if not file_key or not node_id:
        print("エラー: file_keyとnode_idを入力してください")
        print("使用方法: python main.py --file-key <KEY> --node-id <ID>")
        print("または環境変数 FIGMA_FILE_KEY と FIGMA_NODE_ID を設定してください")
        raise SystemExit(1)
    
    print()
    
    # Step 2: Figmaデータの取得
    figma_node = fetch_figma_data(file_key, node_id, figma_token)
    print()
    
    # Step 3: データの軽量化
    print("デザインデータを軽量化中...")
    simplified_data = simplify_node_data(figma_node)
    print(f"軽量化完了 (元のキー数から必要な情報のみを抽出)\n")
    
    # Step 4: Gemini AIによる分析
    report_markdown = analyze_design_with_gemini(simplified_data, gemini_key)
    print()
    
    # Step 5: レポートをファイルに保存
    output_filename = "report.md"
    with open(output_filename, "w", encoding="utf-8") as f:
        f.write(report_markdown)
    
    print(f"✓ レポート作成が完了しました")
    print(f"  ファイル名: {output_filename}")


if __name__ == "__main__":
    main()
