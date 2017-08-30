""" Controlls node over SSH if remote, or via OS if local one. """

from __future__ import print_function
import re
import os
import abc
import paramiko
import errno
import subprocess32 as subprocess
import select
import fcntl
import threading
from time import sleep
from . import tf_cfg, error

__author__ = 'Tempesta Technologies, Inc.'
__copyright__ = 'Copyright (C) 2017 Tempesta Technologies, Inc.'
__license__ = 'GPL2'

# Don't remove files from remote node. Helpful for tests development.
DEBUG_FILES = False
# Default timeout for SSH sessions and command processing.
DEFAULT_TIMEOUT = 5

def _set_nonblock_flag(fd, nonblock=True):
    nonblock_flag = os.O_NONBLOCK
    old = fcntl.fcntl(fd, fcntl.F_GETFL)
    if nonblock:
        fcntl.fcntl(fd, fcntl.F_SETFL, old | nonblock_flag)
    else:
        fcntl.fcntl(fd, fcntl.F_SETFL, old & ~nonblock_flag)

def _set_cloexec_flag(fd, cloexec=True):
    cloexec_flag = fcntl.FD_CLOEXEC
    old = fcntl.fcntl(fd, fcntl.F_GETFD)
    if cloexec:
        fcntl.fcntl(fd, fcntl.F_SETFD, old | cloexec_flag)
    else:
        fcntl.fcntl(fd, fcntl.F_SETFD, old & ~cloexec_flag)

def _pipe_file():
    try:
        pipe_fds = os.pipe()
        map(_set_cloexec_flag, pipe_fds)
        pipe_files = map(os.fdopen, pipe_fds, ('r', 'w'))
        return pipe_files
    except:
        map(os.close, pipe_fds)
        raise

def _read_thread_primitive(buffer, file):
    buffer.append(file.read())

#
# Read from multiple fds, multiplexed with poll(), until EOF on _some_ of these
# fds.
#
def _read_thread_poll(*file2buf2waiteof):
    fd2file = dict()
    fd2buffer = dict()
    # fds for which we need an eof
    fd2eof = set()
    poller = select.poll()

    for file, buffer, wait_eof in file2buf2waiteof:
        if hasattr(file, 'channel'):
            # workaround for paramiko's ChannelFile
            fd = file.channel.fileno()
        else:
            fd = file.fileno()
        fd2file[fd] = file
        fd2buffer[fd] = buffer
        # build a full set and an inverse set
        if wait_eof:
            fd2eof.add(fd)
        _set_nonblock_flag(fd)
        poller.register(fd, select.POLLIN)

    while fd2eof:
        try:
            ready = poller.poll()
        except select.error as e:
            if e.errno == errno.EINTR:
                continue
            raise

        for fd, revents in ready:
            if revents & select.POLLIN:
                fd2buffer[fd].append(fd2file[fd].read())
            elif revents & select.POLLHUP:
                # this `elif` is important because we do not want to unwatch an
                # fd which still has data
                fd2eof.discard(fd)
                poller.unregister(fd)
                fd2file.pop(fd).close()

    for fd, file_obj in fd2file.iteritems():
        poller.unregister(fd)
        file_obj.close()

class Node(object):
    __metaclass__ = abc.ABCMeta

    def __init__(self, type, hostname, workdir):
        self.host = hostname
        self.workdir = workdir
        self.type = type

    def is_remote(self):
        return self.host != 'localhost'

    @abc.abstractmethod
    def run_cmd(self, cmd, timeout=DEFAULT_TIMEOUT, ignore_stderr=False,
                err_msg='', env={}):
        pass

    @abc.abstractmethod
    def mkdir(self, path):
        pass

    @abc.abstractmethod
    def copy_file(self, filename, content):
        pass

    @abc.abstractmethod
    def remove_file(self, filename):
        pass


class LocalNode(Node):
    def __init__(self, type, hostname, workdir):
        Node.__init__(self, type, hostname, workdir)

    def run_cmd(self, cmd, timeout=DEFAULT_TIMEOUT, ignore_stderr=False,
                err_msg='', env={}):
        tf_cfg.dbg(4, "\tRun command '%s' on host %s with environment %s" % (cmd, self.host, env))
        stdout = ''
        stderr = ''
        # Popen() expects full environment
        env_full = {}
        env_full.update(os.environ)
        env_full.update(env)
        with subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, env=env_full) as p:
            # Now this is where the fun begins. `ignore_stderr` means that
            # the process does not close stderr after daemonization, so plain
            # communicate() will simply block indefinitely. There is nothing we
            # can do about this in a proper race-free way, but what we _can_ do
            # is to read everything from stderr until daemonization.
            # For that, we basically reimplement communicate() with a single
            # difference: we do not wait for EOF on the stderr pipe. For that,
            # we turn the fds into non-blocking mode, create a canary pipe, then
            # launch a thread with poll() on these fds and the read end of the
            # canary pipe, then wait for process termination and finally tell
            # the thread to quit by closing the write end of the canary pipe.
            try:
                stderr_buf = []
                stdout_buf = []
                canary_r, canary_w = _pipe_file()
                thread = threading.Thread(
                    target=_read_thread_poll,
                    args=((canary_r, None, True),
                          (p.stdout, stdout_buf, True),
                          (p.stderr, stderr_buf, not ignore_stderr))
                )
                thread.start()
                p.wait()
                canary_w.close()
                thread.join()
                stdout, stderr = map(''.join, (stdout_buf, stderr_buf))
                assert p.returncode == 0, "Return code %d != 0." % p.returncode

            except Exception as e:
                if not err_msg:
                    err_msg = ("Error running command '%s' on %s" %
                               (cmd, self.host))
                error.bug(err_msg, stdout=stdout, stderr=stderr)
        return stdout, stderr

    def mkdir(self, path):
        try:
            os.makedirs(path)
        except OSError:
            if not os.path.isdir(path):
                raise

    def copy_file(self, filename, content):
        # workdir will be ignored if an absolute filename is passed
        filename = os.path.join(self.workdir, filename)
        dirname = os.path.dirname(filename)

        # assume that workdir exists to avoid unnecessary actions
        if dirname != self.workdir:
            self.mkdir(dirname)

        with open(filename, 'w') as f:
            f.write(content)


    def remove_file(self, filename):
        if DEBUG_FILES:
            return
        if os.path.isfile(filename):
            os.remove(filename)


