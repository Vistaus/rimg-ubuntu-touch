'''
Minimal in-process SFTP server for host-side testing of the rimg backend.

Backed by the real filesystem (ROOT = ''), read + list only -- which is all the
backend needs (listdir_attr, stat, open-for-read). Adapted from the canonical
paramiko StubSFTPServer demo. NOT for production; tests only.
'''

import os
import socket
import threading

import paramiko


class _StubServer(paramiko.ServerInterface):
    def check_auth_publickey(self, username, key):
        # The transport already verified the client's signature against this
        # key, so reaching here means the client holds the private key.
        return paramiko.AUTH_SUCCESSFUL

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED

    def get_allowed_auths(self, username):
        return 'publickey'


class _StubSFTPHandle(paramiko.SFTPHandle):
    def stat(self):
        try:
            return paramiko.SFTPAttributes.from_stat(
                os.fstat(self.readfile.fileno()))
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)


class _StubSFTPServer(paramiko.SFTPServerInterface):
    ROOT = ''  # serve absolute paths straight from the real filesystem

    def _real(self, path):
        return self.ROOT + self.canonicalize(path)

    def list_folder(self, path):
        p = self._real(path)
        try:
            out = []
            for fname in os.listdir(p):
                attr = paramiko.SFTPAttributes.from_stat(
                    os.stat(os.path.join(p, fname)))
                attr.filename = fname
                out.append(attr)
            return out
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)

    def stat(self, path):
        try:
            return paramiko.SFTPAttributes.from_stat(os.stat(self._real(path)))
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)

    def lstat(self, path):
        try:
            return paramiko.SFTPAttributes.from_stat(os.lstat(self._real(path)))
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)

    def open(self, path, flags, attr):
        p = self._real(path)
        try:
            fd = os.open(p, flags, getattr(attr, 'st_mode', None) or 0o666)
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)
        mode = 'rb' if not (flags & (os.O_WRONLY | os.O_RDWR)) else 'r+b'
        try:
            f = os.fdopen(fd, mode)
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)
        handle = _StubSFTPHandle(flags)
        handle.filename = p
        handle.readfile = f
        if flags & (os.O_WRONLY | os.O_RDWR):
            handle.writefile = f
        return handle

    def remove(self, path):
        try:
            os.remove(self._real(path))
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)
        return paramiko.SFTP_OK

    def rename(self, oldpath, newpath):
        try:
            os.rename(self._real(oldpath), self._real(newpath))
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)
        return paramiko.SFTP_OK

    # OpenSSH posix-rename@openssh.com extension (overwrites the target).
    def posix_rename(self, oldpath, newpath):
        try:
            os.replace(self._real(oldpath), self._real(newpath))
        except OSError as e:
            return paramiko.SFTPServer.convert_errno(e.errno)
        return paramiko.SFTP_OK


class SFTPTestServer:
    '''Threaded SFTP server accepting repeated connections on localhost.'''

    def __init__(self):
        self._host_key = paramiko.RSAKey.generate(2048)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(('127.0.0.1', 0))
        self._sock.listen(16)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def _serve(self):
        while not self._stop.is_set():
            try:
                self._sock.settimeout(0.5)
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,),
                             daemon=True).start()

    def _handle(self, conn):
        try:
            t = paramiko.Transport(conn)
            t.add_server_key(self._host_key)
            t.set_subsystem_handler('sftp', paramiko.SFTPServer,
                                    _StubSFTPServer)
            t.start_server(server=_StubServer())
            # Keep the transport alive while the client uses the channel.
            chan = t.accept(20)
            if chan is not None:
                while t.is_active() and not self._stop.is_set():
                    self._stop.wait(0.2)
        except Exception:
            pass

    def stop(self):
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass
