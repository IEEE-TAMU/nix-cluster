{ lib, ... }:
{
  options.meta = {
    owner = {
      name = lib.mkOption {
        type = lib.types.str;
        description = "Name of the owner of this configuration.";
      };
      email = lib.mkOption {
        type = lib.types.singleLineStr;
        description = "Email of the owner of this configuration.";
      };
      sshKeys = lib.mkOption {
        type = lib.types.listOf lib.types.singleLineStr;
        description = "List of ssh keys";
      };
    };

    flake = lib.mkOption {
      type = lib.types.str;
      description = "URL of the flake repository.";
    };
  };
}
