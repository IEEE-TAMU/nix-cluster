{
  flake.modules.nixos.bambu-printer =
    {
      config,
      lib,
      pkgs,
      ...
    }:
    let
      cfg = config.ieee-tamu.bambu-printer;
      agentScript = ./bambu-printer-agent.py;
      python = pkgs.python3.withPackages (ps: [ ps.paho-mqtt ]);
      servicePath = [
        python
        pkgs.ffmpeg
      ];
    in
    {
      options.ieee-tamu.bambu-printer = {
        enable = lib.mkEnableOption "the Bambu Lab X2D control API";

        ip = lib.mkOption {
          type = lib.types.str;
          default = "192.168.1.66";
          description = "IPv4 address of the X2D on the lounge LAN.";
        };

        serial = lib.mkOption {
          type = lib.types.str;
          default = "20P5AJ662701075";
          description = "Printer serial number; also used in the MQTT topic.";
        };

        accessCodeFile = lib.mkOption {
          type = lib.types.path;
          description = ''
            File containing the printer's LAN access code. Point this at a
            sops-nix secret, for example
            `config.sops.secrets.bambu_x2d_access_code.path`.
          '';
        };

        port = lib.mkOption {
          type = lib.types.port;
          default = 8789;
          description = "TCP port for the 3D printer control API.";
        };

        bindAddress = lib.mkOption {
          type = lib.types.str;
          default = "0.0.0.0";
          description = "Address the control API binds to.";
        };

        openFirewall = lib.mkOption {
          type = lib.types.bool;
          default = true;
          description = "Open the control API port in the host firewall.";
        };

        tokenFile = lib.mkOption {
          type = lib.types.nullOr lib.types.path;
          default = null;
          description = ''
            File containing a bearer token required by the control API. When
            null the API is unauthenticated and should only be reachable from
            the trusted campus LAN.
          '';
        };

        maxFileBytes = lib.mkOption {
          type = lib.types.ints.positive;
          default = 100 * 1024 * 1024;
          description = "Maximum .gcode.3mf upload size accepted by the API.";
        };

        cameraTimeout = lib.mkOption {
          type = lib.types.ints.positive;
          default = 30;
          description = "Seconds to wait for one RTSPS camera frame.";
        };

        mqttRefreshSeconds = lib.mkOption {
          type = lib.types.ints.positive;
          default = 30;
          description = "How often to request a full printer status snapshot.";
        };

        printUrlScheme = lib.mkOption {
          type = lib.types.str;
          default = "ftp:///";
          description = ''
            URL prefix sent to the printer for uploaded files. X2D/P2S firmware
            expects `ftp:///`; older X1/P1 firmware uses `file:///sdcard/`.
          '';
        };
      };

      config = lib.mkIf cfg.enable {
        users.groups.bambu-printer = { };

        users.users.bambu-printer = {
          isSystemUser = true;
          group = "bambu-printer";
          home = "/var/lib/bambu-printer";
        };

        networking.firewall.allowedTCPPorts =
          lib.optionals cfg.openFirewall [ cfg.port ];

        systemd.services.bambu-printer-agent = {
          description = "Bambu Lab X2D control API";
          wantedBy = [ "multi-user.target" ];
          after = [
            "network-online.target"
          ]
          ++ lib.optional (cfg.tokenFile != null) "sops-install-secrets.service";
          wants = [ "network-online.target" ];
          path = servicePath;
          environment = {
            ACCESS_CODE_FILE = toString cfg.accessCodeFile;
            BIND_ADDRESS = cfg.bindAddress;
            CAMERA_TIMEOUT = toString cfg.cameraTimeout;
            HOME = "/var/lib/bambu-printer";
            MAX_FILE_BYTES = toString cfg.maxFileBytes;
            MQTT_REFRESH_SECONDS = toString cfg.mqttRefreshSeconds;
            PORT = toString cfg.port;
            PRINT_URL_SCHEME = cfg.printUrlScheme;
            PRINTER_IP = cfg.ip;
            PRINTER_SERIAL = cfg.serial;
            PYTHONDONTWRITEBYTECODE = "1";
          }
          // lib.optionalAttrs (cfg.tokenFile != null) {
            TOKEN_FILE = toString cfg.tokenFile;
          };
          serviceConfig = {
            Type = "simple";
            User = "bambu-printer";
            Group = "bambu-printer";
            StateDirectory = "bambu-printer";
            Restart = "always";
            RestartSec = 2;
            ExecStart = "${python}/bin/python3 ${agentScript}";
            NoNewPrivileges = true;
            PrivateTmp = true;
            ProtectSystem = "strict";
            ProtectHome = true;
            RestrictAddressFamilies = [
              "AF_UNIX"
              "AF_INET"
              "AF_INET6"
              "AF_NETLINK"
            ];
          };
        };
      };
    };
}
