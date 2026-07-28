#Requires AutoHotkey v2.0
#SingleInstance Force

ProjectRoot := RegExReplace(A_ScriptDir, "\\ahk$", "")
InstalledRoot := EnvGet("LOCALAPPDATA") "\\AITextExpandLocalLLM"
if !FileExist(ProjectRoot "\\.venv\\Scripts\\python.exe") && FileExist(InstalledRoot "\\.venv\\Scripts\\python.exe") {
    ProjectRoot := InstalledRoot
}

PythonExe := ProjectRoot "\\.venv\\Scripts\\python.exe"
PythonArgs := ""
if !FileExist(PythonExe) {
    pyLauncher := GetCommandPath("py")
    if (pyLauncher != "") {
        PythonExe := pyLauncher
        PythonArgs := "-3"
    } else {
        PythonExe := "python"
    }
}

ScriptPath := ProjectRoot "\\src\\ai_text_expand\\expand_text.py"
ConfigPath := ProjectRoot "\\config.example.json"
PyprojectPath := ProjectRoot "\\pyproject.toml"
LogsDir := EnvGet("LOCALAPPDATA") "\\AITextExpandLocalLLM\\logs"
MainLogPath := LogsDir "\\ai_text_expand.log"
LastErrorPath := LogsDir "\\last_error.txt"
GitHubDistPageUrl := "https://github.com/tsao36/AI_bs_generator/tree/main/ai-text-expand-local-llm/dist"

global SelectedText := ""
global SelectionWindowId := 0
global NativeMenuPassThroughMs := 3000
global LastNativeRightClickTick := 0
global LastNativeRightClickText := ""
global LastNativeRightClickWindowId := 0
global ProgressTick := 0
global ProgressLabel := ""
global ProgressModel := ""
global ShrinkWordThreshold := 30
global ShrinkCharThreshold := 120

GetCommandPath(commandName)
{
    checkCmd := Format('{} /c "where {} >nul 2>&1"', A_ComSpec, commandName)
    if (RunWait(checkCmd, , "Hide") = 0) {
        return commandName
    }
    return ""
}

#HotIf IsSupportedBrowser()
RButton::
{
    global SelectedText, SelectionWindowId, NativeMenuPassThroughMs
    global LastNativeRightClickTick, LastNativeRightClickText, LastNativeRightClickWindowId

    MouseGetPos &mouseX, &mouseY, &currentWindowId
    isAiMenuRequest := LastNativeRightClickTick > 0
        && (A_TickCount - LastNativeRightClickTick <= NativeMenuPassThroughMs)
        && (LastNativeRightClickText != "")

    if isAiMenuRequest {
        SelectedText := LastNativeRightClickText
        SelectionWindowId := LastNativeRightClickWindowId
        LastNativeRightClickTick := 0
        LastNativeRightClickText := ""
        LastNativeRightClickWindowId := 0
        Send "{Esc}"
        Sleep 50
        ShowAiContextMenu(mouseX, mouseY, SelectedText)
        return
    }

    selected := CopySelectedText()
    if (selected = "") {
        LastNativeRightClickTick := 0
        LastNativeRightClickText := ""
        LastNativeRightClickWindowId := 0
        Send "{RButton}"
        return
    }

    SelectionWindowId := currentWindowId
    LastNativeRightClickTick := A_TickCount
    LastNativeRightClickText := selected
    LastNativeRightClickWindowId := SelectionWindowId
    SelectedText := selected
    Send "{RButton}"
}

!RButton::
{
    OpenAiMenuFromCurrentSelection(true)
}

^!a::
{
    OpenAiMenuFromCurrentSelection(false)
}
#HotIf

