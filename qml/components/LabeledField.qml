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

// A label above a TextField. `value` seeds the field; `valueEdited(v)` fires
// on user input so the parent can persist it without a binding loop.
Column {
    id: control
    property alias label: lbl.text
    property string value: ""
    property alias placeholder: field.placeholderText
    property alias inputMethodHints: field.inputMethodHints
    signal valueEdited(string v)

    width: parent ? parent.width : units.gu(40)
    spacing: units.gu(0.5)

    Label { id: lbl }

    TextField {
        id: field
        width: parent.width
        text: control.value
        onTextChanged: if (text !== control.value) control.valueEdited(text)
    }
}
