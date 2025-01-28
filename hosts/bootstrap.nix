{
  imports = [
    ./global.nix
  ];

  # generate by running nixos-anywhere with `--generate-hardware-config nixos-facter ./facter.json`
  facter.reportPath = ../hardware/wyse-5070.json;
}
