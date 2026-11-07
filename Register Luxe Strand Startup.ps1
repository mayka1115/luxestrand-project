$ErrorActionPreference = "Stop"

try {
    $launcherPath = Join-Path $PSScriptRoot "Start Luxe Strand.bat"
    if (-not (Test-Path -LiteralPath $launcherPath -PathType Leaf)) {
        throw "Launcher not found: $launcherPath"
    }

    $startupFolder = [Environment]::GetFolderPath("Startup")
    if (-not $startupFolder) {
        throw "Could not locate the current user's Startup folder."
    }

    $shortcutPath = Join-Path $startupFolder "Luxe Strand.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $launcherPath
    $shortcut.WorkingDirectory = $PSScriptRoot
    $shortcut.Description = "Start the Luxe Strand website when you sign in."
    $shortcut.Save()
} catch {
    Write-Error "Could not register Luxe Strand to start with Windows: $_"
    exit 1
}
