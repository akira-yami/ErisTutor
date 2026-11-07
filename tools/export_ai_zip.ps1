# Run export_ai_zip.bat from the project root. No Python or Git required.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
$outputDir = Join-Path $repoRoot 'exports'
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss_fff'
$zipPath = Join-Path $outputDir "eris-ai-$stamp.zip"
$partialPath = "$zipPath.partial"

# Only these project areas are considered; personal state at the root is excluded.
$includeDirs = @('src', 'docs/knowledge', 'tools', 'gemini_manager', 'installer', 'env-examples')
$skipDirs = @(
    'node_modules', '__pycache__', 'build', 'dist', 'out', 'bin', 'obj',
    'logs', 'log', 'cache', 'state', 'runtime_state', 'data', 'docker-data',
    'memory_core', 'embedding_model', 'paraphrase-multilingual-MiniLM-L12-v2',
    'portable_python', 'venv', 'secret_versions', 'browser_profile', 'browser_profiles',
    'profiles', 'tts_vocal_cache', 'raw_stream_samples',
    'WebAI-to-API', 'WebAI-to-API-2', 'reference-repos'
)
$extensions = @(
    '.py', '.pyi', '.cs', '.gd', '.tscn', '.tres', '.gdshader', '.uid',
    '.js', '.cjs', '.mjs', '.ts', '.tsx', '.jsx', '.html', '.css', '.scss',
    '.go', '.mod', '.sum', '.c', '.h', '.cpp', '.hpp', '.rs', '.sh',
    '.bat', '.cmd', '.ps1', '.iss', '.spec', '.sln', '.csproj', '.props', '.targets',
    '.md', '.rst', '.toml', '.ini', '.cfg', '.godot', '.json', '.yaml', '.yml', '.sql'
)
$rootExtensions = @('.py', '.bat', '.cmd', '.ps1', '.spec', '.sln', '.csproj', '.toml', '.ini', '.cfg', '.godot', '.md')
$specialNames = @('.gitignore', '.gitattributes', '.editorconfig', '.dockerignore', 'Dockerfile', 'LICENSE', 'LICENSE.txt', 'CMakeLists.txt')
$configFiles = @('config/example.json', 'config/secrets.example.json', 'config/low_level_speech_policy.yaml', 'config/SECRETS_SETUP.md')

function Test-ExportFile([System.IO.FileInfo] $File) {
    $name = $File.Name
    if ($File.Attributes -band [System.IO.FileAttributes]::ReparsePoint) { return $false }
    # Source modules such as eris_secrets.py stay; credential DATA files do not.
    if ($name -match '(?i)(\.bak($|[._-])|\.backup($|[._-])|\.local\.)') { return $false }
    if ($name.StartsWith('.env', [System.StringComparison]::OrdinalIgnoreCase)) {
        return $name.EndsWith('.example', [System.StringComparison]::OrdinalIgnoreCase)
    }
    if ($name.StartsWith('.') -and $name -notin $specialNames) { return $false }
    if ($File.Extension -in @('.json', '.yaml', '.yml', '.ini', '.cfg')) {
        if ($name -match '(?i)(secret|credential|cookie|token|account|keyring|storage.state|auth.state)' -and
            $name -notmatch '(?i)\.example\.') { return $false }
    }
    return ($File.Extension -in $extensions -or $name -in $specialNames -or $name -like 'requirements*.txt')
}

function Get-ExportFiles([string] $Directory) {
    foreach ($item in Get-ChildItem -LiteralPath $Directory -Force) {
        if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) { continue }
        if ($item.PSIsContainer) {
            if ($item.Name.StartsWith('.') -or $item.Name -in $skipDirs) { continue }
            # This is a generated gateway copy; the real source lives in tools/whale2api.
            if ($item.FullName -eq (Join-Path $repoRoot 'src/tools/whale2api')) { continue }
            Get-ExportFiles $item.FullName
        } elseif (Test-ExportFile $item) {
            $item
        }
    }
}

$archive = $null
$stream = $null
$createdPartial = $false
try {
    Write-Host 'Collecting source code and project documentation...'
    $files = @(
        foreach ($file in Get-ChildItem -LiteralPath $repoRoot -File -Force) {
            if (($file.Extension -in $rootExtensions -or $file.Name -in $specialNames -or
                $file.Name -like 'requirements*.txt' -or $file.Name -eq '.env.example') -and
                (Test-ExportFile $file)) { $file }
        }
        foreach ($relativeDir in $includeDirs) {
            $dir = Join-Path $repoRoot $relativeDir
            if (Test-Path -LiteralPath $dir -PathType Container) {
                if ((Get-Item -LiteralPath $dir -Force).Attributes -band [System.IO.FileAttributes]::ReparsePoint) { continue }
                Get-ExportFiles $dir
            }
        }
        foreach ($relativeFile in $configFiles) {
            $filePath = Join-Path $repoRoot $relativeFile
            if (Test-Path -LiteralPath $filePath -PathType Leaf) {
                $file = Get-Item -LiteralPath $filePath
                if (Test-ExportFile $file) { $file }
            }
        }
    ) | Sort-Object -Property FullName -Unique
    if (-not $files) { throw 'No source files found.' }

    New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $stream = [System.IO.File]::Open($partialPath, [System.IO.FileMode]::CreateNew)
    $createdPartial = $true
    $archive = [System.IO.Compression.ZipArchive]::new($stream, [System.IO.Compression.ZipArchiveMode]::Create)
    $paths = [System.Collections.Generic.List[string]]::new()
    foreach ($file in $files) {
        $relative = $file.FullName.Substring($repoRoot.Length + 1).Replace('\', '/')
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $archive, $file.FullName, "eris/$relative", [System.IO.Compression.CompressionLevel]::Optimal
        ) | Out-Null
        $paths.Add($relative)
    }
    $entry = $archive.CreateEntry('eris/AI_EXPORT_README.txt')
    $writer = [System.IO.StreamWriter]::new($entry.Open(), [System.Text.UTF8Encoding]::new($false))
    try {
        $writer.WriteLine('Eris source snapshot for external AI code review.')
        $writer.WriteLine('Read AGENTS.md and docs/knowledge first; compare historical notes with current code.')
        $writer.WriteLine('Contains current working files, including uncommitted changes.')
        $writer.WriteLine('Excludes local state, known credential files, binaries, media, caches and model weights.')
        $writer.WriteLine('This is a code review snapshot, not a complete runnable distribution.')
        $writer.WriteLine('Filename filters do not detect secrets hardcoded inside source. Review before sharing.')
        $writer.WriteLine('')
        $writer.WriteLine("Included files: $($paths.Count)")
        foreach ($path in $paths) { $writer.WriteLine($path) }
    } finally { $writer.Dispose() }
    $archive.Dispose()
    $archive = $null
    $stream.Dispose()
    $stream = $null
    Move-Item -LiteralPath $partialPath -Destination $zipPath
    $size = [Math]::Round((Get-Item -LiteralPath $zipPath).Length / 1MB, 2)
    Write-Host "Done: $($paths.Count) files, $size MB"
    Write-Host "ZIP: $zipPath"
    exit 0
} catch {
    if ($null -ne $archive) { $archive.Dispose() }
    if ($null -ne $stream) { $stream.Dispose() }
    if ($createdPartial -and (Test-Path -LiteralPath $partialPath)) {
        Remove-Item -LiteralPath $partialPath -Force
    }
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
