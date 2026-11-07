# Run export_ai_zip.bat from the project root. No Python or Git required.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
$outputDir = Join-Path $repoRoot 'exports'
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$zipPath = Join-Path $outputDir "ErisTutor_ai_export.zip"
$timestampedZipPath = Join-Path $outputDir "ErisTutor_ai_export_$stamp.zip"
$partialPath = "$zipPath.partial"

# Only core source code and documentation are included.
# Exclude tools, embeddings, binary assets, and personal state.
$includeDirs = @('src', 'docs', 'contracts', 'gemini_manager', 'tests')
$skipDirs = @(
    'tools', 'tool', 'node_modules', '__pycache__', 'build', 'dist', 'out', 'bin', 'obj',
    'logs', 'log', 'cache', 'state', 'runtime_state', 'data', 'docker-data',
    'memory', 'memory_core', 'embedding_model', 'paraphrase-multilingual-MiniLM-L12-v2',
    'portable_python', 'venv', '.venv', 'secret_versions', 'browser_profile', 'browser_profiles',
    'profiles', 'tts_vocal_cache', 'raw_stream_samples', 'asset', 'assets',
    'data_Eris_windows_x86_64', '.godot', '.pytest_cache', '.ruff_cache', '.eris_home', '.eris_state',
    'WebAI-to-API', 'WebAI-to-API-2', 'reference-repos', 'exports'
)

$skipExtensions = @(
    '.exe', '.pck', '.db', '.sqlite', '.sqlite3', '.db-shm', '.db-wal', '.png', '.jpg', '.jpeg',
    '.webp', '.gif', '.ico', '.bmp', '.ttf', '.otf', '.woff', '.woff2', '.wav', '.mp3', '.ogg',
    '.flac', '.ctex', '.dll', '.so', '.dylib', '.bin', '.key', '.pem', '.cert', '.zip', '.tar',
    '.gz', '.7z', '.rar', '.pdf', '.docx', '.import', '.pyc', '.pyo', '.pyd', '.moc3',
    '.model3.json', '.exp3.json', '.pose3.json', '.physics3.json', '.cdi3.json', '.motion3.json'
)

$extensions = @(
    '.py', '.pyi', '.cs', '.gd', '.tscn', '.tres', '.gdshader', '.uid',
    '.js', '.cjs', '.mjs', '.ts', '.tsx', '.jsx', '.html', '.css', '.scss',
    '.go', '.mod', '.sum', '.c', '.h', '.cpp', '.hpp', '.rs', '.sh',
    '.bat', '.cmd', '.ps1', '.iss', '.spec', '.sln', '.csproj', '.props', '.targets',
    '.md', '.rst', '.toml', '.ini', '.cfg', '.godot', '.json', '.yaml', '.yml', '.sql', '.patch'
)
$rootExtensions = @('.py', '.bat', '.cmd', '.ps1', '.spec', '.sln', '.csproj', '.toml', '.ini', '.cfg', '.godot', '.md')
$specialNames = @('.gitignore', '.gitattributes', '.editorconfig', '.dockerignore', 'Dockerfile', 'LICENSE', 'LICENSE.txt', 'CMakeLists.txt')
$configFiles = @('config/example.json', 'config/secrets.example.json', 'config/low_level_speech_policy.yaml', 'config/SECRETS_SETUP.md')

function Test-ExportFile([System.IO.FileInfo] $File) {
    $name = $File.Name
    $ext = $File.Extension.ToLower()

    if ($File.Attributes -band [System.IO.FileAttributes]::ReparsePoint) { return $false }
    if ($ext -in $skipExtensions) { return $false }

    if ($name -match '(?i)(\.bak($|[._-])|\.backup($|[._-])|\.local\.)') { return $false }
    if ($name.StartsWith('.env', [System.StringComparison]::OrdinalIgnoreCase)) {
        return $name.EndsWith('.example', [System.StringComparison]::OrdinalIgnoreCase)
    }
    if ($name.StartsWith('.') -and $name -notin $specialNames) { return $false }
    if ($ext -in @('.json', '.yaml', '.yml', '.ini', '.cfg')) {
        if ($name -match '(?i)(secret|credential|cookie|token|account|keyring|storage.state|auth.state)' -and
            $name -notmatch '(?i)\.example\.') { return $false }
    }
    return ($ext -in $extensions -or $name -in $specialNames -or $name -like 'requirements*.txt')
}

function Get-ExportFiles([string] $Directory) {
    foreach ($item in Get-ChildItem -LiteralPath $Directory -Force) {
        if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) { continue }
        if ($item.PSIsContainer) {
            $lowerName = $item.Name.ToLower()
            if ($item.Name.StartsWith('.') -or 
                $item.Name -in $skipDirs -or 
                $lowerName -in $skipDirs -or 
                $lowerName -like '*tool*' -or 
                $lowerName -like '*embed*' -or 
                $lowerName -like '*paraphrase*') { 
                continue 
            }
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
    if (Test-Path -LiteralPath $partialPath) { Remove-Item -LiteralPath $partialPath -Force }
    if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }

    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $stream = [System.IO.File]::Open($partialPath, [System.IO.FileMode]::CreateNew)
    $createdPartial = $true
    $archive = [System.IO.Compression.ZipArchive]::new($stream, [System.IO.Compression.ZipArchiveMode]::Create)
    $paths = [System.Collections.Generic.List[string]]::new()
    foreach ($file in $files) {
        $relative = $file.FullName.Substring($repoRoot.Length + 1).Replace('\', '/')
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $archive, $file.FullName, $relative, [System.IO.Compression.CompressionLevel]::Optimal
        ) | Out-Null
        $paths.Add($relative)
    }

    $entry = $archive.CreateEntry('AI_EXPORT_README.txt')
    $writer = [System.IO.StreamWriter]::new($entry.Open(), [System.Text.UTF8Encoding]::new($false))
    try {
        $writer.WriteLine('ErisTutor source snapshot for Web AI code analysis.')
        $writer.WriteLine('Excluded: tools/ folder, embedding models/caches, binaries, caches, and secrets.')
        $writer.WriteLine('')
        $writer.WriteLine("Generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
        $writer.WriteLine("Included files count: $($paths.Count)")
        $writer.WriteLine('')
        $writer.WriteLine('Files list:')
        foreach ($path in $paths) { $writer.WriteLine(" - $path") }
    } finally { $writer.Dispose() }

    $archive.Dispose()
    $archive = $null
    $stream.Dispose()
    $stream = $null

    Move-Item -LiteralPath $partialPath -Destination $zipPath -Force
    Copy-Item -LiteralPath $zipPath -Destination $timestampedZipPath -Force

    $size = [Math]::Round((Get-Item -LiteralPath $zipPath).Length / 1MB, 2)
    Write-Host "Success! Exported $($paths.Count) files ($size MB)"
    Write-Host "ZIP file: $zipPath"
    Write-Host "Timestamped: $timestampedZipPath"
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
