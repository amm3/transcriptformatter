#!/usr/bin/env python3
"""
Podcast transcription pipeline: audio file -> final reformatted transcript.

  1. Transcribe in DaVinci Resolve (or reuse the clip's existing transcription)
  2. Ask OpenAI (with web search) to name unlabeled speakers ("Speaker 1517")
  3. Confirm each name, then write the names back into Resolve
  4. Export the Resolve-format transcript to a work directory
  5. Reformat it with transcript_reformatter to the final location

Each piece still runs on its own: resolve_transcribe.py, speaker_id.py and
transcript_reformatter.py. This script only wires them together.
"""

import sys
import os
import argparse
import configparser
import copy
import logging

import transcript_reformatter as rf
from transcript_reformatter import write_out, write_status, log_fatal, log_warning, log_error, log_info, log_debug

DEFAULT_TIME_FORMAT = rf.DEFAULT_TIME_FORMAT
LOGGING_FORMAT = rf.LOGGING_FORMAT

DEFAULT_RESOLVE_TRANSCRIBE_DIR = "~/dev/resolve_transcribe"
DEFAULT_WORK_DIR = "~/Downloads/tmp/s"
DEFAULT_MIN_CONFIDENCE = 0.8


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
                        help=f"Where the intermediate Resolve-format transcript goes (default: config or {DEFAULT_WORK_DIR})")

    sid = parser.add_argument_group('speaker identification')
    sid.add_argument('-y', '--yes', action='store_true',
                     help="Don't prompt: accept suggestions at or above --min-confidence, skip the rest")
    sid.add_argument('--min-confidence', type=float, default=None,
                     help=f"Confidence needed to auto-accept with --yes (default: config or {DEFAULT_MIN_CONFIDENCE})")
    sid.add_argument('--skip-speaker-id', action='store_true', help="Don't run AI speaker identification")
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

    base_name = os.path.splitext(os.path.basename(audio_file))[0]
    output_file = os.path.abspath(os.path.expanduser(args.output)) if args.output \
        else os.path.splitext(audio_file)[0] + '.txt'
    if os.path.exists(output_file) and not args.force:
        log_fatal(f"Output file already exists: {output_file} (use -f/--force to overwrite)")

    config_file = find_config_file(args.config)
    openai_config = rf.load_config(config_file)
    if not openai_config.get('api_key'):
        log_fatal("API key not found in configuration file")
    pipeline = load_pipeline_config(config_file)

    work_dir = os.path.expanduser(args.work_dir or pipeline['work_dir'])
    os.makedirs(work_dir, exist_ok=True)
    min_confidence = args.min_confidence if args.min_confidence is not None else pipeline['min_confidence']

    rt = import_resolve_transcribe(pipeline['resolve_transcribe_dir'])

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
    resolve_write_failed = False
    if not args.skip_speaker_id:
        mapping, still_unlabeled = identify_and_confirm(
            transcription, audio_file, openai_config, pipeline, rt,
            auto_yes=args.yes, min_confidence=min_confidence
        )

    if mapping:
        if args.no_apply:
            log_info("--no-apply: renaming speakers in the exported transcript only")
            transcription = apply_mapping(transcription, mapping)
        else:
            write_status("Writing speaker names to Resolve...")
            backup = os.path.join(work_dir, f"{base_name}.transcription-backup.json")
            changed, error = rt.relabel_speakers(item, mapping, backup_file=backup)
            if error:
                # Keep the confirmed names: apply them to the export instead
                resolve_write_failed = True
                log_warning(f"Failed to write speaker names to Resolve: {error}")
                log_warning("Applying the names to the exported transcript only; rename them in the "
                            "Resolve UI if you want Resolve to learn these voices")
                transcription = apply_mapping(transcription, mapping)
            else:
                write_out(f"Renamed {changed} segments in Resolve (original saved to {backup})")
                transcription = rt.get_transcription(item)

    ######################################
    # 4. Export the Resolve-format transcript
    ######################################
    intermediate = rt.export_transcription(project, item, transcription, audio_file, work_dir, base_name, 'txt')
    if not intermediate:
        log_fatal("Failed to export the Resolve transcript")
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
    if resolve_write_failed:
        write_out("  Names applied to the transcript only (Resolve write failed)")
    for old, new in mapping.items():
        write_out(f"  {old or '(no speaker)'} -> {new}")
    if still_unlabeled:
        write_out("  Still unlabeled: " + ', '.join(label or '(no speaker)' for label in still_unlabeled))
    return 0


def identify_and_confirm(transcription, audio_file, openai_config, pipeline, rt, auto_yes, min_confidence):
    """
    Find unlabeled speakers, ask OpenAI who they are, and confirm each name.
    Returns: (mapping {label: name}, [labels left unlabeled])
    """
    try:
        import speaker_id
    except ImportError as e:
        log_warning(f"speaker_id.py not available ({e}); skipping speaker identification")
        return {}, []

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
        return {}, []

    write_out(f"Unlabeled speakers: " + ', '.join(
        f"{label or '(no speaker)'} ({len(idx)} segments)" for label, idx in unlabeled.items()))

    show_name, episode_title = speaker_id.episode_info(audio_file)
    model = pipeline['speaker_id_model'] or speaker_id.DEFAULT_MODEL
    write_status(f"Asking {model} to identify {len(unlabeled)} speaker(s)...")
    try:
        suggestions = speaker_id.identify_speakers(
            segments, show_name, episode_title, openai_config['api_key'], model)
    except Exception as e:
        log_error(f"Speaker identification failed: {e}")
        suggestions = {}

    interactive = not auto_yes and sys.stdin.isatty()
    if not auto_yes and not interactive:
        log_warning("stdin is not a terminal and --yes was not given; leaving speakers unlabeled")
        return {}, list(unlabeled)

    mapping = {}
    still_unlabeled = []
    # Most-heard speakers first
    for label, indices in sorted(unlabeled.items(), key=lambda kv: -len(kv[1])):
        suggestion = suggestions.get(label) or {}
        name = suggestion.get('name')
        confidence = float(suggestion.get('confidence') or 0.0)

        if interactive:
            name = prompt_for_name(label, indices, segments, name, confidence,
                                   suggestion.get('evidence'), min_confidence)
        elif not name or confidence < min_confidence:
            log_info(f"{label or '(no speaker)'}: not auto-accepted "
                     f"({name or 'no suggestion'}, confidence {confidence:.2f})")
            name = None

        if name and name != label:
            mapping[label] = name
        else:
            still_unlabeled.append(label)

    return mapping, still_unlabeled


def prompt_for_name(label, indices, segments, name, confidence, evidence, min_confidence):
    """Show a suggestion and ask the user to accept, edit or skip it. Returns the name or None."""
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
            return None
        if answer.startswith('a') and name:
            return name
        if answer.startswith('s'):
            return None
        if answer.startswith('e'):
            try:
                edited = input("  Name: ").strip()
            except EOFError:
                return None
            return edited or None


def apply_mapping(transcription, mapping):
    """Return a copy of a GetTranscription() dict with speakers renamed (no Resolve write)"""
    result = copy.deepcopy(transcription)
    for seg in result.get('segments') or []:
        old = (seg.get('speaker') or '').strip() or None
        if old in mapping:
            seg['speaker'] = mapping[old]
    return result


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
        'work_dir': parser.get('pipeline', 'work_dir', fallback=DEFAULT_WORK_DIR),
        'speaker_id_model': parser.get('pipeline', 'speaker_id_model', fallback=None),
        'min_confidence': parser.getfloat('pipeline', 'min_confidence', fallback=DEFAULT_MIN_CONFIDENCE),
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
