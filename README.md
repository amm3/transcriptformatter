# Transcript Reformatter

A Python tool that uses OpenAI's API to reformat machine-transcribed audio/video text into clean, well-formatted paragraphs without changing any words.

## Features

- **Automatic Continuation**: Detects when responses are truncated and automatically requests continuation
- **Smart Output Cleaning**: Removes extraneous chat-like responses, keeping only the reformatted text
- **Speaker-Aware Processing**: Automatically detects and handles transcripts with speaker identification (DaVinci Resolve format)
- **Parenthetical Interjection Merging**: Intelligently merges audience reactions (laughing, applause) into surrounding speaker blocks
- **Flexible Sanity Checking**: Smart word-count verification with multiple rules for different chunk sizes
- **Fuzzy Word Matching**: Accepts plural variations and sound-alike corrections (their/there, parent/parents)
- **Paragraph-Level Timestamps**: When enabled, timestamps appear at every paragraph break for granular navigation
- **DaVinci Resolve Auto-Adjustment**: Automatically detects and adjusts DaVinci timelines (subtracts 1 hour offset)
- **Progress Status Updates**: Real-time progress reporting to STDERR independent of log verbosity
- **Configuration File Support**: Store API keys and settings in a config file
- **Multiple File Processing**: Process multiple transcripts in one command
- **Verbose Logging**: Optional debug output for troubleshooting

## Installation

1. **Install Python 3.7+** (if not already installed)

2. **Install required package**:
   ```bash
   pip install openai --break-system-packages
   ```

3. **Download the script files**:
   - `transcript_reformatter.py`
   - `transcript_reformatter.conf.example` (rename to `transcript_reformatter.conf`)

4. **Make the script executable** (Linux/Mac):
   ```bash
   chmod +x transcript_reformatter.py
   ```

## Configuration

1. **Edit the configuration file** (`transcript_reformatter.conf`):
   ```ini
   [openai]
   api_key = sk-your-actual-api-key-here
   model = gpt-4o
   max_tokens = 16000
   temperature = 0.3
   max_continuations = 10
   ```

2. **Get your OpenAI API key**:
   - Go to https://platform.openai.com/api-keys
   - Create a new API key
   - Copy and paste it into the config file

3. **Configuration file lookup order**:
   - First: Same directory as the script
   - Second: Current working directory
   - Third: Path specified with `-c` option

## Usage

### Basic Usage

```bash
./transcript_reformatter.py transcript.txt
```

This will create `transcript_reformatted.txt` in the same directory.

### With Timestamps

```bash
./transcript_reformatter.py transcript.txt --timestamps
```

Adds timestamps at speaker changes AND paragraph breaks for granular navigation.

### Specify Output File

```bash
./transcript_reformatter.py transcript.txt -o output.txt
```

### Process Multiple Files

```bash
./transcript_reformatter.py file1.txt file2.txt file3.txt
```

### Use Custom Config File

```bash
./transcript_reformatter.py transcript.txt -c /path/to/my/config.conf
```

### Verbose Output

```bash
# Info level logging
./transcript_reformatter.py transcript.txt -v

# Debug level logging (shows API responses)
./transcript_reformatter.py transcript.txt -vv
```

### Advanced Options

```bash
# Skip sanity check (not recommended)
./transcript_reformatter.py transcript.txt --skip-sanity-check

# Disable DaVinci timestamp adjustment
./transcript_reformatter.py davinci_export.txt --timestamps --disable-timestamp-adjustment
```

## How It Works

1. **Reads your transcript file** containing raw machine-transcribed text
   - Automatically detects if transcript has speaker identification (DaVinci Resolve format)
   - Groups consecutive statements by the same speaker
   - Merges parenthetical interjections (audience reactions) into speaker blocks

2. **Sends to OpenAI API** with instructions to:
   - Reformat into logical paragraphs
   - Fix punctuation and line breaks
   - NOT change any words
   - Process each speaker's grouped text independently (if speakers detected)

3. **Monitors the response**:
   - If complete: saves the reformatted text
   - If truncated: automatically requests continuation
   - Repeats until complete or max_continuations reached
   - Shows progress updates to STDERR

