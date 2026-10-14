# Shared by install.sh and uninstall.sh: output, the dry run, questions, and
# how to tell what this installation made from everything else in $HOME.
# Sourced, not run.

DRY_RUN=0
# While a dry run describes what a "yes" would lead to, this says so.
COND=''

say() { printf '%s\n' "$*"; }
warn() { printf '%s\n' "$*" >&2; }
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
tildify() { local p=$1; printf '%s' "${p/#$HOME/\~}"; }

# A command as it would be typed.
quote() {
  local out='' a
  for a; do out+=" $(printf '%q' "$a")"; done
  printf '%s' "${out# }"
}

# Run a command that changes something; in a dry run only say which.
run() {
  if (( DRY_RUN )); then
    say "  would run${COND}: $(quote "$@")"
    return 0
  fi
  say "  \$ $(quote "$@")"
  "$@"
}

# ask QUESTION [WORD]: true only for y/yes, or with WORD, for WORD typed out.
# No answer at all (end of input) is no. A dry run asks nothing: it says what
# it would ask and goes on to describe the "yes" branch until `asked`.
ask() {
  local q=$1 word=${2:-} reply=''
  if (( DRY_RUN )); then
    if [[ -n $word ]]; then
      say "  would ask: $q (only typing '$word' confirms)"
    else
      say "  would ask: $q [y/N]"
    fi
    COND=' if you answer yes'
    return 0
  fi
  if [[ -n $word ]]; then
    printf "%s Type '%s' to confirm, anything else keeps it: " "$q" "$word"
  else
    printf '%s [y/N] ' "$q"
  fi
  read -r reply || reply=''
  [[ -z $word && ( $reply == [yY] || ${reply,,} == yes ) ]] && return 0
  [[ -n $word && $reply == "$word" ]]
}
asked() { COND=''; }

OS_RELEASE=${FDROP_OS_RELEASE:-/etc/os-release}

require_fedora() {
  local id=''
  [[ -r $OS_RELEASE ]] && id=$(sed -n 's/^ID=//p' "$OS_RELEASE" | tr -d '"'"'")
  [[ $id == fedora ]] \
    || die "This script is for Fedora; $OS_RELEASE says ID=${id:-nothing}. Nothing was changed."
}

require_user() {
  (( EUID != 0 )) \
    || die "Run this as yourself, not as root: it works on your home directory and asks for sudo only for dnf."
}

pkg_installed() { rpm -q --quiet "$1" 2>/dev/null; }
pkg_nevra() { rpm -q "$1" 2>/dev/null; }

# --- What the omdrop package's copy of `omdrop setup` writes ---------------

PKG_DIR=/usr/share/omdrop
UNIT=airdrop-receiver.service
LINK_APP=netmojo.omdrop-links.desktop
LINK_MIME=netmojo.omdrop-webloc.xml
LINK_TYPES=(application/x-mswinurl application/x-webloc)

config_home() { printf '%s' "${XDG_CONFIG_HOME:-$HOME/.config}"; }
data_home() { printf '%s' "${XDG_DATA_HOME:-$HOME/.local/share}"; }
bin_home() { printf '%s' "${XDG_BIN_HOME:-$HOME/.local/bin}"; }

unit_path() { printf '%s/systemd/user/%s' "$(config_home)" "$UNIT"; }
dropin_path() { printf '%s.d/10-download-dir.conf' "$(unit_path)"; }
link_app_path() { printf '%s/applications/%s' "$(data_home)" "$LINK_APP"; }
link_mime_path() { printf '%s/mime/packages/%s' "$(data_home)" "$LINK_MIME"; }
mimeapps_path() { printf '%s/mimeapps.list' "$(config_home)"; }
skill_dirs() { printf '%s\n' "$HOME/.agents/skills" "$HOME/.claude/skills" "$HOME/.codex/skills" "$HOME/.pi/agent/skills"; }
# Where install.sh moves old links it replaces, one folder per migration.
replaced_links_dir() { printf '%s/fdrop/replaced-links' "$(data_home)"; }

