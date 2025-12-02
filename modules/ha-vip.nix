{
  flake.modules.nixos.ha-vip =
    {
      config,
      lib,
      pkgs,
      ...
    }:
    let
      cfg = config.ieee-tamu.ha-vip;
    in
    {
      options.ieee-tamu.ha-vip = {
        enable = lib.mkEnableOption "High Availability VIP";
        vip = lib.mkOption {
          type = lib.types.str;
          default = "192.168.1.9";
          description = "The virtual IP address to float between nodes.";
        };
        interface = lib.mkOption {
          type = lib.types.str;
          default = "enp1s0";
          description = "The interface to bind the VIP to.";
        };
      };

      config = lib.mkIf cfg.enable {
        services.keepalived = {
          enable = true;
          # issues with firewall so configured manually below
          openFirewall = false;
          vrrpScripts.check_k3s = {
            script = "${lib.getExe pkgs.netcat} -z 127.0.0.1 6443";
            interval = 2;
            weight = -20;
            fall = 2;
            rise = 2;
          };
          vrrpInstances.k3s_api = {
            interface = cfg.interface;
            state = "BACKUP";
            virtualRouterId = 51;
            virtualIps = [
              {
                addr = "${cfg.vip}/24";
              }
            ];
            trackScripts = [ "check_k3s" ];
            # auth not needed for local network

            # extraConfig = ''
            #   authentication {
            #     auth_type PASS
            #     auth_pass ieee-tamu-cluster
            #   }
            # '';
            # Set priority based on IP address (last octet) to ensure unique priorities
            priority =
              let
                # Get the first IPv4 address from the configured interface
                addr = builtins.head config.networking.interfaces.${cfg.interface}.ipv4.addresses;
                lastOctet = lib.last (lib.splitString "." addr.address);
              in
              lib.toInt lastOctet;
          };
        };

        # Allow VRRP protocol for Keepalived (IPv4 only)
        networking.firewall.extraCommands = ''
          iptables -A nixos-fw -p vrrp -j ACCEPT
        '';
      };
    };
}
