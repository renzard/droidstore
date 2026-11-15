import QtQuick 2.12
import Lomiri.Components 1.3
import "."

Ripple {
    id: chip

    property string text: ""
    property string glyph: ""
    property bool selected: false
    property bool closable: false

    width: content.width + units.gu(3)
    height: units.gu(4.6)
    radius: height / 2

    Rectangle {
        anchors.fill: parent
        radius: chip.radius
        color: chip.selected ? Pal.chipSel : "transparent"
        border.width: chip.selected ? 0 : units.dp(1)
        border.color: Pal.outline
        Behavior on color { ColorAnimation { duration: Pal.med } }
    }

    Row {
        id: content
        anchors.centerIn: parent
        spacing: units.gu(0.8)

        Label {
            visible: chip.glyph !== ""
            text: chip.glyph
            font.pixelSize: units.gu(1.8)
            color: chip.selected ? Pal.onChipSel : Pal.primary
        }
        Label {
            text: chip.text
            font.pixelSize: units.gu(1.8)
            color: chip.selected ? Pal.onChipSel : Pal.text
        }
        Label {
            visible: chip.closable
            text: "×"
            font.pixelSize: units.gu(2.2)
            color: Pal.onChipSel
        }
    }
}
