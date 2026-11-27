#!/usr/bin/perl

# Tests for the upstream least_work balancer module (prototype): least
# outstanding work, with route costs and server speeds learned from
# response times.

###############################################################################

use warnings;
use strict;

use Test::More;

BEGIN { use FindBin; chdir($FindBin::Bin); }

use lib 'lib';
use Test::Nginx qw/ :DEFAULT http_end /;

###############################################################################

select STDERR; $| = 1;
select STDOUT; $| = 1;

plan(skip_all => 'win32') if $^O eq 'MSWin32';

my $t = Test::Nginx->new()
	->has(qw/http proxy upstream_least_work rewrite map/)
	->write_file_expand('nginx.conf', <<'EOF');

%%TEST_GLOBALS%%

daemon off;

events {
}

http {
    %%TEST_GLOBALS_HTTP%%

    # separate upstreams for sane statistics

    upstream u0 {
        least_work key=$uri;
        server 127.0.0.1:8081;
        server 127.0.0.1:8082;
    }
    upstream u1 {
        least_work key=$uri;
        server 127.0.0.1:8081;
        server 127.0.0.1:8082;
    }
    upstream u2 {
        least_work key=$uri;
        server 127.0.0.1:8081;
        server 127.0.0.1:8082;
    }

    # slow responses: limit_rate on a 2000 byte file (the first second's
    # worth is sent at once); ?r=1 slows down the first backend only (about
    # 1s), /cheap takes about 0.25s and /costly about 3s on either backend

    map $server_port:$arg_r $map_rate {
        %%PORT_8081%%:1    1000;
        default            0;
    }

    server {
        listen       127.0.0.1:8080;
        server_name  localhost;

        location /u0/ {
            proxy_pass http://u0/;
        }
        location /u1/ {
            proxy_pass http://u1/;
        }
        location /u2/ {
            proxy_pass http://u2/;
        }
    }

    server {
        listen       127.0.0.1:8081;
        listen       127.0.0.1:8082;
        server_name  localhost;

        keepalive_requests 1;

        location / {
            add_header X-Port $server_port;
            set $limit_rate $map_rate;
        }

        location /cheap {
            add_header X-Port $server_port;
            alias %%TESTDIR%%/body.html;
            limit_rate 1600;
        }

        location /costly {
            add_header X-Port $server_port;
            alias %%TESTDIR%%/body.html;
            limit_rate 500;
        }
    }
}

EOF

$t->write_file('index.html', '0123456789' x 200);
$t->write_file('body.html', '0123456789' x 200);
$t->try_run('no least_work')->plan(4);

###############################################################################

my (@s, $p);
my @ports = my ($p1, $p2) = (port(8081), port(8082));

# no statistics yet: all predictions are equal, expect round-robin

is(many('/u0/', 10), "$p1: 5, $p2: 5", 'no statistics, round-robin');

# server speed is learned from requests that found the server idle:
# the first backend answers in about 1s, the second at once

http_get('/u1/?r=1') for (1 .. 6);

is(many('/u1/?r=1', 10), "$p2: 10", 'slower server avoided');

# route costs: /cheap takes about 0.25s and /costly about 3s on either
# server; learn both, sequentially

http_get('/u2/cheap') for (1 .. 4);
http_get('/u2/costly') for (1 .. 2);

# one costly request in flight on one server, three cheap ones on the
# other: least_conn would now pick the server with one connection, but
# it has more outstanding work

my $costly = http_get('/u2/costly', start => 1);
select undef, undef, undef, 0.1;
@s = map { my $s = http_get('/u2/cheap', start => 1);
	select undef, undef, undef, 0.05; $s } (1 .. 3);

my $next = http_get('/u2/cheap');
my $busy = http_end($costly);
http_end $_ for @s;

my ($pn) = $next =~ /X-Port: (\d+)/;
my ($pb) = $busy =~ /X-Port: (\d+)/;

isnt($pn, $pb, 'least outstanding work, not least connections');

# a route never seen before (key "/u2/index.html") gets the average cost,
# and with equal servers the result stays spread

like(many('/u2/index.html', 10), qr/$p1: \d+, $p2: \d+/, 'unknown route');

###############################################################################

sub many {
	my ($uri, $count) = @_;
	my %ports;

	for (1 .. $count) {
		if (http_get($uri) =~ /X-Port: (\d+)/) {
			$ports{$1} = 0 unless defined $ports{$1};
			$ports{$1}++;
		}
	}

	my @keys = map { my $p = $_; grep { $p == $_ } keys %ports } @ports;
	return join ', ', map { $_ . ": " . $ports{$_} } @keys;
}

###############################################################################
