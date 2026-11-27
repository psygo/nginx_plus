#!/usr/bin/perl

# Tests for the upstream least_work balancer module (prototype) with zone:
# learned costs, speeds and outstanding work are shared by worker processes.

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

# reuseport spreads connections over the worker processes

plan(skip_all => 'reuseport spreading needs Linux') unless $^O eq 'linux';

my $t = Test::Nginx->new()
	->has(qw/http proxy upstream_zone upstream_least_work rewrite map/)
	->write_file_expand('nginx.conf', <<'EOF');

%%TEST_GLOBALS%%

daemon off;
worker_processes 2;

events {
}

http {
    %%TEST_GLOBALS_HTTP%%

    upstream u1 {
        zone u1 1m;
        least_work key=$uri;
        server 127.0.0.1:8081;
        server 127.0.0.1:8082;
    }
    upstream u2 {
        zone u2 1m;
        least_work key=$uri;
        server 127.0.0.1:8081;
        server 127.0.0.1:8082;
    }

    # slow responses: limit_rate on a 2000 byte file (limit_rate releases
    # bytes on whole seconds); ?r=1 slows down the first backend only (about
    # 2s), /costly takes about 2s on either backend, /cheap is instant

    map $server_port:$arg_r $map_rate {
        %%PORT_8081%%:1    1000;
        default            0;
    }

    server {
        listen       127.0.0.1:8080 reuseport;
        server_name  localhost;

        location /u1/ {
            proxy_pass http://u1/;
        }
        location /u2/ {
            proxy_pass http://u2/;
        }
        location /pid {
            return 200 "pid $pid\n";
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
        }

        location /costly {
            add_header X-Port $server_port;
            alias %%TESTDIR%%/body.html;
            limit_rate 1000;
        }
    }
}

EOF

$t->write_file('index.html', '0123456789' x 200);
$t->write_file('body.html', '0123456789' x 200);
$t->try_run('no least_work')->plan(3);

###############################################################################

my ($p1, $p2) = (port(8081), port(8082));

# both worker processes take connections, else the tests below prove nothing

my %pids = map { http_get('/pid') =~ /pid (\d+)/; $1 => 1 } (1 .. 20);
is(scalar keys %pids, 2, 'both workers serve');

# what one worker learns, the other uses: the first backend answers in
# about 2s, the second at once; a single request teaches the route's cost,
# the next one (a tie, so round robin) the second server's speed; a worker
# learning alone would still find the servers alike and try the first one

http_get('/u1/?r=1');

is(many('/u1/?r=1', 10), "$p2: 10", 'learning shared');

# outstanding work is shared: a costly request in flight on one server,
# sent by either worker, keeps cheap requests from both workers away

http_get('/u2/cheap') for (1 .. 4);
http_get('/u2/costly');

my $costly = http_get('/u2/costly', start => 1);
select undef, undef, undef, 0.2;

# 20 sequential cheap requests, all within the costly one's 2s; a worker
# unaware of the costly request finds both servers idle and alternates

my @cheap = map { http_get('/u2/cheap') =~ /X-Port: (\d+)/; $1 } (1 .. 20);
my ($pb) = http_end($costly) =~ /X-Port: (\d+)/;

is(join(' ', grep { $_ == $pb } @cheap), '', 'outstanding work shared');

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

	my @keys = map { my $p = $_; grep { $p == $_ } keys %ports }
		($p1, $p2);
	return join ', ', map { $_ . ": " . $ports{$_} } @keys;
}

###############################################################################
