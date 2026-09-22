#!/bin/sh
set -e

# Deploy Lobotomy to FreeBSD jail using rsync
# Usage: ./deploy.sh [--full] [--dry-run] [--skip-tests]
#   --full:       include wiki/ and raw/ (default: preserve them)
#   --dry-run:    show what would be synced without making changes
#   --skip-tests: deploy without running the test suite first (not recommended)

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
JAIL_ROOT="/usr/local/bastille/jails/lobotomy/root/var/www/Lobotomy"
RC_SRC="$REPO_DIR/contrib/freebsd/rc.d/lobotomy"
RC_DEST="/usr/local/bastille/jails/lobotomy/root/usr/local/etc/rc.d/lobotomy"

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
SKIP_TESTS=0
for arg in "$@"; do
    case "$arg" in
        --full) FULL_DEPLOY=1 ;;
        --dry-run) DRY_RUN="--dry-run" ;;
        --skip-tests) SKIP_TESTS=1 ;;
    esac
done

# Run the test suite before shipping anything. It is stdlib-only — no flask, no network,
# no LLM, no config.json needed — so it runs on this host whether or not the app's
# dependencies are installed here, and takes well under a second.
#
# This is the check that matters: the uncommitted-changes check above only proves the code
# is saved, not that it works. Most of what this deploys is guards, and a broken guard is
# silent — it does not crash the server, it just stops protecting the wiki.
if [ "$SKIP_TESTS" = "1" ]; then
    echo "⚠️  Skipping tests (--skip-tests)"
elif [ ! -f "$REPO_DIR/tools/tests/run_all.py" ]; then
    echo "⚠️  No test suite found at tools/tests/run_all.py — deploying unverified"
else
    echo "🧪 Running tests..."
    if ! python3 "$REPO_DIR/tools/tests/run_all.py" >/tmp/lobotomy-deploy-tests.$$ 2>&1; then
        tail -30 /tmp/lobotomy-deploy-tests.$$
        rm -f /tmp/lobotomy-deploy-tests.$$
        echo ""
        echo "❌ Tests failed — nothing was deployed."
        echo "   Fix them, or deploy anyway with: ./deploy.sh --skip-tests"
        exit 1
    fi
    tail -3 /tmp/lobotomy-deploy-tests.$$ | head -2
    rm -f /tmp/lobotomy-deploy-tests.$$
    echo ""
fi

if [ "$FULL_DEPLOY" = "1" ]; then
    echo "🔴 FULL DEPLOY MODE: will overwrite wiki/, raw/"
    echo "   (Ctrl+C to cancel)"
    sleep 2
fi

# Build rsync command with excludes
RSYNC_ARGS="-av --delete --compress"
[ -n "$DRY_RUN" ] && RSYNC_ARGS="$RSYNC_ARGS $DRY_RUN"

# Always exclude these
RSYNC_ARGS="$RSYNC_ARGS --exclude=.git"
RSYNC_ARGS="$RSYNC_ARGS --exclude=.gitignore"
RSYNC_ARGS="$RSYNC_ARGS --exclude=__pycache__"
RSYNC_ARGS="$RSYNC_ARGS --exclude=.pytest_cache"
RSYNC_ARGS="$RSYNC_ARGS --exclude=.env"
# Leading slash = repo root only. Without it rsync matches the name at ANY depth, which
# silently kept tools/README.md off the server along with the root README.md.
RSYNC_ARGS="$RSYNC_ARGS --exclude=/deploy.sh"
RSYNC_ARGS="$RSYNC_ARGS --exclude=/README.md"
RSYNC_ARGS="$RSYNC_ARGS --exclude=/CLAUDE.md"
RSYNC_ARGS="$RSYNC_ARGS --exclude=server.log*"
# The test suite runs here, before the rsync below — it has no business on the server.
# tools/tests/mutate.py in particular edits agent.py in place to check that the suite
# notices a broken guard, which is exactly what should never exist next to a running
# server.
RSYNC_ARGS="$RSYNC_ARGS --exclude=/tools/tests/"
RSYNC_ARGS="$RSYNC_ARGS --exclude=/usr/"

# Exclude data dirs if not full deploy
if [ "$FULL_DEPLOY" != "1" ]; then
    RSYNC_ARGS="$RSYNC_ARGS --exclude=wiki/"
    RSYNC_ARGS="$RSYNC_ARGS --exclude=raw/"
    RSYNC_ARGS="$RSYNC_ARGS --exclude=config.json"
    RSYNC_ARGS="$RSYNC_ARGS --exclude=.user.json"
    RSYNC_ARGS="$RSYNC_ARGS --exclude=.tokens.json"
    RSYNC_ARGS="$RSYNC_ARGS --exclude=.login_log.json"
    echo "📦 Deploying code (preserving wiki/, raw/, config.json)..."
else
    echo "📦 Deploying everything..."
fi

# Run rsync
# Trailing slash on source = sync contents (not the dir itself)
eval rsync $RSYNC_ARGS "$REPO_DIR/" "$JAIL_ROOT/"

# Install rc script directly — rsync puts it under the app dir,
# but it belongs in the jail's /usr/local/etc/rc.d/
mkdir -p "$(dirname "$RC_DEST")"
install -m 555 "$RC_SRC" "$RC_DEST"
echo "✔ rc.d script installed → $RC_DEST"

echo ""
echo "✅ Deploy complete!"
echo ""
echo "Next steps:"
if [ "$FULL_DEPLOY" != "1" ]; then
    echo "  1. Verify config.json has correct API keys"
fi
echo "  • Enable service (first deploy): bastille cmd lobotomy sysrc lobotomy_enable=YES"
echo "  • Restart: bastille cmd lobotomy service lobotomy restart"
