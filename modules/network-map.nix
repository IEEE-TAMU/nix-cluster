let
  flakeModule =
    {
      config,
      lib,
      ...
    }:
    let
      flakeCfg = config.ieee-tamu.network-map;
    in
    {
      options.ieee-tamu.network-map = {
        hosts = lib.mkOption {
          type = lib.types.attrsOf lib.types.str;
          default = { };
          description = "A map of hostnames to IP addresses for all cluster nodes.";
        };
      };

      config = {
        flake.modules.nixos.network-map =
          { config, ... }:
          let
            nixosCfg = config.ieee-tamu.network-map;
          in
          {
            options = {
              ieee-tamu.network-map = {
                enable = lib.mkEnableOption "network map for ieee-tamu cluster";
                interface = lib.mkOption {
                  type = lib.types.str;
                  default = "enp1s0";
                  description = "The interface to configure with the cluster IP address.";
                };
              };
            };

            config = lib.mkIf nixosCfg.enable {
              # populate /etc/hosts so nodes can talk to each other by name (swap name and value)
              networking.hosts = lib.mapAttrs' (
                name: ip: lib.nameValuePair ip (lib.singleton name)
              ) flakeCfg.hosts;

              # automatically configure the interface IP if this host is in the map
              networking.interfaces.${nixosCfg.interface} =
                lib.mkIf (builtins.hasAttr config.networking.hostName flakeCfg.hosts)
                  {
                    useDHCP = false;
                    ipv4.addresses = [
                      {
                        address = flakeCfg.hosts.${config.networking.hostName};
                        prefixLength = 24;
                      }
                    ];
                  };
            };
          };
      };
    };
in
{
  imports = [
    flakeModule
  ];
  flake.modules.flake.network-map = flakeModule;
}
