import QtQuick 2.12
import Lomiri.Components 1.3
import "."

// Thin progress bar: the fill grows smoothly and a light streak keeps sliding across it.
Item {
    id: p

    property real value: 0
    property bool indeterminate: false

    height: units.gu(0.7)

    Rectangle {
        anchors.fill: parent
        radius: height / 2
        color: Pal.surfaceHi
    }

    Item {
        id: fill
        height: parent.height
        width: p.indeterminate ? p.width : p.width * Math.max(0.03, Math.min(1, p.value))
        clip: true
        Behavior on width { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }

        Rectangle {
            width: p.width
            height: parent.height
            radius: height / 2
            color: Pal.primary
        }
        Rectangle {
            id: shine
            width: units.gu(10)
            height: parent.height
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: "#00FFFFFF" }
                GradientStop { position: 0.5; color: "#88FFFFFF" }
                GradientStop { position: 1.0; color: "#00FFFFFF" }
            }
            NumberAnimation on x {
                from: -shine.width
                to: p.width
                duration: 1200
                loops: Animation.Infinite
                running: p.visible && p.width > 0
            }
        }
    }
}
