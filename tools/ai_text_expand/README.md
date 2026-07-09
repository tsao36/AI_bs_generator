# AI Text Expand (Windows 11)

This tool lets you:
1. Highlight text
2. Right-click to open an AI option menu
3. Replace selected text with AI-expanded text

## How It Works

- AutoHotkey script listens for right-click.
- If text is selected, it shows a menu with:
  - Expand with AI
  - Use Native Context Menu
  - Cancel
- Expand with AI calls a Python script.
- Python sends selected text to your local LLM (Ollama) and returns expanded text.
- AutoHotkey pastes the result over the selected text.

## Prerequisites

- Windows 11
- AutoHotkey v2
- Python 3.9+
- Ollama (local LLM runtime)

## Setup

1. Install Python dependencies:

```powershell
cd tools\ai_text_expand
python -m pip install -r requirements.txt
```

2. Install Ollama and pull a model (PowerShell):

```powershell
ollama pull qwen2.5:7b-instruct
```

3. Optional environment variables (PowerShell):

```powershell
# Optional model override:
$env:LOCAL_LLM_MODEL = "qwen2.5:7b-instruct"
# Optional Ollama URL override:
# $env:OLLAMA_BASE_URL = "http://127.0.0.1:11434"
```

4. Install AutoHotkey v2 and run:

- Double-click `ai_expand.ahk`

## Usage

1. Select text in any editor or text field.
2. Right-click.
3. Click "Expand with AI".
4. Selected text is replaced with the expanded version.

If you still need the original app context menu, choose "Use Native Context Menu" in the popup.

## Notes

- Some protected or custom UI apps may block simulated copy/paste.
- If AI generation fails, verify Ollama service is running and model exists.
- The script currently uses `python` from PATH. If needed, edit `PYTHON_EXE` in `ai_expand.ahk`.
