# nix-cluster
NixOS based k3s cluster (hopefully-soon) deployed for IEEE-TAMU.


## Bootstrapping a wyse client
`nix run github:nix-community/nixos-anywhere -- --generate-hardware-config nixos-facter ./hardware/bootstrap.json --flake .#bootstrap --target-host nixos@<ip-address> --build-on-remote --env-password`
