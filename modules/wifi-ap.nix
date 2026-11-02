{
  flake.modules.nixos.wifi-ap =
    {
      config,
      lib,
      ...
    }:
    let
      cfg = config.ieee-tamu.wifi-ap;
    in
    {
      options.ieee-tamu.wifi-ap = {
        enable = lib.mkEnableOption "a bridged Wi-Fi access point for IoT devices";

        interface = lib.mkOption {
          type = lib.types.str;
          default = "wlp0s12f0";
          description = "Wireless interface to run the access point on.";
        };

        bridge = lib.mkOption {
          type = lib.types.str;
          default = "br0";
          description = "Bridge interface the access point is attached to.";
        };

        uplinkInterface = lib.mkOption {
          type = lib.types.str;
          default = "enp1s0";
          description = "Wired interface added to the bridge.";
        };

        ssid = lib.mkOption {
          type = lib.types.str;
          default = "IEEE-TAMU-IoT";
          description = "SSID advertised by the access point.";
        };

        hidden = lib.mkOption {
          type = lib.types.bool;
          default = false;
          description = ''
            Whether to hide the SSID. Hiding an SSID is not a security
            control and may prevent devices with scan-only setup flows (such
            as Bambu printers) from joining.
          '';
        };

        passwordFile = lib.mkOption {
          type = lib.types.path;
          description = "File containing the WPA2 passphrase.";
        };

        band = lib.mkOption {
          type = lib.types.enum [
            "2g"
            "5g"
          ];
          default = "2g";
          description = ''
            Frequency band for the access point. Most IoT devices, including
            Bambu printers, only support 2.4 GHz.
          '';
        };

        channel = lib.mkOption {
          type = lib.types.ints.between 1 196;
          default = 6;
          description = "Channel to operate on.";
        };

        countryCode = lib.mkOption {
          type = lib.types.str;
          default = "US";
          description = "Regulatory country code.";
        };
      };

      config = lib.mkIf cfg.enable {
        assertions = [
          {
            assertion = cfg.interface != cfg.uplinkInterface;
            message = "ieee-tamu.wifi-ap: interface and uplinkInterface must differ";
          }
          {
            assertion = cfg.interface != cfg.bridge;
            message = "ieee-tamu.wifi-ap: interface and bridge must differ";
          }
        ];

        networking.bridges.${cfg.bridge}.interfaces = [ cfg.uplinkInterface ];

        # nixos-facter configures DHCP on every detected interface by default.
        # The wireless interface is owned by hostapd instead.
        networking.interfaces.${cfg.interface}.useDHCP = false;

        services.hostapd = {
          enable = true;

          radios.${cfg.interface} = {
            inherit (cfg) band channel countryCode;

            # 2.4 GHz, 20 MHz only: maximises compatibility with IoT clients.
            wifi4 = {
              enable = true;
              capabilities = [ "SHORT-GI-20" ];
            };
            wifi5.enable = false;

            networks.${cfg.interface} = {
              inherit (cfg) ssid;

              ignoreBroadcastSsid = if cfg.hidden then "empty" else "disabled";

              authentication = {
                # Plain WPA2-PSK (CCMP) is what Bambu and most other IoT
                # devices expect.
                mode = "wpa2-sha1";
                wpaPasswordFile = cfg.passwordFile;
              };

              settings = {
                # Add the AP interface to the bridge. hostapd does this
                # itself, so the wired uplink is the only bridge member
                # declared under networking.bridges.
                bridge = cfg.bridge;

                # Disable management frame protection for older clients.
                ieee80211w = 0;
                sae_require_mfp = 0;
              };
            };
          };
        };

        # hostapd's service only orders itself after the wireless device; wait
        # for the bridge so adding the interface as a bridge port succeeds.
        systemd.services.hostapd.after = [ "network.target" ];
      };
    };
}
