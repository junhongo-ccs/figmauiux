"""
Figma UI/UX Analysis Tool
Figma APIからデザインデータを取得し、Gemini AIでUI/UX・アクセシビリティ分析を実施
"""
import argparse
import os
import json
import pathlib
import re
import sys
import time
from urllib.parse import parse_qs, urlparse
from typing import Any, Dict
import requests
from dotenv import load_dotenv
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


def parse_args() -> argparse.Namespace:
    """CLI引数を解析する"""
    parser = argparse.ArgumentParser(
        description="Figmaノードを分析してUI/UXレポートを生成します。"
    )
    parser.add_argument(
        "--figma-url",
        help="Figmaの共有URL。file_key と node_id を自動抽出します。",
    )
    parser.add_argument(
        "--file-key",
        help="Figma File Key を直接指定します。",
    )
    parser.add_argument(
        "--node-id",
        help="対象ノードの Node ID を直接指定します。例: 421:6",
    )
    parser.add_argument(
        "--output",
        default="report.md",
        help="出力先のMarkdownファイル名。既定値: report.md",
    )
    parser.add_argument(
        "--skip-gemini",
        action="store_true",
        help="Gemini API を使わず、Pythonのみでレポートを生成します。",
    )
    return parser.parse_args()


def collect_inputs_from_cli_or_prompt(
    args: argparse.Namespace,
) -> tuple[str | None, str | None]:
    """CLI引数、標準入力、対話入力の順で file_key / node_id を解決する"""
    file_key = args.file_key
    node_id = normalize_node_id(args.node_id) if args.node_id else None

    if args.figma_url:
        url_file_key, url_node_id = resolve_figma_input(args.figma_url)
        file_key = file_key or url_file_key
        node_id = node_id or url_node_id
        if url_file_key and url_node_id:
            print("Figma URL から File Key と Node ID を自動取得しました")

    if file_key and node_id:
        return file_key, node_id

    if not sys.stdin.isatty():
        piped_input = sys.stdin.read().strip()
        if piped_input:
            stdin_file_key, stdin_node_id = resolve_figma_input(piped_input)
            file_key = file_key or stdin_file_key
            node_id = node_id or stdin_node_id
            if stdin_file_key and stdin_node_id:
                print("標準入力から File Key と Node ID を自動取得しました")

    if file_key and node_id:
        return file_key, node_id

    first_input = input(
        "Figma URL または File Key を入力してください: "
    ).strip()
    input_file_key, input_node_id = resolve_figma_input(first_input)
    file_key = file_key or input_file_key
    node_id = node_id or input_node_id

    if file_key and node_id:
        print("Figma URL から File Key と Node ID を自動取得しました")
        return file_key, node_id

    if not file_key:
        file_key = input("Figma File Key を入力してください: ").strip()
    if not node_id:
        second_input = input("Node ID または Figma URL を入力してください: ").strip()
        extra_file_key, extra_node_id = resolve_figma_input(second_input)
        file_key = file_key or extra_file_key
        node_id = extra_node_id

    return file_key, node_id


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
    max_attempts = 5
    base_delay = 2.0
    max_delay = 30.0
    retryable_statuses = {429, 500, 502, 503, 504}
    
    print(f"Figma APIにリクエスト中... (file_key: {file_key}, node_id: {node_id})")
    
    try:
        with requests.Session() as session:
            response = None

            for attempt in range(1, max_attempts + 1):
                response = session.get(
                    url,
                    headers=headers,
                    params=params,
                    verify=False,
                    timeout=30,
                )

                if response.status_code == 200:
                    break

                if response.status_code not in retryable_statuses or attempt == max_attempts:
                    break

                retry_after = response.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    delay = float(retry_after)
                else:
                    delay = base_delay * (2 ** (attempt - 1))

                if delay > max_delay:
                    print(
                        "Figma API の再試行待機時間が長すぎるため、自動リトライを中断します "
                        f"(Retry-After={delay:.1f} 秒)"
                    )
                    break

                print(
                    f"Figma APIが一時的に失敗しました "
                    f"(status={response.status_code}, attempt={attempt}/{max_attempts})"
                )
                print(f"{delay:.1f} 秒待機して再試行します...")
                time.sleep(delay)
        
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


