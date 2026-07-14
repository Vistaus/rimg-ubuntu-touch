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

// One page per folder level; preserves the remote directory structure.
Page {
    id: page

    property var app
    property string folderPath: ""   // POSIX path relative to the remote root
    property string folderTitle: ""
    readonly property bool isRoot: folderPath === ""

    header: PageHeader {
        id: header
        title: page.folderTitle !== "" ? page.folderTitle
               : (page.isRoot ? i18n.tr("remoteImage") : page.folderPath)
        leadingActionBar.actions: page.isRoot ? [] : [backAction]
        trailingActionBar.actions: [settingsAction]

        Action {
            id: backAction
            iconName: "back"
            text: i18n.tr("Back")
            onTriggered: app.popPage()
        }
        Action {
            id: settingsAction
            iconName: "settings"
            text: i18n.tr("Settings")
            onTriggered: app.openSettings()
        }
    }

    ListModel { id: galleryModel }

    function reload() {
        if (!app || !app.ready) return;
        app.listDir(page.folderPath, function(res) {
            galleryModel.clear();
            for (var i = 0; i < res.folders.length; i++) {
                var name = res.folders[i];
                var fp = page.isRoot ? name : page.folderPath + "/" + name;
                galleryModel.append({ kind: "folder", name: name, relpath: fp,
                                      thumb: "", media: "", mtime: 0 });
            }
            for (var j = 0; j < res.images.length; j++) {
                var im = res.images[j];
                galleryModel.append({ kind: "image", name: im.name,
                                      relpath: im.relpath, thumb: im.thumb,
                                      media: im.type, mtime: im.mtime });
            }
        });
    }

    Component.onCompleted: reload()
    Connections {
        target: app
        onLibraryChanged: page.reload()
        // The root page loads before the Python backend finishes importing;
        // reload once the backend becomes ready.
        onReadyChanged: if (app.ready) page.reload()
    }

    GridView {
        id: grid
        anchors {
            top: header.bottom
            left: parent.left
            right: parent.right
            bottom: parent.bottom
        }
        clip: true
        model: galleryModel
        // Fit as many ~13gu tiles as the width allows, then stretch them so
        // the row fills the screen exactly (no leftover margin).
        readonly property int columns: Math.max(2, Math.floor(width / units.gu(13)))
        cellWidth: width / columns
        cellHeight: cellWidth
        visible: galleryModel.count > 0

        delegate: Item {
            width: grid.cellWidth
            height: grid.cellHeight

            LomiriShape {
                anchors {
                    fill: parent
                    margins: units.gu(0.5)
                }
                backgroundColor: theme.palette.normal.foreground
                aspect: LomiriShape.Flat

                // Image thumbnail (loaded from the local cache). LomiriShape
                // re-renders its source itself and defaults to Stretch, so it
                // must be told to crop; the Image's own fillMode is not enough.
                source: model.kind === "image" ? thumbImage : null
                sourceFillMode: LomiriShape.PreserveAspectCrop
                Image {
                    id: thumbImage
                    visible: false
                    // The #mtime fragment never reaches the filesystem but
                    // busts Qt's pixmap cache when a rotation rewrites the
                    // thumb at the same path.
                    source: model.kind === "image"
                            ? "file://" + model.thumb + "#m" + model.mtime : ""
                    sourceSize.width: grid.cellWidth
                    sourceSize.height: grid.cellHeight
                    fillMode: Image.PreserveAspectCrop
                    asynchronous: true
                    cache: true
                }

                // Video tiles get a play badge over the first-frame thumb.
                Icon {
                    anchors.centerIn: parent
                    width: units.gu(4)
                    height: units.gu(4)
                    name: "media-playback-start"
                    color: "white"
                    visible: model.media === "video"
                }

                // Folder tile content.
                Column {
                    visible: model.kind === "folder"
                    anchors.centerIn: parent
                    spacing: units.gu(0.5)
                    Icon {
                        anchors.horizontalCenter: parent.horizontalCenter
                        width: units.gu(5); height: units.gu(5)
                        name: "folder"
                    }
                    Label {
                        width: grid.cellWidth - units.gu(2)
                        horizontalAlignment: Text.AlignHCenter
                        elide: Text.ElideMiddle
                        text: model.name
                        textSize: Label.Small
                    }
                }
            }

            AbstractButton {
                anchors.fill: parent
                onClicked: {
                    if (model.kind === "folder") {
                        app.openFolder(model.relpath, model.name);
                    } else {
                        // Hand the viewer this folder's whole image list so it
                        // can swipe between neighbours.
                        var imgs = [];
                        var idx = 0;
                        for (var i = 0; i < galleryModel.count; i++) {
                            var it = galleryModel.get(i);
                            if (it.kind !== "image") continue;
                            if (it.relpath === model.relpath) idx = imgs.length;
                            imgs.push({ name: it.name, relpath: it.relpath,
                                        thumb: it.thumb, type: it.media,
                                        mtime: it.mtime });
                        }
                        app.openImage(imgs, idx);
                    }
                }
            }
        }
    }

    // Empty state: nothing indexed yet for this folder.
    Column {
        anchors.centerIn: parent
        width: parent.width - units.gu(8)
        spacing: units.gu(2)
        visible: galleryModel.count === 0

        Label {
            width: parent.width
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
            text: page.isRoot
                  ? i18n.tr("No images indexed yet.\nOpen Settings to connect and index a remote folder.")
                  : i18n.tr("This folder is empty.")
        }
        Button {
            visible: page.isRoot
            anchors.horizontalCenter: parent.horizontalCenter
            text: i18n.tr("Open Settings")
            color: theme.palette.normal.positive
            onClicked: app.openSettings()
        }
    }
}
