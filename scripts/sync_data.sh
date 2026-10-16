#!/bin/bash
# RETIRED (issue #305). This script used to push data files from a Mac to
# production. Production owns its data; any sync runs production to dev only,
# never dev or a Mac to production. #99's Mac-to-production step stays
# superseded.
#
# See planning-studio/initiatives/defect_curator_persistence_305_2026-10-15.md
# (section 3 C). Restores come from a production snapshot or backup, never
# from a local copy.
echo "sync_data.sh is retired: production data is never pushed from a local copy (#305)." >&2
echo "See planning-studio/initiatives/defect_curator_persistence_305_2026-10-15.md" >&2
exit 1
