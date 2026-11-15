import QtQuick 2.12
import Lomiri.Components 1.3
import "."

Item {
    id: sh

    property string title: ""
    property bool showArrow: true
    signal arrowClicked()

    width: parent ? parent.width : 0
    height: units.gu(8)

    Label {
        anchors { left: parent.left; leftMargin: units.gu(2); right: arrow.left; rightMargin: units.gu(1); verticalCenter: parent.verticalCenter }
        text: sh.title
        font.pixelSize: units.gu(3.1)
        color: Pal.text
        elide: Text.ElideRight
    }

    Ripple {
        id: arrow
        visible: sh.showArrow
        anchors { right: parent.right; rightMargin: units.gu(2); verticalCenter: parent.verticalCenter }
        width: units.gu(5.6)
        height: width
        radius: width / 2
        onClicked: sh.arrowClicked()

        Rectangle { anchors.fill: parent; radius: parent.radius; color: Pal.surfaceHi }
        Label {
            anchors.centerIn: parent
            text: "→"
            font.pixelSize: units.gu(2.6)
            color: Pal.text
        }
    }
}
