'''
 Copyright (C) 2026  Franz Thiemann

 This program is free software: you can redistribute it and/or modify
 it under the terms of the GNU General Public License as published by
 the Free Software Foundation; version 3.

 rimg is distributed in the hope that it will be useful,
 but WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 GNU General Public License for more details.

 You should have received a copy of the GNU General Public License
  along with this program.  If not, see <http://www.gnu.org/licenses/>.

 ---------------------------------------------------------------------------
 Backend for remoteImage (rimg).

 Connects to a server over SSH (key-based auth), recursively indexes a remote
 image directory, and builds a *local* thumbnail cache so the gallery renders
 fast even when the SSH link is slow. Full images are fetched on demand.

 This module is import-testable on a normal host: PyOtherSide is optional and
 its absence only disables progress events. Vendored deps (paramiko, Pillow)
 live in src/vendor and are added to the import path by Main.qml before this
 module is imported on the device.
'''

import io
import os
import json
import stat
import errno
import posixpath
import threading
import traceback

try:
    import pyotherside
except ImportError:  # running on the host / in tests
    pyotherside = None

import paramiko
from PIL import Image

# Extensions we treat as images. HEIC/TIFF may need extra Pillow plugins; we
# try them anyway and skip gracefully if decoding fails.
IMAGE_EXTS = {
    '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp',
    '.tif', '.tiff', '.heic', '.heif',
}
# Videos are indexed too: the thumbnail is the first frame (decoded straight
# from the server via PyAV over a seekable SFTP file, no full download).
VIDEO_EXTS = {
    '.mp4', '.m4v', '.mov', '.mkv', '.webm', '.avi', '.3gp', '.mpg', '.mpeg',
}

DEFAULT_THUMB_PX = 256
KEY_NAME = 'id_ed25519'
APP_NAME = 'rimg.frathiemann'

# --- module state ----------------------------------------------------------
_state = {
    'data_dir': None,
    'cache_dir': None,
    'host': '',
    'port': 22,
    'user': '',
    'remote_dir': '',
    'thumb_px': DEFAULT_THUMB_PX,
}
_index_thread = None
_cancel = False


# --- small helpers ---------------------------------------------------------
def _emit(event, *args):
    '''Send a signal to QML if running under PyOtherSide; no-op otherwise.'''
    if pyotherside is not None:
        pyotherside.send(event, *args)


def _data(*parts):
    return os.path.join(_state['data_dir'], *parts)


def _cache(*parts):
    return os.path.join(_state['cache_dir'], *parts)


def _key_path():
    return _data(KEY_NAME)


def _known_hosts_path():
    return _data('known_hosts')


def _index_path():
    return _data('index.json')


def _ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def _is_image(name):
    return os.path.splitext(name)[1].lower() in IMAGE_EXTS


def _is_video(name):
    return os.path.splitext(name)[1].lower() in VIDEO_EXTS


def _load_index():
    try:
        with open(_index_path(), 'r') as f:
            return json.load(f)
    except (IOError, OSError, ValueError):
        return {}


def _save_index(index):
    tmp = _index_path() + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(index, f)
    os.replace(tmp, _index_path())


# --- key management --------------------------------------------------------
def _generate_key():
    '''Generate an Ed25519 keypair in OpenSSH format and store the private key
    with 0600 perms. Uses `cryptography` (a paramiko dependency).'''
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives import serialization

    priv = ed25519.Ed25519PrivateKey.generate()
    priv_bytes = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption(),
    )
    pub_bytes = priv.public_key().public_bytes(
        serialization.Encoding.OpenSSH,
        serialization.PublicFormat.OpenSSH,
    )

    path = _key_path()
    # Write private key restrictively.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(priv_bytes)
    os.chmod(path, 0o600)

    pub_text = pub_bytes.decode('ascii').strip() + ' rimg@ubuntu-touch\n'
    with open(path + '.pub', 'w') as f:
        f.write(pub_text)
    return pub_text.strip()


def ensure_key():
    '''Return the public key text, generating the keypair on first run.'''
    pub = _key_path() + '.pub'
    if not os.path.exists(_key_path()):
        return _generate_key()
    if os.path.exists(pub):
        with open(pub, 'r') as f:
            return f.read().strip()
    # Private key exists but pub is missing: derive it from paramiko.
    key = paramiko.Ed25519Key.from_private_key_file(_key_path())
    text = '%s %s rimg@ubuntu-touch' % (key.get_name(), key.get_base64())
    with open(pub, 'w') as f:
        f.write(text + '\n')
    return text


def get_pubkey():
    try:
        return ensure_key()
    except Exception as exc:  # noqa: BLE001 - surfaced to UI
        return 'ERROR: %s' % exc


