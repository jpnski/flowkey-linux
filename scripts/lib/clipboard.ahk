; ===========================================================================
; clipboard.ahk — shared selection/clipboard capture for hotkey actions.
; ===========================================================================

; Returns true when text was captured. Sets capturedText and captureSource
; ("selection" or "clipboard"). Restores the user's clipboard on all paths.
CaptureTextFromSelectionOrClipboard(&capturedText, &captureSource) {
    priorClip := ""
    try priorClip := A_Clipboard
    catch
        priorClip := ""

    clipSaved := ""
    try {
        clipSaved := ClipboardAll()
        A_Clipboard := ""
    } catch {
        capturedText := ""
        captureSource := "clipboard_busy"
        return false
    }

    Send("^c")
    selectedOk := ClipWait(1)
    fromSelection := ""
    if (selectedOk) {
        try
            fromSelection := A_Clipboard
        catch
            fromSelection := ""
    }
    try A_Clipboard := clipSaved

    capturedText := ""
    captureSource := ""
    if (fromSelection != "") {
        capturedText := fromSelection
        captureSource := "selection"
    } else if (priorClip != "") {
        capturedText := priorClip
        captureSource := "clipboard"
    }
    return (capturedText != "")
}
