$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$project = Join-Path $root "src\LocalModelConsole\LocalModelConsole.csproj"
$output = Join-Path $root "dist"

dotnet publish $project `
  --configuration Release `
  --runtime win-x64 `
  --self-contained false `
  /p:PublishSingleFile=true `
  /p:DebugType=None `
  --output $output

Copy-Item (Join-Path $root "controller.py") $output -Force
Copy-Item (Join-Path $root "discovery.py") $output -Force
Copy-Item (Join-Path $root "models.json") $output -Force
New-Item (Join-Path $output "mcp") -ItemType Directory -Force | Out-Null
Copy-Item (Join-Path $root "mcp\server.py") (Join-Path $output "mcp\server.py") -Force

Write-Host "Built: $output\LocalModelConsole.exe"
