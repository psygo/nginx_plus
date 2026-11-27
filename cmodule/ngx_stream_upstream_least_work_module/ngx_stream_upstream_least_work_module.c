
/*
 * least_work (prototype), stream: least outstanding work, with a cost per
 * route and a speed per server learned from session times.
 *
 *     least_work key=$server_port [alpha=0.02] [calibration=0.002];
 *
 * The same model as the http module, with sessions for requests: a
 * session's time (to its last byte) is the work it gives its server.
 *
 * A request of route r is predicted to take cost[r] / speed[p] on server p.
 * Only requests that found the server idle (no connections when sent)
 * teach cost and speed, since their response time is service time alone:
 *
 *     cost[r]   <- average of rt * speed[p]
 *     speed[p]  =  average of cost[r] / average of rt
 *
 * Every completed request teaches the calibration of its server, the ratio
 * of measured to predicted delay.  A request goes to the server with the
 * least calibration * (work in progress + predicted time); estimates within
 * 1 ms of the least (response times are whole milliseconds) are ties, broken
 * by weighted round robin.
 *
 * Responses measured as 0 ms teach too: the difference of two truncated
 * millisecond clocks is unbiased.  Averages of time are used as at least
 * 0.5 ms, so that a speed stays bounded.
 *
 * If the upstream has a zone, the learned state (route costs, server speeds
 * and calibrations, work in progress) is allocated there and shared by all
 * worker processes, under the lock that already protects the peers;
 * otherwise it is kept per worker process (like least_conn without zone).
 * It mirrors LeastWorkC in sim/explore/cost_routing.py.
 */


#include <ngx_config.h>
#include <ngx_core.h>
#include <ngx_stream.h>


#define NGX_STREAM_LW_ROUTES  1024          /* power of 2 */
#define NGX_STREAM_LW_PROBES  8
#define NGX_STREAM_LW_TIE     1.0           /* ms */
#define NGX_STREAM_LW_FLOOR   0.5           /* ms */


typedef struct {
    uint32_t                           hash;      /* 0: empty slot */
    double                             cost;      /* ms at speed 1 */
} ngx_stream_upstream_lw_route_t;


typedef struct {
    double                             num;       /* speed = num / den */
    double                             den;
    double                             gnum;      /* calibration: gnum / gden */
    double                             gden;
    double                             work;      /* predicted, in progress */
    unsigned                           learned:1;
    unsigned                           calibrated:1;
} ngx_stream_upstream_lw_state_t;


/* learned state: in the upstream zone if there is one */

typedef struct {
    double                             cost_all;
    ngx_uint_t                         cost_all_set;
    ngx_uint_t                         nstates;
    ngx_stream_upstream_lw_state_t    *states;
    ngx_stream_upstream_lw_route_t    *routes;
} ngx_stream_upstream_lw_data_t;


typedef struct {
    ngx_stream_complex_value_t        *key;
    double                             alpha;
    double                             cal_alpha;
    ngx_stream_upstream_lw_data_t     *data;
} ngx_stream_upstream_lw_conf_t;


typedef struct {
    /* the round robin data must be first */
    ngx_stream_upstream_rr_peer_data_t rrp;

    ngx_stream_upstream_lw_conf_t     *conf;
    ngx_stream_upstream_t             *upstream;
    uint32_t                           hash;
    ngx_int_t                          index;     /* -1: no state */
    double                             pred;
    double                             delay;
    ngx_uint_t                         conns;
} ngx_stream_upstream_lw_peer_data_t;


static ngx_int_t ngx_stream_upstream_init_least_work_peer(
    ngx_stream_session_t *s, ngx_stream_upstream_srv_conf_t *us);
static ngx_int_t ngx_stream_upstream_get_least_work_peer(
    ngx_peer_connection_t *pc, void *data);
static void ngx_stream_upstream_free_least_work_peer(ngx_peer_connection_t *pc,
    void *data, ngx_uint_t state);
static ngx_stream_upstream_lw_data_t *ngx_stream_upstream_lw_alloc(
    ngx_slab_pool_t *shpool, ngx_pool_t *pool, ngx_uint_t n);
