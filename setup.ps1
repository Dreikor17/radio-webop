<#
  Radio WebOp - install / update (Windows)

  One script, safe to re-run:
    1. Finds a suitable Python (3.9+); installs Python 3.12 via winget if none is found.
    2. Updates the source from GitHub (git pull) when this is a clean git checkout.
    3. Creates a local virtual environment (.venv) and installs/upgrades dependencies.
    4. Verifies the app imports, then makes a "Radio WebOp" desktop shortcut.

  Normally launched by install.bat (double-click). Options:
    -NoShortcut   don't create/refresh the desktop shortcut
    -NoPull       don't 'git pull' even if this is a git checkout
#>
[CmdletBinding()]
param(
    [switch]$NoShortcut,
    [switch]$NoPull
)

# NOTE: deliberately NOT 'Stop' - native tools (py/pip/git) write to stderr on success,
# which PowerShell 5.1 would turn into terminating errors. We check $LASTEXITCODE instead.
$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Step($m) { Write-Host ""; Write-Host "==> $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "    [OK] $m"  -ForegroundColor Green }
function Warn($m) { Write-Host "    [!]  $m"  -ForegroundColor Yellow }
function Fail($m) { Write-Host ""; Write-Host "    [X]  $m" -ForegroundColor Red; Write-Host ""; exit 1 }

Write-Host ""
Write-Host "  Radio WebOp - install / update" -ForegroundColor White
Write-Host "  ------------------------------" -ForegroundColor DarkGray

# ---- 1. Python -------------------------------------------------------------
function Get-PyMajMin($exe) {
    $v = & $exe -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>$null
    if ($LASTEXITCODE -eq 0 -and $v) { return ("$v").Trim() }
    return $null
}

function Find-Python {
    $cands = New-Object System.Collections.Generic.List[string]
    # the 'py' launcher lists every installed Python + path in one call (no per-version error spam)
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $lines = & py -0p 2>$null
        foreach ($ln in $lines) {
            $m = [regex]::Match([string]$ln, '([A-Za-z]:\\[^\r\n]*?python\.exe)')
            if ($m.Success) { $cands.Add($m.Groups[1].Value) }
        }
    }
    # python / python3 on PATH
    foreach ($n in 'python', 'python3') {
        $c = Get-Command $n -ErrorAction SilentlyContinue
        if ($c -and $c.Source) { $cands.Add($c.Source) }
    }
    # common install locations (finds a fresh winget/python.org install before PATH refreshes)
    $dirs = @("$env:LOCALAPPDATA\Programs\Python", "$env:ProgramFiles\Python", "${env:ProgramFiles(x86)}\Python")
    foreach ($d in $dirs) {
        if (Test-Path $d) {
            Get-ChildItem $d -Directory -Filter 'Python3*' -ErrorAction SilentlyContinue | ForEach-Object {
                $p = Join-Path $_.FullName 'python.exe'
                if (Test-Path $p) { $cands.Add($p) }
            }
        }
    }
    # rank: 3.9+ only, skip Microsoft Store stubs, prefer versions with reliable prebuilt wheels
    $pref = @{ '12' = 0; '11' = 1; '13' = 2; '10' = 3; '9' = 4 }
    $seen = @{}; $ranked = @()
    foreach ($exe in $cands) {
        if (-not $exe) { continue }
        $key = $exe.ToLower()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        if ($key -like '*windowsapps*') { continue }   # Store alias - opens the Store, not a real Python
        if (-not (Test-Path $exe)) { continue }
        $mm = Get-PyMajMin $exe
        if (-not $mm) { continue }
        $parts = $mm.Split('.'); $maj = [int]$parts[0]; $min = [int]$parts[1]
        if ($maj -ne 3 -or $min -lt 9) { continue }
        $rank = if ($pref.ContainsKey("$min")) { $pref["$min"] } else { 100 + $min }
        $ranked += [pscustomobject]@{ Exe = $exe; Version = $mm; Rank = $rank }
    }
    if ($ranked.Count -eq 0) { return $null }
    return ($ranked | Sort-Object Rank | Select-Object -First 1)
}

