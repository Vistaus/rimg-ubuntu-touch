/*
 * Copyright (C) 2026  Franz Thiemann
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; version 3.
 *
 * rimg is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <http://www.gnu.org/licenses/>.
 */

import QtQuick 2.7
import Lomiri.Components 1.3
import QtQuick.Layouts 1.3
import Qt.labs.settings 1.0
import io.thp.pyotherside 1.4

MainView {
    id: root
    objectName: 'mainView'
    applicationName: 'rimg.frathiemann'
    automaticOrientation: true

    width: units.gu(45)
    height: units.gu(75)

    // True once the Python backend is imported and initialised.
    property bool ready: false
    property string pubkey: ""

    // Indexing progress, fanned out from the PyOtherSide 'index_*' events.
    signal indexProgress(int done, int total, string name)
    signal indexDone(var result)
    // Emitted when the local library changes so open gallery pages refresh.
    signal libraryChanged()

    // --- persistent connection config -------------------------------------
    Settings {
        id: settings
        property string host: ""
        property int port: 22
        property string user: ""
        property string remoteDir: ""
        property int thumbPx: 256
    }
    property alias cfgHost: settings.host
    property alias cfgPort: settings.port
    property alias cfgUser: settings.user
    property alias cfgRemoteDir: settings.remoteDir
    property alias cfgThumbPx: settings.thumbPx

    // --- Python backend bridge --------------------------------------------
    Python {
        id: py

        Component.onCompleted: {
            // Vendored deps first, then our own modules.
            addImportPath(Qt.resolvedUrl('../src/vendor'));
            addImportPath(Qt.resolvedUrl('../src/'));
            importModule('backend', function() {
                py.call('backend.init', [], function(pub) {
                    root.pubkey = pub;
                    root.applyConfig();
                    root.ready = true;
                });
            });
        }

        onReceived: {
            // data == [event_name, ...args] from pyotherside.send()
            if (data[0] === 'index_progress') {
                root.indexProgress(data[1], data[2], data[3]);
            } else if (data[0] === 'index_done') {
                root.indexDone(data[1]);
                root.libraryChanged();
            }
        }

        onError: console.log('python error: ' + traceback)
    }

    // --- backend API helpers ----------------------------------------------
    function applyConfig() {
        py.call('backend.configure',
                [settings.host, settings.port, settings.user,
                 settings.remoteDir, settings.thumbPx]);
    }
    function refreshPubkey(cb) {
        py.call('backend.get_pubkey', [], function(pub) {
            root.pubkey = pub;
            if (cb) cb(pub);
        });
    }
    function testConnection(cb) {
        root.applyConfig();
        py.call('backend.test_connection', [], cb);
    }
    function reindex(cb) {
        root.applyConfig();
        py.call('backend.reindex', [], cb);
    }
    function cancelIndex() { py.call('backend.cancel_index', []); }
    function listDir(rel, cb) { py.call('backend.list_dir', [rel], cb); }
    function indexInfo(cb) { py.call('backend.index_info', [], cb); }
    function fetchFull(rel, cb) { py.call('backend.fetch_full', [rel], cb); }
    function rotateImage(rel, clockwise, cb) {
        py.call('backend.rotate', [rel, clockwise], cb);
    }

    // --- navigation -------------------------------------------------------
    function openFolder(rel, title) {
        stack.push(Qt.resolvedUrl('pages/GalleryPage.qml'),
                   {app: root, folderPath: rel, folderTitle: title});
    }
    // images: [{name, relpath, thumb}] for one folder; index: starting image.
    function openImage(images, index) {
        stack.push(Qt.resolvedUrl('pages/ViewerPage.qml'),
                   {app: root, imageList: images, startIndex: index});
    }
    function openSettings() {
        stack.push(Qt.resolvedUrl('pages/SettingsPage.qml'), {app: root});
    }
    function popPage() { stack.pop(); }

    PageStack {
        id: stack
        Component.onCompleted: stack.push(
            Qt.resolvedUrl('pages/GalleryPage.qml'),
            {app: root, folderPath: '', folderTitle: i18n.tr('remoteImage')})
    }
}
