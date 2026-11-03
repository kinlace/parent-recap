# The windows-install CI job's checks of install.ps1 and the program it installs, one per step:
# `scripts\windows-install-checks.ps1 <check>`. They live here rather than in the workflow so a
# change to the program (a Source added or reordered, a new scheduled task, a reworded message)
# changes no file under .github/, which an unattended agent's push can't change (#720). Where a
# check needs what the program offers, it asks the installed program instead of listing it.
#
# Windows PowerShell 5.1, as families run install.ps1, which reads a file without a BOM in the
# PC's code page: this file stays ASCII.
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('pins', 'failed-download', 'net-types', 'install', 'own-runtimes', 'new-window',
    'setup-page', 'second-install', 'wilma', 'uninstall')]
  [string]$Check
)

$ErrorActionPreference = 'Stop'

$Repo = Split-Path -Parent $PSScriptRoot
$Install = Join-Path $Repo 'install.ps1'
$Installed = "$env:LOCALAPPDATA\ParentRecap"
$Py = "$Installed\app\.venv\Scripts\python.exe"
# What a new window's PATH is, the PC's and the user's as Windows keeps them; this process's own
# is the runner's, from before the install (#560). Single quotes only: Windows PowerShell drops
# double quotes from a -Command argument.
$NewWindow = "`$env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + " +
  "[Environment]::GetEnvironmentVariable('Path', 'User'); "
# For the PowerShells a check starts, which read it themselves.
$env:PARENT_RECAP = $Install

# Runs Python code on the installed program's Python, its output passed on, and throws `Failure`
# when it exits with an error.
function Invoke-Py([string]$Code, [string]$Failure) {
  & $Py -c $Code
  if ($LASTEXITCODE -ne 0) { throw $Failure }
}

# Runs PowerShell code in a new window's PATH, its output passed on.
function Invoke-NewWindow([string]$Code, [switch]$PSReadLine) {
  $first = if ($PSReadLine) { 'Import-Module PSReadLine; ' } else { '' }
  powershell -NoProfile -Command ($first + $NewWindow + $Code)
}

# ---------------------------------------------------------------- the checks

# Prints the published line for a pin that doesn't match, so a bump can copy it.
function Test-Pins {
  $ps1 = Get-Content $Install -Raw
  function Pin([string]$Name) {
    if ($ps1 -notmatch ('(?m)^\s*\$' + $Name + " = '([^']*)'")) { throw "install.ps1 pins no $Name" }
    return $Matches[1]
  }
  $py = Pin PythonVersion; $build = Pin PythonBuild; $node = Pin NodeVersion
  $pins = @(
    @{ List = "https://github.com/astral-sh/python-build-standalone/releases/download/$build/SHA256SUMS"
       File = "cpython-$py+$build-x86_64-pc-windows-msvc-install_only.tar.gz"; Pinned = Pin PythonSha256 },
    @{ List = "https://nodejs.org/dist/v$node/SHASUMS256.txt"
       File = "node-v$node-win-x64.zip"; Pinned = Pin NodeSha256 })
  $sums = Join-Path $env:RUNNER_TEMP 'sums.txt'
  $wrong = @()
  foreach ($p in $pins) {
    Invoke-WebRequest $p.List -OutFile $sums -UseBasicParsing
    $line = Get-Content $sums | Where-Object { $_.Trim() -match ('\s\*?' + [regex]::Escape($p.File) + '$') }
    if (-not $line -or ($line -split '\s+')[0] -ne $p.Pinned) {
      $wrong += "$($p.File): install.ps1 pins $($p.Pinned), $($p.List) says: $line"
    }
  }
  Remove-Item $sums
  if ($wrong) { throw ($wrong -join "`n") }
}

function Test-FailedDownload {
  # Beside install.ps1, which finds the checkout from its own folder.
  $offline = Join-Path $Repo 'install-offline.ps1'
  (Get-Content $Install -Raw) -replace "PythonReleases = '[^']*'", "PythonReleases = 'https://github.com/kinlace/no-such-release'" |
    Set-Content $offline -Encoding ascii
  try {
    $out = powershell -NoProfile -ExecutionPolicy Bypass -File $offline *>&1 | Out-String
    $code = $LASTEXITCODE
  } finally {
    Remove-Item $offline
  }
  Write-Host $out
  if ($code -ne 1) { throw "exited with $code, not 1" }
  if ($out -notmatch "couldn't download its Python") { throw 'no message about the download' }
  if (Get-ChildItem "$Installed\runtime" -Filter 'python-*') { throw 'a Python was left behind' }
}

