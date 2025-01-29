{
  config,
  pkgs,
  ...
}: {
  imports = [
    ./global.nix
    ../hardware/wyse-disko.nix
  ];

  facter.reportPath = ../hardware/wyse-8J.json;

  networking.hostName = "ieee-tamu-8J";
  networking.interfaces.enp1s0.ipv4.addresses = [
    {
      address = "192.168.1.11";
      prefixLength = 24;
    }
  ];
  networking.defaultGateway = {
    address = "192.168.1.1";
    interface = "enp1s0";
  };
  networking.nameservers = ["192.168.1.1"];
  networking.firewall.allowedTCPPorts = [
    6443 # k3s API server
    2379 # etcd server client API
    2380 # etcd server peer API
  ];
  networking.firewall.allowedUDPPorts = [
    8472 # flannel VXLAN
  ];

  sops.defaultSopsFile = ../secrets.yaml;
  sops.age.sshKeyPaths = ["/etc/ssh/ssh_host_ed25519_key"];
  sops.secrets.k3s_token = {};

  services.k3s = {
    enable = true;
    role = "server";
    tokenFile = config.sops.secrets.k3s_token.path;
    serverAddr = "https://192.168.1.10:6443";
    extraFlags = [
      "--tls-san 10.125.185.49"
      "--node-name ieee-tamu-8J"
    ];
  };

  # remove once tested
  users.users.root.initialPassword = "bootstrap";
  services.openssh.enable = true;
  services.openssh.settings.PermitRootLogin = "yes";
  environment.systemPackages = with pkgs; [
    git
    vim
  ];
}
