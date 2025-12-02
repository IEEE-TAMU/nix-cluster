{ self, inputs, ... }:
{
  flake.nixosConfigurations.ieee-tamu-5B = inputs.nixpkgs.lib.nixosSystem {
    modules = [
      self.modules.nixos.default
      ../hardware/wyse-disko.nix
      {
        facter.reportPath = ../hardware/wyse-5B.json;

        networking = {
          hostName = "ieee-tamu-5B";
          interfaces.enp1s0 = {
            useDHCP = false;
            ipv4.addresses = [
              {
                address = "192.168.1.10";
                prefixLength = 24;
              }
            ];
          };
          firewall = {
            allowedTCPPorts = [
              6443 # k3s API server
              2379 # etcd server client API
              2380 # etcd server peer API
              10250 # kubelet metrics
            ];
            allowedUDPPorts = [
              8472 # flannel VXLAN
            ];
          };
        };

        ieee-tamu.cluster = {
          enable = true;
          node = {
            role = "server";
            initial = true;
          };
        };

        system.stateVersion = "24.11";
      }
    ];
  };
}
