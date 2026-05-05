#!/bin/sh

# Note: FreeBSD grep supports -r and -o, but using -E for clarity
grep -roE "\[.*\]\(.*\)" wiki/ | while IFS=: read -r file link; do
    # Extract the URL/path from between the parentheses
    target=$(echo "$link" | sed -E 's/.*\]\((.*)\).*/\1/' | cut -d'#' -f1)

    # Skip external links (sh uses 'case' or simple string comparison for patterns)
    case "$target" in
        http*|mailto*) continue ;;
    esac

    # Calculate the directory of the file relative to the wiki root
    # We strip 'wiki/' from the start of the filename for the check
    rel_dir=$(dirname "${file#wiki/}")

    # Check existence:
    # 1. Check relative to the file's location
    # 2. Check relative to the wiki root
    if [ ! -f "wiki/$rel_dir/$target" ] && [ ! -f "wiki/$target" ]; then
        echo "BROKEN LINK: $file -> $target"
    fi
done
