<#
.SYNOPSIS
Create a GitHub repository and publish a sanitized public copy of this repo.

.DESCRIPTION
By default, this script creates a fresh local Git history so previously tracked
local artifacts never appear in the public repository. It can create the GitHub
repository through the GitHub REST API, then push the local `main` branch.

.EXAMPLE
$env:GITHUB_TOKEN = "github_pat_xxx"
powershell -ExecutionPolicy Bypass -File .\publish_public_repo.ps1

.EXAMPLE
$env:GITHUB_TOKEN = "github_pat_xxx"
powershell -ExecutionPolicy Bypass -File .\publish_public_repo.ps1 `
  -Owner "hongyi652" `
  -RepoName "ppt-master-sci-fork" `
  -GitUserName "hongyi" `
  -GitUserEmail "877454565@qq.com"

.EXAMPLE
$env:GITHUB_TOKEN = "github_pat_xxx"
powershell -ExecutionPolicy Bypass -File .\publish_public_repo.ps1 -SkipRepoCreate

.NOTES
Required token scopes:
- Public repo: `public_repo`
- Private repo: `repo`
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "Low")]
param(
    [string]$Owner = "hongyi652",
    [string]$RepoName = "ppt-master-sci-fork",
    [ValidateSet("public", "private")]
    [string]$Visibility = "public",
    [string]$Description = "AI generates natively editable PPTX - fork with MinerU parsing and SVG formula support",
    [string]$DefaultBranch = "main",
    [string]$GithubToken = "",
    [string]$GitUserName = "",
    [string]$GitUserEmail = "",
    [string]$CommitMessage = "",
    [switch]$SkipRepoCreate,
    [switch]$PreserveHistory,
    [switch]$ForcePush
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$script:RepoRoot = Split-Path -Parent $PSCommandPath
$script:ApiBaseUrl = "https://api.github.com"
$script:ResolvedGithubToken = ""
$script:AllowedTrackedFiles = @(
    "projects/README.md",
    "uploads/.gitkeep",
    "exports/.gitkeep"
)

function Write-Step {
    param([string]$Message)

    Write-Host "[STEP] $Message" -ForegroundColor Cyan
}

function Require-Command {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [string]$InstallHint
    )

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $command) {
        throw "$Name is not installed or not on PATH. $InstallHint"
    }
}

function Invoke-Git {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & git @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Arguments -join ' ') failed with exit code $LASTEXITCODE."
    }
}

function Get-GitOutput {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $output = & git @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        $joined = ($output | ForEach-Object { "$_" }) -join [Environment]::NewLine
        throw "git $($Arguments -join ' ') failed with exit code $LASTEXITCODE.`n$joined"
    }

        return ,@($output | ForEach-Object { "$_" } | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
}

function Resolve-GithubToken {
    $token = ""

    if (-not [string]::IsNullOrWhiteSpace($GithubToken)) {
        $token = $GithubToken.Trim()
    }
    elseif (-not [string]::IsNullOrWhiteSpace($env:GITHUB_TOKEN)) {
        $token = $env:GITHUB_TOKEN.Trim()
    }
    elseif (-not [string]::IsNullOrWhiteSpace($env:GH_TOKEN)) {
        $token = $env:GH_TOKEN.Trim()
    }

    if ([string]::IsNullOrWhiteSpace($token)) {
        throw "GitHub token not found. Set GITHUB_TOKEN or GH_TOKEN, or pass -GithubToken. Public repo creation needs a token with public_repo scope."
    }

    if ($token -match '^github_pat_gh[pousr]_' -or $token -match '^gh[pousr]_github_pat_') {
        throw "GitHub token format looks invalid. Use the original token exactly as copied from GitHub. Valid tokens typically start with ghp_, gho_, ghu_, ghs_, ghr_, or github_pat_ — never combine two prefixes together."
    }

    return $token
}

function Invoke-GithubApi {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("GET", "POST")]
        [string]$Method,
        [Parameter(Mandatory = $true)]
        [string]$Uri,
        [object]$Body = $null
    )

    $headers = @{
        Accept = "application/vnd.github+json"
        Authorization = "Bearer $script:ResolvedGithubToken"
        "X-GitHub-Api-Version" = "2022-11-28"
        "User-Agent" = "ppt-master-public-publisher"
    }

    $invokeParams = @{
        Method = $Method
        Uri = $Uri
        Headers = $headers
    }

    if ($null -ne $Body) {
        $invokeParams.ContentType = "application/json"
        $invokeParams.Body = ($Body | ConvertTo-Json -Depth 10 -Compress)
    }

    try {
        return Invoke-RestMethod @invokeParams
    }
    catch {
        $statusCode = $null
        if ($_.Exception.Response) {
            try {
                $statusCode = [int]$_.Exception.Response.StatusCode
            }
            catch {
                $statusCode = $null
            }
        }

        if ($null -ne $statusCode) {
            throw "GitHub API $Method $Uri failed with status $statusCode. $($_.Exception.Message)"
        }

        throw "GitHub API $Method $Uri failed. $($_.Exception.Message)"
    }
}

