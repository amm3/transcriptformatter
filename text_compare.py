#!/usr/bin/env python3

import sys
import os
import argparse
import logging
import time
import re

DEFAULT_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
LOGGING_FORMAT = '%(asctime)s:%(levelname)s:%(message)s'

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

def compare_texts(file1, file2):
    """
    Compare two text files word-by-word.
    Returns True if identical (ignoring punctuation, case, whitespace).
    """
    try:
        with open(file1, 'r', encoding='utf-8') as f1:
            text1 = f1.read()
        log_debug(f"Read {len(text1)} characters from {file1}")
    except Exception as e:
        log_fatal(f"Cannot read file '{file1}': {e}")
    
    try:
        with open(file2, 'r', encoding='utf-8') as f2:
            text2 = f2.read()
        log_debug(f"Read {len(text2)} characters from {file2}")
    except Exception as e:
        log_fatal(f"Cannot read file '{file2}': {e}")
    
    # Normalize both texts
    words1 = normalize_text(text1)
    words2 = normalize_text(text2)
    
    log_info(f"File 1 ({file1}): {len(words1)} words")
    log_info(f"File 2 ({file2}): {len(words2)} words")
    
    # Compare word counts first
    if len(words1) != len(words2):
        log_warning(f"Word count mismatch: {len(words1)} vs {len(words2)}")
        write_out(f"DIFFERENT: Word counts don't match ({len(words1)} vs {len(words2)})")
        return False
    
    # Compare word-by-word
    differences = []
    for i, (w1, w2) in enumerate(zip(words1, words2)):
        if w1 != w2:
            differences.append((i+1, w1, w2))
            log_debug(f"Difference at word {i+1}: '{w1}' vs '{w2}'")
    
    if differences:
        write_out(f"DIFFERENT: {len(differences)} word(s) differ")
        if len(differences) <= 10:
            for pos, w1, w2 in differences:
                write_out(f"  Position {pos}: '{w1}' vs '{w2}'")
        else:
            write_out(f"  Showing first 10 differences:")
            for pos, w1, w2 in differences[:10]:
                write_out(f"  Position {pos}: '{w1}' vs '{w2}'")
        return False
    else:
        write_out("IDENTICAL: All words match")
        return True

def main():

    parser = argparse.ArgumentParser(
        description='Compare two text files word-by-word (ignoring punctuation, case, and whitespace)',
        epilog='Example: %(prog)s file1.txt file2.txt'
    )
    parser.add_argument('files', help="Two files to compare", nargs=2, metavar='FILE')
    parser.add_argument("-v", action="store_true", default=False, help="Print extra info")
    parser.add_argument("-vv", action="store_true", default=False, help="Print (more) extra info")
    args = parser.parse_args()

    ######################################
    # Establish LOGLEVEL
    ######################################
    if args.vv:
        logging.basicConfig(format=LOGGING_FORMAT, datefmt=DEFAULT_TIME_FORMAT, level=logging.DEBUG)
    elif args.v:
        logging.basicConfig(format=LOGGING_FORMAT, datefmt=DEFAULT_TIME_FORMAT, level=logging.INFO)
    else:
        logging.basicConfig(format=LOGGING_FORMAT, datefmt=DEFAULT_TIME_FORMAT, level=logging.WARNING)

    # Validate files exist
    for filepath in args.files:
        if not os.path.isfile(filepath):
            log_fatal(f"File not found: {filepath}")
    
    # Compare the two files
    result = compare_texts(args.files[0], args.files[1])
    
    # Return 0 if identical, 1 if different
    return 0 if result else 1


##############################################################################
#
# Output and Logging Message Functions
#
##############################################################################
def write_out(message):
    info = {
      'levelname' : 'OUTPUT',
      'asctime'   : time.strftime(DEFAULT_TIME_FORMAT, time.localtime()),
      'message'   : message
    }
    print(LOGGING_FORMAT % info)

def log_fatal(msg, exit_code=-1):
    logging.critical("Fatal Err: %s\n" % msg)
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
