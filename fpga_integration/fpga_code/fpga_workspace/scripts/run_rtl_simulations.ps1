param(
    [string]$VivadoBin = 'C:\vivado\Vivado\2018.3\bin',
    [Parameter(Mandatory = $true)][string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
$rtlDirectory = Join-Path (Split-Path $PSScriptRoot -Parent) 'rtl'
$outputPath = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $outputPath -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $rtlDirectory 'vectors.txt') -Destination $outputPath
$sources = @('motion_guard.sv', 'link_watchdog.sv', 'axi_lite_guard.sv',
    'tb_motion_guard.sv', 'tb_link_watchdog.sv', 'tb_axi_lite_guard.sv',
    'axi_lcd.sv', 'tb_axi_lcd.sv') |
    ForEach-Object { Join-Path $rtlDirectory $_ }
$scenarios = @(
    @{ Top = 'tb_link_watchdog'; Pass = 'PASS: watchdog periodic session' },
    @{ Top = 'tb_axi_lite_guard'; Pass = 'PASS: AXI4-Lite register/core checks' },
    @{ Top = 'tb_motion_guard'; Pass = 'PASS 1678 motion guard vectors' },
    @{ Top = 'tb_axi_lcd'; Pass = 'PASS: LCD AXI, palette, color bars, 768000 pixels and two vblank flips' }
)

Push-Location $outputPath
try {
    & (Join-Path $VivadoBin 'xvlog.bat') --sv -i $rtlDirectory @sources *> compile.txt
    if ($LASTEXITCODE -ne 0) { throw 'xvlog failed; see compile.txt' }
    foreach ($scenario in $scenarios) {
        $top = $scenario.Top
        & (Join-Path $VivadoBin 'xelab.bat') "work.$top" --timescale 1ns/1ps -s $top *> "${top}_elaborate.txt"
        if ($LASTEXITCODE -ne 0) { throw "xelab failed: $top" }
        & (Join-Path $VivadoBin 'xsim.bat') $top -runall *> "${top}_run.txt"
        if ($LASTEXITCODE -ne 0) { throw "xsim failed: $top" }
        $text = Get-Content -LiteralPath "${top}_run.txt" -Raw
        if ($text -match '(?im)^\s*(Fatal:|FATAL:|ERROR:)' -or
            -not $text.Contains($scenario.Pass)) { throw "Simulation did not pass: $top" }
        $text.Split("`n") | Where-Object { $_.Contains($scenario.Pass) }
    }
} finally {
    Pop-Location
}
