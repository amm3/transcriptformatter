# Flexible Sanity Check System

## Overview

The transcript reformatter now uses a flexible, intelligent sanity check system that applies different rules based on chunk characteristics. This prevents failures on edge cases like music/singing sections while maintaining quality control.

## Key Changes

### Before
- Strict: Any word difference = FAIL
- All-or-nothing: One bad chunk fails entire transcript
- No output unless everything perfect

### After
- Flexible: Different rules for different chunk sizes
- Chunk-specific decisions: Each chunk evaluated independently
- **Always saves output** with detailed error log
- Automatic fallback to original text when needed

## Decision Rules

The system applies rules in order, using the first match:

### Rule 1: Very Small Chunks (< 15 words)
```
IF original_word_count < 15:
    → Use ORIGINAL (unmodified)
```

**Rationale:** Very small chunks (like song lyrics, short interjections) are prone to LLM over-correction. Safer to keep original.

**Example:**
```
Original: "la la la singing time" (5 words)
Reformed: "la singing time" (3 words)
Action: Use ORIGINAL
```

### Rule 2: One Word Difference (≤ 1 word delta)
```
IF word_count_delta <= 1:
    → Use REFORMATTED
```

**Rationale:** Single word differences are usually acceptable formatting improvements (like removing filler words).

**Example:**
```
Original: "So um I think that..." (20 words)
Reformed: "So I think that..." (19 words, -1 word)
Action: Use REFORMATTED (acceptable)
```

### Rule 3: Large Chunks with Small Percentage Difference
```
IF original_word_count > 70 AND percent_difference < 6%:
    → Use REFORMATTED
```

**Rationale:** In large chunks, small percentage differences are acceptable. 5 words in 100 (5%) is less concerning than 5 words in 10 (50%).

**Example:**
```
Original: 100 words
Reformed: 98 words (2% difference)
Action: Use REFORMATTED (acceptable)

Original: 100 words
Reformed: 92 words (8% difference)
Action: Use ORIGINAL (exceeds 6% threshold)
```

### Rule 4: Global Tolerance Setting
```
IF word_count_delta <= sanity_check_tolerance:
    → Use REFORMATTED
```

**Rationale:** Respects user-configured tolerance from config file.

### Rule 5: Default - Exceeds All Thresholds
```
ELSE:
    → Use ORIGINAL (unmodified)
```

**Rationale:** When chunk doesn't meet any acceptance criteria, keep it safe by using original.

## Error Logging

### Automatic Error Log Creation

When any chunk has issues, an error log is automatically created:

**Filename:** `<output_name>.errors.<ext>`

**Example:**
- Output: `transcript_reformatted.txt`
- Error log: `transcript_reformatted.errors.txt`

### Error Log Format

```
================================================================================
TRANSCRIPT REFORMATTER - SANITY CHECK ISSUES LOG
================================================================================

Input file: original.txt
Output file: transcript_reformatted.txt
Total chunks: 25
Chunks with issues: 3
Timestamp: 2025-10-28 15:30:45

================================================================================

CHUNK 5: Alex
--------------------------------------------------------------------------------
Rule Applied: Small chunk (< 15 words)
Original word count: 8
Reformatted word count: 7
Action: Using ORIGINAL (unmodified)

Details:
Word count mismatch: 8 vs 7
  First difference at position 3:
  Original  [...la la la singing time here...]
  Reformed  [...la la singing time here...]

================================================================================

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

================================================================================

CHUNK 18: Alex
--------------------------------------------------------------------------------
Rule Applied: Large chunk (> 70 words) with < 6% difference
Original word count: 120
Reformatted word count: 117
Delta: 3
Percentage difference: 2.50%
Action: Using REFORMATTED (acceptable)

Details:
Word count mismatch: 120 vs 117
  First difference at position 45:
  Original  [...and you know the tradition...]
  Reformed  [...and the tradition...]

================================================================================

SUMMARY
================================================================================
Chunks using ORIGINAL text: 1
Chunks using REFORMATTED text: 2
Total issues logged: 3
```

## Usage

### No Changes Needed!

The flexible system works automatically:

```bash
./transcript_reformatter.py transcript.txt -o output.txt -v
```

### What You'll See

**Console Output:**
```
INFO:Processing chunk 5/25: Alex
WARNING:  ⚠ Chunk 5: Small chunk (8 words), using original
INFO:Processing chunk 12/25: Ashley  
WARNING:  ⚠ Chunk 12: 1 word difference, within tolerance
INFO:Processing chunk 18/25: Alex
WARNING:  ⚠ Chunk 18: 2.50% difference, within tolerance
...
OUTPUT:⚠ WARNING: 3 chunk(s) had word count differences
OUTPUT:  Chunks with issues: 5, 12, 18
OUTPUT:  See error log for details
OUTPUT:Reformatted transcript saved to: output.txt
OUTPUT:Error log saved to: output.errors.txt
```

