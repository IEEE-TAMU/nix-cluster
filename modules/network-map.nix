{
  flake.modules.nixos.network-map =
    {
      config,
      lib,
      ...
    }:
    let
      cfg = config.ieee-tamu.network-map;

      # The central source of truth for our cluster IPs
      hosts = {
        "ieee-tamu-5B" = "192.168.1.10";
        "ieee-tamu-8J" = "192.168.1.11";
        "ieee-tamu-6Q" = "192.168.1.12";
        "ieee-tamu-7P" = "192.168.1.13";
      };

    in
    {
      options.ieee-tamu.network-map = {
        enable = lib.mkEnableOption "cluster network map";
      };

      config = lib.mkIf cfg.enable {
        # populate /etc/hosts so nodes can talk to each other by name
        networking.extraHosts = lib.concatStringsSep "\n" (
          lib.mapAttrsToList (name: ip: "${ip} ${name}") hosts
        );

        # automatically configure the interface IP if this host is in the map
        networking.interfaces.enp1s0 = lib.mkIf (builtins.hasAttr config.networking.hostName hosts) {
          useDHCP = false;
          ipv4.addresses = [
            {
              address = hosts.${config.networking.hostName};
              prefixLength = 24;
            }
          ];
        };
      };
    };
}