Step "Checking for Python (3.9+)"
$py = Find-Python
if (-not $py) {
    Warn "No suitable Python found."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Step "Installing Python 3.12 via winget (a UAC prompt may appear)"
        winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements
        $py = Find-Python
    }
    if (-not $py) {
        Fail ("Python is required and could not be installed automatically.`n" +
              "    Install Python 3.11 or 3.12 from https://www.python.org/downloads/`n" +
              "    (tick 'Add python.exe to PATH' in the installer), then double-click install.bat again.")
    }
}
Ok "Python $($py.Version)  ($($py.Exe))"

# ---- 2. Update source (git) ------------------------------------------------
if ((Test-Path (Join-Path $Root '.git')) -and -not $NoPull -and (Get-Command git -ErrorAction SilentlyContinue)) {
    # only pull when the working tree is clean, so we never disturb local edits
    & git -C $Root diff --quiet 2>$null;        $dirty1 = ($LASTEXITCODE -ne 0)
    & git -C $Root diff --cached --quiet 2>$null; $dirty2 = ($LASTEXITCODE -ne 0)
    if ($dirty1 -or $dirty2) {
        Step "Skipping 'git pull' - you have local changes (using the current code)"
    } else {
        Step "Updating source from GitHub (git pull)"
        & git -C $Root pull --ff-only
        if ($LASTEXITCODE -eq 0) { Ok "Source up to date" }
        else { Warn "git pull skipped (offline, or the branch has diverged) - using the current code" }
    }
}

# ---- 3. Virtual environment + dependencies ---------------------------------
$venvPy = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
    Step "Creating the virtual environment (.venv)"
    & $py.Exe -m venv (Join-Path $Root '.venv')
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPy)) {
        Fail "Could not create the .venv virtual environment."
    }
    Ok "Created .venv"
} else {
    Step "Using the existing virtual environment (.venv)"
}

Step "Installing / updating dependencies"
& $venvPy -m pip install --upgrade pip --quiet --disable-pip-version-check
& $venvPy -m pip install --upgrade -r (Join-Path $Root 'requirements.txt') --disable-pip-version-check
if ($LASTEXITCODE -ne 0) {
    Warn "Full install failed - retrying the core packages without the optional 'sounddevice'."
    $core = @()
    foreach ($line in (Get-Content (Join-Path $Root 'requirements.txt'))) {
        $pkg = ($line -split '#')[0].Trim()
        if ($pkg -and $pkg -notmatch '^(?i)sounddevice') { $core += $pkg }
    }
    & $venvPy -m pip install --upgrade @core --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { Fail "Dependency install failed. Read the pip messages above." }
    Warn "Installed WITHOUT 'sounddevice' - remote host sound-card audio for serial radios is disabled until it can install (use Python 3.11/3.12). Everything else works."
} else {
    Ok "Dependencies installed"
}

# ---- 4. Verify -------------------------------------------------------------
Step "Verifying the install"
$ver = & $venvPy -c "import backend, backend.server; print(backend.__version__)" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host ($ver -join "`n") -ForegroundColor DarkGray
    Fail "The app failed to import after install."
}
Ok "Radio WebOp $(($ver | Select-Object -Last 1).ToString().Trim()) is ready"

# ---- 5. Desktop shortcut ---------------------------------------------------
if (-not $NoShortcut) {
    try {
        $desktop = [Environment]::GetFolderPath('Desktop')
        $lnkPath = Join-Path $desktop 'Radio WebOp.lnk'
        $ws = New-Object -ComObject WScript.Shell
        $lnk = $ws.CreateShortcut($lnkPath)
        $lnk.TargetPath = Join-Path $Root 'run.bat'
        $lnk.WorkingDirectory = $Root
        $lnk.Description = 'Launch Radio WebOp'
        $lnk.IconLocation = "$venvPy,0"
        $lnk.Save()
        Ok "Desktop shortcut 'Radio WebOp' created"
    } catch {
        Warn "Could not create the desktop shortcut: $($_.Exception.Message)"
    }
}

Write-Host ""
Write-Host "  All set." -ForegroundColor Green
Write-Host "  Start it with the 'Radio WebOp' desktop shortcut, or double-click run.bat." -ForegroundColor White
Write-Host "  It opens http://localhost:8700 in your browser. Re-run install.bat anytime to update." -ForegroundColor Gray
Write-Host ""
