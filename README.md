# nix-cluster
NixOS based k3s cluster deployed for IEEE-TAMU.

## Bootstrapping a wyse host
`nix run github:nix-community/nixos-anywhere -- --generate-hardware-config nixos-facter ./hardware/bootstrap.json --flake .#bootstrap --target-host nixos@<ip-address> --build-on-remote --env-password`

## Adding the bootstrapped host to the secrets file
`ssh-keyscan <ip-address> | ssh-to-age`

Add the scanned age key to the .sops.yaml file and reencrypt the secrets file with `sops updatekeys hosts/secrets.yaml`

## Deploying the cluster
`nixos-rebuild switch -s --use-remote-sudo --fast --flake .#<server-name> --target-host root@<ip-address> --build-host root@<ip-address> --verbose`

## Remote access (WireGuard)

`ieee-tamu-8J` runs a WireGuard server for access to the `192.168.1.0/24` LAN from the campus network. It is intentionally not exposed to the public internet: the router only needs to forward WAN UDP `51820` to `192.168.1.11`.

The server key and a shared preshared key live in `hosts/secrets.yaml` as `wg_server_private_key` and `wg_preshared_key`. Clients do not need accounts; each device gets a WireGuard keypair and a tunnel address. Peers are tracked in `hosts/wireguard-peers.json`; the `ieee-tamu-8J` configuration turns that file into WireGuard peers.

Generate a new client config with:

```bash
./scripts/wireguard-client.sh add caleb-phone
```

This generates the keypair, allocates the next `10.99.0.x` address, updates `hosts/wireguard-peers.json`, and writes `wireguard-clients/caleb-phone.conf` (mode `0600`, ignored by git). It then prints the commit/push/deploy commands. The server must be redeployed before the new config can connect.

Other commands:

```bash
./scripts/wireguard-client.sh list
./scripts/wireguard-client.sh revoke caleb-phone
```

`revoke` removes the peer from `hosts/wireguard-peers.json`; redeploy `ieee-tamu-8J` to apply it. The script needs `sops` access to the server key and preshared key, so run it from a machine that can decrypt `hosts/secrets.yaml`.

A generated client config looks like this (keep it at `/etc/wireguard/ieee-tamu.conf`, mode `0600`):

```ini
[Interface]
PrivateKey = <client private key>
Address = 10.99.0.2/32

[Peer]
PublicKey = <server public key>
PresharedKey = <preshared key>
Endpoint = 10.125.185.49:51820
AllowedIPs = 192.168.1.0/24, 10.99.0.0/24
PersistentKeepalive = 25
```

Then `wg-quick up ieee-tamu`. Only the IEEE LAN is routed through the tunnel; other traffic stays on the campus network. The server source-NATs tunnel traffic onto the LAN, so no routes need to be added to the other cluster hosts, and the router at `192.168.1.1` is reachable too.

The endpoint uses the router IP directly to avoid depending on DNS. If campus Wi-Fi blocks UDP `51820`, change `ieee-tamu.wireguard.listenPort` and the router forward to UDP `443`.

## Lounge music

`ieee-tamu-5B` runs a headless mpv player for the speakers in the officer lounge. The Discord bot and lounge web app talk to it through a small HTTP API:

```
/play  ->  discord bot or lounge web app  ->  http://192.168.1.10:8787  ->  mpv + yt-dlp  ->  speakers
                                                     \->  Spotify Web API + librespot -> mpv
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

Finished tracks are pruned from mpv's playlist on the next control/status call, and stale entries are cleared before starting a new track while idle. This keeps the 25-track limit scoped to the current and upcoming tracks.

### Spotify

The lounge can play Spotify tracks, albums, and playlists with a real Spotify Premium account. Spotify playback is separate from the mpv/yt-dlp queue: the agent resolves Spotify links through the Spotify Web API, `librespot` acts as a Spotify Connect device named `IEEE TAMU Lounge`, and librespot's PCM output is piped into mpv. This means mpv remains the only process that opens the ALSA device, so Spotify and YouTube audio do not fight over the sound card.

Setup on `ieee-tamu-5B`:

1. Create a Spotify Developer app at <https://developer.spotify.com/dashboard>. The account that owns the app must have Spotify Premium.
2. Add the lounge's Spotify account to the app's user list (Development Mode allows a small number of users).
3. Register this redirect URI on the app:
   `https://lounge.ieeetamu.org/api/spotify/callback`
4. Set `ieee-tamu.lounge-music.spotify.clientId` in `hosts/ieee-tamu-5B.nix` to the app's Client ID. No client secret is needed; the agent uses PKCE.
5. Deploy the host and the lounge web app. Open <https://lounge.ieeetamu.org> as an officer and use **Connect Spotify**. Only officers and admins can link or unlink the shared account.

