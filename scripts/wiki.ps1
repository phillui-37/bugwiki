#!/usr/bin/env pwsh
# wiki.ps1 — PowerShell 5.1+ port of scripts/wiki.
# Usage: wiki.ps1 init [target] | find <terms> | new <domain> <slug>
#        index | lint | housekeep [--apply] | list
#
# Same store, same frontmatter, same output shape as the bash helper.
# ripgrep is used when present; Select-String is the fallback.

[CmdletBinding()]
param(
    # everything arrives in one list: value-from-remaining-arguments also takes
    # position 0 on some hosts, which silently folded the command into the query
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$ArgList
)

$ErrorActionPreference = 'Stop'

$Command = ''
$Rest = @()
if ($null -ne $ArgList -and $ArgList.Count -gt 0) {
    $Command = $ArgList[0]
    if ($ArgList.Count -gt 1) { $Rest = @($ArgList[1..($ArgList.Count - 1)]) }
}

# ---- layout and thresholds ------------------------------------------------

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($env:WIKI_HOME) {
    $Wiki = $env:WIKI_HOME
} else {
    $Wiki = (Resolve-Path (Join-Path $ScriptDir '..')).Path
}
$Solutions = Join-Path $Wiki 'solutions'
$Reports = Join-Path $Wiki 'reports'
$Archive = Join-Path $Wiki 'archive'
$Today = (Get-Date).ToString('yyyy-MM-dd')

function Get-EnvInt([string]$Name, [int]$Default) {
    $v = [Environment]::GetEnvironmentVariable($Name)
    if ($v -and $v -match '^\d+$') { return [int]$v }
    return $Default
}

$StaleDays = Get-EnvInt 'WIKI_STALE_DAYS' 180
$ArchiveDays = Get-EnvInt 'WIKI_ARCHIVE_DAYS' 90
$LessonsMax = Get-EnvInt 'WIKI_LESSONS_MAX' 12
$DupJaccard = Get-EnvInt 'WIKI_DUP_JACCARD' 55
$DupMinPages = Get-EnvInt 'WIKI_DUP_MIN_PAGES' 8
$ReportsKeep = Get-EnvInt 'WIKI_REPORTS_KEEP' 30

$script:HasRg = [bool](Get-Command rg -ErrorAction SilentlyContinue)
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$script:Code = 0

# ---- helpers --------------------------------------------------------------

function Write-Lines([string]$Path, [string[]]$Lines) {
    [System.IO.File]::WriteAllLines($Path, $Lines, $Utf8NoBom)
}

function Get-Field([string]$Path, [string]$Key) {
    $lines = [System.IO.File]::ReadAllLines($Path)
    $n = 0
    foreach ($l in $lines) {
        if ($l -match '^---\s*$') {
            $n++
            if ($n -gt 1) { break }
            continue
        }
        if ($n -eq 1 -and $l.StartsWith("$Key`:")) {
            $v = $l.Substring($Key.Length + 1).Trim()
            if ($Key -ne 'title') {
                $i = $v.IndexOf('#')
                if ($i -ge 0) { $v = $v.Substring(0, $i) }
                $v = $v.TrimEnd()
            }
            return $v
        }
    }
    return ''
}

function Get-Pages {
    if (-not (Test-Path $Solutions)) { return @() }
    return @(Get-ChildItem -LiteralPath $Solutions -Recurse -Filter *.md -File |
        Where-Object { $_.Name -ne 'README.md' } |
        Sort-Object FullName)
}