static double ngx_stream_upstream_lw_speed(ngx_stream_upstream_lw_data_t *data,
    ngx_int_t i);
static double ngx_stream_upstream_lw_estimate(
    ngx_stream_upstream_lw_data_t *data, ngx_int_t i, double cost,
    double vdef, double *pred, double *work);
static ngx_stream_upstream_lw_route_t *ngx_stream_upstream_lw_route(
    ngx_stream_upstream_lw_data_t *data, uint32_t hash, ngx_uint_t create);
static void ngx_stream_upstream_lw_learn(
    ngx_stream_upstream_lw_peer_data_t *lwp, double rt);

static ngx_int_t ngx_stream_upstream_least_work_init_module(ngx_cycle_t *cycle);
static void *ngx_stream_upstream_least_work_create_conf(ngx_conf_t *cf);
static char *ngx_stream_upstream_least_work(ngx_conf_t *cf, ngx_command_t *cmd,
    void *conf);


static ngx_command_t  ngx_stream_upstream_least_work_commands[] = {

    { ngx_string("least_work"),
      NGX_STREAM_UPS_CONF|NGX_CONF_1MORE,
      ngx_stream_upstream_least_work,
      NGX_STREAM_SRV_CONF_OFFSET,
      0,
      NULL },

      ngx_null_command
};


static ngx_stream_module_t  ngx_stream_upstream_least_work_module_ctx = {
    NULL,                                  /* preconfiguration */
    NULL,                                  /* postconfiguration */

    NULL,                                  /* create main configuration */
    NULL,                                  /* init main configuration */

    ngx_stream_upstream_least_work_create_conf,
                                           /* create server configuration */
    NULL                                   /* merge server configuration */
};


ngx_module_t  ngx_stream_upstream_least_work_module = {
    NGX_MODULE_V1,
    &ngx_stream_upstream_least_work_module_ctx, /* module context */
    ngx_stream_upstream_least_work_commands, /* module directives */
    NGX_STREAM_MODULE,                     /* module type */
    NULL,                                  /* init master */
    ngx_stream_upstream_least_work_init_module, /* init module */
    NULL,                                  /* init process */
    NULL,                                  /* init thread */
    NULL,                                  /* exit thread */
    NULL,                                  /* exit process */
    NULL,                                  /* exit master */
    NGX_MODULE_V1_PADDING
};


static ngx_int_t
ngx_stream_upstream_init_least_work(ngx_conf_t *cf,
    ngx_stream_upstream_srv_conf_t *us)
{
    ngx_stream_upstream_rr_peers_t  *peers;
    ngx_stream_upstream_lw_conf_t *lwcf;

    ngx_log_debug0(NGX_LOG_DEBUG_STREAM, cf->log, 0, "init least work");

    if (ngx_stream_upstream_init_round_robin(cf, us) != NGX_OK) {
        return NGX_ERROR;
    }

    lwcf = ngx_stream_conf_upstream_srv_conf(us,
                                         ngx_stream_upstream_least_work_module);

    peers = us->peer.data;

    /* per worker process; moved to the zone, if any, at module init */

    lwcf->data = ngx_stream_upstream_lw_alloc(NULL, cf->pool, peers->number);
    if (lwcf->data == NULL) {
        return NGX_ERROR;
    }

    us->peer.init = ngx_stream_upstream_init_least_work_peer;

    return NGX_OK;
}


static ngx_stream_upstream_lw_data_t *
ngx_stream_upstream_lw_alloc(ngx_slab_pool_t *shpool, ngx_pool_t *pool,
    ngx_uint_t n)
{
    size_t                        size;
    ngx_stream_upstream_lw_data_t  *data;

    size = sizeof(ngx_stream_upstream_lw_data_t)
           + n * sizeof(ngx_stream_upstream_lw_state_t)
           + NGX_STREAM_LW_ROUTES * sizeof(ngx_stream_upstream_lw_route_t);

    data = shpool ? ngx_slab_calloc(shpool, size) : ngx_pcalloc(pool, size);
    if (data == NULL) {
        return NULL;
    }

    data->nstates = n;
    data->states = (ngx_stream_upstream_lw_state_t *) (data + 1);
    data->routes = (ngx_stream_upstream_lw_route_t *) (data->states + n);

    return data;
}