# A family's PowerShell window has PSReadLine loaded, which CI's shell and -File don't, and its
# own internal types can stand in for .NET's of the same name (#374).
function Test-NetTypes {
  Import-Module PSReadLine
  function Test-NetOwn([string]$Name) { $t = $Name -as [type]; [bool]($t -and $t.Assembly.GlobalAssemblyCache) }
  # The type that broke the pasted line in 0.6.0, so this check is known to tell.
  if (Test-NetOwn 'System.Runtime.InteropServices.RuntimeInformation') {
    throw "PSReadLine's RuntimeInformation doesn't stand in for .NET's here, so this check can't tell"
  }
  $ast = [System.Management.Automation.Language.Parser]::ParseFile($Install, [ref]$null, [ref]$null)
  $names = $ast.FindAll({ param($n)
      $n -is [System.Management.Automation.Language.TypeExpressionAst] -or
      $n -is [System.Management.Automation.Language.TypeConstraintAst] }, $true) |
    ForEach-Object { $_.TypeName.FullName } | Sort-Object -Unique
  Write-Host "install.ps1 names: $($names -join ', ')"
  $shadowed = @($names | Where-Object { -not (Test-NetOwn $_) })
  if ($shadowed) { throw "not .NET's own type with PSReadLine loaded: $($shadowed -join ', ')" }
}

function Test-Install {
  powershell -NoProfile -ExecutionPolicy Bypass -Command 'Import-Module PSReadLine; & $env:PARENT_RECAP; exit $LASTEXITCODE'
  if ($LASTEXITCODE -ne 0) { throw "exited with $LASTEXITCODE" }
  Get-Content "$Installed\logs\*.log" -Tail 20
  # A step that ends in a traceback still lets the install go on, so only its log tells (#596).
  if (Select-String -Path "$Installed\logs\*.log" -Pattern '^Traceback' -Quiet) { throw 'a Python step ended in a traceback' }
}

