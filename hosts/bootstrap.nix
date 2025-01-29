{
  imports = [
    ./global.nix
    ../hardware/wyse-disko.nix
  ];

  # generate by running nixos-anywhere with `--generate-hardware-config nixos-facter ./hardware/bootstrap.json`
  facter.reportPath = ../hardware/bootstrap.json;
  users.users.root.initialPassword = "bootstrap";

  # generate ssh host keys for use with sops
  services.openssh.enable = true;
  services.openssh.settings.PermitRootLogin = "yes";
}
