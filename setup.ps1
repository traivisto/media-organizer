<#
    One-time local setup for Kuva-arkisto.

    Run this in PowerShell on the machine that will do the indexing:

        cd "C:\Users\traiv\OneDrive\My Digital Solutions\Media classifier"
        .\setup.ps1

    If PowerShell refuses to run it, allow scripts for this window only:

        Set-ExecutionPolicy -Scope Process Bypass

    Do NOT run as Administrator: an elevated shell does not inherit your
    mapped network drives, so Z:\ would not exist.

    Everything it creates lives outside OneDrive. A SQLite database with WAL
    journalling inside a syncing folder eventually corrupts (spec 10.5).

    The runtime goes in ~\.mediaidx rather than %LOCALAPPDATA%\mediaidx, which
    would be the conventional choice. Reason: the Claude desktop app is an MSIX
    package, so processes it launches have their AppData\Local writes redirected
    into a package-private store at

        %LOCALAPPDATA%\Packages\Claude_*\LocalCache\Local\

    Anything written there is invisible to Explorer and to your own shells, which
    is confusing and hard to diagnose. The profile root is not virtualised, so an
    assistant-launched process and your own PowerShell see the same files.

    It is NOT at C:\mediaidx either. Root-level folders grant Authenticated Users
    Modify, so every account on the machine could read the index -- which comes to
    hold face embeddings and the names of the people in your photographs. The
    profile root inherits per-user ACLs instead.
#>

$ErrorActionPreference = "Stop"

$Root    = "$env:USERPROFILE\.mediaidx"
$OldRoot = "C:\mediaidx"
$Venv    = "$Root\venv"
$Py      = "$Venv\Scripts\python.exe"
$Project = $PSScriptRoot

function Step($msg) { Write-Host "`n== $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "   OK   $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "   ..   $msg" -ForegroundColor Yellow }

Write-Host "Kuva-arkisto setup"
Write-Host "  machine  $env:COMPUTERNAME"
Write-Host "  user     $env:USERNAME"
Write-Host "  project  $Project"
Write-Host "  runtime  $Root"

