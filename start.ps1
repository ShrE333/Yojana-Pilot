<#
Scheme Saathi launcher. Run from anywhere:

    .\start.ps1                 public site at http://localhost:8000; citizen workspace at /app
    .\start.ps1 -Cli            terminal chat instead of the web page
    .\start.ps1 -NoWeb          terminal chat using only saved schemes (no search credits used)
    .\start.ps1 -Crawl          crawl the Maharashtra department / Zilla Parishad sites first (no search credits)
    .\start.ps1 -Update         scrape more schemes first, then chat
    .\start.ps1 -Update -State karnataka -Searches 10
    .\start.ps1 -Stats          show what is in the scheme catalog
    .\start.ps1 -Test           run the offline test suite
    .\start.ps1 -Debug          chat and print the understood profile after each turn

If Windows blocks scripts, double-click start.bat instead, or run:
    powershell -ExecutionPolicy Bypass -File .\start.ps1
#>
param(
    [switch]$Cli,
    [switch]$Crawl,
    [string]$District = "",
    [switch]$NoWeb,
    [switch]$Update,
    [string]$State = "maharashtra",
    [int]$Searches = 10,
    [switch]$Stats,
    [switch]$Test,
    [switch]$Debug
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# Devanagari (Hindi / Marathi) in the console
chcp 65001 > $null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

function Say($text, $color = "Cyan") { Write-Host $text -ForegroundColor $color }

# 1. Use the project's explicit environment, never an unrelated global Python.
$projectPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    Say 'Create the project environment first: py -3.13 -m venv .venv; then install requirements.txt. See README.md.' 'Red'
    exit 1
}
& $projectPython --version
if ($LASTEXITCODE -ne 0) {
    Say 'The project environment is broken. Recreate it with a standard Python installation; see README.md.' 'Red'
    exit 1
}
# 2. .env with keys
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Say "Created .env from .env.example. Configure a language service when you want live AI conversation. The public site and Rescue checks do not need model keys." "Yellow"
}

# 3. Packages (install only if something is missing)
& $projectPython -c "import yaml, rapidfuzz, dotenv, jsonschema, httpx, bs4, lxml, pypdf, openai, anthropic, google.genai, fastapi, uvicorn" 2>$null
if ($LASTEXITCODE -ne 0) {
    Say "Installing required packages (first run only)..."
    & $projectPython -m pip install -r requirements.lock.txt
    if ($LASTEXITCODE -ne 0) { Say "Package install failed. See the messages above." "Red"; exit 1 }
}

# 4. What to run
if ($Test) {
    Say "Running tests..."
    & $projectPython -m pytest -q
    exit $LASTEXITCODE
}
if ($Stats) {
    & $projectPython -m ingest --stats
    exit $LASTEXITCODE
}
if ($Update) {
    Say "Scraping official websites for $State + central schemes (up to $Searches searches)..."
    & $projectPython -m ingest --state $State --max-searches $Searches --max-pages ($Searches * 3)
    if ($LASTEXITCODE -ne 0) { Say "Scraping stopped with an error; starting chat with saved schemes." "Yellow" }
}

if ($Crawl) {
    $crawlArgs = @("-m", "ingest", "--source-map", "mh", "--max-sites", "45", "--pages-per-site", "12")
    if ($District -ne "") { $crawlArgs += @("--district", $District) }
    Say "Crawling Maharashtra department and Zilla Parishad sites (no search credits; LLM reads scheme pages)..."
    & $projectPython @crawlArgs
}

if (-not ($Cli -or $NoWeb -or $Debug)) {
    & $projectPython -m ingest --stats
    Say "Starting the web page at http://localhost:8000 (Ctrl+C to stop)" "Green"
    & $projectPython -m web
    exit $LASTEXITCODE
}

$chatArgs = @("-m", "saathi")
if ($NoWeb) { $chatArgs += "--no-web" }
if ($Debug) { $chatArgs += "--debug" }
& $projectPython -m ingest --stats
Say "Starting Scheme Saathi. Type 'quit' to exit." "Green"
& $projectPython @chatArgs
exit $LASTEXITCODE
