{
  config,
  lib,
  ...
}: let
  cfg = config.ieee-tamu.cluster;
in {
  options = {
    ieee-tamu.cluster = {
      enable = lib.mkEnableOption "ieee-tamu cluster";
    };
  };
  config = lib.mkIf cfg.enable {
    assertions = [];
  };
}
