#!/usr/bin/env python3

import sys
import os
import argparse
import logging
import time
import configparser
import re
from openai import OpenAI
from typing import List, Tuple, Optional

DEFAULT_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
LOGGING_FORMAT = '%(asctime)s:%(levelname)s:%(message)s'
DEFAULT_CONFIG_FILE = "transcript_reformatter.conf"

# Sanity Check Thresholds
# These control the flexible sanity check system that decides whether to use
# original or reformatted text for each speaker chunk based on word count differences.
#
# Rule 1: Chunks smaller than SMALL_CHUNK_THRESHOLD always use original text
#         (small chunks like lyrics/interjections are prone to LLM over-correction)
SMALL_CHUNK_THRESHOLD = 15        # Words: chunks below this use original

# Rule 2: Chunks with word count delta <= SINGLE_WORD_DELTA_THRESHOLD use reformatted
#         (single word differences are usually acceptable formatting improvements)
SINGLE_WORD_DELTA_THRESHOLD = 1   # Words: accept reformatted if delta is this or less

# Rule 3: Large chunks (> LARGE_CHUNK_THRESHOLD) with percentage difference
#         < LARGE_CHUNK_PERCENT_THRESHOLD use reformatted
#         (small percentage differences in large chunks are acceptable)
LARGE_CHUNK_THRESHOLD = 70        # Words: chunks above this use percentage rule
LARGE_CHUNK_PERCENT_THRESHOLD = 6.0  # Percent: accept reformatted if diff is less than this

# Rule 4: Otherwise, use original text (safe fallback for problematic chunks)

# Status Reporting Configuration
# Report progress to STDERR at regular intervals
STATUS_REPORT_CHUNK_INTERVAL = 5   # Report every N chunks
STATUS_REPORT_TIME_INTERVAL = 30   # Report every N seconds (whichever comes first)

# Sound-alike word mappings for fuzzy matching
# Maps words that sound the same but are spelled differently
# Note: After normalization, apostrophes are removed (they're → theyre)
SOUND_ALIKE_GROUPS = [
    {'theyre', 'their', 'there'},
    {'your', 'youre'},
    {'its', 'its'},  # "its" (possessive) vs "it's" → "its" (contraction, normalized)
    {'to', 'too', 'two'},
    {'then', 'than'},
    {'hear', 'here'},
    {'where', 'wear', 'were'},
    {'know', 'no'},
    {'right', 'write'},
    {'our', 'hour'},
    {'through', 'threw'},
    {'by', 'buy', 'bye'},
    {'new', 'knew'},
    {'one', 'won'},
    {'for', 'four'},
    {'would', 'wood'},
    {'could', 'couldve'},  # "could've" → "couldve" after normalization
    {'should', 'shouldve'},  # "should've" → "shouldve"
    {'wont', 'want'},  # "won't" → "wont" after normalization
]

# Build reverse lookup map for faster checking
SOUND_ALIKE_MAP = {}
for group in SOUND_ALIKE_GROUPS:
    for word in group:
        SOUND_ALIKE_MAP[word] = group

SYSTEM_PROMPT = """You are a text formatting assistant. Your task is to reformat transcribed text into logical paragraph flow. 
CRITICAL RULES:
1. DO NOT CHANGE, ADD, OR REMOVE ANY WORDS - including repeated words
2. PRESERVE ALL WORDS EXACTLY - even if a word appears twice in a row
3. Only modify punctuation and line breaks
4. Format into natural paragraphs
5. Output ONLY the reformatted text with no preamble or explanation
6. Do not add any commentary, apologies, or meta-text"""

CONTINUATION_PROMPT = """Continue reformatting from where you left off. Remember:
1. DO NOT CHANGE, ADD, OR REMOVE ANY WORDS - including repeated words
2. PRESERVE ALL WORDS EXACTLY
3. Only modify punctuation and line breaks
4. Output ONLY the reformatted text with no preamble or explanation"""


def main():
    parser = argparse.ArgumentParser(
        description='Reformat transcribed text using OpenAI API with automatic continuation'
    )
    parser.add_argument('files', help="Transcript file(s) to process", nargs='*')
    parser.add_argument("-c", "--config", help="Configuration file path", default=None)
    parser.add_argument("-o", "--output", help="Output file (default: input_reformatted.txt)", default=None)
    parser.add_argument("--skip-sanity-check", action="store_true", default=False, 
                        help="Skip word comparison sanity check")
    parser.add_argument("--save-failed", action="store_true", default=False,
                        help="Save output even if sanity check fails (for inspection)")
    parser.add_argument("--timestamps", action="store_true", default=False,
                        help="Include timestamps in output (when speakers change)")
    parser.add_argument("--disable-timestamp-adjustment", action="store_true", default=False,
                        help="Disable automatic 1-hour subtraction for DaVinci Resolve timestamps")
    parser.add_argument("-v", action="store_true", default=False, help="Print extra info")
    parser.add_argument("-vv", action="store_true", default=False, help="Print (more) extra info")
    args = parser.parse_args()

    # Establish LOGLEVEL
    if args.vv:
        logging.basicConfig(format=LOGGING_FORMAT, datefmt=DEFAULT_TIME_FORMAT, level=logging.DEBUG)
    elif args.v:
        logging.basicConfig(format=LOGGING_FORMAT, datefmt=DEFAULT_TIME_FORMAT, level=logging.INFO)
    else:
        logging.basicConfig(format=LOGGING_FORMAT, datefmt=DEFAULT_TIME_FORMAT, level=logging.WARNING)

    # Load configuration
    config = load_config(args.config)
    
    # Validate configuration
    if not config.get('api_key'):
        log_fatal("API key not found in configuration file")
    
    # Process files
    if not args.files:
        log_fatal("No input files specified")
    
    for file_path in args.files:
        if not os.path.exists(file_path):
            log_error(f"File not found: {file_path}")
            continue
        
        log_info(f"Processing file: {file_path}")
        process_transcript(file_path, args.output, config, args.skip_sanity_check, args.save_failed, args.timestamps, args.disable_timestamp_adjustment)
    
    return 0


