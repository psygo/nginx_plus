#!/bin/sh
# Build the least_work prototype for the benchmark (inside the container):
# nginx master + cmodule/ngx_http_upstream_least_work_module, with the same
# flags as the other benchmark builds (build.sh), and the load generator with
# the "classes" distribution (backward compatible with the other ones).
set -e
B=/w/wt/cost-routing
mkdir -p /build/bin
cd $B/bench/linux
go build -o /build/bin/loadgen loadgen.go
rm -rf /build/lw-bench && mkdir -p /build/lw-bench
git -c safe.directory="*" -C /w/nginx archive origin/master | tar -x -C /build/lw-bench
cd /build/lw-bench
./auto/configure --with-stream --with-cc-opt='-O2' --without-http_gzip_module \
    --without-http_rewrite_module --with-threads \
    --add-module=$B/cmodule/ngx_http_upstream_least_work_module > /build/lw.configure.log 2>&1
make -j"$(nproc)" > /build/lw.make.log 2>&1
cp objs/nginx /build/bin/nginx-lw
echo "built lw: $(md5sum objs/nginx | cut -c1-12)"
