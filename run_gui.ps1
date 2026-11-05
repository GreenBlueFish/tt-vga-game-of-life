# Launch the interactive cocotb GUI for tt_um_vga_interface on Windows.
#
# Why this script exists:
#   * cocotb's Makefile needs Unix shell tools (make, tr, sh), which live in
#     MSYS2, and it cannot handle spaces in the project path. This project's
#     path contains a space ("UW 2B"), so we copy the sources to a space-free
#     staging directory and run the flow there under MSYS2 bash, pointed at the
#     Windows Python that has cocotb installed.
#
# Usage (from anywhere):
#   powershell -ExecutionPolicy Bypass -File run_gui.ps1
#   # or, if your policy allows:   .\run_gui.ps1
#
# Optional: run the automated smoke test instead of the interactive window:
#   .\run_gui.ps1 -SelfTest 3
#
# Edit the paths below if your tools are installed elsewhere.

param(
    [int]$SelfTest = 0,          # >0 : scripted load+step then auto-close
    [string]$Module = "gui",     # cocotb MODULE: "gui" (window) or "test" (suite)
    [switch]$FullFrame           # use real 640x480 timing (default: fast sim frame)
)

$ErrorActionPreference = "Stop"

# --- Tool locations (edit if yours differ) ---
$Bash       = "C:\msys64\usr\bin\bash.exe"
$IverilogWin = "C:\iverilog\bin"
# Python that has cocotb installed (we verified C:\Python314):
$PyWin      = "C:\Python314"
$PyScripts  = "$env:APPDATA\Python\Python314\Scripts"   # cocotb-config lives here

# --- Sanity checks ---
foreach ($p in @($Bash, "$IverilogWin\iverilog.exe", "$PyWin\python.exe")) {
    if (-not (Test-Path $p)) { throw "Required tool not found: $p" }
}

# --- Stage sources to a space-free directory ---
$Stage = "C:\tt_vga_gui"
$ProjRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "Staging project to $Stage ..." -ForegroundColor Cyan
if (Test-Path $Stage) {
    # best-effort clean; ignore locked leftovers
    cmd /c "rmdir /s /q `"$Stage`"" 2>$null | Out-Null
}
New-Item -ItemType Directory -Path "$Stage\src", "$Stage\test" -Force | Out-Null
Copy-Item -Recurse -Force "$ProjRoot\src\*"  "$Stage\src\"
Copy-Item -Recurse -Force "$ProjRoot\test\*" "$Stage\test\"

# --- Convert Windows tool paths to MSYS (/c/...) form ---
function To-Msys([string]$p) {
    $p = $p -replace '\\', '/'
    if ($p -match '^([A-Za-z]):(.*)$') { return "/" + $Matches[1].ToLower() + $Matches[2] }
    return $p
}
$PyMsys      = To-Msys $PyWin
$PyScrMsys   = To-Msys $PyScripts
$IvMsys      = To-Msys $IverilogWin

# --- Write a bash runner into the staging dir (avoids fragile inline quoting) ---
$selfLine = ""
if ($SelfTest -gt 0) { $selfLine = "export GUI_SELFTEST=$SelfTest" }

# Fast simulation frame (default): shrink the VGA frame so a generation takes
# a few hundred clocks instead of 420,000. -FullFrame opts back into real
# 640x480 timing. This only affects simulation; the taped-out RTL is unchanged.
$fastLine  = ""
$makeExtra = ""
if (-not $FullFrame) {
    $fastLine  = "export GUI_FAST_FRAME=1"
    $makeExtra = "COMPILE_ARGS+=-DSIM_FAST_FRAME"
}

# LF line endings are required for bash.
#
# IMPORTANT: if a Python virtualenv is active in the calling shell, its env
# vars (VIRTUAL_ENV, and its Scripts dir on PATH) leak into this subshell and
# cocotb will boot the venv's Python instead of the one we want - which fails
# with "No simulator available!" if that venv lacks the matching icarus VPI.
# So we scrub the venv out and pin the chosen Python first on PATH.
$runnerLines = @(
    '#!/usr/bin/env bash',
    'set -e',
    '# Scrub any inherited virtualenv so cocotb uses the Python we pin below.',
    'unset VIRTUAL_ENV',
    'unset PYTHONHOME',
    'unset PYTHONPATH',
    "export PATH=`"$PyMsys`:$PyScrMsys`:/usr/bin:$IvMsys`:`$PATH`"",
    'cd /c/tt_vga_gui/test',
    $selfLine,
    $fastLine,
    'echo "python:   $(command -v python)"',
    'echo "iverilog: $(command -v iverilog)"',
    'echo "cocotb:   $(cocotb-config --version)"',
    '# Pin the interpreter cocotb embeds (overrides any venv detection).',
    "export COCOTB_PY_DIR=`"$PyMsys`"",
    "make -B MODULE=$Module $makeExtra"
)
$runnerPath = "$Stage\test\_run.sh"
# Join with LF and write without a BOM.
[System.IO.File]::WriteAllText($runnerPath, ($runnerLines -join "`n") + "`n")

Write-Host "Launching cocotb (MODULE=$Module) ..." -ForegroundColor Cyan
Write-Host "Close the GUI window to end the session." -ForegroundColor DarkGray

# Scrub the venv from THIS process's environment too, so it is not inherited
# by the bash subprocess.
$env:CHERE_INVOKING = "1"
$env:MSYSTEM = "MSYS"
Remove-Item Env:\VIRTUAL_ENV  -ErrorAction SilentlyContinue
Remove-Item Env:\PYTHONHOME   -ErrorAction SilentlyContinue
Remove-Item Env:\PYTHONPATH   -ErrorAction SilentlyContinue
& $Bash /c/tt_vga_gui/test/_run.sh

Write-Host "Done." -ForegroundColor Green
