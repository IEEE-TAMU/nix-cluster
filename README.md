# nix-cluster
NixOS based k3s cluster deployed for IEEE-TAMU.

## Bootstrapping a wyse host
`nix run github:nix-community/nixos-anywhere -- --generate-hardware-config nixos-facter ./hardware/bootstrap.json --flake .#bootstrap --target-host nixos@<ip-address> --build-on-remote --env-password`

## Adding the bootstrapped host to the secrets file
`ssh-keyscan <ip-address> | ssh-to-age`

Add the scanned age key to the .sops.yaml file and reencrypt the secrets file with `sops updatekeys hosts/secrets.yaml`

## Deploying the cluster
`nixos-rebuild switch -s --use-remote-sudo --fast --flake .#<server-name> --target-host root@<ip-address> --build-host root@<ip-address> --verbose`

## Lounge music

`ieee-tamu-5B` runs a headless mpv player for the speakers in the officer lounge. The Discord bot talks to it through a small HTTP API:

```
/play  ->  discord bot  ->  http://192.168.1.10:8787  ->  mpv + yt-dlp  ->  speakers
```

The player starts automatically as `lounge-music-mpv.service`; the control API is `lounge-music-agent.service`. mpv runs at 100% by default (configurable with `ieee-tamu.lounge-music.maxVolume`) and the service best-effort sets common ALSA controls to 100% on startup; use the volume knob on the speakers for coarse loudness. Audio is normalized with ffmpeg's `dynaudnorm` filter by default to even out quiet and loud tracks; disable or tune it with `ieee-tamu.lounge-music.audioFilter`. The Discord bot's `/volume` and `/mute` commands control mpv's software volume. If you need to adjust the hardware mixer once, run `alsamixer` on the host.

Useful commands on the host:

```bash
systemctl status lounge-music-mpv lounge-music-agent
journalctl -u lounge-music-mpv -f
mpv --audio-device=help           # list output devices
speaker-test -c 2 -t sine -l 1    # test the default analog output
curl -s http://localhost:8787/health
```

If YouTube starts asking for sign-in or bot verification, export a Netscape-format `cookies.txt` from a logged-in browser and point `ieee-tamu.lounge-music.ytdlpCookiesFile` at a sops secret containing it.

The control API is unauthenticated unless `ieee-tamu.lounge-music.tokenFile` is set (for example to a sops-nix secret containing a bearer token). Keep port 8787 on the trusted campus LAN. To enable auth, add a sops secret on the node, point `ieee-tamu.lounge-music.tokenFile` at `config.sops.secrets.<name>.path`, and set the same value as `MUSIC_AGENT_TOKEN` in `cluster-infra/secrets/discord.yaml`.

Spotify links are not supported yet. Only YouTube, YouTube Music, SoundCloud, and Bandcamp URLs (plus free-text search) are accepted. YouTube playlists are expanded up to `ieee-tamu.lounge-music.maxQueueLength` entries (25 by default).
