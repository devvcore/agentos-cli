#!/bin/sh
# AgentOS CLI installer:  curl -fsSL https://tryagentos.net/install.sh | sh
#
# Installs the `aos` command for the current user (no sudo) with uv, which it
# installs first if it's missing. Re-running upgrades to the latest release.
#
#   AOS_VERSION=v0.2.0   install that release instead of the latest
#   AOS_FROM_GIT=1       install from the main branch instead of a release
#   AOS_NO_MODIFY_PATH=1 don't add ~/.local/bin to your shell's PATH
set -eu

REPO="devvcore/agentos-cli"
BOLD="$(printf '\033[1m')"; DIM="$(printf '\033[2m')"; GREEN="$(printf '\033[32m')"; RED="$(printf '\033[31m')"; RESET="$(printf '\033[0m')"
if [ ! -t 1 ] || [ -n "${NO_COLOR:-}" ]; then BOLD=""; DIM=""; GREEN=""; RED=""; RESET=""; fi

say() { printf '%s\n' "$*"; }
ok() { printf '%s✓%s %s\n' "${GREEN}" "${RESET}" "$*"; }
die() { printf '%s✕%s %s\n' "${RED}" "${RESET}" "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1; }

case "$(uname -s)" in
  Darwin|Linux) ;;
  *) die "This installer supports macOS and Linux. On Windows, use WSL, or: uv tool install git+https://github.com/${REPO}" ;;
esac
need curl || die "curl is required."

say "${BOLD}AgentOS CLI${RESET} ${DIM}· installs the \`aos\` command for $(id -un)${RESET}"

# 1. uv (Astral's Python tool installer). It brings its own Python if needed.
if ! need uv; then
  for d in "${HOME}/.local/bin" "${HOME}/.cargo/bin"; do
    if [ -x "${d}/uv" ]; then PATH="${d}:${PATH}"; export PATH; fi
  done
fi
if ! need uv; then
  say "${DIM}Installing uv (https://docs.astral.sh/uv)...${RESET}"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh >/dev/null 2>&1 || die "Couldn't install uv. Install it from https://docs.astral.sh/uv and run this again."
  PATH="${HOME}/.local/bin:${HOME}/.cargo/bin:${PATH}"; export PATH
  need uv || die "uv was installed but isn't on PATH. Open a new terminal and run this again."
  ok "uv installed"
fi

# 2. Which release
if [ -n "${AOS_FROM_GIT:-}" ]; then
  SOURCE="git+https://github.com/${REPO}"
  LABEL="main branch"
else
  TAG="${AOS_VERSION:-}"
  if [ -z "${TAG}" ]; then
    # /releases/latest redirects to /releases/tag/<tag>; no API token or rate limit.
    # The query string skips GitHub's cached redirect, which lags a new release.
    TAG="$(curl -fsSI "https://github.com/${REPO}/releases/latest?ts=$(date +%s)" | tr -d '\r' | sed -n 's#^[Ll]ocation: .*/releases/tag/\(.*\)$#\1#p' | tail -n1)"
  fi
  if [ -z "${TAG}" ]; then
    SOURCE="git+https://github.com/${REPO}"
    LABEL="main branch (no release found)"
  else
    VER="${TAG#v}"
    SOURCE="https://github.com/${REPO}/releases/download/${TAG}/agentos_cli-${VER}-py3-none-any.whl"
    LABEL="${TAG}"
  fi
fi

# 3. Install (or upgrade) as a uv tool: its own environment, `aos` in ~/.local/bin
say "${DIM}Installing aos ${LABEL}...${RESET}"
uv tool install --force --quiet "${SOURCE}" || die "Install failed. Try: uv tool install git+https://github.com/${REPO}"

BIN="$(uv tool dir --bin 2>/dev/null || printf '%s' "${HOME}/.local/bin")"
[ -x "${BIN}/aos" ] || die "aos wasn't found in ${BIN} after install."
ok "$("${BIN}/aos" --version) installed at ${BIN}/aos"

case ":${PATH}:" in
  *":${BIN}:"*) ;;
  *)
    if [ -z "${AOS_NO_MODIFY_PATH:-}" ]; then
      if uv tool update-shell >/dev/null 2>&1; then
        ok "Added ${BIN} to your PATH (open a new terminal, or run: export PATH=\"${BIN}:\${PATH}\")"
      else
        say "Add ${BIN} to your PATH: export PATH=\"${BIN}:\${PATH}\""
      fi
    else
      say "Add ${BIN} to your PATH: export PATH=\"${BIN}:\${PATH}\""
    fi
    ;;
esac

say ""
say "${BOLD}Next${RESET}"
say "  aos login     ${DIM}# sign in or create your AgentOS account (opens your browser)${RESET}"
say "  aos setup     ${DIM}# add AgentOS to Claude Code, Codex, Cursor and your other agents${RESET}"
say "  aos           ${DIM}# everything else${RESET}"
