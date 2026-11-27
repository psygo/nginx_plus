#!/bin/sh
# Build nginx master + the least_work prototype module (debug, for nginx-tests),
# then run its tests with an nginx-tests checkout at /tmp/ntm.
#   lw-build.sh [test]     (inside the ltbench container)
set -e
M=/w/wt/cost-routing/cmodule
rm -rf /build/lw && mkdir -p /build/lw /build/bin
git -c safe.directory="*" -C /w/nginx archive origin/master | tar -x -C /build/lw
cd /build/lw
./auto/configure --builddir=objs-t --with-stream --with-threads --with-debug \
    --add-module=$M/ngx_http_upstream_least_work_module > configure.log 2>&1
make -j12 -f objs-t/Makefile > make.log 2>&1 || { grep -E "error|warning" make.log | head -20; exit 1; }
cp objs-t/nginx /build/bin/nginx-lw
echo "built nginx-lw $(md5sum objs-t/nginx | cut -c1-10)"
if [ "$1" = test ]; then
  cp $M/t/upstream_least_work*.t /tmp/ntm/
  cd /tmp/ntm && TEST_NGINX_GLOBALS="user root;" TEST_NGINX_BINARY=/build/bin/nginx-lw \
      prove -v upstream_least_work.t upstream_least_work_zone.t 2>&1 | grep -E "^(ok|not ok)|Result|Failed|skipped|#   (got|expected)"
fi