def load_config(config_path=None):
    """Load configuration from file with fallback logic"""
    config = {}
    
    # Determine config file path
    if config_path:
        config_file = config_path
        if not os.path.exists(config_file):
            log_fatal(f"Specified config file not found: {config_file}")
    else:
        # Look in execution directory first
        script_dir = os.path.dirname(os.path.abspath(__file__))
        config_file = os.path.join(script_dir, DEFAULT_CONFIG_FILE)
        
        if not os.path.exists(config_file):
            # Try current working directory
            config_file = os.path.join(os.getcwd(), DEFAULT_CONFIG_FILE)
        
        if not os.path.exists(config_file):
            log_fatal(f"Configuration file not found. Expected: {DEFAULT_CONFIG_FILE}")
    
    log_info(f"Loading configuration from: {config_file}")
    
    # Parse config file
    parser = configparser.ConfigParser()
    try:
        parser.read(config_file)
        
        if 'openai' not in parser:
            log_fatal("Configuration file missing [openai] section")
        
        config['api_key'] = parser.get('openai', 'api_key', fallback=None)
        config['model'] = parser.get('openai', 'model', fallback='gpt-4o')
        config['max_tokens'] = parser.getint('openai', 'max_tokens', fallback=16000)
        config['temperature'] = parser.getfloat('openai', 'temperature', fallback=0.3)
        config['max_continuations'] = parser.getint('openai', 'max_continuations', fallback=10)
        
        log_debug(f"Configuration loaded: model={config['model']}, max_tokens={config['max_tokens']}")
        
    except Exception as e:
        log_fatal(f"Error parsing configuration file: {e}")
    
    return config


