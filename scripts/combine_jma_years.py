#!/usr/bin/env python3
"""
Fast JMA year data combination script.
Parses fixed-width format files and exports to CSV with required columns.
"""

import csv
from pathlib import Path
from typing import Optional

# Configuration
DATA_DIR = Path("data/raw/jma_downloads")
OUTPUT_FILE = Path("data/raw/jma_tohoku_2010_2023.csv")
START_YEAR = 2010
END_YEAR = 2023

REQUIRED_COLUMNS = ["Date", "Time", "Latitude(°N)", "Longitude(°E)", "Depth(km)", "Mag"]


def parse_magnitude(mag_text: str) -> Optional[float]:
    """Parse JMA magnitude field including special encodings."""
    mag_text = mag_text.strip()
    if not mag_text:
        return None
    
    # Check for letter encoding (A0=-1.0, A9=-1.9, B0=-2.0, etc.)
    if len(mag_text) == 2 and mag_text[0].isalpha() and mag_text[1].isdigit():
        letter_offset = ord(mag_text[0].upper()) - ord('A') + 1
        return -(letter_offset + int(mag_text[1]) / 10.0)
    
    # Try parsing as float
    try:
        mag = float(mag_text)
        # JMA magnitude stored in tenths, e.g., "34" means M3.4
        return mag / 10.0
    except ValueError:
        return None


def parse_jma_record(line: str) -> Optional[dict]:
    """Parse one fixed-width JMA hypocenter record."""
    if len(line) < 55 or line[0] not in {'J', 'U', 'I'}:
        return None
    
    try:
        # Parse date/time fields (fixed positions)
        year = int(line[1:5])
        month = int(line[5:7])
        day = int(line[7:9])
        hour = int(line[9:11])
        minute = int(line[11:13])
        
        # Second in hundredths
        second_hundredths = float(line[13:17].strip())
        second = int(second_hundredths / 100.0)
        
        # Latitude: degrees (pos 21-24) and minutes (pos 24-28)
        lat_deg = float(line[21:24].strip())
        lat_min = float(line[24:28].strip())
        latitude = lat_deg + (lat_min / 60.0)
        
        # Longitude: degrees (pos 32-36) and minutes (pos 36-40)
        lon_deg = float(line[32:36].strip())
        lon_min = float(line[36:40].strip())
        longitude = lon_deg + (lon_min / 60.0)
        
        # Depth in hundredths of km (pos 44-49)
        depth_hundredths = float(line[44:49].strip())
        depth = depth_hundredths / 100.0
        
        # Magnitude (pos 49-51)
        magnitude = parse_magnitude(line[49:51])
        
        if magnitude is None:
            return None
        
        date = f"{year:04d}-{month:02d}-{day:02d}"
        time = f"{hour:02d}:{minute:02d}:{second:02d}"
        
        return {
            "Date": date,
            "Time": time,
            "Latitude(°N)": round(latitude, 5),
            "Longitude(°E)": round(longitude, 5),
            "Depth(km)": round(depth, 2),
            "Mag": round(magnitude, 1),
        }
    except (ValueError, IndexError):
        return None


def main():
    """Main processing function."""
    print("Starting JMA data combination script...")
    print(f"Data directory: {DATA_DIR}")
    print(f"Output file: {OUTPUT_FILE}")
    print()
    
    # Count total lines for progress
    print("Counting total lines...")
    total_lines = 0
    file_counts = {}
    
    for year in range(START_YEAR, END_YEAR + 1):
        year_file = DATA_DIR / f"h{year}" / f"h{year}"
        if year_file.exists():
            with open(year_file, 'r', encoding='utf-8', errors='ignore') as f:
                count = sum(1 for line in f if line.strip())
                file_counts[year] = count
                total_lines += count
                print(f"  h{year}: {count} lines")
        else:
            print(f"  h{year}: File not found")
    
    print(f"\nTotal lines to process: {total_lines}")
    print("Processing files...")
    print()
    
    # Process files and write CSV
    processed_lines = 0
    valid_records = 0
    
    with open(OUTPUT_FILE, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=REQUIRED_COLUMNS)
        writer.writeheader()
        
        for year in range(START_YEAR, END_YEAR + 1):
            year_file = DATA_DIR / f"h{year}" / f"h{year}"
            
            if year_file.exists():
                print(f"Processing: h{year}")
                
                with open(year_file, 'r', encoding='utf-8', errors='ignore') as f:
                    for line in f:
                        if line.strip():
                            processed_lines += 1
                            record = parse_jma_record(line)
                            
                            if record is not None:
                                writer.writerow(record)
                                valid_records += 1
                            
                            # Show progress every 500 lines
                            if processed_lines % 500 == 0:
                                percentage = round((processed_lines / total_lines) * 100, 1)
                                print(f"  Progress: {percentage}% ({processed_lines}/{total_lines} lines) - {valid_records} valid records", end='\r')
                
                print(f"  h{year} complete - {valid_records} valid records so far" + " " * 50)
    
    print(f"\n=== Success! ===")
    print(f"Output file: {OUTPUT_FILE}")
    print(f"Total lines processed: {processed_lines}")
    print(f"Valid records saved: {valid_records}")
    print()


if __name__ == "__main__":
    main()
