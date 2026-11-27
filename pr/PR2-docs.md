Title: Documented the "bucket" and "smooth" parameters of "least_time".

Documentation for the `bucket=` and `smooth=` parameters of the `least_time` directive, proposed in nginx/nginx#2050 (which builds on nginx/nginx#2048), in `ngx_http_upstream_module` and `ngx_stream_upstream_module`.

- **bucket** sets the precision with which average times are compared (20 ms by default, as today).
- **smooth** sets the time over which average times are smoothed (by default, each response has the weight of 0.05, as today).

The version "1.31.12" next to each parameter is a placeholder for the release that will include nginx/nginx#2050; I'll update it once that is known. Both files validate against `dtd/module.dtd`.