OpenAiMenuFromCurrentSelection(fallbackToNativeWhenEmpty := false)
{
    global SelectedText, SelectionWindowId

    MouseGetPos &mouseX, &mouseY, &currentWindowId
    selected := CopySelectedText()
    if (selected = "") {
        if fallbackToNativeWhenEmpty {
            Send "{RButton}"
        } else {
            ShowStatusNearMouse("Select text first to open AI menu", 1800)
            SoundBeep 700, 60
        }
        return false
    }

    SelectedText := selected
    SelectionWindowId := currentWindowId
    ShowAiContextMenu(mouseX, mouseY, selected)
    return true
}

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

ShrinkOneSentence(*)
{
    ExpandWithLocalAI("one_sentence", "1 sentence")
}

ShrinkThreeSentences(*)
{
    ExpandWithLocalAI("three_sentences", "3 sentences")
}

ShowAiContextMenu(mouseX, mouseY, selectedText)
{
    aiMenu := Menu()
    aiMenu.Add("Expand with Local AI - 2 sentences", ExpandTwoSentences)
    aiMenu.Add("Expand with Local AI - 5 sentences", ExpandFiveSentences)
    aiMenu.Add("Expand with Local AI - paragraph", ExpandParagraph)

    if IsLargeSelection(selectedText) {
        aiMenu.Add()
        aiMenu.Add("Shrink with Local AI - 1 sentence", ShrinkOneSentence)
        aiMenu.Add("Shrink with Local AI - 3 sentences", ShrinkThreeSentences)
    }

    aiMenu.Add()
    aiMenu.Add("Check GitHub for Latest Download", CheckGitHubForLatestDownload)
    aiMenu.Add("Open AI Logs Folder", OpenLogsFolder)
    aiMenu.Add("Open Last Error Log", OpenLastErrorLog)
    aiMenu.Add("Copy Last Error Path", CopyLastErrorPath)
    aiMenu.Add("Use Native Context Menu", ShowNativeContextMenu)
    aiMenu.Add("Cancel", (*) => 0)

    ; Re-read cursor position in logical coordinates right before showing,
    ; so the menu appears next to the pointer regardless of per-monitor DPI.
    pt := Buffer(8, 0)
    DllCall("GetCursorPos", "Ptr", pt)
    DllCall("PhysicalToLogicalPointForPerMonitorDPI", "Ptr", 0, "Ptr", pt)
    showX := NumGet(pt, 0, "Int")
    showY := NumGet(pt, 4, "Int")
    aiMenu.Show(showX, showY)
}

IsLargeSelection(text)
{
    global ShrinkWordThreshold, ShrinkCharThreshold

    normalized := Trim(text)
    if (normalized = "") {
        return false
    }

    charCount := StrLen(normalized)
    if (charCount >= ShrinkCharThreshold) {
        return true
    }

    wordCount := CountWords(normalized)
    estimatedTokenCount := Ceil(charCount / 4)

    ; Handle both space-separated languages and CJK-heavy text.
    return (wordCount >= ShrinkWordThreshold) || (estimatedTokenCount >= ShrinkWordThreshold)
}

