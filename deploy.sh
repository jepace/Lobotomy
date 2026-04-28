#!/bin/sh
set -e

# Deploy Lobotomy to FreeBSD jail
# Usage: ./deploy.sh [--full]
#   --full: include wiki/, raw/, and blog/ (default: preserve them)

JAIL_ROOT="/usr/local/bastille/jails/Lobotomy/root/var/www/Lobotomy"
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ ! -d "$JAIL_ROOT" ]; then
    echo "Error: Jail not found at $JAIL_ROOT"
    exit 1
fi

echo "Deploying Lobotomy to $JAIL_ROOT"
echo ""

# Check for uncommitted changes
if ! git -C "$REPO_DIR" diff-index --quiet HEAD --; then
    echo "⚠️  Warning: You have uncommitted changes. Commit first?"
    exit 1
fi

FULL_DEPLOY=0
if [ "$1" = "--full" ]; then
    FULL_DEPLOY=1
    echo "🔴 FULL DEPLOY MODE: will overwrite wiki/, raw/, blog/"
    echo "   (Ctrl+C to cancel)"
    sleep 3
fi

# Create jail dirs if missing
mkdir -p "$JAIL_ROOT/tools/templates"
mkdir -p "$JAIL_ROOT/wiki"
mkdir -p "$JAIL_ROOT/raw/inbox"
mkdir -p "$JAIL_ROOT/raw/assets"
mkdir -p "$JAIL_ROOT/blog"

# Deploy code
echo "📦 Deploying code..."
cp -r "$REPO_DIR/tools/"* "$JAIL_ROOT/tools/"
cp "$REPO_DIR/CLAUDE.md" "$JAIL_ROOT/"
cp "$REPO_DIR/README.md" "$JAIL_ROOT/"
cp "$REPO_DIR/requirements.txt" "$JAIL_ROOT/"

# Deploy full data (if --full)
if [ "$FULL_DEPLOY" = "1" ]; then
    echo "🔄 Copying wiki/, raw/, blog/..."
    rm -rf "$JAIL_ROOT/wiki" "$JAIL_ROOT/raw" "$JAIL_ROOT/blog"
    cp -r "$REPO_DIR/wiki" "$JAIL_ROOT/"
    cp -r "$REPO_DIR/raw" "$JAIL_ROOT/"
    cp -r "$REPO_DIR/blog" "$JAIL_ROOT/" 2>/dev/null || true
else
    echo "⏭️  Skipping wiki/, raw/, blog/ (use --full to deploy those)"
fi

# Don't overwrite production config
if [ ! -f "$JAIL_ROOT/config.json" ]; then
    if [ -f "$REPO_DIR/config.json" ]; then
        cp "$REPO_DIR/config.json" "$JAIL_ROOT/"
        echo "⚙️  Deployed config.json (update API keys in the jail)"
    fi
fi

echo ""
echo "✅ Deploy complete!"
echo ""
echo "Next steps:"
echo "  1. Log into the jail: bastille console Lobotomy"
echo "  2. Update API keys in config.json if needed"
echo "  3. Restart the service: service lobotomy restart"
echo "     (or: pkill -f 'python3.*serve.py')"
