# Russian TTS test-set generator (SAPI Irina).
# Run in an UNCONFINED PowerShell (SAPI fails from python subprocesses under the sandbox):
#   powershell -NoProfile -ExecutionPolicy Bypass -File tests\fixtures\make_tts.ps1
# The script itself stays ASCII-only: PowerShell 5.1 reads BOM-less UTF-8 as ANSI,
# so all Cyrillic text is loaded from phrases_ru.json with -Encoding UTF8.
param(
    [string]$OutDir = (Join-Path $PSScriptRoot "ru_tts")
)

$ErrorActionPreference = "Stop"
$phrasesPath = Join-Path $PSScriptRoot "phrases_ru.json"
$phrases = Get-Content -Raw -Encoding UTF8 $phrasesPath | ConvertFrom-Json
New-Item -ItemType Directory -Force $OutDir | Out-Null

$v = New-Object -ComObject SAPI.SpVoice
$voices = @($v.GetVoices())
$ru = $voices | Where-Object { $_.GetDescription() -like "*Irina*" } | Select-Object -First 1
if (-not $ru) { throw "SAPI Russian voice (Irina) not found" }
$v.Voice = $ru
$v.Rate = 0

$manifest = @()
foreach ($phrase in $phrases) {
    $wav = Join-Path $OutDir ("{0}.wav" -f $phrase.id)
    if (Test-Path $wav) { Remove-Item $wav -Force }
    $fs = New-Object -ComObject SAPI.SpFileStream
    $fs.Open($wav, 3, $false)
    $v.AudioOutputStream = $fs
    $v.Speak($phrase.text)
    $fs.Close()
    $size = (Get-Item $wav).Length
    $manifest += [pscustomobject]@{
        id = $phrase.id
        category = $phrase.category
        text = $phrase.text
        wav = ("{0}.wav" -f $phrase.id)
        bytes = $size
    }
    Write-Host ("[{0}] {1} -> {2} bytes" -f $phrase.id, $phrase.category, $size)
}
$manifestPath = Join-Path $OutDir "manifest.json"
$manifest | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 $manifestPath
Write-Host ("Done: {0} phrases, manifest {1}" -f $manifest.Count, $manifestPath)


