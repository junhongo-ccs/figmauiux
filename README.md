# Figma UI/UX Analysis Tool

Figma API からデザインデータを取得し、Google Gemini AI を使用して UI/UX およびアクセシビリティの改善レポートを自動生成する Python ツールです。

## 機能

- Figma の**特定フレーム単位**でデザインデータを取得・分析
  - ⚠️ ページ全体ではなく、フレーム単位での分析を推奨（Gemini のトークン制限対策）
- データを軽量化してトークン使用量を削減
- Gemini AI による以下の観点での分析:
  - アクセシビリティ（コントラスト比、フォントサイズ、タッチターゲットサイズ）
  - デザインの一貫性（余白、フォント）
  - 具体的な改善提案
- Markdown 形式のレポート出力

## 必要要件

- Python 3.10 以上
- **Figma 個人アクセストークン** (Personal Access Token)
- **Google Gemini API キー**

## セットアップ

### 1. リポジトリをクローン

```bash
git clone <repository-url>
cd figmauiux
```

### 2. 必要なライブラリをインストール

```bash
pip install requests google-generativeai python-dotenv
```

### 3. API キーを取得

#### Figma 個人アクセストークン (Personal Access Token)

1. [Figma アカウント設定](https://www.figma.com/settings) にアクセス
2. 下部の「Personal access tokens」セクションへ移動
3. 「Generate new token」をクリックしてトークンを生成
4. 生成されたトークンをコピー（再表示できないため注意）

#### Google Gemini API キー

1. [Google AI Studio](https://aistudio.google.com/app/apikey) にアクセス
2. 「Get API key」または「Create API key」をクリック
3. 生成された API キーをコピー

### 4. 環境変数を設定

`.env` ファイルをプロジェクトルートに作成し、以下の内容を記載:

```env
# Figma 個人アクセストークン
FIGMA_ACCESS_TOKEN=figd_xxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Google Gemini API キー
GEMINI_API_KEY=AIzaSyxxxxxxxxxxxxxxxxxxxxxxxxx
```

## 使用方法

### 1. スクリプトを実行

```bash
python main.py
```

### 2. 必要な情報を入力

実行すると、以下の2つの情報の入力が求められます:

#### (1) Figma File Key

Figma ファイルの URL から取得します:

```
https://www.figma.com/design/{ここがFile Key}/ファイル名?node-id=...
                              ↑
                    この部分をコピー
```

**例**: `xOskOYr8g02pwCze4BWR7a`

#### (2) Node ID（フレームID）

分析したい**特定のフレーム**の ID を取得します:

1. Figma でフレームを選択
2. 右クリック → 「Copy/Paste as」→ 「Copy link」
3. コピーされた URL の末尾を確認:
   ```
   https://www.figma.com/design/xxxxx?node-id=1:1099
                                              ↑
                                    この部分（コロン区切り）
   ```
4. **半角コロン** `:` 区切りの形式で入力（例: `1:1099`）

⚠️ **重要**: ページ全体ではなく、分析したい**フレーム1つ**を指定してください。データ量が多いと Gemini の処理が失敗する可能性があります。

### 3. レポートを確認

分析完了後、`report.md` ファイルが生成されます。

```
✓ レポート作成が完了しました
  ファイル名: report.md
```

## ファイル構成

```
.
├── main.py                           # メインスクリプト
├── .env                              # API キー（gitignore対象、自分で作成）
├── .env.example                      # 環境変数のテンプレート
├── report.md                         # 生成されたレポート（実行後）
├── .github/copilot-instructions.md   # AI エージェント向けガイド
└── README.md                         # このファイル
```

## よくある質問

### Q1: Node ID の入力で「全角コロン」エラーが出る

A: Node ID は**半角コロン** `:` で入力してください（全角 `：` は不可）。

正: `1:1099`  
誤: `1：1099`

### Q2: "node_id is not found" エラーが出る

A: 以下を確認してください:
- Node ID が正しい形式（`数字:数字`）か
- Figma でフレームを選択してリンクをコピーしているか
- アクセス権限のあるファイルか

### Q3: 分析が途中で失敗する

A: データ量が多すぎる可能性があります。**フレーム単位**で分析してください（ページ全体ではなく）。

## 開発

このプロジェクトは Dev Container に対応しています。VS Code で開くと、必要な環境が自動的にセットアップされます。

詳細な実装ガイドラインは `.github/copilot-instructions.md` を参照してください。