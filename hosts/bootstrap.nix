{
  imports = [
    ./global.nix
    ../hardware/wyse-disko.nix
  ];

  # generate by running nixos-anywhere with `--generate-hardware-config nixos-facter output.json`
  facter.reportPath = ../hardware/wyse-5070.json;
  users.users.root.initialPassword = "bootstrap";
}