4. **Cleans the output**:
   - Removes phrases like "Here is the reformatted text:"
   - Removes questions like "Would you like me to continue?"
   - Keeps only the actual reformatted transcript

5. **Performs flexible sanity check**:
   - Compares original and reformatted text word-by-word (per speaker if applicable)
   - Uses fuzzy matching to accept plural variations (parent→parents) and sound-alikes (their→there)
   - Applies different rules based on chunk size:
     - Small chunks (< 15 words): Always use original (protects lyrics/interjections)
     - Single word differences: Accept reformatted (filler word removal)
     - Large chunks (> 70 words) with < 6% difference: Accept reformatted
     - Otherwise: Use original text (safe fallback)
   - Creates detailed error log if issues found

6. **Adds timestamps** (if `--timestamps` flag used):
   - Detects DaVinci Resolve timelines (starting at 01:00:00:00)
   - Automatically subtracts 1 hour for YouTube compatibility (can be disabled)
   - Adds timestamps at speaker changes
   - Matches paragraphs back to original timestamps for granular navigation

7. **Saves the result** to a new file
   - With speaker attribution and optional timestamps
   - Creates error log (`.errors.txt`) if sanity check found issues

## Command-Line Options

```
positional arguments:
  files                 Transcript file(s) to process

optional arguments:
  -h, --help            Show help message
  -c CONFIG, --config CONFIG
                        Configuration file path
  -o OUTPUT, --output OUTPUT
                        Output file (default: input_reformatted.txt)
  --skip-sanity-check   Skip word comparison sanity check (not recommended)
  --timestamps          Include timestamps in output (at speaker and paragraph breaks)
  --disable-timestamp-adjustment
                        Disable automatic 1-hour subtraction for DaVinci Resolve timelines
  -v                    Print extra info (INFO level)
  -vv                   Print more extra info (DEBUG level)
```

## Configuration Options

### `[openai]` Section

- **`api_key`** (required): Your OpenAI API key
- **`model`** (default: `gpt-4o`): Which GPT model to use
  - Recommended: `gpt-4o` (fast and capable)
  - Alternatives: `gpt-4o-mini`, `gpt-4-turbo`, `o1-preview`
- **`max_tokens`** (default: `16000`): Maximum response length
  - Increase for longer transcripts
  - Decrease to save on API costs
- **`temperature`** (default: `0.3`): Response randomness (0.0-2.0)
  - Lower = more consistent
  - This task works best with low temperature
- **`max_continuations`** (default: `10`): Maximum continuation attempts
  - Prevents infinite loops
  - 10 is usually more than enough

### Advanced Configuration (in script)

These can be edited at the top of `transcript_reformatter.py`:

```python
# Sanity Check Thresholds
SMALL_CHUNK_THRESHOLD = 15        # Chunks below this use original
SINGLE_WORD_DELTA_THRESHOLD = 1   # Accept if delta ≤ this
LARGE_CHUNK_THRESHOLD = 70        # Chunks above this use % rule
LARGE_CHUNK_PERCENT_THRESHOLD = 6.0  # Accept if diff < this %

# Status Reporting
STATUS_REPORT_CHUNK_INTERVAL = 5   # Report every N chunks
STATUS_REPORT_TIME_INTERVAL = 30   # Report every N seconds
```

## Examples

### Example 1: Simple Transcript

**Input** (`interview.txt`):
```
um so yeah i was thinking that we could you know maybe start with
the introduction and then uh move on to the main points and
basically what i wanted to say was that the project has been
going really well
```

**Command**:
```bash
./transcript_reformatter.py interview.txt
```

**Output** (`interview_reformatted.txt`):
```
Um, so yeah, I was thinking that we could, you know, maybe start with the introduction and then move on to the main points. And basically what I wanted to say was that the project has been going really well.
```

### Example 2: Speaker-Based Transcript with Timestamps

