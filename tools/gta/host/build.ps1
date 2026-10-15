param(
    [Parameter(Mandatory = $true)][string]$CargoDirectory,
    [Parameter(Mandatory = $true)][string]$RustupDirectory,
    [Parameter(Mandatory = $true)][string]$TargetDirectory,
    [ValidateSet('build', 'test')][string]$Action = 'build',
    [string]$Toolchain = 'stable-x86_64-pc-windows-msvc'
)
$ErrorActionPreference = 'Stop'
$toolchainBin = Join-Path $RustupDirectory "toolchains\$Toolchain\bin"
$cargoExe = Join-Path $toolchainBin 'cargo.exe'
if (-not (Test-Path -LiteralPath $cargoExe -PathType Leaf)) {
    throw "Cargo executable not found: $cargoExe"
}
if (-not [System.IO.Path]::IsPathRooted($TargetDirectory) -or
    [System.IO.Path]::GetPathRoot($TargetDirectory).Length -lt 3) {
    throw 'TargetDirectory must be an absolute path to the separate GTA build directory.'
}
$oldCargo = $env:CARGO_HOME
$oldRustup = $env:RUSTUP_HOME
$oldPath = $env:PATH
try {
    $env:CARGO_HOME = $CargoDirectory
    $env:RUSTUP_HOME = $RustupDirectory
    $env:PATH = "$toolchainBin;$oldPath"
    & $cargoExe $Action --offline --locked --manifest-path (Join-Path $PSScriptRoot 'Cargo.toml') --target-dir $TargetDirectory
    if ($LASTEXITCODE -ne 0) { throw "Cargo $Action failed with exit code $LASTEXITCODE" }
} finally {
    $env:CARGO_HOME = $oldCargo
    $env:RUSTUP_HOME = $oldRustup
    $env:PATH = $oldPath
}
