param(
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$ArgsList
)

$BinDir = Split-Path -Parent $MyInvocation.MyCommand.Path
python "$BinDir\aic.py" @ArgsList
if ($LASTEXITCODE -ne $null) {
    exit $LASTEXITCODE
}
