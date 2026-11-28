#!/usr/bin/env python3
"""
Podcast transcription pipeline: audio file -> final reformatted transcript.

  1. Transcribe in DaVinci Resolve (or reuse the clip's existing transcription)
  2. Ask OpenAI (with web search) to name unlabeled speakers ("Speaker 1517")
  3. Confirm each name, then write the names back into Resolve
  4. Export the Resolve-format transcript
  5. Reformat it with transcript_reformatter to the final location

Scratch files go in a temporary directory that is removed at the end. Two
working files are kept next to the audio file (unless --no-sidecars):

  <episode>.resolve.txt    Resolve-format transcript with timecodes and speakers
  <episode>.speakers.json  speaker identification notes: suggestions, evidence,
                           what was accepted, and whether the Resolve write worked

A rerun reuses the decisions in <episode>.speakers.json instead of calling
OpenAI again (--redo-speakers to ignore them).

Each piece still runs on its own: resolve_transcribe.py, speaker_id.py and
transcript_reformatter.py. This script only wires them together.
"""

import sys
import os
import argparse
import configparser
import copy
import json
import logging
import shutil
import tempfile
import time

import transcript_reformatter as rf
import speaker_id
from transcript_reformatter import write_out, write_status, log_fatal, log_warning, log_error, log_info, log_debug

DEFAULT_TIME_FORMAT = rf.DEFAULT_TIME_FORMAT
LOGGING_FORMAT = rf.LOGGING_FORMAT

DEFAULT_RESOLVE_TRANSCRIBE_DIR = "~/dev/resolve_transcribe"
DEFAULT_MIN_CONFIDENCE = 0.8

SIDECAR_TRANSCRIPT = 'resolve.txt'
SIDECAR_SPEAKERS = 'speakers.json'
SPEAKERS_VERSION = 1


def main():
    parser = argparse.ArgumentParser(
        description='Transcribe a podcast in DaVinci Resolve, identify unlabeled speakers with AI, '
                    'write the names back to Resolve, and reformat the transcript',
        epilog='Example: podcast_transcribe.py "/Volumes/media/Podcasts/Show/episode.mp3"'
    )
    parser.add_argument('audio_file', help="Podcast audio file")
    parser.add_argument('-o', '--output', default=None,
                        help="Final transcript path (default: next to the audio file, with a .txt extension)")
    parser.add_argument('-c', '--config', default=None, help="Configuration file path")
    parser.add_argument('-f', '--force', action='store_true', help="Overwrite the final transcript if it exists")
    parser.add_argument('--work-dir', default=None,
                        help="Use this directory for scratch files and keep it "
                             "(default: a temporary directory, removed at the end)")
    parser.add_argument('--keep-work', action='store_true',
                        help="Keep the temporary scratch directory and print its path")
    parser.add_argument('--no-sidecars', action='store_true',
                        help=f"Don't save <episode>.{SIDECAR_TRANSCRIPT} and <episode>.{SIDECAR_SPEAKERS} "
                             "next to the audio file")

    sid = parser.add_argument_group('speaker identification')
    sid.add_argument('-y', '--yes', action='store_true',
                     help="Don't prompt: accept suggestions at or above --min-confidence, skip the rest")
    sid.add_argument('--min-confidence', type=float, default=None,
                     help=f"Confidence needed to auto-accept with --yes (default: config or {DEFAULT_MIN_CONFIDENCE})")
    sid.add_argument('--skip-speaker-id', action='store_true', help="Don't run AI speaker identification")
    sid.add_argument('--redo-speakers', action='store_true',
                     help=f"Ignore decisions saved in <episode>.{SIDECAR_SPEAKERS} and identify speakers again")
    sid.add_argument('--no-apply', action='store_true',
                     help="Use accepted names in the exported transcript only; don't write them to Resolve")

    res = parser.add_argument_group('Resolve (passed to resolve_transcribe)')
    res.add_argument('--database', default=None, help="Resolve database name")
    res.add_argument('--project-name', default=None, help="Resolve project name")

    fmt = parser.add_argument_group('reformatting (passed to transcript_reformatter)')
    fmt.add_argument('--timestamps', action='store_true', help="Include timestamps in output")
    fmt.add_argument('--disable-timestamp-adjustment', action='store_true',
                     help="Disable automatic 1-hour subtraction for Resolve timestamps")
    fmt.add_argument('--skip-sanity-check', action='store_true', help="Skip word comparison sanity check")
    fmt.add_argument('--save-failed', action='store_true', help="Save output even if sanity check fails")

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

    ######################################
    # Pre-flight: everything that can fail cheaply, before Resolve or the API
    ######################################
    audio_file = os.path.abspath(os.path.expanduser(args.audio_file))
    if not os.path.isfile(audio_file):
        log_fatal(f"Audio file not found: {audio_file}")

    output_file = os.path.abspath(os.path.expanduser(args.output)) if args.output \
        else os.path.splitext(audio_file)[0] + '.txt'
    if os.path.exists(output_file) and not args.force:
        log_fatal(f"Output file already exists: {output_file} (use -f/--force to overwrite)")

    config_file = find_config_file(args.config)
    openai_config = rf.load_config(config_file)
    if not openai_config.get('api_key'):
        log_fatal("API key not found in configuration file")
    pipeline = load_pipeline_config(config_file)

    sidecars = pipeline['sidecars'] and not args.no_sidecars
    if sidecars and not os.access(os.path.dirname(audio_file), os.W_OK):
        log_fatal(f"Can't write sidecar files next to the audio in {os.path.dirname(audio_file)} "
                  "(use --no-sidecars to skip them)")

    min_confidence = args.min_confidence if args.min_confidence is not None else pipeline['min_confidence']
    rt = import_resolve_transcribe(pipeline['resolve_transcribe_dir'])

    if args.work_dir:
        work_dir = os.path.abspath(os.path.expanduser(args.work_dir))
        os.makedirs(work_dir, exist_ok=True)
        keep = {'work_dir': True}
    else:
        work_dir = tempfile.mkdtemp(prefix='podcast_transcribe-')
        keep = {'work_dir': args.keep_work}
    log_debug(f"Work directory: {work_dir}")

    try:
        return run(args, audio_file, output_file, openai_config, pipeline, rt,
                   work_dir, keep, sidecars, min_confidence)
    finally:
        if keep['work_dir']:
            write_out(f"Work files kept in: {work_dir}")
        else:
            shutil.rmtree(work_dir, ignore_errors=True)


