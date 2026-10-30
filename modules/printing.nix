{
  flake.modules.nixos.printing =
    {
      config,
      lib,
      pkgs,
      ...
    }:
    let
      cfg = config.ieee-tamu.printing;
      agentScript = ./print-agent.py;
      servicePath = [
        pkgs.cups
        pkgs.coreutils
        pkgs.python3
      ];
    in
    {
      options.ieee-tamu.printing = {
        enable = lib.mkEnableOption "the CUPS printer and print control API";

        printerName = lib.mkOption {
          type = lib.types.str;
          default = "Brother_MFC-7360N";
          description = "CUPS queue name.";
        };

        deviceUri = lib.mkOption {
          type = lib.types.str;
          default = "lpd://192.168.1.38/binary_p1";
          description = "CUPS device URI for the printer.";
        };

        model = lib.mkOption {
          type = lib.types.str;
          default = "brother-BrGenML1-cups-en.ppd";
          description = "PPD model name passed to lpadmin.";
        };

        location = lib.mkOption {
          type = lib.types.str;
          default = "IEEE Lounge";
          description = "Human-readable printer location.";
        };

        ppdOptions = lib.mkOption {
          type = lib.types.attrsOf lib.types.str;
          default = {
            PageSize = "Letter";
          };
          description = "Default PPD options for the printer.";
        };

        drivers = lib.mkOption {
          type = lib.types.listOf lib.types.package;
          default = [ pkgs.brgenml1cupswrapper ];
          description = "CUPS driver packages for the printer.";
        };

        port = lib.mkOption {
          type = lib.types.port;
          default = 8788;
          description = "TCP port for the print control API.";
        };

        bindAddress = lib.mkOption {
          type = lib.types.str;
          default = "0.0.0.0";
          description = "Address the print control API binds to.";
        };

        openFirewall = lib.mkOption {
          type = lib.types.bool;
          default = true;
          description = "Open the print control API port in the host firewall.";
        };

        tokenFile = lib.mkOption {
          type = lib.types.nullOr lib.types.path;
          default = null;
          description = ''
            File containing a bearer token required by the print control API.
            When null the API is unauthenticated and should only be reachable
            from a trusted network.
          '';
        };

        maxFileBytes = lib.mkOption {
          type = lib.types.ints.positive;
          default = 10 * 1024 * 1024;
          description = "Maximum upload size accepted by the print control API.";
        };

        maxCopies = lib.mkOption {
          type = lib.types.ints.positive;
          default = 20;
          description = "Maximum number of copies accepted per request.";
        };

        defaultLpOptions = lib.mkOption {
          type = lib.types.listOf lib.types.str;
          default = [ "media=Letter" ];
          description = "Default `lp -o` options applied to every job.";
        };
      };

      config = lib.mkIf cfg.enable {
        services.printing = {
          enable = true;
          inherit (cfg) drivers;
        };

        # CUPS D-Bus calls to fprintd cause 30 second hangs in the web UI.
        security.pam.services.cups.fprintAuth = false;

        hardware.printers = {
          ensureDefaultPrinter = cfg.printerName;
          ensurePrinters = [
            {
              name = cfg.printerName;
              inherit (cfg)
                deviceUri
                location
                model
                ppdOptions
                ;
            }
          ];
        };

        networking.firewall.allowedTCPPorts = lib.optionals cfg.openFirewall [ cfg.port ];

        users.groups.print-agent = { };

        users.users.print-agent = {
          isSystemUser = true;
          group = "print-agent";
          extraGroups = [ "lp" ];
          home = "/var/lib/print-agent";
        };

        systemd.services.print-agent = {
          description = "Printer control API";
          wantedBy = [ "multi-user.target" ];
          after = [ "cups.service" ] ++ lib.optional (cfg.tokenFile != null) "sops-install-secrets.service";
          requires = [ "cups.service" ];
          path = servicePath;
          environment = {
            BIND_ADDRESS = cfg.bindAddress;
            DEFAULT_LP_OPTIONS = lib.concatStringsSep "," cfg.defaultLpOptions;
            HOME = "/var/lib/print-agent";
            MAX_COPIES = toString cfg.maxCopies;
            MAX_FILE_BYTES = toString cfg.maxFileBytes;
            PORT = toString cfg.port;
            PRINTER_NAME = cfg.printerName;
          }
          // lib.optionalAttrs (cfg.tokenFile != null) {
            TOKEN_FILE = toString cfg.tokenFile;
          };
          serviceConfig = {
            Type = "simple";
            User = "print-agent";
            Group = "print-agent";
            SupplementaryGroups = [ "lp" ];
            StateDirectory = "print-agent";
            RuntimeDirectory = "print-agent";
            Restart = "always";
            RestartSec = 2;
            ExecStart = "${pkgs.python3}/bin/python3 ${agentScript}";
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