**Files Created:**
- `output.txt` - Reformatted transcript (always saved)
- `output.errors.txt` - Detailed error log (if any issues)

### With Verbose Mode

```bash
./transcript_reformatter.py transcript.txt -vv
```

Shows detailed decision-making for each chunk.

## Configuration

### Tolerance Setting

The `sanity_check_tolerance` setting still works:

```ini
[openai]
sanity_check_tolerance = 2
```

This adds Rule 4 that accepts up to 2 word differences for any chunk size.

### Disabling Sanity Check

```bash
./transcript_reformatter.py transcript.txt --skip-sanity-check
```

Skips all checking (not recommended).

## Examples

### Example 1: Music Section

**Input:**
```
[Chunk 8: Unknown]
(singing) la la la la la la la
```

**Process:**
- Original: 7 words
- Reformed: 5 words (LLM tried to "fix" it)
- **Rule 1 applies:** < 15 words → Use ORIGINAL
- **Result:** Original lyrics preserved

### Example 2: Filler Word Removal

**Input:**
```
[Chunk 12: Ashley]
So um I was thinking that you know we could maybe...
```

**Process:**
- Original: 45 words
- Reformed: 44 words (removed "um")
- **Rule 2 applies:** Delta = 1 → Use REFORMATTED
- **Result:** Cleaner text accepted

### Example 3: Long Passage

**Input:**
```
[Chunk 18: Alex]
(120-word theological explanation)
```

**Process:**
- Original: 120 words
- Reformed: 117 words (3 words, 2.5% diff)
- **Rule 3 applies:** >70 words AND <6% → Use REFORMATTED
- **Result:** Minor improvements accepted

### Example 4: Problem Chunk

**Input:**
```
[Chunk 22: Alex]
(30-word passage with unusual content)
```

**Process:**
- Original: 30 words
- Reformed: 25 words (5 words, 16.7% diff)
- **Rule 5 applies:** Exceeds thresholds → Use ORIGINAL
- **Result:** Problematic reformatting rejected

## Benefits

### 1. Robustness
- No longer fails on edge cases (music, singing, unusual content)
- Handles long transcripts with varied content

### 2. Transparency
- Every decision logged with rationale
- Easy to review what happened
- Clear audit trail

### 3. Flexibility
- Different rules for different chunk characteristics
- Adapts to content type automatically

### 4. Safety
- Always saves output (no lost work)
- Falls back to original when uncertain
- Preserves problematic chunks unmodified

### 5. Quality Control
- Still catches major issues
- Accepts minor improvements
- Detailed reporting for review

## Testing

### Run Test Suite

```bash
python3 test_flexible_sanity_check.py
```

**Expected Output:**
```
Test 1: Rule 1: Very small chunk (< 15 words)
✓ PASS: Action matches expected

Test 2: Rule 2: Delta exactly 1 word (medium chunk)
✓ PASS: Action matches expected

Test 3: Rule 3: Large chunk (>70) with 2% difference
✓ PASS: Action matches expected

Test 4: Rule 3 FAIL: Large chunk (>70) with 8% difference
✓ PASS: Action matches expected

Test 5: Edge case: Exactly 15 words with 2 word delta
✓ PASS: Action matches expected
```

## Troubleshooting

### Too Many Original Chunks Being Used

If too many chunks are falling back to original:
- Check error log to see which rules are triggering
- Consider adjusting the 15-word threshold (edit script)
- Review if LLM is being too aggressive with changes

### Too Many Reformatted Chunks Accepted

If you want stricter checking:
- Set `sanity_check_tolerance = 0` in config
- Chunks must exactly match (except within rule thresholds)

### Want to Review Specific Chunk

Check error log:
- Find chunk number
- See original vs reformed word counts
- Review decision rationale
- Manually inspect that section in output

## Rule Priorities

Rules are checked in this exact order:

1. **Rule 1 (Size)** - Always checked first
2. **Rule 2 (Delta)** - If not caught by Rule 1
3. **Rule 3 (Percentage)** - For large chunks only
4. **Rule 4 (Tolerance)** - User-configured fallback
5. **Rule 5 (Default)** - Last resort

## Summary

The flexible sanity check system:
- ✅ Handles edge cases gracefully
- ✅ Always produces output
- ✅ Logs all decisions transparently
- ✅ Adapts to chunk characteristics
- ✅ Maintains quality control
- ✅ Falls back safely when uncertain
- ✅ No configuration required (but configurable)

Your transcripts now process successfully even with challenging content like music sections, while maintaining quality where it matters!
