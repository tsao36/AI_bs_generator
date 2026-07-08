#Requires AutoHotkey v2.0
#SingleInstance Force

ProjectRoot := RegExReplace(A_ScriptDir, "\\ahk$", "")
PythonExe := ProjectRoot "\\.venv\\Scripts\\python.exe"
if !FileExist(PythonExe) {
    PythonExe := "python"
}

ScriptPath := ProjectRoot "\\src\\ai_text_expand\\expand_text.py"
ConfigPath := ProjectRoot "\\config.example.json"
LogsDir := EnvGet("LOCALAPPDATA") "\\AITextExpandLocalLLM\\logs"
MainLogPath := LogsDir "\\ai_text_expand.log"
LastErrorPath := LogsDir "\\last_error.txt"

global SelectedText := ""
global ProgressTick := 0
global ProgressLabel := ""
global ProgressModel := ""
global ContextMenu := Menu()
ContextMenu.Add("Expand with Local AI - 2 sentences", ExpandTwoSentences)
ContextMenu.Add("Expand with Local AI - 5 sentences", ExpandFiveSentences)
ContextMenu.Add("Expand with Local AI - paragraph", ExpandParagraph)
ContextMenu.Add()
ContextMenu.Add("Open AI Logs Folder", OpenLogsFolder)
ContextMenu.Add("Open Last Error Log", OpenLastErrorLog)
ContextMenu.Add("Copy Last Error Path", CopyLastErrorPath)
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

    precheckError := PreflightCheck()
    if (precheckError != "") {
        RecordFailure("Preflight check failed", precheckError, "")
        ShowStatus("AI precheck failed", 1800)
        MsgBox "Local AI precheck failed.`n`n" precheckError, "AI Text Expand", "Iconx"
        return
    }

    inputFile := A_Temp "\\ai_text_expand_input_" A_TickCount ".txt"
    outputFile := A_Temp "\\ai_text_expand_output_" A_TickCount ".txt"
    errorFile := A_Temp "\ai_text_expand_error_" A_TickCount ".txt"

    TryDelete(inputFile)
    TryDelete(outputFile)
    TryDelete(errorFile)
    FileAppend SelectedText, inputFile, "UTF-8"

    modelName := GetConfiguredModel()
    StartProgressIndicator(lengthLabel, modelName)
    command := Format('"{}" "{}" --input "{}" --output "{}" --config "{}" --length "{}" > "{}" 2>&1', PythonExe, ScriptPath, inputFile, outputFile, ConfigPath, lengthMode, errorFile)
    exitCode := RunWait(command, , "Hide")
    StopProgressIndicator()

    if (exitCode != 0) {
        detail := GetFailureDetails(errorFile)
        ShowStatus("AI expansion failed", 1800)
        MsgBox "Local AI expansion failed.`n`n" detail, "AI Text Expand", "Iconx"
        return
    }

    if !FileExist(outputFile) {
        RecordFailure("No AI output created", "The output file was not created.", errorFile)
        ShowStatus("No AI output created", 1800)
        MsgBox "Local AI did not create an output file.", "AI Text Expand", "Iconx"
        return
    }

    expanded := Trim(FileRead(outputFile, "UTF-8"))
    if (expanded = "") {
        RecordFailure("AI returned empty text", "The output file exists but is empty.", errorFile)
        ShowStatus("AI returned empty text", 1800)
        MsgBox "Local AI returned empty text.", "AI Text Expand", "Icon!"
        return
    }

    PasteOverSelection(expanded)
    TryDelete(errorFile)
    ShowStatus(Format("Inserted {} using {}", lengthLabel, modelName), 1600)
}

ShowNativeContextMenu(*)
{
    Send "+{F10}"
}

OpenLogsFolder(*)
{
    EnsureLogsDir()
    Run LogsDir
}

OpenLastErrorLog(*)
{
    EnsureLogsDir()
    if FileExist(LastErrorPath) {
        Run LastErrorPath
        return
    }

    MsgBox "No error log was found yet.`n`nWhen expansion fails, details will be saved to:`n" LastErrorPath, "AI Text Expand", "Icon!"
}