# The receiving service rendered from the packaged template: it runs the
# receiver in /usr/share/omdrop. One rendered from a checkout runs that.
unit_is_ours() {
  local f=$1
  [[ -f $f && ! -L $f ]] \
    && grep -qxF "WorkingDirectory=$PKG_DIR" "$f" \
    && grep -qE "^ExecStart=.* $PKG_DIR/bin/airdrop-serve\.py( |$)" "$f"
}

# `omdrop dir` marks the drop-in it writes.
dropin_is_ours() {
  [[ -f $1 && ! -L $1 ]] && [[ "$(head -n 1 "$1")" == '# Written by omdrop.'* ]]
}

link_app_is_ours() {
  [[ -f $1 && ! -L $1 ]] && grep -qxF "Exec=\"$PKG_DIR/bin/omdrop\" open-link %f" "$1"
}

# Byte for byte what omdrop writes; any omdrop, packaged or not, writes the same.
link_mime_is_omdrops() {
  local want='<?xml version="1.0" encoding="UTF-8"?>
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
  <mime-type type="application/x-webloc">
    <comment>Website location</comment>
    <glob pattern="*.webloc"/>
  </mime-type>
</mime-info>'
  [[ -f $1 && ! -L $1 && "$(cat "$1")" == "$want" ]]
}

# The [Default Applications] lines that name the link opener.
mimeapps_our_lines() {
  local list; list=$(mimeapps_path)
  [[ -f $list ]] || return 0
  A="$LINK_APP" awk '
    /^\[/ { in_defaults = ($0 == "[Default Applications]"); next }
    in_defaults && ($0 ~ "^application/x-(mswinurl|webloc)=" && (substr($0, index($0, "=") + 1) == ENVIRON["A"] || substr($0, index($0, "=") + 1) == ENVIRON["A"] ";")) { print }
  ' "$list"
}

mimeapps_drop_our_lines() {
  local list tmp
  list=$(mimeapps_path)
  tmp=$(mktemp "$list.fdrop.XXXXXX") || return 1
  A="$LINK_APP" awk '
    /^\[/ { in_defaults = ($0 == "[Default Applications]") }
    in_defaults && ($0 ~ "^application/x-(mswinurl|webloc)=" && (substr($0, index($0, "=") + 1) == ENVIRON["A"] || substr($0, index($0, "=") + 1) == ENVIRON["A"] ";")) { next }
    { print }' "$list" > "$tmp" || { rm -f -- "$tmp"; return 1; }
  # Written through, so a mimeapps.list that is a link, or has its own mode, stays so.
  cat -- "$tmp" > "$list"
  local rc=$?
  rm -f -- "$tmp"
  return "$rc"
}

# Skill links into the packaged skill, as `omdrop skill install` makes them.
our_skill_links() {
  local dir
  while IFS= read -r dir; do
    [[ -L $dir/omdrop && "$(readlink "$dir/omdrop")" == "$PKG_DIR/skills/omdrop" ]] && printf '%s\n' "$dir/omdrop"
  done < <(skill_dirs)
}

# --- Links from installing by hand -----------------------------------------
#
# Before the packages, `omdrop setup` from a checkout linked ~/.local/bin/omdrop
# to that checkout, and Fdrop was linked there by hand. ~/.local/bin comes
# before /usr/bin on Fedora's PATH, so such a link hides the packaged command.
# Only symbolic links with these names that point outside /usr count; a file
# someone put there is theirs.
LEGACY_NAMES=(omdrop fdrop omdrop-gnome)

legacy_links() {
  local bindir name link raw
  bindir=$(bin_home)
  for name in "${LEGACY_NAMES[@]}"; do
    link=$bindir/$name
    [[ -L $link ]] || continue
    raw=$(readlink "$link")
    [[ $raw == /usr/* ]] && continue
    printf '%s\n' "$link"
  done
}

legacy_files() {
  local bindir name
  bindir=$(bin_home)
  for name in "${LEGACY_NAMES[@]}"; do
    [[ -e $bindir/$name && ! -L $bindir/$name ]] && printf '%s\n' "$bindir/$name"
  done
}
