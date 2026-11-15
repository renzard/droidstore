import QtQuick 2.12
import Lomiri.Components 1.3
import "."

// Touch feedback: the item shrinks slightly, a state layer fades in and (for square items) a ripple spreads.
// Children go inside as usual. The MouseArea sits *under* them, so buttons inside a Ripple still get their taps.
Item {
    id: r

    property real radius: 0
    property bool pressScale: true
    property color stateColor: Pal.overlay
    property alias pressed: area.pressed
    property real px: 0
    property real py: 0
    default property alias content: holder.data

    signal clicked()
    signal pressAndHold()

    scale: (pressScale && area.pressed) ? 0.97 : 1.0
    Behavior on scale { NumberAnimation { duration: Pal.fast; easing.type: Easing.OutCubic } }

    MouseArea {
        id: area
        anchors.fill: parent
        onPressed: {
            r.px = mouse.x;
            r.py = mouse.y;
            waveAnim.restart();
        }
        onClicked: r.clicked()
        onPressAndHold: r.pressAndHold()
    }

    Item {
        id: holder
        anchors.fill: parent
    }

    Rectangle {
        anchors.fill: parent
        radius: r.radius
        color: r.stateColor
        opacity: area.pressed ? 0.7 : 0
        Behavior on opacity { NumberAnimation { duration: Pal.fast } }
    }

    Item {
        anchors.fill: parent
        clip: true
        visible: r.radius === 0

        Rectangle {
            id: wave
            property real size: 0
            x: r.px - size / 2
            y: r.py - size / 2
            width: size
            height: size
            radius: size / 2
            color: r.stateColor
            opacity: 0
        }
    }

    ParallelAnimation {
        id: waveAnim
        NumberAnimation { target: wave; property: "size"; from: 0; to: Math.max(r.width, r.height) * 2.2; duration: 450; easing.type: Easing.OutCubic }
        NumberAnimation { target: wave; property: "opacity"; from: 1; to: 0; duration: 450 }
    }
}