def run(args, audio_file, output_file, openai_config, pipeline, rt, work_dir, keep, sidecars, min_confidence):
    base_name = os.path.splitext(os.path.basename(audio_file))[0]
    speakers_file = speaker_id.sidecar_path(audio_file, SIDECAR_SPEAKERS)
    transcript_sidecar = speaker_id.sidecar_path(audio_file, SIDECAR_TRANSCRIPT)

    ######################################
    # 1. Transcribe (or reuse) in Resolve
    ######################################
    write_status(f"Connecting to DaVinci Resolve...")
    resolve = rt.connect_to_resolve()
    if not resolve:
        log_fatal("Failed to connect to DaVinci Resolve. Is it running?")

    project, media_pool = rt.setup_transcription_project(
        resolve,
        args.project_name or rt.TRANSCRIPTION_PROJECT_NAME,
        args.database or rt.DEFAULT_DATABASE_NAME
    )
    if not project or not media_pool:
        log_fatal("Failed to set up the Resolve transcription project")

    write_status(f"Transcribing (or reusing existing transcription): {os.path.basename(audio_file)}")
    item, transcription, error = rt.ensure_transcribed(resolve, project, media_pool, audio_file)
    if error:
        log_fatal(error)
    if not isinstance(transcription, dict) or not transcription.get('segments'):
        log_fatal("No transcription available (this pipeline needs Resolve Studio 21.1+ for GetTranscription)")

    ######################################
    # 2-3. Identify, confirm and apply speaker names
    ######################################
    mapping = {}
    still_unlabeled = []
    notes = None
    resolve_write_failed = False
    if not args.skip_speaker_id:
        previous = None
        if sidecars and not args.redo_speakers:
            previous = load_speaker_notes(speakers_file)
        mapping, still_unlabeled, notes = identify_and_confirm(
            transcription, audio_file, openai_config, pipeline, rt,
            auto_yes=args.yes, min_confidence=min_confidence, previous=previous
        )
        # Save the decisions before touching Resolve, so they survive a failure there
        if sidecars and notes:
            notes['resolve_write'] = 'pending' if mapping and not args.no_apply else 'not attempted'
            save_speaker_notes(speakers_file, notes)

    if mapping:
        if args.no_apply:
            log_info("--no-apply: renaming speakers in the exported transcript only")
            transcription = apply_mapping(transcription, mapping)
            resolve_write = 'skipped (--no-apply)'
        else:
            write_status("Writing speaker names to Resolve...")
            backup = os.path.join(work_dir, f"{base_name}.transcription-backup.json")
            changed, error = rt.relabel_speakers(item, mapping, backup_file=backup)
            if error:
                # Keep the confirmed names: apply them to the export instead
                resolve_write_failed = True
                keep['work_dir'] = True  # holds the backup of the original transcription
                resolve_write = f"failed: {error}"
                log_warning(f"Failed to write speaker names to Resolve: {error}")
                log_warning("Applying the names to the exported transcript only; rename them in the "
                            "Resolve UI if you want Resolve to learn these voices")
                transcription = apply_mapping(transcription, mapping)
            else:
                resolve_write = 'ok'
                write_out(f"Renamed {changed} segments in Resolve")
                transcription = rt.get_transcription(item)
        if sidecars and notes:
            notes['resolve_write'] = resolve_write
            save_speaker_notes(speakers_file, notes)

    ######################################
    # 4. Export the Resolve-format transcript
    ######################################
    intermediate = rt.export_transcription(project, item, transcription, audio_file, work_dir, base_name, 'txt')
    if not intermediate:
        log_fatal("Failed to export the Resolve transcript")
    if sidecars:
        # Saved before reformatting so a reformat failure doesn't lose it
        with open(intermediate, 'r', encoding='utf-8') as f:
            write_atomic(transcript_sidecar, f.read())
        write_out(f"Resolve transcript: {transcript_sidecar}")
    else:
        write_out(f"Resolve transcript: {intermediate}")

    ######################################
    # 5. Reformat to the final location
    ######################################
    ok = rf.process_transcript(
        intermediate, output_file, openai_config,
        skip_sanity_check=args.skip_sanity_check,
        save_failed=args.save_failed,
        include_timestamps=args.timestamps,
        disable_timestamp_adjustment=args.disable_timestamp_adjustment,
        force=True  # existence was checked during pre-flight
    )
    if not ok:
        log_fatal("Reformatting failed")

    ######################################
    # Summary
    ######################################
    write_out("")
    write_out(f"Done: {output_file}")
    if sidecars and notes:
        write_out(f"Speaker notes: {speakers_file}")
    if resolve_write_failed:
        write_out("  Names applied to the transcript only (Resolve write failed)")
    for old, new in mapping.items():
        write_out(f"  {old or '(no speaker)'} -> {new}")
    if still_unlabeled:
        write_out("  Still unlabeled: " + ', '.join(label or '(no speaker)' for label in still_unlabeled))
    return 0