function Get-Rel([string]$FullName) {
    $rel = $FullName.Substring($Wiki.Length).TrimStart('\', '/')
    return ($rel -replace '\\', '/')
}

function Get-DateOrNull([string]$Text) {
    if (-not $Text) { return $null }
    try {
        return [datetime]::ParseExact($Text, 'yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture)
    } catch {
        return $null
    }
}

function Get-DaysSince($Date) {
    if ($null -eq $Date) { return -1 }
    return [int]((Get-Date).Date - $Date.Date).TotalDays
}

function Get-Tokens([string]$Text) {
    if (-not $Text) { return @() }
    $parts = [regex]::Split($Text.ToLowerInvariant(), '[^a-z0-9]+')
    return @($parts | Where-Object { $_.Length -gt 3 } | Select-Object -Unique)
}

# ---- commands -------------------------------------------------------------

function Invoke-List {
    foreach ($p in Get-Pages) {
        $rel = Get-Rel $p.FullName
        $rel = $rel -replace '^solutions/', ''
        $rel -replace '\.md$', ''
    }
    $script:Code = 0; return
}

function Invoke-Find([string[]]$Terms) {
    if (-not $Terms -or $Terms.Count -eq 0) {
        [Console]::Error.WriteLine('usage: wiki find <terms>')
        $script:Code = 1; return
    }
    $q = ($Terms -join ' ')
    $literal = ($env:WIKI_REGEX -ne '1')
    $hits = @()

    if ($script:HasRg) {
        $rgArgs = @('-l', '-i')
        if ($literal) { $rgArgs += '-F' }
        # '--glob=*.md' must be one token: when splatted as '--glob', '*.md'
        # PowerShell eats the pair and rg receives '*.md' as the pattern
        $rgArgs += @('--glob=*.md', '--', $q, $Solutions)
        $out = & rg @rgArgs 2>$null
        $code = $LASTEXITCODE
        if ($code -gt 1) {
            [Console]::Error.WriteLine("search error (rg exit $code) for: $q")
            $script:Code = 2; return
        }
        if ($code -eq 0) { $hits += @($out) }
    } else {
        foreach ($f in (Get-Pages)) {
            $text = Get-Content -LiteralPath $f.FullName -Raw -ErrorAction SilentlyContinue
            if ($null -eq $text) { continue }
            if ($literal) {
                if ($text.IndexOf($q, [StringComparison]::OrdinalIgnoreCase) -ge 0) { $hits += $f.FullName }
            } elseif ($text -match $q) {
                $hits += $f.FullName
            }
        }
    }

    # a slug is a search key too: match file names for a one-word query
    if ($q -notmatch '\s' -and (Test-Path $Solutions)) {
        $byName = Get-ChildItem -LiteralPath $Solutions -Recurse -Filter '*.md' -File |
            Where-Object { $_.Name.ToLowerInvariant().Contains($q.ToLowerInvariant()) } |
            ForEach-Object { $_.FullName }
        $hits += @($byName)
    }

    $hits = @($hits | Where-Object { $_ } | Sort-Object -Unique)
    if ($hits.Count -eq 0) {
        Write-Output ("no match for: {0}" -f $q)
        Write-Output 'try a shorter fragment, or: wiki list'
        $script:Code = 0; return
    }

    foreach ($h in $hits) {
        $status = Get-Field $h 'status'
        $rec = Get-Field $h 'recurrence'
        Write-Output ("--- {0}  [{1}, recurrence {2}]" -f (Get-Rel $h), $status, $rec)
        $one = Get-Field $h 'oneliner'
        if ($one) { Write-Output ("    = {0}" -f $one) }

        if ($script:HasRg) {
            $rgArgs = @('-n', '-i', '-m', '4')
            if ($literal) { $rgArgs += '-F' }
            $rgArgs += @('--', $q, $h)
            $out = & rg @rgArgs 2>$null
        } else {
            $matches = Select-String -LiteralPath $h -Pattern $q -SimpleMatch:(-not $literal) -ErrorAction SilentlyContinue
            $out = @($matches | Select-Object -First 4 | ForEach-Object { "{0}:{1}" -f $_.LineNumber, $_.Line })
        }
        $inner = 0
        foreach ($line in @($out)) {
            if (-not $line) { continue }
            if ($line -match '^\d+:\s*oneliner:') { continue }  # already printed
            Write-Output ("    {0}" -f $line)
            $inner++
            if ($inner -ge 4) { break }
        }
    }
    $script:Code = 0; return
}

function Invoke-New([string]$Domain, [string]$Slug) {
    if (-not $Domain -or -not $Slug) {
        [Console]::Error.WriteLine('usage: wiki new <domain> <slug>')
        $script:Code = 1; return
    }
    $dir = Join-Path $Solutions $Domain
    $file = Join-Path $dir "$Slug.md"
    if (Test-Path $file) {
        [Console]::Error.WriteLine("exists: $file")
        $script:Code = 1; return
    }
    New-Item -ItemType Directory -Force -Path $dir | Out-Null

    $template = Join-Path $Wiki 'templates/entry.md'
    if (-not (Test-Path $template)) {
        $alt = Join-Path $ScriptDir '../assets/templates/entry.md'
        if (Test-Path $alt) { $template = $alt }
    }
    if (-not (Test-Path $template)) {
        [Console]::Error.WriteLine("no template at $template")
        $script:Code = 1; return
    }

    $title = $Slug -replace '-', ' '
    if ($title.Length -gt 0) { $title = $title.Substring(0, 1).ToUpperInvariant() + $title.Substring(1) }
    $lines = [System.IO.File]::ReadAllLines($template)
    $out = foreach ($l in $lines) {
        if ($l -match '^title:') { "title: $title" }
        elseif ($l -match '^first_seen:') { "first_seen: $Today" }
        elseif ($l -match '^last_seen:') { "last_seen: $Today" }
        elseif ($l -match '^last_verified:') { "last_verified: $Today" }
        else { $l }
    }
    Write-Lines $file @($out)
    Invoke-Index | Out-Null
    Write-Output $file
    $script:Code = 0; return
}

function Invoke-Index {
    $readme = Join-Path $Wiki 'README.md'
    if (-not (Test-Path $readme)) {
        Write-Lines $readme @("# $(Split-Path -Leaf $Wiki)")
    }
    $lines = @([System.IO.File]::ReadAllLines($readme))
    if (-not ($lines -contains '<!-- index:start -->')) {
        # a repo clone keeps its README as documentation; the index is data
        if ((Test-Path (Join-Path $Wiki 'SKILL.md')) -and (Test-Path (Join-Path $Wiki 'scripts'))) {
            $readme = Join-Path $Wiki 'INDEX.md'
            if (Test-Path $readme) {
                $lines = @([System.IO.File]::ReadAllLines($readme))
            } else {
                $lines = @('# Index', '', '<!-- index:start -->', '<!-- index:end -->')
            }
        } else {
            $lines += @('', '## Index', '', '<!-- index:start -->', '<!-- index:end -->')
        }
    }

    $rows = @()
    foreach ($p in Get-Pages) {
        $title = Get-Field $p.FullName 'title'
        $status = Get-Field $p.FullName 'status'
        $seen = Get-Field $p.FullName 'last_seen'
        $domain = Split-Path -Leaf (Split-Path -Parent $p.FullName)
        $ref = Get-Rel $p.FullName
        $rows += ("| [{0}]({1}) | {2} | {3} | {4} |" -f $title, $ref, $domain, $status, $seen)
    }

    $result = New-Object System.Collections.Generic.List[string]
    $inside = $false
    foreach ($l in $lines) {
        if ($l -eq '<!-- index:start -->') {
            $result.Add($l)
            $result.Add('')
            $result.Add('| Page | Domain | Status | Last seen |')
            $result.Add('| --- | --- | --- | --- |')
            foreach ($r in $rows) { $result.Add($r) }
            $inside = $true
            continue
        }
        if ($l -eq '<!-- index:end -->') {
            $inside = $false
            $result.Add($l)
            continue
        }
        if ($inside) { continue }
        $result.Add($l)
    }
    Write-Lines $readme $result.ToArray()
    Write-Output ("indexed {0} entries" -f $rows.Count)
    $script:Code = 0; return
}

function Invoke-Lint {
    $bad = 0
    $adv = 0
    foreach ($p in Get-Pages) {
        $rel = Get-Rel $p.FullName
        $title = Get-Field $p.FullName 'title'
        $status = Get-Field $p.FullName 'status'
        $rec = Get-Field $p.FullName 'recurrence'
        if (-not $title) { Write-Output "missing title: $rel"; $bad = 1 }

        if ($status -notin @('verified', 'workaround', 'obsolete', 'unconfirmed')) {
            if (-not $status) { Write-Output "missing status: $rel" } else { Write-Output "bad status ($status): $rel" }
            $bad = 1
        }

        if (-not $rec) {
            Write-Output "missing recurrence: $rel"
            $bad = 1
        } elseif ($rec -notmatch '^\d+$') {
            Write-Output "bad recurrence ($rec): $rel"
            $bad = 1
        } elseif ([int]$rec -ge 2) {
            Write-Output ("promote to LESSONS.md ({0} days): {1}" -f $rec, $rel)
            $adv = 1
        }

        $seen = Get-Field $p.FullName 'last_verified'
        if (-not $seen) { $seen = Get-Field $p.FullName 'last_seen' }
        $d = Get-DateOrNull $seen
        if ($null -ne $d) {
            $age = Get-DaysSince $d
            if ($age -ge $StaleDays -and $status -ne 'obsolete') {
                Write-Output ("stale ({0} days, {1}): {2}" -f $age, $status, $rel)
            }
        }
    }
    if ($bad -eq 0 -and $adv -eq 0) { Write-Output 'lint clean' }
    if ($bad -ne 0) { $script:Code = 1; return }
    $script:Code = 0; return
}

function Invoke-Housekeep([string]$Apply) {
    New-Item -ItemType Directory -Force -Path $Reports | Out-Null
    $report = Join-Path $Reports "$Today.md"
    $pages = @(Get-Pages)
    $archived = 0
    $collisions = @()
    $noObsoleteSince = @()
    $queue = @()
    $promote = @()
    $verify = @()

    foreach ($p in $pages) {
        $rel = Get-Rel $p.FullName
        $status = Get-Field $p.FullName 'status'
        if ($status -eq 'obsolete') {
            $since = Get-Field $p.FullName 'obsolete_since'
            if (-not $since) {
                $noObsoleteSince += "- $rel (obsolete, no obsolete_since)"
                continue
            }
            $d = Get-DateOrNull $since
            if ($null -ne $d -and (Get-DaysSince $d) -ge $ArchiveDays) {
                $queue += "- $rel (obsolete $((Get-DaysSince $d))d)"
            }
        }
    }

    if ($Apply -eq '--apply') {
        foreach ($p in $pages) {
            $status = Get-Field $p.FullName 'status'
            if ($status -ne 'obsolete') { continue }
            $since = Get-Field $p.FullName 'obsolete_since'
            if (-not $since) { continue }
            $d = Get-DateOrNull $since
            if ($null -eq $d -or (Get-DaysSince $d) -lt $ArchiveDays) { continue }

            $domain = Split-Path -Leaf (Split-Path -Parent $p.FullName)
            $dest = Join-Path $Archive $domain
            New-Item -ItemType Directory -Force -Path $dest | Out-Null
            $target = Join-Path $dest $p.Name
            if (Test-Path $target) {
                $collisions += "- $(Get-Rel $p.FullName) -> archive/$domain/$($p.Name) exists, skipped"
                continue
            }
            Move-Item -LiteralPath $p.FullName -Destination $target
            $archived++
        }
        $queue = @()
        Invoke-Index | Out-Null
        Get-ChildItem -LiteralPath $Reports -Filter '*.md' -File |
            Sort-Object LastWriteTime -Descending |
            Select-Object -Skip $ReportsKeep |
            Remove-Item -Force
    }

    foreach ($p in $pages) {
        if (-not (Test-Path $p.FullName)) { continue }
        $rel = Get-Rel $p.FullName
        $rec = Get-Field $p.FullName 'recurrence'
        if ($rec -notmatch '^\d+$') { $rec = '1' }
        if ([int]$rec -ge 2) { $promote += "- $rel (recurrence $rec)" }

        $seen = Get-Field $p.FullName 'last_verified'
        if (-not $seen) { $seen = Get-Field $p.FullName 'last_seen' }
        $d = Get-DateOrNull $seen
        $status = Get-Field $p.FullName 'status'
        if ($null -ne $d -and (Get-DaysSince $d) -ge $StaleDays -and $status -ne 'obsolete') {
            $verify += "- $rel ($((Get-DaysSince $d))d, $status)"
        }
    }

    $dup = @()
    if ($pages.Count -ge $DupMinPages) {
        $sets = @()
        foreach ($p in $pages) {
            if (-not (Test-Path $p.FullName)) { continue }
            $sets += , @{ Path = (Get-Rel $p.FullName); Tokens = (Get-Tokens (Get-Field $p.FullName 'oneliner')) }
        }
        for ($i = 0; $i -lt $sets.Count; $i++) {
            for ($j = $i + 1; $j -lt $sets.Count; $j++) {
                $a = $sets[$i].Tokens
                $b = $sets[$j].Tokens
                if ($a.Count -eq 0 -or $b.Count -eq 0) { continue }
                $inter = 0
                foreach ($t in $a) { if ($b -contains $t) { $inter++ } }
                $union = $a.Count + $b.Count - $inter
                if ($union -gt 0) {
                    $pct = [int](100 * $inter / $union)
                    if ($pct -ge $DupJaccard) {
                        $dup += ("- {0}  <->  {1}  ({2}% overlap)" -f $sets[$i].Path, $sets[$j].Path, $pct)
                    }
                }
            }
        }
    } else {
        $dup += "- skipped: $($pages.Count) pages, scan starts at $DupMinPages"
    }

    $lessonsPath = Join-Path $Wiki 'LESSONS.md'
    $lessonsRows = 0
    if (Test-Path $lessonsPath) {
        foreach ($l in [System.IO.File]::ReadAllLines($lessonsPath)) {
            if ($l -match '^\|') {
                if ($l -match '^\|\s*---') { continue }
                if ($l -match '^\|\s*Rule') { continue }
                $lessonsRows++
            }
        }
    }

    $body = New-Object System.Collections.Generic.List[string]
    $body.Add("# Wiki housekeeping — $Today")
    $body.Add('')
    $body.Add("Thresholds: stale **${StaleDays}d**, archive obsolete after **${ArchiveDays}d**, LESSONS max **$LessonsMax rows**, duplicate >= **$DupJaccard%** oneliner overlap (scan starts at $DupMinPages pages).")
    $body.Add('')
    $body.Add("Pages: $($pages.Count) | LESSONS rows: $lessonsRows | mode: $(if ($Apply -eq '--apply') { '--apply' } else { '--report' })")
    $body.Add('')
    $body.Add("## Archive queue (moved now: $archived)")
    $body.Add('')
    if ($queue.Count) { $body.AddRange([string[]]$queue) } else { $body.Add('- none') }
    if ($collisions.Count) {
        $body.Add('')
        $body.Add('## Archive collisions (not moved)')
        $body.Add('')
        $body.AddRange([string[]]$collisions)
    }
    if ($noObsoleteSince.Count) {
        $body.Add('')
        $body.Add('## Obsolete without obsolete_since')
        $body.Add('')
        $body.AddRange([string[]]$noObsoleteSince)
    }
    $body.Add('')
    $body.Add('## Promote to LESSONS.md')
    $body.Add('')
    if ($promote.Count) { $body.AddRange([string[]]$promote) } else { $body.Add('- none') }
    $body.Add('')
    $body.Add("## Verify or mark obsolete (last_verified older than ${StaleDays}d)")
    $body.Add('')
    if ($verify.Count) { $body.AddRange([string[]]$verify) } else { $body.Add('- none') }
    $body.Add('')
    $body.Add('## Duplicate candidates')
    $body.Add('')
    $body.AddRange([string[]]$dup)
    if ($lessonsRows -gt $LessonsMax) {
        $body.Add('')
        $body.Add('## LESSONS.md too long')
        $body.Add('')
        $body.Add("- $lessonsRows rows, max ${LessonsMax}: promote to AGENTS.md or drop the dead ones")
    }
    $body.Add('')
    $body.Add('---')
    $body.Add("No page is deleted. ``--apply`` moves obsolete pages (age counted from ``obsolete_since``) into ``archive/``, and never overwrites an existing archived file. ``reports/`` keeps the newest $ReportsKeep.")
    Write-Lines $report $body.ToArray()

    Write-Output "report: $report"
    Write-Output ("pages {0} | archived {1} | promote {2} | stale {3} | lessons rows {4}/{5}" -f `
        $pages.Count, $archived, $promote.Count, $verify.Count, $lessonsRows, $LessonsMax)
    $script:Code = 0; return
}

function Invoke-Init([string]$Target) {
    if (-not $Target) { $Target = Join-Path $HOME '.agents/wiki' }
    $assets = Join-Path $ScriptDir '../assets'
    if (-not (Test-Path $assets)) { $assets = $Wiki }

    $inPlace = $false
    if ((Test-Path $Target) -and @(Get-ChildItem -LiteralPath $Target -Force).Count -gt 0) {
        if ((Test-Path (Join-Path $Target 'scripts/wiki')) -or (Test-Path (Join-Path $Target 'scripts/wiki.ps1'))) {
            $inPlace = $true
        } else {
            [Console]::Error.WriteLine("target is not empty and is not this package: $Target")
            $script:Code = 1; return
        }
    }

    foreach ($d in @('solutions', 'templates', 'reports', 'archive', 'bin')) {
        New-Item -ItemType Directory -Force -Path (Join-Path $Target $d) | Out-Null
    }

    foreach ($f in @('README.md', 'LESSONS.md')) {
        $src = Join-Path $assets $f
        $dst = Join-Path $Target $f
        if ((Test-Path $src) -and -not (Test-Path $dst)) { Copy-Item -LiteralPath $src -Destination $dst }
    }
    $tplSrc = Join-Path $assets 'templates/entry.md'
    $tplDst = Join-Path $Target 'templates/entry.md'
    if ((Test-Path $tplSrc) -and -not (Test-Path $tplDst)) { Copy-Item -LiteralPath $tplSrc -Destination $tplDst }

    $blockSrc = Join-Path $assets 'AGENTS-block.md'
    $blockDst = Join-Path $Target 'AGENTS-snippet.md'
    if ((Test-Path $blockSrc) -and -not (Test-Path $blockDst)) { Copy-Item -LiteralPath $blockSrc -Destination $blockDst }

    Copy-Item -LiteralPath (Join-Path $ScriptDir 'wiki.ps1') -Destination (Join-Path $Target 'bin/wiki.ps1') -Force
    $cmdSrc = Join-Path $ScriptDir 'wiki.cmd'
    if (Test-Path $cmdSrc) { Copy-Item -LiteralPath $cmdSrc -Destination (Join-Path $Target 'bin/wiki.cmd') -Force }

    $sh = Join-Path $ScriptDir 'wiki'
    if (Test-Path $sh) {
        Copy-Item -LiteralPath $sh -Destination (Join-Path $Target 'bin/wiki') -Force
        if (Get-Command chmod -ErrorAction SilentlyContinue) {
            & chmod +x (Join-Path $Target 'bin/wiki') 2>$null
        }
    }

    if ($inPlace) {
        Write-Output "wiki:  $Target (in place: existing package files left untouched)"
    } else {
        Write-Output "wiki:  $Target"
    }
    Write-Output "cmd:   $(Join-Path $Target 'bin/wiki.cmd')"
    Write-Output ''
    Write-Output 'next:'
    Write-Output "  1. add $(Join-Path $Target 'bin') to PATH"
    Write-Output "  2. append $Target/AGENTS-snippet.md to your agents instruction file"
    Write-Output "  3. wiki housekeep    # first report"
    $script:Code = 0; return
}

# ---- dispatch -------------------------------------------------------------

switch ($Command) {
    'init' { Invoke-Init $Rest[0]; exit $script:Code }
    'list' { Invoke-List; exit $script:Code }
    'find' { Invoke-Find $Rest; exit $script:Code }
    'new' { Invoke-New $Rest[0] $Rest[1]; exit $script:Code }
    'index' { Invoke-Index; exit $script:Code }
    'lint' { Invoke-Lint; exit $script:Code }
    'housekeep' { Invoke-Housekeep $Rest[0]; exit $script:Code }
    default {
        Write-Output 'wiki init [target] | wiki find <terms> | wiki new <domain> <slug>'
        Write-Output 'wiki index | wiki lint | wiki housekeep [--apply] | wiki list'
        exit 1
    }
}
