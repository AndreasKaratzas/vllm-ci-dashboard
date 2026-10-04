#!/usr/bin/env bash

export RED='\033[0;31m'
export GREEN='\033[0;32m'
export YELLOW='\033[1;33m'
export BLUE='\033[0;34m'
export MAGENTA='\033[0;35m'
export CYAN='\033[0;36m'
export BOLD='\033[1m'
export NC='\033[0m'

show_banner() {
    echo ""
    echo -e "${CYAN}╔═══════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║${NC}  ${BOLD}👁️  Panoptes – vLLM ecosystem project dashboard ${NC}                  ${CYAN}║${NC}"
    echo -e "${CYAN}╚═══════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

setup_python_env() {
    local python_bin=$1

    if [ ! -d ".venv" ]; then
        echo -e "${YELLOW}Creating Python virtual environment...${NC}"
        uv venv --python "$python_bin" .venv
    else
        echo -e "${GREEN}✓ Virtual environment exists${NC}"
    fi

    echo -e "${BLUE}Activating virtual environment...${NC}"
    source .venv/bin/activate
    echo ""
}

install_python_packages() {
    if [ -f "pyproject.toml" ]; then
        echo -e "${BLUE}Installing Python packages...${NC}"
        uv pip install -e ".[dev]"
        echo -e "${GREEN}✓ Python packages installed${NC}"
    elif [ -f "requirements.txt" ]; then
        echo -e "${BLUE}Installing from requirements.txt...${NC}"
        uv pip install -r requirements.txt
        echo -e "${GREEN}✓ Requirements installed${NC}"
    else
        echo -e "${YELLOW}⚠ No pyproject.toml or requirements.txt found${NC}"
    fi
    echo ""
}

show_env_info() {
    echo -e "${BLUE}Environment Configuration:${NC}"
    echo -e "  Python:  ${GREEN}$(python --version 2>&1)${NC}"
    echo -e "  Node:    ${GREEN}$(node --version 2>/dev/null || echo 'not found')${NC}"
    echo -e "  Venv:    ${GREEN}$VIRTUAL_ENV${NC}"
    echo -e "  gh CLI:  ${GREEN}$(gh --version 2>/dev/null | head -1 || echo 'not found')${NC}"
    echo ""
}

dash-collect() {
    echo -e "${CYAN}Collecting dashboard data...${NC}"
    python scripts/collect.py && \
    python scripts/collect_activity.py && \
    python scripts/collect_ci.py
}

dash-render() {
    echo -e "${CYAN}Rendering dashboards...${NC}"
    python scripts/render.py
}

dash-test() {
    echo -e "${CYAN}Running tests...${NC}"
    python -m pytest tests/ -v
}

dash-clean() {
    echo -e "${YELLOW}Cleaning...${NC}"
    [ -d ".venv" ] && rm -rf .venv && echo -e "${GREEN}✓ venv removed${NC}"
    find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    [ -d ".pytest_cache" ] && rm -rf .pytest_cache
    [ -d ".hypothesis" ] && rm -rf .hypothesis
    [ -d "_site" ] && rm -rf _site
    echo -e "${GREEN}✓ Clean${NC}"
}

dash-lint-js() {
    echo -e "${CYAN}Checking JS/HTML/CSS formatting with prettier...${NC}"
    prettier --check 'docs/assets/**/*.{js,css}' 'docs/*.html'
}

dash-fmt-js() {
    echo -e "${CYAN}Formatting JS/HTML/CSS with prettier...${NC}"
    prettier --write 'docs/assets/**/*.{js,css}' 'docs/*.html'
}

dash-lint-workflows() {
    echo -e "${CYAN}Linting GitHub Actions workflows...${NC}"
    actionlint .github/workflows/*.yml && \
    yamllint -d '{extends: default, rules: {line-length: disable, truthy: disable, comments: disable, document-start: disable}}' .github/workflows/
}

dash-lint-shell() {
    echo -e "${CYAN}Linting shell scripts with shellcheck...${NC}"
    find . -type f \( -name '*.sh' -o -name '*.bash' \) \
        -not -path './.venv/*' -not -path './node_modules/*' \
        -exec shellcheck {} +
}

dash-lint-spell() {
    echo -e "${CYAN}Spellchecking with cspell...${NC}"
    cspell --no-progress --no-summary \
        'scripts/**/*.py' 'tests/**/*.py' \
        'docs/assets/js/*.js' 'docs/*.html' \
        'README.md' 'dashboards/**/*.md' \
        '.github/workflows/*.yml'
}

show_tips() {
    echo -e "${CYAN}Available Commands:${NC}"
    echo -e "  ${BOLD}dash-collect${NC}        - Run all data collectors (GitHub API, CI, activity)"
    echo -e "  ${BOLD}dash-render${NC}         - Render markdown dashboards and site data"
    echo -e "  ${BOLD}dash-test${NC}           - Run the test suite"
    echo -e "  ${BOLD}dash-clean${NC}          - Clean venv and caches"
    echo -e "  ${BOLD}dash-lint-js${NC}        - prettier --check on docs/assets/**"
    echo -e "  ${BOLD}dash-fmt-js${NC}         - prettier --write on docs/assets/**"
    echo -e "  ${BOLD}dash-lint-workflows${NC} - actionlint + yamllint on .github/workflows/"
    echo -e "  ${BOLD}dash-lint-shell${NC}     - shellcheck on all .sh/.bash files"
    echo ""
}