def identify_and_confirm(transcription, audio_file, openai_config, pipeline, rt, auto_yes, min_confidence,
                         previous=None):
    """
    Find unlabeled speakers, reuse decisions from a previous run where there are
    any, ask OpenAI about the rest, and confirm each name.
    Returns: (mapping {label: name}, [labels left unlabeled], notes dict or None)
    notes is None when there was nothing to decide.
    """
    # Drop Resolve's '(...)' silence markers so the model sees clean text
    segments = []
    for seg in transcription.get('segments') or []:
        text = rt.clean_text(seg.get('text'))
        if text:
            segments.append({'start': seg.get('start'), 'end': seg.get('end'),
                             'speaker': seg.get('speaker'), 'text': text})

    unlabeled = speaker_id.find_unlabeled(segments)
    if not unlabeled:
        write_out("All speakers are labeled")
        return {}, [], None

    show_name, episode_title = speaker_id.episode_info(audio_file)
    now = time.strftime('%Y-%m-%dT%H:%M:%S%z')
    notes = {
        'version': SPEAKERS_VERSION,
        'created': now,
        'updated': now,
        'audio_file': os.path.basename(audio_file),
        'show': show_name,
        'episode_title': episode_title,
        'model': None,
        'min_confidence': min_confidence,
        'speakers': {},
        'resolve_write': None,
    }

    mapping = {}
    still_unlabeled = []

    # Reuse decisions from an earlier run (same clip, so the same Resolve labels)
    prior = (previous or {}).get('speakers') or {}
    reused = [label for label in unlabeled if speaker_id.label_key(label) in prior]
    if reused:
        notes['created'] = previous.get('created') or now
        notes['model'] = previous.get('model')
        write_out(f"Reusing earlier speaker decisions for: " + ', '.join(
            speaker_id.label_key(label) for label in reused))
        for label in reused:
            entry = dict(prior[speaker_id.label_key(label)])
            entry['segments'] = len(unlabeled[label])
            notes['speakers'][speaker_id.label_key(label)] = entry
            if entry.get('name') and entry['name'] != label:
                mapping[label] = entry['name']
            else:
                still_unlabeled.append(label)

    todo = {label: idx for label, idx in unlabeled.items() if label not in reused}
    if not todo:
        return mapping, still_unlabeled, notes

    write_out(f"Unlabeled speakers: " + ', '.join(
        f"{speaker_id.label_key(label)} ({len(idx)} segments)" for label, idx in todo.items()))

    interactive = not auto_yes and sys.stdin.isatty()
    if not auto_yes and not interactive:
        log_warning("stdin is not a terminal and --yes was not given; leaving speakers unlabeled")
        return mapping, still_unlabeled + list(todo), (notes if notes['speakers'] else None)

    # Reused names count as named speakers, so the model only sees what's left
    if mapping:
        for seg in segments:
            label = speaker_id.normalize_label(seg.get('speaker'))
            if label in mapping:
                seg['speaker'] = mapping[label]

    model = pipeline['speaker_id_model'] or speaker_id.DEFAULT_MODEL
    notes['model'] = model
    write_status(f"Asking {model} to identify {len(todo)} speaker(s)...")
    try:
        suggestions = speaker_id.identify_speakers(
            segments, show_name, episode_title, openai_config['api_key'], model)
    except Exception as e:
        log_error(f"Speaker identification failed: {e}")
        suggestions = {}

    # Most-heard speakers first
    for label, indices in sorted(todo.items(), key=lambda kv: -len(kv[1])):
        suggestion = suggestions.get(label) or {}
        suggested = suggestion.get('name')
        confidence = float(suggestion.get('confidence') or 0.0)
        evidence = suggestion.get('evidence')

        if interactive:
            name, decision = prompt_for_name(label, indices, segments, suggested, confidence,
                                             evidence, min_confidence)
        elif suggested and confidence >= min_confidence:
            name, decision = suggested, 'auto-accepted'
        else:
            log_info(f"{speaker_id.label_key(label)}: not auto-accepted "
                     f"({suggested or 'no suggestion'}, confidence {confidence:.2f})")
            name, decision = None, 'auto-skipped'

        notes['speakers'][speaker_id.label_key(label)] = {
            'segments': len(indices),
            'suggestion': suggested,
            'confidence': confidence,
            'evidence': evidence or None,
            'name': name if name and name != label else None,
            'decision': decision,
        }
        if name and name != label:
            mapping[label] = name
        else:
            still_unlabeled.append(label)

    return mapping, still_unlabeled, notes


