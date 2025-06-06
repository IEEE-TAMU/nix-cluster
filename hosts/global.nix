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

  users.users.root.hashedPasswordFile = config.sops.secrets.root_password.path;
  services.openssh.enable = true;
  services.openssh.settings.PermitRootLogin = "yes";
  environment.systemPackages = with pkgs; [
    git
    vim
  ];
}
