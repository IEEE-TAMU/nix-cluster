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
    systems,
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
      systems = import systems;
      flake = let
        nixosModules = import ./modules;
        commonModules =
          builtins.attrValues nixosModules
          ++ [
            disko.nixosModules.disko
            facter.nixosModules.facter
          ];
      in {
        inherit nixosModules;

        # configuration for getting nixos up and running on a new machine
        # generates host ssh keys, checks hardware configuration, etc
        # before commissioning into the cluster
        nixosConfigurations.bootstrap = nixpkgs.lib.nixosSystem {
          system = "x86_64-linux";
          modules = commonModules ++ [./hosts/bootstrap.nix];
        };
      };
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
          typos.settings.exclude = "hardware";
        };

        # install the shellHook and packages from git-hooks
        # as well as helpful tools for managing the cluster
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
    };
}
