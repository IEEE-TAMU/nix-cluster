# Modules

This directory contains the NixOS and flake-parts modules used by the cluster. Custom config options use the `config.ieee-tamu` namespace.

## Modules

- `wyse.nix` — Main hardware-specific module for Dell Wyse thin clients (imports all sub-modules).
- `cluster-node.nix` — k3s cluster node configuration with role, init, and firewall rules.
- `ha-vip.nix` — Keepalived-based high-availability virtual IP for the k3s API.
- `network-map.nix` — Cluster hostname-to-IP mapping for `/etc/hosts` and interface config.
- `ci.nix` — CI/CD support via nix-github-actions for GitHub Actions.
- `minimal.nix` — Minimal NixOS profile (headless, minimal kernel modules).
- `meta.nix` — Metadata options (owner, flake URL).
