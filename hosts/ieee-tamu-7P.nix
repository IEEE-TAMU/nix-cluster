{ pkgs, config, ... }:
{
  imports = [
    ./global.nix
    ../hardware/wyse-disko.nix
  ];

  ieee-tamu.cluster = {
    enable = true;
    tokenFile = config.sops.secrets.k3s_token.path;
    node = {
      facter-config = ../hardware/wyse-7P.json;
      hostName = "ieee-tamu-7P";
      nameservers = [ "192.168.1.1" ];
      interface = "enp1s0";
      ipv4.addresses = [
        {
          address = "192.168.1.13";
          prefixLength = 24;
        }
      ];
      defaultGateway = "192.168.1.1";
      allowedTCPPorts = [
        6443 # k3s API server
        2379 # etcd server client API
        2380 # etcd server peer API
        10250 # kubelet metrics
      ];
      allowedUDPPorts = [
        8472 # flannel VXLAN
      ];
      role = "agent";
    };
    init = {
      ipv4.addresses = [
        {
          address = "192.168.1.10";
          prefixLength = 24;
        }
      ];
    };
  };

  services.minecraft-server = {
    enable = true;
    package = pkgs.minecraftServers.vanilla-1-21;
    eula = true;
    openFirewall = true;
    jvmOpts = "-Xms4092M -Xmx4092M";
  };

  system.stateVersion = "24.11";
}
