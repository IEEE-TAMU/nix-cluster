# Hosts

This directory contains host specific configurations for cluster machines.

## Available Hosts

- [global](./global.nix): Not an actual host, but contains any global configuration that is applied to all hosts.
- [bootstrap](./bootstrap.nix): Minimal configuration necessary to make sure diskio and initial filesystem setup is complete (things like generating the disk partitions, setting up ssh host keys, etc).