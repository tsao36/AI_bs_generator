$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$version = "0.1.15"
$packageName = "AITextExpandLocalLLM-v$version"
$distDir = Join-Path $projectRoot "dist"
$stagingRoot = Join-Path ([System.IO.Path]::GetTempPath()) "$packageName-package"
$stagingDir = Join-Path $stagingRoot $packageName
$zipPath = Join-Path $distDir "$packageName.zip"

function Add-LauncherExecutable($targetCmd, $outputExe)
{
    $escapedTarget = $targetCmd.Replace("\", "\\").Replace('"', '\"')
    $source = @"
using System;
using System.Diagnostics;
using System.IO;

public class Program
{
    public static int Main()
    {
        string baseDir = AppDomain.CurrentDomain.BaseDirectory;
        string target = Path.Combine(baseDir, "$escapedTarget");

        if (!File.Exists(target))
        {
            Console.Error.WriteLine("Required file was not found: " + target);
            Console.WriteLine("Press any key to exit...");
            Console.ReadKey(true);
            return 1;
        }

        ProcessStartInfo startInfo = new ProcessStartInfo();
        startInfo.FileName = "cmd.exe";
        startInfo.Arguments = "/c \"" + target + "\"";
        startInfo.WorkingDirectory = baseDir;
        startInfo.UseShellExecute = false;

        using (Process process = Process.Start(startInfo))
        {
            process.WaitForExit();
            return process.ExitCode;
        }
    }
}
"@

    Add-Type -TypeDefinition $source -Language CSharp -OutputAssembly $outputExe -OutputType ConsoleApplication
}

if (Test-Path $stagingRoot) {
    Remove-Item $stagingRoot -Recurse -Force
}
if (-not (Test-Path $distDir)) {
    New-Item -ItemType Directory -Path $distDir | Out-Null
}

New-Item -ItemType Directory -Path $stagingDir | Out-Null

$itemsToPackage = @(
    "Install.cmd",
    "ahk",
    "scripts",
    "src",
    "config.example.json",
    "pyproject.toml",
    "requirements.txt",
    "README.md"
)

foreach ($item in $itemsToPackage) {
    $source = Join-Path $projectRoot $item
    if (-not (Test-Path $source)) {
        throw "Required package item not found: $source"
    }

    Copy-Item $source -Destination $stagingDir -Recurse -Force
}

Add-LauncherExecutable "Install.cmd" (Join-Path $stagingDir "01_Setup_and_Start.exe")

Get-ChildItem $stagingDir -Directory -Recurse -Force |
    Where-Object { $_.Name -in @("__pycache__", ".pytest_cache") } |
    Remove-Item -Recurse -Force

Get-ChildItem $stagingDir -File -Recurse -Force |
    Where-Object { $_.Name -like "*.pyc" -or $_.Name -like "*.pyo" } |
    Remove-Item -Force

if (Test-Path $zipPath) {
    Remove-Item $zipPath -Force
}

Get-ChildItem $distDir -Filter "*.zip" -File | Where-Object { $_.FullName -ne $zipPath } | Remove-Item -Force

Compress-Archive -Path $stagingDir -DestinationPath $zipPath
Remove-Item $stagingRoot -Recurse -Force

Write-Host "Package created: $zipPath"
Write-Host "Share this zip with users. They should extract it, then double-click 01_Setup_and_Start.exe."