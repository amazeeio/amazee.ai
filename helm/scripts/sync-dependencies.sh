#!/bin/bash

# Script to synchronize Helm chart dependencies and versions
# Usage: ./sync-dependencies.sh [update|check|bump]

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

# Get current directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELM_DIR="$(dirname "$SCRIPT_DIR")"

# Function to get chart version
get_chart_version() {
    local chart_path=$1
    if [ -f "$chart_path/Chart.yaml" ]; then
        grep '^version:' "$chart_path/Chart.yaml" | awk '{print $2}'
    else
        echo ""
    fi
}

# Function to update main chart dependencies
update_main_dependencies() {
    local main_version=$(get_chart_version "$HELM_DIR")

    print_status "Updating main chart dependencies to match subchart versions..."

    # Get subchart versions (excluding postgres since we use Bitnami)
    local backend_version=$(get_chart_version "$HELM_DIR/charts/backend")
    local frontend_version=$(get_chart_version "$HELM_DIR/charts/frontend")

    # Update Chart.yaml dependencies
    cd "$HELM_DIR"

    # Create backup
    cp Chart.yaml Chart.yaml.backup

    # Update dependency versions for custom charts only
    sed -i "s/version: 0.0.1/version: $backend_version/" Chart.yaml
    sed -i "s/version: 0.0.1/version: $frontend_version/" Chart.yaml

    # Update dependencies
    helm dependency update

    print_status "✅ Updated main chart dependencies"
    print_status "  - postgresql: 16.7.12 (Bitnami)"
    print_status "  - backend: $backend_version"
    print_status "  - frontend: $frontend_version"
}

# Function to check version consistency
check_versions() {
    print_status "Checking version consistency across charts..."

    local main_version=$(get_chart_version "$HELM_DIR")
    local backend_version=$(get_chart_version "$HELM_DIR/charts/backend")
    local frontend_version=$(get_chart_version "$HELM_DIR/charts/frontend")

    print_status "Current versions:"
    print_status "  - Main chart: $main_version"
    print_status "  - PostgreSQL: 16.7.12 (Bitnami)"
    print_status "  - Backend: $backend_version"
    print_status "  - Frontend: $frontend_version"

    # Check if custom chart versions match
    if [ "$main_version" = "$backend_version" ] && [ "$main_version" = "$frontend_version" ]; then
        print_status "✅ All custom chart versions are consistent"
    else
        print_warning "⚠️  Custom chart versions are inconsistent"
        print_warning "Consider running: $0 update"
    fi
}

# Function to bump all versions together
bump_all_versions() {
    if [ $# -lt 1 ]; then
        print_error "Usage: $0 bump [major|minor|patch]"
        exit 1
    fi

    local bump_type=$1

    # Validate bump type
    if [[ ! "$bump_type" =~ ^(major|minor|patch)$ ]]; then
        print_error "Invalid bump type: $bump_type. Use major, minor, or patch"
        exit 1
    fi

    print_status "Bumping $bump_type version for all custom charts..."

    # Use the existing bump script (it will skip postgres automatically)
    "$SCRIPT_DIR/bump-version.sh" "$bump_type"

    # Update dependencies after bumping
    update_main_dependencies

    print_status "✅ All custom charts bumped and dependencies updated"
}

# Main script logic
case "${1:-check}" in
    "update")
        update_main_dependencies
        ;;
    "check")
        check_versions
        ;;
    "bump")
        bump_all_versions "$2"
        ;;
    *)
        print_error "Usage: $0 [update|check|bump [major|minor|patch]]"
        print_error "  update: Update main chart dependencies to match subchart versions"
        print_error "  check:  Check version consistency across all charts"
        print_error "  bump:   Bump all chart versions together"
        exit 1
        ;;
esac