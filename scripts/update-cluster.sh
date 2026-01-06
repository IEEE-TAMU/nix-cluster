#!/usr/bin/env bash
set -e

# Target hosts derived from the repository structure
HOSTS=(
    "ieee-tamu-5B"
    "ieee-tamu-7P"
    "ieee-tamu-8J"
    # 6Q is the usual jump host due to ha-vip priority, so we update it last
    "ieee-tamu-6Q"
)

JUMP_HOST="root@ieee-tamu.engr.tamu.edu"

for host in "${HOSTS[@]}"; do
    echo "========================================"
    echo "Deploying to $host via $JUMP_HOST..."
    echo "========================================"
    
    # We use a slight delay before reboot to allow the ssh connection to close cleanly,
    # preventing the script from aborting due to 'connection reset by peer'.
    ssh -J "$JUMP_HOST" "root@$host" "nixos-rebuild switch --flake github:ieee-tamu/nix-cluster --refresh && (sleep 2; reboot) &"
    
    echo "Command sent to $host."
done

echo "All hosts have been signaled to update and reboot."
