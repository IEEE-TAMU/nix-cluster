# nix-cluster
NixOS based k3s cluster deployed for IEEE-TAMU.

## Bootstrapping a wyse host
`nix run github:nix-community/nixos-anywhere -- --generate-hardware-config nixos-facter ./hardware/bootstrap.json --flake .#bootstrap --target-host nixos@<ip-address> --build-on-remote --env-password`

## Adding the bootstrapped host to the secrets file
`ssh-keyscan <ip-address> | ssh-to-age`

Add the scanned age key to the .sops.yaml file and reencrypt the secrets file with `sops updatekeys secrets.yaml`

## Deploying the cluster
`nixos-rebuild switch -s --use-remote-sudo --fast --flake .#<server-name> --target-host root@<ip-address> --build-host root@<ip-address> --verbose`
