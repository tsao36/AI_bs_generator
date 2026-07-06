#Requires AutoHotkey v2.0
#SingleInstance Force

ProjectRoot := RegExReplace(A_ScriptDir, "\\ahk$", "")
PythonExe := ProjectRoot "\\.venv\\Scripts\\python.exe"
if !FileExist(PythonExe) {
    PythonExe := "python"
}

ScriptPath := ProjectRoot "\\src\\ai_text_expand\\expand_text.py"
ConfigPath := ProjectRoot "\\config.example.json"

global SelectedText := ""
global ContextMenu := Menu()
ContextMenu.Add("Expand with Local AI - 2 sentences", ExpandTwoSentences)
ContextMenu.Add("Expand with Local AI - 5 sentences", ExpandFiveSentences)
ContextMenu.Add("Expand with Local AI - paragraph", ExpandParagraph)
ContextMenu.Add("Use Native Context Menu", ShowNativeContextMenu)
ContextMenu.Add("Cancel", (*) => 0)

#HotIf IsSupportedBrowser()
RButton::
{
    global SelectedText

    selected := CopySelectedText()
    if (selected = "") {
        Send "{RButton}"
        return
    }

    SelectedText := selected
    MouseGetPos &mouseX, &mouseY
    ContextMenu.Show(mouseX, mouseY)
}
#HotIf

ExpandTwoSentences(*)
{
    ExpandWithLocalAI("two_sentences", "2 sentences")
}

ExpandFiveSentences(*)
{
    ExpandWithLocalAI("five_sentences", "5 sentences")
}

ExpandParagraph(*)
{
    ExpandWithLocalAI("paragraph", "paragraph")
}

ExpandWithLocalAI(lengthMode, lengthLabel)
{
    global SelectedText

    if (SelectedText = "") {
        MsgBox "No selected text found.", "AI Text Expand", "Icon!"
        return
    }

    inputFile := A_Temp "\\ai_text_expand_input_" A_TickCount ".txt"
    outputFile := A_Temp "\\ai_text_expand_output_" A_TickCount ".txt"

    TryDelete(inputFile)
    TryDelete(outputFile)
    FileAppend SelectedText, inputFile, "UTF-8"

    modelName := GetConfiguredModel()
    ShowStatus(Format("Generating {} with {}...`nDo not move away to another page until completion.", lengthLabel, modelName))
    command := Format('"{}" "{}" --input "{}" --output "{}" --config "{}" --length "{}"', PythonExe, ScriptPath, inputFile, outputFile, ConfigPath, lengthMode)
    exitCode := RunWait(command, , "Hide")
    HideStatus()

    if (exitCode != 0) {
        ShowStatus("AI expansion failed", 1800)
        MsgBox "Local AI expansion failed. Check Ollama, the selected model, and Python dependencies.", "AI Text Expand", "Iconx"
        return
    }

    if !FileExist(outputFile) {
        ShowStatus("No AI output created", 1800)
        MsgBox "Local AI did not create an output file.", "AI Text Expand", "Iconx"
        return
    }

    expanded := Trim(FileRead(outputFile, "UTF-8"))
    if (expanded = "") {
        ShowStatus("AI returned empty text", 1800)
        MsgBox "Local AI returned empty text.", "AI Text Expand", "Icon!"
        return
    }

    PasteOverSelection(expanded)
    ShowStatus(Format("Inserted {} using {}", lengthLabel, modelName), 1600)
}

ShowNativeContextMenu(*)
{
    Send "+{F10}"
}

CopySelectedText()
{
    savedClipboard := ClipboardAll()
    A_Clipboard := ""

    Send "^c"
    if !ClipWait(0.5) {
        A_Clipboard := savedClipboard
        return ""
    }

    selected := A_Clipboard
    A_Clipboard := savedClipboard
    return selected
}

PasteOverSelection(text)
{
    savedClipboard := ClipboardAll()
    A_Clipboard := text
    ClipWait(0.5)
    Send "^v"
    Sleep 80
    A_Clipboard := savedClipboard
}

TryDelete(path)
{
    try {
        if FileExist(path) {
            FileDelete path
        }
    }
}

IsSupportedBrowser()
{
    browserExecutables := Map(
        "chrome.exe", true,
        "msedge.exe", true,
        "firefox.exe", true,
        "brave.exe", true,
        "opera.exe", true,
        "vivaldi.exe", true
    )

    try {
        activeProcess := WinGetProcessName("A")
    } catch {
        return false
    }
    return browserExecutables.Has(StrLower(activeProcess))
}

ShowStatus(message, timeoutMs := 0)
{
    ToolTip message
    if (timeoutMs > 0) {
        SetTimer HideStatus, -timeoutMs
    }
}

HideStatus()
{
    ToolTip
}

GetConfiguredModel()
{
    if !FileExist(ConfigPath) {
        return "local LLM"
    }

    configText := FileRead(ConfigPath, "UTF-8")
    if RegExMatch(configText, '"LOCAL_LLM_MODEL"\s*:\s*"([^"\r\n]+)"', &match) {
        return match[1]
    }
    return "local LLM"
}
