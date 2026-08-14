#!/usr/bin/env sh
# Usage: sync.sh news  or  sync.sh cci
set -eu

job="${1:?Pass news or cci}"
case "$job" in
  news|cci) ;;
  *) echo "Usage: $0 news|cci" >&2; exit 2 ;;
esac

docker exec -i company-portal-app python - "$job" <<'PY'
import os
import sys
from urllib.request import Request, urlopen

endpoint = f"http://127.0.0.1:8000/api/sync/{sys.argv[1]}"
request = Request(endpoint, data=b"", method="POST")
request.add_header("Authorization", f"Bearer {os.environ['CRON_SECRET']}")
with urlopen(request, timeout=120) as response:
    print(response.read().decode("utf-8"))
PY
