#!/usr/bin/env bash
set -e

# Target hosts derived from the repository structure
HOSTS=(
    "ieee-tamu-5B"
    "ieee-tamu-7P"
    "ieee-tamu-8J"
    "ieee-tamu-6Q"
)

JUMP_HOST="root@ieee-tamu.engr.tamu.edu"

for host in "${HOSTS[@]}"; do
    echo "========================================"
    echo "Cleaning $host via $JUMP_HOST..."
    echo "========================================"

    ssh -J "$JUMP_HOST" "root@$host" "nix-collect-garbage && k3s crictl rmi --prune"

    echo "Command sent to $host."
done

echo "All hosts have been cleaned."
