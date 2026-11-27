#!/usr/bin/perl

# Stream tests for the upstream least_work balancer module (prototype) with
# zone: learned costs, speeds and outstanding work are shared by worker
# processes.

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

# reuseport spreads connections over the worker processes

plan(skip_all => 'reuseport spreading needs Linux') unless $^O eq 'linux';

my $t = Test::Nginx->new()
	->has(qw/http stream stream_return stream_upstream_zone/)
	->has(qw/stream_upstream_least_work rewrite map/)
	->write_file_expand('nginx.conf', <<'EOF');

%%TEST_GLOBALS%%

daemon off;
worker_processes 2;

events {
}

stream {
    %%TEST_GLOBALS_STREAM%%

    upstream u1 {
        zone u1 1m;
        least_work key=$server_port;
        server 127.0.0.1:8081;
        server 127.0.0.1:8082;
    }
    upstream u2 {
        zone u2 1m;
        least_work key=$server_port;
        server 127.0.0.1:8081;
        server 127.0.0.1:8082;
    }

    server {
        listen      127.0.0.1:8084 reuseport;
        return      "pid $pid";
    }

    server {
        listen      127.0.0.1:8086 reuseport;
        proxy_pass  u1;
    }

    # routes of u2: cheap requests come to 8087, costly ones to 8088

    server {
        listen      127.0.0.1:8087 reuseport;
        listen      127.0.0.1:8088 reuseport;
        proxy_pass  u2;
    }
}

http {
    %%TEST_GLOBALS_HTTP%%

    # slow responses: limit_rate on a 2000 byte file (limit_rate releases
    # bytes on whole seconds); ?r=1 slows down the first backend only (about
    # 2s), /costly takes about 2s on either backend, /cheap is instant

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

my @ports = my ($p1, $p2) = (port(8081), port(8082));

# both worker processes take connections, else the tests below prove nothing

my %pids;

for (1 .. 20) {
	my ($s, $b) = (getconn(port(8084)), '');
	$s->sysread($b, 64);
	$pids{$1} = 1 if $b =~ /pid (\d+)/;
}

is(scalar keys %pids, 2, 'both workers serve');

# what one worker learns, the other uses: the first backend answers in
# about 2s, the second at once; a single session teaches the route's cost,
# the next one (a tie, so round robin) the second server's speed; a worker
# learning alone would still find the servers alike and try the first one

get('/?r=1', port(8086));

is(many('/?r=1', 10, port(8086)), "$p2: 10", 'learning shared');

# outstanding work is shared: a costly session in flight on one server,
# sent by either worker, keeps cheap sessions from both workers away

get('/cheap', port(8087)) for (1 .. 4);
get('/costly', port(8088));

my $costly = http_get('/costly', start => 1, socket => getconn(port(8088)));
select undef, undef, undef, 0.2;

# 20 sequential cheap sessions, all within the costly one's 2s; a worker
# unaware of the costly session finds both servers idle and alternates

my @cheap = map { get('/cheap', port(8087)) =~ /X-Port: (\d+)/; $1 } (1 .. 20);
my ($pb) = http_end($costly) =~ /X-Port: (\d+)/;

is(join(' ', grep { $_ == $pb } @cheap), '', 'outstanding work shared');

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
