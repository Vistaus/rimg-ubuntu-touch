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
import QtMultimedia 5.9
import Lomiri.Components 1.3

// Full-screen media viewer for one folder: swipe left/right between items.
// Images: cached thumb as placeholder -> full image, pinch zoom about the
// pinch centre (Flickable.resizeContent), 90-degree rotation that also
// rewrites the file on the server. Videos: first-frame thumb + play button,
// fetched on demand and played inline via QtMultimedia/media-hub.
Page {
    id: page

    property var app
    property var imageList: []   // [{name, relpath, thumb, mtime, type}]
    property int startIndex: 0

    // Full-file downloads, keyed by relpath. Kept at page level so delegate
    // recycling can't lose results or fire callbacks on destroyed items.
    property var fullPaths: ({})   // relpath -> local file path
    property var fullErrors: ({})  // relpath -> error message
    property var pending: ({})     // relpath -> true while downloading

    // Bumped after a rotation: cache-busts the file:// URLs (the #fragment
    // never reaches the filesystem but changes Qt's pixmap-cache key).
    property int imageRev: 0
    property bool rotating: false
    property string statusText: ""

    readonly property var currentEntry:
        (pager.currentIndex >= 0 && pager.currentIndex < imageList.length)
        ? imageList[pager.currentIndex] : null
    readonly property bool currentIsImage:
        currentEntry !== null && (currentEntry.type || "image") === "image"

    function ensureFull(rel) {
        if (!rel || fullPaths[rel] || pending[rel]) return;
        pending[rel] = true;
        app.fetchFull(rel, function(res) {
            page.pending[rel] = false;
            if (res.ok) {
                page.fullPaths[rel] = res.path;
                page.fullPathsChanged();
            } else {
                page.fullErrors[rel] = res.msg || i18n.tr("Failed to load.");
                page.fullErrorsChanged();
            }
        });
    }

    // Fetch the item at `i` plus its image neighbours. PyOtherSide serialises
    // the calls on one worker thread, so the visible item downloads first and
    // the neighbours warm the cache for the next swipe. Videos are never
    // prefetched (arbitrarily large) — they download when playback is tapped.
    function fetchAround(i) {
        var order = [i, i + 1, i - 1];
        for (var k = 0; k < order.length; k++) {
            var j = order[k];
            if (j < 0 || j >= page.imageList.length) continue;
            if ((page.imageList[j].type || "image") === "video") continue;
            ensureFull(page.imageList[j].relpath);
        }
    }

    function doRotate(clockwise) {
        var entry = page.currentEntry;
        if (!entry || page.rotating || !page.currentIsImage) return;
        page.rotating = true;
        page.statusText = i18n.tr("Rotating...");
        app.rotateImage(entry.relpath, clockwise, function(res) {
            page.rotating = false;
            if (!res || !res.ok) {
                page.statusText = (res && res.msg) ? res.msg
                                                   : i18n.tr("Rotation failed.");
                return;
            }
            page.statusText = "";
            page.imageRev++;
            if (pager.currentItem && pager.currentItem.resetZoom)
                pager.currentItem.resetZoom();
            app.libraryChanged();   // gallery thumbs pick up the new mtime
        });
    }

    header: PageHeader {
        id: header
        title: page.currentEntry ? page.currentEntry.name : ""
        subtitle: page.imageList.length > 1
                  ? i18n.tr("%1 of %2").arg(pager.currentIndex + 1)
                                       .arg(page.imageList.length) : ""
        leadingActionBar.actions: [
            Action {
                iconName: "back"
                text: i18n.tr("Back")
                onTriggered: app.popPage()
            }
        ]
        trailingActionBar.actions: [
            Action {
                iconName: "rotate-right"
                text: i18n.tr("Rotate right")
                visible: page.currentIsImage
                enabled: !page.rotating
                onTriggered: page.doRotate(true)
            },
            Action {
                iconName: "rotate-left"
                text: i18n.tr("Rotate left")
                visible: page.currentIsImage
                enabled: !page.rotating
                onTriggered: page.doRotate(false)
            }
        ]
    }

    Component.onCompleted: {
        pager.currentIndex = page.startIndex;
        pager.positionViewAtIndex(page.startIndex, ListView.SnapPosition);
        fetchAround(page.startIndex);
    }

    ListView {
        id: pager
        anchors {
            top: header.bottom
            left: parent.left
            right: parent.right
            bottom: parent.bottom
        }
        clip: true
        orientation: ListView.Horizontal
        snapMode: ListView.SnapOneItem
        highlightRangeMode: ListView.StrictlyEnforceRange
        highlightMoveDuration: 250
        cacheBuffer: width > 0 ? width : 0
        model: page.imageList
        // While zoomed in, horizontal drags pan the image instead of paging.
        interactive: !currentItem || !currentItem.zoomed

        onCurrentIndexChanged: {
            if (currentIndex >= 0 && currentIndex < page.imageList.length)
                page.fetchAround(currentIndex);
        }

        delegate: Item {
            id: pageItem
            width: pager.width
            height: pager.height

            readonly property bool isVideo:
                (modelData.type || "image") === "video"
            readonly property string fullPath:
                page.fullPaths[modelData.relpath] || ""
            readonly property string errorText:
                page.fullErrors[modelData.relpath] || ""
            readonly property bool ready:
                !isVideo && fullPath !== "" && fullImage.status === Image.Ready
            readonly property bool zoomed:
                !isVideo && flick.contentWidth > flick.width + 1
            property bool playRequested: false

            function resetZoom() { flick.resetZoom(); }

            property bool isCurrent: ListView.isCurrentItem
            onIsCurrentChanged: {
                if (!isCurrent) {
                    resetZoom();
                    playRequested = false;   // unloads + stops any video
                }
            }

            // Placeholder: the cached thumbnail blown up, until the full
            // image is decoded (images) or playback starts (videos).
            Image {
                anchors.fill: parent
                fillMode: Image.PreserveAspectFit
                source: modelData.thumb
                        ? "file://" + modelData.thumb + "#m" + modelData.mtime
                          + "r" + page.imageRev
                        : ""
                asynchronous: true
                visible: pageItem.isVideo ? !videoLoader.active
                                          : !pageItem.ready
            }

            ActivityIndicator {
                anchors.centerIn: parent
                running: visible
                visible: pageItem.errorText === ""
                         && (pageItem.isVideo
                             ? (pageItem.playRequested && pageItem.fullPath === "")
                             : !pageItem.ready)
            }

            Label {
                anchors {
                    horizontalCenter: parent.horizontalCenter
                    bottom: parent.bottom
                    bottomMargin: units.gu(4)
                }
                width: parent.width - units.gu(8)
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
                visible: pageItem.errorText !== ""
                text: pageItem.errorText
            }

            // --- image: zoomable Flickable ---------------------------------
            Flickable {
                id: flick
                anchors.fill: parent
                clip: true
                visible: !pageItem.isVideo && pageItem.ready
                interactive: pageItem.zoomed

                function resetZoom() {
                    contentWidth = width;
                    contentHeight = height;
                    contentX = 0;
                    contentY = 0;
                }
                Component.onCompleted: resetZoom()
                onWidthChanged: if (!pageItem.zoomed) resetZoom()
                onHeightChanged: if (!pageItem.zoomed) resetZoom()

                PinchArea {
                    id: pinchArea
                    width: Math.max(flick.contentWidth, flick.width)
                    height: Math.max(flick.contentHeight, flick.height)
                    enabled: !pageItem.isVideo && pageItem.ready

                    property real maxZoom: 4.0
                    property real startWidth

                    onPinchStarted: startWidth = flick.contentWidth
                    onPinchUpdated: {
                        // Pan with the moving pinch centre...
                        flick.contentX += pinch.previousCenter.x - pinch.center.x;
                        flick.contentY += pinch.previousCenter.y - pinch.center.y;
                        // ...and zoom about it: resizeContent keeps the given
                        // content point fixed in the viewport.
                        var z = Math.max(1.0, Math.min(maxZoom,
                                (startWidth * pinch.scale) / flick.width));
                        flick.resizeContent(flick.width * z, flick.height * z,
                                            pinch.center);
                    }
                    onPinchFinished: flick.returnToBounds()

                    Image {
                        id: fullImage
                        width: flick.contentWidth
                        height: flick.contentHeight
                        fillMode: Image.PreserveAspectFit
                        asynchronous: true
                        source: !pageItem.isVideo && pageItem.fullPath !== ""
                                ? "file://" + pageItem.fullPath
                                  + "#r" + page.imageRev
                                : ""

                        MouseArea {
                            anchors.fill: parent
                            onDoubleClicked: {
                                if (pageItem.zoomed) {
                                    flick.resetZoom();
                                } else {
                                    flick.resizeContent(flick.width * 2,
                                                        flick.height * 2,
                                                        Qt.point(mouse.x, mouse.y));
                                    flick.returnToBounds();
                                }
                            }
                        }
                    }
                }
            }

            // --- video: play badge + on-demand inline playback -------------
            Icon {
                anchors.centerIn: parent
                width: units.gu(6)
                height: units.gu(6)
                name: "media-playback-start"
                color: "white"
                visible: pageItem.isVideo
                         && (!pageItem.playRequested || pageItem.errorText !== "")
            }

            MouseArea {
                anchors.fill: parent
                enabled: pageItem.isVideo && !videoLoader.active
                onClicked: {
                    pageItem.playRequested = true;
                    page.ensureFull(modelData.relpath);
                }
            }

            Loader {
                id: videoLoader
                anchors.fill: parent
                active: pageItem.isVideo && pageItem.playRequested
                        && pageItem.fullPath !== "" && pageItem.isCurrent
                sourceComponent: Rectangle {
                    color: "black"
                    Video {
                        id: player
                        anchors.fill: parent
                        source: "file://" + pageItem.fullPath
                        autoPlay: true

                        MouseArea {
                            anchors.fill: parent
                            onClicked: {
                                if (player.playbackState
                                        === MediaPlayer.PlayingState)
                                    player.pause();
                                else
                                    player.play();
                            }
                        }
                    }
                }
            }
        }
    }

    // Rotation progress / errors.
    Label {
        anchors {
            horizontalCenter: parent.horizontalCenter
            bottom: parent.bottom
            bottomMargin: units.gu(1.5)
        }
        width: parent.width - units.gu(4)
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.WordWrap
        visible: page.statusText !== ""
        text: page.statusText
    }
}