static ngx_int_t
ngx_stream_upstream_least_work_init_module(ngx_cycle_t *cycle)
{
#if (NGX_STREAM_UPSTREAM_ZONE)

    ngx_uint_t                      i;
    ngx_stream_upstream_lw_conf_t  *lwcf;
    ngx_stream_upstream_lw_data_t  *data;
    ngx_stream_upstream_rr_peers_t *peers;
    ngx_stream_upstream_srv_conf_t *uscf, **uscfp;
    ngx_stream_upstream_main_conf_t  *umcf;

    /*
     * shared memory zones are initialized by now, and the peers of an
     * upstream with zone are in its zone; this runs before worker processes
     * are started, so all of them see the same learned state
     */

    umcf = ngx_stream_cycle_get_module_main_conf(cycle,
                                                 ngx_stream_upstream_module);
    if (umcf == NULL) {
        return NGX_OK;
    }

    uscfp = umcf->upstreams.elts;

    for (i = 0; i < umcf->upstreams.nelts; i++) {
        uscf = uscfp[i];

        if (uscf->peer.init_upstream != ngx_stream_upstream_init_least_work
            || uscf->shm_zone == NULL)
        {
            continue;
        }

        peers = uscf->peer.data;

        if (peers->shpool == NULL) {
            continue;
        }

        data = ngx_stream_upstream_lw_alloc(peers->shpool, NULL, peers->number);
        if (data == NULL) {
            ngx_log_error(NGX_LOG_EMERG, cycle->log, 0,
                          "upstream zone \"%V\" is too small for least_work "
                          "in upstream \"%V\"",
                          &uscf->shm_zone->shm.name, &uscf->host);
            return NGX_ERROR;
        }

        lwcf = ngx_stream_conf_upstream_srv_conf(uscf,
                                         ngx_stream_upstream_least_work_module);
        lwcf->data = data;
    }

#endif

    return NGX_OK;
}


static ngx_int_t
ngx_stream_upstream_init_least_work_peer(ngx_stream_session_t *s,
    ngx_stream_upstream_srv_conf_t *us)
{
    ngx_str_t                          key;
    ngx_stream_upstream_lw_conf_t     *lwcf;
    ngx_stream_upstream_lw_peer_data_t  *lwp;

    ngx_log_debug0(NGX_LOG_DEBUG_STREAM, s->connection->log, 0,
                   "init least work peer");

    lwp = ngx_pcalloc(s->connection->pool,
                      sizeof(ngx_stream_upstream_lw_peer_data_t));
    if (lwp == NULL) {
        return NGX_ERROR;
    }

    s->upstream->peer.data = &lwp->rrp;

    if (ngx_stream_upstream_init_round_robin_peer(s, us) != NGX_OK) {
        return NGX_ERROR;
    }

    s->upstream->peer.get = ngx_stream_upstream_get_least_work_peer;
    s->upstream->peer.free = ngx_stream_upstream_free_least_work_peer;

    lwcf = ngx_stream_conf_upstream_srv_conf(us,
                                         ngx_stream_upstream_least_work_module);

    if (ngx_stream_complex_value(s, lwcf->key, &key) != NGX_OK) {
        return NGX_ERROR;
    }

    lwp->conf = lwcf;
    lwp->upstream = s->upstream;
    lwp->hash = ngx_crc32_long(key.data, key.len);
    lwp->index = -1;

    if (lwp->hash == 0) {
        lwp->hash = 1;
    }

    return NGX_OK;
}


