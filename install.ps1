# Install or update Parent Recap on a Windows PC into %LOCALAPPDATA%\ParentRecap\app (ADR 0019):
# the one line a family pastes into PowerShell, the same for every Household, from the `stable`
# branch, so a family only ever gets a tested release (ADR 0002):
#
#   irm https://raw.githubusercontent.com/kinlace/parent-recap/stable/install.ps1 | iex
#
# Pasted that way, it downloads the `stable` branch into %LOCALAPPDATA%\ParentRecap\plugin first,
# installs from there, and opens the setup page. Run as a file next to `app\` (a checkout or an
# unzipped release), it installs from that folder, and only installs.
# Needs no administrator rights: it writes only under %LOCALAPPDATA%\ParentRecap and setup's
# record (~\.family\install-record.json), so `parent-recap uninstall` can remove what it made.
# Safe to re-run: a run that stopped partway picks up from where it got to, and a run with
# nothing new to do downloads and installs nothing. It never touches the config or the secrets.
#
# ASCII only: Windows PowerShell 5.1 reads a script file without a byte order mark in the PC's
# own code page.

& {
  Set-StrictMode -Version 3.0
  $ErrorActionPreference = 'Stop'
  # Windows PowerShell 5.1 draws a progress bar for every downloaded block, which makes a large
  # download many times slower.
  $ProgressPreference = 'SilentlyContinue'
  [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor
    [Net.SecurityProtocolType]::Tls12

  $Repo = 'kinlace/parent-recap'
  $Target = if ($env:PARENT_RECAP_HOME) { $env:PARENT_RECAP_HOME } else { Join-Path $env:LOCALAPPDATA 'ParentRecap' }
  $App = Join-Path $Target 'app'
  $Logs = Join-Path $Target 'logs'
  $Runtimes = Join-Path $Target 'runtime'
  # Everything the programs it runs print goes to a dated log, not the window: pip's output is
  # pages a family can't act on.
  $Log = Join-Path $Logs ('install-{0}.log' -f (Get-Date -Format 'yyyy-MM-dd-HHmmss'))
  # Windows' own tar, which unpacks .tar.gz and .zip alike: another one on PATH, such as Git's,
  # may not read C:\ paths.
  $Tar = Join-Path $env:SystemRoot 'System32\tar.exe'

  # -- Parent Recap's own Python (ADR 0011)
  # The python-build-standalone build install.sh pins, for Windows x64. To bump it, see
  # CONTRIBUTING.md.
  $PythonVersion = '3.13.16'
  $PythonBuild = '20261003'
  $PythonSha256 = '5e100ee3d592ff500f4408a624f054d202e32d9dba8a12b2226bef81083fd778'
  $PythonReleases = 'https://github.com/astral-sh/python-build-standalone/releases/download'
  $PythonFile = "cpython-$PythonVersion+$PythonBuild-x86_64-pc-windows-msvc-install_only.tar.gz"
  $Runtime = Join-Path $Runtimes "python-$PythonVersion-$PythonBuild"
  $Python = Join-Path $Runtime 'python.exe'

  # -- Parent Recap's own Node (ADR 0011)
  # The nodejs.org LTS release install.sh pins, for Windows x64, which the wilma CLI runs on
  # (own_node.py). To bump it, see CONTRIBUTING.md.
  $NodeVersion = '24.21.0'
  $NodeSha256 = '158f7685b44de51f6c0df1d153526cbcd3e1bc739a8dfc607721cef75de9e541'
  $NodeReleases = 'https://nodejs.org/dist'
  $NodeFile = "node-v$NodeVersion-win-x64.zip"
  $NodeRuntime = Join-Path $Runtimes "node-v$NodeVersion"

  $TryAgain = 'Run the install line again. If it fails again, tell the Parent Recap team.'

  function Stop-Install([string]$Message, [string]$Next = $TryAgain) {
    throw [System.InvalidOperationException]::new("$Message`n   $Next")
  }

  # Runs a program with its output going to the install log, and returns its exit code. Not
  # through PowerShell's own error handling: Windows PowerShell 5.1 turns anything a program
  # writes to stderr into an error, which would stop the install.
  function Invoke-Logged([string]$Program, [string[]]$Arguments) {
    $before = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
      & $Program @Arguments 2>&1 | ForEach-Object { "$_" } | Out-File -FilePath $Log -Append -Encoding utf8
      return $LASTEXITCODE
    } finally {
      $ErrorActionPreference = $before
    }
  }

  function Test-ParentRecap([string]$Folder) {
    $manifest = Join-Path $Folder '.claude-plugin\plugin.json'
    if (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) { return $false }
    try { $name = (Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json).name } catch { return $false }
    return $name -eq 'parent-recap'
  }

  function Remove-Folder([string]$Folder) {
    if (Test-Path -LiteralPath $Folder) { Remove-Item -LiteralPath $Folder -Recurse -Force }
  }

  # Downloads $Url into $Folder, checks it against $Sha256, and unpacks it into $Folder\unpacked
  # without its top folder, where $Program must then be. Returns that folder.
  function Get-Runtime([string]$What, [string]$Url, [string]$Sha256, [string]$Folder, [string]$Program) {
    Write-Host "Downloading Parent Recap's own $What..."
    $file = Join-Path $Folder ($Url -replace '.*/', '')
    try {
      Invoke-WebRequest -Uri $Url -OutFile $file -UseBasicParsing
    } catch {
      "Downloading $Url failed: $_" | Out-File -FilePath $Log -Append -Encoding utf8
      Stop-Install "Parent Recap couldn't download its $What (is this PC online?)."
    }
    if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $Sha256) {
      Stop-Install ("The $What Parent Recap downloaded isn't the one it expects (its checksum doesn't " +
        "match), so it wasn't used.")
    }
    $unpacked = Join-Path $Folder 'unpacked'
    New-Item -ItemType Directory -Path $unpacked | Out-Null
    $code = Invoke-Logged $Tar @('-xf', $file, '-C', $unpacked, '--strip-components', '1')
    if ($code -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $unpacked $Program) -PathType Leaf)) {
      Stop-Install "Parent Recap couldn't unpack the $What it downloaded."
    }
    return $unpacked
  }

  # Puts the pinned $What in $Destination unless its $Program is there. It's downloaded and
  # checked in a folder next to the runtimes, so the last step is a rename on the same disk,
  # which can't leave half a runtime behind. A run that was cut off leaves only that folder,
  # which the next run clears, and a runtime that is in place stays for the next run.
  function Install-Runtime([string]$What, [string]$Url, [string]$Sha256, [string]$Destination, [string]$Program) {
    if (Test-Path -LiteralPath (Join-Path $Destination $Program) -PathType Leaf) { return }
    $download = Join-Path $Runtimes '.download'
    Remove-Folder $download
    Remove-Folder $Destination
    New-Item -ItemType Directory -Path $download | Out-Null
    try {
      $unpacked = Get-Runtime $What $Url $Sha256 $download $Program
      try {
        Move-Item -LiteralPath $unpacked -Destination $Destination
      } catch {
        Stop-Install "Parent Recap couldn't put the $What it downloaded in place (is the disk full?)."
      }
    } finally {
      Remove-Item -LiteralPath $download -Recurse -Force -ErrorAction SilentlyContinue
    }
  }

  # Puts the `stable` branch in $Target\plugin, replacing what's there only if it's Parent Recap.
  function Get-Stable {
    $plugin = Join-Path $Target 'plugin'
    if ((Test-Path -LiteralPath $plugin) -and -not (Test-ParentRecap $plugin)) {
      Stop-Install "$plugin holds something that isn't Parent Recap, so it was left as it is." `
        'Move it somewhere else and paste the line again.'
    }
    Write-Host 'Downloading the latest stable release of Parent Recap...'
    $download = Join-Path $Target '.plugin-download'
    Remove-Folder $download
    New-Item -ItemType Directory -Path (Join-Path $download 'plugin') | Out-Null
    try {
      $zip = Join-Path $download 'stable.zip'
      try {
        Invoke-WebRequest -Uri "https://github.com/$Repo/archive/refs/heads/stable.zip" -OutFile $zip -UseBasicParsing
      } catch {
        Stop-Install "Parent Recap couldn't download its latest release (is this PC online?)."
      }
      $code = Invoke-Logged $Tar @('-xf', $zip, '-C', (Join-Path $download 'plugin'), '--strip-components', '1')
      if ($code -ne 0 -or -not (Test-ParentRecap (Join-Path $download 'plugin'))) {
        Stop-Install "The download isn't Parent Recap, so it wasn't used."
      }
      # Swapped in whole, so a file the new release dropped doesn't stay behind.
      if (Test-Path -LiteralPath $plugin) { Move-Item -LiteralPath $plugin -Destination (Join-Path $download 'old') }
      Move-Item -LiteralPath (Join-Path $download 'plugin') -Destination $plugin
    } finally {
      Remove-Item -LiteralPath $download -Recurse -Force -ErrorAction SilentlyContinue
    }
    return $plugin
  }

  # The folder of the Python the venv in $App was made with, from its pyvenv.cfg; empty without one.
  function Get-VenvHome {
    $cfg = Join-Path $App '.venv\pyvenv.cfg'
    if (-not (Test-Path -LiteralPath $cfg -PathType Leaf)) { return '' }
    $line = Get-Content -LiteralPath $cfg | Where-Object { $_ -match '^home\s*=' } | Select-Object -First 1
    if ($line) { return ($line -replace '^home\s*=\s*', '').TrimEnd('\') }
    return ''
  }

  function Install-ParentRecap {
    if ([Environment]::OSVersion.Platform -ne 'Win32NT') {
      Stop-Install 'This install line is for Windows.' 'On a Mac, paste the line in the README into Terminal.'
    }
    # The processor's own, which 9 is x64: x64 PowerShell on an ARM PC says AMD64 in
    # PROCESSOR_ARCHITECTURE. Not RuntimeInformation, which in a PowerShell window resolves to
    # PSReadLine's own type of that name, without OSArchitecture (#374).
    if (@(Get-CimInstance Win32_Processor)[0].Architecture -ne 9) {
      Stop-Install 'Parent Recap runs only on Windows PCs with an Intel or AMD processor (x64), for now.' `
        'Tell the Parent Recap team which PC you have.'
    }
    New-Item -ItemType Directory -Path $Target, $Logs, $Runtimes -Force | Out-Null
    $os = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
    $pc = if ($os) { "$($os.Caption) $($os.Version)" } else { 'Windows' }
    Set-Content -LiteralPath $Log -Value "PC: x64, $pc, Python $PythonVersion, Node $NodeVersion" -Encoding utf8

    $here = $PSScriptRoot
    $fromDownload = -not ($here -and (Test-ParentRecap $here) -and
      (Test-Path -LiteralPath (Join-Path $here 'app\pyproject.toml') -PathType Leaf))
    $plugin = if ($fromDownload) { Get-Stable } else { $here }

    Install-Runtime 'Python' "$PythonReleases/$PythonBuild/$PythonFile" $PythonSha256 $Runtime 'python.exe'
    Install-Runtime 'Node' "$NodeReleases/v$NodeVersion/$NodeFile" $NodeSha256 $NodeRuntime 'node.exe'

    # The program, without what belongs to this PC (the venv, Python's caches and VERSION) and
    # without the tests, which don't go to families.
    $code = Invoke-Logged robocopy @((Join-Path $plugin 'app'), $App, '/MIR',
      '/XD', '.venv', '__pycache__', '*.egg-info', (Join-Path $plugin 'app\tests'), '/XF', 'VERSION',
      '/NFL', '/NDL', '/NJH', '/NJS', '/NP')
    if ($code -ge 8) { Stop-Install "Parent Recap couldn't copy its program into $App (is the disk full?)." }
    $manifest = Get-Content -LiteralPath (Join-Path $plugin '.claude-plugin\plugin.json') -Raw | ConvertFrom-Json
    $version = if ($manifest.PSObject.Properties['version']) { $manifest.version } else { 'unknown' }
    $versionFile = Join-Path $App 'VERSION'
    if (-not (Test-Path -LiteralPath $versionFile) -or (Get-Content -LiteralPath $versionFile -Raw).Trim() -ne $version) {
      Set-Content -LiteralPath $versionFile -Value $version -Encoding ascii
    }

    # A venv on another Python, such as an older pinned one, is made again.
    $venvPython = Join-Path $App '.venv\Scripts\python.exe'
    if ((Get-VenvHome) -ne $Runtime -or -not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
      Remove-Folder (Join-Path $App '.venv')
      if ((Invoke-Logged $Python @('-m', 'venv', (Join-Path $App '.venv'))) -ne 0) {
        Stop-Install "Parent Recap couldn't set up its Python in $App. The full log is at $Log"
      }
    }

    # Prebuilt packages only, at the versions that were tested (app\constraints.txt, see
    # CONTRIBUTING.md), as install.sh installs them. What they were installed for is noted in the
    # venv: the same again needs no pip, so a run with nothing new to do needs no network. The
    # program's own code is installed in place (-e), so a new copy of it needs no pip either.
    $stamp = Join-Path $App '.venv\parent-recap-packages.txt'
    $had = if (Test-Path -LiteralPath $stamp -PathType Leaf) { (Get-Content -LiteralPath $stamp -Raw).Trim() } else { '' }
    $packages = @(
      (Get-FileHash -LiteralPath (Join-Path $App 'constraints.txt') -Algorithm SHA256).Hash,
      (Get-FileHash -LiteralPath (Join-Path $App 'pyproject.toml') -Algorithm SHA256).Hash
    ) -join ' '
    $pipFailed = "Parent Recap couldn't install its Python packages. The full log is at $Log"
    $sendLog = 'Run the install line again. If it fails again, send that log to the Parent Recap team.'
    $pip = @('-m', 'pip', 'install', '--only-binary', ':all:', '--disable-pip-version-check',
      '-c', (Join-Path $App 'constraints.txt'))
    $installed = $false
    if (-not $had.StartsWith("$packages ")) {
      Write-Host "Installing Parent Recap's Python packages..."
      if ((Invoke-Logged $venvPython @('-m', 'pip', 'install', '--only-binary', ':all:', '--upgrade', 'pip')) -ne 0) {
        Stop-Install $pipFailed $sendLog
      }
      if ((Invoke-Logged $venvPython ($pip + @('-e', $App))) -ne 0) { Stop-Install $pipFailed $sendLog }
      $installed = $true
    }
    # Google's packages only for a Household whose config uses Google Calendar, which the program
    # installed above reads: turning it on in manage runs the install again, and an update keeps
    # them.
    $google = (Invoke-Logged $venvPython @('-m', 'family_brief.google_packages', 'wanted')) -eq 0
    $wanted = "$packages google=$google"
    if ($google -and ($installed -or $had -ne $wanted)) {
      if (-not $installed) { Write-Host "Installing Parent Recap's Python packages..." }
      if ((Invoke-Logged $venvPython ($pip + @('-e', "$($App)[google]"))) -ne 0) { Stop-Install $pipFailed $sendLog }
      $installed = $true
    }
    if ($had -ne $wanted) { Set-Content -LiteralPath $stamp -Value $wanted -Encoding ascii }
    if (-not $installed) { Write-Host "Parent Recap's Python packages are up to date." }

    # Only once the program runs on the new Python, so a failed pip step keeps the old one. The
    # wilma CLI runs on whichever Node is there, so an old Node goes with it.
    Get-ChildItem -LiteralPath $Runtimes -Directory |
      Where-Object { ($_.Name -like 'python-*' -or $_.Name -like 'node-*') -and
        $_.FullName -ne $Runtime -and $_.FullName -ne $NodeRuntime } |
      Remove-Item -Recurse -Force
    $record = @('-m', 'family_brief.install_record', 'program', $App, 'logs', $Logs, 'runtime', $Runtimes)
    if ($fromDownload) { $record += @('plugin', $plugin) }
    if ((Invoke-Logged $venvPython $record) -ne 0) {
      Stop-Install "Parent Recap couldn't write setup's record. The full log is at $Log"
    }
    # A Household that connected Wilma gets the wilma CLI version this release pins (ADR 0008), on
    # the Node above. One that couldn't be updated keeps working, and the next install tries again.
    Invoke-Logged $venvPython @('-m', 'family_brief.setup_wilma', 'update') | Out-Null

    Write-Host "Parent Recap $version is installed in $App"
    Write-Host "   Command: $(Join-Path $App '.venv\Scripts\parent-recap.exe')"
  }

  $failed = $false
  try {
    Install-ParentRecap
  } catch {
    Write-Host "Parent Recap wasn't installed. $($_.Exception.Message)" -ForegroundColor Red
    $failed = $true
  }
  # Run as a file, a failed install exits with 1, and one that worked with 0, whatever the last
  # program it ran exited with. Pasted into PowerShell, exiting would close the family's window,
  # with the message in it.
  if ($PSCommandPath) { exit [int]$failed }
  # Pasted, the line goes on to the setup page (ADR 0006), as get.sh does on a Mac, served from
  # this window until setup is done; it picks up where setup got to. Run as a file, as an update
  # from the chat or CI runs it, it only installs, as install.sh does.
  if (-not $failed -and -not $PSCommandPath) {
    Write-Host 'Parent Recap is installed. The setup page opens in your browser now.'
    Write-Host '   Keep this PowerShell window open until setup is done: the page runs from it.'
    & (Join-Path $App '.venv\Scripts\parent-recap.exe') setup page
  }
}
