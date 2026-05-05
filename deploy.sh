#!/bin/sh
set -e

# Deploy Lobotomy to FreeBSD jail using rsync
# Usage: ./deploy.sh [--full] [--dry-run]
#   --full:    include wiki/, raw/, and blog/ (default: preserve them)
#   --dry-run: show what would be synced without making changes

JAIL_ROOT="/usr/local/bastille/jails/Lobotomy/root/var/www/Lobotomy"
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ ! -d "$JAIL_ROOT" ]; then
    echo "Error: Jail not found at $JAIL_ROOT"
    exit 1
fi

# Check for rsync
if ! command -v rsync >/dev/null; then
    echo "Error: rsync not found. Install with: pkg install rsync"
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
DRY_RUN=""
for arg in "$@"; do
    case "$arg" in
        --full) FULL_DEPLOY=1 ;;
        --dry-run) DRY_RUN="--dry-run" ;;
    esac
done

if [ "$FULL_DEPLOY" = "1" ]; then
    echo "🔴 FULL DEPLOY MODE: will overwrite wiki/, raw/, blog/"
    echo "   (Ctrl+C to cancel)"
    sleep 2
fi

# Build rsync command with excludes
RSYNC_ARGS="-av --delete --compress"
[ -n "$DRY_RUN" ] && RSYNC_ARGS="$RSYNC_ARGS $DRY_RUN"

# Always exclude these
RSYNC_ARGS="$RSYNC_ARGS --exclude=.git"
RSYNC_ARGS="$RSYNC_ARGS --exclude=__pycache__"
RSYNC_ARGS="$RSYNC_ARGS --exclude=.pytest_cache"
RSYNC_ARGS="$RSYNC_ARGS --exclude=.env"
RSYNC_ARGS="$RSYNC_ARGS --exclude=deploy.sh"
RSYNC_ARGS="$RSYNC_ARGS --exclude=tools/server.log"

# Exclude data dirs if not full deploy
if [ "$FULL_DEPLOY" != "1" ]; then
    RSYNC_ARGS="$RSYNC_ARGS --exclude=wiki/"
    RSYNC_ARGS="$RSYNC_ARGS --exclude=raw/"
    RSYNC_ARGS="$RSYNC_ARGS --exclude=blog/"
    RSYNC_ARGS="$RSYNC_ARGS --exclude=config.json"
    RSYNC_ARGS="$RSYNC_ARGS --exclude=.user.json"
    RSYNC_ARGS="$RSYNC_ARGS --exclude=.tokens.json"
    RSYNC_ARGS="$RSYNC_ARGS --exclude=.login_log.json"
    echo "📦 Deploying code (preserving wiki/, raw/, blog/, config.json)..."
else
    echo "📦 Deploying everything..."
fi

# Run rsync
# Trailing slash on source = sync contents (not the dir itself)
eval rsync $RSYNC_ARGS "$REPO_DIR/" "$JAIL_ROOT/"

echo ""
echo "✅ Deploy complete!"
echo ""
echo "Next steps:"
echo "  1. Log into the jail: bastille console Lobotomy"
if [ "$FULL_DEPLOY" != "1" ]; then
    echo "  2. Verify config.json has correct API keys"
fi
echo "  3. Restart the service: service lobotomy restart"
echo "     (or: pkill -f 'python3.*serve.py')"
