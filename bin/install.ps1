# Jar Finance — Windows Installer (PowerShell)
# Run: .\bin\install.ps1

Write-Host "=============================="
Write-Host "  Jar — Installation"
Write-Host "=============================="
Write-Host ""

# ── Find project root ────────────────────────────────────────────
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot
Write-Host "Project:  $ProjectRoot"

# ── Find Python 3.9+ ────────────────────────────────────────────
$PythonCmd = $null
foreach ($cmd in @("python3", "python", "py")) {
    try {
        $version = & $cmd --version 2>&1
        if ($version -match "Python 3\.(\d+)") {
            if ([int]$Matches[1] -ge 9) {
                $PythonCmd = $cmd
                Write-Host "Python:   $version ($cmd)"
                break
            }
        }
    } catch {}
}

# ── Install Python if not found ──────────────────────────────────
if (-not $PythonCmd) {
    Write-Host ""
    Write-Host "Python 3.9+ not found. Installing automatically..."

    # Try winget first (Windows 10 1709+ and Windows 11)
    $wingetAvailable = Get-Command winget -ErrorAction SilentlyContinue
    if ($wingetAvailable) {
        Write-Host "Installing Python via winget..."
        winget install Python.Python.3.11 --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -eq 0) {
            # Refresh PATH
            $pyDir = "$env:LOCALAPPDATA\Programs\Python\Python311"
            $env:PATH = "$pyDir;$pyDir\Scripts;$env:PATH"
            $PythonCmd = "python"
        }
    }

    # Fallback: download from python.org
    if (-not $PythonCmd) {
        Write-Host "Downloading Python 3.11 installer..."
        $installerUrl = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
        $installerPath = "$env:TEMP\python-3.11-installer.exe"

        try {
            Invoke-WebRequest -Uri $installerUrl -OutFile $installerPath -UseBasicParsing
        } catch {
            Write-Host ""
            Write-Host "Download failed. Please install Python manually:"
            Write-Host "  https://www.python.org/downloads/"
            Write-Host "  IMPORTANT: Check 'Add Python to PATH' during installation."
            exit 1
        }

        Write-Host "Installing Python 3.11 (this may take a minute)..."
        $process = Start-Process -FilePath $installerPath -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_pip=1", "Include_launcher=1" -Wait -PassThru

        if ($process.ExitCode -ne 0) {
            Write-Host "Silent install failed. Running interactive installer..."
            Write-Host "IMPORTANT: Check 'Add Python to PATH' at the bottom of the installer."
            Start-Process -FilePath $installerPath -ArgumentList "PrependPath=1" -Wait
        }

        Remove-Item $installerPath -ErrorAction SilentlyContinue

        # Refresh PATH
        $pyDir = "$env:LOCALAPPDATA\Programs\Python\Python311"
        $env:PATH = "$pyDir;$pyDir\Scripts;$env:PATH"
        $PythonCmd = "python"
    }

    # Verify installation
    try {
        $version = & $PythonCmd --version 2>&1
        Write-Host "Python:   $version"
    } catch {
        Write-Host ""
        Write-Host "Python installation completed but not found on PATH."
        Write-Host "Please close this window, reopen PowerShell, and run install.ps1 again."
        exit 1
    }
}

# ── Create virtual environment ───────────────────────────────────
Write-Host ""
if (Test-Path "venv") {
    Write-Host "Virtual environment already exists."
} else {
    Write-Host "Creating virtual environment..."
    & $PythonCmd -m venv venv
}
& .\venv\Scripts\Activate.ps1

# ── Install dependencies ─────────────────────────────────────────
Write-Host "Installing dependencies..."
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
Write-Host "Dependencies installed."

# ── Initialize database ─────────────────────────────────────────
Write-Host ""
Write-Host "Initializing database..."
if (-not (Test-Path "data")) { New-Item -ItemType Directory -Path "data" | Out-Null }

& python -c @"
import sqlite3, os
db_path = os.path.join('data', 'jar.db')
schema_path = os.path.join('core', 'database', 'schema.sql')
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript(open(schema_path).read())
    conn.close()
    print('Database updated (existing data preserved)')
elif os.path.exists(schema_path):
    conn = sqlite3.connect(db_path)
    conn.executescript(open(schema_path).read())
    conn.close()
    print('Database initialized')
else:
    print('Warning: schema.sql not found, skipping DB init')
"@

# ── Create launcher ──────────────────────────────────────────────
Write-Host ""
$LauncherPath = Join-Path $ProjectRoot "launch_jar.bat"
@"
@echo off
cd /d "$ProjectRoot"
call venv\Scripts\activate.bat
echo Starting Jar at http://localhost:5000
start http://localhost:5000
python app\server.py
"@ | Out-File -FilePath $LauncherPath -Encoding ASCII
Write-Host "Launcher created: $LauncherPath"

# ── Done ─────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=============================="
Write-Host "  Jar installed successfully!"
Write-Host "=============================="
Write-Host ""
Write-Host "  To start: double-click launch_jar.bat"
Write-Host ""
Write-Host "  Optional: install Ollama for local AI chat"
Write-Host "    Download from: https://ollama.com/download/windows"
Write-Host "    Then run: ollama pull qwen3:8b"
Write-Host ""
Write-Host "  All data stays on your machine. Nothing is sent anywhere. Ever."
Write-Host ""
