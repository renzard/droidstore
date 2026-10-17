import QtQuick 2.12
import Lomiri.Components 1.3

Page {
    id: page

    // passed in from Main.qml
    property var appRoot
    property var py

    property string message: ""
    property bool busy: false

    header: PageHeader {
        id: header
        title: "Add APK"
    }

    function importPath(path, onDone) {
        if (busy || path.trim() === "") {
            if (onDone) {
                onDone();
            }
            return;
        }
        busy = true;
        message = "Copying…";
        py.call("store.import_apk", [path], function(result) {
            busy = false;
            if (onDone) {
                onDone();
            }
            if (result.ok) {
                message = "Added: " + result.name + (result.warning !== "" ? "\n⚠ " + result.warning : "");
                pathField.text = "";
                appRoot.refreshLibrary();
            } else {
                message = result.error;
            }
        });
    }

    function scan() {
        py.call("store.find_apks", [], function(items) {
            foundModel.clear();
            for (var i = 0; i < items.length; i++) {
                foundModel.append({ path: items[i].path, name: items[i].name, size: items[i].size });
            }
        });
    }

    Component.onCompleted: scan()

    ListModel { id: foundModel }

    Column {
        id: top
        anchors {
            top: header.bottom
            left: parent.left
            right: parent.right
            margins: units.gu(2)
        }
        spacing: units.gu(1)

        Label {
            width: parent.width
            wrapMode: Text.WordWrap
            fontSize: "small"
            text: "Tap \"Choose from an app\" to pick the APK from another app (e.g. File Manager), pick it from the list, or type its path. "
                  + "It will be copied to the library and opened with ATL."
        }

        Button {
            width: parent.width
            text: "Choose from an app…"
            color: theme.palette.normal.positive
            onClicked: pageStack.push(Qt.resolvedUrl("PickerPage.qml"), { importPage: page })
        }

        TextField {
            id: pathField
            width: parent.width
            placeholderText: "/home/phablet/Downloads/app.apk"
            inputMethodHints: Qt.ImhNoPredictiveText | Qt.ImhNoAutoUppercase
            onAccepted: page.importPath(text)
        }

        Button {
            text: "Add from path"
            enabled: !page.busy && pathField.text.trim() !== ""
            color: theme.palette.normal.positive
            onClicked: page.importPath(pathField.text)
        }

        Label {
            width: parent.width
            visible: text !== ""
            wrapMode: Text.WordWrap
            text: page.message
        }

        Label {
            text: "APKs found on the device"
            font.bold: true
        }
    }

    ListView {
        anchors {
            top: top.bottom
            left: parent.left
            right: parent.right
            bottom: parent.bottom
            topMargin: units.gu(1)
        }
        clip: true
        model: foundModel

        delegate: ListItem {
            height: units.gu(7)
            onClicked: page.importPath(model.path)

            ListItemLayout {
                title.text: model.name
                subtitle.text: Math.round(model.size / 1048576 * 10) / 10 + " MB"
                Icon {
                    SlotsLayout.position: SlotsLayout.Trailing
                    width: units.gu(3)
                    name: "add"
                }
            }
        }

        Label {
            anchors.centerIn: parent
            width: parent.width - units.gu(4)
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
            visible: foundModel.count === 0
            text: "No .apk files found in Downloads or Documents"
        }
    }
}
