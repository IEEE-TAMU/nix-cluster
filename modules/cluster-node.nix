{
  config,
  lib,
  pkgs,
  ...
}: let
  cfg = config.ieee-tamu.cluster;
in {
  options = {
    ieee-tamu.cluster = let
      nodeModule = lib.types.submodule {
        options = {
          facter-config = lib.mkOption {
            type = lib.types.path;
          };
          disko-config = lib.mkOption {
            type = lib.types.path;
          };
          initial = lib.mkOption {
            type = lib.types.bool;
            default = false;
          };
          hostName = lib.mkOption {
            type = lib.types.str;
          };
          nameservers = lib.mkOption {
            type = lib.types.listOf lib.types.str;
          };
          interface = lib.mkOption {
            type = lib.types.str;
          };
          ipv4.addresses = lib.mkOption {
            type = lib.types.listOf (lib.types.submodule {
              options = {
                address = lib.mkOption {
                  type = lib.types.str;
                };
                prefixLength = lib.mkOption {
                  type = lib.types.int;
                };
              };
            });
          };
          defaultGateway = lib.mkOption {
            type = lib.types.str;
          };
          allowedTCPPorts = lib.mkOption {
            type = lib.types.listOf lib.types.port;
          };
          allowedUDPPorts = lib.mkOption {
            type = lib.types.listOf lib.types.port;
          };
          role = lib.mkOption {
            type = lib.types.enum ["server" "agent"];
          };
          extraFlags = lib.mkOption {
            type = lib.types.listOf lib.types.str;
            default = [];
          };
        };
      };
    in {
      enable = lib.mkEnableOption "ieee-tamu cluster";
      package = lib.mkPackageOption pkgs "k3s" {};
      tokenFile = lib.mkOption {
        type = lib.types.path;
      };
      node = lib.mkOption {
        type = nodeModule;
      };
      init = lib.mkOption {
        type = nodeModule;
      };
    };
  };
  config = lib.mkIf cfg.enable {
    assertions = [];

    facter.reportPath = cfg.node.facter-config;

    networking = {
      inherit (cfg.node) hostName nameservers;
      interfaces.${cfg.node.interface} = {
        inherit (cfg.node) ipv4;
      };
      defaultGateway = {
        inherit (cfg.node) interface;
        address = cfg.node.defaultGateway;
      };
      firewall = {
        inherit (cfg.node) allowedTCPPorts allowedUDPPorts;
      };
    };

    services.k3s = {
      enable = true;
      inherit (cfg) package tokenFile;
      inherit (cfg.node) role;
      clusterInit = cfg.node.initial;
      serverAddr = lib.mkIf (!cfg.node.initial) "https://${(builtins.elemAt cfg.init.ipv4.addresses 0).address}:6443";
      extraFlags =
        cfg.node.extraFlags
        ++ [
          "--node-name ${cfg.node.hostName}"
        ];
    };
  };
}
