{
  pkgs,
  config,
  ...
}:
{
  nix.settings.experimental-features = [
    "nix-command"
    "flakes"
  ];
  sops.defaultSopsFile = ./secrets.yaml;
  sops.age.sshKeyPaths = [ "/etc/ssh/ssh_host_ed25519_key" ];
  sops.secrets.k3s_token = { };
  sops.secrets.root_password.neededForUsers = true;
  users.mutableUsers = false;

  users.users.root = {
    hashedPasswordFile = config.sops.secrets.root_password.path;
    openssh.authorizedKeys.keys = [
      "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIO2FLDIautZl87H9xJKsPJsO0gO/8t4jOS3Szz4j2qY4"
    ];
  };

  services.openssh.enable = true;

  environment.systemPackages = with pkgs; [
    git
    vim
  ];

  nixpkgs.config.allowUnfree = true;
}
