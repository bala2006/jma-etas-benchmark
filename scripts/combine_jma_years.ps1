# PowerShell script to combine and parse JMA year data files into single CSV
# Extracts only required columns: Date, Time, Latitude(°N), Longitude(°E), Depth(km), Mag

$ErrorActionPreference = "Continue"

$dataDir = "c:\Users\balac\Desktop\MEXT\p1\jma-etas-benchmark\data\raw\jma_downloads"
$outputFile = "c:\Users\balac\Desktop\MEXT\p1\jma-etas-benchmark\data\raw\jma_tohoku_2010_2023.csv"
$startYear = 2010
$endYear = 2023

Write-Host "Starting JMA data combination script..." -ForegroundColor Green
Write-Host "Data directory: $dataDir"
Write-Host "Output file: $outputFile"
Write-Host ""

# Function to convert coordinate from degrees/minutes to decimal degrees
function ConvertCoordinate {
    param(
        [double]$degrees,
        [double]$minutes
    )
    if ([double]::IsNaN($degrees) -or [double]::IsNaN($minutes)) {
        return $null
    }
    return $degrees + ($minutes / 60.0)
}

# Function to parse magnitude field
function ParseMagnitude {
    param([string]$magText)
    $magText = $magText.Trim()
    if ([string]::IsNullOrEmpty($magText)) { return $null }
    
    # Check for letter encoding (A0=-1.0, A9=-1.9, B0=-2.0, etc.)
    if ($magText.Length -eq 2 -and [char]::IsLetter($magText[0]) -and [char]::IsDigit($magText[1])) {
        $letterOffset = [int][char]::ToUpper($magText[0]) - [int][char]'A' + 1
        return -($letterOffset + [int]$magText[1] / 10.0)
    }
    
    # Try parsing as float
    if ([double]::TryParse($magText, [ref]$null)) {
        $mag = [double]::Parse($magText)
        # JMA magnitude stored in tenths, e.g., "34" means M3.4
        return $mag / 10.0
    }
    return $null
}

# Function to parse JMA fixed-width record
function ParseJMARecord {
    param([string]$line)
    
    if ($line.Length -lt 55 -or $line[0] -notmatch '[JUI]') {
        return $null
    }
    
    try {
        # Parse date/time fields (fixed positions)
        $year = [int]$line.Substring(1, 4)
        $month = [int]$line.Substring(5, 2)
        $day = [int]$line.Substring(7, 2)
        $hour = [int]$line.Substring(9, 2)
        $minute = [int]$line.Substring(11, 2)
        
        # Second in hundredths
        $secondHundredths = [double]$line.Substring(13, 4).Trim()
        $second = [int]($secondHundredths / 100.0)
        
        # Latitude: degrees (pos 21-24) and minutes (pos 24-28)
        $latDeg = [double]$line.Substring(21, 3).Trim()
        $latMin = [double]$line.Substring(24, 4).Trim()
        $latitude = ConvertCoordinate $latDeg $latMin
        
        # Longitude: degrees (pos 32-36) and minutes (pos 36-40)
        $lonDeg = [double]$line.Substring(32, 4).Trim()
        $lonMin = [double]$line.Substring(36, 4).Trim()
        $longitude = ConvertCoordinate $lonDeg $lonMin
        
        # Depth in hundredths of km (pos 44-49)
        $depthHundredths = [double]$line.Substring(44, 5).Trim()
        $depth = $depthHundredths / 100.0
        
        # Magnitude (pos 49-51)
        $magnitude = ParseMagnitude $line.Substring(49, 2)
        
        if ($null -eq $latitude -or $null -eq $longitude -or $null -eq $depth -or $null -eq $magnitude) {
            return $null
        }
        
        $date = "{0:D4}-{1:D2}-{2:D2}" -f $year, $month, $day
        $time = "{0:D2}:{1:D2}:{2:D2}" -f $hour, $minute, $second
        
        return [PSCustomObject]@{
            Date = $date
            Time = $time
            "Latitude(°N)" = [math]::Round($latitude, 5)
            "Longitude(°E)" = [math]::Round($longitude, 5)
            "Depth(km)" = [math]::Round($depth, 2)
            Mag = [math]::Round($magnitude, 1)
        }
    }
    catch {
        return $null
    }
}

# First pass: count total lines for progress calculation
Write-Host "Counting total lines..." -ForegroundColor Yellow
$totalLines = 0
for ($year = $startYear; $year -le $endYear; $year++) {
    $yearFolder = "h$year"
    $yearFile = Join-Path $dataDir $yearFolder $yearFolder
    if (Test-Path $yearFile) {
        $lines = @(Get-Content $yearFile | Where-Object { $_.Trim() -ne "" })
        $lineCount = $lines.Count
        if ($lineCount -eq 0) { $lineCount = 1 }
        $totalLines += $lineCount
        Write-Host "  h$year`: $lineCount lines"
    }
    else {
        Write-Host "  h$year`: File not found" -ForegroundColor Red
    }
}

Write-Host "`nTotal lines to process: $totalLines" -ForegroundColor Green
Write-Host "Creating output file..." -ForegroundColor Yellow
Write-Host ""

# Create CSV header
$csvHeader = "Date,Time,Latitude(°N),Longitude(°E),Depth(km),Mag"
$csvHeader | Out-File -FilePath $outputFile -Encoding UTF8 -Force

$processedLines = 0
$validRecords = 0

# Loop through each year
for ($year = $startYear; $year -le $endYear; $year++) {
    $yearFolder = "h$year"
    $yearFile = Join-Path $dataDir $yearFolder $yearFolder
    
    if (Test-Path $yearFile) {
        Write-Host "Processing: h$year" -ForegroundColor Cyan
        
        # Read all lines
        $lines = @(Get-Content $yearFile | Where-Object { $_.Trim() -ne "" })
        if ($lines.Count -eq 0) { $lines = @() }
        
        foreach ($line in $lines) {
            $processedLines++
            $record = ParseJMARecord $line
            
            if ($null -ne $record) {
                # Format record as CSV line
                $csvLine = "{0},{1},{2},{3},{4},{5}" -f `
                    $record.Date, `
                    $record.Time, `
                    $record."Latitude(°N)", `
                    $record."Longitude(°E)", `
                    $record."Depth(km)", `
                    $record.Mag
                
                # Append to file progressively
                $csvLine | Out-File -FilePath $outputFile -Encoding UTF8 -Append
                $validRecords++
            }
            
            # Show progress every 100 lines
            if ($processedLines % 100 -eq 0) {
                $percentage = [math]::Round(($processedLines / $totalLines) * 100, 1)
                Write-Host "  Progress: $percentage% ($processedLines/$totalLines lines) - $validRecords valid records" -NoNewline
                Write-Host "`r" -NoNewline
            }
        }
        
        Write-Host "  Year h$year complete - Valid records so far: $validRecords                                             "
    }
    else {
        Write-Host "  Warning: File not found - $yearFile" -ForegroundColor Red
    }
}

Write-Host "`n=== Success! ===" -ForegroundColor Green
Write-Host "Output file: $outputFile"
Write-Host "Total lines processed: $processedLines"
Write-Host "Valid records saved: $validRecords" -ForegroundColor Green
Write-Host ""
