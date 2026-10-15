param(
    [string]$CargoDirectory = $(if ($env:CARGO_HOME) {$env:CARGO_HOME} else {Join-Path $env:USERPROFILE '.cargo'}),
    [string]$RustupDirectory = $(if ($env:RUSTUP_HOME) {$env:RUSTUP_HOME} else {Join-Path $env:USERPROFILE '.rustup'}),
    [string]$TargetDirectory = "$PSScriptRoot\..\..\..\.build\gta-runtime",
    [string]$OutputDirectory = "$PSScriptRoot\..\..\..\build\gta-runtime",
    [ValidateSet('build','test')][string]$Action = 'build'
)
$ErrorActionPreference = 'Stop'
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$installation = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if ($LASTEXITCODE -ne 0 -or !$installation) { throw 'Visual Studio C++ x86 build tools are required.' }
$vcvars = Join-Path $installation 'VC\Auxiliary\Build\vcvars32.bat'
$environment = & $env:ComSpec /d /c "call `"$vcvars`" >nul && set"
if ($LASTEXITCODE -ne 0) { throw 'vcvars32 failed' }
$seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($line in $environment) {
    if ($line -match '^([^=]+)=(.*)$' -and $seen.Add($matches[1])) {
        [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process')
    }
}
$toolchainBin = Join-Path $RustupDirectory 'toolchains\stable-x86_64-pc-windows-msvc\bin'
$env:CARGO_HOME = $CargoDirectory
$env:RUSTUP_HOME = $RustupDirectory
$env:PATH = "$toolchainBin;$env:PATH"
$env:RUSTFLAGS = '-C target-feature=+crt-static'
$tlsOutput = Join-Path ([IO.Path]::GetFullPath($TargetDirectory)) 'tls'
New-Item -ItemType Directory -Force -Path $tlsOutput | Out-Null
& cl.exe /nologo /c /O2 /GS- /Zl "/Fo$tlsOutput\tls.obj" "$PSScriptRoot\tls.c"
if ($LASTEXITCODE -ne 0) { throw 'Owned TLS metadata compilation failed' }
& link.exe /nologo /DLL /NOENTRY /NODEFAULTLIB /MACHINE:X86 /INCLUDE:__tls_used /DYNAMICBASE /NXCOMPAT /SAFESEH /Brepro /BASE:0x65000000 "/OUT:$tlsOutput\gta-thaw-tls.dll" "/IMPLIB:$tlsOutput\tls.lib" "$tlsOutput\tls.obj"
if ($LASTEXITCODE -ne 0) { throw 'Owned TLS metadata linking failed' }
$env:THAW_TLS_SHA = (Get-FileHash -LiteralPath "$tlsOutput\gta-thaw-tls.dll" -Algorithm SHA256).Hash.ToLowerInvariant()
& (Join-Path $toolchainBin 'cargo.exe') test --offline --locked --release --manifest-path (Join-Path $PSScriptRoot 'Cargo.toml') --target i686-pc-windows-msvc --target-dir $TargetDirectory
if ($LASTEXITCODE -ne 0) { throw 'Rust host tests failed' }
if ($Action -eq 'build') {
    & (Join-Path $toolchainBin 'cargo.exe') build --offline --locked --release --manifest-path (Join-Path $PSScriptRoot 'Cargo.toml') --target i686-pc-windows-msvc --target-dir $TargetDirectory
    if ($LASTEXITCODE -ne 0) { throw 'Rust host build failed' }
    $release = Join-Path ([IO.Path]::GetFullPath($TargetDirectory)) 'i686-pc-windows-msvc\release'
    Copy-Item -LiteralPath "$tlsOutput\gta-thaw-tls.dll" -Destination "$release\gta-thaw-tls.dll"
    $output = [IO.Path]::GetFullPath($OutputDirectory)
    New-Item -ItemType Directory -Force -Path $output | Out-Null
    $dependencies = & dumpbin.exe /nologo /dependents "$release\gta-thaw-runtime.exe"
    if ($LASTEXITCODE -ne 0 -or ($dependencies -join "`n") -match '(?i)(vcruntime|msvcp|ucrtbase)\d*\.dll') {
        throw 'Rust host requires an external VC runtime or dependency inspection failed'
    }
    Copy-Item -LiteralPath "$release\gta-thaw-runtime.exe" -Destination "$output\gta-thaw-runtime.exe"
    Copy-Item -LiteralPath "$release\gta-thaw-tls.dll" -Destination "$output\gta-thaw-tls.dll"
    $sourceFiles = foreach ($relative in @('Cargo.toml','Cargo.lock','build.rs','build.ps1','README.md','tls.c','src/main.rs','src/image.rs','src/file_events.rs')) {
        $path = Join-Path $PSScriptRoot $relative
        @{path="tools/gta/runtime_host/$relative";bytes=(Get-Item -LiteralPath $path).Length;sha256=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()}
    }
    @{
        format='gta-thaw-rust-host-build-v1';executable='gta-thaw-runtime.exe';machine='i686';tests_passed=$true
        bytes=(Get-Item -LiteralPath "$output\gta-thaw-runtime.exe").Length
        sha256=(Get-FileHash -LiteralPath "$output\gta-thaw-runtime.exe" -Algorithm SHA256).Hash.ToLowerInvariant()
        tls=@{file='gta-thaw-tls.dll';bytes=(Get-Item -LiteralPath "$output\gta-thaw-tls.dll").Length;sha256=$env:THAW_TLS_SHA}
        source_files=@($sourceFiles);static_crt=$true;original_game_files_embedded=$false
        original_process_created=$false;original_entrypoint_called=$false;image_base=4194304;image_size=25481216
        file_events_isolated=$true
        built_utc=[DateTime]::UtcNow.ToString('o')
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath "$output\host-build.json" -Encoding UTF8
    Write-Host "Tested Rust host published to $output"
}
