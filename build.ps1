param(
    [string]$OutputDirectory = $PSScriptRoot,
    [string]$ScratchDirectory
)

if (-not $ScratchDirectory) {
    $ScratchDirectory = Join-Path $env:TEMP 'market_dashboard_build'
}

$ErrorActionPreference = 'Stop'
$frameworkDirectory = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
$compiler = Join-Path $frameworkDirectory 'csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) {
    $frameworkDirectory = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319'
    $compiler = Join-Path $frameworkDirectory 'csc.exe'
}
if (-not (Test-Path -LiteralPath $compiler)) { throw '.NET Framework C# 컴파일러를 찾을 수 없습니다.' }

$outputFolder = [IO.Path]::GetFullPath($OutputDirectory)
$scratchFolder = [IO.Path]::GetFullPath($ScratchDirectory)
[IO.Directory]::CreateDirectory($outputFolder) | Out-Null
[IO.Directory]::CreateDirectory($scratchFolder) | Out-Null
$executable = Join-Path $outputFolder 'MarketDashboard.exe'
$responsePath = Join-Path $scratchFolder 'compile.rsp'
$logPath = Join-Path $scratchFolder 'build.log'
$xamlPath = Join-Path $PSScriptRoot 'src\Dashboard.xaml'
$manifestPath = Join-Path $PSScriptRoot 'app.manifest'
$iconPath = Join-Path $PSScriptRoot 'app.ico'

$arguments = @(
    '/nologo', '/target:winexe', '/platform:anycpu',
    '/optimize+', '/debug-', '/langversion:5', '/codepage:65001', '/utf8output',
    '/main:MarketDash.App', ('/out:"{0}"' -f $executable),
    ('/win32manifest:"{0}"' -f $manifestPath),
    ('/win32icon:"{0}"' -f $iconPath),
    ('/resource:"{0}",MarketDash.Dashboard.xaml' -f $xamlPath)
)

foreach ($reference in @('mscorlib.dll', 'System.dll', 'System.Core.dll', 'System.Configuration.dll', 'System.Xml.dll', 'System.Xaml.dll', 'System.Drawing.dll', 'System.Windows.Forms.dll', 'WindowsBase.dll', 'PresentationCore.dll', 'PresentationFramework.dll')) {
    $referencePath = Join-Path $frameworkDirectory $reference
    if (-not (Test-Path -LiteralPath $referencePath)) {
        $referencePath = Join-Path (Join-Path $frameworkDirectory 'WPF') $reference
    }
    if (-not (Test-Path -LiteralPath $referencePath)) { throw "프레임워크 참조를 찾을 수 없습니다: $reference" }
    $arguments += '/reference:"{0}"' -f $referencePath
}

foreach ($source in (Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'src') -Filter '*.cs' -File | Sort-Object Name)) {
    $arguments += '"{0}"' -f $source.FullName
}
[IO.File]::WriteAllLines($responsePath, $arguments, [Text.UTF8Encoding]::new($true))
& $compiler '/noconfig' "@$responsePath" 2>&1 | Tee-Object -FilePath $logPath
if ($LASTEXITCODE -ne 0) { throw "빌드 실패. 로그: $logPath" }

$configSource = Join-Path $PSScriptRoot 'MarketDashboard.exe.config'
$configTarget = Join-Path $outputFolder 'MarketDashboard.exe.config'
if (-not [string]::Equals($configSource, $configTarget, [StringComparison]::OrdinalIgnoreCase)) {
    Copy-Item -LiteralPath $configSource -Destination $configTarget -Force
}
Write-Output "Release EXE: $executable"