def process_transcript(input_file, output_file, config, skip_sanity_check=False, save_failed=False, include_timestamps=False, disable_timestamp_adjustment=False):
    """Process a transcript file with automatic continuation and optional speaker awareness"""
    
    write_status(f"Starting to process: {input_file}")
    
    # Read input file
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            transcript_text = f.read()
    except Exception as e:
        log_error(f"Error reading file {input_file}: {e}")
        return
    
    if not transcript_text.strip():
        log_warning(f"File is empty: {input_file}")
        return
    
    log_info(f"Transcript length: {len(transcript_text)} characters")
    
    # Parse transcript to detect speakers
    segments, has_speakers = parse_transcript_with_speakers(transcript_text)
    
    if has_speakers:
        log_info(f"Detected speaker-based transcript with {len(segments)} segments")
        # Group consecutive segments by same speaker
        grouped_segments = group_segments_by_speaker(segments)
        log_info(f"Grouped into {len(grouped_segments)} speaker chunks")
        
        # Detect and adjust DaVinci Resolve timestamps if needed
        if not disable_timestamp_adjustment and detect_davinci_timeline(grouped_segments):
            log_info("Detected DaVinci Resolve timeline (starts at 01:00:00:xx)")
            log_info("Automatically subtracting 1 hour from all timestamps")
            write_status("Adjusting timestamps (-1 hour for DaVinci timeline)")
            
            # Adjust all timestamps in grouped segments
            for seg in grouped_segments:
                if seg.start_timestamp:
                    seg.start_timestamp = adjust_timestamp(seg.start_timestamp)
                
                # Adjust timestamps in timestamped_lines
                if seg.timestamped_lines:
                    seg.timestamped_lines = [
                        (adjust_timestamp(ts), text) 
                        for ts, text in seg.timestamped_lines
                    ]
        elif disable_timestamp_adjustment:
            log_debug("Timestamp adjustment disabled by --disable-timestamp-adjustment flag")
        
        for i, seg in enumerate(grouped_segments):
            speaker_name = seg.speaker if seg.speaker else "(Unknown)"
            log_debug(f"  Chunk {i+1}: {speaker_name} - {len(seg.text)} chars")
    else:
        log_info("No speaker information detected, processing as single document")
        grouped_segments = segments
    
    # Initialize OpenAI client
    try:
        client = OpenAI(api_key=config['api_key'])
    except Exception as e:
        log_fatal(f"Error initializing OpenAI client: {e}")
    
    # Process each speaker chunk
    reformatted_segments = []
    error_log = []
    chunks_with_issues = []
    
    # Status reporting tracking
    last_status_time = time.time()
    total_chunks = len(grouped_segments)
    
    for idx, segment in enumerate(grouped_segments):
        speaker_label = f"{segment.speaker}" if segment.speaker else "(Unknown Speaker)"
        log_info(f"Processing chunk {idx+1}/{len(grouped_segments)}: {speaker_label}")
        
        # Status reporting (every N chunks or N seconds, whichever comes first)
        current_time = time.time()
        time_since_last_status = current_time - last_status_time
        chunk_num = idx + 1
        
        should_report = False
        if chunk_num == 1:
            # Always report first chunk
            should_report = True
        elif chunk_num % STATUS_REPORT_CHUNK_INTERVAL == 0:
            # Report every N chunks
            should_report = True
        elif time_since_last_status >= STATUS_REPORT_TIME_INTERVAL:
            # Report if enough time has elapsed
            should_report = True
        
        if should_report:
            write_status(f"Processing chunk {chunk_num} of {total_chunks}...")
            last_status_time = current_time
        
        # Reformat this segment
        reformatted_text = reformat_with_continuation(
            client, 
            segment.text, 
            config
        )
        
        # Sanity check this segment
        use_original = False
        chunk_issue = None
        
        if not skip_sanity_check:
            log_debug(f"Sanity checking chunk {idx+1}...")
            original_words = normalize_text(segment.text)
            reformatted_words = normalize_text(reformatted_text)
            
            is_identical, comparison_result, fuzzy_count = compare_word_lists(original_words, reformatted_words)
            
            original_count = len(original_words)
            reformatted_count = len(reformatted_words)
            word_delta = abs(original_count - reformatted_count)
            
            if is_identical:
                log_debug(f"  ✓ Chunk {idx+1} passed sanity check")
            else:
                # Apply flexible rules using global thresholds
                
                # Rule 1: Very small chunks - use original
                if original_count < SMALL_CHUNK_THRESHOLD:
                    use_original = True
                    chunk_issue = {
                        'chunk': idx + 1,
                        'speaker': speaker_label,
                        'rule': f'Small chunk (< {SMALL_CHUNK_THRESHOLD} words)',
                        'original_count': original_count,
                        'reformatted_count': reformatted_count,
                        'action': 'Using ORIGINAL (unmodified)',
                        'details': comparison_result
                    }
                    log_warning(f"  ⚠ Chunk {idx+1}: Small chunk ({original_count} words), using original")
                
                # Rule 2: Delta <= 1 word - use reformatted
                elif word_delta <= SINGLE_WORD_DELTA_THRESHOLD:
                    chunk_issue = {
                        'chunk': idx + 1,
                        'speaker': speaker_label,
                        'rule': f'Word delta ≤ {SINGLE_WORD_DELTA_THRESHOLD}',
                        'original_count': original_count,
                        'reformatted_count': reformatted_count,
                        'delta': word_delta,
                        'action': 'Using REFORMATTED (acceptable)',
                        'details': comparison_result
                    }
                    log_warning(f"  ⚠ Chunk {idx+1}: {word_delta} word difference, within tolerance")
                
                # Rule 3: Large chunk with small percentage difference - use reformatted
                elif original_count > LARGE_CHUNK_THRESHOLD:
                    percent_diff = (word_delta / original_count) * 100
                    if percent_diff < LARGE_CHUNK_PERCENT_THRESHOLD:
                        chunk_issue = {
                            'chunk': idx + 1,
                            'speaker': speaker_label,
                            'rule': f'Large chunk (> {LARGE_CHUNK_THRESHOLD} words) with < {LARGE_CHUNK_PERCENT_THRESHOLD}% difference',
                            'original_count': original_count,
                            'reformatted_count': reformatted_count,
                            'delta': word_delta,
                            'percent_diff': f"{percent_diff:.2f}%",
                            'action': 'Using REFORMATTED (acceptable)',
                            'details': comparison_result
                        }
                        log_warning(f"  ⚠ Chunk {idx+1}: {percent_diff:.2f}% difference, within tolerance")
                    else:
                        use_original = True
                        chunk_issue = {
                            'chunk': idx + 1,
                            'speaker': speaker_label,
                            'rule': f'Large chunk but difference > {LARGE_CHUNK_PERCENT_THRESHOLD}%',
                            'original_count': original_count,
                            'reformatted_count': reformatted_count,
                            'delta': word_delta,
                            'percent_diff': f"{percent_diff:.2f}%",
                            'action': 'Using ORIGINAL (unmodified)',
                            'details': comparison_result
                        }
                        log_warning(f"  ⚠ Chunk {idx+1}: {percent_diff:.2f}% difference, exceeds {LARGE_CHUNK_PERCENT_THRESHOLD}% threshold")
                
                # Rule 4: Exceeds all thresholds - use original
                else:
                    use_original = True
                    chunk_issue = {
                        'chunk': idx + 1,
                        'speaker': speaker_label,
                        'rule': 'Exceeds all thresholds',
                        'original_count': original_count,
                        'reformatted_count': reformatted_count,
                        'delta': word_delta,
                        'action': 'Using ORIGINAL (unmodified)',
                        'details': comparison_result
                    }
                    log_error(f"  ✗ Chunk {idx+1}: Exceeds thresholds, using original")
                
                if chunk_issue:
                    error_log.append(chunk_issue)
                    chunks_with_issues.append(idx + 1)
        
        # Store segment (original or reformatted based on decision)
        final_text = segment.text if use_original else reformatted_text
        
        # Find paragraph timestamps if we have timestamped lines
        paragraph_timestamps = []
        if segment.timestamped_lines and include_timestamps:
            paragraph_timestamps = find_paragraph_timestamps(final_text, segment.timestamped_lines)
        
        reformatted_segments.append({
            'speaker': segment.speaker,
            'timestamp': segment.start_timestamp,
            'text': final_text,
            'original_text': segment.text,
            'was_modified': not use_original,
            'paragraph_timestamps': paragraph_timestamps  # List of (timestamp, paragraph_text)
        })
    
    # Summary
    write_status(f"Completed processing all {total_chunks} chunks")
    
    if not skip_sanity_check:
        if not error_log:
            log_info("✓ All chunks passed sanity check without issues")
            write_out("✓ Sanity check passed: All chunks processed successfully")
        else:
            log_warning(f"⚠ {len(error_log)} chunk(s) had issues (see error log)")
            write_out(f"⚠ WARNING: {len(error_log)} chunk(s) had word count differences")
            write_out(f"  Chunks with issues: {', '.join(map(str, chunks_with_issues))}")
            write_out(f"  See error log for details")
    
    # Assemble final output
    if has_speakers:
        output_parts = []
        for seg in reformatted_segments:
            # Add speaker header
            if seg['speaker']:
                # Format speaker line with optional timestamp
                if include_timestamps and seg['timestamp']:
                    formatted_ts = format_timestamp(seg['timestamp'])
                    output_parts.append(f"{formatted_ts} **{seg['speaker']}:**")
                else:
                    output_parts.append(f"{seg['speaker']}:")
            
            # Add text with paragraph-level timestamps if available
            if include_timestamps and seg.get('paragraph_timestamps'):
                # Use paragraph timestamps
                for idx, (para_ts, para_text) in enumerate(seg['paragraph_timestamps']):
                    # Skip timestamp on first paragraph if it matches the speaker timestamp
                    # (to avoid duplicate timestamps when speaker changes)
                    if idx == 0 and seg['speaker'] and para_ts == seg['timestamp']:
                        # First paragraph, speaker header already has timestamp
                        output_parts.append(para_text)
                    else:
                        # Subsequent paragraphs or different timestamp - include timestamp
                        if para_ts:
                            formatted_ts = format_timestamp(para_ts)
                            output_parts.append(f"{formatted_ts} {para_text}")
                        else:
                            output_parts.append(para_text)
                    output_parts.append('')  # Blank line after each paragraph
            else:
                # No paragraph timestamps, just add text as-is
                output_parts.append(seg['text'])
                output_parts.append('')  # Blank line between speakers
        
        final_output = '\n'.join(output_parts).strip()
    else:
        # No speakers, just concatenate (timestamps not applicable without speakers)
        final_output = '\n\n'.join(seg['text'] for seg in reformatted_segments)
    
    # Determine output file path
    if not output_file:
        base, ext = os.path.splitext(input_file)
        output_file = f"{base}_reformatted{ext}"
    
    # Write output
    write_status(f"Saving reformatted transcript...")
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(final_output)
        write_out(f"Reformatted transcript saved to: {output_file}")
        write_status(f"✓ Saved: {output_file}")
        log_info(f"Output length: {len(final_output)} characters")
    except Exception as e:
        log_error(f"Error writing output file: {e}")
        return
    
    # Write error log if there were issues
    if error_log and not skip_sanity_check:
        base, ext = os.path.splitext(output_file)
        error_log_file = f"{base}.errors{ext}"
        
        write_status(f"Saving error log...")
        try:
            with open(error_log_file, 'w', encoding='utf-8') as f:
                f.write("="*80 + "\n")
                f.write("TRANSCRIPT REFORMATTER - SANITY CHECK ISSUES LOG\n")
                f.write("="*80 + "\n\n")
                f.write(f"Input file: {input_file}\n")
                f.write(f"Output file: {output_file}\n")
                f.write(f"Total chunks: {len(grouped_segments)}\n")
                f.write(f"Chunks with issues: {len(error_log)}\n")
                f.write(f"Timestamp: {time.strftime(DEFAULT_TIME_FORMAT, time.localtime())}\n")
                f.write("\n" + "="*80 + "\n\n")
                
                for issue in error_log:
                    f.write(f"CHUNK {issue['chunk']}: {issue['speaker']}\n")
                    f.write("-"*80 + "\n")
                    f.write(f"Rule Applied: {issue['rule']}\n")
                    f.write(f"Original word count: {issue['original_count']}\n")
                    f.write(f"Reformatted word count: {issue['reformatted_count']}\n")
                    
                    if 'delta' in issue:
                        f.write(f"Word count delta: {issue['delta']}\n")
                    if 'percent_diff' in issue:
                        f.write(f"Percentage difference: {issue['percent_diff']}\n")
                    
                    f.write(f"Action: {issue['action']}\n")
                    f.write(f"\nDetails:\n{issue['details']}\n")
                    f.write("\n" + "="*80 + "\n\n")
                
                # Summary statistics
                original_used = sum(1 for i in error_log if 'ORIGINAL' in i['action'])
                reformatted_used = sum(1 for i in error_log if 'REFORMATTED' in i['action'])
                
                f.write("SUMMARY\n")
                f.write("="*80 + "\n")
                f.write(f"Chunks using ORIGINAL text: {original_used}\n")
                f.write(f"Chunks using REFORMATTED text: {reformatted_used}\n")
                f.write(f"Total issues logged: {len(error_log)}\n")
            
            write_out(f"Error log saved to: {error_log_file}")
            log_info(f"Error log written with {len(error_log)} issue(s)")
            
        except Exception as e:
            log_error(f"Error writing error log: {e}")


