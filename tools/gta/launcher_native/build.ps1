param([string]$OutputDirectory = "$PSScriptRoot\..\..\..\build\gta-launcher", [string]$VcVars)
$ErrorActionPreference = 'Stop'
if (!$VcVars) {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (!(Test-Path -LiteralPath $vswhere)) { throw 'Supply -VcVars with the path to vcvars64.bat.' }
    $installation = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if ($LASTEXITCODE -ne 0 -or !$installation) { throw 'Visual Studio C++ x64 build tools are required.' }
    $VcVars = Join-Path $installation 'VC\Auxiliary\Build\vcvars64.bat'
}
$vcvars = [IO.Path]::GetFullPath($VcVars)
if (!(Test-Path -LiteralPath $vcvars)) { throw "Missing x64 toolchain environment: $vcvars" }
$environment = & $env:ComSpec /d /c "call `"$vcvars`" >nul && set"
if ($LASTEXITCODE -ne 0) { throw 'vcvars64 failed' }
$imported = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($line in $environment) {
    if ($line -match '^([^=]+)=(.*)$' -and $imported.Add($matches[1])) {
        [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process')
    }
}
$output = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $output | Out-Null
& cl.exe /nologo /std:c++17 /W4 /WX /O2 /MT /EHsc "/Fo$output\launcher_test.obj" "/Fe$output\launcher_test.exe" "$PSScriptRoot\launcher_test.cpp" /link shell32.lib
if ($LASTEXITCODE -ne 0) { throw 'Launcher test build failed' }
& "$output\launcher_test.exe"
if ($LASTEXITCODE -ne 0) { throw 'Launcher command-line round-trip test failed' }
& cl.exe /nologo /std:c++17 /W4 /WX /O2 /MT /EHsc "/Fo$output\launcher.obj" "$PSScriptRoot\launcher.cpp" /link /SUBSYSTEM:WINDOWS "/OUT:$output\GTA American Wasteland.exe" user32.lib
if ($LASTEXITCODE -ne 0) { throw 'Launcher x64 build failed' }
$dependencies = & dumpbin.exe /nologo /dependents "$output\GTA American Wasteland.exe"
if ($LASTEXITCODE -ne 0 -or ($dependencies -match '(?i)(vcruntime|msvcp|ucrtbase).*\.dll')) {
    throw 'Launcher must not require an installed Visual C++ runtime.'
}
$sources = foreach ($name in @('launcher.cpp','launcher_command.h','launcher_test.cpp','build.ps1')) {
    @{ path = "tools/gta/launcher_native/$name"; sha256 = (Get-FileHash -LiteralPath "$PSScriptRoot\$name" -Algorithm SHA256).Hash.ToLowerInvariant() }
}
@{
    format = 'gta-aw-launcher-build-v1'
    machine = 'amd64'
    executable = 'GTA American Wasteland.exe'
    bytes = (Get-Item -LiteralPath "$output\GTA American Wasteland.exe").Length
    sha256 = (Get-FileHash -LiteralPath "$output\GTA American Wasteland.exe" -Algorithm SHA256).Hash.ToLowerInvariant()
    tests_passed = $true
    sources = @($sources)
} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath "$output\launcher-build.json" -Encoding UTF8
