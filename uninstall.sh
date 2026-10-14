#!/bin/bash
# Remove Fdrop and the omdrop package, on Fedora.
#
#   ./uninstall.sh                 remove the fdrop and omdrop packages, and
#                                  the files omdrop generated that point into
#                                  them; keep your identity and settings
#   ./uninstall.sh --all           also your omdrop settings, after listing
#                                  every path and asking; your identity and
#                                  omdrop-owl only if you answer separately
#   ./uninstall.sh [--all] --dry-run   say what that would do, change nothing
#   ./uninstall.sh --help
#
# A file is removed only when it is provably this installation's: a service
# or desktop file that runs /usr/share/omdrop, a link into it, or a file at a
# path omdrop owns. Nothing is removed recursively, and a folder is removed
# only once it is empty. ~/Downloads is never touched.
set -uo pipefail
umask 022

ROOT=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=tools/install-common.sh
. "$ROOT/tools/install-common.sh"

ALL=0

usage() {
  cat <<EOF
Usage: ./uninstall.sh [--all] [--dry-run]

Removes the fdrop and omdrop packages, the receiving service and link handler
that omdrop generated for them, and nothing of yours.

  --all       Also remove omdrop's settings in your home directory. Lists
              every path and asks first. Your AirDrop identity (~/.omdrop)
              and the omdrop-owl package are each kept unless you confirm
              them separately.
  --dry-run   Change nothing: no dnf, no sudo, no services, no files. Print
              each command and path that would be affected.
  -h, --help  Show this help.

Always kept: ~/Downloads and wherever received files went, ~/.opendrop.
EOF
}

for arg; do
  case $arg in
    --all) ALL=1 ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help) usage; exit 0 ;;
    *) warn "Unknown option: $arg"; usage >&2; exit 2 ;;
  esac
done

require_user
require_fedora
command -v rpm >/dev/null 2>&1 || die "rpm is missing, so installed packages cannot be checked."

# --- What is there ----------------------------------------------------------

pkgs=()
for p in fdrop omdrop; do pkg_installed "$p" && pkgs+=("$p"); done
owl=0; pkg_installed omdrop-owl && owl=1
have_systemctl=0; command -v systemctl >/dev/null 2>&1 && have_systemctl=1
receiving=0
(( have_systemctl )) && [[ "$(systemctl --user is-active "$UNIT" 2>/dev/null)" == active ]] && receiving=1

declare -A REMOVING=()
generated=()   # always removed: they point into the packages
settings=()    # --all
identity=()    # --all, and only on its own confirmation
dirs_settings=() dirs_identity=()
kept=()
mimeapps_lines=''
unit_removed=0

plan_file() {  # ARRAY PATH
  local -n into=$1
  into+=("$2")
  REMOVING[$2]=1
}

