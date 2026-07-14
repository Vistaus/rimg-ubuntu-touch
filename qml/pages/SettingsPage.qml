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
import "../components"

Page {
    id: page
    property var app

    property bool indexing: false
    property string statusText: ""
    property string indexInfoText: ""

    function formatBytes(b) {
        if (b < 1024) return b + " B";
        if (b < 1024 * 1024) return (b / 1024).toFixed(1) + " KiB";
        if (b < 1024 * 1024 * 1024) return (b / (1024 * 1024)).toFixed(1) + " MiB";
        return (b / (1024 * 1024 * 1024)).toFixed(2) + " GiB";
    }

    function refreshIndexInfo() {
        if (!app || !app.ready) return;
        app.indexInfo(function(info) {
            if (!info) return;
            if (info.updated <= 0) {
                page.indexInfoText = i18n.tr("No index yet.");
                return;
            }
            page.indexInfoText =
                i18n.tr("Images: %1").arg(info.count) + "\n"
                + i18n.tr("Last updated: %1").arg(
                      Qt.formatDateTime(new Date(info.updated * 1000),
                                        "yyyy-MM-dd hh:mm")) + "\n"
                + i18n.tr("Size on disk (incl. cached images): %1").arg(
                      page.formatBytes(info.bytes));
        });
    }

    Component.onCompleted: refreshIndexInfo()

    header: PageHeader {
        id: header
        title: i18n.tr("Settings")
        leadingActionBar.actions: [
            Action {
                iconName: "back"
                text: i18n.tr("Back")
                onTriggered: app.popPage()
            }
        ]
    }

    Connections {
        target: app
        onIndexProgress: {
            page.indexing = true;
            progress.indeterminate = (total <= 0);
            progress.maximumValue = Math.max(total, 1);
            progress.value = done;
            page.statusText = i18n.tr("Indexing %1 / %2").arg(done).arg(total)
                              + (name ? "  " + name : "");
        }
        onIndexDone: {
            page.indexing = false;
            page.statusText = result.msg;
            page.refreshIndexInfo();
        }
        onReadyChanged: if (app.ready) page.refreshIndexInfo()
    }

    Flickable {
        id: flick
        anchors {
            top: header.bottom
            left: parent.left
            right: parent.right
            bottom: parent.bottom
        }
        clip: true
        contentHeight: col.height + units.gu(4)

        Column {
            id: col
            width: parent.width - units.gu(4)
            x: units.gu(2)
            y: units.gu(2)
            spacing: units.gu(2)

            // --- server connection ---
            Label { text: i18n.tr("Server"); textSize: Label.Large }

            LabeledField {
                id: hostField
                label: i18n.tr("Host")
                placeholder: "example.com"
                value: app.cfgHost
                onValueEdited: app.cfgHost = v
            }
            LabeledField {
                id: portField
                label: i18n.tr("Port")
                placeholder: "22"
                value: "" + app.cfgPort
                inputMethodHints: Qt.ImhDigitsOnly
                onValueEdited: app.cfgPort = parseInt(v) || 22
            }
            LabeledField {
                id: userField
                label: i18n.tr("Username")
                placeholder: "user"
                value: app.cfgUser
                onValueEdited: app.cfgUser = v
            }
            LabeledField {
                id: dirField
                label: i18n.tr("Remote image directory")
                placeholder: "/home/user/Pictures"
                value: app.cfgRemoteDir
                onValueEdited: app.cfgRemoteDir = v
            }

            // --- thumbnail size ---
            Label {
                text: i18n.tr("Thumbnail size: %1 px").arg(Math.round(thumbSlider.value))
            }
            Slider {
                id: thumbSlider
                width: parent.width
                minimumValue: 96
                maximumValue: 512
                value: app.cfgThumbPx
                live: true
                onValueChanged: app.cfgThumbPx = Math.round(value)
            }

            // --- public key ---
            Label { text: i18n.tr("Public key"); textSize: Label.Large }
            Label {
                width: parent.width
                wrapMode: Text.WordWrap
                textSize: Label.Small
                text: i18n.tr("Add this key to ~/.ssh/authorized_keys on the server.")
            }
            TextArea {
                id: keyArea
                width: parent.width
                readOnly: true
                autoSize: true
                maximumLineCount: 6
                text: app.pubkey
            }
            Button {
                text: i18n.tr("Copy public key")
                enabled: app.pubkey !== ""
                onClicked: {
                    // Lomiri's Clipboard (a common, review-clean capability)
                    // actually puts text on the system clipboard, unlike
                    // TextArea.copy() under Wayland confinement.
                    Clipboard.push(app.pubkey);
                    page.statusText = i18n.tr("Public key copied to clipboard.");
                }
            }

            // --- index stats ---
            Label { text: i18n.tr("Index"); textSize: Label.Large }
            Label {
                width: parent.width
                wrapMode: Text.WordWrap
                textSize: Label.Small
                text: page.indexInfoText !== "" ? page.indexInfoText
                                                : i18n.tr("Loading...")
            }

            // --- actions ---
            Button {
                width: parent.width
                text: i18n.tr("Test connection")
                onClicked: {
                    page.statusText = i18n.tr("Connecting...");
                    app.testConnection(function(res) {
                        page.statusText = res.msg;
                    });
                }
            }
            Button {
                width: parent.width
                color: theme.palette.normal.positive
                text: page.indexing ? i18n.tr("Cancel indexing")
                                    : i18n.tr("Re-index folder")
                onClicked: {
                    if (page.indexing) {
                        app.cancelIndex();
                    } else {
                        page.statusText = i18n.tr("Starting...");
                        app.reindex(function(res) {
                            if (!res.ok) page.statusText = res.msg;
                        });
                    }
                }
            }

            ProgressBar {
                id: progress
                width: parent.width
                visible: page.indexing
                minimumValue: 0
                maximumValue: 1
                value: 0
            }
            Label {
                width: parent.width
                wrapMode: Text.WordWrap
                text: page.statusText
                visible: page.statusText !== ""
            }
        }
    }
}
