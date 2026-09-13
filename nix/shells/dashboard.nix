{ pkgs, config, ... }:

let
  pythonVersion = config.python.version or "312";
  python        = pkgs."python${pythonVersion}";
in
pkgs.mkShell {
  name = config.project.name;

  packages = with pkgs; [
    python uv

    # Node + JS tooling for the dashboard frontend (docs/assets/js).
    # cspell moved to a top-level attribute after nodePackages was
    # removed from nixpkgs.
    nodejs_22
    prettier
    cspell

    git git-lfs gh

    ripgrep fzf eza bat jq yq-go

    # Shell + YAML + workflow validation
    shellcheck yamllint actionlint

    # GitHub Actions runner — test workflows locally before pushing
    act

    # API debugging (Buildkite / GitHub) and micro-benchmarks for collectors
    httpie hyperfine

    # Better git diffs
    delta

    claude-code
  ];

  PIP_NO_CACHE_DIR = "1";

  shellHook = ''
    source ${./shell-utils.sh}

    show_banner
    setup_python_env "${python}/bin/python"
    install_python_packages
    show_env_info

    export -f dash-collect
    export -f dash-render
    export -f dash-test
    export -f dash-clean
    export -f dash-lint-js
    export -f dash-fmt-js
    export -f dash-lint-workflows
    export -f dash-lint-shell
    export -f dash-lint-spell

    show_tips
  '';
}