if (([Security.Principal.WindowsPrincipal] `
     [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "`nThis shell is elevated. Mapped drives such as Z:\ will not be" `
               "visible.`nClose it and run again without 'Run as administrator'." `
               -ForegroundColor Red
    exit 1
}

# --- migrate from an earlier location ------------------------------------
if ((Test-Path "$OldRoot\photos.db") -and -not (Test-Path "$Root\photos.db")) {
    Step "Moving your index out of $OldRoot"
    New-Item -ItemType Directory -Path $Root -Force | Out-Null
    foreach ($item in @("photos.db", "photos.db-wal", "photos.db-shm", "thumbs")) {
        if (Test-Path "$OldRoot\$item") {
            Move-Item "$OldRoot\$item" $Root -Force
            Ok $item
        }
    }
    Warn "config and venv are rebuilt below; delete $OldRoot when you are happy"
}

# --- uv -------------------------------------------------------------------
Step "uv (Python installer and package manager)"
if (Get-Command uv -ErrorAction SilentlyContinue) {
    Ok (uv --version)
} else {
    Warn "installing uv via winget"
    winget install --id astral-sh.uv --accept-source-agreements `
                   --accept-package-agreements --silent
    $env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Host "uv installed but not on PATH. Open a new PowerShell and re-run." `
                   -ForegroundColor Red
        exit 1
    }
    Ok (uv --version)
}

# --- interpreter and environment -----------------------------------------
Step "Python 3.12 (your system Python is left untouched)"
uv python install 3.12
if (-not (Test-Path $Py)) {
    uv venv $Venv --python 3.12
}
Ok $Py

# --- packages -------------------------------------------------------------
Step "PyTorch with CUDA (~2.5 GB, this is the slow part)"
uv pip install --python $Py torch --index-url https://download.pytorch.org/whl/cu128
Ok "torch"

Step "Remaining packages (includes the CUDA 13 runtime for the face models, ~2 GB)"
uv pip install --python $Py -e "$Project[dev]"
Ok "mediaidx and dependencies"

# onnxruntime-gpu falls back to the CPU without complaint when its CUDA
# libraries are missing, and a face pass then takes seven times longer with
# nothing in the output to explain it. Better to find out here.
Step "GPU check for the face stage"
$prov = & $Py -c "import onnxruntime as ort; ort.preload_dlls(directory=''); print('CUDAExecutionProvider' in ort.get_available_providers())" 2>$null
if ($prov -match "True") {
    Ok "onnxruntime sees CUDA"
} else {
    Warn 'onnxruntime has no CUDA provider -- faces would run on the CPU. Run "mediaidx doctor" for the detail.'
}

# --- exiftool -------------------------------------------------------------
Step "ExifTool"
function Find-ExifTool {
    $cmd = Get-Command exiftool -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($c in @(
        "$env:LOCALAPPDATA\Programs\ExifTool\ExifTool.exe",
        "$env:ProgramFiles\ExifTool\exiftool.exe",
        "${env:ProgramFiles(x86)}\ExifTool\exiftool.exe"
    )) { if (Test-Path $c) { return $c } }
    return $null
}

$exifPath = Find-ExifTool
if ($null -eq $exifPath) {
    Warn "installing via winget"
    winget install --id OliverBetz.ExifTool --accept-source-agreements `
                   --accept-package-agreements --silent
    # winget updates PATH for new processes only, so re-probe known locations
    $env:PATH = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
    $exifPath = Find-ExifTool
}
if ($null -eq $exifPath) {
    Write-Host "ExifTool could not be located after installing." -ForegroundColor Red
    Write-Host "Install it manually, then set tools.exiftool in $Root\config.toml"
    exit 1
}
Ok $exifPath

# --- configuration --------------------------------------------------------
Step "Configuration"
$cfg = "$Root\config.toml"
if (Test-Path $cfg) {
    Ok "$cfg (kept, not overwritten)"
} else {
    $names = Join-Path $Project "names.json"
    @"
# Kuva-arkisto configuration. Generated by setup.ps1.
#
# No library path appears here. Folders are indexed by passing them to
# ``mediaidx add``, and nothing else is ever touched (spec P7).

[paths]
# Runtime data, deliberately outside OneDrive (spec 10.5).
database   = "$($Root -replace '\\','/')/photos.db"
thumbnails = "$($Root -replace '\\','/')/thumbs"
logs       = "$($Root -replace '\\','/')/logs"

# The only irreplaceable data the system produces, so this one does belong
# in OneDrive where it is backed up for free.
names_export = "$($names -replace '\\','/')"

[tools]
exiftool = "$($exifPath -replace '\\','/')"

[scan]
reader_threads = 8
chunk_size     = 256

# This library renames everything to ``YYYYMMDD@HHMMSS-Make-Model-NN``, which
# matches 95% of its files and carries the camera as well as the timestamp.
# No other archive uses this, so it belongs here rather than in the
# application. Tried before the shipped defaults.
filename_patterns_extra = [
  { re = '^(?P<ymd>\d{8})@(?P<hms>\d{6})(?:-(?P<make>.+?)-(?P<model>.+?))?-\d+$', hint = "archive" },
]
"@ | ForEach-Object {
        # NOT Out-File -Encoding utf8: Windows PowerShell 5.1 prepends a BOM,
        # which tomllib rejects at line 1, column 1.
        [System.IO.File]::WriteAllText($cfg, $_, [System.Text.UTF8Encoding]::new($false))
    }
    Ok "$cfg (written)"
}

# --- point the app at that config ----------------------------------------
Step "MEDIAIDX_CONFIG"
# The application looks in %LOCALAPPDATA%\mediaidx\config.toml by default, but
# the runtime lives in C:\mediaidx to escape MSIX redirection. Without this the
# config is silently ignored and the built-in defaults win -- which puts the
# database somewhere else entirely and drops the library's filename patterns.
[Environment]::SetEnvironmentVariable("MEDIAIDX_CONFIG", $cfg, "User")
$env:MEDIAIDX_CONFIG = $cfg
Ok "MEDIAIDX_CONFIG = $cfg"

# --- convenience ----------------------------------------------------------
Step "Shortcut"
$alias = "Set-Alias mediaidx `"$Venv\Scripts\mediaidx.exe`""
if (-not (Test-Path $PROFILE)) {
    New-Item -ItemType File -Path $PROFILE -Force | Out-Null
}
# Replace any existing mediaidx alias rather than skipping: after the runtime
# moves, an alias left pointing at the old path is worse than none, because it
# fails in a way that looks like a broken install.
$lines = @(Get-Content $PROFILE -ErrorAction SilentlyContinue |
           Where-Object { $_ -notmatch '^\s*Set-Alias\s+mediaidx\b' })
[System.IO.File]::WriteAllLines($PROFILE, @($lines + $alias))
Ok "'mediaidx' alias -> $Venv\Scripts\mediaidx.exe"

# --- verify ---------------------------------------------------------------
Step "Checking the environment"
& $Py -m mediaidx doctor

Write-Host "`nDone. Open a NEW PowerShell window, then:" -ForegroundColor Cyan
Write-Host '    mediaidx add "Z:\Pictures\2019"'
Write-Host '    mediaidx embed'
Write-Host '    mediaidx serve'