function Test-OwnRuntimes {
  & "$Installed\app\.venv\Scripts\parent-recap.exe" --help
  if ($LASTEXITCODE -ne 0) { throw 'parent-recap --help failed' }
  Invoke-Py "import sys; from family_brief import own_node; n = own_node.node(); print(n, own_node.npm()); sys.exit(n is None or own_node.npm() is None)" `
    "the program doesn't find its own Node"
  Invoke-Py "import sys; print(sys._base_executable); sys.exit('ParentRecap' not in sys._base_executable)" `
    "the venv isn't on Parent Recap's own Python"
  Invoke-Py "import sys; from family_brief import baileys_install as b; print(b.folder(), b.installed_version()); sys.exit(not b.up_to_date())" `
    "the install didn't leave the pinned Baileys tree"
}

function Test-NewWindow {
  $found = Invoke-NewWindow -PSReadLine '(Get-Command parent-recap).Source; parent-recap --help | Out-Null; exit $LASTEXITCODE'
  if ($LASTEXITCODE -ne 0) { throw 'parent-recap --help failed in a new window' }
  Write-Host "parent-recap: $found"
  # A script on the venv's Python, not pip's launcher, which Defender can block (#591).
  if ($found -ne "$Installed\bin\parent-recap.cmd") { throw "a new window finds parent-recap at $found" }
  # It passes the program's exit code on: argparse's 2 for a command it doesn't know.
  Invoke-NewWindow 'parent-recap no-such-command; exit $LASTEXITCODE'
  if ($LASTEXITCODE -ne 2) { throw "parent-recap no-such-command exited with $LASTEXITCODE" }
  $python = Invoke-NewWindow '(Get-Command python -ErrorAction SilentlyContinue).Source'
  Write-Host "python: $python"
  if ($python -like "$Installed*") { throw "python in a new window is Parent Recap's: $python" }
  $inside = @(Get-ChildItem "$Installed\bin" | ForEach-Object Name) -join ','
  Write-Host "the PATH folder holds: $inside"
  if ($inside -ne 'parent-recap.cmd') { throw "the PATH folder holds $inside" }
}

# As the pasted line ends: the installed program's setup page, served on this PC, worded for
# Windows and listing the Sources and AIs the program offers on Windows, in its order.
function Test-SetupPage {
  $expected = Invoke-Py "import json; from family_brief import setup_connect; from family_brief.setup_server import context; print(json.dumps({'sources': setup_connect.sources(), 'ais': list(context.ais())}))" `
    "the program didn't say which Sources and AIs it offers" | ConvertFrom-Json
  $wantSources = @($expected.sources) -join ','
  $wantAis = @($expected.ais) -join ','
  $out = Join-Path $env:RUNNER_TEMP 'setup-page.txt'
  # As install.ps1 opens it, on the venv's Python.
  $page = Start-Process $Py -ArgumentList '-m', 'family_brief', 'setup', 'page' `
    -RedirectStandardOutput $out -NoNewWindow -PassThru
  try {
    $url = $null
    for ($i = 0; $i -lt 60 -and -not $url; $i++) {
      Start-Sleep -Seconds 1
      $line = Get-Content $out -ErrorAction SilentlyContinue | Select-Object -First 1
      if ($line) { $url = ($line | ConvertFrom-Json).url }
    }
    if (-not $url) { throw 'the setup page gave no address' }
    $state = Invoke-RestMethod -Uri "${url}api/state" -UseBasicParsing
    $sources = @($state.connect.sources | ForEach-Object { $_.name }) -join ','
    $ais = @($state.ais) -join ','
    Write-Host "system $($state.system), Sources $sources, AIs $ais"
    Write-Host "the program offers Sources $wantSources, AIs $wantAis"
    if ($state.system -ne 'windows') { throw "the page words itself for $($state.system)" }
    if (-not $wantSources -or -not $wantAis) { throw 'the program offers no Source or no AI on Windows' }
    if ($sources -ne $wantSources) { throw "the page lists $sources, not $wantSources" }
    if ($ais -ne $wantAis) { throw "the page offers $ais, not $wantAis" }
  } finally {
    Stop-Process -Id $page.Id -Force -ErrorAction SilentlyContinue
  }
}

function Test-SecondInstall {
  $watched = @("$Installed\runtime", $Py, "$Installed\app\.venv\parent-recap-packages.txt", "$Installed\baileys")
  $before = $watched | ForEach-Object { (Get-Item $_).LastWriteTimeUtc.Ticks }
  $out = powershell -NoProfile -ExecutionPolicy Bypass -File $Install *>&1 | Out-String
  if ($LASTEXITCODE -ne 0) { throw "exited with $LASTEXITCODE" }
  Write-Host $out
  if ($out -match 'Downloading|Installing') { throw 'the second install downloaded or installed something' }
  if ($out -notmatch 'up to date') { throw 'no word that the packages are up to date' }
  $after = $watched | ForEach-Object { (Get-Item $_).LastWriteTimeUtc.Ticks }
  if (Compare-Object $before $after) { throw 'the second install changed the runtimes, the venv or the Baileys tree' }
}

# As the setup page's Wilma step installs it, from npm, with the program's own Node.
function Test-Wilma {
  Invoke-Py "import sys; from family_brief import setup_wilma; r = setup_wilma.install(); print(r); sys.exit(r['result'] != 'installed')" `
    "the wilma CLI didn't install"
  Invoke-Py "import subprocess, sys; from family_brief import own_node, setup_wilma; c = own_node.wilma(); print(c, setup_wilma.installed_version()); sys.exit(subprocess.run([*c, '--help'], env=own_node.wilma_env()).returncode)" `
    'wilma --help failed'
  # The town list the page searches comes with the CLI.
  Invoke-Py "import sys; from family_brief import setup_wilma; t = setup_wilma.tenants(); print(len(t or [])); sys.exit(not t)" `
    "the wilma CLI came without Wilma's town list"
}

