import QtQuick 2.12
import Lomiri.Components 1.3

Page {
    id: page

    // passed in from Main.qml
    property var py

    header: PageHeader {
        id: header
        title: "Settings"
    }

    Component.onCompleted: {
        py.call("store.get_settings", [], function(s) {
            atlField.text = s.atl_bin;
            repoField.text = s.repo_url;
            catalogField.text = s.catalog_url;
            envField.text = s.extra_env;
            argsField.text = s.extra_args;
        });
        py.call("store.device_info", [], function(info) {
            infoLabel.text = "Architecture: " + info.machine
                + "\nABI: " + (info.abis.length ? info.abis.join(", ") : "unknown")
                + "\nData folder: " + info.data_dir;
        });
    }

    Flickable {
        anchors {
            top: header.bottom
            left: parent.left
            right: parent.right
            bottom: parent.bottom
        }
        contentHeight: column.height + units.gu(4)
        clip: true

        Column {
            id: column
            width: parent.width
            spacing: units.gu(1)
            topPadding: units.gu(2)

            Label {
                x: units.gu(2)
                text: "ATL command"
            }
            TextField {
                id: atlField
                x: units.gu(2)
                width: parent.width - units.gu(4)
                inputMethodHints: Qt.ImhNoPredictiveText | Qt.ImhNoAutoUppercase
            }

            Label {
                x: units.gu(2)
                text: "Repository APK (F-Droid)"
            }
            TextField {
                id: repoField
                x: units.gu(2)
                width: parent.width - units.gu(4)
                inputMethodHints: Qt.ImhUrlCharactersOnly | Qt.ImhNoPredictiveText
            }

            Label {
                x: units.gu(2)
                text: "Prebuilt catalog.json URL (optional)"
            }
            TextField {
                id: catalogField
                x: units.gu(2)
                width: parent.width - units.gu(4)
                inputMethodHints: Qt.ImhUrlCharactersOnly | Qt.ImhNoPredictiveText
            }

            Label {
                x: units.gu(2)
                text: "Diagnostics: extra environment variables for ATL"
            }
            TextField {
                id: envField
                x: units.gu(2)
                width: parent.width - units.gu(4)
                placeholderText: "e.g. G_MESSAGES_DEBUG=all"
                inputMethodHints: Qt.ImhNoPredictiveText | Qt.ImhNoAutoUppercase
            }

            Label {
                x: units.gu(2)
                text: "Diagnostics: extra ATL arguments"
            }
            TextField {
                id: argsField
                x: units.gu(2)
                width: parent.width - units.gu(4)
                inputMethodHints: Qt.ImhNoPredictiveText | Qt.ImhNoAutoUppercase
            }

            Button {
                x: units.gu(2)
                text: "Save"
                color: theme.palette.normal.positive
                onClicked: {
                    py.call("store.set_settings", [{
                        atl_bin: atlField.text,
                        repo_url: repoField.text,
                        catalog_url: catalogField.text,
                        extra_env: envField.text,
                        extra_args: argsField.text
                    }], function() {
                        page.pageStack.pop();
                    });
                }
            }

            Label {
                id: infoLabel
                x: units.gu(2)
                width: parent.width - units.gu(4)
                wrapMode: Text.WrapAnywhere
                fontSize: "small"
            }
        }
    }
}
