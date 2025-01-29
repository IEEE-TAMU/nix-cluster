{
  config,
  pkgs,
  ...
}: {
  imports = [
    ./global.nix
    ../hardware/wyse-disko.nix
  ];

  facter.reportPath = ../hardware/wyse-5B.json;

  networking.hostName = "ieee-tamu-5B";

  sops.defaultSopsFile = ../secrets.yaml;
  sops.age.sshKeyPaths = ["/etc/ssh/ssh_host_ed25519_key"];
  sops.secrets.k3s_token = {};

  services.k3s = {
    enable = true;
    role = "server";
    tokenFile = config.sops.secrets.k3s_token.path;
    clusterInit = true;
  };

  services.etcd.enable = true;

  # remove once tested
  users.users.root.initialPassword = "bootstrap";
  services.openssh.enable = true;
  services.openssh.settings.PermitRootLogin = "yes";
  environment.systemPackages = with pkgs; [
    git
    vim
  ];
}