# A folder goes only when everything in it is going too.
plan_dir_if_emptied() {  # ARRAY DIR
  local -n into=$1
  local dir=$2 entry
  [[ -d $dir && ! -L $dir ]] || return 0
  shopt -s nullglob dotglob
  for entry in "$dir"/*; do
    if [[ -z ${REMOVING[$entry]:-} ]]; then
      shopt -u nullglob dotglob
      kept+=("$(tildify "$dir")/: still holds $(tildify "$entry"), which this script does not know")
      return 0
    fi
  done
  shopt -u nullglob dotglob
  into+=("$dir")
  REMOVING[$dir]=1
}

# The receiving service.
f=$(unit_path)
if unit_is_ours "$f"; then
  plan_file generated "$f"
  unit_removed=1
elif [[ -e $f || -L $f ]]; then
  kept+=("$(tildify "$f"): does not run $PKG_DIR, so another omdrop made it")
fi

# The handler for received links, its MIME type and the defaults naming it.
f=$(link_app_path)
handler_ours=1
if link_app_is_ours "$f"; then
  plan_file generated "$f"
elif [[ -e $f || -L $f ]]; then
  handler_ours=0
  kept+=("$(tildify "$f"): runs another copy of omdrop")
fi
if (( handler_ours )); then
  f=$(link_mime_path)
  link_mime_is_omdrops "$f" && plan_file generated "$f"
  mimeapps_lines=$(mimeapps_our_lines)
fi

while IFS= read -r f; do
  [[ -n $f ]] && plan_file generated "$f"
done < <(our_skill_links)

if (( ALL )); then
  f=$(dropin_path)
  if dropin_is_ours "$f"; then
    plan_file settings "$f"
  elif [[ -e $f || -L $f ]]; then
    kept+=("$(tildify "$f"): not written by omdrop")
  fi
  plan_dir_if_emptied dirs_settings "${f%/*}"

  for f in "$(config_home)/airdrop/config.toml" \
           "$(config_home)/omdrop/settings" \
           "$(config_home)/omdrop/known-senders" \
           "$(config_home)/omdrop/visibility"; do
    [[ -f $f && ! -L $f ]] && plan_file settings "$f"
  done
  plan_dir_if_emptied dirs_settings "$(config_home)/airdrop"
  plan_dir_if_emptied dirs_settings "$(config_home)/omdrop"

  # Old links install.sh moved aside, with the note on how to restore them.
  backups=$(replaced_links_dir)
  if [[ -d $backups ]]; then
    for d in "$backups"/*/; do
      d=${d%/}
      [[ -d $d && ! -L $d ]] || continue
      for f in "$d"/*; do
        [[ -L $f ]] && plan_file settings "$f"
      done
      [[ -f $d/RESTORE ]] && head -n 1 "$d/RESTORE" | grep -q '^# Written by fdrop install.sh' \
        && plan_file settings "$d/RESTORE"
      plan_dir_if_emptied dirs_settings "$d"
    done
    plan_dir_if_emptied dirs_settings "$backups"
    plan_dir_if_emptied dirs_settings "${backups%/*}"
  fi

  for name in certificate.pem key.pem validation_record.cms \
              certificate.self-signed.pem key.self-signed.pem .identity.lock; do
    f=$HOME/.omdrop/keys/$name
    [[ -f $f && ! -L $f ]] && plan_file identity "$f"
  done
  plan_dir_if_emptied dirs_identity "$HOME/.omdrop/keys"
  plan_dir_if_emptied dirs_identity "$HOME/.omdrop"
fi

# --- The plan ---------------------------------------------------------------

(( DRY_RUN )) && say "Dry run: nothing is removed, stopped or changed."
list() { local p; for p; do say "    $(tildify "$p")"; done; }

say "Packages:"
if (( ${#pkgs[@]} )); then
  for p in "${pkgs[@]}"; do say "    remove $(pkg_nevra "$p")"; done
else
  say "    fdrop and omdrop are not installed."
fi
(( owl )) && say "    keep $(pkg_nevra omdrop-owl)$( (( ALL )) && printf ', unless you answer yes below')"
if (( ${#generated[@]} )) || [[ -n $mimeapps_lines ]]; then
  say "Generated for the omdrop package, removed with it:"
  list "${generated[@]}"
  [[ -n $mimeapps_lines ]] && say "    in $(tildify "$(mimeapps_path)"), the lines: ${mimeapps_lines//$'\n'/, }"
fi
if (( ALL )); then
  if (( ${#settings[@]} + ${#dirs_settings[@]} )); then
    say "Your omdrop settings (--all):"
    list "${settings[@]}" "${dirs_settings[@]}"
  fi
  if (( ${#identity[@]} )); then
    say "Your AirDrop identity, kept unless you confirm it separately:"
    list "${identity[@]}" "${dirs_identity[@]}"
  fi
fi
say "Kept:"
say "    ~/Downloads and any other folder received files went to"
(( ALL )) || say "    ~/.omdrop (your AirDrop identity and private key), ~/.config/airdrop, ~/.config/omdrop, the receiver's drop-in"
say "    ~/.opendrop, if it exists (opendrop's, not omdrop's)"
for k in "${kept[@]}"; do say "    $k"; done
say

# --- Questions, all before anything changes ---------------------------------

ans_all=yes ans_id=no ans_owl=no ans_fw=no
answer() {  # VAR QUESTION [WORD]
  local -n out=$1; shift
  if (( DRY_RUN )); then ask "$@"; asked; out=dry
  elif ask "$@"; then out=yes
  else out=no
  fi
}
if (( ALL )); then
  if (( ${#settings[@]} + ${#dirs_settings[@]} )); then
    answer ans_all "Remove your omdrop settings listed above?"
    (( DRY_RUN )) && say "    (no stops there, and nothing is changed)"
    if [[ $ans_all == no ]]; then say; say "Nothing was changed."; exit 0; fi
  fi
  (( ${#identity[@]} )) && answer ans_id "Delete this computer's AirDrop identity, including its private key? Apple devices that know this computer will see a new one." delete
  (( owl )) && answer ans_owl "Also remove the omdrop-owl package (the radio backend)?"
  if pkg_installed omdrop && command -v firewall-cmd >/dev/null 2>&1; then
    answer ans_fw "Remove the firewalld rule omdrop may have added for port 8771 (uses sudo)?"
  fi
  say
fi
COND=''

# gate ANSWER LABEL: go ahead on yes; in a dry run, describe it as depending on the answer.
gate() {
  case $1 in
    yes) COND=''; return 0 ;;
    dry) COND=" if you answer yes to $2"; return 0 ;;
    *) return 1 ;;
  esac
}

# --- Do it ------------------------------------------------------------------

if gate "$ans_fw" "the firewall question"; then
  run omdrop firewall remove || warn "The firewall rule was not removed; the reason is above. Going on."
fi
COND=''

if (( receiving )); then
  say "Receiving is on; turning it off first."
  if command -v omdrop >/dev/null 2>&1; then
    run omdrop off || warn "omdrop off failed; stopping the service directly."
  fi
  if (( DRY_RUN )) || [[ "$(systemctl --user is-active "$UNIT" 2>/dev/null)" == active ]]; then
    COND=' if it is still running then'
    run systemctl --user stop "$UNIT" || warn "Could not stop $UNIT."
    COND=''
  fi
fi

remove=("${pkgs[@]}")
[[ $ans_owl == yes ]] && remove+=(omdrop-owl)
if (( ${#remove[@]} )); then
  # --no-autoremove: dependencies stay, so omdrop-owl leaves only when asked to.
  run sudo dnf remove --no-autoremove "${remove[@]}" \
    || die "dnf did not remove the packages; see above. Nothing in your home directory was changed."
fi
if [[ $ans_owl == dry ]]; then
  say "  (if you answer yes to the omdrop-owl question, omdrop-owl is added to that dnf command)"
fi

if (( ${#generated[@]} )) || [[ -n $mimeapps_lines ]]; then
  if (( unit_removed )) && (( have_systemctl )); then
    if (( DRY_RUN )) || [[ "$(systemctl --user is-enabled "$UNIT" 2>/dev/null)" == enabled ]]; then
      (( DRY_RUN )) && COND=' if the service is enabled'
      run systemctl --user disable "$UNIT" || warn "Could not disable $UNIT."
      COND=''
    fi
  fi
  for f in "${generated[@]}"; do
    run rm -f -- "$f" || warn "Could not remove $f."
  done
  if [[ -n $mimeapps_lines ]]; then
    if (( DRY_RUN )); then
      say "  would edit: $(tildify "$(mimeapps_path)"), removing only: ${mimeapps_lines//$'\n'/, }"
    else
      say "  editing $(tildify "$(mimeapps_path)"): removing ${mimeapps_lines//$'\n'/, }"
      mimeapps_drop_our_lines || warn "Could not edit $(mimeapps_path)."
    fi
  fi
  if (( unit_removed )) && (( have_systemctl )); then
    run systemctl --user daemon-reload || true
  fi
  if [[ -n ${REMOVING[$(link_app_path)]:-} ]] && command -v update-desktop-database >/dev/null 2>&1; then
    run update-desktop-database "$(data_home)/applications" || true
  fi
  if [[ -n ${REMOVING[$(link_mime_path)]:-} ]] && command -v update-mime-database >/dev/null 2>&1; then
    run update-mime-database "$(data_home)/mime" || true
  fi
fi

if (( ALL )) && gate "$ans_all" "the settings question"; then
  for f in "${settings[@]}"; do run rm -f -- "$f" || warn "Could not remove $f."; done
  for d in "${dirs_settings[@]}"; do run rmdir -- "$d" || warn "Could not remove the folder $d."; done
fi
COND=''

if gate "$ans_id" "the identity question"; then
  for f in "${identity[@]}"; do run rm -f -- "$f" || warn "Could not remove $f."; done
  for d in "${dirs_identity[@]}"; do run rmdir -- "$d" || warn "Could not remove the folder $d."; done
elif (( ${#identity[@]} )); then
  say "Kept your AirDrop identity in ~/.omdrop."
fi
COND=''

say
if (( DRY_RUN )); then
  say "Dry run finished. Nothing was changed."
else
  say "Done."
fi
