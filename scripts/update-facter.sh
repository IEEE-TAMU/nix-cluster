#!/usr/bin/env bash
# Update nixos-facter hardware reports for all cluster nodes
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
HARDWARE_DIR="$REPO_DIR/hardware"
JUMP_HOST="root@ieee-tamu.engr.tamu.edu"

# Hostname -> facter json postfix mapping
declare -A HOSTS=(
  [ieee-tamu-5B]="wyse-5B"
  [ieee-tamu-8J]="wyse-8J"
  [ieee-tamu-6Q]="wyse-6Q"
  [ieee-tamu-7P]="wyse-7P"
)

for host in "${!HOSTS[@]}"; do
  outfile="$HARDWARE_DIR/${HOSTS[$host]}.json"
  echo "Updating facter report for $host -> $outfile"

  ssh -J "$JUMP_HOST" "root@$host" \
    "nix run nixpkgs#nixos-facter" > "$outfile" 2>/dev/null

  echo "  Saved $outfile ($(wc -c < "$outfile") bytes)"
done

echo "All facter reports updated."
