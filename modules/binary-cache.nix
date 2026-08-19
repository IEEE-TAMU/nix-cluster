let
  flakeModule =
    {
      config,
      lib,
      ...
    }:
    let
      clusterHosts = config.ieee-tamu.network-map.hosts;
    in
    {
      config.flake.modules.nixos.binary-cache =
        {
          config,
          lib,
          pkgs,
          ...
        }:
        let
          cfg = config.ieee-tamu.binary-cache;
          self = config.networking.hostName;
          peerUrls = map (ip: "http://${ip}:${toString cfg.port}?trusted=true") (
            lib.attrValues (lib.filterAttrs (name: _: name != self) clusterHosts)
          );
        in
        {
          options.ieee-tamu.binary-cache = {
            enable = lib.mkEnableOption "peer-to-peer nix binary cache";
            port = lib.mkOption {
              type = lib.types.port;
              default = 5000;
              description = "Port the nix-serve cache listens on.";
            };
            secretKeyFile = lib.mkOption {
              type = lib.types.nullOr lib.types.path;
              default = null;
              description = "Path to a binary cache signing key, if any.";
            };
          };

          config = lib.mkIf cfg.enable {
            services.nix-serve = {
              enable = true;
              inherit (cfg) port secretKeyFile;
              openFirewall = true;
            };

            nix.settings = {
              substituters = lib.mkBefore peerUrls;
            };
          };
        };
    };
in
{
  imports = [
    flakeModule
  ];
  flake.modules.flake.binary-cache = flakeModule;
}
