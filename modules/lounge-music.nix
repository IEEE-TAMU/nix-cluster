{
  flake.modules.nixos.lounge-music =
    {
      config,
      lib,
      pkgs,
      ...
    }:
    let
      cfg = config.ieee-tamu.lounge-music;
      runtimeDirectory = "lounge-music";
      mpvSocket = "/run/${runtimeDirectory}/mpv.sock";
      agentScript = ./lounge-music-agent.py;
      servicePath = [
        pkgs.mpv
        pkgs.yt-dlp
        pkgs.deno
        pkgs.python3
        pkgs.alsa-utils
      ];
      ytdlRawOptions = [
        "yes-playlist="
        "playlist-end=${toString cfg.maxQueueLength}"
      ]
      ++ lib.optional (cfg.ytdlpCookiesFile != null) "cookies=${toString cfg.ytdlpCookiesFile}";
      mpvArgs = [
        "--idle=yes"
        "--no-video"
        "--audio-display=no"
        "--no-config"
        "--terminal=no"
        "--volume=100"
        "--ytdl-format=bestaudio/best"
        "--input-ipc-server=${mpvSocket}"
      ]
      ++ lib.optional (cfg.audioDevice != null) "--audio-device=${cfg.audioDevice}"
      ++ [
        "--ytdl-raw-options=${lib.concatStringsSep "," ytdlRawOptions}"
      ]
      ++ cfg.extraMpvArgs;
    in
    {
      options.ieee-tamu.lounge-music = {
        enable = lib.mkEnableOption "the lounge music player and control API";

        port = lib.mkOption {
          type = lib.types.port;
          default = 8787;
          description = "TCP port for the lounge music control API.";
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

        audioDevice = lib.mkOption {
          type = lib.types.nullOr lib.types.str;
          default = null;
          example = "alsa/hw:CARD=PCH,DEV=0";
          description = ''
            mpv audio device. Leave null to use mpv's default device.
            Run `mpv --audio-device=help` on the host to list the available devices.
          '';
        };

        tokenFile = lib.mkOption {
          type = lib.types.nullOr lib.types.path;
          default = null;
          description = ''
            File containing a bearer token required by the control API.
            When null the API is unauthenticated and should only be reachable
            from a trusted network.
          '';
        };

        ytdlpCookiesFile = lib.mkOption {
          type = lib.types.nullOr lib.types.path;
          default = null;
          description = ''
            Optional Netscape-format cookies file passed to yt-dlp. Useful when
            YouTube asks for sign-in or bot verification.
          '';
        };

        maxQueueLength = lib.mkOption {
          type = lib.types.ints.positive;
          default = 25;
          description = "Maximum number of queued tracks.";
        };

        maximizeAlsaMixer = lib.mkOption {
          type = lib.types.bool;
          default = true;
          description = ''
            Best-effort `amixer` calls that unmute and set common ALSA controls
            (Master, PCM, Headphone, Speaker) to 100% when the player starts.
            Missing controls are ignored. Use the physical speaker knob for
            loudness.
          '';
        };

        extraMpvArgs = lib.mkOption {
          type = lib.types.listOf lib.types.str;
          default = [ ];
          description = "Extra arguments appended to the mpv command line.";
        };
      };

      config = lib.mkIf cfg.enable {
        users.groups.lounge-music = { };

        users.users.lounge-music = {
          isSystemUser = true;
          group = "lounge-music";
          extraGroups = [ "audio" ];
          home = "/var/lib/lounge-music";
        };

        environment.systemPackages = with pkgs; [
          alsa-utils
          mpv
          yt-dlp
        ];

        networking.firewall.allowedTCPPorts = lib.optionals cfg.openFirewall [ cfg.port ];

        systemd.services.lounge-music-mpv = {
          description = "Lounge music player (mpv)";
          wantedBy = [ "multi-user.target" ];
          after = [
            "network.target"
            "sound.target"
          ];
          path = servicePath;
          environment = {
            HOME = "/var/lib/lounge-music";
            XDG_CACHE_HOME = "/var/cache/lounge-music";
          };
          serviceConfig = {
            Type = "simple";
            User = "lounge-music";
            Group = "lounge-music";
            SupplementaryGroups = [ "audio" ];
            RuntimeDirectory = runtimeDirectory;
            StateDirectory = runtimeDirectory;
            CacheDirectory = runtimeDirectory;
            Restart = "always";
            RestartSec = 2;
            ExecStart = "${pkgs.mpv}/bin/mpv ${lib.escapeShellArgs mpvArgs}";
            ExecStartPre = lib.optionals cfg.maximizeAlsaMixer (
              map (control: "-${pkgs.alsa-utils}/bin/amixer -q sset ${control} 100% unmute") [
                "Master"
                "PCM"
                "Headphone"
                "Speaker"
              ]
            );
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

        systemd.services.lounge-music-agent = {
          description = "Lounge music control API";
          wantedBy = [ "multi-user.target" ];
          after = [
            "lounge-music-mpv.service"
          ]
          ++ lib.optional (cfg.tokenFile != null) "sops-install-secrets.service";
          requires = [ "lounge-music-mpv.service" ];
          path = servicePath;
          environment = {
            BIND_ADDRESS = cfg.bindAddress;
            MAX_QUEUE_LENGTH = toString cfg.maxQueueLength;
            MPV_SOCKET = mpvSocket;
            PORT = toString cfg.port;
          }
          // lib.optionalAttrs (cfg.tokenFile != null) {
            TOKEN_FILE = toString cfg.tokenFile;
          };
          serviceConfig = {
            Type = "simple";
            User = "lounge-music";
            Group = "lounge-music";
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