static ngx_int_t
ngx_stream_upstream_get_least_work_peer(ngx_peer_connection_t *pc, void *data)
{
    ngx_stream_upstream_lw_peer_data_t *lwp = data;

    time_t                             now;
    double                             cost, vdef, pred, work, e, best_e,
                                       best_pred, best_work;
    uintptr_t                          m;
    ngx_int_t                          rc, total, k;
    ngx_uint_t                         i, n, p, ncand, round, primary;
    ngx_stream_upstream_rr_peer_t     *peer, *best;
    ngx_stream_upstream_rr_peers_t    *peers;
    ngx_stream_upstream_lw_data_t     *lwd;
    ngx_stream_upstream_lw_route_t    *route;
    ngx_stream_upstream_rr_peer_data_t  *rrp;

    ngx_log_debug1(NGX_LOG_DEBUG_STREAM, pc->log, 0,
                   "get least work peer, try: %ui", pc->tries);

    rrp = &lwp->rrp;
    lwd = lwp->conf->data;

    lwp->index = -1;

    if (rrp->peers->single) {
        return ngx_stream_upstream_get_round_robin_peer(pc, rrp);
    }

    pc->cached = 0;
    pc->connection = NULL;

    now = ngx_time();

    peers = rrp->peers;
    primary = (peers == lwp->upstream->upstream->peer.data);

    ngx_stream_upstream_rr_peers_wlock(peers);

#if (NGX_STREAM_UPSTREAM_ZONE)
    if (peers->config && rrp->config != *peers->config) {
        goto busy;
    }
#endif

    /*
     * the learned state is read and changed under the lock of the primary
     * peers; backup servers have no learned state and are equal here, which
     * leaves weighted round robin among them
     */

    cost = 0;
    vdef = 1;

    if (primary) {
        route = ngx_stream_upstream_lw_route(lwd, lwp->hash, 0);

        if (route) {
            cost = route->cost;

        } else if (lwd->cost_all_set) {
            cost = lwd->cost_all;
        }

        vdef = ngx_stream_upstream_lw_speed(lwd, -1);
    }

    /* the least estimate: calibration * (work in progress + predicted time) */

    best = NULL;
    best_e = 0;

    for (peer = peers->peer, i = 0;
         peer;
         peer = peer->next, i++)
    {
        n = i / (8 * sizeof(uintptr_t));
        m = (uintptr_t) 1 << i % (8 * sizeof(uintptr_t));

        if (rrp->tried[n] & m) {
            continue;
        }

        if (peer->down) {
            continue;
        }

        if (peer->max_fails
            && peer->fails >= peer->max_fails
            && now - peer->checked <= peer->fail_timeout)
        {
            continue;
        }

        if (peer->max_conns && peer->conns >= peer->max_conns) {
            continue;
        }

        k = primary ? (ngx_int_t) i : -1;
        e = ngx_stream_upstream_lw_estimate(lwd, k, cost, vdef, &pred, &work);

        ngx_log_debug5(NGX_LOG_DEBUG_STREAM, pc->log, 0,
                       "least work peer %ui: estimate %.3f, predicted %.3f, "
                       "work %.3f, cost %.3f", i, e, pred, work, cost);

        if (best == NULL || e < best_e) {
            best = peer;
            best_e = e;
        }
    }

    if (best == NULL) {
        ngx_log_debug0(NGX_LOG_DEBUG_STREAM, pc->log, 0,
                       "get least work peer, no peer found");

        goto failed;
    }

    /*
     * estimates within NGX_STREAM_LW_TIE of the least are ties: count them,
     * and if there are several, select among them by weighted round robin,
     * starting at the first one
     */

    best = NULL;
    total = 0;
    ncand = 0;

#if (NGX_SUPPRESS_WARN)
    p = 0;
    best_pred = 0;
    best_work = 0;
#endif

    for (round = 0; round < 2; round++) {

        for (peer = peers->peer, i = 0;
             peer;
             peer = peer->next, i++)
        {
            n = i / (8 * sizeof(uintptr_t));
            m = (uintptr_t) 1 << i % (8 * sizeof(uintptr_t));

            if (rrp->tried[n] & m) {
                continue;
            }

            if (peer->down) {
                continue;
            }

            if (peer->max_fails
                && peer->fails >= peer->max_fails
                && now - peer->checked <= peer->fail_timeout)
            {
                continue;
            }

            if (peer->max_conns && peer->conns >= peer->max_conns) {
                continue;
            }

            k = primary ? (ngx_int_t) i : -1;
            e = ngx_stream_upstream_lw_estimate(lwd, k, cost, vdef, &pred,
                                              &work);

            if (e > best_e + NGX_STREAM_LW_TIE) {
                continue;
            }

            if (round == 0) {
                if (ncand++ == 0) {
                    best = peer;
                    best_pred = pred;
                    best_work = work;
                    p = i;
                }

                continue;
            }

            peer->current_weight += peer->effective_weight;
            total += peer->effective_weight;

            if (peer->effective_weight < peer->weight) {
                peer->effective_weight++;
            }

            if (peer->current_weight > best->current_weight) {
                best = peer;
                best_pred = pred;
                best_work = work;
                p = i;
            }
        }

        if (ncand == 1) {
            break;
        }
    }

    best->current_weight -= total;

    if (now - best->checked > best->fail_timeout) {
        best->checked = now;
    }

    pc->sockaddr = best->sockaddr;
    pc->socklen = best->socklen;
    pc->name = &best->name;

    lwp->conns = best->conns;

    best->conns++;

    rrp->current = best;
    ngx_stream_upstream_rr_peer_ref(peers, best);

    n = p / (8 * sizeof(uintptr_t));
    m = (uintptr_t) 1 << p % (8 * sizeof(uintptr_t));

    rrp->tried[n] |= m;

    if (primary && p < lwd->nstates) {
        lwp->index = p;
        lwp->pred = best_pred;
        lwp->delay = best_work + best_pred;
        lwd->states[p].work += best_pred;
    }

    ngx_stream_upstream_rr_peers_unlock(peers);

    return NGX_OK;

failed:

    if (peers->next) {
        ngx_log_debug0(NGX_LOG_DEBUG_STREAM, pc->log, 0,
                       "get least work peer, backup servers");

        rrp->peers = peers->next;

        n = (rrp->peers->number + (8 * sizeof(uintptr_t) - 1))
                / (8 * sizeof(uintptr_t));

        for (i = 0; i < n; i++) {
             rrp->tried[i] = 0;
        }

        ngx_stream_upstream_rr_peers_unlock(peers);

        rc = ngx_stream_upstream_get_least_work_peer(pc, rrp);

        if (rc != NGX_BUSY) {
            return rc;
        }

        ngx_stream_upstream_rr_peers_wlock(peers);
    }

#if (NGX_STREAM_UPSTREAM_ZONE)
busy:
#endif

    ngx_stream_upstream_rr_peers_unlock(peers);

    pc->name = peers->name;

    return NGX_BUSY;
}


