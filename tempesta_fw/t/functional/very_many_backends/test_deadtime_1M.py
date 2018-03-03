__author__ = 'Tempesta Technologies, Inc.'
__copyright__ = 'Copyright (C) 2017 Tempesta Technologies, Inc.'
__license__ = 'GPL2'

from helpers import control, tempesta, nginx, tf_cfg, deproxy, chains, remote
from . import multi_backend

import unittest
import threading
import time
import select
import asyncore

class AddingBackendNewSG(unittest.TestCase):
    """ 1 backend in server group """
    num_attempts = 10
    max_deadtime = 1
    num_extra_interfaces = 8
    num_extra_ports = 32
    ips = []
    normal_servers = 0
    config = "cache 0;\n"
    long_deadtime = 0
    short_deadtime = 0
    base_port = 16384
    wait = True

    def create_tempesta(self):
        """ Normally no override is needed.
        Create controller for TempestaFW and add all servers to default group.
        """
        self.tempesta = control.Tempesta()

    def setUp(self):
        self.client = None
        self.tempesta = None
        self.servers = []
        self.create_tempesta()
        tf_cfg.dbg(2, "Creating interfaces")
        self.interface = tf_cfg.cfg.get('Server', 'aliases_interface')
        self.base_ip = tf_cfg.cfg.get('Server',   'aliases_base_ip')
        self.ips = multi_backend.create_interfaces(self.interface,
                           self.base_ip, self.num_extra_interfaces + 1)

    def tearDown(self):
        unittest.TestCase.tearDown(self)
        asyncore.close_all()
        if self.client:
            self.client.stop("Client")
        if self.tempesta:
            self.tempesta.stop("Tempesta")
        for server in self.servers:
            server.stop("Server")
        tf_cfg.dbg(2, "Removing interfaces")
        multi_backend.remove_interface(self.interface, self.ips[0])
        self.ips = []

    def configure_tempesta(self):
        """ Configure tempesta 1 port in group """
        sg = tempesta.ServerGroup('default')
        server = self.servers[0]
        sg.add_server(server.ip, server.config.listeners[0].port,
                      server.conns_n)
        self.tempesta.config.add_sg(sg)
        self.append_extra_server_groups()
        return

    def append_server_group(self, id):
        sg = tempesta.ServerGroup('new-%i' % id)
        server = self.servers[1]
        for listener in server.config.listeners:
            sg.add_server(server.ip, listener.port, server.conns_n)
        self.tempesta.config.add_sg(sg)

    def append_extra_server_groups(self):
        sgid = 0
        for ifc in range(self.num_extra_interfaces):
            server = self.servers[self.extra_servers_base + ifc]
            for listener in server.config.listeners:
                sg = tempesta.ServerGroup('extra-%i' % sgid)
                sg.add_server(server.ip, listener.port, server.conns_n)
                self.tempesta.config.add_sg(sg)
                sgid += 1

    def create_client(self):
        """ Override to set desired list of benchmarks and their options. """
        self.client = deproxy.Client()
        self.client.set_tester(self)
        self.message_chains = [chains.base()]

    def is_srvs_ready(self):
        return True

    def setup_nginx_config(self, config):
        config.enable_multi_accept()
        config.set_worker_connections(32768)
        config.set_workers(4096)
        config.set_worker_rlimit_nofile(16384)
        config.set_ka(timeout = 180)
        for listener in config.listeners:
            listener.backlog = 9000
        config.build_config()
        return

    def create_servers(self):
        self.servers = []
        #default server
        defport=tempesta.upstream_port_start_from()
        server = control.Nginx(listen_port=defport)
        self.setup_nginx_config(server.config)
        self.servers.append(server)

        server = control.Nginx(listen_port=self.base_port,
                               listen_ports=self.num_attempts,
                               listen_ip=self.ips[0])
        self.setup_nginx_config(server.config)
        self.servers.append(server)

        self.extra_servers_base = len(self.servers)
        for ifc in range(self.num_extra_interfaces):
            server = control.Nginx(listen_port=self.base_port,
                                   listen_ports=self.num_extra_ports,
                                   listen_ip=self.ips[ifc + 1])
            self.setup_nginx_config(server.config)
            self.servers.append(server)

    def loop(self, timeout=None):
        """Poll for socket events no more than `self.timeout`
           or `timeout` seconds."""
        timeout = 1
        self.wait = True
        try:
            eta = time.time() + timeout
            s_map = asyncore.socket_map

            if hasattr(select, 'poll'):
                poll_fun = asyncore.poll2
            else:
                poll_fun = asyncore.poll

            while self.wait and (eta > time.time()):
                poll_fun(eta - time.time(), s_map)
        except asyncore.ExitNow:
            pass

    def recieved_response(self, response):
        self.wait = False

    def run_test(self):
        for self.current_chain in self.message_chains:
            self.recieved_chain = deproxy.MessageChain.empty()
            self.client.clear()
            self.client.set_request(self.current_chain.request)
            self.loop()

    @staticmethod
    def run_tester(self, timeout):
        start_time = time.time()
        success_time = start_time
        curtime = start_time
        while curtime - start_time < timeout:
            self.run_test()
            curtime = time.time()
            if True:
                if curtime - success_time > self.max_deadtime:
                    self.long_deadtime += 1
                else:
                    self.short_deadtime += 1
                success_time = curtime
            if curtime - start_time > timeout:
                break

        if curtime - success_time > self.max_deadtime:
            self.long_deadtime += 1

    def pre_test(self):
        self.tempesta.config.set_defconfig(self.config)
        self.create_servers()
        self.configure_tempesta()
        for server in self.servers:
            server.start()

        remote.tempesta.run_cmd("sysctl -w net.core.somaxconn=8192")
        remote.tempesta.run_cmd("sysctl -w net.ipv4.tcp_max_orphans=1000000")

        self.tempesta.start()
        self.create_client()
        self.client.start()

        self.long_deadtime = 0
        self.short_deadtime = 0

        self.thr = threading.Thread(target=self.run_tester,
                        args=(self, 2*self.num_attempts*self.max_deadtime))
        self.thr.start()

    def post_test(self):
        self.thr.join(timeout=1)
        tf_cfg.dbg(2, "LONG: %i, SHORT: %i" %
                   (self.long_deadtime, self.short_deadtime))
        assert self.long_deadtime == 0, 'Too long deadtime'

    def test(self):
        self.pre_test()
        for i in range(self.num_attempts):
            self.append_server_group(i)
            self.tempesta.reload()
            time.sleep(self.max_deadtime)
        self.post_test()