CountWords(text)
{
    count := 0
    searchPos := 1

    while RegExMatch(text, "\\S+", &match, searchPos) {
        count += 1
        searchPos := match.Pos + match.Len
    }

    return count
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
    command := Format('{} /c ""{}" {} "{}" --input "{}" --output "{}" --config "{}" --length "{}" > "{}" 2>&1"', A_ComSpec, PythonExe, PythonArgs, ScriptPath, inputFile, outputFile, ConfigPath, lengthMode, errorFile)
    try {
        exitCode := RunWait(command, , "Hide")
    } catch as err {
        StopProgressIndicator()
        RecordFailure("Failed to launch local AI bridge", err.Message, errorFile)
        ShowStatus("AI launch failed", 1800)
        MsgBox "Local AI launch failed.`n`n" err.Message "`n`nDetails saved to:`n" LastErrorPath, "AI Text Expand", "Iconx"
        return
    }
    StopProgressIndicator()

    if (exitCode != 0) {
        detail := GetFailureDetails(errorFile, exitCode)
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

    A_Clipboard := expanded

    didAutoPaste := TryAutoPasteToOriginalWindow()

    TryDelete(errorFile)
    if didAutoPaste {
        ShowStatusNearMouse("Your words are expanded and pasted", 4500)
    } else {
        ShowStatusNearMouse("Your words are expanded and can be pasted", 4500)
    }
    SoundBeep 1200, 80
}

TryAutoPasteToOriginalWindow()
{
    global SelectionWindowId

    if (SelectionWindowId = 0) {
        return false
    }

    MouseGetPos ,, &currentMouseWindowId
    if (currentMouseWindowId != SelectionWindowId) {
        return false
    }

    activeWindowId := WinExist("A")
    if (activeWindowId != SelectionWindowId) {
        return false
    }

    Send "^v"
    return true
}

ShowNativeContextMenu(*)
{
    Send "+{F10}"
}

CheckGitHubForLatestDownload(*)
{
    currentVersion := GetCurrentAppVersion()
    if (currentVersion = "") {
        prompt := "Open the GitHub package page to check for the latest download?`n`nLook for the newest AITextExpandLocalLLM-v*.zip file."
    } else {
        prompt := "Current local package version: " currentVersion "`n`nOpen the GitHub package page to check for a newer download?"
    }

    if (MsgBox(prompt, "AI Text Expand", "YesNo Iconi") = "Yes") {
        Run GitHubDistPageUrl
    }
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
        "vivaldi.exe", true,
        "outlook.exe", true
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

ShowStatusNearMouse(message, timeoutMs := 0)
{
    MouseGetPos &mx, &my
    ToolTip message, mx + 16, my + 16
    TrayTip "AI Text Expand", message, 1
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

GetCurrentAppVersion()
{
    try {
        if !FileExist(PyprojectPath) {
            return ""
        }

        projectText := FileRead(PyprojectPath, "UTF-8")
        if RegExMatch(projectText, 'version\s*=\s*"([0-9]+(?:\.[0-9]+)*)"', &match) {
            return match[1]
        }
    } catch {
        return ""
    }

    return ""
}

PreflightCheck()
{
    issues := []

    if !FileExist(ScriptPath) {
        issues.Push("Bridge script not found: " ScriptPath)
    }

    if FileExist(ProjectRoot "\\.venv\\Scripts\\python.exe") {
        if !TestPythonExecutable(ProjectRoot "\\.venv\\Scripts\\python.exe", "") {
            issues.Push("Virtual environment Python exists but is not runnable.")
        }
    } else {
        if !TestPythonExecutable(PythonExe, PythonArgs) {
            issues.Push("Python was not found. Re-run 01_Setup_and_Start.exe.")
            if (ProjectRoot != InstalledRoot && FileExist(InstalledRoot "\\.venv\\Scripts\\python.exe")) {
                issues.Push("A valid installed environment exists in " InstalledRoot ", but this script is running from another folder. Use the installed copy.")
            }
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

TestPythonExecutable(pyExe, pyArgs := "")
{
    checkCmd := Format('{} /c ""{}" {} --version >nul 2>&1"', A_ComSpec, pyExe, pyArgs)
    return RunWait(checkCmd, , "Hide") = 0
}

ReadErrorFile(errorFile, exitCode := "")
{
    if FileExist(errorFile) {
        text := Trim(FileRead(errorFile, "UTF-8"))
        if (text != "") {
            return text
        }
    }

    detail := "Python exited"
    if (exitCode != "") {
        detail .= " with code " exitCode
    }
    detail .= " but did not write any error output.`n"
    detail .= "This usually means the local AI bridge failed before stderr redirection completed.`n"
    detail .= "Re-run 01_Setup_and_Start.exe to repair the install, then try again."
    return detail
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

GetFailureDetails(errorFile, exitCode := "")
{
    rawError := ReadErrorFile(errorFile, exitCode)
    detail := ClassifyFailure(rawError)
    RecordFailure("Local AI expansion failed", rawError, errorFile)

    return detail "`n`nDetails saved to:`n" LastErrorPath
}
