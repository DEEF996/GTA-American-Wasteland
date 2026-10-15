param(
    [Parameter(Mandatory=$true)][string]$GameDirectory,
    [Parameter(Mandatory=$true)][string]$LoaderArchive
)
$ErrorActionPreference = 'Stop'
$project = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$game = (Resolve-Path -LiteralPath $GameDirectory).Path.TrimEnd('\')
$manifest = Get-Content -LiteralPath "$game\thaw_gta\base-copy.json" -Raw | ConvertFrom-Json
if ($manifest.format -ne 'gta-thaw-isolated-copy-v1' -or $manifest.game -ne $game -or $manifest.source -eq $game) {
    throw 'Deployment requires the verified isolated game copy.'
}
if (Get-Process gta_sa -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq "$game\gta_sa.exe" }) {
    throw 'Close the isolated game before replacing its adapter.'
}
$build = "$project\build\gta-native"
$metadata = Get-Content -LiteralPath "$build\adapter-build.json" -Raw | ConvertFrom-Json
if ((Get-FileHash -LiteralPath "$game\gta_sa.exe").Hash.ToLowerInvariant() -ne $metadata.supported_exe_sha256) {
    throw 'Unsupported game executable.'
}
if ((Get-FileHash -LiteralPath "$build\thaw_gta.asi").Hash.ToLowerInvariant() -ne $metadata.asi_sha256 -or !$metadata.packet_tests_passed) {
    throw 'Build validation or adapter hash differs.'
}
$zipHash = (Get-FileHash -LiteralPath $LoaderArchive).Hash.ToLowerInvariant()
if ($zipHash -ne '14b3a1ad018899571ac9aa01482977f3c6d49e6cba99f552d01c5acacd1315e1') {
    throw 'Expected the verified Ultimate ASI Loader v9.7.4 NoPDB archive.'
}
$dependency = "$project\research\dependencies"
$license = "$dependency\Ultimate-ASI-Loader-LICENSE.txt"
if (!(Test-Path -LiteralPath $license -PathType Leaf)) { throw 'Missing ASI loader license.' }
$staging = "$build\loader-v9.7.4"
Expand-Archive -LiteralPath $LoaderArchive -DestinationPath $staging -Force
$loader = "$staging\dinput8.dll"
$loaderHash = (Get-FileHash -LiteralPath $loader).Hash
$original = @($manifest.files | Where-Object { $_.path -ieq 'vorbisFile.dll' })
if ($original.Count -ne 1) { throw 'Missing original proxy manifest entry.' }
$installedHash = (Get-FileHash -LiteralPath "$game\vorbisFile.dll").Hash
if ($installedHash -ne $original[0].sha256 -and $installedHash -ne $loaderHash) { throw 'Another proxy is already installed.' }
# UAL v9.7.4's vorbis proxy explicitly looks for vorbisHooked.dll.
$backup = "$game\vorbisHooked.dll"
if (!(Test-Path -LiteralPath $backup)) {
    if ($installedHash -ne $original[0].sha256) { throw 'Original proxy backup missing.' }
    Copy-Item -LiteralPath "$game\vorbisFile.dll" -Destination $backup
}
if ((Get-FileHash -LiteralPath $backup).Hash -ne $original[0].sha256) { throw 'Original proxy backup differs.' }
$scripts = "$game\scripts"
New-Item -ItemType Directory -Path $scripts -Force | Out-Null
$ini = "[GlobalSets]`r`nLoadPlugins=1`r`nLoadFromScriptsOnly=1`r`nLoadRecursively=0`r`nDontLoadFromDllMain=1`r`nUseD3D8to9=0`r`nDisableCrashDumps=0`r`n"
if ((Test-Path -LiteralPath "$game\vorbisFile.ini") -and [IO.File]::ReadAllText("$game\vorbisFile.ini") -ne $ini) {
    throw 'Existing loader settings differ; inspect before replacement.'
}
Copy-Item -LiteralPath $loader -Destination "$game\vorbisFile.dll" -Force
Copy-Item -LiteralPath "$build\thaw_gta.asi" -Destination "$scripts\thaw_gta.asi" -Force
Copy-Item -LiteralPath $license -Destination "$game\thaw_gta\Ultimate-ASI-Loader-LICENSE.txt" -Force
[IO.File]::WriteAllText("$game\vorbisFile.ini", $ini, [Text.Encoding]::ASCII)
@{
    format = 'gta-thaw-probe-install-v1'
    game = $game
    adapter_sha256 = $metadata.asi_sha256
    loader_sha256 = $loaderHash.ToLowerInvariant()
    loader_archive_sha256 = $zipHash
    loader_release = 'https://github.com/ThirteenAG/Ultimate-ASI-Loader/releases/tag/v9.7.4'
    original_proxy_backup = $backup
    original_proxy_sha256 = $original[0].sha256
    gameplay_enabled = $false
} | ConvertTo-Json | Set-Content -LiteralPath "$game\thaw_gta\probe-install.json" -Encoding UTF8
Write-Host 'Installed the telemetry probe in the isolated copy. Skating is not enabled.'
