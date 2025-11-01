# Transcript Reformatter

A Python tool that uses OpenAI's API to reformat machine-transcribed audio/video text into clean, well-formatted paragraphs without changing any words.

## Features

- **Automatic Continuation**: Detects when responses are truncated and automatically requests continuation
- **Smart Output Cleaning**: Removes extraneous chat-like responses, keeping only the reformatted text
- **Speaker-Aware Processing**: Automatically detects and handles transcripts with speaker identification (DaVinci Resolve format)
- **Sanity Check**: Automatically verifies that no words were changed, only punctuation and formatting
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
   - `transcript_reformatter.conf`

4. **Make the script executable** (Linux/Mac):
   ```bash
   chmod +x transcript_reformatter.py
   ```

## Configuration

1. **Edit the configuration file** (`transcript_reformatter.conf`):
   ```ini
   [openai]
   api_key = sk-your-actual-api-key-here
   model = gpt-4.1
   max_tokens = 16000
   temperature = 0.1
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

### Skip Sanity Check

If you trust the model or need to bypass the word comparison check:

```bash
./transcript_reformatter.py transcript.txt --skip-sanity-check
```

**Warning**: Only use this if you understand the risks. The sanity check protects you from inadvertent word changes.

## How It Works

1. **Reads your transcript file** containing raw machine-transcribed text
   - Automatically detects if transcript has speaker identification (DaVinci Resolve format)
   - Groups consecutive statements by the same speaker

2. **Sends to OpenAI API** with instructions to:
   - Reformat into logical paragraphs
   - Fix punctuation and line breaks
   - NOT change any words
   - Process each speaker's grouped text independently (if speakers detected)

3. **Monitors the response**:
   - If complete: saves the reformatted text
   - If truncated: automatically requests continuation
   - Repeats until complete or max_continuations reached

4. **Cleans the output**:
   - Removes phrases like "Here is the reformatted text:"
   - Removes questions like "Would you like me to continue?"
   - Keeps only the actual reformatted transcript

5. **Performs sanity check**:
   - Compares original and reformatted text word-by-word (per speaker if applicable)
   - Ignores punctuation, capitalization, and whitespace
   - If any words changed: refuses to save and reports differences
   - If all words match: proceeds to save

6. **Saves the result** to a new file
   - With speaker attribution if detected
   - As plain paragraphs if no speakers

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

### Example 2: Long Transcript with Auto-Continuation

**Command with verbose output**:
```bash
./transcript_reformatter.py long_lecture.txt -vv
```

**Output** (logging):
```
2025-10-28 14:32:10:INFO:Processing file: long_lecture.txt
2025-10-28 14:32:10:INFO:Transcript length: 45000 characters
2025-10-28 14:32:10:INFO:Sending request to OpenAI API...
2025-10-28 14:32:15:DEBUG:Received chunk of 16000 characters, finish_reason: length
2025-10-28 14:32:15:INFO:Response truncated, requesting continuation 1/10
2025-10-28 14:32:20:DEBUG:Received chunk of 15800 characters, finish_reason: length
2025-10-28 14:32:20:INFO:Response truncated, requesting continuation 2/10
2025-10-28 14:32:25:DEBUG:Received chunk of 13200 characters, finish_reason: stop
2025-10-28 14:32:25:INFO:Response completed in single request
2025-10-28 14:32:25:INFO:Performing sanity check...
2025-10-28 14:32:25:INFO:✓ Sanity check passed: All words preserved
2025-10-28 14:32:25:OUTPUT:✓ Sanity check passed: All words preserved
2025-10-28 14:32:25:OUTPUT:Reformatted transcript saved to: long_lecture_reformatted.txt
```

### Example 3: Sanity Check Catches Word Changes

**Scenario**: Model inadvertently changes a word

**Output**:
```
2025-10-28 14:35:12:INFO:Performing sanity check...
2025-10-28 14:35:12:ERROR:✗ Sanity check FAILED: Words were changed!
2025-10-28 14:35:12:ERROR:3 word(s) differ
  Position 145: 'quarterly' vs 'annual'
  Position 287: 'increase' vs 'decrease'
  Position 431: 'client' vs 'customer'
2025-10-28 14:35:12:OUTPUT:ERROR: Sanity check failed - 3 word(s) differ
  Position 145: 'quarterly' vs 'annual'
  Position 287: 'increase' vs 'decrease'
  Position 431: 'client' vs 'customer'
2025-10-28 14:35:12:OUTPUT:The reformatted text contains word changes and will NOT be saved.
```

In this case, the file is **not saved**, protecting your transcript from inadvertent changes.

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

### Sanity check fails (words were changed)
- The model changed words instead of just formatting
- Review the comparison output to see what changed
- Try with a different model (gpt-4o is most reliable)
- Increase the emphasis in the system prompt
- If the changes are acceptable, use `--skip-sanity-check` (not recommended)

### Model returns the same text unformatted
- Try a different model (gpt-4o is recommended)
- Verify the input file is actually unformatted
- Check that temperature isn't too low (0.3 is good)

## API Costs

Approximate costs (as of October 2024):
- **GPT-4o**: $2.50 / 1M input tokens, $10.00 / 1M output tokens
- **GPT-4o-mini**: $0.15 / 1M input tokens, $0.60 / 1M output tokens

A typical 10-minute interview transcript (~2000 words = ~2700 tokens) costs:
- GPT-4o: ~$0.03-0.05 per transcript
- GPT-4o-mini: ~$0.002-0.003 per transcript

## License

This script is provided as-is for personal and commercial use.

## Support

For issues or questions:
1. Check the Troubleshooting section
2. Run with `-vv` to see detailed logs
3. Verify your OpenAI API key and credits

## Tips

- **Start with smaller files** to test your setup
- **Use `-v` or `-vv`** to understand what's happening
- **Keep backups** of original transcripts
- **Adjust max_tokens** based on your typical transcript length
- **Monitor API costs** in your OpenAI dashboard