static void
ngx_stream_upstream_free_least_work_peer(ngx_peer_connection_t *pc,
    void *data, ngx_uint_t state)
{
    ngx_stream_upstream_lw_peer_data_t *lwp = data;

    ngx_stream_upstream_t             *u;
    ngx_stream_upstream_rr_peers_t    *peers;
    ngx_stream_upstream_lw_state_t    *st;

    ngx_log_debug0(NGX_LOG_DEBUG_STREAM, pc->log, 0, "free least work peer");

    if (lwp->index >= 0) {

        /* the peer was selected from the primary peers: lock them */

        peers = lwp->rrp.peers;

        ngx_stream_upstream_rr_peers_wlock(peers);

        st = &lwp->conf->data->states[lwp->index];

        st->work -= lwp->pred;

        if (st->work < 0) {
            st->work = 0;
        }

        u = lwp->upstream;

        /*
         * only successful attempts are accounted to mitigate preferring
         * of failing peers
         */

        if (!(state & (NGX_PEER_FAILED|NGX_PEER_NEXT))
            && u->state->response_time != (ngx_msec_t) -1)
        {
            ngx_stream_upstream_lw_learn(lwp,
                                         (double) u->state->response_time);
        }

        ngx_stream_upstream_rr_peers_unlock(peers);

        lwp->index = -1;
    }

    ngx_stream_upstream_free_round_robin_peer(pc, &lwp->rrp, state);
}


