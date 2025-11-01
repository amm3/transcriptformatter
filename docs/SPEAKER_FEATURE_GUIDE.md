# Speaker-Aware Transcript Formatting

## Overview

The transcript reformatter now intelligently detects and processes transcripts with speaker identification and timestamps (such as those exported from DaVinci Resolve).

## Features

### 🎯 Automatic Detection
- Detects timestamp format: `[HH:MM:SS:FF - HH:MM:SS:FF]`
- Identifies speaker names (non-indented line after timestamp)
- Handles segments without speaker identification (indented text)
- Falls back to single-document processing if no structure detected

### 📊 Speaker Grouping
- Groups consecutive statements from the same speaker
- Sends each speaker's grouped text to the LLM as a batch
- Maintains speaker context for better formatting
- **Intelligently merges parenthetical interjections** (like audience reactions) into surrounding speaker's text when appropriate

### ✅ Per-Speaker Sanity Checking
- Performs sanity check on each speaker chunk independently
- Reports which speaker's chunk failed (if any)
- Provides detailed error messages per speaker

### 📝 Output Format
- Outputs speaker names followed by their formatted text
- Separates speakers with blank lines
- Preserves speaker attribution throughout

## Input Format

### DaVinci Resolve Format
```
[01:01:40:13 - 01:01:45:19]
Alex
 telling the story from Exodus to Maccabees. So about 1500 years

[01:01:45:19 - 01:01:49:03]
Alex
 of biblical history they have to tell in five to seven minutes.

[01:01:49:03 - 01:01:51:17]
Ashley
 Michael, are you ready to come up and give a demonstration?

[01:01:52:22 - 01:01:53:17]
 (Audience Laughing)

[01:01:53:17 - 01:01:55:05]
Alex
 He did great, he passed.
```

### Format Rules
1. **Timestamp Line**: `[HH:MM:SS:FF - HH:MM:SS:FF]` format
2. **Speaker Line**: Non-indented text immediately after timestamp
3. **Text Lines**: Indented text (starts with space/tab)
4. **No Speaker**: If text is indented right after timestamp, no speaker is present

### Handling Unknown Speakers
Segments without speaker identification (like audience reactions) are handled intelligently:

**Parenthetical interjections** between the same speaker are automatically merged:
```
[01:01:40:13 - 01:01:45:19]
Alex
 So I was explaining the tradition

[01:01:46:00 - 01:01:47:00]
 (Audience Laughing)

[01:01:48:00 - 01:01:50:00]
Alex
 and that was my point
```

**Results in:**
```
Alex:
So I was explaining the tradition (Audience Laughing) and that was my point.
```

**Non-parenthetical or between different speakers** are kept separate as (Unknown).

## Usage

### Basic Usage (Auto-Detection)
```bash
./transcript_reformatter.py resolve_export.txt -o formatted.txt -v
```

The script automatically detects if the input has speaker structure.

### Expected Output
```
2025-10-28 14:32:10:INFO:Detected speaker-based transcript with 11 segments
2025-10-28 14:32:10:INFO:Grouped into 6 speaker chunks
2025-10-28 14:32:10:INFO:Processing chunk 1/6: Alex
2025-10-28 14:32:12:DEBUG:  ✓ Chunk 1 passed sanity check
2025-10-28 14:32:12:INFO:Processing chunk 2/6: Ashley
2025-10-28 14:32:14:DEBUG:  ✓ Chunk 2 passed sanity check
...
2025-10-28 14:32:25:INFO:✓ All chunks passed sanity check
2025-10-28 14:32:25:OUTPUT:✓ Sanity check passed: All words preserved across all speakers
```

## Output Format Examples

### With Speakers Detected
```
Alex:
Telling the story from Exodus to Maccabees, so about 1500 years of biblical 
history, they have to tell in five to seven minutes.

Ashley:
Michael, are you ready to come up and give a demonstration?

(Unknown):
(Audience Laughing)

Alex:
He did great, he passed. But there is a long and revered tradition in scripture 
of storytelling and I think that's really important.

Ashley:
Can you elaborate on that tradition a bit more? I think the audience would 
benefit from understanding the context.

Alex:
Absolutely. The oral tradition was how stories were passed down for generations 
before they were ever written down.
```

### Without Speakers (Plain Text)
If no speaker structure is detected, the script processes it as before:
```
This is just regular text that will be formatted into nice paragraphs without 
any speaker attribution. The reformatter will handle it as a single document.
```

## Processing Details

### How It Works

1. **Parse Phase**
   - Scans for timestamp patterns
   - Identifies speaker names vs. text content
   - Creates individual segments for each timestamped section

