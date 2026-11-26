#!/usr/bin/env python3
"""
Identify unlabeled speakers in a podcast transcript with OpenAI (web search enabled).

Unlabeled speakers are segments with no speaker or with one of Resolve's
auto-enrolled numeric labels ("Speaker 1517"). This module has no Resolve
dependency: it works on GetTranscription()['segments'] dicts or on Resolve's
txt export, so it runs on its own:

    speaker_id.py transcript.txt

podcast_transcribe.py imports it to name speakers before writing them back to Resolve.
"""

import sys
import os
import re
import json
import argparse
import configparser
import logging
import time
from collections import OrderedDict

DEFAULT_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
LOGGING_FORMAT = '%(asctime)s:%(levelname)s:%(message)s'
DEFAULT_CONFIG_FILE = "transcript_reformatter.conf"

# Model used for identification; must support the web_search tool in the Responses API
DEFAULT_MODEL = "gpt-5.5"

NO_SPEAKER_LABEL = "(no speaker)"   # how the None label is shown to the model
MAX_EXCERPTS_PER_LABEL = 8
MAX_EXCERPT_CHARS = 400
OPENING_CHARS = 3000                # amount of the episode opening sent (hosts usually introduce guests here)

UNLABELED_RE = re.compile(r'^Speaker \d+$')
TIMESTAMP_RE = re.compile(r'^\[(\d{2}:\d{2}:\d{2}[:;]\d{2})\s*-\s*(\d{2}:\d{2}:\d{2}[:;]\d{2})\]$')

SYSTEM_PROMPT = """You identify speakers in podcast transcripts.
Some speakers are unlabeled: they have generic labels like "Speaker 1517" or no label at all.
Use the show name, the episode title, the named speakers, what people say (introductions,
names used in address, self-references, topics) and web searches (show notes, episode pages,
guest lists) to work out who each unlabeled speaker is.

Rules:
- Only name a person when the evidence supports it. Use null when you can't tell.
- An unlabeled speaker may be one of the already-named speakers (the diarization split one
  voice in two); in that case give that existing name exactly as written.
- Two different unlabeled labels may be the same person.
- confidence is your probability (0 to 1) that the name is correct.
- evidence is one or two short sentences: the transcript clue and/or source you relied on.
- Use the person's full name as commonly written (e.g. "Jonathan Karl").

Respond with ONLY a JSON object, no prose and no code fences, of this form:
{"<label>": {"name": "<full name or null>", "confidence": <0-1>, "evidence": "<short>"}, ...}
with exactly one key per unlabeled label you were asked about."""


def main():
    parser = argparse.ArgumentParser(
        description='Suggest names for unlabeled speakers in a Resolve-format transcript using OpenAI with web search'
    )
    parser.add_argument('transcript', help="Resolve-format transcript (.txt)")
    parser.add_argument('-c', '--config', default=None, help="Configuration file path")
    parser.add_argument('--model', default=None, help="OpenAI model (default: [pipeline] speaker_id_model or %s)" % DEFAULT_MODEL)
    parser.add_argument('--show', default=None, help="Show name (default: parent folder of the transcript)")
    parser.add_argument('--title', default=None, help="Episode title (default: from .episode.json or the file name)")
    parser.add_argument('-v', action='store_true', default=False, help="Print extra info")
    parser.add_argument('-vv', action='store_true', default=False, help="Print (more) extra info")
    args = parser.parse_args()

    if args.vv:
        level = logging.DEBUG
    elif args.v:
        level = logging.INFO
    else:
        level = logging.WARNING
    logging.basicConfig(format=LOGGING_FORMAT, datefmt=DEFAULT_TIME_FORMAT, level=level)

    if not os.path.isfile(args.transcript):
        log_fatal(f"File not found: {args.transcript}")

    segments = parse_resolve_txt(args.transcript)
    if not segments:
        log_fatal(f"No Resolve-format segments found in {args.transcript}")

    unlabeled = find_unlabeled(segments)
    if not unlabeled:
        write_out("No unlabeled speakers")
        return 0

    api_key, config_model = load_config(args.config)
    model = args.model or config_model or DEFAULT_MODEL
    show_name, episode_title = episode_info(args.transcript)
    show_name = args.show or show_name
    episode_title = args.title or episode_title

    write_out(f"Show: {show_name} | Episode: {episode_title} | Model: {model}")
    write_out("Known speakers: " + (', '.join(known_speakers(segments)) or '(none)'))

    suggestions = identify_speakers(segments, show_name, episode_title, api_key, model)

    for label, indices in unlabeled.items():
        s = suggestions.get(label) or {}
        print(f"\n{label or NO_SPEAKER_LABEL}  ({len(indices)} segments)")
        print(f"  Suggested: {s.get('name') or '(none)'}  [confidence {s.get('confidence', 0.0):.2f}]")
        if s.get('evidence'):
            print(f"  Evidence:  {s['evidence']}")
        for i in indices[:2]:
            print(f"  Sample:    \"{truncate(segments[i]['text'], 150)}\"")

    print()
    print(json.dumps({(k or NO_SPEAKER_LABEL): v for k, v in suggestions.items()}, indent=2, ensure_ascii=False))
    return 0


