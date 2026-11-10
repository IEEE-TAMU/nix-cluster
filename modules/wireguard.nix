{
  flake.modules.nixos.wireguard =
    {
      config,
      lib,
      ...
    }:
    let
      cfg = config.ieee-tamu.wireguard;

      peerType = lib.types.submodule {
        options = {
          publicKey = lib.mkOption {
            type = lib.types.str;
            description = "WireGuard public key of the client.";
          };
          allowedIPs = lib.mkOption {
            type = lib.types.listOf lib.types.str;
            default = [ ];
            description = "Tunnel IPs assigned to this client.";
          };
          presharedKeyFile = lib.mkOption {
            type = lib.types.nullOr lib.types.path;
            default = null;
            description = "Optional preshared key file for this client.";
          };
        };
      };
    in
    {
      options.ieee-tamu.wireguard = {
        enable = lib.mkEnableOption "WireGuard road-warrior VPN server";

        interface = lib.mkOption {
          type = lib.types.str;
          default = "wg0";
          description = "WireGuard interface name.";
        };

        address = lib.mkOption {
          type = lib.types.str;
          default = "10.99.0.1/24";
          description = "WireGuard address assigned to the server.";
        };

        listenPort = lib.mkOption {
          type = lib.types.port;
          default = 51820;
          description = "UDP port WireGuard listens on.";
        };

        lanInterface = lib.mkOption {
          type = lib.types.str;
          default = "enp1s0";
          description = "LAN interface used as the NAT egress interface.";
        };

        natSubnet = lib.mkOption {
          type = lib.types.str;
          default = "10.99.0.0/24";
          description = "Tunnel subnet to source-NAT onto the LAN.";
        };

        privateKeyFile = lib.mkOption {
          type = lib.types.path;
          description = "Path to the server WireGuard private key.";
        };

        peers = lib.mkOption {
          type = lib.types.listOf peerType;
          default = [ ];
          description = "WireGuard clients allowed to use this server.";
        };
      };

      config = lib.mkIf cfg.enable {
        networking.wireguard.interfaces.${cfg.interface} = {
          ips = [ cfg.address ];
          listenPort = cfg.listenPort;
          privateKeyFile = cfg.privateKeyFile;
          peers = map (
            peer:
            {
              inherit (peer) publicKey allowedIPs;
            }
            // lib.optionalAttrs (peer.presharedKeyFile != null) {
              inherit (peer) presharedKeyFile;
            }
          ) cfg.peers;
        };

        # Source-NAT tunnel traffic onto the LAN so the other cluster hosts
        # do not need a route back to the WireGuard subnet.
        networking.nat = {
          enable = true;
          internalIPs = [ cfg.natSubnet ];
          externalInterface = cfg.lanInterface;
        };

        networking.firewall = {
          allowedUDPPorts = [ cfg.listenPort ];
          trustedInterfaces = [ cfg.interface ];
          checkReversePath = "loose";
        };

        boot.kernel.sysctl."net.ipv4.ip_forward" = lib.mkDefault 1;
      };
    };
}
