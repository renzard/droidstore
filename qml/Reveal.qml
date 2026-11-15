import QtQuick 2.12
import Lomiri.Components 1.3
import "."

// Fades + slides its content up. `order` staggers siblings (the 3rd section appears after the 2nd).
Item {
    id: r

    property int order: 0
    property bool active: true
    property bool shown: false
    default property alias content: holder.data

    width: parent ? parent.width : 0
    height: holder.height
    opacity: shown ? 1 : 0

    Behavior on opacity { NumberAnimation { duration: Pal.slow; easing.type: Easing.OutCubic } }

    transform: Translate {
        y: r.shown ? 0 : units.gu(3)
        Behavior on y { NumberAnimation { duration: Pal.slow; easing.type: Easing.OutCubic } }
    }

    Timer {
        interval: 40 + r.order * 90
        running: r.active && !r.shown
        onTriggered: r.shown = true
    }

    Column {
        id: holder
        width: parent.width
    }
}
