#!/usr/bin/env bash
# Manage IEEE-TAMU WireGuard client peers and configs.
set -euo pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(dirname "$SCRIPT_PATH")"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
PEERS_FILE="$REPO_DIR/hosts/wireguard-peers.json"
SECRETS_FILE="$REPO_DIR/hosts/secrets.yaml"
OUTPUT_DIR="$REPO_DIR/wireguard-clients"
ENDPOINT="${IEEE_WG_ENDPOINT:-10.125.185.49:51820}"
ALLOWED_IPS="${IEEE_WG_ALLOWED_IPS:-192.168.1.0/24, 10.99.0.0/24}"

cleanup_path=""
trap 'rm -rf "${cleanup_path:-}"' EXIT

# Re-exec with the tools the script needs if they are not already installed.
if ! command -v wg >/dev/null 2>&1 ||
	! command -v jq >/dev/null 2>&1 ||
	! command -v sops >/dev/null 2>&1; then
	exec nix shell nixpkgs#wireguard-tools nixpkgs#jq nixpkgs#sops --command bash "$SCRIPT_PATH" "$@"
fi

usage() {
	cat <<'EOF'
Usage:
  wireguard-client.sh add <name> [--output <path>] [--force]
  wireguard-client.sh revoke <name>
  wireguard-client.sh list

add      Generate a keypair, allocate a tunnel IP, add the peer to
         hosts/wireguard-peers.json, and write a client config.
revoke   Remove a peer from hosts/wireguard-peers.json.
list     Show configured peers and tunnel addresses.

After add/revoke, commit and push the change, then deploy
ieee-tamu-8J so the server applies it.
EOF
}

validate_name() {
	local name="$1"
	if [[ ! "$name" =~ ^[A-Za-z0-9._-]+$ ]]; then
		echo "Invalid peer name '$name'. Use letters, numbers, '.', '_' or '-'." >&2
		exit 1
	fi
}

ensure_peers_file() {
	if [[ ! -f "$PEERS_FILE" ]]; then
		printf '{}\n' >"$PEERS_FILE"
	fi
}

deploy_hint() {
	cat <<EOF

Next steps:
  git -C "$REPO_DIR" add hosts/wireguard-peers.json
  git -C "$REPO_DIR" commit -m "wireguard: $1"
  git -C "$REPO_DIR" push
  nixos-rebuild switch -s --use-remote-sudo --fast \\
    --flake "$REPO_DIR#ieee-tamu-8J" \\
    --target-host root@ieee-tamu-8J \\
    --build-host root@ieee-tamu-8J
EOF
}

cmd_add() {
	local name="${1:-}"
	shift || true
	[[ -n "$name" ]] || {
		usage
		exit 1
	}
	validate_name "$name"

	local output="$OUTPUT_DIR/$name.conf"
	local force=false
	while [[ $# -gt 0 ]]; do
		case "$1" in
		--output)
			output="${2:-}"
			[[ -n "$output" ]] || {
				echo "--output requires a path" >&2
				exit 1
			}
			shift 2
			;;
		--force)
			force=true
			shift
			;;
		*)
			echo "Unknown option: $1" >&2
			usage
			exit 1
			;;
		esac
	done

	ensure_peers_file
	if jq -e --arg name "$name" 'has($name)' "$PEERS_FILE" >/dev/null; then
		echo "Peer '$name' already exists. Revoke it first." >&2
		exit 1
	fi
	if [[ -e "$output" && "$force" != true ]]; then
		echo "Output file '$output' already exists. Use --force to overwrite." >&2
		exit 1
	fi

	local used_ips address=""
	used_ips="$(jq -r '.[].address // empty' "$PEERS_FILE" | cut -d/ -f1)"
	for n in {2..254}; do
		local candidate="10.99.0.$n"
		if ! grep -qx "$candidate" <<<"$used_ips"; then
			address="$candidate"
			break
		fi
	done
	if [[ -z "$address" ]]; then
		echo "No free tunnel addresses left in 10.99.0.0/24." >&2
		exit 1
	fi

	cleanup_path="$(mktemp -d)"
	umask 077

	wg genkey >"$cleanup_path/client.key"
	wg pubkey <"$cleanup_path/client.key" >"$cleanup_path/client.pub"

	local psk server_private_key server_public_key
	psk="$(sops -d --extract '["wg_preshared_key"]' "$SECRETS_FILE")"
	server_private_key="$(sops -d --extract '["wg_server_private_key"]' "$SECRETS_FILE")"
	server_public_key="$(printf '%s\n' "$server_private_key" | wg pubkey)"

	mkdir -p "$OUTPUT_DIR"
	chmod 700 "$OUTPUT_DIR"

	cat >"$output" <<EOF
[Interface]
# $name
PrivateKey = $(cat "$cleanup_path/client.key")
Address = $address/32

[Peer]
PublicKey = $server_public_key
PresharedKey = $psk
Endpoint = $ENDPOINT
AllowedIPs = $ALLOWED_IPS
PersistentKeepalive = 25
EOF
	chmod 600 "$output"

	jq \
		--arg name "$name" \
		--arg publicKey "$(cat "$cleanup_path/client.pub")" \
		--arg address "$address/32" \
		--arg created "$(date -Iseconds)" \
		'.[$name] = {publicKey: $publicKey, address: $address, created: $created}' \
		"$PEERS_FILE" >"$cleanup_path/peers.json"
	chmod 644 "$cleanup_path/peers.json"
	mv "$cleanup_path/peers.json" "$PEERS_FILE"

	echo "Added peer '$name' with tunnel address $address/32."
	echo "Client config: $output"
	deploy_hint "add $name"
}

cmd_revoke() {
	local name="${1:-}"
	[[ -n "$name" ]] || {
		usage
		exit 1
	}
	validate_name "$name"
	ensure_peers_file

	if ! jq -e --arg name "$name" 'has($name)' "$PEERS_FILE" >/dev/null; then
		echo "Peer '$name' does not exist." >&2
		exit 1
	fi

	cleanup_path="$(mktemp)"
	jq --arg name "$name" 'del(.[$name])' "$PEERS_FILE" >"$cleanup_path"
	chmod 644 "$cleanup_path"
	mv "$cleanup_path" "$PEERS_FILE"

	echo "Revoked peer '$name'."
	if [[ -e "$OUTPUT_DIR/$name.conf" ]]; then
		echo "Local config still exists at $OUTPUT_DIR/$name.conf; delete it if you no longer need it."
	fi
	deploy_hint "revoke $name"
}

cmd_list() {
	ensure_peers_file
	if [[ "$(jq 'length' "$PEERS_FILE")" -eq 0 ]]; then
		echo "No peers configured."
		return
	fi
	jq -r 'to_entries[] | "\(.key)\t\(.value.address)\t\(.value.created // "")"' "$PEERS_FILE"
}

case "${1:-}" in
add)
	shift
	cmd_add "$@"
	;;
revoke)
	shift
	cmd_revoke "$@"
	;;
list)
	cmd_list
	;;
*)
	usage
	exit 1
	;;
esac
