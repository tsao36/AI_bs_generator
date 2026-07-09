#Requires AutoHotkey v2.0
#SingleInstance Force

PYTHON_EXE := "python"
SCRIPT_PATH := A_ScriptDir "\\expand_text.py"

global g_SelectedText := ""

global g_Menu := Menu()
g_Menu.Add("Expand with AI", ExpandWithAI)
g_Menu.Add("Use Native Context Menu", ShowNativeContextMenu)
g_Menu.Add("Cancel", (*) => 0)

RButton::
{
    selected := GetSelectedText()
    if (selected = "") {
        Send "{RButton}"
        return
    }

    g_SelectedText := selected
    MouseGetPos &mx, &my
    g_Menu.Show(mx, my)
}

ExpandWithAI(*)
{
    if (g_SelectedText = "") {
        MsgBox "No selected text found.", "AI Expand", "Icon!"
        return
    }

    inFile := A_Temp "\\ai_expand_in_" A_TickCount ".txt"
    outFile := A_Temp "\\ai_expand_out_" A_TickCount ".txt"

    try {
        FileDelete inFile
    }
    try {
        FileDelete outFile
    }

    FileAppend g_SelectedText, inFile, "UTF-8"

    cmd := Format('"{}" "{}" --input "{}" --output "{}"', PYTHON_EXE, SCRIPT_PATH, inFile, outFile)
    exitCode := RunWait(cmd, , "Hide")

    if (exitCode != 0) {
        MsgBox "AI generation failed. Check Ollama service/model and Python dependencies.", "AI Expand", "Iconx"
        return
    }

    if !FileExist(outFile) {
        MsgBox "No output generated.", "AI Expand", "Iconx"
        return
    }

    expanded := Trim(FileRead(outFile, "UTF-8"))
    if (expanded = "") {
        MsgBox "Generated text is empty.", "AI Expand", "Icon!"
        return
    }

    PasteOverSelection(expanded)
}

ShowNativeContextMenu(*)
{
    Send "+{F10}"
}

GetSelectedText()
{
    clipSaved := ClipboardAll()
    A_Clipboard := ""

    Send "^c"
    if !ClipWait(0.4) {
        A_Clipboard := clipSaved
        return ""
    }

    txt := A_Clipboard
    A_Clipboard := clipSaved
    return txt
}

PasteOverSelection(text)
{
    clipSaved := ClipboardAll()
    A_Clipboard := text
    ClipWait(0.4)
    Send "^v"
    Sleep 50
    A_Clipboard := clipSaved
}
