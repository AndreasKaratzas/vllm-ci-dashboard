{ pkgs, config }:

{
  dashboard = import ./dashboard.nix { inherit pkgs config; };
  minimal = import ./minimal.nix { inherit pkgs config; };
}