##############################################################################
# Transcript helpers
##############################################################################

def is_unlabeled(speaker):
    """True for no speaker, or a Resolve auto-label like 'Speaker 1517'"""
    speaker = (speaker or '').strip()
    return not speaker or bool(UNLABELED_RE.match(speaker))


def normalize_label(speaker):
    """Stripped speaker label, or None for no speaker"""
    return (speaker or '').strip() or None


def is_parenthetical(text):
    """True when the text is only a sound cue like '(Music)' or '(Audience Laughing)'"""
    text = (text or '').strip()
    return bool(re.fullmatch(r'(\([^()]*\)\s*)+', text))


def parse_resolve_txt(path):
    """
    Parse Resolve's transcript txt export:

        [00:00:07:14 - 00:00:48:02]
        Speaker Name
         Segment text

    (the speaker line is absent when there is no speaker). Returns a list of
    {'start', 'end', 'speaker', 'text'} dicts, the same shape as
    GetTranscription()['segments'].
    """
    with open(path, 'r', encoding='utf-8') as f:
        lines = f.read().split('\n')

    segments = []
    i = 0
    while i < len(lines):
        match = TIMESTAMP_RE.match(lines[i].strip())
        if not match:
            i += 1
            continue

        start, end = match.group(1), match.group(2)
        j = i + 1
        speaker = None
        if j < len(lines) and lines[j] and lines[j][0] not in (' ', '\t') and not TIMESTAMP_RE.match(lines[j].strip()):
            speaker = lines[j].strip() or None
            j += 1

        text_lines = []
        while j < len(lines) and not TIMESTAMP_RE.match(lines[j].strip()):
            text_lines.append(lines[j].strip())
            j += 1
        text = ' '.join(t for t in text_lines if t).strip()
        if text:
            segments.append({'start': start, 'end': end, 'speaker': speaker, 'text': text})
        i = j

    return segments


def find_unlabeled(segments):
    """
    Map each unlabeled label to the indices of its segments, most-heard first.
    Segments that are only sound cues ('(Music)') are ignored, so a label made
    up entirely of them is not reported. Speakerless segments use the key None.
    """
    found = {}
    for idx, seg in enumerate(segments):
        if not is_unlabeled(seg.get('speaker')) or is_parenthetical(seg.get('text')):
            continue
        found.setdefault(normalize_label(seg.get('speaker')), []).append(idx)
    return OrderedDict(sorted(found.items(), key=lambda kv: -len(kv[1])))


def known_speakers(segments):
    """Named (non-generic) speakers, in order of first appearance"""
    names = []
    for seg in segments:
        speaker = normalize_label(seg.get('speaker'))
        if speaker and not is_unlabeled(speaker) and speaker not in names:
            names.append(speaker)
    return names


def episode_info(path):
    """
    (show_name, episode_title) for an audio or transcript path: the show is the
    parent folder; the title comes from a sibling <basename>.episode.json if
    present, otherwise the file name minus a 'YYYY-MM-DD_NNNNNN_' prefix.
    """
    path = os.path.abspath(path)
    show_name = os.path.basename(os.path.dirname(path))
    base = os.path.splitext(path)[0]
    episode_json = base + '.episode.json'
    if os.path.isfile(episode_json):
        try:
            with open(episode_json, 'r', encoding='utf-8') as f:
                title = (json.load(f).get('episode_title') or '').strip()
            if title:
                return show_name, title
        except (OSError, ValueError) as e:
            log_debug(f"Could not read {episode_json}: {e}")
    title = re.sub(r'^\d{4}-\d{2}-\d{2}_\d+_', '', os.path.basename(base))
    return show_name, title


def truncate(text, limit):
    text = (text or '').strip()
    return text if len(text) <= limit else text[:limit].rstrip() + '...'


##############################################################################
# OpenAI
##############################################################################

def pick_excerpt_indices(indices, limit=MAX_EXCERPTS_PER_LABEL):
    """Up to `limit` indices: the first few, then spread evenly across the episode"""
    if len(indices) <= limit:
        return list(indices)
    head = indices[:3]
    rest = indices[3:]
    step = len(rest) / (limit - len(head))
    return head + [rest[int(k * step)] for k in range(limit - len(head))]


def format_line(seg):
    label = normalize_label(seg.get('speaker')) or NO_SPEAKER_LABEL
    return f"[{seg.get('start') or '?'}] {label}: {truncate(seg.get('text'), MAX_EXCERPT_CHARS)}"


