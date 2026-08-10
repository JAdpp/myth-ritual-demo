param(
    [int]$RequestsPerMinute = 20,
    [int]$TimeoutSeconds = 120
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $projectRoot
$env:PYTHONIOENCODING = 'utf-8'

$annotator = 'data\annotate_c1_retrieval.py'
$validator = 'data\validate_a1_annotations.py'
$annotationDb = 'data\corpus\a1_retrieval_annotations\annotations.sqlite3'

function Invoke-CheckedPython {
    param(
        [Parameter(Mandatory = $true)][string]$Stage,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    Write-Output ("{0} stage={1} event=start" -f (Get-Date -Format o), $Stage)
    & python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Stage '$Stage' stopped with exit code $LASTEXITCODE. The next stage was not started."
    }
    Write-Output ("{0} stage={1} event=pass" -f (Get-Date -Format o), $Stage)
}

function Invoke-AnnotationValidation {
    param([Parameter(Mandatory = $true)][string]$Stage)
    Invoke-CheckedPython -Stage $Stage -Arguments @(
        $validator,
        '--db', $annotationDb,
        '--table', 'annotation_jobs',
        '--json-column', 'annotation_json',
        '--status-column', 'status',
        '--id-column', 'entry_id',
        '--hash-column', 'source_text_sha256',
        '--accepted-status', 'valid'
    )
}

$common = @(
    '--sample-mode', 'stratified',
    '--batch-char-limit', '4000',
    '--rpm', $RequestsPerMinute.ToString(),
    '--timeout', $TimeoutSeconds.ToString(),
    '--max-tokens', '12000',
    '--retries', '2',
    '--resume',
    '--retry-quarantined',
    '--live'
)

# Gate 1: a deliberately smaller micro-batch for the 120-record pilot.
Invoke-CheckedPython -Stage 'pilot-120' -Arguments (@(
    $annotator, '--limit', '120', '--batch-size', '4'
) + $common)
Invoke-AnnotationValidation -Stage 'validate-pilot-120'

# Gate 2: the 120 records are a deterministic prefix of this 10% canary.
Invoke-CheckedPython -Stage 'canary-1235' -Arguments (@(
    $annotator, '--limit', '1235', '--batch-size', '6', '--workers', '4'
) + $common)
Invoke-AnnotationValidation -Stage 'validate-canary-1235'

# Gate 3: only a successful pilot and canary can unlock the full corpus.
Invoke-CheckedPython -Stage 'full-12353' -Arguments (@(
    $annotator, '--batch-size', '6', '--workers', '4', '--confirm-full-run'
) + $common)
Invoke-AnnotationValidation -Stage 'validate-full-12353'

Write-Output ("{0} pipeline=completed" -f (Get-Date -Format o))
