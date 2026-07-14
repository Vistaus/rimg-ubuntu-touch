'''
Host-side test for the rimg backend logic (runs on amd64, no device needed).

Exercises the full SSH/SFTP/key path against an in-process SFTP server:
key generation, connect + TOFU pinning, recursive indexing, thumbnail creation
mirroring the remote tree, incremental re-index, list_dir, and fetch_full.

Run:  python3 tests/test_backend.py     (also works under pytest)
'''

import os
import sys
import json
import shutil
import tempfile
import threading

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), 'src')
sys.path.insert(0, SRC)
sys.path.insert(0, HERE)

import backend  # noqa: E402
from sftpserver import SFTPTestServer  # noqa: E402


# --- fake pyotherside to capture progress/done events ----------------------
class FakePyOtherSide:
    def __init__(self):
        self.events = []
        self.done = threading.Event()
        self.last_done = None

    def send(self, event, *args):
        self.events.append((event, args))
        if event == 'index_done':
            self.last_done = args[0]
            self.done.set()


def _make_image(path, color):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new('RGB', (640, 480), color).save(path, 'JPEG')


def _make_video(path, color, frames=4):
    import av
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with av.open(path, 'w') as container:
        stream = container.add_stream('mpeg4', rate=10)
        stream.width, stream.height = 320, 240
        stream.pix_fmt = 'yuv420p'
        for _ in range(frames):
            frame = av.VideoFrame.from_image(Image.new('RGB', (320, 240), color))
            container.mux(stream.encode(frame))
        container.mux(stream.encode())


def _wait_index(fake, timeout=30):
    fake.done.clear()
    fake.last_done = None
    assert backend.reindex()['ok'], 'reindex did not start'
    assert fake.done.wait(timeout), 'index_done never arrived'
    return fake.last_done


