{
  description = "";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-24.11";
    systems.url = "github:nix-systems/default";
    flake-parts.url = "github:hercules-ci/flake-parts";
    sops-nix.url = "github:Mic92/sops-nix";
    sops-nix.inputs.nixpkgs.follows = "nixpkgs";
    git-hooks.url = "github:gigamonster256/git-hooks.nix";
    git-hooks.inputs.nixpkgs.follows = "nixpkgs";
    disko.url = "github:nix-community/disko";
    disko.inputs.nixpkgs.follows = "nixpkgs";
    facter.url = "github:numtide/nixos-facter-modules";
  };

  outputs = inputs @ {
    nixpkgs,
    flake-parts,
    git-hooks,
    disko,
    facter,
    ...
  }:
    flake-parts.lib.mkFlake {inherit inputs;} {
      imports = [
        git-hooks.flakeModule
      ];
      systems = import inputs.systems;
      perSystem = {
        config,
        pkgs,
        ...
      }: {
        formatter = pkgs.alejandra;

        pre-commit.settings.hooks = {
          alejandra.enable = true;
          pretty-format-json.enable = true;
          pretty-format-json.settings.autofix = true;
          check-json.enable = true;
          yamlfmt.enable = true;
          yamlfmt.settings.lint-only = false;
          typos.enable = true;
        };

        devShells.default = pkgs.mkShellNoCC {
          buildInputs = builtins.attrValues {
            inherit
              (pkgs)
              nixos-anywhere
              sops
              kubectl
              ;
          };

          inputsFrom = [
            config.pre-commit.devShell
          ];
        };
      };

      flake = {
        nixosModules = import ./modules;

        # configuration for getting nixos up and running on a new machine
        # generates host ssh keys, checks hardware configuration, etc
        # before commissioning into the cluster
        nixosConfigurations.bootstrap = nixpkgs.lib.nixosSystem {
          system = "x86_64-linux";
          modules = [
            disko.nixosModules.disko
            facter.nixosModules.facter
            {
              # generate by running nixos-anywhere with `--generate-hardware-config nixos-facter ./facter.json`
              config.facter.reportPath = ./hardware/wyse-5070.json;
            }
            ./hosts/bootstrap.nix
          ];
        };
      };
    };
}