Credentials are persisted by the music agent, not by the web app:

- OAuth refresh token: `/var/lib/lounge-music/spotify/credentials.json` (mode `0600`)
- librespot reusable credentials: `/var/lib/lounge-music/spotify/librespot/credentials.json`
- Saved mpv queue/volume state: `/var/lib/lounge-music/player-state.json`

The web app only forwards the OAuth authorization code to the agent; it never receives or stores the refresh token. The optional agent bearer token is still supported, but Spotify linking does not require it: the lounge web app's officer session is the access boundary.

Spotify queue editing is limited by Spotify's API. `/remove`, `/move`, and `/clear` are rejected while Spotify is the active source; use the Spotify app for those operations. `/stop` pauses Spotify but does not clear Spotify's queue. Starting a Spotify track from the Spotify app while YouTube is playing automatically pauses the YouTube stream, and starting a YouTube/yt-dlp URL from Discord or the lounge web app pauses Spotify.

Useful commands:

```bash
systemctl status lounge-music-mpv lounge-music-agent
journalctl -u lounge-music-agent -f
curl -s http://localhost:8787/spotify/status
```

If the Spotify refresh token is revoked (for example after changing the Spotify password), the lounge UI shows a reconnect error; use **Connect Spotify** again. The librespot credential cache is deleted when the account is disconnected.

YouTube, YouTube Music, SoundCloud, Bandcamp, and Spotify URLs (plus free-text search) are supported. YouTube playlists are expanded up to `ieee-tamu.lounge-music.maxQueueLength` entries (25 by default); Spotify albums and playlists are capped to the same queue length. yt-dlp is pinned to the `visionos` player client because YouTube's default TVHTML5 URLs return 403 for some videos.

## Printing

`ieee-tamu-5B` also runs CUPS for the Brother MFC-7360N in the officer lounge. The printer is network-attached at `192.168.1.38` (`lpd://192.168.1.38/binary_p1`). The Discord bot talks to a small HTTP API rather than CUPS directly:

```
/print  ->  discord bot  ->  http://192.168.1.10:8788  ->  lp  ->  Brother MFC-7360N
```

The module is `ieee-tamu.printing`; CUPS is `cups.service` and the control API is `print-agent.service`. It accepts PDF, PNG, JPEG, and plain-text files up to 10 MB and 20 copies per request. Printer state and queued jobs are available from `GET /status`; jobs can be cancelled with `POST /cancel`.

Useful commands on the host:

```bash
systemctl status cups print-agent
journalctl -u print-agent -f
lpstat -p Brother_MFC-7360N
lpstat -W not-completed -o Brother_MFC-7360N
lp -d Brother_MFC-7360N /path/to/test.pdf
curl -s http://localhost:8788/health
```

The control API is unauthenticated unless `ieee-tamu.printing.tokenFile` is set (for example to a sops-nix secret containing a bearer token). Keep port 8788 on the trusted campus LAN. To enable auth, add a sops secret on the node, point `ieee-tamu.printing.tokenFile` at `config.sops.secrets.<name>.path`, and set the same value as `PRINT_AGENT_TOKEN` in `cluster-infra/secrets/discord.yaml`.

## Wi-Fi for IoT devices

`ieee-tamu-5B` also runs a 2.4 GHz WPA2 access point for devices that cannot use the campus WPA-Enterprise network. The wired `enp1s0` and wireless `wlp0s12f0` interfaces are bridged into `br0`, so wireless clients get a normal IEEE LAN address (DHCP from the LAN router) rather than being NATed. The host's cluster IP and keepalived VIP both live on `br0`.

The network is named `IEEE TAMU Lounge` and is hidden. Its WPA2 passphrase is managed through the cluster secrets.

Hidden SSIDs are not a security control. Bambu's setup app scans for broadcast networks, so P1/A1 printers should be joined to this hidden network with a `user_wifi.cfg` on the root of the microSD card:

```
ssid: IEEE TAMU Lounge
password: <the configured WPA2 passphrase>
```

Insert the card and restart the printer. The passphrase is alphanumeric only, so it also works with the on-screen keyboard if the network is temporarily unhidden (`ieee-tamu.wifi-ap.hidden = false`). The module is `ieee-tamu.wifi-ap`; useful commands on the host:

```bash
systemctl status hostapd
journalctl -u hostapd -f
bridge link
iw dev wlp0s12f0 info
hostapd_cli status
```

Because the host IP moves from `enp1s0` to `br0`, the first deploy will briefly drop the SSH connection. Reconnect to `192.168.1.10`; the address is unchanged.
