{ self, inputs, ... }:
{
  ieee-tamu.network-map.hosts.ieee-tamu-8J = "192.168.1.11";

  flake.ci.x86_64-linux.nixos = [ "ieee-tamu-8J" ];

  flake.nixosConfigurations.ieee-tamu-8J = inputs.nixpkgs.lib.nixosSystem {
    modules = [
      self.modules.nixos.wyse
      {
        networking.hostName = "ieee-tamu-8J";
        ieee-tamu.cluster = {
          enable = true;
          node.role = "server";
        };
      }
    ];
  };
}
