#!/bin/sh

grep -roE "\[.*\]\(.*\)" wiki/ | while IFS=: read -r file link; do
    target=$(echo "$link" | sed -E 's/.*\]\((.*)\).*/\1/' | cut -d'#' -f1)

    case "$target" in
        http*|mailto*) continue ;;
    esac

    # Resolve relative to the file's directory (handles ../raw/... escaping wiki/)
    file_dir=$(dirname "$file")
    resolved=$(cd "$file_dir" && realpath -m "$target" 2>/dev/null)
    if [ -z "$resolved" ] || [ ! -f "$resolved" ]; then
        echo "BROKEN LINK: $file -> $target"
    fi
done
