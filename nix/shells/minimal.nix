{ pkgs, config }:

let
  pythonVersion = config.python.version;
  python = pkgs."python${pythonVersion}";
in
pkgs.mkShell {
  name = "panoptes-minimal";

  packages = with pkgs; [
    python
    uv
    nodejs_22
    git
    gh
    ripgrep
    bat
    jq
    claude-code
  ];

  shellHook = ''
    echo -e "\033[0;36m=== Panoptes Minimal Shell ===\033[0m"
    echo "Python: ${python.version}"

    if [ -f ".venv/bin/activate" ]; then
      source .venv/bin/activate
      echo "✓ Virtual environment activated"
    else
      echo "Run 'uv venv' to create a virtual environment"
    fi
  '';
}
