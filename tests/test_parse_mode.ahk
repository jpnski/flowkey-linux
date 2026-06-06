#Requires AutoHotkey v2.0
; Regression tests for prefix-driven mode detection (prompt / prompt: / multiline).

#Include "..\scripts\lib\mode_prefix.ahk"

cases := [
    ; mode, input, expectedMode, expectedTextStartsWith
    ["prompt inline", "prompt Develop a app for java", "prompt", "Develop a app"],
    ["prompt colon", "prompt: Develop a app for java", "prompt", "Develop a app"],
    ["Prompt case", "Prompt: Develop a app for java", "prompt", "Develop a app"],
    ["slash prompt", "/prompt Develop a app for java", "prompt", "Develop a app"],
    ["prompts plural", "prompts Develop a app for java", "prompt", "Develop a app"],
    ["prompt dash", "prompt - Develop a app for java", "prompt", "Develop a app"],
    ["prompt own line", "prompt`n`nDevelop a app for java that play ducks", "prompt", "Develop a app"],
    ["prompt colon own line", "prompt:`nDevelop ducks game", "prompt", "Develop ducks"],
    ["prompt leading blank multiline", "`n`nprompt:`nDevelop ducks game`nUse Java", "prompt", "Develop ducks"],
    ["grammar plain", "Develop a app for java", "grammar", "Develop a app"],
    ["no false prompts", "I need prompts for my app", "grammar", "I need prompts"],
]

failures := 0
total := cases.Length
for c in cases {
    got := ParseModeAndText(c[2])
    modeOk := (got.mode = c[3])
    textOk := modeOk && (c[3] = "grammar" || InStr(got.text, c[4]) = 1)
    if (!modeOk || !textOk) {
        failures += 1
        FileAppend(Format("FAIL [{}]: want mode={} text~`"{}`" got mode={} text=`"{}`"`n",
            c[1], c[3], c[4], got.mode, SubStr(got.text, 1, 60)), "**")
    }
}

if (failures > 0) {
    FileAppend(Format("test_parse_mode: {}/{} FAILED`n", failures, total), "**")
    ExitApp(1)
}
; Success: exit 0 only — FileAppend("*") needs a console and errors when run from Explorer/IDE.
ExitApp(0)
