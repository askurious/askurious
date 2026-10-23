#!/usr/bin/env bash
# Source after cd to deploy/docker. One opt-in selection for deploy, cron, backup and manual operations.
isolation_value() {
  local v
  v=$(printenv "$1" || true)
  [ -n "$v" ] || v=$(sed -n "s/^$1=//p" .env 2>/dev/null | tail -n 1 | tr -d '\r' || true)
  v=${v%\"}; v=${v#\"}; v=${v%\'}; v=${v#\'}
  printf '%s' "$v"
}
ISOLATION_ACTIVE=0
case "$(isolation_value DB_ISOLATION)" in
  ''|0)
    case "$(isolation_value COMPOSE_FILE)" in
      *isolation/roles.yaml*) echo 'Isolation overrides require DB_ISOLATION=1.' >&2; return 1 ;;
    esac
    ;;
  1)
    ISOLATION_ACTIVE=1
    # Never store absolute checkout paths in .env: deploy/rollback move that folder.
    expected="$PWD/compose.yaml:$PWD/isolation/roles.yaml:$PWD/isolation/networks.yaml"
    case "$(isolation_value COMPOSE_FILE)" in
      ''|'compose.yaml:isolation/roles.yaml:isolation/networks.yaml'|"$expected"|"$expected:$PWD/isolation/transition.yaml") ;;
      *) echo 'DB_ISOLATION manages COMPOSE_FILE; remove the custom value first.' >&2; return 1 ;;
    esac
    export COMPOSE_FILE="$expected"
    unset expected
    ;;
  *) echo 'DB_ISOLATION must be 0 or 1.' >&2; return 1 ;;
esac
export ISOLATION_ACTIVE