# --- lifecycle / config ----------------------------------------------------
def _default_data_dir():
    base = os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share')
    return os.path.join(base, APP_NAME)


def _default_cache_dir():
    base = os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.cache')
    return os.path.join(base, APP_NAME)


def init(data_dir=None, cache_dir=None):
    '''Ensure storage locations exist and a key is present; return the pubkey.

    With no arguments (the on-device call) the confined XDG data/cache dirs are
    used. Tests pass explicit dirs.'''
    _state['data_dir'] = data_dir or _default_data_dir()
    _state['cache_dir'] = cache_dir or _default_cache_dir()
    _ensure_dir(_state['data_dir'])
    _ensure_dir(_cache('thumbs'))
    _ensure_dir(_cache('full'))
    return get_pubkey()


def configure(host, port, user, remote_dir, thumb_px=DEFAULT_THUMB_PX):
    _state['host'] = (host or '').strip()
    _state['port'] = int(port or 22)
    _state['user'] = (user or '').strip()
    _state['remote_dir'] = (remote_dir or '').strip()
    try:
        _state['thumb_px'] = max(64, int(thumb_px))
    except (TypeError, ValueError):
        _state['thumb_px'] = DEFAULT_THUMB_PX


# --- SSH connection --------------------------------------------------------
def _connect():
    '''Open an SSHClient using our generated key with TOFU host-key pinning.
    Raises on failure (caller converts to a UI message).'''
    ensure_key()
    key = paramiko.Ed25519Key.from_private_key_file(_key_path())

    client = paramiko.SSHClient()
    kh = _known_hosts_path()
    if os.path.exists(kh):
        client.load_host_keys(kh)
    # AutoAdd handles first-contact (TOFU); a *changed* key for a known host
    # still raises BadHostKeyException, which is what we want.
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=_state['host'],
        port=_state['port'],
        username=_state['user'],
        pkey=key,
        look_for_keys=False,
        allow_agent=False,
        timeout=15,
        banner_timeout=20,
        auth_timeout=20,
    )
    client.save_host_keys(kh)
    return client


def test_connection():
    '''Connect, confirm the remote dir is listable, and return a status dict.'''
    if not _state['host'] or not _state['user']:
        return {'ok': False, 'msg': 'Set host and username first.'}
    client = None
    try:
        client = _connect()
        sftp = client.open_sftp()
        if _state['remote_dir']:
            sftp.listdir(_state['remote_dir'])
        sftp.close()
        return {'ok': True, 'msg': 'Connected successfully.'}
    except paramiko.BadHostKeyException:
        return {'ok': False, 'msg': 'Host key changed! Connection refused. '
                                    'Delete known_hosts to re-trust.'}
    except paramiko.AuthenticationException:
        return {'ok': False, 'msg': 'Auth failed. Is the public key in the '
                                    "server's authorized_keys?"}
    except IOError as exc:
        if exc.errno == errno.ENOENT:
            return {'ok': False, 'msg': 'Remote directory not found.'}
        return {'ok': False, 'msg': 'I/O error: %s' % exc}
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'msg': str(exc) or exc.__class__.__name__}
    finally:
        if client is not None:
            client.close()


# --- indexing --------------------------------------------------------------
def _walk_images(sftp, base):
    '''Yield (relpath, SFTPAttributes) for every image/video under base,
    recursively. relpath is POSIX, relative to base.'''
    stack = ['']
    while stack:
        rel = stack.pop()
        remote = posixpath.join(base, rel) if rel else base
        try:
            entries = sftp.listdir_attr(remote)
        except IOError:
            continue
        for attr in entries:
            name = attr.filename
            if name in ('.', '..'):
                continue
            child_rel = posixpath.join(rel, name) if rel else name
            if stat.S_ISDIR(attr.st_mode):
                stack.append(child_rel)
            elif stat.S_ISREG(attr.st_mode) and (_is_image(name)
                                                 or _is_video(name)):
                yield child_rel, attr


def _make_thumb(image_bytes, dest, px):
    '''Decode image bytes, scale to a px-box thumbnail, save JPEG at dest.'''
    _ensure_dir(os.path.dirname(dest))
    with Image.open(io.BytesIO(image_bytes)) as im:
        im = im.convert('RGB')
        im.thumbnail((px, px), Image.Resampling.LANCZOS)
        im.save(dest, 'JPEG', quality=82)


