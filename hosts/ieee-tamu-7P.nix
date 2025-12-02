{ self, inputs, ... }:
{
  flake.nixosConfigurations.ieee-tamu-7P = inputs.nixpkgs.lib.nixosSystem {
    modules = [
      self.modules.nixos.default
      ../hardware/wyse-disko.nix
      (
        { pkgs, ... }:
        {
          facter.reportPath = ../hardware/wyse-7P.json;

          networking = {
            hostName = "ieee-tamu-7P";
            interfaces.enp1s0 = {
              useDHCP = false;
              ipv4.addresses = [
                {
                  address = "192.168.1.13";
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
            node.role = "agent";
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
      )
    ];
  };
}
