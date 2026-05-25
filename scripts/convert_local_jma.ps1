param(
    [string]$InputRoot = "data/raw/jma_downloads",
    [string]$OutputCsv = "data/raw/jma_tohoku_2010_2023.csv"
)

$ErrorActionPreference = "Stop"

function Parse-Number {
    param([string]$Text)
    $trimmed = $Text.Trim()
    if ($trimmed.Length -eq 0) { return $null }
    $value = 0.0
    if ([double]::TryParse($trimmed, [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$value)) {
        return $value
    }
    return $null
}

function Parse-Magnitude {
    param([string]$Text)
    $trimmed = $Text.Trim()
    if ($trimmed.Length -eq 0) { return $null }
    if ($trimmed.Length -eq 2 -and [char]::IsLetter($trimmed[0]) -and [char]::IsDigit($trimmed[1])) {
        $letterOffset = [int][char]::ToUpperInvariant($trimmed[0]) - [int][char]'A' + 1
        return -1.0 * ($letterOffset + ([double]::Parse($trimmed[1], [Globalization.CultureInfo]::InvariantCulture) / 10.0))
    }
    $value = Parse-Number $trimmed
    if ($null -eq $value) { return $null }
    return $value / 10.0
}

function Parse-Record {
    param([string]$Line)
    if ($Line.Length -lt 55) { return $null }
    if ($Line[0] -notin @('J', 'U', 'I')) { return $null }

    try {
        $year = [int]$Line.Substring(1, 4)
        $month = [int]$Line.Substring(5, 2)
        $day = [int]$Line.Substring(7, 2)
        $hour = [int]$Line.Substring(9, 2)
        $minute = [int]$Line.Substring(11, 2)
    } catch {
        return $null
    }

    $secondRaw = Parse-Number $Line.Substring(13, 4)
    $latDeg = Parse-Number $Line.Substring(21, 3)
    $latMin = Parse-Number $Line.Substring(24, 4)
    $lonDeg = Parse-Number $Line.Substring(32, 4)
    $lonMin = Parse-Number $Line.Substring(36, 4)
    $depthRaw = Parse-Number $Line.Substring(44, 5)
    $mag = Parse-Magnitude $Line.Substring(49, 2)

    if ($null -eq $secondRaw -or $null -eq $latDeg -or $null -eq $latMin -or $null -eq $lonDeg -or $null -eq $lonMin -or $null -eq $depthRaw -or $null -eq $mag) {
        return $null
    }

    $seconds = $secondRaw / 100.0
    $wholeSecond = [Math]::Floor($seconds)
    $milliseconds = [Math]::Round(($seconds - $wholeSecond) * 1000.0)
    if ($wholeSecond -ge 60) {
        $wholeSecond = 59
        $milliseconds = 999
    }

    try {
        $dt = [datetime]::new($year, $month, $day, $hour, $minute, [int]$wholeSecond).AddMilliseconds($milliseconds)
    } catch {
        return $null
    }

    $lat = $latDeg + (($latMin / 100.0) / 60.0)
    $lon = $lonDeg + (($lonMin / 100.0) / 60.0)
    $depth = $depthRaw / 100.0

    if ($dt -lt [datetime]"2010-01-01T00:00:00" -or $dt -gt [datetime]"2023-12-31T23:59:59") { return $null }
    if ($lat -lt 36.0 -or $lat -gt 42.0 -or $lon -lt 140.0 -or $lon -gt 146.0) { return $null }

    return [pscustomobject]@{
        Date = $dt.ToString("yyyy-MM-dd", [Globalization.CultureInfo]::InvariantCulture)
        Time = $dt.ToString("HH:mm:ss", [Globalization.CultureInfo]::InvariantCulture)
        Lat = $lat
        Lon = $lon
        Depth = $depth
        Mag = $mag
    }
}

$outPath = Join-Path (Get-Location) $OutputCsv
New-Item -ItemType Directory -Force -Path (Split-Path $outPath -Parent) | Out-Null

$writer = [System.IO.StreamWriter]::new($outPath, $false, [System.Text.UTF8Encoding]::new($false))
try {
    $writer.WriteLine("Date,Time,Latitude(°N),Longitude(°E),Depth(km),Mag")
    $count = 0
    foreach ($year in 2010..2023) {
        $file = Join-Path $InputRoot "h$year/h$year"
        if (-not (Test-Path $file)) {
            Write-Host "Skipping missing file: $file"
            continue
        }
        Write-Host "Converting $file"
        foreach ($line in [System.IO.File]::ReadLines((Resolve-Path $file))) {
            $row = Parse-Record $line
            if ($null -eq $row) { continue }
            $writer.WriteLine(("{0},{1},{2:F6},{3:F6},{4:F2},{5:F1}" -f $row.Date, $row.Time, $row.Lat, $row.Lon, $row.Depth, $row.Mag))
            $count++
        }
    }
    Write-Host "Saved $count filtered events to $OutputCsv"
    Write-Host "This file covers downloaded years 2010-2023."
} finally {
    $writer.Close()
}
