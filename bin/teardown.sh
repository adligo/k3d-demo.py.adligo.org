#!/usr/bin/env bash
# Tears down all k3d clusters and their Docker containers.
# Usage: bash teardown.sh

set -euo pipefail

echo "=== Listing k3d clusters ==="
clusters=$(k3d cluster list -o json 2>/dev/null | grep -o '"name":"[^"]*"' | cut -d'"' -f4)

if [ -z "$clusters" ]; then
  echo "No k3d clusters found."
else
  for cluster in $clusters; do
    echo "Deleting k3d cluster: $cluster"
    k3d cluster delete "$cluster"
  done
  echo "All k3d clusters deleted."
fi

# Clean up any orphaned k3d containers that weren't removed by cluster delete
orphans=$(docker ps -a --filter "label=app=k3d" --format '{{.Names}}' 2>/dev/null || true)
if [ -n "$orphans" ]; then
  echo ""
  echo "=== Removing orphaned k3d containers ==="
  echo "$orphans"
  docker rm -f $orphans
fi

echo ""
echo "Done. Verify with: docker ps -a --filter label=app=k3d"
