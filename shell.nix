{
  pkgs ? import <nixpkgs> { },
}:
pkgs.mkShellNoCC {
  packages = builtins.attrValues {
    inherit (pkgs)
      nixos-anywhere
      nixos-rebuild
      sops
      openssh
      ssh-to-age
      ;
  };
}
