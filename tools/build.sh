#!/bin/sh
# Build or serve the wiki web front end using MkDocs.
#
# Usage:
#   sh tools/build.sh              # build static site into site/
#   sh tools/build.sh --serve      # start dev server on 127.0.0.1:8000
#   sh tools/build.sh --serve 0.0.0.0:8080   # bind to custom addr:port
#
# Requirements (FreeBSD):
#   pkg install py311-mkdocs py311-mkdocs-material
#   -- or --
#   pip install mkdocs mkdocs-material
#
# Production deployment:
#   sh tools/build.sh
#   cp -r site/ /usr/local/www/wiki/   # or wherever nginx serves from

set -e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

if [ "$1" = "--serve" ]; then
    ADDR="${2:-127.0.0.1:8000}"
    echo "Starting MkDocs dev server at http://$ADDR"
    echo "Press Ctrl-C to stop."
    exec mkdocs serve --dev-addr="$ADDR"
fi

echo "Building wiki static site..."
mkdocs build --clean
echo ""
echo "Site built: site/"
echo "Quick preview: python3 -m http.server 8000 --directory site"
echo "Deploy:       copy site/ to your nginx document root"
