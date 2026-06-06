; ===========================================================================
; mode_prefix.ahk
; Detect grammar-fix mode from optional prefix keywords (prompt:, /prompt, etc.).
; ===========================================================================

_ModePrefixEntries() {
    return [
        { mode: "prompt",    kw: "(?:prompts|prompt)" },
        { mode: "summarize", kw: "(?:summarizes|summarize)" },
        { mode: "explain",   kw: "(?:explains|explain)" },
        { mode: "tone",      kw: "tone" },
    ]
}

; One line: optional list markers, optional slash, keyword, separator, optional inline body.
; Dash separator must precede generic \s+ so "prompt - text" does not leave a leading hyphen in the body.
_ModePrefixLinePattern(kw) {
    return "i)^\s*[>\-\*]*\s*/?" kw "(\s*:\s*|\s*-\s+|$|\s+)(.*)$"
}

; Whole blob: keyword and body on the same line (after separator).
_ModePrefixInlinePattern(kw) {
    return "i)^\s*[>\-\*]*\s*/?" kw "(\s*:\s*|\s*-\s+|\s+)(.+)$"
}

_ParseModeFromLines(lines) {
    firstIdx := 0
    for i, line in lines {
        if (Trim(line, "`t ") != "") {
            firstIdx := i
            break
        }
    }
    if !firstIdx
        return { mode: "grammar", text: "" }

    firstLine := Trim(lines[firstIdx], "`t ")
    for entry in _ModePrefixEntries() {
        if RegExMatch(firstLine, _ModePrefixLinePattern(entry.kw), &m) {
            parts := []
            inline := Trim(m[2], "`t ")
            if (inline != "")
                parts.Push(inline)
            Loop lines.Length - firstIdx {
                idx := firstIdx + A_Index
                line := Trim(lines[idx], "`t ")
                if (line != "")
                    parts.Push(line)
            }
            body := ""
            for i, part in parts
                body .= (i = 1 ? "" : "`n") part
            return { mode: entry.mode, text: Trim(body, "`r`n`t ") }
        }
    }
    return { mode: "grammar", text: "" }
}

_ParseModeInline(text) {
    for entry in _ModePrefixEntries() {
        if RegExMatch(text, _ModePrefixInlinePattern(entry.kw), &m)
            return { mode: entry.mode, text: Trim(m[2], "`r`n`t ") }
    }
    return { mode: "grammar", text: text }
}

ParseModeAndText(selected) {
    text := Trim(selected, "`r`n`t ")
    if (text != "" && SubStr(text, 1, 1) = Chr(0xFEFF))
        text := SubStr(text, 2)
    if (text = "")
        return { mode: "grammar", text: "" }

    fromLines := _ParseModeFromLines(StrSplit(text, "`n", "`r"))
    if (fromLines.mode != "grammar")
        return fromLines
    return _ParseModeInline(text)
}
