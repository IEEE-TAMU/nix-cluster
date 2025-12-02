{
  flake.modules.nixos.cluster-node =
    {
      config,
      lib,
      pkgs,
      ...
    }:
    let
      cfg = config.ieee-tamu.cluster;
    in
    {
      options = {
        ieee-tamu.cluster =
          let
            nodeModule = lib.types.submodule {
              options = {
                initial = lib.mkOption {
                  type = lib.types.bool;
                  default = false;
                };
                role = lib.mkOption {
                  type = lib.types.enum [
                    "server"
                    "agent"
                  ];
                };
                extraFlags = lib.mkOption {
                  type = lib.types.listOf lib.types.str;
                  default = [ ];
                };
              };
            };
          in
          {
            enable = lib.mkEnableOption "ieee-tamu cluster";
            package = lib.mkPackageOption pkgs "k3s" { };
            tokenFile = lib.mkOption {
              type = lib.types.path;
            };
            node = lib.mkOption {
              type = nodeModule;
            };
            init.ipv4.address = lib.mkOption {
              type = lib.types.str;
            };
          };
      };
      config = lib.mkIf cfg.enable {
        services.k3s = {
          enable = true;
          inherit (cfg) package tokenFile;
          inherit (cfg.node) role;
          clusterInit = cfg.node.initial;
          serverAddr = lib.mkIf (!cfg.node.initial) "https://${cfg.init.ipv4.address}:6443";
          extraFlags = cfg.node.extraFlags ++ [
            "--node-name ${config.networking.hostName}"
          ];
        };
      };
    };
}
