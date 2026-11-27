#!/usr/bin/perl

# Stream tests for the upstream least_work balancer module (prototype):
# least outstanding work, with route costs and server speeds learned from
# session times.

###############################################################################

use warnings;
use strict;

use Test::More;

use IO::Socket::INET;

BEGIN { use FindBin; chdir($FindBin::Bin); }

use lib 'lib';
use Test::Nginx qw/ :DEFAULT http_end /;

###############################################################################

select STDERR; $| = 1;
select STDOUT; $| = 1;

plan(skip_all => 'win32') if $^O eq 'MSWin32';

my $t = Test::Nginx->new()
	->has(qw/http stream stream_upstream_least_work rewrite map/)
	->write_file_expand('nginx.conf', <<'EOF');

%%TEST_GLOBALS%%

daemon off;

events {
}

stream {
    %%TEST_GLOBALS_STREAM%%

    # separate upstreams for sane statistics; the route is the port
    # the client connected to

    upstream u0 {
        least_work key=$server_port;
        server 127.0.0.1:8081;
        server 127.0.0.1:8082;
    }
    upstream u1 {
        least_work key=$server_port;
        server 127.0.0.1:8081;
        server 127.0.0.1:8082;
    }
    upstream u2 {
        least_work key=$server_port;
        server 127.0.0.1:8081;
        server 127.0.0.1:8082;
    }

    server {
        listen      127.0.0.1:8085;
        proxy_pass  u0;
    }

    server {
        listen      127.0.0.1:8086;
        proxy_pass  u1;
    }

    # routes of u2: cheap requests come to 8087, costly ones to 8088,
    # and 8089 is never used before the last test

    server {
        listen      127.0.0.1:8087;
        listen      127.0.0.1:8088;
        listen      127.0.0.1:8089;
        proxy_pass  u2;
    }
}

http {
    %%TEST_GLOBALS_HTTP%%

    # slow responses: limit_rate on a 2000 byte file (limit_rate releases
    # bytes on whole seconds); ?r=1 slows down the first backend only (about
    # 2s), /cheap takes about 1s and /costly about 4s on either backend

    map $server_port:$arg_r $map_rate {
        %%PORT_8081%%:1    1000;
        default            0;
    }

    server {
        listen       127.0.0.1:8081;
        listen       127.0.0.1:8082;
        server_name  localhost;

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

my @ports = my ($p1, $p2) = (port(8081), port(8082));

# no statistics yet: all predictions are equal, expect round-robin

is(many('/', 10, port(8085)), "$p1: 5, $p2: 5", 'no statistics, round-robin');

# server speed is learned from sessions that found the server idle:
# the first backend answers in about 2s, the second at once

get('/?r=1', port(8086)) for (1 .. 6);

is(many('/?r=1', 10, port(8086)), "$p2: 10", 'slower server avoided');

# route costs: cheap sessions take about 1s and costly ones about 4s on
# either server; learn both, sequentially

get('/cheap', port(8087)) for (1 .. 4);
get('/costly', port(8088)) for (1 .. 2);

# one costly session in flight on one server, three cheap ones on the
# other: least_conn would now pick the server with one connection, but
# it has more outstanding work

my $costly = http_get('/costly', start => 1, socket => getconn(port(8088)));
select undef, undef, undef, 0.1;
my @s = map {
	my $s = http_get('/cheap', start => 1, socket => getconn(port(8087)));
	select undef, undef, undef, 0.05; $s
} (1 .. 3);

my $next = get('/cheap', port(8087));
my $busy = http_end($costly);
http_end $_ for @s;

my ($pn) = $next =~ /X-Port: (\d+)/;
my ($pb) = $busy =~ /X-Port: (\d+)/;

isnt($pn, $pb, 'least outstanding work, not least connections');

# a route never seen before (port 8089) gets the average cost, and with
# equal servers the result stays spread

like(many('/', 10, port(8089)), qr/$p1: \d+, $p2: \d+/, 'unknown route');

###############################################################################

sub getconn {
	my ($port) = @_;
	my $s = IO::Socket::INET->new(
		Proto => 'tcp',
		PeerAddr => "127.0.0.1:$port"
	)
		or die "Can't connect to nginx: $!\n";

	return $s;
}

sub get {
	my ($uri, $port) = @_;
	return http_get($uri, socket => getconn($port));
}

sub many {
	my ($uri, $count, $port) = @_;
	my %ports;

	for (1 .. $count) {
		if (get($uri, $port) =~ /X-Port: (\d+)/) {
			$ports{$1} = 0 unless defined $ports{$1};
			$ports{$1}++;
		}
	}

	my @keys = map { my $p = $_; grep { $p == $_ } keys %ports } @ports;
	return join ', ', map { $_ . ": " . $ports{$_} } @keys;
}

###############################################################################
