#!/bin/bash
# down.sh — stop and remove the staging containers. Volumes and every file
# under $STAGING_ROOT are kept (this script never passes -v; lib.sh refuses it).
#
# Usage: down.sh            the whole stack, bots included
#        down.sh service ... stop only those services (containers kept)

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
if [[ $# -gt 0 ]]; then
  staging_compose --profile bots stop "$@"
else
  staging_compose --profile bots down
fi
note "down; volumes ${STAGING_VOLUMES[*]} and $STAGING_ROOT are untouched"
