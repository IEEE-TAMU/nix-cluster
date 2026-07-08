{ self, inputs, ... }:
{
  ieee-tamu.network-map.hosts.ieee-tamu-7P = "192.168.1.13";

  flake.ci.x86_64-linux.nixos = [ "ieee-tamu-7P" ];

  flake.nixosConfigurations.ieee-tamu-7P = inputs.nixpkgs.lib.nixosSystem {
    modules = [
      self.modules.nixos.wyse
      (
        { pkgs, ... }:
        {
          networking.hostName = "ieee-tamu-7P";

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
        }
      )
    ];
  };
}
