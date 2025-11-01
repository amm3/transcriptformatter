#!/usr/bin/env python3
"""
Word-by-word comparison utility to find exact differences between two text files.
Useful for investigating sanity check failures.
"""

import sys
import re

def normalize_text(text):
    """Normalize text by removing punctuation, converting to lowercase, and splitting into words."""
    text = re.sub(r'[^\w\s]', '', text.lower())
    words = text.split()
    return words

def find_word_differences(file1, file2):
    """Find and display all word differences between two files."""
    
    # Read files
    try:
        with open(file1, 'r', encoding='utf-8') as f:
            text1 = f.read()
        print(f"File 1: {file1}")
        print(f"  Length: {len(text1)} characters")
    except Exception as e:
        print(f"ERROR: Cannot read {file1}: {e}")
        return
    
    try:
        with open(file2, 'r', encoding='utf-8') as f:
            text2 = f.read()
        print(f"File 2: {file2}")
        print(f"  Length: {len(text2)} characters")
    except Exception as e:
        print(f"ERROR: Cannot read {file2}: {e}")
        return
    
    # Normalize
    words1 = normalize_text(text1)
    words2 = normalize_text(text2)
    
    print(f"\nWord counts:")
    print(f"  File 1: {len(words1)} words")
    print(f"  File 2: {len(words2)} words")
    print(f"  Difference: {abs(len(words1) - len(words2))} words")
    
    # Find differences
    min_len = min(len(words1), len(words2))
    max_len = max(len(words1), len(words2))
    
    print(f"\n{'='*80}")
    print("WORD-BY-WORD COMPARISON")
    print('='*80)
    
    differences = []
    
    # Check word by word up to min length
    for i in range(min_len):
        if words1[i] != words2[i]:
            differences.append(i)
            
            # Show context
            start = max(0, i - 5)
            end = min(min_len, i + 6)
            
            print(f"\nDifference at position {i + 1}:")
            print(f"  Context (file 1): ...{' '.join(words1[start:end])}...")
            print(f"  Context (file 2): ...{' '.join(words2[start:end])}...")
            print(f"  >>> File 1 word: '{words1[i]}'")
            print(f"  >>> File 2 word: '{words2[i]}'")
    
    # Check for extra/missing words
    if len(words1) > len(words2):
        print(f"\nFile 1 has {len(words1) - len(words2)} EXTRA word(s):")
        for i in range(min_len, len(words1)):
            start = max(0, i - 5)
            end = min(len(words1), i + 6)
            print(f"  Position {i + 1}: '{words1[i]}'")
            print(f"    Context: ...{' '.join(words1[start:end])}...")
    elif len(words2) > len(words1):
        print(f"\nFile 2 has {len(words2) - len(words1)} EXTRA word(s):")
        for i in range(min_len, len(words2)):
            start = max(0, i - 5)
            end = min(len(words2), i + 6)
            print(f"  Position {i + 1}: '{words2[i]}'")
            print(f"    Context: ...{' '.join(words2[start:end])}...")
    
    # Summary
    print(f"\n{'='*80}")
    print("SUMMARY")
    print('='*80)
    
    if not differences and len(words1) == len(words2):
        print("✓ Files are IDENTICAL (after normalization)")
    else:
        print(f"✗ Files are DIFFERENT")
        print(f"  Word substitutions: {len(differences)}")
        print(f"  Word count difference: {abs(len(words1) - len(words2))}")
        print(f"  Total discrepancies: {len(differences) + abs(len(words1) - len(words2))}")

def main():
    if len(sys.argv) != 3:
        print("Usage: python find_word_diff.py <file1> <file2>")
        print("\nExample:")
        print("  python find_word_diff.py original.txt reformatted.txt")
        return 1
    
    find_word_differences(sys.argv[1], sys.argv[2])
    return 0

if __name__ == '__main__':
    sys.exit(main())
