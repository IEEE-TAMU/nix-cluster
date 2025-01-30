{
  name = "ieee-tamu";
  extraServerFlags = [
    "--tls-san 10.125.185.49"
  ];
  nodes = let
    networkingConfig = {
      networking = {
        defaultGateway = "192.168.1.1";
        nameservers = ["192.168.1.1"];
      };
    };

    wyzeHardware = {
      interface = "enp1s0";
      disko-config = ./hardware/wyse-disko.nix;
    };

    facter-config = node: {
      facter.reportPath = ./. + "./hardware/wyse-${node}.json";
    };

    nodeConfigs = [
      {
        name = "ieee-tamu-5B";
        initial = true;
        role = "server";
        ipv4 = "192.168.1.10/24";
      }
      {
        name = "ieee-tamu-8J";
        role = "server";
        ipv4 = "192.168.1.11/24";
      }
      {
        name = "ieee-tamu-6Q";
        role = "server";
        ipv4 = "192.168.1.12/24";
      }
      {
        name = "ieee-tamu-7P";
        role = "agent";
        ipv4 = "192.168.1.13/24";
      }
    ];
  in
    map (node: wyzeHardware // networkingConfig // node) nodeConfigs;
}