# Parent Recap's tasks in Task Scheduler's root folder, as PowerShell sees them.
function Get-OurTasks {
  @(Get-ScheduledTask -TaskPath '\' | Where-Object { $_.TaskName -like 'Parent Recap *' } | ForEach-Object TaskName)
}

# What setup leaves (a config, a secret in Credential Manager, the evening tasks in Task Scheduler,
# the wilma CLI above, the install's Baileys tree), then uninstall run from the installed program,
# as a family runs it: the program and its Python go once it has ended, from a hidden PowerShell.
# The runner's output is a pipe in the PC's code page, as an agent's is, with no emoji (#380).
function Test-Uninstall {
  $pr = "$Installed\app\.venv\Scripts\parent-recap.exe"
  New-Item -ItemType Directory -Force "$HOME\.family" | Out-Null
  $config = "kids:`n- name: Mia`ngmail:`n  username: parent@example.com`nemail:`n  to: [parent@example.com]`nweekend_events:`n  enabled: true`n"
  [IO.File]::WriteAllText("$HOME\.family\config.yaml", $config)
  Invoke-Py "from family_brief import gateway, install_record; from family_brief.system import secrets; secrets.put(gateway.ACCOUNT, 'sk-ci-not-a-real-key'); install_record.add('keychain', gateway.ACCOUNT)" `
    "the Gateway Key wasn't stored"
  & $pr schedule install
  if ($LASTEXITCODE -ne 0) { throw 'schedule install failed' }
  # The config above turns Weekend Picks on, so the program runs both its evening jobs.
  Invoke-Py "import sys; from family_brief.system import jobs; i = jobs.installed(); print(sorted(i)); sys.exit(not {jobs.DAILY, jobs.WEEKEND} <= i)" `
    "the program doesn't run the evening Brief and Weekend Picks"
  # Whichever tasks those take (the evening Brief, its sign-in catch-up and Weekend Picks today),
  # Task Scheduler holds those the program says it keeps, so uninstall has them to remove.
  $kept = @(Invoke-Py "from family_brief.system import jobs; print('\n'.join(sorted(f.name for f in jobs.found())))" `
    "the program didn't list its tasks")
  $tasks = @(Get-OurTasks | Sort-Object)
  Write-Host "Task Scheduler holds: $($tasks -join ', '); the program keeps: $($kept -join ', ')"
  if (-not $tasks) { throw 'no tasks to remove' }
  if (($tasks -join ',') -ne (@($kept | Sort-Object) -join ',')) { throw 'Task Scheduler and the program list different tasks' }

  # From a new window's PATH, as the family types it.
  $out = Invoke-NewWindow 'parent-recap uninstall --confirm --remove-archive; exit $LASTEXITCODE' | Out-String
  $code = $LASTEXITCODE
  Write-Host $out
  if ($code -ne 0) { throw "uninstall exited with $code" }
  if ($out -notmatch 'The program goes once uninstall has ended') { throw 'no word that the program goes once uninstall has ended' }
  for ($i = 0; $i -lt 90 -and (Test-Path -LiteralPath $Installed); $i++) { Start-Sleep -Seconds 1 }

  $left = @(
    Get-OurTasks | ForEach-Object { "task $_" }
    cmdkey /list | Select-String 'Parent Recap/' | ForEach-Object { "credential $($_.Line.Trim())" }
    @($Installed, "$HOME\.family", "$HOME\ParentRecap") | Where-Object { Test-Path -LiteralPath $_ } | ForEach-Object { "folder $_" }
    "$([Environment]::GetEnvironmentVariable('Path', 'User'))".Split(';') | Where-Object { $_ -like '*ParentRecap*' } | ForEach-Object { "PATH entry $_" }
  )
  if ($left.Count -ne 0) { throw "left behind: $($left -join '; ')" }
}

# ---------------------------------------------------------------- running one

$checks = @{
  'pins' = { Test-Pins }; 'failed-download' = { Test-FailedDownload }; 'net-types' = { Test-NetTypes }
  'install' = { Test-Install }; 'own-runtimes' = { Test-OwnRuntimes }; 'new-window' = { Test-NewWindow }
  'setup-page' = { Test-SetupPage }; 'second-install' = { Test-SecondInstall }; 'wilma' = { Test-Wilma }
  'uninstall' = { Test-Uninstall }
}
try {
  & $checks[$Check]
} catch {
  # The step's name says which check this is; the annotation says it on the run's summary too.
  Write-Host $_.InvocationInfo.PositionMessage
  $saw = "$($_.Exception.Message)" -replace "`r?`n", '%0A'
  Write-Host "::error title=windows-install $Check::$saw"
  exit 1
}
# A check fails only by throwing: a program it ran to see what it does may exit with an error.
exit 0
