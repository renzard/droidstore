import QtQuick 2.12
import Lomiri.Components 1.3
import "."

// Square card for the horizontal rows on the Discover tab.
Ripple {
    id: card

    property string name: ""
    property string icon: ""
    property bool installed: false
    property bool updateAvailable: false

    width: units.gu(11.5)
    height: units.gu(15.5)
    radius: units.gu(1.5)

    Column {
        anchors { top: parent.top; left: parent.left; right: parent.right; topMargin: units.gu(1); leftMargin: units.gu(0.5); rightMargin: units.gu(0.5) }
        spacing: units.gu(1)

        Item {
            width: units.gu(8.5)
            height: width

            AppIcon {
                anchors.fill: parent
                source: card.icon
                letter: card.name
            }

            // a badge pops in when the app is installed (or has an update)
            Rectangle {
                anchors { right: parent.right; top: parent.top; rightMargin: -units.gu(0.6); topMargin: -units.gu(0.6) }
                width: units.gu(2.8)
                height: width
                radius: width / 2
                color: card.updateAvailable ? Pal.primary : Pal.ok
                scale: (card.installed || card.updateAvailable) ? 1 : 0
                Behavior on scale { NumberAnimation { duration: Pal.med; easing.type: Easing.OutBack } }

                Label {
                    anchors.centerIn: parent
                    text: card.updateAvailable ? "↑" : "✓"
                    font.pixelSize: units.gu(1.8)
                    font.bold: true
                    color: Pal.dark ? "#0A2A12" : "#FFFFFF"
                }
            }
        }

        Label {
            width: parent.width
            text: card.name
            font.pixelSize: units.gu(1.8)
            color: Pal.text
            wrapMode: Text.WordWrap
            maximumLineCount: 2
            elide: Text.ElideRight
        }
    }
}