function Test-GithubRepoExists {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepositoryOwner,
        [Parameter(Mandatory = $true)]
        [string]$RepositoryName
    )

    $uri = "$script:ApiBaseUrl/repos/$RepositoryOwner/$RepositoryName"
    try {
        $null = Invoke-GithubApi -Method GET -Uri $uri
        return $true
    }
    catch {
        if ($_.Exception.Message -match "status 404") {
            return $false
        }

        throw
    }
}

function Ensure-PlaceholderFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RelativePath
    )

    $fullPath = Join-Path $script:RepoRoot $RelativePath
    $parentDir = Split-Path -Parent $fullPath
    if (-not (Test-Path -LiteralPath $parentDir)) {
        New-Item -ItemType Directory -Path $parentDir -Force | Out-Null
    }

    if (-not (Test-Path -LiteralPath $fullPath)) {
        Set-Content -LiteralPath $fullPath -Value "" -Encoding utf8
    }
}

function Get-OptionalGitConfig {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Key
    )

    $output = & git config --get $Key 2>$null
    if ($LASTEXITCODE -eq 0) {
        return (@($output | ForEach-Object { "$_" }) | Select-Object -First 1)
    }

    if ($LASTEXITCODE -eq 1) {
        return ""
    }

    throw "git config --get $Key failed with exit code $LASTEXITCODE."
}

function Set-GitIdentityIfNeeded {
    if (-not [string]::IsNullOrWhiteSpace($GitUserName)) {
        Invoke-Git -Arguments @("config", "user.name", $GitUserName)
    }

    if (-not [string]::IsNullOrWhiteSpace($GitUserEmail)) {
        Invoke-Git -Arguments @("config", "user.email", $GitUserEmail)
    }

    $configuredName = Get-OptionalGitConfig -Key "user.name"
    $configuredEmail = Get-OptionalGitConfig -Key "user.email"

    if ([string]::IsNullOrWhiteSpace($configuredName) -or [string]::IsNullOrWhiteSpace($configuredEmail)) {
        throw 'Git identity is not configured. Pass -GitUserName and -GitUserEmail, or run: git config --global user.name "Your Name" ; git config --global user.email "you@example.com"'
    }
}

function Get-ForbiddenTrackedFiles {
    $trackedFiles = Get-GitOutput -Arguments @("ls-files")
    $violations = New-Object System.Collections.Generic.List[string]

    foreach ($trackedFile in $trackedFiles) {
        if ($script:AllowedTrackedFiles -contains $trackedFile) {
            continue
        }

        if ($trackedFile -in @(".env", "LOCAL_SETUP_CN.md", "svg_qc_output.txt")) {
            [void]$violations.Add($trackedFile)
            continue
        }

        if ($trackedFile.StartsWith("uploads/", [System.StringComparison]::Ordinal) -or
            $trackedFile.StartsWith("exports/", [System.StringComparison]::Ordinal) -or
            $trackedFile.StartsWith("tmp_mineru_output/", [System.StringComparison]::Ordinal) -or
            $trackedFile.StartsWith("tmp_mineru_probe/", [System.StringComparison]::Ordinal)) {
            [void]$violations.Add($trackedFile)
            continue
        }

        if ($trackedFile.StartsWith("projects/", [System.StringComparison]::Ordinal) -and $trackedFile -ne "projects/README.md") {
            [void]$violations.Add($trackedFile)
            continue
        }
    }

        return ,($violations.ToArray())
}

function Initialize-CleanGitHistory {
    $gitDir = Join-Path $script:RepoRoot ".git"
    if (Test-Path -LiteralPath $gitDir) {
        $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $parentDir = Split-Path -Parent $script:RepoRoot
        $backupName = "{0}.git.backup_{1}" -f (Split-Path -Leaf $script:RepoRoot), $timestamp
        $backupPath = Join-Path $parentDir $backupName

        Write-Step "Backing up existing .git to $backupPath"
        if ($PSCmdlet.ShouldProcess($gitDir, "Move existing git history to $backupPath")) {
            Move-Item -LiteralPath $gitDir -Destination $backupPath
        }
    }

    Write-Step "Initializing a clean git repository"
    if ($PSCmdlet.ShouldProcess($script:RepoRoot, "Initialize fresh git repository")) {
        Invoke-Git -Arguments @("init")
    }
}

function Sanitize-ExistingGitHistory {
    Write-Step "Removing tracked local artifacts from the current git index"
    if ($PSCmdlet.ShouldProcess($script:RepoRoot, "Remove tracked local artifacts from the git index")) {
        Invoke-Git -Arguments @("rm", "-r", "--cached", "--ignore-unmatch", "uploads", "exports", "tmp_mineru_output", "tmp_mineru_probe")
        Invoke-Git -Arguments @("rm", "--cached", "--ignore-unmatch", ".env", "LOCAL_SETUP_CN.md", "svg_qc_output.txt")
    }
}

