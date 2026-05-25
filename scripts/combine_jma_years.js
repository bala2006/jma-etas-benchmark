#!/usr/bin/env node
/**
 * Fast JMA year data combination script using Node.js
 * Parses fixed-width format files and exports to CSV with required columns.
 */

const fs = require('fs');
const path = require('path');
const readline = require('readline');

// Configuration
const DATA_DIR = path.join(__dirname, '..', 'data', 'raw', 'jma_downloads');
const OUTPUT_FILE = path.join(__dirname, '..', 'data', 'raw', 'jma_tohoku_2010_2023.csv');
const START_YEAR = 2010;
const END_YEAR = 2023;

const REQUIRED_COLUMNS = ['Date', 'Time', 'Latitude(°N)', 'Longitude(°E)', 'Depth(km)', 'Mag'];

/**
 * Parse JMA magnitude field including special encodings
 */
function parseMagnitude(magText) {
  magText = magText.trim();
  if (!magText) return null;

  // Check for letter encoding (A0=-1.0, A9=-1.9, B0=-2.0, etc.)
  if (magText.length === 2 && isNaN(magText[0]) && !isNaN(magText[1])) {
    const letterOffset = magText.charCodeAt(0) - 'A'.charCodeAt(0) + 1;
    return -(letterOffset + parseInt(magText[1]) / 10.0);
  }

  // Try parsing as float
  const mag = parseFloat(magText);
  if (!isNaN(mag)) {
    // JMA magnitude stored in tenths, e.g., "34" means M3.4
    return mag / 10.0;
  }

  return null;
}

/**
 * Parse one fixed-width JMA hypocenter record
 */
function parseJMARecord(line) {
  if (line.length < 55 || !['J', 'U', 'I'].includes(line[0])) {
    return null;
  }

  try {
    // Parse date/time fields (fixed positions)
    const year = parseInt(line.substring(1, 5));
    const month = parseInt(line.substring(5, 7));
    const day = parseInt(line.substring(7, 9));
    const hour = parseInt(line.substring(9, 11));
    const minute = parseInt(line.substring(11, 13));

    // Second in hundredths
    const secondHundredths = parseFloat(line.substring(13, 17).trim());
    const second = Math.floor(secondHundredths / 100.0);

    // Latitude: degrees (pos 21-24) and minutes (pos 24-28)
    const latDeg = parseFloat(line.substring(21, 24).trim());
    const latMin = parseFloat(line.substring(24, 28).trim());
    const latitude = latDeg + latMin / 60.0;

    // Longitude: degrees (pos 32-36) and minutes (pos 36-40)
    const lonDeg = parseFloat(line.substring(32, 36).trim());
    const lonMin = parseFloat(line.substring(36, 40).trim());
    const longitude = lonDeg + lonMin / 60.0;

    // Depth in hundredths of km (pos 44-49)
    const depthHundredths = parseFloat(line.substring(44, 49).trim());
    const depth = depthHundredths / 100.0;

    // Magnitude (pos 49-51)
    const magnitude = parseMagnitude(line.substring(49, 51));

    if (magnitude === null) return null;

    const date = `${year.toString().padStart(4, '0')}-${month.toString().padStart(2, '0')}-${day.toString().padStart(2, '0')}`;
    const time = `${hour.toString().padStart(2, '0')}:${minute.toString().padStart(2, '0')}:${second.toString().padStart(2, '0')}`;

    return {
      Date: date,
      Time: time,
      'Latitude(°N)': Math.round(latitude * 100000) / 100000,
      'Longitude(°E)': Math.round(longitude * 100000) / 100000,
      'Depth(km)': Math.round(depth * 100) / 100,
      Mag: Math.round(magnitude * 10) / 10,
    };
  } catch (error) {
    return null;
  }
}

/**
 * Count lines in a file
 */
async function countLines(filePath) {
  return new Promise((resolve, reject) => {
    let lineCount = 0;
    const rl = readline.createInterface({
      input: fs.createReadStream(filePath),
      crlfDelay: Infinity,
    });

    rl.on('line', (line) => {
      if (line.trim()) lineCount++;
    });

    rl.on('close', () => resolve(lineCount));
    rl.on('error', reject);
  });
}

/**
 * Process a single year file and return records
 */
async function processYearFile(yearFile) {
  return new Promise((resolve, reject) => {
    const records = [];
    const rl = readline.createInterface({
      input: fs.createReadStream(yearFile),
      crlfDelay: Infinity,
    });

    rl.on('line', (line) => {
      if (line.trim()) {
        const record = parseJMARecord(line);
        if (record) {
          records.push(record);
        }
      }
    });

    rl.on('close', () => resolve(records));
    rl.on('error', reject);
  });
}

/**
 * Format record as CSV row
 */
function formatCSVRow(record) {
  return REQUIRED_COLUMNS
    .map((col) => {
      const value = record[col];
      return typeof value === 'string' && value.includes(',') ? `"${value}"` : value;
    })
    .join(',');
}

/**
 * Main processing function
 */
async function main() {
  console.log('Starting JMA data combination script...');
  console.log(`Data directory: ${DATA_DIR}`);
  console.log(`Output file: ${OUTPUT_FILE}`);
  console.log('');

  // Count total lines for progress
  console.log('Counting total lines...');
  let totalLines = 0;
  const fileCounts = {};

  for (let year = START_YEAR; year <= END_YEAR; year++) {
    const yearFile = path.join(DATA_DIR, `h${year}`, `h${year}`);
    if (fs.existsSync(yearFile)) {
      const count = await countLines(yearFile);
      fileCounts[year] = count;
      totalLines += count;
      console.log(`  h${year}: ${count} lines`);
    } else {
      console.log(`  h${year}: File not found`);
    }
  }

  console.log(`\nTotal lines to process: ${totalLines}`);
  console.log('Processing files...');
  console.log('');

  // Write CSV header
  const csvStream = fs.createWriteStream(OUTPUT_FILE, { encoding: 'utf8' });
  csvStream.write(REQUIRED_COLUMNS.join(',') + '\n');

  let processedLines = 0;
  let validRecords = 0;

  for (let year = START_YEAR; year <= END_YEAR; year++) {
    const yearFile = path.join(DATA_DIR, `h${year}`, `h${year}`);

    if (fs.existsSync(yearFile)) {
      console.log(`Processing: h${year}`);

      const records = await processYearFile(yearFile);

      for (const record of records) {
        csvStream.write(formatCSVRow(record) + '\n');
        validRecords++;
        processedLines++;

        // Show progress every 500 lines
        if (processedLines % 500 === 0) {
          const percentage = ((processedLines / totalLines) * 100).toFixed(1);
          process.stdout.write(
            `  Progress: ${percentage}% (${processedLines}/${totalLines} lines) - ${validRecords} valid records\r`
          );
        }
      }

      console.log(`  h${year} complete - ${validRecords} valid records so far${' '.repeat(50)}`);
    }
  }

  csvStream.end();

  csvStream.on('finish', () => {
    console.log('\n=== Success! ===');
    console.log(`Output file: ${OUTPUT_FILE}`);
    console.log(`Total lines processed: ${processedLines}`);
    console.log(`Valid records saved: ${validRecords}`);
    console.log('');
  });

  csvStream.on('error', (error) => {
    console.error('Error writing to file:', error);
    process.exit(1);
  });
}

// Run the script
main().catch((error) => {
  console.error('Error:', error);
  process.exit(1);
});
