import QtQuick 2.12
import Lomiri.Components 1.3
import "."

// App icon: a rounded placeholder with the first letter, replaced by a fade/pop-in once the image is ready.
Item {
    id: ic

    property string source: ""
    property string letter: ""

    Rectangle {
        anchors.fill: parent
        radius: width * 0.24
        color: Pal.surfaceHi

        Label {
            anchors.centerIn: parent
            text: ic.letter.length ? ic.letter.charAt(0).toUpperCase() : ""
            font.pixelSize: ic.height * 0.45
            color: Pal.textDim
            opacity: img.status === Image.Ready ? 0 : 1
            Behavior on opacity { NumberAnimation { duration: Pal.med } }
        }
    }

    Image {
        id: img
        anchors.fill: parent
        source: ic.source
        fillMode: Image.PreserveAspectFit
        asynchronous: true
        sourceSize.width: ic.width * 2
        sourceSize.height: ic.height * 2
        opacity: status === Image.Ready ? 1 : 0
        scale: status === Image.Ready ? 1 : 0.8
        Behavior on opacity { NumberAnimation { duration: Pal.med } }
        Behavior on scale { NumberAnimation { duration: Pal.med; easing.type: Easing.OutBack } }
    }
}