2. **Grouping Phase**
   - Combines consecutive segments from same speaker
   - Example: 3 Alex segments + 2 Ashley segments → 2 chunks

3. **Formatting Phase**
   - Sends each speaker's grouped text to LLM
   - Formats independently for better context
   - Maintains speaker identity

4. **Sanity Check Phase**
   - Checks each chunk independently
   - Reports per-speaker results
   - Fails entire job if any chunk fails (unless --save-failed)

5. **Assembly Phase**
   - Combines all formatted chunks
   - Adds speaker labels
   - Writes final output

## Error Handling

### Per-Speaker Sanity Check Failures

If a specific speaker's chunk fails sanity check:

```
2025-10-28 14:35:12:ERROR:  ✗ Chunk 3 FAILED sanity check!
2025-10-28 14:35:12:ERROR:  Speaker: Alex
2025-10-28 14:35:12:ERROR:  Word count mismatch: 245 vs 244
2025-10-28 14:35:12:OUTPUT:ERROR in chunk 3 (Alex): Word count mismatch: 245 vs 244
  First difference at position 123:
  Original  [...we ask it from you give us grace...]
  Reformed  [...we ask it from you. Give us grace...]
```

### Saving Failed Output

Use `--save-failed` to save output even if chunks fail:
```bash
./transcript_reformatter.py resolve_export.txt --save-failed -vv
```

This allows you to:
- Inspect which speaker's section had issues
- Manually review the changes
- Decide if the differences are acceptable

## Testing

### Test the Parser
```bash
python3 test_speaker_parsing.py
```

This shows:
- How the transcript is parsed into segments
- How segments are grouped by speaker
- Preview of each chunk

### Test with Sample File
```bash
./transcript_reformatter.py sample_speaker_transcript.txt -o test_output.txt -vv
```

## Configuration

All existing configuration options work with speaker-aware processing:

```ini
[openai]
api_key = your-key-here
model = gpt-4o
max_tokens = 16000
temperature = 0.3
max_continuations = 10
sanity_check_tolerance = 0
```

## Advantages of Speaker-Aware Processing

### Better Context
- Each speaker's text is formatted with their speaking style in mind
- Natural paragraph breaks within each speaker's sections
- Preserves conversational flow

### More Accurate Sanity Checks
- Smaller chunks = easier to verify
- Can identify which speaker had issues
- More granular error reporting

### Efficient Processing
- Batches reduce API calls compared to per-timestamp processing
- Maintains speaker continuity
- Optimal chunk sizes for LLM processing

## Limitations

### Timestamp Information
- Currently timestamps are parsed but not included in output
- Future versions may optionally include timestamps
- Start timestamps are preserved in parsing but not displayed

### Speaker Name Variations
- Speaker names must be consistent (case-sensitive)
- "Alex" and "alex" are treated as different speakers
- Future enhancement could normalize speaker names

### Format Requirements
- Must follow DaVinci Resolve export format
- Other formats require custom parsing logic
- Mixed formats in one file may not parse correctly

## Troubleshooting

### "No speaker information detected"
This is normal if:
- Input is plain text without timestamps
- Timestamps don't match the expected format
- File uses a different transcription format

The script will still process it as a single document.

### Speaker wrongly identified
Check that:
- Speaker name is not indented
- Speaker name is on line immediately after timestamp
- Text lines are indented with space or tab

### Chunks seem too small/large
The grouping algorithm combines consecutive segments from the same speaker. If you need different behavior, this could be made configurable in future versions.

## Examples

### Example 1: DaVinci Resolve Export
```bash
# Process a DaVinci Resolve transcript export
./transcript_reformatter.py interview_export.txt -o interview_formatted.txt -v
```

### Example 2: With Debug Output
```bash
# See detailed parsing and processing
./transcript_reformatter.py panel_discussion.txt -vv
```

### Example 3: With Tolerance
```bash
# Allow 1 word difference per speaker chunk
# (Set sanity_check_tolerance = 1 in config first)
./transcript_reformatter.py long_interview.txt -v
```

## Future Enhancements

Potential additions in future versions:
- Optional timestamp inclusion in output
- Speaker name normalization
- Custom timestamp format support
- Configurable grouping strategies
- Speaker statistics in output

## Summary

The speaker-aware processing:
- ✅ Automatically detects speaker structure
- ✅ Groups by speaker for efficient processing
- ✅ Maintains speaker attribution
- ✅ Performs per-speaker sanity checks
- ✅ Falls back gracefully for plain text
- ✅ Works with existing configuration
- ✅ Provides detailed error reporting

Use it just like before - the speaker detection is automatic!
