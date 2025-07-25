{ config, ... }:
{
  imports = [
    ./global.nix
    ../hardware/wyse-disko.nix
  ];

  ieee-tamu.cluster = {
    enable = true;
    tokenFile = config.sops.secrets.k3s_token.path;
    node = {
      facter-config = ../hardware/wyse-8J.json;
      hostName = "ieee-tamu-8J";
      nameservers = [ "192.168.1.1" ];
      interface = "enp1s0";
      ipv4.addresses = [
        {
          address = "192.168.1.11";
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
      role = "server";
      extraFlags = [
        "--tls-san ieee-tamu.engr.tamu.edu"
      ];
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

  system.stateVersion = "24.11";
}
