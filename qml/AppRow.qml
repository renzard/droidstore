import QtQuick 2.12
import Lomiri.Components 1.3
import "."

// One app in a list (Search results, My apps). When `expandable`, a "⋮" button slides open extra actions.
Item {
    id: row

    property string pkgId: ""
    property string name: ""
    property string summary: ""
    property string icon: ""
    property string version: ""
    property string newVersion: ""
    property real size: 0
    property bool isNative: false
    property bool verified: false
    property bool installed: false
    property bool updateAvailable: false
    property bool busy: false
    property real progress: 0
    property bool local: false
    property bool showKind: true   // library rows do not know if the app is native
    property bool expandable: false
    property bool expanded: false

    signal opened()
    signal action()
    signal addToDrawer()
    signal removed()

    width: parent ? parent.width : 0
    height: base.height + extra.height
    Behavior on height { NumberAnimation { duration: Pal.med; easing.type: Easing.OutCubic } }

    readonly property string meta: {
        var parts = [];
        if (updateAvailable && newVersion !== "") parts.push(version + " → " + newVersion);
        else if (version !== "") parts.push(version);
        if (size > 0) parts.push((size / 1048576).toFixed(1) + " MB");
        if (local) parts.push("your own APK");
        else if (showKind) parts.push(isNative ? "native" : "Java");
        return parts.join("  ·  ");
    }

    Ripple {
        id: base
        width: parent.width
        height: units.gu(10.5)
        onClicked: row.opened()

        AppIcon {
            id: ico
            anchors { left: parent.left; leftMargin: units.gu(2); verticalCenter: parent.verticalCenter }
            width: units.gu(6.6)
            height: width
            source: row.icon
            letter: row.name
        }

        Column {
            anchors {
                left: ico.right; leftMargin: units.gu(1.8)
                right: buttons.left; rightMargin: units.gu(1)
                verticalCenter: parent.verticalCenter
            }
            spacing: units.gu(0.3)

            Row {
                width: parent.width
                spacing: units.gu(0.8)
                Label {
                    width: Math.min(implicitWidth, parent.width - (row.verified ? units.gu(3) : 0))
                    text: row.name
                    font.pixelSize: units.gu(2)
                    font.bold: true
                    color: Pal.text
                    elide: Text.ElideRight
                }
                Label {
                    visible: row.verified
                    text: "✔"
                    font.pixelSize: units.gu(1.8)
                    color: Pal.ok
                }
            }
            Label {
                width: parent.width
                visible: text !== ""
                text: row.summary
                font.pixelSize: units.gu(1.6)
                color: Pal.textDim
                maximumLineCount: 2
                wrapMode: Text.WordWrap
                elide: Text.ElideRight
            }
            Label {
                width: parent.width
                text: row.meta
                font.pixelSize: units.gu(1.4)
                color: row.updateAvailable ? Pal.primary : Pal.textDim
                elide: Text.ElideRight
            }
        }

        Row {
            id: buttons
            anchors { right: parent.right; rightMargin: units.gu(1.5); verticalCenter: parent.verticalCenter }
            spacing: units.gu(0.5)

            ActionButton {
                label: row.updateAvailable ? "Update" : (row.installed ? "Open" : "Get")
                tonal: row.installed && !row.updateAvailable
                busy: row.busy
                progress: row.progress
                onClicked: row.action()
            }

            Ripple {
                visible: row.expandable
                width: visible ? units.gu(4.6) : 0
                height: units.gu(4.6)
                radius: width / 2
                onClicked: row.expanded = !row.expanded
                Label {
                    anchors.centerIn: parent
                    text: "⋮"
                    font.pixelSize: units.gu(2.8)
                    color: Pal.textDim
                    rotation: row.expanded ? 90 : 0
                    Behavior on rotation { NumberAnimation { duration: Pal.med; easing.type: Easing.OutCubic } }
                }
            }
        }

        // download progress along the bottom edge of the row
        ProgressLine {
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom; leftMargin: units.gu(2); rightMargin: units.gu(2) }
            height: units.gu(0.5)
            value: row.progress
            opacity: row.busy ? 1 : 0
            visible: opacity > 0
            Behavior on opacity { NumberAnimation { duration: Pal.med } }
        }
    }

    Item {
        id: extra
        anchors.top: base.bottom
        width: parent.width
        height: (row.expandable && row.expanded) ? units.gu(6.4) : 0
        opacity: height > 0 ? 1 : 0
        clip: true
        Behavior on opacity { NumberAnimation { duration: Pal.med } }

        Row {
            anchors { left: parent.left; leftMargin: units.gu(2); verticalCenter: parent.verticalCenter }
            spacing: units.gu(1)

            ActionButton {
                label: "Add to app drawer"
                tonal: true
                onClicked: { row.addToDrawer(); row.expanded = false; }
            }
            ActionButton {
                label: "Remove"
                tonal: true
                danger: true
                onClicked: row.removed()
            }
        }
    }
}
