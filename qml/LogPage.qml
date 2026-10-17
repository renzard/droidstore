import QtQuick 2.12
import Lomiri.Components 1.3

Page {
    id: page

    // passed in from Main.qml
    property var py
    property string pkgId: ""

    property bool fullLines: false      // false: very long lines are cut (the dex2oat command line is huge)
    property int selectedCount: 0
    property int lastTapped: -1
    property string notice: ""
    property string summary: ""         // why the last launch ended, searched in the whole log

    header: PageHeader {
        id: header
        title: "Log"
        trailingActionBar.actions: [
            Action {
                iconName: "go-up"
                text: "Top"
                onTriggered: list.positionViewAtBeginning()
            },
            Action {
                iconName: "go-down"
                text: "Bottom"
                onTriggered: list.positionViewAtEnd()
            },
            Action {
                iconName: "reload"
                text: "Reload"
                onTriggered: page.load()
            }
        ]
    }

    ListModel { id: linesModel }

    function load() {
        py.call("store.get_launch_summary", [pkgId], function(text) {
            summary = text || "";
        });
        py.call("store.get_launch_log", [pkgId, 1000, fullLines ? 0 : 300, false], function(text) {
            linesModel.clear();
            var lines = (text !== "" ? text : "(no log yet: launch the app first)").split("\n");
            for (var i = 0; i < lines.length; i++) {
                linesModel.append({ lineText: lines[i], picked: false });
            }
            selectedCount = 0;
            lastTapped = -1;
            notice = lines.length + " lines";
            list.positionViewAtEnd();
        });
    }

    function countSelected() {
        var n = 0;
        for (var i = 0; i < linesModel.count; i++) {
            if (linesModel.get(i).picked) n++;
        }
        selectedCount = n;
    }

    function setAll(value) {
        for (var i = 0; i < linesModel.count; i++) {
            linesModel.setProperty(i, "picked", value);
        }
        countSelected();
    }

    function copyLines(onlySelected) {
        var out = [];
        for (var i = 0; i < linesModel.count; i++) {
            var item = linesModel.get(i);
            if (!onlySelected || item.picked) out.push(item.lineText);
        }
        if (out.length === 0) {
            notice = "Nothing selected";
            return;
        }
        if (!onlySelected && summary !== "") out.unshift(summary, "");
        Clipboard.push(out.join("\n"));
        notice = "Copied " + out.length + (out.length === 1 ? " line" : " lines");
    }

    Component.onCompleted: load()

    Column {
        id: toolbar
        anchors {
            top: header.bottom
            left: parent.left
            right: parent.right
            margins: units.gu(1)
        }
        spacing: units.gu(1)

        Flow {
            width: parent.width
            spacing: units.gu(1)

            Button {
                text: "Copy all"
                color: theme.palette.normal.positive
                onClicked: page.copyLines(false)
            }
            Button {
                text: "Copy selected (" + page.selectedCount + ")"
                enabled: page.selectedCount > 0
                onClicked: page.copyLines(true)
            }
            Button {
                text: "Select all"
                onClicked: page.setAll(true)
            }
            Button {
                text: "Clear"
                enabled: page.selectedCount > 0
                onClicked: page.setAll(false)
            }
            Button {
                text: page.fullLines ? "Cut long lines" : "Show full lines"
                onClicked: {
                    page.fullLines = !page.fullLines;
                    page.load();
                }
            }
        }

        Rectangle {
            width: parent.width
            height: causeLabel.implicitHeight + units.gu(1.5)
            visible: page.summary !== ""
            color: "transparent"
            border.color: theme.palette.normal.negative
            border.width: units.dp(1)
            radius: units.dp(4)

            Label {
                id: causeLabel
                anchors {
                    left: parent.left
                    right: parent.right
                    verticalCenter: parent.verticalCenter
                    margins: units.gu(0.75)
                }
                text: page.summary
                wrapMode: Text.WrapAnywhere
                fontSize: "x-small"
                color: theme.palette.normal.backgroundText
            }
        }

        Label {
            width: parent.width
            text: page.notice + "  ·  tap a line to select it, press and hold to select from the last tapped line"
            fontSize: "x-small"
            wrapMode: Text.WordWrap
            color: theme.palette.normal.backgroundSecondaryText
        }
    }

    ListView {
        id: list
        anchors {
            top: toolbar.bottom
            topMargin: units.gu(1)
            left: parent.left
            right: parent.right
            bottom: parent.bottom
        }
        clip: true
        model: linesModel
        boundsBehavior: Flickable.StopAtBounds

        delegate: Rectangle {
            width: list.width
            height: lineLabel.implicitHeight + units.gu(0.5)
            color: picked ? theme.palette.selected.background : "transparent"

            Label {
                id: lineLabel
                anchors {
                    left: parent.left
                    right: parent.right
                    verticalCenter: parent.verticalCenter
                    leftMargin: units.gu(1)
                    rightMargin: units.gu(1)
                }
                text: lineText
                wrapMode: Text.WrapAnywhere
                font.family: "Ubuntu Mono"
                fontSize: "x-small"
                color: picked ? theme.palette.selected.backgroundText : theme.palette.normal.backgroundText
            }

            MouseArea {
                anchors.fill: parent
                onClicked: {
                    linesModel.setProperty(index, "picked", !picked);
                    page.lastTapped = index;
                    page.countSelected();
                }
                onPressAndHold: {
                    var from = page.lastTapped >= 0 ? Math.min(page.lastTapped, index) : index;
                    var to = page.lastTapped >= 0 ? Math.max(page.lastTapped, index) : index;
                    for (var i = from; i <= to; i++) {
                        linesModel.setProperty(i, "picked", true);
                    }
                    page.lastTapped = index;
                    page.countSelected();
                }
            }
        }

    }
}
