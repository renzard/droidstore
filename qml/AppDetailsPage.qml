import QtQuick 2.12
import Lomiri.Components 1.3

Page {
    id: page

    // passed in from Main.qml
    property var appRoot
    property var py
    property string pkgId: ""

    // all of the app's details (filled from store.get_details)
    property var info: ({})
    property bool loaded: false

    header: PageHeader {
        id: header
        title: page.loaded ? page.info.name : ""
    }

    // Main.qml updates the page when progress / installation state changes
    function applyProps(props) {
        var copy = {};
        for (var key in info) {
            copy[key] = info[key];
        }
        for (var k in props) {
            copy[k] = props[k];
        }
        info = copy;
    }

    function formatSize(bytes) {
        if (!bytes) {
            return "";
        }
        return (bytes / 1048576).toFixed(1) + " MB";
    }

    function formatDate(ms) {
        if (!ms) {
            return "";
        }
        return new Date(ms).toLocaleDateString(Qt.locale(), Locale.ShortFormat);
    }

    function linkHtml(url) {
        var safe = url.replace(/&/g, "&amp;");
        return "<a href=\"" + safe + "\">" + safe + "</a>";
    }

    Component.onCompleted: {
        if (appRoot) {
            appRoot.detailsPage = page;
        }
        py.call("store.get_details", [pkgId], function(result) {
            page.info = result;
            page.loaded = true;
        });
    }

    Component.onDestruction: {
        if (appRoot && appRoot.detailsPage === page) {
            appRoot.detailsPage = null;
        }
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
        visible: page.loaded

        Column {
            id: column
            width: parent.width
            spacing: units.gu(2)
            topPadding: units.gu(2)

            // ----- header: icon, name, version -----
            Row {
                x: units.gu(2)
                width: parent.width - units.gu(4)
                spacing: units.gu(2)

                Image {
                    id: iconImage
                    width: units.gu(10)
                    height: width
                    source: page.info.icon || ""
                    fillMode: Image.PreserveAspectFit
                    asynchronous: true
                }

                Column {
                    width: parent.width - iconImage.width - units.gu(2)
                    spacing: units.gu(0.5)

                    Label {
                        width: parent.width
                        text: (page.info.name || "") + (page.info.verified ? "  ✔" : "")
                        fontSize: "large"
                        wrapMode: Text.WordWrap
                    }
                    Label {
                        width: parent.width
                        visible: (page.info.author || "") !== ""
                        text: page.info.author || ""
                        fontSize: "small"
                        color: theme.palette.normal.backgroundSecondaryText
                        wrapMode: Text.WordWrap
                    }
                    Label {
                        width: parent.width
                        text: (page.info.version || "") + "  •  " + page.formatSize(page.info.size)
                              + "  •  " + (page.info.native ? "native" : "Java")
                        fontSize: "small"
                        color: theme.palette.normal.backgroundSecondaryText
                        wrapMode: Text.WordWrap
                    }
                }
            }

            // ----- buttons -----
            Row {
                x: units.gu(2)
                spacing: units.gu(1)

                Button {
                    width: units.gu(18)
                    text: page.info.installed ? "Open"
                          : (page.info.busy ? Math.round((page.info.progress || 0) * 100) + "%" : "Get")
                    enabled: !page.info.busy
                    color: page.info.installed ? theme.palette.normal.positive : theme.palette.normal.activity
                    onClicked: page.info.installed ? appRoot.launchApp(page.pkgId) : appRoot.installApp(page.pkgId)
                }
                Button {
                    visible: page.info.installed === true
                    width: units.gu(6)
                    iconName: "delete"
                    color: theme.palette.normal.negative
                    onClicked: appRoot.removeApp(page.pkgId)
                }
            }

            ProgressBar {
                x: units.gu(2)
                width: parent.width - units.gu(4)
                visible: page.info.busy === true
                minimumValue: 0
                maximumValue: 1
                value: page.info.progress || 0
            }

            // ----- screenshots -----
            ListView {
                width: parent.width
                height: visible ? units.gu(32) : 0
                visible: (page.info.screenshots || []).length > 0
                orientation: ListView.Horizontal
                spacing: units.gu(1)
                leftMargin: units.gu(2)
                rightMargin: units.gu(2)
                clip: true
                model: page.info.screenshots || []

                delegate: Image {
                    height: ListView.view.height
                    width: Math.max(units.gu(8), implicitWidth * height / Math.max(1, implicitHeight))
                    source: modelData
                    fillMode: Image.PreserveAspectFit
                    asynchronous: true
                }
            }

            // ----- description -----
            Label {
                x: units.gu(2)
                width: parent.width - units.gu(4)
                visible: text !== ""
                text: page.info.summary || ""
                fontSize: "medium"
                font.bold: true
                wrapMode: Text.WordWrap
            }

            Label {
                x: units.gu(2)
                width: parent.width - units.gu(4)
                visible: text !== ""
                text: page.info.description || ""
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
            }

            Label {
                x: units.gu(2)
                width: parent.width - units.gu(4)
                visible: page.info.hasDetail === false
                text: "No more information available. Tap \"Refresh catalog\" (top right) to download it."
                fontSize: "small"
                color: theme.palette.normal.backgroundSecondaryText
                wrapMode: Text.WordWrap
            }

            // ----- what's new -----
            Label {
                x: units.gu(2)
                width: parent.width - units.gu(4)
                visible: (page.info.whatsNew || "") !== ""
                text: "What's new"
                font.bold: true
            }
            Label {
                x: units.gu(2)
                width: parent.width - units.gu(4)
                visible: (page.info.whatsNew || "") !== ""
                text: page.info.whatsNew || ""
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
            }

            // ----- warnings (anti-features) -----
            Column {
                x: units.gu(2)
                width: parent.width - units.gu(4)
                spacing: units.gu(0.5)
                visible: (page.info.antiFeatures || []).length > 0

                Label {
                    text: "Warning"
                    font.bold: true
                    color: theme.palette.normal.negative
                }
                Repeater {
                    model: page.info.antiFeatures || []
                    Label {
                        width: parent.width
                        text: "• " + modelData
                        wrapMode: Text.WordWrap
                        fontSize: "small"
                    }
                }
            }

            // ----- information -----
            Column {
                x: units.gu(2)
                width: parent.width - units.gu(4)
                spacing: units.gu(1)

                Label {
                    text: "Information"
                    font.bold: true
                }
                Label {
                    width: parent.width
                    visible: (page.info.categories || []).length > 0
                    text: "Categories: " + (page.info.categories || []).join(", ")
                    wrapMode: Text.WordWrap
                    fontSize: "small"
                }
                Label {
                    width: parent.width
                    visible: (page.info.license || "") !== ""
                    text: "License: " + (page.info.license || "")
                    wrapMode: Text.WordWrap
                    fontSize: "small"
                }
                Label {
                    width: parent.width
                    visible: !!page.info.lastUpdated
                    text: "Last updated: " + page.formatDate(page.info.lastUpdated)
                    fontSize: "small"
                }
                Label {
                    width: parent.width
                    visible: !!page.info.added
                    text: "Added: " + page.formatDate(page.info.added)
                    fontSize: "small"
                }
                Label {
                    width: parent.width
                    visible: !!page.info.minSdk
                    text: "Minimum Android API: " + page.info.minSdk
                          + (page.info.targetSdk ? "  (target " + page.info.targetSdk + ")" : "")
                    fontSize: "small"
                }
                Label {
                    width: parent.width
                    text: "Package: " + page.pkgId
                    wrapMode: Text.WrapAnywhere
                    fontSize: "small"
                }
            }

            // ----- permissions -----
            Column {
                x: units.gu(2)
                width: parent.width - units.gu(4)
                spacing: units.gu(0.5)
                visible: (page.info.permissions || []).length > 0

                Label {
                    text: "Permissions (" + (page.info.permissions || []).length + ")"
                    font.bold: true
                }
                Label {
                    width: parent.width
                    text: (page.info.permissions || []).join("\n")
                    wrapMode: Text.WrapAnywhere
                    fontSize: "x-small"
                    color: theme.palette.normal.backgroundSecondaryText
                }
            }

            // ----- links -----
            Column {
                x: units.gu(2)
                width: parent.width - units.gu(4)
                spacing: units.gu(1)
                visible: (page.info.webSite || "") !== "" || (page.info.sourceCode || "") !== ""
                         || (page.info.issueTracker || "") !== ""

                Label {
                    text: "Links"
                    font.bold: true
                }
                Label {
                    width: parent.width
                    visible: (page.info.webSite || "") !== ""
                    text: "Website: " + page.linkHtml(page.info.webSite || "")
                    textFormat: Text.RichText
                    wrapMode: Text.WrapAnywhere
                    fontSize: "small"
                    linkColor: theme.palette.normal.activity
                    onLinkActivated: Qt.openUrlExternally(link)
                }
                Label {
                    width: parent.width
                    visible: (page.info.sourceCode || "") !== ""
                    text: "Source code: " + page.linkHtml(page.info.sourceCode || "")
                    textFormat: Text.RichText
                    wrapMode: Text.WrapAnywhere
                    fontSize: "small"
                    linkColor: theme.palette.normal.activity
                    onLinkActivated: Qt.openUrlExternally(link)
                }
                Label {
                    width: parent.width
                    visible: (page.info.issueTracker || "") !== ""
                    text: "Issue tracker: " + page.linkHtml(page.info.issueTracker || "")
                    textFormat: Text.RichText
                    wrapMode: Text.WrapAnywhere
                    fontSize: "small"
                    linkColor: theme.palette.normal.activity
                    onLinkActivated: Qt.openUrlExternally(link)
                }
            }
        }
    }

    Label {
        anchors.centerIn: parent
        visible: !page.loaded
        text: "Loading…"
    }
}
