{ inputs, config, ... }@flake:
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

      nix.settings.experimental-features = [
        "nix-command"
        "flakes"
      ];

      sops.defaultSopsFile = ../hosts/secrets.yaml;
      sops.age.sshKeyPaths = [ "/etc/ssh/ssh_host_ed25519_key" ];
      # do not try to use ssh host rsa keys
      sops.gnupg.sshKeyPaths = [ ];
      sops.secrets.root_password.neededForUsers = true;

      users.mutableUsers = false;
      users.users.root = {
        hashedPasswordFile = config.sops.secrets.root_password.path;
        openssh.authorizedKeys.keys = [
          "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIO2FLDIautZl87H9xJKsPJsO0gO/8t4jOS3Szz4j2qY4 IEEE@IEEEPC"
          "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOevicH4lyiFYuIcUPKSvu3+zjY67wzLkkCCN3Er7Hff caleb@chnorton-fw"
        ];
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
        node = {
          # FIXME: only read role if enabled?
          extraFlags = lib.optionals (config.ieee-tamu.cluster.node.role == "server") [
            "--tls-san ieee-tamu.engr.tamu.edu"
            "--tls-san ${config.ieee-tamu.ha-vip.vip}"
          ];
        };
      };
      # FIXME: only check role if cluster is enabled
      ieee-tamu.ha-vip.enable = config.ieee-tamu.cluster.node.role == "server";

      # configure the leader ip
      ieee-tamu.cluster.init.ipv4.address = flake.config.ieee-tamu.network-map.hosts.ieee-tamu-5B;

      system.stateVersion = lib.mkDefault "24.11";
    };
}
