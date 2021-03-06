from helpers import control, tempesta, tf_cfg
from testers import stress

class MixedRequests(stress.StressTest):
    """ Mixed requests test """
    config = 'cache 0;\n'
    script = None
    clients_num = int(tf_cfg.cfg.get('General', 'concurrent_connections'))

    def create_servers(self):
        stress.StressTest.create_servers(self)
        for server in self.servers:
            server.config.set_return_code()

    def create_clients(self):
        self.wrk = control.Wrk()
        self.wrk.set_script(self.script)
        self.clients = [self.wrk]

    def test(self):
        """ Run mixed requests test """
        self.generic_test_routine(self.config)