class RemoteNode(Node):
    def __init__(self, type, hostname, workdir, user, port=22):
        Node.__init__(self, type, hostname, workdir)
        self.user = user
        self.port = port
        self.connect()

    def connect(self):
        """ Open SSH connection to node if remote. Returns False on SSH errors.
        """
        try:
            self.ssh = paramiko.SSHClient()
            self.ssh.load_system_host_keys()
            # Workaround: paramiko prefer RSA keys to ECDSA, so add RSA
            # key to known_hosts.
            self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.ssh.connect(hostname=self.host, username=self.user,
                             port=self.port, timeout=DEFAULT_TIMEOUT)
        except Exception as e:
            error.bug("Error connecting %s" % self.host)

    def close(self):
        """ Release SSH connection without waiting for GC. """
        self.ssh.close()

    def run_cmd(self, cmd, timeout=DEFAULT_TIMEOUT, ignore_stderr=False,
                err_msg='', env={}):
        tf_cfg.dbg(4, "\tRun command '%s' on host %s with environment %s" %
                      (cmd, self.host, env))
        stderr = ''
        stdout = ''
        # we could simply pass environment to exec_command(), but openssh' default
        # is to reject such environment variables, so pass them via env(1)
        if len(env) > 0:
            cmd = ' '.join([
                'env',
                ' '.join([ "%s='%s'" % (k, v) for k, v in env.items() ]),
                cmd
            ])
            tf_cfg.dbg(4, "\tEffective command '%s' after injecting environment" % cmd)
        try:
            # See LocalNode.run_cmd() for details. With SSH, it is slightly
            # easier: the SSH server closes the channel once the command exits,
            # so we do not care about `ignore_stderr` and just reimplement
            # Popen.communicate() here. Due to the brain-dead way in which
            # paramiko supports polling its channels, we do not use poll() here
            # but simply spawn a thread for each stream and just read those
            # in blocking mode.
            _, out_f, err_f = self.ssh.exec_command(cmd, timeout=timeout)
            files = (out_f, err_f)
            buffers = ([], [])
            threads = map(
                lambda buffer, file: threading.Thread(
                    target=_read_thread_primitive,
                    args=(buffer, file)
                ),
                buffers,
                files
            )
            map(threading.Thread.start, threads)
            exit_status = out_f.channel.recv_exit_status()
            map(threading.Thread.join, threads)
            stdout, stderr = map(''.join, buffers)
            assert exit_status == 0, "Return code %d != 0." % exit_status

        except Exception as e:
            if not err_msg:
                err_msg = ("Error running command '%s' on %s" %
                           (cmd, self.host))
            error.bug(err_msg, stdout=stdout, stderr=stderr)
        return stdout, stderr

    def mkdir(self, path):
        self.run_cmd('mkdir -p %s' % path)

    def copy_file(self, filename, content):
        # workdir will be ignored if an absolute filename is passed
        filename = os.path.join(self.workdir, filename)
        dirname = os.path.dirname(filename)

        # assume that workdir exists to avoid unnecessary actions
        if dirname != self.workdir:
            self.mkdir(dirname)

        try:
            with self.ssh.open_sftp() as sftp, \
                 sftp.file(filename, 'w', -1) as sfile:
                    sfile.write(content)
        except Exception as e:
            error.bug(("Error copying file %s to %s" %
                       (filename, self.host)))

    def remove_file(self, filename):
        if DEBUG_FILES:
            return
        try:
            sftp = self.ssh.open_sftp()
            try:
                sftp.unlink(filename)
            except IOError as e:
                if e.errno != errno.ENOENT:
                    raise
            sftp.close()
        except Exception as e:
            error.bug(("Error removing file %s on %s" %
                       (filename, self.host)))



def create_node(host):
    hostname = tf_cfg.cfg.get(host, 'hostname')
    workdir = tf_cfg.cfg.get(host, 'workdir')

    if hostname != 'localhost':
        port = int(tf_cfg.cfg.get(host, 'port'))
        username = tf_cfg.cfg.get(host, 'user')
        return RemoteNode(host, hostname, workdir, username, port)
    return LocalNode(host, hostname, workdir)


#-------------------------------------------------------------------------------
# Helper functions.
#-------------------------------------------------------------------------------

def get_max_thread_count(node):
    out, _ = node.run_cmd('grep -c processor /proc/cpuinfo')
    m = re.match(r'^(\d+)$', out)
    if not m:
        return 1
    return int(m.group(1).decode('ascii'))

#-------------------------------------------------------------------------------
# Global accessible SSH/Local connections
#-------------------------------------------------------------------------------

client = None
tempesta = None
server = None

def connect():
    global client
    client = create_node('Client')

    global tempesta
    tempesta = create_node('Tempesta')

    global server
    server = create_node('Server')

    for node in [client, server]:
        node.mkdir(node.workdir)

# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