def format_timestamp(timestamp: Optional[str]) -> Optional[str]:
    """
    Format timestamp for output.
    Converts HH:MM:SS:FF to [HH:MM:SS] (dropping frame number).
    If no frame number present, keeps as is.
    
    Args:
        timestamp: String like "01:23:45:12" or "01:23:45" or None
    
    Returns:
        Formatted timestamp like "[01:23:45]" or None
    """
    if not timestamp:
        return None
    
    # Split by colon
    parts = timestamp.split(':')
    
    if len(parts) == 4:
        # HH:MM:SS:FF format - drop frames
        return f"[{parts[0]}:{parts[1]}:{parts[2]}]"
    elif len(parts) == 3:
        # HH:MM:SS format - keep as is
        return f"[{timestamp}]"
    else:
        # Unknown format, return as is in brackets
        return f"[{timestamp}]"


def detect_davinci_timeline(segments: List['SpeakerSegment']) -> bool:
    """
    Detect if timestamps appear to be from a DaVinci Resolve timeline starting at 01:00:00:00.
    
    DaVinci Resolve starts timelines at 01:00:00:00 (1 hour), so we check if the earliest
    timestamp is close to this value (within first 5 minutes after 01:00:00:00).
    
    Args:
        segments: List of parsed transcript segments with timestamps
    
    Returns:
        True if this appears to be a DaVinci timeline that needs adjustment
    """
    # Find earliest timestamp
    earliest = None
    for seg in segments:
        if seg.start_timestamp:
            if earliest is None:
                earliest = seg.start_timestamp
            else:
                # Simple string comparison works for HH:MM:SS:FF format
                if seg.start_timestamp < earliest:
                    earliest = seg.start_timestamp
    
    if not earliest:
        return False
    
    # Parse the earliest timestamp
    parts = earliest.split(':')
    if len(parts) < 3:
        return False
    
    try:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = int(parts[2])
        
        # Check if timestamp is in the range 01:00:00:xx to 01:04:59:xx
        # (first 5 minutes after 1 hour mark, strictly less than 5 minutes)
        if hours == 1 and (minutes < 5 or (minutes == 5 and seconds == 0)):
            log_debug(f"Detected DaVinci timeline: earliest timestamp is {earliest}")
            return True
        
        return False
    except (ValueError, IndexError):
        return False


