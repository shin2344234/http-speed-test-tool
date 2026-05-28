param(
    [Parameter(Mandatory = $true)]
    [string]$ServerIp,

    [int]$Port = 8080,

    [string]$DownloadSize = "1G",

    [string]$UploadSize = "500M",

    [int]$Runs = 3,

    [int]$Streams = 1,

    [int]$TimeoutSeconds = 120,

    [string]$InstallDir = "C:\install\speed",

    [string]$RepoRawBase = "https://raw.githubusercontent.com/shin2344234/http-speed-test-tool/main",

    [string]$PythonWingetId = "Python.Python.3.13",

    [version]$MinimumPythonVersion = [version]"3.9",

    [switch]$NoProgress
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message"
}

function Update-ProcessPath {
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $pathParts = @($machinePath, $userPath) | Where-Object { $_ }
    $env:Path = ($pathParts -join ";")
}

function Get-PythonVersion {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Exe,

        [string[]]$PrefixArgs = @()
    )

    try {
        $versionArgs = @($PrefixArgs) + @("-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))")
        $output = & $Exe @versionArgs 2>$null
        if ($LASTEXITCODE -ne 0) {
            return $null
        }
        $text = ($output | Select-Object -First 1).ToString().Trim()
        if ($text -match "^\d+\.\d+\.\d+") {
            return [version]$Matches[0]
        }
    }
    catch {
        return $null
    }

    return $null
}

function New-PythonCandidate {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Exe,

        [string[]]$PrefixArgs = @()
    )

    $version = Get-PythonVersion -Exe $Exe -PrefixArgs $PrefixArgs
    if ($null -eq $version) {
        return $null
    }

    [pscustomobject]@{
        Exe = $Exe
        PrefixArgs = $PrefixArgs
        Version = $version
        Display = (@($Exe) + @($PrefixArgs)) -join " "
    }
}

function Find-CompatiblePython {
    param([version]$MinimumVersion)

    $candidates = New-Object System.Collections.Generic.List[object]

    foreach ($commandName in @("py", "python", "python3")) {
        $command = Get-Command $commandName -ErrorAction SilentlyContinue
        if ($null -eq $command) {
            continue
        }

        if ($commandName -eq "py") {
            $candidate = New-PythonCandidate -Exe $command.Source -PrefixArgs @("-3")
        }
        else {
            $candidate = New-PythonCandidate -Exe $command.Source
        }

        if ($null -ne $candidate) {
            $candidates.Add($candidate) | Out-Null
        }
    }

    $knownRoots = @(
        Join-Path $env:LocalAppData "Programs\Python"
        $env:ProgramFiles
        ${env:ProgramFiles(x86)}
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }

    foreach ($root in $knownRoots) {
        Get-ChildItem -LiteralPath $root -Directory -Filter "Python*" -ErrorAction SilentlyContinue |
            ForEach-Object {
                $pythonExe = Join-Path $_.FullName "python.exe"
                if (Test-Path -LiteralPath $pythonExe) {
                    $candidate = New-PythonCandidate -Exe $pythonExe
                    if ($null -ne $candidate) {
                        $candidates.Add($candidate) | Out-Null
                    }
                }
            }
    }

    $compatible = $candidates |
        Where-Object { $_.Version -ge $MinimumVersion } |
        Sort-Object Version -Descending |
        Select-Object -First 1

    return $compatible
}

function Install-PythonWithWinget {
    param([string]$PackageId)

    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($null -eq $winget) {
        throw "Python is missing or incompatible, and winget was not found. Install winget/App Installer first, then rerun this script."
    }

    Write-Step "Installing Python with winget"
    & $winget.Source install -e --id $PackageId --scope user --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
    if ($LASTEXITCODE -ne 0) {
        throw "winget failed to install $PackageId. Exit code: $LASTEXITCODE"
    }

    Update-ProcessPath
}

function Invoke-DownloadFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Uri,

        [Parameter(Mandatory = $true)]
        [string]$OutFile
    )

    Write-Host "Downloading $Uri"
    Invoke-WebRequest -Uri $Uri -OutFile $OutFile -UseBasicParsing
}

if ($Runs -lt 1) {
    throw "Runs must be at least 1."
}

if ($Streams -lt 1) {
    throw "Streams must be at least 1."
}

if ($Port -lt 1 -or $Port -gt 65535) {
    throw "Port must be between 1 and 65535."
}

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

Write-Step "Creating install folder"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

Write-Step "Checking Python"
Update-ProcessPath
$python = Find-CompatiblePython -MinimumVersion $MinimumPythonVersion

if ($null -eq $python) {
    Install-PythonWithWinget -PackageId $PythonWingetId
    $python = Find-CompatiblePython -MinimumVersion $MinimumPythonVersion
}

if ($null -eq $python) {
    throw "Python $MinimumPythonVersion or newer was not found after installation."
}

Write-Host "Using Python $($python.Version): $($python.Display)"

Write-Step "Downloading HTTP speed test files"
$files = @(
    "http_speed_test.py",
    "http-speed-test.cmd",
    "http-speed-test.sh",
    "README.md"
)

foreach ($file in $files) {
    $uri = "$RepoRawBase/$file"
    $destination = Join-Path $InstallDir $file
    Invoke-DownloadFile -Uri $uri -OutFile $destination
}

Write-Step "Running speed test"
$toolPath = Join-Path $InstallDir "http_speed_test.py"
$serverUrl = "http://$($ServerIp):$Port"
$testArgs = @(
    $toolPath,
    "both",
    $serverUrl,
    "--download-size",
    $DownloadSize,
    "--upload-size",
    $UploadSize,
    "--runs",
    [string]$Runs,
    "--streams",
    [string]$Streams,
    "--timeout",
    [string]$TimeoutSeconds
)

if (-not $NoProgress) {
    $testArgs += "--progress"
}

Write-Host "Server: $serverUrl"
Write-Host "Install dir: $InstallDir"
Write-Host "Streams: $Streams"
& $python.Exe @($python.PrefixArgs + $testArgs)
exit $LASTEXITCODE
