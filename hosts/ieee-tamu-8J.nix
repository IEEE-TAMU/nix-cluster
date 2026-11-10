{ self, inputs, ... }:
{
  ieee-tamu.network-map.hosts.ieee-tamu-8J = "192.168.1.11";

  flake.ci.x86_64-linux.nixos = [ "ieee-tamu-8J" ];

  flake.nixosConfigurations.ieee-tamu-8J = inputs.nixpkgs.lib.nixosSystem {
    modules = [
      self.modules.nixos.wyse
      self.modules.nixos.wireguard
      (
        { config, lib, ... }:
        {
          networking.hostName = "ieee-tamu-8J";
          ieee-tamu.cluster = {
            enable = true;
            node.role = "server";
          };

          sops.secrets.wg_server_private_key = { };
          sops.secrets.wg_preshared_key = { };

          ieee-tamu.wireguard = {
            enable = true;
            lanInterface = "enp1s0";
            privateKeyFile = config.sops.secrets.wg_server_private_key.path;
            peers = lib.mapAttrsToList (_name: peer: {
              publicKey = peer.publicKey;
              allowedIPs = [ peer.address ];
              presharedKeyFile = config.sops.secrets.wg_preshared_key.path;
            }) (builtins.fromJSON (builtins.readFile ./wireguard-peers.json));
          };
        }
      )
    ];
  };
}
