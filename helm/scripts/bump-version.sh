#!/bin/bash

# Script to bump Helm chart versions
# Usage: ./bump-version.sh [major|minor|patch] [chart-name]

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to bump version
bump_version() {
    local version=$1
    local bump_type=$2

    IFS='.' read -ra VERSION_PARTS <<< "$version"
    local major=${VERSION_PARTS[0]}
    local minor=${VERSION_PARTS[1]}
    local patch=${VERSION_PARTS[2]}

    case $bump_type in
        "major")
            major=$((major + 1))
            minor=0
            patch=0
            ;;
        "minor")
            minor=$((minor + 1))
            patch=0
            ;;
        "patch")
            patch=$((patch + 1))
            ;;
        *)
            print_error "Invalid bump type: $bump_type. Use major, minor, or patch"
            exit 1
            ;;
    esac

    echo "$major.$minor.$patch"
}

# Function to update Chart.yaml version
update_chart_version() {
    local chart_path=$1
    local new_version=$2

    if [ -f "$chart_path/Chart.yaml" ]; then
        # Update version in Chart.yaml
        sed -i "s/^version: .*/version: $new_version/" "$chart_path/Chart.yaml"

        # Update appVersion if it matches the old version pattern
        local current_app_version=$(grep '^appVersion:' "$chart_path/Chart.yaml" | awk '{print $2}' | tr -d '"')
        if [[ "$current_app_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            sed -i "s/^appVersion: .*/appVersion: \"$new_version\"/" "$chart_path/Chart.yaml"
        fi

        print_status "Updated $chart_path/Chart.yaml to version $new_version"
    else
        print_warning "Chart.yaml not found in $chart_path"
    fi
}

# Main script logic
if [ $# -lt 1 ]; then
    print_error "Usage: $0 [major|minor|patch] [chart-name]"
    print_error "Examples:"
    print_error "  $0 patch                    # Bump patch version for all charts"
    print_error "  $0 minor frontend           # Bump minor version for frontend chart only"
    print_error "  $0 major                    # Bump major version for all charts"
    exit 1
fi

BUMP_TYPE=$1
CHART_NAME=$2

# Validate bump type
if [[ ! "$BUMP_TYPE" =~ ^(major|minor|patch)$ ]]; then
    print_error "Invalid bump type: $BUMP_TYPE. Use major, minor, or patch"
    exit 1
fi

# Get current directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELM_DIR="$(dirname "$SCRIPT_DIR")"

print_status "Bumping $BUMP_TYPE version for Helm charts in $HELM_DIR"

# Function to process a chart
process_chart() {
    local chart_path=$1
    local chart_name=$(basename "$chart_path")

    if [ -n "$CHART_NAME" ] && [ "$chart_name" != "$CHART_NAME" ]; then
        return
    fi

    if [ -f "$chart_path/Chart.yaml" ]; then
        local current_version=$(grep '^version:' "$chart_path/Chart.yaml" | awk '{print $2}')
        local new_version=$(bump_version "$current_version" "$BUMP_TYPE")

        print_status "Processing $chart_name: $current_version -> $new_version"
        update_chart_version "$chart_path" "$new_version"
    fi
}

# Process main chart
if [ -z "$CHART_NAME" ] || [ "$CHART_NAME" = "amazee-ai" ]; then
    process_chart "$HELM_DIR"
fi

# Process subcharts
if [ -d "$HELM_DIR/charts" ]; then
    for chart_dir in "$HELM_DIR/charts"/*/; do
        if [ -d "$chart_dir" ]; then
            process_chart "$chart_dir"
        fi
    done
fi

print_status "Version bump completed successfully!"
print_status "Don't forget to:"
print_status "1. Review the changes with 'git diff'"
print_status "2. Commit the version changes"
print_status "3. Create a new release to trigger the GitHub Actions workflow"