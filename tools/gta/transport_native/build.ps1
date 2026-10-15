param([string]$OutputDirectory = "$PSScriptRoot\..\..\..\build\gta-transport", [string]$VcVars)
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
& cl.exe /nologo /std:c++17 /W4 /WX /O2 /MT /EHsc "/Fo$output\atomic_store_test.obj" "/Fe$output\atomic_store_test.exe" "$PSScriptRoot\atomic_store_test.cpp"
if ($LASTEXITCODE -ne 0) { throw 'Atomic transport test build failed' }
& "$output\atomic_store_test.exe"
if ($LASTEXITCODE -ne 0) { throw 'Atomic transport test failed' }
& cl.exe /nologo /std:c++17 /W4 /WX /O2 /MT /LD /EHsc "/Fo$output\atomic_store.obj" "$PSScriptRoot\atomic_store.cpp" /link "/OUT:$output\thaw-gta-atomic.dll" "/IMPLIB:$output\thaw-gta-atomic.lib"
if ($LASTEXITCODE -ne 0) { throw 'Atomic transport x64 DLL build failed' }
$dependencies = & dumpbin.exe /nologo /dependents "$output\thaw-gta-atomic.dll"
if ($LASTEXITCODE -ne 0 -or ($dependencies -match '(?i)(vcruntime|msvcp|ucrtbase).*\.dll')) {
    throw 'Atomic transport DLL must not require an installed Visual C++ runtime.'
}
$sources = foreach ($name in @('atomic_store.cpp','atomic_store_test.cpp','build.ps1')) {
    @{ path = "tools/gta/transport_native/$name"; sha256 = (Get-FileHash -LiteralPath "$PSScriptRoot\$name" -Algorithm SHA256).Hash.ToLowerInvariant() }
}
@{
    format = 'gta-thaw-atomic-build-v1'
    machine = 'amd64'
    dll = 'thaw-gta-atomic.dll'
    bytes = (Get-Item -LiteralPath "$output\thaw-gta-atomic.dll").Length
    sha256 = (Get-FileHash -LiteralPath "$output\thaw-gta-atomic.dll" -Algorithm SHA256).Hash.ToLowerInvariant()
    tests_passed = $true
    sources = @($sources)
} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath "$output\transport-build.json" -Encoding UTF8