def _make_video_thumb(sftp, remote_path, dest, px):
    '''Thumbnail = first decodable frame of a remote video. PyAV reads from
    the seekable SFTP file directly, so only the container header and the
    first packets travel over the wire, not the whole file.'''
    import av  # lazy: keeps app startup light, host tests don't need it

    _ensure_dir(os.path.dirname(dest))
    with sftp.open(remote_path, 'rb') as fo:
        with av.open(fo) as container:
            frame = next(container.decode(video=0))
            im = frame.to_image()
    im = im.convert('RGB')
    im.thumbnail((px, px), Image.Resampling.LANCZOS)
    im.save(dest, 'JPEG', quality=82)


def _thumb_dest(relpath):
    # Mirror the remote tree under cache/thumbs, with a .jpg extension.
    return _cache('thumbs', relpath + '.jpg')


def _reindex_worker():
    global _cancel
    client = None
    new_index = {}
    old_index = _load_index()
    done = 0
    errors = 0
    try:
        client = _connect()
        sftp = client.open_sftp()
        base = _state['remote_dir']
        px = _state['thumb_px']

        items = list(_walk_images(sftp, base))
        total = len(items)
        _emit('index_progress', 0, total, 'Scanning...')

        for relpath, attr in items:
            if _cancel:
                break
            size = int(attr.st_size or 0)
            mtime = int(attr.st_mtime or 0)
            prev = old_index.get(relpath)
            thumb = _thumb_dest(relpath)

            # Incremental: reuse if size+mtime match and the thumb still exists.
            if (prev and prev.get('size') == size
                    and prev.get('mtime') == mtime
                    and os.path.exists(thumb)):
                new_index[relpath] = prev
                done += 1
                _emit('index_progress', done, total, relpath)
                continue

            try:
                remote = posixpath.join(base, relpath)
                if _is_video(relpath):
                    _make_video_thumb(sftp, remote, thumb, px)
                else:
                    bio = io.BytesIO()
                    sftp.getfo(remote, bio)
                    _make_thumb(bio.getvalue(), thumb, px)
                new_index[relpath] = {
                    'relpath': relpath,
                    'dir': posixpath.dirname(relpath),
                    'name': posixpath.basename(relpath),
                    'type': 'video' if _is_video(relpath) else 'image',
                    'size': size,
                    'mtime': mtime,
                    'thumb': thumb,
                }
            except Exception:  # noqa: BLE001 - one bad file shouldn't abort
                errors += 1
            finally:
                done += 1
                _emit('index_progress', done, total, relpath)

        sftp.close()

        # Drop thumbs for files that vanished remotely (only on a full pass).
        if not _cancel:
            for relpath in old_index:
                if relpath not in new_index:
                    try:
                        os.remove(_thumb_dest(relpath))
                    except OSError:
                        pass

        # On cancel, keep whatever we already had plus what we just did.
        merged = new_index if not _cancel else {**old_index, **new_index}
        _save_index(merged)
        _emit('index_done', {
            'ok': True,
            'cancelled': bool(_cancel),
            'count': len(merged),
            'errors': errors,
            'msg': 'Cancelled.' if _cancel else 'Indexed %d images.' % len(merged),
        })
    except paramiko.BadHostKeyException:
        _emit('index_done', {'ok': False, 'cancelled': False, 'count': 0,
                             'errors': errors, 'msg': 'Host key changed!'})
    except Exception as exc:  # noqa: BLE001
        _emit('index_done', {'ok': False, 'cancelled': False, 'count': 0,
                             'errors': errors,
                             'msg': str(exc) or traceback.format_exc()})
    finally:
        if client is not None:
            client.close()


def reindex():
    '''Start a background indexing pass. Progress arrives via 'index_progress'
    events and completion via an 'index_done' event.'''
    global _index_thread, _cancel
    if _index_thread is not None and _index_thread.is_alive():
        return {'ok': False, 'msg': 'Indexing already in progress.'}
    if not _state['host'] or not _state['remote_dir']:
        return {'ok': False, 'msg': 'Configure server and remote dir first.'}
    _cancel = False
    _index_thread = threading.Thread(target=_reindex_worker, daemon=True)
    _index_thread.start()
    return {'ok': True, 'msg': 'Indexing started.'}


def cancel_index():
    global _cancel
    _cancel = True
    return {'ok': True}


# --- gallery queries -------------------------------------------------------
def list_dir(rel):
    '''Return folders and images directly inside `rel` (POSIX, '' = root),
    derived from the local index. No network access.'''
    rel = (rel or '').strip('/')
    index = _load_index()
    folders = set()
    images = []
    for entry in index.values():
        d = entry.get('dir', '')
        if d == rel:
            images.append({
                'name': entry['name'],
                'relpath': entry['relpath'],
                'thumb': entry['thumb'],
                # Old index entries (pre-video) default to image.
                'type': entry.get('type', 'image'),
                # mtime lets QML build cache-busting thumb URLs.
                'mtime': entry.get('mtime', 0),
            })
        elif rel == '' or d.startswith(rel + '/'):
            # First path component below `rel` is a subfolder of this view.
            tail = d[len(rel):].lstrip('/') if rel else d
            if tail:
                folders.add(tail.split('/', 1)[0])
    images.sort(key=lambda e: e['name'].lower())
    return {
        'path': rel,
        'folders': sorted(folders, key=str.lower),
        'images': images,
    }


