Title: Tests: least_time "bucket" and "smooth" parameters.

Tests for the `bucket=` and `smooth=` parameters of `least_time`, proposed in nginx/nginx#2050 (which builds on nginx/nginx#2048), in http and in stream.

The tests are in two new files, `upstream_least_time_bucket_smooth.t` and `stream_upstream_least_time_bucket_smooth.t`, rather than in `upstream_least_time.t` and `stream_upstream_least_time.t`. nginx without these parameters rejects the configuration, so this way only the new files are skipped there, and the existing least_time tests keep running.

- **bucket:** with `bucket=5s`, a server answering in about 4 s and one answering at once fall into the same bucket, so both keep being selected.
- **smooth:** with `smooth=10s`, the faster server takes every request once both have averages, as without the parameter.

Tested on Linux, gcc 14, run as root with `TEST_NGINX_GLOBALS="user root;"`:

- with nginx/nginx#2050: all `upstream*.t` and `stream_upstream*.t` pass (40 files, 435 tests), and the new tests also pass with `TEST_NGINX_UNSAFE=1`;
- with nginx master: the new files are skipped ("no least_time bucket and smooth"), and `upstream_least_time.t` and `stream_upstream_least_time.t` pass.