def prompt_for_name(label, indices, segments, name, confidence, evidence, min_confidence):
    """
    Show a suggestion and ask the user to accept, edit or skip it.
    Returns (name or None, decision) where decision is 'accepted', 'edited' or 'skipped'.
    """
    print("", file=sys.stderr)
    print(f"{label or '(no speaker)'}  ({len(indices)} segments)", file=sys.stderr)
    if name:
        print(f"  Suggested: {name}  [confidence {confidence:.2f}]", file=sys.stderr)
    else:
        print("  Suggested: (none)", file=sys.stderr)
    if evidence:
        print(f"  Evidence:  {evidence}", file=sys.stderr)
    for i in indices[:2]:
        text = segments[i]['text']
        print(f"  Sample:    \"{text[:150]}{'...' if len(text) > 150 else ''}\"", file=sys.stderr)

    default = 'a' if name and confidence >= min_confidence else 's'
    choices = '[a]ccept / [e]dit / [s]kip' if name else '[e]dit / [s]kip'
    while True:
        try:
            answer = input(f"  {choices} (default {default}): ").strip().lower() or default
        except EOFError:
            return None, 'skipped'
        if answer.startswith('a') and name:
            return name, 'accepted'
        if answer.startswith('s'):
            return None, 'skipped'
        if answer.startswith('e'):
            try:
                edited = input("  Name: ").strip()
            except EOFError:
                return None, 'skipped'
            return (edited, 'edited') if edited else (None, 'skipped')