def _dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def index_info():
    '''Stats about the local index for the settings page: image count, mtime
    of the last (re)index, and bytes used on disk by index + caches.'''
    index = _load_index()
    try:
        updated = int(os.path.getmtime(_index_path()))
    except OSError:
        updated = 0
    size = _dir_size(_cache('thumbs')) + _dir_size(_cache('full'))
    try:
        size += os.path.getsize(_index_path())
    except OSError:
        pass
    return {'count': len(index), 'updated': updated, 'bytes': size}


# --- on-demand full image --------------------------------------------------
def fetch_full(relpath):
    '''Download the full image for `relpath` into the local full-image cache
    (if not already cached) and return its local path.'''
    relpath = (relpath or '').lstrip('/')
    if not relpath:
        return {'ok': False, 'msg': 'No image specified.'}
    dest = _cache('full', relpath)
    if os.path.exists(dest):
        return {'ok': True, 'path': dest}
    client = None
    try:
        client = _connect()
        sftp = client.open_sftp()
        _ensure_dir(os.path.dirname(dest))
        tmp = dest + '.part'
        sftp.get(posixpath.join(_state['remote_dir'], relpath), tmp)
        os.replace(tmp, dest)
        sftp.close()
        return {'ok': True, 'path': dest}
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'msg': str(exc) or exc.__class__.__name__}
    finally:
        if client is not None:
            client.close()


# --- rotation ---------------------------------------------------------------
def rotate(relpath, clockwise=True):
    '''Rotate an image 90 degrees, replacing both the local cache and the
    ORIGINAL FILE ON THE SERVER (atomic: temp upload + rename). Pixels are
    physically rotated and re-encoded (JPEG q95); the EXIF block is kept but
    its Orientation tag is reset so viewers don't rotate twice. The local
    thumb is regenerated and the index entry updated with the new remote
    size/mtime so the next reindex doesn't refetch.'''
    relpath = (relpath or '').lstrip('/')
    if _is_video(relpath):
        return {'ok': False, 'msg': 'Rotating videos is not supported.'}
    got = fetch_full(relpath)
    if not got.get('ok'):
        return got
    local = got['path']

    tmp = local + '.rot'
    try:
        with Image.open(local) as im:
            fmt = im.format
            exif = im.getexif()
            out = im.transpose(Image.Transpose.ROTATE_270 if clockwise
                               else Image.Transpose.ROTATE_90)
        if 0x0112 in exif:      # Orientation: pixels are upright now
            exif[0x0112] = 1
        kwargs = {'quality': 95} if fmt == 'JPEG' else {}
        if exif:
            kwargs['exif'] = exif.tobytes()
        out.save(tmp, fmt, **kwargs)
    except Exception as exc:  # noqa: BLE001
        try:
            os.remove(tmp)
        except OSError:
            pass
        return {'ok': False, 'msg': 'Rotate failed: %s' % exc}

    client = None
    try:
        client = _connect()
        sftp = client.open_sftp()
        remote = posixpath.join(_state['remote_dir'], relpath)
        rtmp = remote + '.rimg-tmp'
        sftp.put(tmp, rtmp)
        try:
            sftp.posix_rename(rtmp, remote)
        except IOError:
            # Server without posix-rename extension: not atomic, but safe
            # ordering (the temp upload above already succeeded).
            sftp.remove(remote)
            sftp.rename(rtmp, remote)
        attr = sftp.stat(remote)
        sftp.close()
    except Exception as exc:  # noqa: BLE001
        try:
            os.remove(tmp)
        except OSError:
            pass
        return {'ok': False, 'msg': 'Upload failed: %s' % exc}
    finally:
        if client is not None:
            client.close()

    # Server now holds the rotated file: update local cache, thumb and index.
    os.replace(tmp, local)
    thumb = _thumb_dest(relpath)
    try:
        with open(local, 'rb') as f:
            _make_thumb(f.read(), thumb, _state['thumb_px'])
    except Exception:  # noqa: BLE001 - thumb refresh is best-effort
        pass
    index = _load_index()
    entry = index.get(relpath)
    if entry:
        entry['size'] = int(attr.st_size or 0)
        entry['mtime'] = int(attr.st_mtime or 0)
        _save_index(index)
    return {'ok': True, 'path': local, 'thumb': thumb}