static double
ngx_stream_upstream_lw_speed(ngx_stream_upstream_lw_data_t *data, ngx_int_t i)
{
    double      sum;
    ngx_uint_t  j, n;

    if (i >= 0 && (ngx_uint_t) i < data->nstates && data->states[i].learned) {
        return data->states[i].num
               / ngx_max(data->states[i].den, NGX_STREAM_LW_FLOOR);
    }

    /* not learned yet: the mean of the learned speeds, on their scale */

    sum = 0;
    n = 0;

    for (j = 0; j < data->nstates; j++) {
        if (data->states[j].learned) {
            sum += data->states[j].num
                   / ngx_max(data->states[j].den, NGX_STREAM_LW_FLOOR);
            n++;
        }
    }

    return n ? sum / n : 1;
}


static double
ngx_stream_upstream_lw_estimate(ngx_stream_upstream_lw_data_t *data,
    ngx_int_t i, double cost, double vdef, double *pred, double *work)
{
    double                         v, cal;
    ngx_stream_upstream_lw_state_t  *st;

    v = vdef;
    cal = 1;
    *work = 0;

    if (i >= 0 && (ngx_uint_t) i < data->nstates) {
        st = &data->states[i];

        if (st->learned) {
            v = st->num / ngx_max(st->den, NGX_STREAM_LW_FLOOR);
        }

        if (st->calibrated) {
            cal = ngx_max(st->gnum, NGX_STREAM_LW_FLOOR)
                  / ngx_max(st->gden, NGX_STREAM_LW_FLOOR);
        }

        *work = st->work;
    }

    *pred = cost / v;

    return cal * (*work + *pred);
}


static ngx_stream_upstream_lw_route_t *
ngx_stream_upstream_lw_route(ngx_stream_upstream_lw_data_t *data, uint32_t hash,
    ngx_uint_t create)
{
    ngx_uint_t                     k, j;
    ngx_stream_upstream_lw_route_t  *r;

    for (k = 0; k < NGX_STREAM_LW_PROBES; k++) {
        j = (hash + k) & (NGX_STREAM_LW_ROUTES - 1);
        r = &data->routes[j];

        if (r->hash == hash) {
            return r;
        }

        if (r->hash == 0) {
            if (!create) {
                return NULL;
            }

            r->hash = hash;
            r->cost = 0;
            return r;
        }
    }

    if (!create) {
        return NULL;
    }

    /* all probed slots taken: replace the last one */

    r->hash = hash;
    r->cost = 0;

    return r;
}


static void
ngx_stream_upstream_lw_learn(ngx_stream_upstream_lw_peer_data_t *lwp, double rt)
{
    double                          a, v, x, k_old;
    ngx_uint_t                      known;
    ngx_stream_upstream_lw_conf_t  *lwcf;
    ngx_stream_upstream_lw_data_t  *data;
    ngx_stream_upstream_lw_route_t *route;
    ngx_stream_upstream_lw_state_t *st;

    lwcf = lwp->conf;
    data = lwcf->data;
    st = &data->states[lwp->index];

    ngx_log_debug5(NGX_LOG_DEBUG_STREAM, lwp->upstream->peer.log, 0,
                   "least work learn peer %i: rt %.0f, conns %ui, "
                   "speed %.3f/%.3f", lwp->index, rt, lwp->conns,
                   st->num, st->den);

    /* calibration: every completed request */

    if (lwp->delay > 0) {
        a = lwcf->cal_alpha;

        if (!st->calibrated) {
            st->gnum = lwp->delay;
            st->gden = lwp->delay;
            st->calibrated = 1;
        }

        st->gnum += a * (rt - st->gnum);
        st->gden += a * (lwp->delay - st->gden);
    }

    /* cost and speed: only requests that found the server idle */

    if (lwp->conns != 0 || rt < 0) {
        return;
    }

    a = lwcf->alpha;
    v = ngx_stream_upstream_lw_speed(data, lwp->index);
    x = rt * v;

    route = ngx_stream_upstream_lw_route(data, lwp->hash, 0);
    known = (route != NULL);
    k_old = known ? route->cost : 0;

    if (!known) {
        route = ngx_stream_upstream_lw_route(data, lwp->hash, 1);
        route->cost = x;

    } else {
        route->cost += a * (x - route->cost);
    }

    if (data->cost_all_set) {
        data->cost_all += a * (x - data->cost_all);

    } else {
        data->cost_all = x;
        data->cost_all_set = 1;
    }

    if (!known || k_old <= 0) {
        /* a new route, or a zero cost, says nothing about speed */
        return;
    }

    if (!st->learned) {
        /* start at the current speed, then move by alpha */
        st->num = k_old;
        st->den = k_old / v;
        st->learned = 1;
    }

    st->num += a * (k_old - st->num);
    st->den += a * (rt - st->den);
}