function Ensure-GithubRepository {
    if ($SkipRepoCreate) {
        Write-Step "Skipping GitHub repository creation because -SkipRepoCreate was provided"
        return
    }

    $script:ResolvedGithubToken = Resolve-GithubToken
    $viewer = Invoke-GithubApi -Method GET -Uri "$script:ApiBaseUrl/user"
    if ($viewer.login -ne $Owner) {
        throw "Authenticated GitHub account '$($viewer.login)' does not match -Owner '$Owner'. Use a token for '$Owner' or pass -Owner '$($viewer.login)'."
    }

    if (Test-GithubRepoExists -RepositoryOwner $Owner -RepositoryName $RepoName) {
        Write-Step "GitHub repository $Owner/$RepoName already exists; skipping creation"
        return
    }

    $body = @{
        name = $RepoName
        description = $Description
        private = ($Visibility -eq "private")
        auto_init = $false
        has_issues = $true
        has_projects = $false
        has_wiki = $false
    }

    $repoUrl = "https://github.com/$Owner/$RepoName"
    Write-Step "Creating GitHub repository $repoUrl"
    if ($PSCmdlet.ShouldProcess($repoUrl, "Create remote GitHub repository")) {
        $null = Invoke-GithubApi -Method POST -Uri "$script:ApiBaseUrl/user/repos" -Body $body
    }
}

Push-Location $script:RepoRoot
try {
    if ([string]::IsNullOrWhiteSpace($CommitMessage)) {
        $CommitMessage = "feat: initial public release of $RepoName"
    }

    Write-Step "Validating local prerequisites"
    Require-Command -Name "git" -InstallHint "Install Git from https://git-scm.com/downloads"

    Ensure-PlaceholderFile -RelativePath "uploads/.gitkeep"
    Ensure-PlaceholderFile -RelativePath "exports/.gitkeep"

    Ensure-GithubRepository

    if ($PreserveHistory) {
        Sanitize-ExistingGitHistory
    }
    else {
        Initialize-CleanGitHistory
    }

    Set-GitIdentityIfNeeded

    Write-Step "Staging repository files"
    if ($PSCmdlet.ShouldProcess($script:RepoRoot, "Stage repository files")) {
        Invoke-Git -Arguments @("add", ".")
    }

    $forbiddenTrackedFiles = Get-ForbiddenTrackedFiles
        if ($forbiddenTrackedFiles.Count -gt 0) {
        $details = $forbiddenTrackedFiles -join [Environment]::NewLine
        throw "Refusing to publish because forbidden files are still tracked:`n$details"
    }

    $stagedFiles = Get-GitOutput -Arguments @("diff", "--cached", "--name-only")
        if ($stagedFiles.Count -gt 0) {
        Write-Step "Creating commit"
        if ($PSCmdlet.ShouldProcess($script:RepoRoot, "Create initial publish commit")) {
            Invoke-Git -Arguments @("commit", "-m", $CommitMessage)
        }
    }
    else {
        Write-Step "No staged changes found; reusing the current HEAD commit"
    }

    $remoteUrl = "https://github.com/$Owner/$RepoName.git"
    $existingRemotes = Get-GitOutput -Arguments @("remote")
        if ($existingRemotes -contains "origin") {
        Write-Step "Updating origin remote to $remoteUrl"
        if ($PSCmdlet.ShouldProcess("origin", "Set remote URL to $remoteUrl")) {
            Invoke-Git -Arguments @("remote", "set-url", "origin", $remoteUrl)
        }
    }
    else {
        Write-Step "Adding origin remote $remoteUrl"
        if ($PSCmdlet.ShouldProcess("origin", "Add remote URL $remoteUrl")) {
            Invoke-Git -Arguments @("remote", "add", "origin", $remoteUrl)
        }
    }

    Write-Step "Setting default branch to $DefaultBranch"
    if ($PSCmdlet.ShouldProcess($DefaultBranch, "Rename current branch to $DefaultBranch")) {
        Invoke-Git -Arguments @("branch", "-M", $DefaultBranch)
    }

    $pushArgs = @("push", "-u", "origin", $DefaultBranch)
    if ($ForcePush) {
        $pushArgs = @("push", "--force", "-u", "origin", $DefaultBranch)
    }

    Write-Step "Pushing branch to GitHub"
    if ($PSCmdlet.ShouldProcess($remoteUrl, "Push $DefaultBranch to origin")) {
        Invoke-Git -Arguments $pushArgs
    }

    Write-Host ""
    Write-Host "Publish complete." -ForegroundColor Green
    Write-Host "Repository: https://github.com/$Owner/$RepoName"
    Write-Host "Clone URL : $remoteUrl"
}
finally {
    Pop-Location
}