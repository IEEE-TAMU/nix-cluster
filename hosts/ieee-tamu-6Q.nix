{ self, inputs, ... }:
{
  flake.nixosConfigurations.ieee-tamu-6Q = inputs.nixpkgs.lib.nixosSystem {
    modules = [
      self.modules.nixos.default
      ../hardware/wyse-disko.nix
      {
        facter.reportPath = ../hardware/wyse-6Q.json;

        ieee-tamu.network-map.enable = true;

        networking = {
          hostName = "ieee-tamu-6Q";
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
          node.role = "server";
        };

        system.stateVersion = "24.11";
      }

    ];
  };
}
