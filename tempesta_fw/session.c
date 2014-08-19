/**
 *		Tempesta FW
 *
 * Handling client and server sessions (at OSI level 5 and higher).
 *
 * Copyright (C) 2012-2014 NatSys Lab. (info@natsys-lab.com).
 *
 * This program is free software; you can redistribute it and/or modify it
 * under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License,
 * or (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful, but WITHOUT
 * ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
 * FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along with
 * this program; if not, write to the Free Software Foundation, Inc., 59
 * Temple Place - Suite 330, Boston, MA 02111-1307, USA.
 */
#include "log.h"
#include "sched.h"
#include "session.h"

static struct kmem_cache *sess_cache;

/*
 * TODO create a new session (choose appropriate server connection) depending on
 * properties of user connection [TODO request ?] (cookie session,
 * client IP session, basic authentication, URL parameters,
 * HTTP header value etc.)
 */
TfwSession *
tfw_create_session(TfwClient *cli)
{
	TfwSession *sess;
	TfwServer *srv;

	srv = tfw_sched_get_srv();
	if (!srv) {
		TFW_ERR("Can't get an appropriate server for a session");
		return NULL;
	}

	sess = kmem_cache_alloc(sess_cache, GFP_ATOMIC);
	if (!sess)
		return NULL;

	sess->srv = srv;
	sess->cli = cli;

	return sess;
}

void
tfw_session_free(TfwSession *s)
{
	kmem_cache_free(sess_cache, s);
}

int
tfw_session_init(void)
{
	sess_cache = kmem_cache_create("tfw_sess_cache", sizeof(TfwSession),
				       0, 0, NULL);
	if (!sess_cache)
		return -ENOMEM;
	return 0;
}

void
tfw_session_exit(void)
{
	kmem_cache_destroy(sess_cache);
}