static void *
ngx_stream_upstream_least_work_create_conf(ngx_conf_t *cf)
{
    ngx_stream_upstream_lw_conf_t  *conf;

    conf = ngx_pcalloc(cf->pool, sizeof(ngx_stream_upstream_lw_conf_t));
    if (conf == NULL) {
        return NULL;
    }

    /*
     * set by ngx_pcalloc():
     *
     *     conf->key = NULL;
     *     conf->data = NULL;
     */

    conf->alpha = 0.02;
    conf->cal_alpha = 0.002;

    return conf;
}


static char *
ngx_stream_upstream_least_work(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    ngx_stream_upstream_lw_conf_t *lwcf = conf;

    ngx_int_t                          v;
    ngx_str_t                         *value, s;
    ngx_uint_t                         i;
    ngx_stream_upstream_srv_conf_t    *uscf;
    ngx_stream_compile_complex_value_t ccv;

    uscf = ngx_stream_conf_get_module_srv_conf(cf, ngx_stream_upstream_module);

    if (uscf->peer.init_upstream) {
        ngx_conf_log_error(NGX_LOG_WARN, cf, 0,
                           "load balancing method redefined");
    }

    value = cf->args->elts;

    for (i = 1; i < cf->args->nelts; i++) {

        if (ngx_strncmp(value[i].data, "key=", 4) == 0) {

            s.len = value[i].len - 4;
            s.data = value[i].data + 4;

            lwcf->key = ngx_palloc(cf->pool,
                                   sizeof(ngx_stream_complex_value_t));
            if (lwcf->key == NULL) {
                return NGX_CONF_ERROR;
            }

            ngx_memzero(&ccv, sizeof(ngx_stream_compile_complex_value_t));

            ccv.cf = cf;
            ccv.value = &s;
            ccv.complex_value = lwcf->key;

            if (ngx_stream_compile_complex_value(&ccv) != NGX_OK) {
                return NGX_CONF_ERROR;
            }

            continue;
        }

        if (ngx_strncmp(value[i].data, "alpha=", 6) == 0) {

            v = ngx_atofp(value[i].data + 6, value[i].len - 6, 3);

            if (v <= 0 || v > 1000) {
                goto invalid;
            }

            lwcf->alpha = (double) v / 1000;
            continue;
        }

        if (ngx_strncmp(value[i].data, "calibration=", 12) == 0) {

            v = ngx_atofp(value[i].data + 12, value[i].len - 12, 4);

            if (v <= 0 || v > 10000) {
                goto invalid;
            }

            lwcf->cal_alpha = (double) v / 10000;
            continue;
        }

        goto invalid;
    }

    if (lwcf->key == NULL) {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                           "\"least_work\" requires the \"key\" parameter");
        return NGX_CONF_ERROR;
    }

    uscf->peer.init_upstream = ngx_stream_upstream_init_least_work;

    uscf->flags = NGX_STREAM_UPSTREAM_CREATE
                  |NGX_STREAM_UPSTREAM_WEIGHT
                  |NGX_STREAM_UPSTREAM_MAX_CONNS
                  |NGX_STREAM_UPSTREAM_MAX_FAILS
                  |NGX_STREAM_UPSTREAM_FAIL_TIMEOUT
                  |NGX_STREAM_UPSTREAM_DOWN
                  |NGX_STREAM_UPSTREAM_BACKUP;

    return NGX_CONF_OK;

invalid:

    ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                       "invalid parameter \"%V\"", &value[i]);

    return NGX_CONF_ERROR;
}
