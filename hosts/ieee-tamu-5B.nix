{ self, inputs, ... }:
{
  ieee-tamu.network-map.hosts.ieee-tamu-5B = "192.168.1.10";

  flake.ci.x86_64-linux.nixos = [ "ieee-tamu-5B" ];

  flake.nixosConfigurations.ieee-tamu-5B = inputs.nixpkgs.lib.nixosSystem {
    modules = [
      self.modules.nixos.wyse
      self.modules.nixos.printing
      {
        networking.hostName = "ieee-tamu-5B";
        ieee-tamu.cluster = {
          enable = true;
          node = {
            role = "server";
            initial = true;
          };
        };

        # Speakers in the officer lounge are wired to this node.
        ieee-tamu.lounge-music = {
          enable = true;
          # If mpv picks HDMI instead of the analog jack, set this to the
          # analog output reported by `mpv --audio-device=help`, e.g.:
          # audioDevice = "alsa/hw:CARD=PCH,DEV=0";
        };

        # Brother MFC-7360N in the officer lounge is on the cluster LAN.
        ieee-tamu.printing = {
          enable = true;
          deviceUri = "lpd://192.168.1.38/binary_p1";
        };
      }
    ];
  };
}
