{ inputs, ... }:
{
  flake.modules.nixos.default =
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
      ];

      nix.settings.experimental-features = [
        "nix-command"
        "flakes"
      ];

      sops.defaultSopsFile = ./secrets.yaml;
      sops.age.sshKeyPaths = [ "/etc/ssh/ssh_host_ed25519_key" ];
      sops.secrets.k3s_token = { };
      sops.secrets.root_password.neededForUsers = true;

      users.mutableUsers = false;
      users.users.root = {
        hashedPasswordFile = config.sops.secrets.root_password.path;
        openssh.authorizedKeys.keys = [
          "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIO2FLDIautZl87H9xJKsPJsO0gO/8t4jOS3Szz4j2qY4 IEEE@IEEEPC"
          "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB3tGxUsgEJN/dwJ+QovVJd0yNg+YkJercIjGVJD+rvt caleb@chnorton-fw"
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

      ieee-tamu.cluster = {
        tokenFile = config.sops.secrets.k3s_token.path;
        node = {
          # FIXME: only read role if enabled?
           extraFlags =
             lib.optionals (config.ieee-tamu.cluster.node.role == "server") [
               "--tls-san ieee-tamu.engr.tamu.edu"
              "--tls-san ${config.ieee-tamu.ha-vip.vip}"
             ];
        };
        init.ipv4.address = "192.168.1.10";
      };
      # FIXME: only check role if cluster is enabled
      ieee-tamu.ha-vip.enable = config.ieee-tamu.cluster.node.role == "server";
    };
}