CopyLastErrorPath(*)
{
    EnsureLogsDir()
    A_Clipboard := LastErrorPath
    ShowStatus("Copied last error path", 1400)
    MsgBox "Copied to clipboard:`n" LastErrorPath, "AI Text Expand", "Iconi"
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

StartProgressIndicator(lengthLabel, modelName)
{
    global ProgressTick, ProgressLabel, ProgressModel
    ProgressTick := 0
    ProgressLabel := lengthLabel
    ProgressModel := modelName
    UpdateProgressIndicator()
    SetTimer UpdateProgressIndicator, 150
}

StopProgressIndicator()
{
    SetTimer UpdateProgressIndicator, 0
    HideStatus()
}

UpdateProgressIndicator()
{
    global ProgressTick, ProgressLabel, ProgressModel

    width := 18
    pos := Mod(ProgressTick, width)
    bar := ""
    Loop width {
        idx := A_Index - 1
        if (idx = pos) {
            bar .= "■"
        } else {
            bar .= "·"
        }
    }

    spinnerChars := ["|", "/", "-", "\\"]
    spinner := spinnerChars[Mod(ProgressTick, spinnerChars.Length) + 1]

    ToolTip Format("Running local LLM ({}) with {} {} `n[{}]", ProgressLabel, ProgressModel, spinner, bar)
    ProgressTick += 1
}

GetConfiguredModel()
{
    try {
        if !FileExist(ConfigPath) {
            return "local LLM"
        }

        configText := FileRead(ConfigPath, "UTF-8")
        if RegExMatch(configText, '"LOCAL_LLM_MODEL"\s*:\s*"([^"\r\n]+)"', &match) {
            return match[1]
        }
    } catch {
        return "local LLM"
    }

    return "local LLM"
}

PreflightCheck()
{
    issues := []

    if !FileExist(ScriptPath) {
        issues.Push("Bridge script not found: " ScriptPath)
    }

    if FileExist(ProjectRoot "\\.venv\\Scripts\\python.exe") {
        if !TestPythonExecutable(ProjectRoot "\\.venv\\Scripts\\python.exe") {
            issues.Push("Virtual environment Python exists but is not runnable.")
        }
    } else {
        if !TestPythonExecutable("python") {
            issues.Push("Python was not found. Re-run Install.exe.")
        }
    }

    if FileExist(ConfigPath) {
        try {
            FileRead(ConfigPath, "UTF-8")
        } catch {
            issues.Push("Config file exists but cannot be read: " ConfigPath)
        }
    }

    if (issues.Length = 0) {
        return ""
    }

    detail := ""
    for issue in issues {
        detail .= "- " issue "`n"
    }
    return RTrim(detail, "`n")
}

TestPythonExecutable(pyExe)
{
    checkCmd := Format('{} /c ""{}" --version >nul 2>&1"', A_ComSpec, pyExe)
    return RunWait(checkCmd, , "Hide") = 0
}

ReadErrorFile(errorFile)
{
    if FileExist(errorFile) {
        text := Trim(FileRead(errorFile, "UTF-8"))
        if (text != "") {
            return text
        }
    }
    return "No error details were captured from Python output."
}

ClassifyFailure(rawError)
{
    lower := StrLower(rawError)
    modelName := GetConfiguredModel()

    if InStr(lower, "could not connect to ollama") || InStr(lower, "connection refused") || InStr(lower, "timed out") {
        return "Could not connect to Ollama. Start Ollama and verify OLLAMA_BASE_URL in config.example.json.`n`n" TruncateForPopup(rawError)
    }

    if InStr(lower, "model") && InStr(lower, "not found") {
        return "Configured model is not installed: " modelName "`nRun Install.exe again or pull the model with: ollama pull " modelName "`n`n" TruncateForPopup(rawError)
    }

    if InStr(lower, "modulenotfounderror") || InStr(lower, "no module named") {
        return "Python dependencies are missing or broken. Re-run Install.exe to repair .venv.`n`n" TruncateForPopup(rawError)
    }

    if InStr(lower, "input file not found") || InStr(lower, "selected text is empty") {
        return "Input text was not captured correctly. Try selecting text again and rerun.`n`n" TruncateForPopup(rawError)
    }

    return TruncateForPopup(rawError)
}

TruncateForPopup(text)
{
    if (StrLen(text) > 1200) {
        return SubStr(text, 1, 1200) "..."
    }
    return text
}

EnsureLogsDir()
{
    if !DirExist(LogsDir) {
        DirCreate LogsDir
    }
}

RecordFailure(title, rawError, tempErrorFile)
{
    EnsureLogsDir()
    timestamp := FormatTime(A_Now, "yyyy-MM-dd HH:mm:ss")
    modelName := GetConfiguredModel()
    context := "[" timestamp "] " title "`n"
    context .= "Model: " modelName "`n"
    context .= "Python: " PythonExe "`n"
    context .= "Script: " ScriptPath "`n"
    context .= "Config: " ConfigPath "`n"
    if (tempErrorFile != "") {
        context .= "TempErrorFile: " tempErrorFile "`n"
    }
    context .= "Details:`n" rawError "`n`n"

    FileAppend context, MainLogPath, "UTF-8"
    TryDelete(LastErrorPath)
    FileAppend context, LastErrorPath, "UTF-8"
}

GetFailureDetails(errorFile)
{
    rawError := ReadErrorFile(errorFile)
    detail := ClassifyFailure(rawError)
    RecordFailure("Local AI expansion failed", rawError, errorFile)

    return detail "`n`nDetails saved to:`n" LastErrorPath
}
