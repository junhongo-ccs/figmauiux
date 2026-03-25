# Figma UI/UX Analysis Tool

Figma API からデザインデータを取得し、Google Gemini AI を使用して UI/UX およびアクセシビリティの改善レポートを自動生成する Python ツールです。

## 機能

- Figma の特定フレーム/ノードからデザインデータを取得
- データを軽量化してトークン使用量を削減
- Gemini AI による以下の観点での分析:
  - アクセシビリティ（コントラスト比、フォントサイズ、タッチターゲットサイズ）
  - デザインの一貫性（余白、フォント）
  - 具体的な改善提案
- Markdown 形式のレポート出力

## 必要要件

- Python 3.10 以上
- Figma アクセストークン
- Google Gemini API キー

## セットアップ

1. リポジトリをクローン

```bash
git clone <repository-url>
cd figmauiux
```

2. 必要なライブラリをインストール

```bash
pip install -r requirements.txt
```

3. 環境変数を設定

`.env` ファイルを作成し、以下の内容を記載:

```env
FIGMA_ACCESS_TOKEN=your_figma_access_token_here
GEMINI_API_KEY=your_gemini_api_key_here
```

- Figma トークンは [Figma Settings](https://www.figma.com/developers/api#access-tokens) から取得
- Gemini API キーは [Google AI Studio](https://ai.google.dev/) から取得

## 使用方法

```bash
python main.py
```

URL を直接指定して実行することもできます。

```bash
python main.py --figma-url "https://www.figma.com/design/...?...node-id=421-6"
```

Gemini を使わず、Pythonのみでベースレポートを出力する場合:

```bash
python main.py --figma-url "https://www.figma.com/design/...?...node-id=421-6" --skip-gemini
```

実行すると、以下の情報を入力するプロンプトが表示されます:

- **Figma URL または Figma File Key**: Figmaの共有リンクをそのまま貼るか、`file_key` だけを入力します
- **Node ID または Figma URL**: 分析したいフレームやコンポーネントのリンク、または `node_id` だけを入力します
  - `1:1099` と `1-1099` のどちらでも入力可能です
  - 全角コロン `：` を貼り付けても内部で半角 `:` に変換されます
  - `https://www.figma.com/design/...?...node-id=1-1099` のようなURLなら、`file_key` と `node_id` を自動抽出できます

分析完了後、既定では `report.md` ファイルが生成されます。`--output` で変更できます。

## ファイル構成

```
.
├── main.py              # メインスクリプト
├── .env                 # API キー（gitignore対象）
├── .env.example         # 環境変数のテンプレート
├── report.md            # 生成されたレポート（実行後）
└── README.md            # このファイル
```

## 開発

このプロジェクトは Dev Container に対応しています。VS Code で開くと、必要な環境が自動的にセットアップされます。

詳細な実装ガイドラインは `.github/copilot-instructions.md` を参照してください。
