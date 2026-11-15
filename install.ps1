# Install or update Parent Recap on a Windows PC into %LOCALAPPDATA%\ParentRecap\app (ADR 0019):
# the one line a family pastes into PowerShell, the same for every Household, from the `stable`
# branch, so a family only ever gets a tested release (ADR 0002):
#
#   irm https://raw.githubusercontent.com/kinlace/parent-recap/stable/install.ps1 | iex
#
# Pasted that way, it downloads the `stable` branch into %LOCALAPPDATA%\ParentRecap\plugin first,
# installs from there, and opens the setup page. Run as a file next to `app\` (a checkout or an
# unzipped release), it installs from that folder, and only installs.
# It does only what must happen before any Python is there: the checks below, the download of
# `stable` and Parent Recap's own Python, by checksum. Then it hands over to the release's
# app\windows_install.py on that Python, which does the install's other steps (#990), so that an
# update can run them without starting a .ps1 file.
# Needs no administrator rights, and refuses to run as administrator (#464): it writes only under %LOCALAPPDATA%\ParentRecap and setup's
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
  # The python-build-standalone build install.sh pins, for Windows x64, the same one
  # app\windows_install.py pins. To bump it, see CONTRIBUTING.md.
  $PythonVersion = '3.13.16'
  $PythonBuild = '20261003'
  $PythonSha256 = '5e100ee3d592ff500f4408a624f054d202e32d9dba8a12b2226bef81083fd778'
  # Where the builds are, tried in turn, as in install.sh: Astral's mirror, which uv downloads from,
  # then the project's GitHub releases (#843). The checksum decides what is used.
  $PythonReleases = @(
    'https://releases.astral.sh/github/python-build-standalone/releases/download',
    'https://github.com/astral-sh/python-build-standalone/releases/download')
  $PythonFile = "cpython-$PythonVersion+$PythonBuild-x86_64-pc-windows-msvc-install_only.tar.gz"
  $PythonUrls = @($PythonReleases | ForEach-Object { "$_/$PythonBuild/$PythonFile" })
  $Runtime = Join-Path $Runtimes "python-$PythonVersion-$PythonBuild"
  $Python = Join-Path $Runtime 'python.exe'

  $TryAgain = 'Run the install line again. If it fails again, tell the Parent Recap team.'

  function Stop-Install([string]$Message, [string]$Next = $TryAgain) {
    throw [System.InvalidOperationException]::new("$Message`n   $Next")
  }

  # Whether this PowerShell runs as administrator, as a window opened with "Run as administrator"
  # does: on the full token UAC splits off the user's own (TokenElevationTypeFull, 2). What it makes
  # then belongs to the Administrators group, and the owner-only folders setup makes shut out the
  # evening task, which runs as the user without it (#464). On a PC with UAC off, as CI's runner
  # is, every window runs as administrator, the task too (TokenElevationTypeDefault, 1): that's fine.
  # The type is used through a variable, not named as [ParentRecap.Token]: CI checks every type the
  # script names is .NET's own (#374).
  function Test-Elevated {
    $native = 'ParentRecap.Token' -as [type]
    if (-not $native) {
      $native = @(Add-Type -Namespace ParentRecap -Name Token -PassThru -MemberDefinition @'
[DllImport("kernel32.dll")] public static extern IntPtr GetCurrentProcess();
[DllImport("kernel32.dll")] public static extern bool CloseHandle(IntPtr handle);
[DllImport("advapi32.dll")] public static extern bool OpenProcessToken(IntPtr process, uint access, out IntPtr token);
[DllImport("advapi32.dll")] public static extern bool GetTokenInformation(IntPtr token, int kind, out int value, int size, out int returned);
'@)[0]
    }
    $token = [IntPtr]::Zero
    if (-not $native::OpenProcessToken($native::GetCurrentProcess(), 8, [ref]$token)) { return $false }
    try {
      $kind = 0
      $size = 0
      return $native::GetTokenInformation($token, 18, [ref]$kind, 4, [ref]$size) -and $kind -eq 2
    } finally {
      [void]$native::CloseHandle($token)
    }
  }

  # Runs a program with its output going to the install log, and returns its exit code. Not
  # through PowerShell's own error handling: Windows PowerShell 5.1 turns anything a program
  # writes to stderr into an error, which would stop the install.
  function Invoke-Logged([string]$Program, [string[]]$Arguments) {
    $before = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    # Piped, Python writes the PC's code page, which has no status line's emoji, so a step's
    # last print would end it in a traceback (#596). The window keeps its own setting.
    $utf8 = $env:PYTHONUTF8
    $env:PYTHONUTF8 = '1'
    try {
      & $Program @Arguments 2>&1 | ForEach-Object { "$_" } | Out-File -FilePath $Log -Append -Encoding utf8
      return $LASTEXITCODE
    } finally {
      $ErrorActionPreference = $before
      $env:PYTHONUTF8 = $utf8
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

  # Downloads the first of $Urls that serves the file into $Folder, checks it against $Sha256, and
  # unpacks it into $Folder\unpacked without its top folder, where $Program must then be. Returns
  # that folder.
  function Get-Runtime([string]$What, [string[]]$Urls, [string]$Sha256, [string]$Folder, [string]$Program) {
    Write-Host "Downloading Parent Recap's own $What..."
    $file = Join-Path $Folder ($Urls[0] -replace '.*/', '')
    $got = $false
    foreach ($url in $Urls) {
      try {
        Invoke-WebRequest -Uri $url -OutFile $file -UseBasicParsing
        $got = $true
        break
      } catch {
        "Downloading $url failed: $_" | Out-File -FilePath $Log -Append -Encoding utf8
      }
    }
    if (-not $got) { Stop-Install "Parent Recap couldn't download its $What (is this PC online?)." }
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
  function Install-Runtime([string]$What, [string[]]$Urls, [string]$Sha256, [string]$Destination, [string]$Program) {
    if (Test-Path -LiteralPath (Join-Path $Destination $Program) -PathType Leaf) { return }
    $download = Join-Path $Runtimes '.download'
    Remove-Folder $download
    Remove-Folder $Destination
    New-Item -ItemType Directory -Path $download | Out-Null
    try {
      $unpacked = Get-Runtime $What $Urls $Sha256 $download $Program
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

  function Install-ParentRecap {
    if ([Environment]::OSVersion.Platform -ne 'Win32NT') {
      Stop-Install 'This install line is for Windows.' 'On a Mac, paste the line in the README into Terminal.'
    }
    # Asked of the processor itself, which Windows numbers 9 for x64. Not RuntimeInformation: in a
    # PowerShell window that resolves to PSReadLine's own type of that name, without
    # OSArchitecture (#374). Nor PROCESSOR_ARCHITECTURE, which x64 PowerShell on an ARM PC gives
    # as AMD64. A PC with more than one processor lists each, all the same kind.
    if (@(Get-CimInstance Win32_Processor)[0].Architecture -ne 9) {
      Stop-Install 'Parent Recap runs only on Windows PCs with an Intel or AMD processor (x64), for now.' `
        'Tell the Parent Recap team which PC you have.'
    }
    if (Test-Elevated) {
      Stop-Install ('This PowerShell window runs as administrator. Installed from it, Parent Recap could never send ' +
        'a Brief: the evening job, which runs as you, couldn''t open its own files.') `
        'Close this window, open PowerShell from the Start menu the normal way (not "Run as administrator"), and run the install again there.'
    }
    New-Item -ItemType Directory -Path $Target, $Logs, $Runtimes -Force | Out-Null
    $os = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
    $pc = if ($os) { "$($os.Caption) $($os.Version)" } else { 'Windows' }
    Set-Content -LiteralPath $Log -Value "PC: x64, $pc, Python $PythonVersion" -Encoding utf8

    $here = $PSScriptRoot
    $fromDownload = -not ($here -and (Test-ParentRecap $here) -and
      (Test-Path -LiteralPath (Join-Path $here 'app\pyproject.toml') -PathType Leaf))
    $plugin = if ($fromDownload) { Get-Stable } else { $here }

    Install-Runtime 'Python' $PythonUrls $PythonSha256 $Runtime 'python.exe'

    # What the release's own installer is run with: by path and with -I, so the standard library
    # only, on the Python above, appending to this log.
    $installer = @('-I', (Join-Path $plugin 'app\windows_install.py'), '--log', $Log)
    if ($fromDownload) { $installer += '--downloaded' }
    return $installer
  }

  $failed = $false
  try {
    $installer = @(Install-ParentRecap)
  } catch {
    Write-Host "Parent Recap wasn't installed. $($_.Exception.Message)" -ForegroundColor Red
    $failed = $true
  }
  # The hand-over: the release's installer does the rest (the Node, the program, its venv and
  # packages, the record, the command, the Start menu entries), says in this window what it does
  # and, when a step fails, what the family can do, and exits with 1 then. Started here rather than
  # inside a function, so what it says comes to the window as it goes.
  if (-not $failed) {
    $before = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
      & $Python @installer
      $failed = $LASTEXITCODE -ne 0
    } catch {
      Write-Host "Parent Recap wasn't installed. $($_.Exception.Message)`n   $TryAgain" -ForegroundColor Red
      $failed = $true
    } finally {
      $ErrorActionPreference = $before
    }
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
    # The venv's Python, not pip's parent-recap.exe, which Defender can block (#591).
    & (Join-Path $App '.venv\Scripts\python.exe') -m family_brief setup page
  }
}