def collect_small_text_issues(node: Dict[str, Any]) -> list[dict]:
    """14px未満のTEXTノードを収集する"""
    issues = []
    for entry in flatten_scene_nodes(node):
        current = entry["node"]
        if current.get("type") != "TEXT":
            continue

        style = current.get("style", {})
        font_size = style.get("fontSize")
        if font_size is None or font_size >= 14:
            continue

        issues.append(
            {
                "name": current.get("name", ""),
                "text": current.get("characters", "")[:60],
                "font_size": font_size,
            }
        )

    return issues


def collect_touch_target_candidates(node: Dict[str, Any]) -> list[dict]:
    """44px未満の操作要素候補を収集する"""
    interactive_keywords = {
        "button", "btn", "link", "tab", "chip", "toggle", "switch",
        "cta", "icon", "menu", "card", "group"
    }
    candidates = []

    for entry in flatten_scene_nodes(node):
        current = entry["node"]
        current_type = current.get("type", "")
        name = current.get("name", "")
        bbox = current.get("absoluteBoundingBox")
        if not bbox:
            continue

        width = bbox.get("width")
        height = bbox.get("height")
        if width is None or height is None:
            continue
        if width >= 44 and height >= 44:
            continue

        lowered_name = name.lower()
        if current_type == "TEXT":
            continue
        if current_type not in {"FRAME", "GROUP", "COMPONENT", "INSTANCE", "RECTANGLE", "ELLIPSE"}:
            continue
        if not any(keyword in lowered_name for keyword in interactive_keywords):
            continue

        candidates.append(
            {
                "name": name,
                "type": current_type,
                "width": round(width, 1),
                "height": round(height, 1),
            }
        )

    return candidates


def collect_font_usage(node: Dict[str, Any]) -> list[dict]:
    """フォント利用状況を集計する"""
    usage: dict[tuple[str | None, float | None], int] = {}

    for entry in flatten_scene_nodes(node):
        current = entry["node"]
        if current.get("type") != "TEXT":
            continue

        style = current.get("style", {})
        key = (style.get("fontFamily"), style.get("fontWeight"))
        usage[key] = usage.get(key, 0) + 1

    rows = []
    for (font_family, font_weight), count in sorted(
        usage.items(),
        key=lambda item: (-item[1], str(item[0][0]), str(item[0][1])),
    ):
        rows.append(
            {
                "font_family": font_family or "Unknown",
                "font_weight": font_weight,
                "count": count,
            }
        )
    return rows


def format_text_label(name: str, text: str) -> str:
    """レポート表示用ラベルを組み立てる"""
    stripped_text = text.strip()
    stripped_name = name.strip()
    if stripped_text and stripped_name and stripped_text != stripped_name:
        return f"{stripped_name} / {stripped_text}"
    if stripped_text:
        return stripped_text
    if stripped_name:
        return stripped_name
    return "(名称未設定)"


