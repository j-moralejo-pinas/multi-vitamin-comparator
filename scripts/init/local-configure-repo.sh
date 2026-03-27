#!/bin/bash
# Local script to configure repository (mirrors GitHub Actions workflow)
# Usage: local-configure-repo.sh <python_version> [python_version_max] [repo_topics] [workflow]
#
# Prerequisites:
#   - gh CLI is installed and authenticated
#   - You are in the git project root directory
#   - All setup scripts are available in ./scripts/init/

set -euo pipefail

# Validate inputs
PYTHON_VERSION="${1:?Python version is required}"
WORKFLOW="${2:?Workflow is required}"
PYTHON_VERSION_MAX="${3:-}"
REPO_TOPICS="${4:-}"


# Get GITHUB_REPOSITORY from git remote URL
# Convert from git@github.com:owner/repo.git or https://github.com/owner/repo.git to owner/repo
REPO_URL=$(git config --get remote.origin.url)
if [[ $REPO_URL =~ git@github.com:([^/]+)/(.+)\.git$ ]]; then
  GITHUB_REPOSITORY="${BASH_REMATCH[1]}/${BASH_REMATCH[2]}"
elif [[ $REPO_URL =~ https://github.com/([^/]+)/(.+)\.git$ ]]; then
  GITHUB_REPOSITORY="${BASH_REMATCH[1]}/${BASH_REMATCH[2]}"
else
  echo "Error: Could not determine repository from git remote"
  exit 1
fi

echo "=========================================="
echo "Local Repository Configuration Script"
echo "=========================================="
echo "Repository: $GITHUB_REPOSITORY"
echo "Python version: $PYTHON_VERSION"
echo "Python max version: ${PYTHON_VERSION_MAX:-'Not specified'}"
echo "Topics: ${REPO_TOPICS:-'Not specified'}"
echo "Workflow: $WORKFLOW"
echo "=========================================="
echo ""

# Check if direnv, and nix are available
if ! command -v direnv >/dev/null; then
  echo "Error: direnv is not installed or not in PATH"
  exit 1
fi
if ! command -v nix >/dev/null; then
  echo "Error: nix is not installed or not in PATH"
  exit 1
fi

# Allow direnv to load .envrc files
direnv allow .
eval "$(direnv export bash)"

# Check if gh is available
if ! command -v gh >/dev/null; then
  echo "Error: gh CLI is not installed or not in PATH"
  exit 1
fi

# Check if gh is authenticated
if ! gh auth status >/dev/null 2>&1; then
  echo "Error: gh Cj-moralejo-pinas/asdfLI is not authenticated. Please run 'gh auth login' first"
  exit 1
fi

# Check if git user and email are set
if ! git config user.name >/dev/null; then
  echo "Error: git user.name is not set. Please run 'git config --global user.name \"Your Name\"'"
  exit 1
fi
if ! git config user.email >/dev/null; then
  echo "Error: git user.email is not set. Please run 'git config --global user.email \"you@example.com\"'"
  exit 1
fi

echo "✓ gh CLI is authenticated"
echo ""

# Ensure dependencies
# chmod +x ./scripts/init/ensure-dependencies.sh
# ./scripts/init/ensure-dependencies.sh
# echo ""

# Step 1: Get current repo info and initialize project
echo "Step 1: Get repo info and initialize project..."
export GITHUB_REPOSITORY
chmod +x ./scripts/init/configure-project.sh
./scripts/init/configure-project.sh \
  "$PYTHON_VERSION" \
  "$WORKFLOW" \
  "$PYTHON_VERSION_MAX" \
  "$REPO_TOPICS"


echo "✓ Step 1 complete"
echo ""

# Step 2: Core settings
echo "Step 2: Core repo settings..."
chmod +x ./scripts/init/setup-core-settings.sh
./scripts/init/setup-core-settings.sh "$GITHUB_REPOSITORY"
echo "✓ Step 2 complete"
echo ""

# Step 3: Topics
echo "Step 3: Set repository topics..."
chmod +x ./scripts/init/setup-topics.sh
./scripts/init/setup-topics.sh "$GITHUB_REPOSITORY" "$REPO_TOPICS"
echo "✓ Step 3 complete"
echo ""

# Step 4: Workflow token permissions
echo "Step 4: Set workflow token permissions..."
chmod +x ./scripts/init/setup-workflow-permissions.sh
./scripts/init/setup-workflow-permissions.sh "$GITHUB_REPOSITORY"
echo "✓ Step 4 complete"
echo ""

# Step 5: Create or update rulesets
echo "Step 5: Create or update rulesets..."
chmod +x ./scripts/init/setup-rulesets.sh
./scripts/init/setup-rulesets.sh "$GITHUB_REPOSITORY" "$WORKFLOW"
echo "✓ Step 5 complete"
echo ""

# Step 6: Create venv with uv and install optional dependencies
echo "Step 6: Create virtual environment and install optional dependencies..."
chmod +x ./scripts/init/setup-venv.sh
./scripts/init/setup-venv.sh
echo "✓ Step 6 complete"
echo ""

echo "=========================================="
echo "✓ Repository configuration completed!"
echo "=========================================="
