param([Parameter(Mandatory=$true)][string]$TargetDirectory)
$ErrorActionPreference='Stop'
$source=$PSScriptRoot
$target=[IO.Path]::GetFullPath($TargetDirectory)
if ($target -eq [IO.Path]::GetFullPath($source)) { throw 'Choose a separate installed application directory.' }
$exe=Join-Path $source 'ClientOpsTool.exe'
if (!(Test-Path $exe)) { throw 'ClientOpsTool.exe is missing from the package.' }
# The executable cannot safely be replaced while in use.
$running=Get-Process ClientOpsTool -ErrorAction SilentlyContinue
if ($running) { throw 'Close ClientOpsTool before upgrading.' }
New-Item -ItemType Directory -Force -Path $target | Out-Null
$backup=Join-Path $target ('rollback-'+(Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $backup | Out-Null
$oldExe=Join-Path $target 'ClientOpsTool.exe'
if (Test-Path $oldExe) { Copy-Item $oldExe $backup }
$configs=@('site_profiles.json','database_profiles.json','queries.json','updates.json','api_requests.json','workflows.json','ops_settings.json')
try {
  Copy-Item $exe ($oldExe+'.new') -Force
  Move-Item ($oldExe+'.new') $oldExe -Force
  foreach ($name in $configs) {
    $file=Join-Path $source $name
    if ((Test-Path $file) -and !(Test-Path (Join-Path $target $name))) { Copy-Item $file (Join-Path $target $name) }
  }
  Get-ChildItem $source -File | Where-Object { $_.Name -like '*.md' -or $_.Name -like '*.example.json' } | Copy-Item -Destination $target -Force
} catch {
  if (Test-Path (Join-Path $backup 'ClientOpsTool.exe')) { Copy-Item (Join-Path $backup 'ClientOpsTool.exe') $oldExe -Force }
  throw
}
Write-Host "Upgrade complete. Existing configuration files were retained. Previous executable: $backup"