def adjust_timestamp(timestamp: str, subtract_hours: int = 1) -> str:
    """
    Adjust a timestamp by subtracting hours.
    
    Args:
        timestamp: Timestamp string like "01:23:45:12"
        subtract_hours: Number of hours to subtract (default: 1)
    
    Returns:
        Adjusted timestamp string
    """
    if not timestamp:
        return timestamp
    
    parts = timestamp.split(':')
    if len(parts) < 3:
        return timestamp
    
    try:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = int(parts[2])
        frames = parts[3] if len(parts) > 3 else None
        
        # Subtract hours
        hours = max(0, hours - subtract_hours)
        
        # Rebuild timestamp
        if frames:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frames}"
        else:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    
    except (ValueError, IndexError):
        # If parsing fails, return original
        return timestamp


def is_plural_variant(word1: str, word2: str) -> bool:
    """
    Check if two words differ only by adding/removing 's' for plural.
    
    Args:
        word1: First word (normalized)
        word2: Second word (normalized)
    
    Returns:
        True if one is the plural of the other
    """
    if not word1 or not word2:
        return False
    
    # Check if word1 + 's' == word2 or word2 + 's' == word1
    if word1 + 's' == word2 or word2 + 's' == word1:
        return True
    
    # Check for 'es' plural (e.g., box/boxes, church/churches)
    if word1 + 'es' == word2 or word2 + 'es' == word1:
        return True
    
    # Check for 'ies' vs 'y' plural (e.g., baby/babies)
    if len(word1) > 2 and len(word2) > 2:
        if word1.endswith('ies') and word2.endswith('y'):
            if word1[:-3] == word2[:-1]:
                return True
        if word2.endswith('ies') and word1.endswith('y'):
            if word2[:-3] == word1[:-1]:
                return True
    
    return False


def are_sound_alikes(word1: str, word2: str) -> bool:
    """
    Check if two words are sound-alikes (homophones).
    
    Args:
        word1: First word (normalized, lowercase)
        word2: Second word (normalized, lowercase)
    
    Returns:
        True if words are in the same sound-alike group
    """
    if word1 == word2:
        return True
    
    # Check if both words are in the same sound-alike group
    if word1 in SOUND_ALIKE_MAP and word2 in SOUND_ALIKE_MAP:
        return SOUND_ALIKE_MAP[word1] == SOUND_ALIKE_MAP[word2]
    
    return False


def fuzzy_word_match(word1: str, word2: str) -> bool:
    """
    Check if two words are acceptably similar (exact match, plural variant, or sound-alike).
    
    Args:
        word1: First word (normalized)
        word2: Second word (normalized)
    
    Returns:
        True if words are acceptably similar
    """
    # Exact match
    if word1 == word2:
        return True
    
    # Plural variant check
    if is_plural_variant(word1, word2):
        return True
    
    # Sound-alike check
    if are_sound_alikes(word1, word2):
        return True
    
    return False


