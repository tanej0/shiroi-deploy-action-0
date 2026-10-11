param(
    [string]$SourceRef = 'main',
    [switch]$Force,
    [switch]$Rollback
)
$ErrorActionPreference = 'Stop'
$repository = 'tanej0/shiroi-deploy-action-0'
if (!(Get-Command gh -ErrorAction SilentlyContinue)) { throw 'Install GitHub CLI first: https://cli.github.com/' }
gh auth status --hostname github.com 2>$null
if ($LASTEXITCODE -ne 0) { throw 'GitHub login is missing or expired. Run: gh auth login --hostname github.com --web' }
$operation = if ($Rollback) { 'rollback' } else { 'update' }
$forceValue = $Force.IsPresent.ToString().ToLowerInvariant()
gh workflow run deploy.yml --repo $repository --ref main -f "operation=$operation" -f "source_ref=$SourceRef" -f "force=$forceValue"
if ($LASTEXITCODE -ne 0) { throw 'Could not start the workflow. No successful dispatch was confirmed.' }
Write-Host 'Workflow requested. Check progress:'
gh run list --repo $repository --workflow deploy.yml --limit 3
Write-Host "https://github.com/$repository/actions/workflows/deploy.yml"