def apply_mapping(transcription, mapping):
    """Return a copy of a GetTranscription() dict with speakers renamed (no Resolve write)"""
    result = copy.deepcopy(transcription)
    for seg in result.get('segments') or []:
        old = (seg.get('speaker') or '').strip() or None
        if old in mapping:
            seg['speaker'] = mapping[old]
    return result


##############################################################################
# Sidecar files
##############################################################################

def write_atomic(path, text):
    """Write text to path via a hidden temp file in the same directory, then rename"""
    directory, name = os.path.split(path)
    tmp = os.path.join(directory, f".{name}.tmp")
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(text)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def load_speaker_notes(path):
    """Speaker notes from an earlier run, or None if missing or unreadable"""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            notes = json.load(f)
    except (OSError, ValueError) as e:
        log_warning(f"Ignoring unreadable speaker notes {path}: {e}")
        return None
    if not isinstance(notes, dict) or not isinstance(notes.get('speakers'), dict):
        log_warning(f"Ignoring speaker notes in an unexpected format: {path}")
        return None
    log_info(f"Loaded speaker notes: {path}")
    return notes


def save_speaker_notes(path, notes):
    notes['updated'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
    try:
        write_atomic(path, json.dumps(notes, indent=2, ensure_ascii=False) + '\n')
    except OSError as e:
        log_warning(f"Could not save speaker notes to {path}: {e}")


##############################################################################
# Config
##############################################################################

def find_config_file(config_path=None):
    """Locate transcript_reformatter.conf the same way transcript_reformatter does"""
    if config_path:
        if not os.path.exists(config_path):
            log_fatal(f"Specified config file not found: {config_path}")
        return config_path
    for directory in (os.path.dirname(os.path.abspath(__file__)), os.getcwd()):
        candidate = os.path.join(directory, rf.DEFAULT_CONFIG_FILE)
        if os.path.exists(candidate):
            return candidate
    log_fatal(f"Configuration file not found. Expected: {rf.DEFAULT_CONFIG_FILE}")


def load_pipeline_config(config_file):
    """Read the optional [pipeline] section"""
    parser = configparser.ConfigParser()
    parser.read(config_file)
    return {
        'resolve_transcribe_dir': parser.get('pipeline', 'resolve_transcribe_dir', fallback=DEFAULT_RESOLVE_TRANSCRIBE_DIR),
        'speaker_id_model': parser.get('pipeline', 'speaker_id_model', fallback=None),
        'min_confidence': parser.getfloat('pipeline', 'min_confidence', fallback=DEFAULT_MIN_CONFIDENCE),
        'sidecars': parser.getboolean('pipeline', 'sidecars', fallback=True),
    }


def import_resolve_transcribe(directory):
    """Import resolve_transcribe.py from its own repo"""
    directory = os.path.expanduser(directory)
    if not os.path.isfile(os.path.join(directory, 'resolve_transcribe.py')):
        log_fatal(f"resolve_transcribe.py not found in {directory} (set [pipeline] resolve_transcribe_dir)")
    sys.path.insert(0, directory)
    try:
        import resolve_transcribe
    except ImportError as e:
        log_fatal(f"Could not import resolve_transcribe: {e}")
    for name in ('ensure_transcribed', 'relabel_speakers'):
        if not hasattr(resolve_transcribe, name):
            log_fatal(f"resolve_transcribe.py in {directory} is too old (missing {name}())")
    return resolve_transcribe


if __name__ == '__main__':
    sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 1)  # reopen STDOUT unbuffered
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(1)
