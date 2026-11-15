import QtQuick 2.12
import Lomiri.Components 1.3
import "."

// Horizontal row of AppCards (Discover). Cards pop in when the model fills.
ListView {
    id: lv

    signal appClicked(string pkgId)

    width: parent ? parent.width : 0
    height: units.gu(16.5)
    orientation: ListView.Horizontal
    leftMargin: units.gu(1.5)
    rightMargin: units.gu(1.5)
    spacing: units.gu(0.5)
    clip: true

    delegate: AppCard {
        name: model.name
        icon: model.icon
        installed: model.installed
        updateAvailable: model.updateAvailable
        onClicked: lv.appClicked(model.pkgId)
    }

    add: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Pal.med }
            NumberAnimation { property: "scale"; from: 0.7; to: 1; duration: Pal.med; easing.type: Easing.OutBack }
        }
    }
    displaced: Transition {
        NumberAnimation { properties: "x"; duration: Pal.med; easing.type: Easing.OutCubic }
    }
}
