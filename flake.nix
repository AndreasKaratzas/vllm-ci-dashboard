{
  description = "Panoptes – vLLM ecosystem project dashboard";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        config = import ./nix/config.nix;

        pkgs = import nixpkgs {
          inherit system;
          config = { allowUnfree = true; };
        };

        shells = import ./nix/shells { inherit pkgs config; };
      in
      {
        devShells.default = shells.dashboard;
        devShells.dashboard = shells.dashboard;
        devShells.minimal = shells.minimal;
      }
    );
}