def build_prompt(segments, show_name, episode_title, unlabeled):
    parts = [
        f"Show: {show_name}",
        f"Episode title: {episode_title}",
        "Named speakers: " + (', '.join(known_speakers(segments)) or '(none)'),
        "Unlabeled labels to identify: " + ', '.join(
            f'"{label or NO_SPEAKER_LABEL}" ({len(idx)} segments)' for label, idx in unlabeled.items()),
        "",
        "=== Episode opening ===",
    ]
    used = 0
    for seg in segments:
        line = format_line(seg)
        if used + len(line) > OPENING_CHARS:
            break
        parts.append(line)
        used += len(line)

    for label, indices in unlabeled.items():
        parts.append("")
        parts.append(f'=== Excerpts for "{label or NO_SPEAKER_LABEL}" (with the line before and after) ===')
        for idx in pick_excerpt_indices(indices):
            for k in (idx - 1, idx, idx + 1):
                if 0 <= k < len(segments):
                    parts.append(format_line(segments[k]))
            parts.append("---")
    return '\n'.join(parts)


def parse_response(text, unlabeled):
    """
    Parse the model's JSON into {label: {'name', 'confidence', 'evidence'}} for
    the requested labels only. Raises ValueError if no JSON object is found.
    """
    text = (text or '').strip()
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text)
    try:
        data = json.loads(text)
    except ValueError:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if not match:
            raise ValueError(f"No JSON object in model response: {truncate(text, 200)!r}")
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("Model response JSON is not an object")

    by_display = {(label or NO_SPEAKER_LABEL): label for label in unlabeled}
    result = {}
    for key, value in data.items():
        if key not in by_display or not isinstance(value, dict):
            log_debug(f"Ignoring unexpected key in model response: {key!r}")
            continue
        name = value.get('name')
        name = name.strip() if isinstance(name, str) and name.strip().lower() not in ('', 'null', 'none', 'unknown') else None
        try:
            confidence = min(1.0, max(0.0, float(value.get('confidence') or 0.0)))
        except (TypeError, ValueError):
            confidence = 0.0
        result[by_display[key]] = {
            'name': name,
            'confidence': confidence if name else 0.0,
            'evidence': str(value.get('evidence') or '').strip(),
        }
    return result


def identify_speakers(segments, show_name, episode_title, api_key, model=DEFAULT_MODEL):
    """
    Ask OpenAI (Responses API with web search) to name the unlabeled speakers.
    Returns {label: {'name': str|None, 'confidence': float, 'evidence': str}};
    label None means segments with no speaker.
    """
    from openai import OpenAI

    unlabeled = find_unlabeled(segments)
    if not unlabeled:
        return {}

    prompt = build_prompt(segments, show_name, episode_title, unlabeled)
    log_debug(f"Speaker ID prompt ({len(prompt)} chars):\n{prompt}")
    log_info(f"Asking {model} to identify {len(unlabeled)} unlabeled speaker(s)...")

    client = OpenAI(api_key=api_key)
    started = time.time()
    response = client.responses.create(
        model=model,
        instructions=SYSTEM_PROMPT,
        input=prompt,
        tools=[{"type": "web_search"}],
    )
    text = response.output_text
    log_info(f"Response received in {time.time() - started:.0f}s")
    log_debug(f"Speaker ID response:\n{text}")
    return parse_response(text, unlabeled)


def load_config(config_path=None):
    """Return (api_key, speaker_id_model or None) from transcript_reformatter.conf"""
    if config_path:
        if not os.path.exists(config_path):
            log_fatal(f"Specified config file not found: {config_path}")
        config_file = config_path
    else:
        config_file = None
        for directory in (os.path.dirname(os.path.abspath(__file__)), os.getcwd()):
            candidate = os.path.join(directory, DEFAULT_CONFIG_FILE)
            if os.path.exists(candidate):
                config_file = candidate
                break
        if not config_file:
            log_fatal(f"Configuration file not found. Expected: {DEFAULT_CONFIG_FILE}")

    parser = configparser.ConfigParser()
    parser.read(config_file)
    api_key = parser.get('openai', 'api_key', fallback=None)
    if not api_key:
        log_fatal(f"No [openai] api_key in {config_file}")
    return api_key, parser.get('pipeline', 'speaker_id_model', fallback=None)


##############################################################################
# Output and Logging Message Functions
##############################################################################
def write_out(message):
    info = {
        'levelname': 'OUTPUT',
        'asctime': time.strftime(DEFAULT_TIME_FORMAT, time.localtime()),
        'message': message
    }
    print(LOGGING_FORMAT % info)


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


if __name__ == '__main__':
    sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 1)  # reopen STDOUT unbuffered
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(1)