def normalize_text(text):
    """
    Normalize text by removing punctuation, converting to lowercase,
    and splitting into words (ignoring all whitespace).
    """
    # Remove all punctuation and convert to lowercase
    text = re.sub(r'[^\w\s]', '', text.lower())
    # Split into words, which automatically handles all whitespace
    words = text.split()
    return words


class SpeakerSegment:
    """Represents a segment of text from a single speaker"""
    def __init__(self, speaker: Optional[str], text: str, start_timestamp: Optional[str] = None, timestamped_lines: Optional[List[Tuple[str, str]]] = None):
        self.speaker = speaker
        self.text = text
        self.start_timestamp = start_timestamp
        # List of (timestamp, text) tuples for fine-grained timestamp tracking
        self.timestamped_lines = timestamped_lines or []
    
    def __repr__(self):
        return f"SpeakerSegment(speaker={self.speaker}, timestamp={self.start_timestamp}, text_len={len(self.text)}, lines={len(self.timestamped_lines)})"


def parse_transcript_with_speakers(text: str) -> Tuple[List[SpeakerSegment], bool]:
    """
    Parse transcript text that may contain timestamps and speaker identification.
    
    Returns:
        Tuple of (list of SpeakerSegments, has_speakers_flag)
    
    Format examples:
        [01:01:40:13 - 01:01:45:19]
        Alex
         telling the story from Exodus to Maccabees.
        
        [01:01:52:22 - 01:01:53:17]
         (Audience Laughing)
    """
    # Pattern for timestamps: [HH:MM:SS:FF - HH:MM:SS:FF]
    timestamp_pattern = r'\[(\d{2}:\d{2}:\d{2}:\d{2})\s*-\s*(\d{2}:\d{2}:\d{2}:\d{2})\]'
    
    lines = text.split('\n')
    segments = []
    
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # Check for timestamp
        timestamp_match = re.match(timestamp_pattern, line.strip())
        if timestamp_match:
            start_time = timestamp_match.group(1)
            
            # Next line might be speaker name (not indented) or text (indented)
            if i + 1 < len(lines):
                next_line = lines[i + 1]
                
                # Check if next line is indented (starts with space/tab)
                if next_line and (next_line[0] in (' ', '\t')):
                    # No speaker, text is indented
                    speaker = None
                    text_start_idx = i + 1
                else:
                    # Speaker name on next line
                    speaker = next_line.strip() if next_line.strip() else None
                    text_start_idx = i + 2
                
                # Collect text lines until next timestamp or end
                text_lines = []
                timestamped_lines = [(start_time, '')]  # Will accumulate text for this timestamp
                j = text_start_idx
                while j < len(lines):
                    if re.match(timestamp_pattern, lines[j].strip()):
                        break
                    text_lines.append(lines[j])
                    j += 1
                
                # Join text and clean up
                text = '\n'.join(text_lines).strip()
                
                # Store the text with its timestamp
                if text:
                    timestamped_lines[0] = (start_time, text)
                
                if text:  # Only add if there's actual text
                    segments.append(SpeakerSegment(speaker, text, start_time, timestamped_lines))
                
                i = j  # Move to next timestamp
            else:
                i += 1
        else:
            i += 1
    
    # Check if we found any speaker information
    has_speakers = any(seg.speaker for seg in segments)
    
    # If no structured format detected, treat entire text as one segment
    if not segments:
        segments = [SpeakerSegment(None, text.strip(), None)]
        has_speakers = False
    
    return segments, has_speakers


def group_segments_by_speaker(segments: List[SpeakerSegment]) -> List[SpeakerSegment]:
    """
    Group consecutive segments from the same speaker into single segments.
    Also merges unknown speaker parenthetical interjections (like audience reactions)
    into surrounding speaker blocks if the same speaker is on both sides.
    
    Args:
        segments: List of SpeakerSegments
    
    Returns:
        List of grouped SpeakerSegments with combined text
    """
    if not segments:
        return []
    
    def is_parenthetical(text: str) -> bool:
        """Check if text is entirely wrapped in parentheses"""
        text = text.strip()
        return text.startswith('(') and text.endswith(')')
    
    # First pass: mark parenthetical unknown speakers that can be merged
    merge_flags = [False] * len(segments)
    
    for i in range(1, len(segments) - 1):
        seg = segments[i]
        prev_seg = segments[i - 1]
        next_seg = segments[i + 1]
        
        # Check if this is an unknown speaker with parenthetical text
        # between two segments from the same speaker
        if (seg.speaker is None and 
            is_parenthetical(seg.text) and
            prev_seg.speaker == next_seg.speaker and
            prev_seg.speaker is not None):
            
            merge_flags[i] = True
            log_debug(f"Marking segment {i} '{seg.text[:30]}...' for merge into {prev_seg.speaker}")
    
    # Second pass: group segments, merging parentheticals
    grouped = []
    i = 0
    
    while i < len(segments):
        current_speaker = segments[i].speaker
        current_texts = [segments[i].text]
        current_start = segments[i].start_timestamp
        current_timestamped_lines = list(segments[i].timestamped_lines)  # Copy the list
        
        j = i + 1
        
        # Collect consecutive segments from same speaker, including mergeable parentheticals
        while j < len(segments):
            if segments[j].speaker == current_speaker:
                # Same speaker, add to group
                current_texts.append(segments[j].text)
                current_timestamped_lines.extend(segments[j].timestamped_lines)
                j += 1
            elif merge_flags[j]:
                # Parenthetical to merge, add to current group
                current_texts.append(segments[j].text)
                current_timestamped_lines.extend(segments[j].timestamped_lines)
                j += 1
            else:
                # Different speaker and not mergeable
                break
        
        # Create grouped segment
        combined_text = ' '.join(current_texts)
        grouped.append(SpeakerSegment(current_speaker, combined_text, current_start, current_timestamped_lines))
        
        i = j
    
    return grouped


