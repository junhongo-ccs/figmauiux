# Figma UI/UX Analysis Tool - AI Coding Instructions

## Project Overview

This tool fetches design data from Figma API, simplifies it, and uses Google Gemini AI to generate UI/UX and accessibility improvement reports in Markdown format.

## Architecture

- **Single-file script**: All logic is contained in `main.py`
- **Data flow**: Figma API → Data simplification → Gemini AI analysis → Markdown report
- **Environment**: Python 3.10+, runs in dev container (Ubuntu 24.04.3 LTS)

## Key APIs & Authentication

- **Figma API**: `GET https://api.figma.com/v1/files/{file_key}/nodes?ids={node_id}`
  - Header: `X-Figma-Token: {access_token}`
  - Returns nested node structure in `response["nodes"][node_id]["document"]`
- **Gemini API**: Uses `google.generativeai` library with model `gemini-1.5-pro`
  - Temperature: 0 (for consistent analysis results)

## Required Environment Variables (`.env`)

```
FIGMA_ACCESS_TOKEN=<your_figma_token>
GEMINI_API_KEY=<your_gemini_key>
```

Both must be set or script exits with `SystemExit(1)`.

## Data Processing Pattern

### Node Simplification (`simplify_node_data`)

Recursively extracts only these keys from Figma nodes to reduce token usage:
- `id`, `name`, `type`
- `absoluteBoundingBox` (x, y, width, height)
- `fills` (colors/backgrounds)
- `characters` (TEXT nodes only)
- `style` (TEXT nodes: fontFamily, fontWeight, fontSize, letterSpacing, lineHeightPx)
- `children` (recursively simplified)

Use `.get()` for safe key access since not all nodes have all properties.

## Analysis Criteria (Gemini Prompt)

The tool analyzes designs for:
1. **Accessibility**
   - Contrast ratio issues (text vs background)
   - Font size < 14px warnings
   - Touch targets < 44px warnings
2. **Consistency**
   - Spacing irregularities (margins/padding)
   - Font family/weight inconsistencies
3. **Improvement suggestions** with concrete examples

## Error Handling

- API failures (non-200 status): Print status code + response body, then `SystemExit(1)`
- Missing env vars: Print error message, then `SystemExit(1)`
- Gemini exceptions: Display stack trace, then `SystemExit(1)`

## Output

- Generates `report.md` with Markdown-formatted analysis
- Console message: "レポート作成が完了しました"

## Development Workflow

1. Ensure `.env` file exists with both API keys
2. Run: `python main.py`
3. Input `file_key` and `node_id` when prompted (or modify constants in code)
4. Check generated `report.md` for analysis results