def run():
    tmp = tempfile.mkdtemp(prefix='rimg-test-')
    data_dir = os.path.join(tmp, 'data')
    cache_dir = os.path.join(tmp, 'cache')
    remote = os.path.join(tmp, 'remote')

    # Remote tree: 2 images + 1 video at root, nested folders preserved.
    _make_image(os.path.join(remote, 'a.jpg'), (200, 0, 0))
    _make_image(os.path.join(remote, 'b.png'), (0, 200, 0))
    _make_video(os.path.join(remote, 'clip.mp4'), (200, 0, 200))
    _make_image(os.path.join(remote, 'vacation', 'beach.jpg'), (0, 0, 200))
    _make_image(os.path.join(remote, 'vacation', 'city', 'tower.jpg'), (200, 200, 0))
    os.makedirs(os.path.join(remote, 'empty'), exist_ok=True)

    fake = FakePyOtherSide()
    backend.pyotherside = fake

    server = SFTPTestServer().start()
    try:
        pub = backend.init(data_dir, cache_dir)
        assert pub.startswith('ssh-ed25519 '), 'pubkey format: %r' % pub
        assert os.path.exists(backend._key_path())
        assert oct(os.stat(backend._key_path()).st_mode & 0o777) == '0o600'

        backend.configure('127.0.0.1', server.port, 'tester', remote, thumb_px=128)

        res = backend.test_connection()
        assert res['ok'], 'test_connection failed: %s' % res
        assert os.path.exists(backend._known_hosts_path()), 'host key not pinned'

        # --- first index ---
        done = _wait_index(fake)
        assert done['ok'] and not done['cancelled'], done
        assert done['count'] == 5, 'expected 5 items, got %s' % done['count']
        assert done['errors'] == 0, done

        # Thumbnails mirror the remote tree under cache/thumbs, as .jpg
        # (the video's thumb is its first frame).
        for rel in ['a.jpg', 'b.png', 'clip.mp4', 'vacation/beach.jpg',
                    'vacation/city/tower.jpg']:
            t = os.path.join(cache_dir, 'thumbs', rel + '.jpg')
            assert os.path.exists(t), 'missing thumb %s' % t
            with Image.open(t) as im:
                assert max(im.size) <= 128, 'thumb too big: %s' % (im.size,)

        idx = json.load(open(backend._index_path()))
        assert set(idx.keys()) == {
            'a.jpg', 'b.png', 'clip.mp4', 'vacation/beach.jpg',
            'vacation/city/tower.jpg'}
        assert idx['clip.mp4']['type'] == 'video', idx['clip.mp4']
        assert idx['a.jpg']['type'] == 'image', idx['a.jpg']

        # --- list_dir: folder structure preserved, media types exposed ---
        root = backend.list_dir('')
        assert root['folders'] == ['vacation'], root
        assert {i['name'] for i in root['images']} == {'a.jpg', 'b.png',
                                                       'clip.mp4'}, root
        types = {i['name']: i['type'] for i in root['images']}
        assert types['clip.mp4'] == 'video' and types['a.jpg'] == 'image', types

        vac = backend.list_dir('vacation')
        assert vac['folders'] == ['city'], vac
        assert {i['name'] for i in vac['images']} == {'beach.jpg'}, vac

        city = backend.list_dir('vacation/city')
        assert city['folders'] == [] and {i['name'] for i in city['images']} == {'tower.jpg'}

        # --- incremental: re-index reuses thumbs (no rewrite) ---
        thumb_mtimes = {
            rel: os.stat(os.path.join(cache_dir, 'thumbs', rel + '.jpg')).st_mtime_ns
            for rel in idx
        }
        done2 = _wait_index(fake)
        assert done2['ok'] and done2['count'] == 5 and done2['errors'] == 0, done2
        for rel, mt in thumb_mtimes.items():
            now = os.stat(os.path.join(cache_dir, 'thumbs', rel + '.jpg')).st_mtime_ns
            assert now == mt, 'thumb %s was rewritten on incremental pass' % rel

        # --- deletion handling: remove a remote file, re-index drops its thumb ---
        os.remove(os.path.join(remote, 'a.jpg'))
        done3 = _wait_index(fake)
        assert done3['count'] == 4, done3
        assert not os.path.exists(os.path.join(cache_dir, 'thumbs', 'a.jpg.jpg'))

        # --- fetch_full: downloads + caches the real bytes ---
        ff = backend.fetch_full('vacation/beach.jpg')
        assert ff['ok'], ff
        assert os.path.exists(ff['path'])
        with open(ff['path'], 'rb') as f, \
                open(os.path.join(remote, 'vacation', 'beach.jpg'), 'rb') as g:
            assert f.read() == g.read(), 'full image bytes differ'
        # Second call hits the cache (path unchanged, still valid).
        assert backend.fetch_full('vacation/beach.jpg')['path'] == ff['path']

        # --- rotate: local cache, thumb, index AND the server copy ---
        rot = backend.rotate('vacation/beach.jpg', clockwise=True)
        assert rot['ok'], rot
        rpath = os.path.join(remote, 'vacation', 'beach.jpg')
        with Image.open(rpath) as im:
            assert im.size == (480, 640), 'server copy not rotated: %s' % (im.size,)
        with Image.open(rot['path']) as im:
            assert im.size == (480, 640), 'local cache not rotated: %s' % (im.size,)
        assert os.path.exists(rot['thumb'])
        idx = json.load(open(backend._index_path()))
        st = os.stat(rpath)
        assert idx['vacation/beach.jpg']['size'] == st.st_size
        assert idx['vacation/beach.jpg']['mtime'] == int(st.st_mtime)
        # No stray temp files left next to the original.
        stray = [f for f in os.listdir(os.path.dirname(rpath))
                 if f.endswith('.rimg-tmp')]
        assert not stray, stray
        # Rotate back (counter-clockwise) restores the orientation.
        rot2 = backend.rotate('vacation/beach.jpg', clockwise=False)
        assert rot2['ok'], rot2
        with Image.open(rpath) as im:
            assert im.size == (640, 480), im.size
        # The incremental pass must not refetch the rotated file.
        done4 = _wait_index(fake)
        assert done4['ok'] and done4['count'] == 4 and done4['errors'] == 0, done4

        # Rotating a video is refused.
        rv = backend.rotate('clip.mp4')
        assert not rv['ok'], rv

        print('ALL BACKEND TESTS PASSED')
    finally:
        server.stop()
        backend.pyotherside = None
        shutil.rmtree(tmp, ignore_errors=True)


def test_backend():  # pytest entrypoint
    run()


if __name__ == '__main__':
    run()