**Input** (`panel.txt`):
```
[01:00:05:10 - 01:00:15:20]
Alex
 So I think the key issue here is really about understanding the fundamentals

[01:00:16:00 - 01:00:18:15]
 (Audience Applause)

[01:00:19:00 - 01:00:35:10]
Alex
 and building on that foundation we can move forward with confidence
```

**Command**:
```bash
./transcript_reformatter.py panel.txt --timestamps
```

**Output** (`panel_reformatted.txt`):
```
[00:00:05] **Alex:**

[00:00:05] So I think the key issue here is really about understanding the fundamentals (Audience Applause) and building on that foundation we can move forward with confidence.
```

Note:
- DaVinci timestamp automatically adjusted (01:00:05 → 00:00:05)
- Audience reaction merged into Alex's text
- Timestamp appears at speaker change but not duplicated on first paragraph

### Example 3: Long Transcript with Progress Updates

**Command**:
```bash
./transcript_reformatter.py long_sermon.txt --timestamps -v
```

**Output** (to STDERR - progress updates):
```
STATUS: Starting to process: long_sermon.txt
STATUS: Adjusting timestamps (-1 hour for DaVinci timeline)
STATUS: Processing chunk 1 of 47...
STATUS: Processing chunk 5 of 47...
STATUS: Processing chunk 10 of 47...
STATUS: Completed processing all 47 chunks
STATUS: Saving reformatted transcript...
STATUS: ✓ Saved: long_sermon_reformatted.txt
```

**Output** (to STDOUT - logs with `-v`):
```
2025-10-29 14:32:10:INFO:Loading configuration from: transcript_reformatter.conf
2025-10-29 14:32:10:INFO:Transcript length: 45000 characters
2025-10-29 14:32:10:INFO:Detected DaVinci Resolve timeline (starts at 01:00:00:xx)
2025-10-29 14:32:10:INFO:Automatically subtracting 1 hour from all timestamps
2025-10-29 14:32:15:INFO:Processing chunk 1/47: Pastor
2025-10-29 14:32:20:WARNING:  ⚠ Chunk 12: 1 word difference, within tolerance
2025-10-29 14:32:25:OUTPUT:⚠ WARNING: 3 chunk(s) had word count differences
2025-10-29 14:32:25:OUTPUT:  Chunks with issues: 12, 23, 35
2025-10-29 14:32:25:OUTPUT:  See error log for details
2025-10-29 14:32:25:OUTPUT:Reformatted transcript saved to: long_sermon_reformatted.txt
2025-10-29 14:32:25:OUTPUT:Error log saved to: long_sermon_reformatted.errors.txt
```

### Example 4: Fuzzy Matching Success

**Original chunk**: "The parent knows their child well"
**Reformed chunk**: "The parents know there children well"

**Result**:
```
✓ Accepted with fuzzy matching:
  - parent → parents (plural)
  - knows → know (plural)
  - their → there (sound-alike)
```

Sanity check passes because all variations are acceptable!

## Timestamp Features

### DaVinci Resolve Auto-Adjustment

DaVinci Resolve starts timelines at `01:00:00:00`. The script:
- Automatically detects DaVinci timelines (first timestamp between 01:00:00 and 01:05:00)
- Subtracts 1 hour from all timestamps for YouTube compatibility
- Can be disabled with `--disable-timestamp-adjustment`

**Example**:
- DaVinci: `[01:00:15:10]` → Output: `[00:00:15]`
- DaVinci: `[01:25:30:00]` → Output: `[00:25:30]`

### Paragraph-Level Timestamps

When `--timestamps` is enabled:
- Timestamps appear at speaker changes (in header)
- Timestamps appear at each paragraph break (for navigation)
- First paragraph doesn't duplicate speaker timestamp
- Timestamps matched to original timing via intelligent word matching

**Example**:
```
[00:05:30] **Speaker:**

[00:05:30] First paragraph about topic A...

[00:08:15] Second paragraph transitioning to topic B...

[00:12:40] Final thoughts on this topic.
```

## Sanity Check System

### Flexible Rules

The sanity check uses intelligent rules based on chunk size:

1. **Small chunks (< 15 words)**: Always use original
   - Protects lyrics, short interjections from over-correction

2. **Single word difference**: Accept reformatted
   - Usually filler word removal ("um", "uh")

3. **Large chunks (> 70 words) with < 6% difference**: Accept reformatted
   - Minor improvements in large sections are acceptable

4. **Otherwise**: Use original (safe fallback)

### Fuzzy Word Matching

Automatically accepts:
- **Plural variations**: parent→parents, box→boxes, baby→babies
- **Sound-alikes**: their→there, your→you're, to→too, then→than

### Error Logging

If issues are found, creates `<output>.errors.txt`:
```
CHUNK 12: Ashley
--------------------------------------------------------------------------------
Rule Applied: Word delta ≤ 1
Original word count: 45
Reformatted word count: 44
Delta: 1
Action: Using REFORMATTED (acceptable)

Details:
1 word(s) differ
  Position 23: 'um' vs 'removed'
```

## Progress Status Updates

Status messages go to STDERR (separate from logs):
```bash
# See only status (hide logs)
./transcript_reformatter.py file.txt > /dev/null

# See only logs (hide status)
./transcript_reformatter.py file.txt 2>/dev/null

# Save status to file
./transcript_reformatter.py file.txt 2> progress.log
```

## Troubleshooting

### "API key not found in configuration file"
- Make sure `transcript_reformatter.conf` exists
- Check that the `api_key` line is uncommented and has your actual key

### "Configuration file not found"
- The script looks for `transcript_reformatter.conf` in:
  1. Same directory as the script
  2. Current working directory
- Or specify path with `-c` option

### "Error initializing OpenAI client"
- Verify your API key is correct
- Check that you have credits in your OpenAI account
- Ensure you've installed the openai package

### Response seems incomplete
- Increase `max_tokens` in the config file
- Increase `max_continuations` if hitting the limit
- Use `-vv` to see what's happening

### Sanity check warnings
- The flexible system may report acceptable variations
- Check the error log to see what changed
- Fuzzy matching accepts plural and sound-alike changes
- Original text is used for problematic chunks automatically

### Timestamps not appearing
- Use `--timestamps` flag
- Verify input has timestamps (e.g., DaVinci Resolve export)
- Check if transcript has speaker structure

### Wrong timestamp offset
- By default, DaVinci timelines are auto-adjusted (-1 hour)
- Use `--disable-timestamp-adjustment` to keep original times
- Adjustment only applies if first timestamp is 01:00:xx to 01:05:xx

## API Costs

Approximate costs (as of October 2024):
- **GPT-4o**: $2.50 / 1M input tokens, $10.00 / 1M output tokens
- **GPT-4o-mini**: $0.15 / 1M input tokens, $0.60 / 1M output tokens

A typical 10-minute interview transcript (~2000 words = ~2700 tokens) costs:
- GPT-4o: ~$0.03-0.05 per transcript
- GPT-4o-mini: ~$0.002-0.003 per transcript

## Documentation

Additional documentation available:
- `FLEXIBLE_SANITY_CHECK_GUIDE.md` - Detailed sanity check rules
- `FUZZY_MATCHING_FEATURE.md` - Plural and sound-alike matching
- `PARAGRAPH_TIMESTAMPS_FEATURE.md` - Paragraph-level timestamp system
- `DAVINCI_TIMESTAMP_ADJUSTMENT.md` - Auto-adjustment for DaVinci timelines
- `STATUS_REPORTING_FEATURE.md` - Progress update system

## License

This script is provided as-is for personal and commercial use.

## Support

For issues or questions:
1. Check the Troubleshooting section
2. Run with `-vv` to see detailed logs
3. Check the error log file if created
4. Verify your OpenAI API key and credits

## Tips

- **Start with smaller files** to test your setup
- **Use `-v` or `-vv`** to understand what's happening
- **Keep backups** of original transcripts
- **Use `--timestamps`** for video editing reference
- **Check error logs** to understand sanity check decisions
- **Monitor API costs** in your OpenAI dashboard
- **Adjust thresholds** in script if default rules don't fit your needs