def build_deterministic_report(
    file_key: str,
    node_id: str,
    contrast_issues: list[dict],
    small_text_issues: list[dict],
    touch_targets: list[dict],
    font_usage: list[dict],
) -> str:
    """Pythonだけで最低限のレポートを生成する"""
    lines = [
        "# UI/UX およびアクセシビリティ改善レポート",
        "",
        f"- 対象 File Key: `{file_key}`",
        f"- 対象 Node ID: `{node_id}`",
        "",
        "## WCAG 2.1 の基準",
        "- 通常テキスト: コントラスト比 4.5:1 以上で AA",
        "- 大きい文字: コントラスト比 3.0:1 以上で AA",
        "- 通常テキストの AAA: 7.0:1 以上",
        "- 大きい文字の AAA: 4.5:1 以上",
        "",
        "## 1. アクセシビリティ",
        "",
        "### 1.1 コントラスト比",
    ]

    failures = [item for item in contrast_issues if "❌" in item["level"]]
    if failures:
        lines.append("以下のテキスト要素は WCAG 2.1 のコントラスト基準を満たしていません。")
        for item in sorted(failures, key=lambda row: row["ratio"]):
            label = format_text_label(item["name"], item["text"])
            lines.append(
                f"- `{label}`: {item['ratio']}:1、文字色 {item['text_color']}、背景色 {item['bg_color']}"
            )
    else:
        lines.append("不合格のテキスト要素は見つかりませんでした。")

    lines.extend([
        "",
        "### 1.2 フォントサイズ",
    ])

    if small_text_issues:
        lines.append("14px未満のテキスト要素です。重要情報かどうかを確認してください。")
        for item in sorted(small_text_issues, key=lambda row: row["font_size"]):
            label = format_text_label(item["name"], item["text"])
            lines.append(f"- `{label}`: {item['font_size']}px")
    else:
        lines.append("14px未満のテキストは見つかりませんでした。")

    lines.extend([
        "",
        "### 1.3 タッチターゲット",
    ])

    if touch_targets:
        lines.append("44px未満の操作要素候補です。実際にタップ対象かをデザイン上で確認してください。")
        for item in sorted(touch_targets, key=lambda row: (row["width"] * row["height"], row["name"])):
            lines.append(
                f"- `{item['name'] or '(名称未設定)'}` ({item['type']}): {item['width']}px x {item['height']}px"
            )
    else:
        lines.append("44px未満の明確な操作要素候補は見つかりませんでした。")

    lines.extend([
        "",
        "## 2. 一貫性",
        "",
        "### 2.1 フォント使用状況",
    ])

    if font_usage:
        for item in font_usage:
            lines.append(
                f"- `{item['font_family']}` / weight `{item['font_weight']}`: {item['count']} 件"
            )
    else:
        lines.append("TEXTノードが見つからなかったため、フォント集計はありません。")

    lines.extend([
        "",
        "### 2.2 所見",
        "- 余白の一貫性は JSON だけでは確定しづらいため、このレポートでは断定しません。",
        "- 断片テキスト単位の指摘が混ざる場合は、実際のUIコンポーネント単位で再確認してください。",
        "",
        "## 3. 改善提案",
        "- コントラスト不合格のテキストは、文字色または背景色を調整して AA 以上にしてください。",
        "- 10px〜12px のテキストは、補助情報かどうかを確認し、必要なら 14px 以上へ引き上げてください。",
        "- 44px未満の操作要素は、タップ領域を拡張してください。",
        "- フォントの使い分けは、フォントファミリー数と weight の種類を絞って整理してください。",
    ])

    return "\n".join(lines) + "\n"


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


