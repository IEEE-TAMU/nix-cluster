{
  description = "";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-25.11";
    flake-parts.url = "github:hercules-ci/flake-parts";
    import-tree.url = "github:vic/import-tree";
    sops-nix.url = "github:Mic92/sops-nix";
    sops-nix.inputs.nixpkgs.follows = "nixpkgs";
    disko.url = "github:nix-community/disko";
    disko.inputs.nixpkgs.follows = "nixpkgs";
    facter.url = "github:nix-community/nixos-facter-modules";
  };

  outputs =
    {
      nixpkgs,
      flake-parts,
      import-tree,
      ...
    }@inputs:
    flake-parts.lib.mkFlake { inherit inputs; } {
      systems = [ "x86_64-linux" ];

      imports = [
        flake-parts.flakeModules.modules
        (import-tree [
          ./modules
          ./hosts
        ])
      ];

      meta = {
        flake = "github:ieee-tamu/nix-cluster";
        owner.sshKeys = [
          "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIO2FLDIautZl87H9xJKsPJsO0gO/8t4jOS3Szz4j2qY4 IEEE@IEEEPC"
          "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOevicH4lyiFYuIcUPKSvu3+zjY67wzLkkCCN3Er7Hff caleb@chnorton-fw"
        ];
      };

      perSystem =
        { pkgs, ... }:
        {
          formatter = pkgs.nixfmt-tree;
          devShells.default = import ./shell.nix { inherit pkgs; };
        };
    };
}