class RemovingBackendSG(AddingBackendNewSG):
    num_attempts = 10
    max_deadtime = 1

    def remove_server_group(self, id):
        self.tempesta.config.remove_sg("new-%i" % id)

    def configure_tempesta(self):
        """ Configure tempesta 1 port in group """
        sg = tempesta.ServerGroup('default')
        server = self.servers[0]
        sg.add_server(server.ip, server.config.listeners[0].port,
                      server.conns_n)
        self.tempesta.config.add_sg(sg)
        self.append_extra_server_groups()
        for i in range(self.num_attempts):
            self.append_server_group(i)
        return

    def test(self):
        self.pre_test()
        for i in range(self.num_attempts):
            self.remove_server_group(i)
            self.tempesta.reload()
            time.sleep(self.max_deadtime)
        self.post_test()

class ChangingSG(AddingBackendNewSG):
    num_attempts = 10
    max_deadtime = 1
    def_sg = None

    def configure_tempesta(self):
        """ Configure tempesta 1 port in group """
        sg = tempesta.ServerGroup('default')
        self.def_sg = sg
        server = self.servers[0]
        sg.add_server(server.ip, server.config.listeners[0].port,
                      server.conns_n)
        self.tempesta.config.add_sg(sg)
        self.append_extra_server_groups()
        return

    def test(self):
        self.pre_test()
        for i in range(self.num_attempts):
            server = self.servers[1]
            self.def_sg.add_server(server.ip,
                server.config.listeners[i].port, server.conns_n)
            self.tempesta.reload()
            time.sleep(self.max_deadtime)
        self.post_test()
