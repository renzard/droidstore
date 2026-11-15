import QtQuick 2.12
import Lomiri.Components 1.3
import "."

// One line of the details page: symbol, small caption, value. With `tappable` it acts as a link.
Ripple {
    id: ir

    property string glyph: ""
    property string caption: ""
    property string value: ""
    property bool tappable: false

    width: parent ? parent.width : 0
    height: col.height + units.gu(2.4)
    enabled: tappable

    Label {
        id: g
        anchors { left: parent.left; leftMargin: units.gu(2); top: parent.top; topMargin: units.gu(1.4) }
        width: units.gu(3.5)
        text: ir.glyph
        font.pixelSize: units.gu(2.2)
        color: Pal.textDim
    }

    Column {
        id: col
        anchors { left: g.right; leftMargin: units.gu(1.5); right: parent.right; rightMargin: units.gu(2); top: parent.top; topMargin: units.gu(1.2) }
        spacing: units.gu(0.2)

        Label {
            width: parent.width
            visible: ir.caption !== ""
            text: ir.caption
            font.pixelSize: units.gu(1.5)
            color: Pal.textDim
        }
        Label {
            width: parent.width
            text: ir.value
            wrapMode: Text.WrapAnywhere
            font.pixelSize: units.gu(1.9)
            color: ir.tappable ? Pal.primary : Pal.text
        }
    }
}
