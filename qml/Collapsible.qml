import QtQuick 2.12
import Lomiri.Components 1.3
import "."

// A section with a title that opens/closes with an animation (chevron turns, content slides open).
Item {
    id: c

    property string title: ""
    property string glyph: ""
    property bool expanded: false
    default property alias content: body.data

    width: parent ? parent.width : 0
    height: head.height + wrap.height

    Ripple {
        id: head
        width: parent.width
        height: units.gu(7)
        onClicked: c.expanded = !c.expanded

        Label {
            id: g
            anchors { left: parent.left; leftMargin: units.gu(2); verticalCenter: parent.verticalCenter }
            width: units.gu(3.5)
            text: c.glyph
            font.pixelSize: units.gu(2.4)
            color: Pal.textDim
        }
        Label {
            anchors { left: g.right; leftMargin: units.gu(1.5); right: chev.left; verticalCenter: parent.verticalCenter }
            text: c.title
            font.pixelSize: units.gu(2.4)
            color: Pal.text
            elide: Text.ElideRight
        }
        Label {
            id: chev
            anchors { right: parent.right; rightMargin: units.gu(2.5); verticalCenter: parent.verticalCenter }
            text: "›"
            font.pixelSize: units.gu(3.4)
            color: Pal.textDim
            rotation: c.expanded ? 90 : 0
            Behavior on rotation { NumberAnimation { duration: Pal.med; easing.type: Easing.OutCubic } }
        }
    }

    Item {
        id: wrap
        y: head.height
        width: parent.width
        height: c.expanded ? body.height + units.gu(1) : 0
        opacity: c.expanded ? 1 : 0
        clip: true
        Behavior on height { NumberAnimation { duration: Pal.med; easing.type: Easing.OutCubic } }
        Behavior on opacity { NumberAnimation { duration: Pal.med } }

        Column {
            id: body
            width: parent.width
        }
    }
}
