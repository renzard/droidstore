import QtQuick 2.12
import Lomiri.Components 1.3
import "."

Ripple {
    id: row

    property string name: ""
    property int count: 0
    property bool divider: true

    width: parent ? parent.width : 0
    height: units.gu(9.6)

    Rectangle {
        id: tile
        anchors { left: parent.left; leftMargin: units.gu(2); verticalCenter: parent.verticalCenter }
        width: units.gu(6.2)
        height: width
        radius: units.gu(1.4)
        color: Pal.tile

        Label {
            anchors.centerIn: parent
            text: Pal.glyph(row.name)
            font.pixelSize: units.gu(3)
            color: Pal.onTile
        }
    }

    Column {
        anchors { left: tile.right; leftMargin: units.gu(2); right: chevron.left; rightMargin: units.gu(1); verticalCenter: parent.verticalCenter }
        spacing: units.gu(0.3)
        Label {
            width: parent.width
            text: row.name
            font.pixelSize: units.gu(2.3)
            color: Pal.text
            elide: Text.ElideRight
        }
        Label {
            width: parent.width
            text: row.count + (row.count === 1 ? " app" : " apps")
            font.pixelSize: units.gu(1.7)
            color: Pal.textDim
        }
    }

    Label {
        id: chevron
        anchors { right: parent.right; rightMargin: units.gu(2.5); verticalCenter: parent.verticalCenter }
        text: "›"
        font.pixelSize: units.gu(3.4)
        color: Pal.textDim
    }

    Rectangle {
        visible: row.divider
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
        height: units.dp(1)
        color: Pal.outline
    }
}
