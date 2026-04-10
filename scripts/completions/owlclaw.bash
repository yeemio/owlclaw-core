# Bash completion for owlclaw CLI.
# Usage: source scripts/completions/owlclaw.bash

_owlclaw_complete() {
  local cur prev words cword
  _init_completion || return
  COMPREPLY=($(compgen -W "init reload db skill memory agent trigger" -- "$cur"))
}

complete -F _owlclaw_complete owlclaw
