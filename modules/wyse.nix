{ inputs, ... }@flake:
{
  flake.modules.nixos.wyse =
    {
      lib,
      pkgs,
      config,
      ...
    }:
    {
      imports = [
        inputs.sops-nix.nixosModules.sops
        inputs.facter.nixosModules.facter
        inputs.disko.nixosModules.disko
        inputs.self.modules.nixos.cluster-node
        inputs.self.modules.nixos.ha-vip
        inputs.self.modules.nixos.network-map
        inputs.self.modules.nixos.binary-cache
        inputs.self.modules.nixos.minimal
        ../hardware/wyse-disko.nix
      ];

      ieee-tamu.network-map.enable = true;
      ieee-tamu.network-map.interface = lib.mkDefault "enp1s0";

      facter.reportPath =
        let
          inherit (config.networking) hostName;
          hostNamePostfix = lib.removePrefix "ieee-tamu-" hostName;
        in
        lib.mkDefault ../hardware/wyse-${hostNamePostfix}.json;

      nix.settings = {
        experimental-features = [
          "nix-command"
          "flakes"
        ];
        extra-substituters = [ "https://ieeetamu.cachix.org" ];
        extra-trusted-public-keys = [
          "ieeetamu.cachix.org-1:2GZnWNg5DRoPlUGP8V2EB3YpkTtfw2wCHf4VJye4ZhI="
        ];
      };

      sops.defaultSopsFile = ../hosts/secrets.yaml;
      sops.age.sshKeyPaths = [ "/etc/ssh/ssh_host_ed25519_key" ];
      # do not try to use ssh host rsa keys
      sops.gnupg.sshKeyPaths = [ ];
      sops.secrets.root_password.neededForUsers = true;

      users.mutableUsers = false;
      users.users.root = {
        hashedPasswordFile = config.sops.secrets.root_password.path;
        openssh.authorizedKeys.keys = flake.config.meta.owner.sshKeys;
      };

      services.openssh.enable = true;
      hardware.bluetooth.enable = false;

      environment.systemPackages = with pkgs; [
        git
        vim
      ];

      nixpkgs.config.allowUnfree = true;

      networking = {
        nameservers = [ "192.168.1.1" ];
        defaultGateway = "192.168.1.1";
      };

      sops.secrets.k3s_token = { };
      ieee-tamu.cluster = {
        tokenFile = config.sops.secrets.k3s_token.path;
        node.extraFlags = lib.optionals (config.ieee-tamu.cluster.node.role == "server") [
          "--tls-san ieee-tamu.engr.tamu.edu"
          "--tls-san ${config.ieee-tamu.ha-vip.vip}"
        ];
      };
      ieee-tamu.ha-vip.enable =
        config.ieee-tamu.cluster.enable && config.ieee-tamu.cluster.node.role == "server";
      ieee-tamu.binary-cache.enable = config.ieee-tamu.cluster.enable;

      # configure the leader ip
      ieee-tamu.cluster.init.ipv4.address = flake.config.ieee-tamu.network-map.hosts.ieee-tamu-5B;

      system.stateVersion = lib.mkDefault "24.11";

      system.autoUpgrade = {
        enable = true;
        inherit (flake.config.meta) flake;
      };
    };
}