def find_paragraph_timestamps(reformatted_text: str, timestamped_lines: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """
    Match paragraphs in reformatted text back to original timestamped lines.
    
    Args:
        reformatted_text: Text after LLM processing, with paragraph breaks
        timestamped_lines: List of (timestamp, original_text) tuples
    
    Returns:
        List of (timestamp, paragraph_text) tuples
    """
    # Split reformatted text into paragraphs
    paragraphs = [p.strip() for p in reformatted_text.split('\n\n') if p.strip()]
    
    if not paragraphs or not timestamped_lines:
        return [(None, reformatted_text)]
    
    # Build a search index of original text with timestamps
    # Normalize for matching: remove punctuation, lowercase, split into words
    original_words_map = []  # List of (timestamp, word_index, word)
    
    for timestamp, original_text in timestamped_lines:
        words = normalize_text(original_text)
        for idx, word in enumerate(words):
            original_words_map.append((timestamp, word))
    
    result = []
    
    for paragraph in paragraphs:
        # Get first few significant words from paragraph to match
        para_words = normalize_text(paragraph)
        if not para_words:
            result.append((None, paragraph))
            continue
        
        # Try to find where this paragraph starts in original text
        # Look for first 3-5 words as a sequence
        search_words = para_words[:min(5, len(para_words))]
        
        best_timestamp = None
        
        # Search for matching sequence in original
        for i in range(len(original_words_map) - len(search_words) + 1):
            # Check if we have a match
            match = True
            for j, search_word in enumerate(search_words):
                if original_words_map[i + j][1] != search_word:
                    match = False
                    break
            
            if match:
                # Found a match! Use the timestamp from first matched word
                best_timestamp = original_words_map[i][0]
                break
        
        result.append((best_timestamp, paragraph))
    
    return result


def normalize_text(text):
    """
    Normalize text by removing punctuation, converting to lowercase,
    and splitting into words (ignoring all whitespace).
    """
    # Remove all punctuation and convert to lowercase
    text = re.sub(r'[^\w\s]', '', text.lower())
    # Split into words, which automatically handles all whitespace
    words = text.split()
    return words


def compare_word_lists(words1, words2):
    """
    Compare two word lists and return differences, using fuzzy matching.
    Fuzzy matching accepts: exact matches, plural variants, and sound-alikes.
    Returns (is_identical, differences_list, fuzzy_match_count)
    """
    fuzzy_matches = []  # Track acceptable variations
    
    if len(words1) != len(words2):
        # Find where the lists start to differ
        diff_msg = f"Word count mismatch: {len(words1)} vs {len(words2)}"
        
        # Show context around where they diverge
        min_len = min(len(words1), len(words2))
        divergence_point = None
        
        for i in range(min_len):
            if not fuzzy_word_match(words1[i], words2[i]):
                divergence_point = i
                break
            elif words1[i] != words2[i]:
                # Track fuzzy match
                fuzzy_matches.append((i+1, words1[i], words2[i]))
        
        if divergence_point is not None:
            # Show words around the divergence point
            start = max(0, divergence_point - 3)
            end = min(min_len, divergence_point + 4)
            
            diff_msg += f"\n  First difference at position {divergence_point + 1}:"
            diff_msg += f"\n  Original  [...{' '.join(words1[start:end])}...]"
            diff_msg += f"\n  Reformed  [...{' '.join(words2[start:end])}...]"
        elif min_len > 0:
            # Lists are identical up to min_len, show where the extra/missing word is
            if len(words1) > len(words2):
                diff_msg += f"\n  Original has extra word(s) at position {min_len + 1}: '{words1[min_len]}'"
                if min_len > 0:
                    diff_msg += f"\n  Context: ...{' '.join(words1[max(0, min_len-3):min_len+2])}..."
            else:
                diff_msg += f"\n  Reformed has extra word(s) at position {min_len + 1}: '{words2[min_len]}'"
                if min_len > 0:
                    diff_msg += f"\n  Context: ...{' '.join(words2[max(0, min_len-3):min_len+2])}..."
        
        if fuzzy_matches:
            diff_msg += f"\n  Note: {len(fuzzy_matches)} acceptable variation(s) found (plural/sound-alike)"
        
        return False, diff_msg, len(fuzzy_matches)
    
    # Same length - check word by word
    differences = []
    for i, (w1, w2) in enumerate(zip(words1, words2)):
        if w1 != w2:
            if fuzzy_word_match(w1, w2):
                # Acceptable variation
                fuzzy_matches.append((i+1, w1, w2))
            else:
                # Real difference
                differences.append((i+1, w1, w2))
    
    if differences:
        diff_msg = f"{len(differences)} word(s) differ"
        if len(differences) <= 10:
            for pos, w1, w2 in differences:
                diff_msg += f"\n  Position {pos}: '{w1}' vs '{w2}'"
        else:
            diff_msg += f"\n  Showing first 10 differences:"
            for pos, w1, w2 in differences[:10]:
                diff_msg += f"\n  Position {pos}: '{w1}' vs '{w2}'"
        
        if fuzzy_matches:
            diff_msg += f"\n  Note: {len(fuzzy_matches)} acceptable variation(s) (plural/sound-alike)"
            if len(fuzzy_matches) <= 5:
                for pos, w1, w2 in fuzzy_matches:
                    reason = ""
                    if is_plural_variant(w1, w2):
                        reason = " (plural)"
                    elif are_sound_alikes(w1, w2):
                        reason = " (sound-alike)"
                    diff_msg += f"\n    Position {pos}: '{w1}' → '{w2}'{reason}"
        
        return False, diff_msg, len(fuzzy_matches)
    
    # No real differences, but might have fuzzy matches
    if fuzzy_matches:
        diff_msg = f"All words match (with {len(fuzzy_matches)} acceptable variation(s))"
        if len(fuzzy_matches) <= 10:
            for pos, w1, w2 in fuzzy_matches:
                reason = ""
                if is_plural_variant(w1, w2):
                    reason = " (plural)"
                elif are_sound_alikes(w1, w2):
                    reason = " (sound-alike)"
                diff_msg += f"\n  Position {pos}: '{w1}' → '{w2}'{reason}"
        return True, diff_msg, len(fuzzy_matches)
    
    return True, "All words match exactly", 0


def reformat_with_continuation(client, transcript_text, config):
    """Reformat transcript with automatic continuation detection"""
    
    user_prompt = f"""Please reformat the following transcribed text into a logical paragraph flow. DO NOT CHANGE ANY WORD. Only modify punctuation and line breaks:

{transcript_text}"""
    
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt}
    ]
    
    full_response = ""
    continuation_count = 0
    max_continuations = config.get('max_continuations', 10)
    
    log_info("Sending request to OpenAI API...")
    
    while continuation_count <= max_continuations:
        try:
            response = client.chat.completions.create(
                model=config['model'],
                messages=messages,
                max_tokens=config['max_tokens'],
                temperature=config['temperature']
            )
            
            chunk = response.choices[0].message.content
            finish_reason = response.choices[0].finish_reason
            
            log_debug(f"Received chunk of {len(chunk)} characters, finish_reason: {finish_reason}")
            
            # Clean the chunk
            cleaned_chunk = clean_response_chunk(chunk, is_first_chunk=(continuation_count == 0))
            full_response += cleaned_chunk
            
            # Check if we need to continue
            if finish_reason == "stop":
                log_info("Response completed in single request")
                break
            elif finish_reason == "length":
                continuation_count += 1
                log_info(f"Response truncated, requesting continuation {continuation_count}/{max_continuations}")
                
                # Add the assistant's response and request continuation
                messages.append({"role": "assistant", "content": chunk})
                messages.append({"role": "user", "content": CONTINUATION_PROMPT})
            else:
                log_warning(f"Unexpected finish_reason: {finish_reason}")
                break
                
        except Exception as e:
            log_error(f"API request error: {e}")
            if full_response:
                log_warning("Returning partial response due to error")
                break
            else:
                log_fatal(f"Failed to get any response: {e}")
    
    if continuation_count > max_continuations:
        log_warning(f"Reached maximum continuations ({max_continuations})")
    
    return full_response.strip()


