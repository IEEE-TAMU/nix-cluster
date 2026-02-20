{ self, inputs, ... }:
{
  ieee-tamu.network-map.hosts.ieee-tamu-6Q = "192.168.1.12";

  flake.nixosConfigurations.ieee-tamu-6Q = inputs.nixpkgs.lib.nixosSystem {
    modules = [
      self.modules.nixos.wyse
      {
        networking = {
          hostName = "ieee-tamu-6Q";
          firewall = {
            allowedTCPPorts = [
              6443 # k3s API server
              2379 # etcd server client API
              2380 # etcd server peer API
              10250 # kubelet metrics
              9100 # node-exporter metrics
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
      }
    ];
  };
}
