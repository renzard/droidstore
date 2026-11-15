import QtQuick 2.12
import Lomiri.Components 1.3
import "."

// Pill button. While `busy` it turns into a progress pill that fills up and shows the percentage.
Ripple {
    id: b

    property string label: ""
    property bool busy: false
    property real progress: 0
    property bool tonal: false
    property bool danger: false
    property real minWidth: units.gu(10)

    width: Math.max(minWidth, caption.implicitWidth + units.gu(4))
    height: units.gu(4.6)
    radius: height / 2
    enabled: !busy

    Behavior on width { NumberAnimation { duration: Pal.med; easing.type: Easing.OutCubic } }

    Rectangle {
        anchors.fill: parent
        radius: b.radius
        color: b.busy ? Pal.surfaceHi : (b.tonal ? Pal.surfaceHi : Pal.primary)
        Behavior on color { ColorAnimation { duration: Pal.med } }
    }

    Rectangle {
        width: parent.width * Math.max(0.04, Math.min(1, b.progress))
        height: parent.height
        radius: b.radius
        color: Pal.primary
        opacity: b.busy ? 0.45 : 0
        Behavior on width { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
        Behavior on opacity { NumberAnimation { duration: Pal.med } }
    }

    Label {
        id: caption
        anchors.centerIn: parent
        text: b.busy ? Math.round(b.progress * 100) + "%" : b.label
        font.pixelSize: units.gu(1.75)
        font.bold: true
        color: b.busy ? Pal.text : (b.danger ? Pal.danger : (b.tonal ? Pal.primary : Pal.onPrimary))
    }
}
