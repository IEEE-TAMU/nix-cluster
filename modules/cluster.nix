{
  config,
  lib,
  pkgs,
  ...
}: let
  cfg = config.ieee.cluster;
in {
  options = {
    ieee.cluster = {};
  };
  config = lib.mkIf cfg.enable {
    assertions = [];
  };
}