def clean_response_chunk(chunk, is_first_chunk=False):
    """Remove extraneous chat output from response chunks"""
    
    # Common patterns to remove (case-insensitive)
    removal_patterns = [
        r'^(here is|here\'s|sure,?|okay,?|certainly,?|of course,?).*?:\s*',
        r'^(let me|i\'ll|i will).*?\.\s*',
        r'(would you like me to continue\??|should i continue\??|continue\??)\s*$',
        r'^\*+\s*',
        r'\s*\*+$',
    ]
    
    cleaned = chunk
    
    if is_first_chunk:
        # More aggressive cleaning for first chunk
        for pattern in removal_patterns:
            cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE | re.MULTILINE)
    
    # Remove trailing prompts/questions about continuation
    continuation_patterns = [
        r'\n\n(Would you like|Should I|Shall I).*continue.*$',
        r'\n\nLet me know.*continue.*$',
    ]
    
    for pattern in continuation_patterns:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE | re.DOTALL)
    
    return cleaned


##############################################################################
#
# Output and Logging Message Functions
#
##############################################################################
def write_out(message):
    info = {
        'levelname': 'OUTPUT',
        'asctime': time.strftime(DEFAULT_TIME_FORMAT, time.localtime()),
        'message': message
    }
    print(LOGGING_FORMAT % info)


def write_status(message):
    """Write status message to stderr (for progress updates independent of logging)"""
    info = {
        'levelname': 'STATUS',
        'asctime': time.strftime(DEFAULT_TIME_FORMAT, time.localtime()),
        'message': message
    }
    print(LOGGING_FORMAT % info, file=sys.stderr, flush=True)


def log_fatal(msg, exit_code=1):
    logging.critical("Fatal Err: %s" % msg)
    sys.exit(exit_code)


def log_warning(msg):
    logging.warning(msg)


def log_error(msg):
    logging.error(msg)


def log_info(msg):
    logging.info(msg)


def log_debug(msg):
    logging.debug(msg)


#
# Initial Setup and call to main()
#
if __name__ == '__main__':
    sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 1)  # reopen STDOUT unbuffered
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(1)
