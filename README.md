# AI Text Expand — Local LLM

> Right-click any selected text in a browser and instantly rewrite it with a locally running AI model. No internet, no API keys, no data leaves your PC.

---

## How It Works

1. Highlight text in any browser webpage text field.
2. Right-click the selection **twice** (or press **Alt + Right-click** for a single-step shortcut).
3. Choose a length option from the AI menu.
4. The selected text is replaced in-place with the rewritten version.

Keyboard shortcut: **Ctrl + Alt + A** opens the AI menu at the mouse pointer.

### Menu Options

| Option | Available when |
|---|---|
| Expand — 2 sentences | Always |
| Expand — 5 sentences | Always |
| Expand — paragraph | Always |
| Shrink — 1 sentence | Selection is large (≥ 30 words or 120 chars) |
| Shrink — 3 sentences | Selection is large |

### Supported Browsers

Chrome · Edge · Firefox · Brave · Opera · Vivaldi · Outlook (desktop)

---

## Quick Install

Download the latest release zip from [`dist/`](dist/), extract it, then double-click:

```
01_Setup_and_Start.exe
```

The installer handles everything: Python venv, AutoHotkey v2, Ollama, and the configured LLM model. It also detects Intel integrated graphics and enables GPU acceleration automatically.

If Windows blocks the unsigned executable, use the included `Install.cmd` fallback instead.

### What gets installed

- App files copied to `%LOCALAPPDATA%\AITextExpandLocalLLM`
- Python virtual environment created in the same folder
- Ollama installed (via `winget`, or direct download if winget is unavailable)
- Default LLM model pulled: `llama3.1:8b-instruct-q4_K_M`
- AI Text Expand launched automatically on completion

### Corporate / Intel network

The installer routes downloads through `http://proxy-dmz.intel.com:912` by default.

```powershell
# Custom proxy
.\scripts\install.ps1 -Proxy "http://your-proxy:port"

# No proxy
.\scripts\install.ps1 -NoProxy

# Skip winget-based installs (install Python, AHK, Ollama manually first)
.\scripts\install.ps1 -SkipWingetInstall

# Skip model download (useful on restricted networks — pull the model later)
.\scripts\install.ps1 -SkipModelPull
```

If the model download times out on a restricted network, the app still installs. Pull the model manually later with `ollama pull llama3.1:8b-instruct-q4_K_M`, or double-click `Install.exe` again once connected.

---

## Configuration

Edit `config.example.json` in the install folder:

```json
{
  "LOCAL_LLM_MODEL": "llama3.1:8b-instruct-q4_K_M",
  "LOCAL_LLM_OUTPUT_LANGUAGE": "auto",
  "OLLAMA_BASE_URL": "http://127.0.0.1:11434",
  "timeout_seconds": 90,
  "OLLAMA_NUM_GPU": 999
}
```

| Setting | Description |
|---|---|
| `LOCAL_LLM_MODEL` | Any model installed in Ollama. Instruction-tuned models work best. |
| `LOCAL_LLM_OUTPUT_LANGUAGE` | `auto` (detect from input), or force: `english`, `chinese-traditional`, `chinese-simplified`, `japanese`, `korean` … |
| `OLLAMA_BASE_URL` | Change if Ollama runs on a different port or host. |
| `timeout_seconds` | Per-request timeout. Increase for slower hardware. |
| `OLLAMA_NUM_GPU` | GPU offload layers sent to Ollama. `999` = offload everything. |

To confirm which processor Ollama is actually using, run `ollama ps` and check the **PROCESSOR** column.

---

## Intel GPU Acceleration

The installer automatically detects Intel Arc, Iris Xe, UHD, and HD integrated graphics and sets `OLLAMA_IGPU_ENABLE=1` so Ollama uses the GPU instead of CPU.

`run.ps1` also enforces this at every launch — if Ollama was already running without GPU support it is restarted transparently.

---

## Updating

From the right-click AI menu, choose **Update to Latest Version**.

The updater:
1. Calls the GitHub API to find the newest release zip.
2. Downloads and extracts it silently.
3. Runs the installer in update mode (skips model re-pull).
4. Offers to reload AI Text Expand immediately.

To check which version is running, look at `pyproject.toml` in the install folder.

---

## Starting / Stopping

```powershell
# Start (or restart) the AutoHotkey helper
.\scripts\run.ps1

# Register to start automatically at Windows login
.\scripts\enable_startup.ps1

# Check if it is running (auto-starts if not)
.\scripts\health_check.ps1
```

The AutoHotkey tray icon lets you exit the helper at any time.

---

## Diagnostics

| Location | Contains |
|---|---|
| `%LOCALAPPDATA%\AITextExpandLocalLLM\logs\ai_text_expand.log` | Full run log |
| `%LOCALAPPDATA%\AITextExpandLocalLLM\logs\last_error.txt` | Most recent failure detail |

The right-click menu → **Open Last Error Log** / **Open AI Logs Folder** provides quick access without navigating manually.

---

## Requirements

| Component | Minimum |
|---|---|
| Windows | 10 / 11 |
| Python | 3.9+ |
| AutoHotkey | v2 |
| Ollama | any recent version |
| RAM | 8 GB (16 GB recommended for 8B models) |

---

## Project Structure

```
ai-text-expand-local-llm/
├── ahk/
│   └── ai_text_expand.ahk      # Right-click menu, hotkeys, status tooltips
├── scripts/
│   ├── install.ps1             # Full setup (Python, AHK, Ollama, model)
│   ├── run.ps1                 # Start the AutoHotkey helper
│   ├── enable_startup.ps1      # Add to Windows startup
│   ├── health_check.ps1        # Verify / auto-start helper
│   ├── update.ps1              # Auto-download and install latest version
│   ├── package.ps1             # Build a distributable zip
│   └── test_expand.ps1         # Smoke-test the Python bridge
├── src/
│   └── ai_text_expand/
│       └── expand_text.py      # Python <-> Ollama bridge
├── dist/                       # Release zips (one per version)
├── config.example.json         # Runtime configuration
├── pyproject.toml              # Version and project metadata
└── requirements.txt            # Python dependencies
```

---

## Building a Release

```powershell
.\scripts\package.ps1
```

Creates `dist\AITextExpandLocalLLM-vX.Y.Z.zip`. The version number is read from `pyproject.toml`.

> **Developer workflow:** A git pre-commit hook automatically bumps the patch version and rebuilds the dist zip whenever source files are committed. Just `git commit` and `git push` as normal.