def analyze_design_with_gemini(
    design_json: dict,
    api_key: str,
    contrast_issues: list[dict],
    small_text_issues: list[dict],
    touch_targets: list[dict],
    font_usage: list[dict],
    base_report: str,
) -> str:
    """
    Gemini REST APIを直接呼び出してデザインデータを分析し、改善レポートを生成。
    google-generativeai SDKを使わずrequestsで直接呼び出すことでZscaler SSL問題を回避。
    コントラスト比はPythonで事前計算済みの値を渡す（LLMに推測させない）
    """
    design_json_str = json.dumps(design_json, indent=2, ensure_ascii=False)
    contrast_json_str = json.dumps(contrast_issues, indent=2, ensure_ascii=False)
    small_text_json_str = json.dumps(small_text_issues, indent=2, ensure_ascii=False)
    touch_target_json_str = json.dumps(touch_targets, indent=2, ensure_ascii=False)
    font_usage_json_str = json.dumps(font_usage, indent=2, ensure_ascii=False)

    prompt = f"""以下のFigmaデザインデータと、Pythonで事前集計した分析結果を提供します。これをもとに、UI/UXおよびアクセシビリティの観点から改善レポートをMarkdown形式で作成してください。

# デザインデータ（JSON）
```json
{design_json_str}
```

# Pythonで計算済みのコントラスト比（WCAG2.1準拠・再計算禁止）
```json
{contrast_json_str}
```

# 14px未満テキスト一覧（Python抽出済み）
```json
{small_text_json_str}
```

# 44px未満の操作要素候補（Python抽出済み）
```json
{touch_target_json_str}
```

# フォント使用状況（Python集計済み）
```json
{font_usage_json_str}
```

# 参考となるベースレポート（Python生成済み）
```md
{base_report}
```

# 分析観点

## 1. アクセシビリティ
- コントラスト比: 上記の計算済みデータだけを使い、「不合格 ❌」の箇所のみ指摘してください。コントラスト比を推測しないでください
- フォントサイズ: 14px未満のテキスト一覧だけを使って要約してください
- タッチターゲット: Pythonが抽出した候補だけを使って要約してください。候補がない場合は「明確な候補なし」と書いてください

## 2. 一貫性
- 余白: JSONだけでは断定しにくいため、断定口調は避けてください
- フォント: fontFamilyやfontWeightの集計結果から、使い分けの多さを要約してください

## 3. 改善提案
- 上記の問題点に対して、具体的な修正例を提示してください
- ただし、根拠のない一般論を増やしすぎず、Python集計結果に紐づく提案を優先してください

# 出力形式
Markdown形式で、見出しや箇条書きを使って読みやすく構造化してください。
レポートの冒頭に必ず「WCAG 2.1 の基準」という凡例セクションを入れてください。
凡例には以下をそのまま分かりやすく記載してください。

## WCAG 2.1 の基準
- 通常テキスト: コントラスト比 4.5:1 以上で AA
- 大きい文字: 3.0:1 以上で AA
- 通常テキストの AAA: 7.0:1 以上
- 大きい文字の AAA: 4.5:1 以上

# 厳守事項
- Pythonが出していない数値を新規に捏造しないでください
- 要素名やテキストは、与えられた一覧にあるものだけを使ってください
- 「余白が不統一」などの断定は、明確な根拠が薄い場合は避けてください
- ベースレポートを土台に、表現を整理する方向で改善してください
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
            raise RuntimeError(f"Gemini API request failed: status={response.status_code}")

        data = response.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]

        print("Gemini APIからレスポンスを受信しました")
        print("分析が完了しました")
        return text

    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Gemini API呼び出し中に例外が発生しました: {e}") from e
        
    except Exception as e:
        raise RuntimeError(f"Gemini API呼び出し中に例外が発生しました: {e}") from e


def main():
    """
    メイン実行処理
    """
    print("=== Figma UI/UX Analysis Tool ===\n")
    args = parse_args()
    
    # Step 1: 環境変数の読み込み
    figma_token, gemini_key = load_env_vars()
    print("環境変数の読み込みが完了しました\n")
    file_key, node_id = collect_inputs_from_cli_or_prompt(args)

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
    small_text_issues = collect_small_text_issues(figma_node)
    touch_targets = collect_touch_target_candidates(figma_node)
    font_usage = collect_font_usage(figma_node)

    base_report = build_deterministic_report(
        file_key=file_key,
        node_id=node_id,
        contrast_issues=contrast_issues,
        small_text_issues=small_text_issues,
        touch_targets=touch_targets,
        font_usage=font_usage,
    )

    report_markdown = base_report
    if args.skip_gemini:
        print("Gemini AI はスキップされました。Python生成レポートを出力します。\n")
    else:
        print("Gemini AIによる分析を開始します...")
        try:
            report_markdown = analyze_design_with_gemini(
                simplified_data,
                gemini_key,
                contrast_issues,
                small_text_issues,
                touch_targets,
                font_usage,
                base_report,
            )
            print()
        except RuntimeError as e:
            print(f"Gemini分析に失敗したため、Python生成レポートにフォールバックします: {e}\n")
    
    # Step 5: レポートをファイルに保存
    output_filename = args.output
    with open(output_filename, "w", encoding="utf-8") as f:
        f.write(report_markdown)
    
    print(f"✓ レポート作成が完了しました")
    print(f"  ファイル名: {output_filename}")


if __name__ == "__main__":
    main()
