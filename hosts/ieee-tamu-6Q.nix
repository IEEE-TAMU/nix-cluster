{ self, inputs, ... }:
{
  ieee-tamu.network-map.hosts.ieee-tamu-6Q = "192.168.1.12";

  flake.ci.x86_64-linux.nixos = [ "ieee-tamu-6Q" ];

  flake.nixosConfigurations.ieee-tamu-6Q = inputs.nixpkgs.lib.nixosSystem {
    modules = [
      self.modules.nixos.wyse
      {
        networking.hostName = "ieee-tamu-6Q";
        ieee-tamu.cluster = {
          enable = true;
          node.role = "server";
        };
      }
    ];
  };
}
