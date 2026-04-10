#compdef owlclaw

_owlclaw() {
  local -a commands
  commands=(
    "init:Initialize config"
    "reload:Reload config"
    "db:Database commands"
    "skill:Skill commands"
    "memory:Memory commands"
    "agent:Agent commands"
    "trigger:Trigger template commands"
  )
  _describe "command" commands
}

compdef _owlclaw owlclaw
