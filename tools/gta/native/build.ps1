param([string]$OutputDirectory = "$PSScriptRoot\..\..\..\build\gta-native", [string]$VcVars)
$ErrorActionPreference = 'Stop'
if (!$VcVars) {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (!(Test-Path -LiteralPath $vswhere)) { throw 'Supply -VcVars with the full path to vcvars32.bat.' }
    $installation = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if ($LASTEXITCODE -ne 0 -or !$installation) { throw 'Visual Studio C++ x86 build tools are required.' }
    $VcVars = Join-Path $installation 'VC\Auxiliary\Build\vcvars32.bat'
}
$vcvars = [IO.Path]::GetFullPath($VcVars)
if (!(Test-Path -LiteralPath $vcvars)) { throw "Missing x86 toolchain environment: $vcvars" }
$environment = & $env:ComSpec /d /c "call `"$vcvars`" >nul && set"
if ($LASTEXITCODE -ne 0) { throw 'vcvars32 failed' }
$imported = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($line in $environment) {
    if ($line -match '^([^=]+)=(.*)$' -and $imported.Add($matches[1])) {
        [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process')
    }
}
$output = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $output | Out-Null
& cl.exe /nologo /std:c++17 /W4 /O2 /MT /EHsc /D_CRT_SECURE_NO_WARNINGS "/Fo$output\protocol_test.obj" "/Fe$output\protocol_test.exe" "$PSScriptRoot\protocol_test.cpp"
if ($LASTEXITCODE -ne 0) { throw 'Protocol test build failed' }
& "$output\protocol_test.exe"
if ($LASTEXITCODE -ne 0) { throw 'Protocol test failed' }
& cl.exe /nologo /std:c++17 /W4 /O2 /MT /EHsc /D_CRT_SECURE_NO_WARNINGS "/Fo$output\pose_preview_test.obj" "/Fe$output\pose_preview_test.exe" "$PSScriptRoot\pose_preview_test.cpp"
if ($LASTEXITCODE -ne 0) { throw 'Pose preview test build failed' }
& "$output\pose_preview_test.exe"
if ($LASTEXITCODE -ne 0) { throw 'Pose preview test failed' }
& cl.exe /nologo /std:c++17 /W4 /O2 /MT /EHsc /D_CRT_SECURE_NO_WARNINGS "/Fo$output\board_attack_test.obj" "/Fe$output\board_attack_test.exe" "$PSScriptRoot\board_attack_test.cpp"
if ($LASTEXITCODE -ne 0) { throw 'Board attack test build failed' }
& "$output\board_attack_test.exe"
if ($LASTEXITCODE -ne 0) { throw 'Board attack test failed' }
foreach ($test in @('weapon_control','weapon_presentation','aim_input','vehicle_snapshot','aim_target','movement_ped_state_policy','presentation_time','game_effects')) {
    & cl.exe /nologo /std:c++17 /W4 /O2 /MT /EHsc /D_CRT_SECURE_NO_WARNINGS "/Fo$output\${test}_test.obj" "/Fe$output\${test}_test.exe" "$PSScriptRoot\${test}_test.cpp"
    if ($LASTEXITCODE -ne 0) { throw "$test test build failed" }
    & "$output\${test}_test.exe"
    if ($LASTEXITCODE -ne 0) { throw "$test test failed" }
}
& cl.exe /nologo /std:c++17 /W4 /O2 /MT /EHsc /D_CRT_SECURE_NO_WARNINGS "/Fo$output\board_preview_test.obj" "/Fe$output\board_preview_test.exe" "$PSScriptRoot\board_preview_test.cpp"
if ($LASTEXITCODE -ne 0) { throw 'Board preview test build failed' }
& "$output\board_preview_test.exe"
if ($LASTEXITCODE -ne 0) { throw 'Board preview test failed' }
& cl.exe /nologo /std:c++17 /W4 /O2 /MT /EHsc /D_CRT_SECURE_NO_WARNINGS "/Fo$output\movement_hold_test.obj" "/Fe$output\movement_hold_test.exe" "$PSScriptRoot\movement_hold_test.cpp"
if ($LASTEXITCODE -ne 0) { throw 'Movement hold test build failed' }
& "$output\movement_hold_test.exe"
if ($LASTEXITCODE -ne 0) { throw 'Movement hold test failed' }
& cl.exe /nologo /std:c++17 /W4 /O2 /MT /EHsc /D_CRT_SECURE_NO_WARNINGS "/Fo$output\camera_preview_test.obj" "/Fe$output\camera_preview_test.exe" "$PSScriptRoot\camera_preview_test.cpp"
if ($LASTEXITCODE -ne 0) { throw 'Camera preview test build failed' }
& "$output\camera_preview_test.exe"
if ($LASTEXITCODE -ne 0) { throw 'Camera preview test failed' }
& cl.exe /nologo /std:c++17 /W4 /O2 /MT /EHsc /D_CRT_SECURE_NO_WARNINGS "/Fo$output\hud_preview_test.obj" "/Fe$output\hud_preview_test.exe" "$PSScriptRoot\hud_preview_test.cpp"
if ($LASTEXITCODE -ne 0) { throw 'HUD preview test build failed' }
& "$output\hud_preview_test.exe"
if ($LASTEXITCODE -ne 0) { throw 'HUD preview test failed' }
& cl.exe /nologo /std:c++17 /W4 /O2 /MT /EHsc /D_CRT_SECURE_NO_WARNINGS "/Fo$output\startup_status_test.obj" "/Fe$output\startup_status_test.exe" "$PSScriptRoot\startup_status_test.cpp"
if ($LASTEXITCODE -ne 0) { throw 'Startup status test build failed' }
& "$output\startup_status_test.exe"
if ($LASTEXITCODE -ne 0) { throw 'Startup status test failed' }
& cl.exe /nologo /std:c++17 /W4 /O2 /MT /EHsc /D_CRT_SECURE_NO_WARNINGS "/Fo$output\settings_menu_test.obj" "/Fe$output\settings_menu_test.exe" "$PSScriptRoot\settings_menu_test.cpp"
if ($LASTEXITCODE -ne 0) { throw 'Settings menu test build failed' }
& "$output\settings_menu_test.exe"
if ($LASTEXITCODE -ne 0) { throw 'Settings menu test failed' }
& cl.exe /nologo /c /std:c++17 /W4 /O2 /MT /EHsc /D_CRT_SECURE_NO_WARNINGS "/Fo$output\settings_menu.obj" "$PSScriptRoot\settings_menu.cpp"
if ($LASTEXITCODE -ne 0) { throw 'Settings menu x86 build failed' }
& cl.exe /nologo /c /std:c++17 /W4 /O2 /MT /EHsc /D_CRT_SECURE_NO_WARNINGS "/Fo$output\startup_status.obj" "$PSScriptRoot\startup_status.cpp"
if ($LASTEXITCODE -ne 0) { throw 'Startup status x86 build failed' }
& cl.exe /nologo /c /std:c++17 /W4 /O2 /MT /EHsc /D_CRT_SECURE_NO_WARNINGS "/Fo$output\hud_preview.obj" "$PSScriptRoot\hud_preview.cpp"
if ($LASTEXITCODE -ne 0) { throw 'HUD preview x86 build failed' }
& cl.exe /nologo /c /std:c++17 /W4 /O2 /MT /EHsc /D_CRT_SECURE_NO_WARNINGS "/Fo$output\camera_preview.obj" "$PSScriptRoot\camera_preview.cpp"
if ($LASTEXITCODE -ne 0) { throw 'Camera preview x86 build failed' }
& cl.exe /nologo /c /std:c++17 /W4 /O2 /MT /EHsc /D_CRT_SECURE_NO_WARNINGS "/Fo$output\movement_hold.obj" "$PSScriptRoot\movement_hold.cpp"
if ($LASTEXITCODE -ne 0) { throw 'Movement hold x86 build failed' }
& cl.exe /nologo /c /std:c++17 /W4 /O2 /MT /EHsc /D_CRT_SECURE_NO_WARNINGS "/Fo$output\board_preview.obj" "$PSScriptRoot\board_preview.cpp"
if ($LASTEXITCODE -ne 0) { throw 'Board preview x86 build failed' }
& cl.exe /nologo /c /std:c++17 /W4 /O2 /MT /EHsc /D_CRT_SECURE_NO_WARNINGS "/Fo$output\pose_preview.obj" "$PSScriptRoot\pose_preview.cpp"
if ($LASTEXITCODE -ne 0) { throw 'Pose preview x86 build failed' }
& cl.exe /nologo /c /std:c++17 /W4 /O2 /MT /EHsc /D_CRT_SECURE_NO_WARNINGS "/Fo$output\board_attack.obj" "$PSScriptRoot\board_attack.cpp"
if ($LASTEXITCODE -ne 0) { throw 'Board attack x86 build failed' }
foreach ($module in @('weapon_control','weapon_presentation','vehicle_snapshot','game_effects')) {
    & cl.exe /nologo /c /std:c++17 /W4 /O2 /MT /EHsc /D_CRT_SECURE_NO_WARNINGS "/Fo$output\$module.obj" "$PSScriptRoot\$module.cpp"
    if ($LASTEXITCODE -ne 0) { throw "$module x86 build failed" }
}
& cl.exe /nologo /std:c++17 /W4 /O2 /MT /LD /EHsc /D_CRT_SECURE_NO_WARNINGS "/Fo$output\thaw_gta.obj" "$PSScriptRoot\thaw_gta.cpp" "$output\pose_preview.obj" "$output\board_preview.obj" "$output\movement_hold.obj" "$output\camera_preview.obj" "$output\hud_preview.obj" "$output\startup_status.obj" "$output\settings_menu.obj" "$output\board_attack.obj" "$output\weapon_control.obj" "$output\weapon_presentation.obj" "$output\vehicle_snapshot.obj" "$output\game_effects.obj" /link "/DEF:$PSScriptRoot\thaw_gta.def" "/OUT:$output\thaw_gta.asi" "/IMPLIB:$output\thaw_gta.lib" bcrypt.lib user32.lib
if ($LASTEXITCODE -ne 0) { throw 'GTA x86 ASI build failed' }
@{
    schema = 1
    format = 'gta-thaw-telemetry-adapter-v1'
    asi_sha256 = (Get-FileHash -LiteralPath "$output\thaw_gta.asi" -Algorithm SHA256).Hash.ToLowerInvariant()
    supported_exe_sha256 = 'a559aa772fd136379155efa71f00c47aad34bbfeae6196b0fe1047d0645cbd26'
    protocol = 'GTATHAW2'
    snapshot_bytes = 272
    shared_mapping_bytes = 280
    pose_preview_protocol = 'GTAPOSE5'
    pose_preview_packet_bytes = 5168
    board_preview_bundle = 'GTABRD02'
    board_attack_protocol = 'GTAHIT01'
    board_attack_packet_bytes = 104
    pose_preview_default_enabled = $false
    movement_hold_protocol = 'GTAHOLD1'
    movement_hold_packet_bytes = 72
    movement_hold_default_enabled = $false
    movement_command_protocol = 'GTAMOVE1'
    movement_command_packet_bytes = 104
    movement_command_default_enabled = $false
    movement_trajectory_protocol = 'GTAMOVE3'
    movement_trajectory_packet_bytes = 144
    movement_trajectory_default_enabled = $false
    movement_interactive_default_enabled = $false
    movement_interactive_continuous_duration_ms = 0
    movement_bounded_interactive_max_duration_ms = 30000
    camera_preview_protocol = 'GTACAM02'
    camera_preview_packet_bytes = 140
    camera_preview_default_enabled = $false
    aim_input_protocol = 'GTAAIM01'
    aim_input_packet_bytes = 96
    game_effects_protocol = 'GTAFX001'
    game_effects_packet_bytes = 104
    game_effects_max_real_seconds = 5
    vehicle_snapshot_protocol = 'GTAVEH01'
    vehicle_snapshot_packet_bytes = 184688
    vehicle_snapshot_max_vehicles = 16
    hud_preview_protocol = 'GTAHUD01'
    hud_preview_packet_bytes = 73860
    hud_preview_default_enabled = $false
    startup_status_protocol = 'GTASTAT1'
    startup_status_packet_bytes = 64
    settings_menu_protocol = 'GTAMENU1'
    settings_menu_packet_bytes = 2472
    settings_menu_input_packet_bytes = 320
    packet_tests_passed = $true
    gameplay_enabled = $false
    live_game_verified = $false
    built_utc = [DateTime]::UtcNow.ToString('o')
} | ConvertTo-Json | Set-Content -LiteralPath "$output\adapter-build.json" -Encoding UTF8
Write-Host 'Built only; no game was launched and no ASI was installed.'
