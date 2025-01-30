{
  config,
  pkgs,
  ...
}: {
  imports = [
    ../hardware/wyse-disko.nix
  ];

  sops.defaultSopsFile = ../secrets.yaml;
  sops.age.sshKeyPaths = ["/etc/ssh/ssh_host_ed25519_key"];
  sops.secrets.k3s_token = {};

  ieee-tamu.cluster = {
    enable = true;
    tokenFile = config.sops.secrets.k3s_token.path;
    node = {
      facter-config = ../hardware/wyse-7P.json;
      hostName = "ieee-tamu-7P";
      nameservers = ["192.168.1.1"];
      interface = "enp1s0";
      ipv4.addresses = [
        {
          address = "192.168.1.13";
          prefixLength = 24;
        }
      ];
      defaultGateway = "192.168.1.1";
      allowedTCPPorts = [
        6443 # k3s API server
        2379 # etcd server client API
        2380 # etcd server peer API
      ];
      allowedUDPPorts = [
        8472 # flannel VXLAN
      ];
      role = "agent";
    };
    init = {
      ipv4.addresses = [
        {
          address = "192.168.1.10";
          prefixLength = 24;
        }
      ];
    };
  };

  # remove once tested
  users.users.root.initialPassword = "bootstrap";
  services.openssh.enable = true;
  services.openssh.settings.PermitRootLogin = "yes";
  environment.systemPackages = with pkgs; [
    git
    vim
  ];

  system.stateVersion = "24.11";
}
