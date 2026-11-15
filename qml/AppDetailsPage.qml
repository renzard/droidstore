import QtQuick 2.12
import Lomiri.Components 1.3
import "."

Page {
    id: page

    // passed in from Main.qml
    property var appRoot
    property var py
    property string pkgId: ""

    // all of the app's details (filled from store.get_details)
    property var info: ({})
    property bool loaded: false
    property bool entered: false
    property bool descExpanded: false

    // lists that never change while the page is open (kept apart from `info` so progress updates do not reset them)
    property var shots: []
    property var antiList: []
    property var catList: []
    property var permList: []

    header: PageHeader { visible: false }

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

    function remaining() {
        var size = info.size || 0;
        var left = size * (1 - (info.progress || 0));
        return size > 0 ? (left / 1048576).toFixed(2) + " MB remaining" : "";
    }

    Component.onCompleted: {
        if (appRoot) {
            appRoot.detailsPage = page;
        }
        entered = true;
        py.call("store.get_details", [pkgId], function(result) {
            page.shots = result.screenshots || [];
            page.antiList = result.antiFeatures || [];
            page.catList = result.categories || [];
            page.permList = result.permissions || [];
            page.info = result;
            page.loaded = true;
        });
    }

    Component.onDestruction: {
        if (appRoot && appRoot.detailsPage === page) {
            appRoot.detailsPage = null;
        }
    }

    Rectangle { anchors.fill: parent; color: Pal.bg }

    // the whole page slides + fades in when it opens
    Item {
        id: body
        width: parent.width   // (no anchors: x is animated)
        height: parent.height
        opacity: page.entered ? 1 : 0
        x: page.entered ? 0 : units.gu(6)
        Behavior on opacity { NumberAnimation { duration: Pal.med; easing.type: Easing.OutCubic } }
        Behavior on x { NumberAnimation { duration: Pal.slow; easing.type: Easing.OutCubic } }

        // ---------- top bar ----------
        Item {
            id: topBar
            anchors { top: parent.top; left: parent.left; right: parent.right }
            height: units.gu(7.5)
            z: 3

            Rectangle {
                anchors.fill: parent
                color: Pal.bg
            }

            Ripple {
                id: backButton
                anchors { left: parent.left; leftMargin: units.gu(1); verticalCenter: parent.verticalCenter }
                width: units.gu(5.6)
                height: width
                radius: width / 2
                onClicked: appRoot ? appRoot.goBack() : undefined

                Label {
                    anchors.centerIn: parent
                    text: "←"
                    font.pixelSize: units.gu(3)
                    color: Pal.text
                }
            }

            // the name fades in once the big title has scrolled away
            Label {
                anchors { left: backButton.right; leftMargin: units.gu(1); right: parent.right; rightMargin: units.gu(2); verticalCenter: parent.verticalCenter }
                text: page.info.name || ""
                font.pixelSize: units.gu(2.4)
                color: Pal.text
                elide: Text.ElideRight
                opacity: Math.max(0, Math.min(1, (flick.contentY - units.gu(8)) / units.gu(6)))
            }

            Rectangle {
                anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
                height: units.dp(1)
                color: Pal.outline
                opacity: Math.max(0, Math.min(1, flick.contentY / units.gu(4)))
            }
        }

        Flickable {
            id: flick
            anchors { top: topBar.bottom; left: parent.left; right: parent.right; bottom: parent.bottom }
            contentHeight: column.height + units.gu(4)
            clip: true
            visible: page.loaded

            Column {
                id: column
                width: parent.width

                // ----- hero: icon, name, author, summary -----
                Reveal {
                    order: 0
                    active: page.loaded && page.entered

                    Item {
                        width: parent.width
                        height: heroRow.height + units.gu(2)

                        Row {
                            id: heroRow
                            x: units.gu(2)
                            y: units.gu(1)
                            width: parent.width - units.gu(4)
                            spacing: units.gu(2)

                            AppIcon {
                                id: heroIcon
                                width: units.gu(11)
                                height: width
                                source: page.info.icon || ""
                                letter: page.info.name || ""
                                scale: page.loaded ? 1 : 0.6
                                Behavior on scale { NumberAnimation { duration: Pal.slow; easing.type: Easing.OutBack } }
                            }

                            Column {
                                width: parent.width - heroIcon.width - units.gu(2)
                                spacing: units.gu(0.5)

                                Label {
                                    width: parent.width
                                    text: page.info.name || ""
                                    font.pixelSize: units.gu(3.4)
                                    color: Pal.text
                                    wrapMode: Text.WordWrap
                                }
                                Label {
                                    width: parent.width
                                    visible: (page.info.author || "") !== ""
                                    text: "by " + (page.info.author || "")
                                    font.pixelSize: units.gu(1.9)
                                    color: Pal.primary
                                    wrapMode: Text.WordWrap
                                }
                                Label {
                                    width: parent.width
                                    text: (page.info.lastUpdated ? "Last updated: " + page.formatDate(page.info.lastUpdated) + "  " : "")
                                          + "(" + page.formatSize(page.info.size) + ")"
                                    font.pixelSize: units.gu(1.7)
                                    color: Pal.textDim
                                    wrapMode: Text.WordWrap
                                }
                            }
                        }
                    }
                    Label {
                        x: units.gu(2)
                        width: parent.width - units.gu(4)
                        visible: text !== ""
                        text: page.info.summary || ""
                        font.pixelSize: units.gu(2.3)
                        color: Pal.text
                        wrapMode: Text.WordWrap
                    }
                }

                // ----- tags (categories open the Search tab with that category) -----
                Reveal {
                    order: 1
                    active: page.loaded && page.entered
                    visible: page.catList.length > 0 || page.info.verified === true

                    Item { width: parent.width; height: units.gu(2) }
                    Flow {
                        x: units.gu(2)
                        width: parent.width - units.gu(4)
                        spacing: units.gu(1)

                        Chip {
                            visible: page.info.verified === true
                            text: "Works with ATL"
                            glyph: "✔"
                            selected: true
                        }
                        Repeater {
                            model: page.catList
                            delegate: Chip {
                                text: modelData
                                onClicked: { appRoot.openCategory(modelData); appRoot.goBack(); }
                            }
                        }
                    }
                }

                // ----- install / update / open -----
                Reveal {
                    order: 2
                    active: page.loaded && page.entered

                    Item { width: parent.width; height: units.gu(2.5) }

                    Item {
                        id: actionArea
                        x: units.gu(2)
                        width: parent.width - units.gu(4)
                        height: page.info.busy ? units.gu(7.5) : buttonColumn.height
                        Behavior on height { NumberAnimation { duration: Pal.med; easing.type: Easing.OutCubic } }

                        Column {
                            id: buttonColumn
                            width: parent.width
                            spacing: units.gu(1.2)
                            opacity: page.info.busy ? 0 : 1
                            visible: opacity > 0
                            Behavior on opacity { NumberAnimation { duration: Pal.med } }

                            Row {
                                spacing: units.gu(1.2)

                                ActionButton {
                                    height: units.gu(5.8)
                                    minWidth: page.info.installed ? units.gu(18) : actionArea.width
                                    label: page.info.updateAvailable ? "Update" : (page.info.installed ? "Open" : "Install")
                                    onClicked: (page.info.installed && !page.info.updateAvailable)
                                               ? appRoot.launchApp(page.pkgId) : appRoot.installApp(page.pkgId)
                                }
                                ActionButton {
                                    visible: page.info.updateAvailable === true
                                    height: units.gu(5.8)
                                    tonal: true
                                    label: "Open"
                                    onClicked: appRoot.launchApp(page.pkgId)
                                }
                                ActionButton {
                                    visible: page.info.installed === true
                                    height: units.gu(5.8)
                                    tonal: true
                                    danger: true
                                    label: "Remove"
                                    onClicked: appRoot.removeApp(page.pkgId)
                                }
                            }

                            ActionButton {
                                visible: page.info.installed === true
                                tonal: true
                                label: "Add to app drawer"
                                onClicked: appRoot.addToDrawer(page.pkgId)
                            }
                        }

                        // download state: percentage, MB left, progress bar
                        Column {
                            width: parent.width
                            spacing: units.gu(1.2)
                            opacity: page.info.busy ? 1 : 0
                            visible: opacity > 0
                            Behavior on opacity { NumberAnimation { duration: Pal.med } }

                            Item {
                                width: parent.width
                                height: units.gu(3)
                                Label {
                                    anchors.left: parent.left
                                    text: "Downloading… " + Math.round((page.info.progress || 0) * 100) + "%"
                                    font.pixelSize: units.gu(2)
                                    color: Pal.textDim
                                }
                                Label {
                                    anchors.right: parent.right
                                    text: page.remaining()
                                    font.pixelSize: units.gu(2)
                                    color: Pal.textDim
                                }
                            }
                            ProgressLine {
                                width: parent.width
                                value: page.info.progress || 0
                            }
                        }
                    }
                }

                // ----- screenshots -----
                Reveal {
                    order: 3
                    active: page.loaded && page.entered
                    visible: page.shots.length > 0

                    Item { width: parent.width; height: units.gu(3) }
                    ListView {
                        width: parent.width
                        height: units.gu(34)
                        orientation: ListView.Horizontal
                        spacing: units.gu(1.2)
                        leftMargin: units.gu(2)
                        rightMargin: units.gu(2)
                        clip: true
                        model: page.shots

                        delegate: Rectangle {
                            height: ListView.view.height
                            width: Math.max(units.gu(14), shot.implicitWidth * height / Math.max(1, shot.implicitHeight))
                            radius: units.gu(1.8)
                            color: Pal.surface
                            border.width: units.dp(1)
                            border.color: Pal.outline
                            clip: true

                            Image {
                                id: shot
                                anchors.fill: parent
                                anchors.margins: units.dp(1)
                                source: modelData
                                fillMode: Image.PreserveAspectCrop
                                asynchronous: true
                                sourceSize.height: units.gu(34) * 2
                                opacity: status === Image.Ready ? 1 : 0
                                Behavior on opacity { NumberAnimation { duration: Pal.slow } }
                            }
                        }
                    }
                }

                // ----- anti-features -----
                Reveal {
                    order: 4
                    active: page.loaded && page.entered
                    visible: page.antiList.length > 0

                    Item { width: parent.width; height: units.gu(3) }
                    Item {
                        id: antiBox
                        property bool open: false
                        x: units.gu(2)
                        width: parent.width - units.gu(4)
                        height: antiHead.height + antiBody.height

                        Rectangle { anchors.fill: parent; radius: units.gu(2.2); color: Pal.warnBg }

                        Ripple {
                            id: antiHead
                            width: parent.width
                            height: units.gu(7.5)
                            radius: units.gu(2.2)
                            stateColor: "#22000000"
                            onClicked: antiBox.open = !antiBox.open

                            Label {
                                id: warnIcon
                                anchors { left: parent.left; leftMargin: units.gu(2); verticalCenter: parent.verticalCenter }
                                text: "⚠"
                                font.pixelSize: units.gu(3)
                                color: Pal.warnText
                            }
                            Label {
                                anchors { left: warnIcon.right; leftMargin: units.gu(1.5); right: antiChevron.left; verticalCenter: parent.verticalCenter }
                                text: "This app has anti-features"
                                font.pixelSize: units.gu(2.3)
                                color: Pal.warnText
                                elide: Text.ElideRight
                            }
                            Label {
                                id: antiChevron
                                anchors { right: parent.right; rightMargin: units.gu(2.5); verticalCenter: parent.verticalCenter }
                                text: "›"
                                font.pixelSize: units.gu(3.4)
                                color: Pal.warnText
                                rotation: antiBox.open ? 90 : 0
                                Behavior on rotation { NumberAnimation { duration: Pal.med; easing.type: Easing.OutCubic } }
                            }
                        }

                        Item {
                            id: antiBody
                            y: antiHead.height
                            width: parent.width
                            height: antiBox.open ? antiColumn.height + units.gu(2) : 0
                            opacity: antiBox.open ? 1 : 0
                            clip: true
                            Behavior on height { NumberAnimation { duration: Pal.med; easing.type: Easing.OutCubic } }
                            Behavior on opacity { NumberAnimation { duration: Pal.med } }

                            Column {
                                id: antiColumn
                                x: units.gu(2)
                                width: parent.width - units.gu(4)
                                spacing: units.gu(0.8)
                                Repeater {
                                    model: page.antiList
                                    delegate: Label {
                                        width: antiColumn.width
                                        text: "•  " + modelData
                                        wrapMode: Text.WordWrap
                                        font.pixelSize: units.gu(1.9)
                                        color: Pal.warnText
                                    }
                                }
                            }
                        }
                    }
                }

                // ----- description (collapsed to a few lines, "More" opens it with an animation) -----
                Reveal {
                    order: 5
                    active: page.loaded && page.entered
                    visible: (page.info.description || "") !== ""

                    Item { width: parent.width; height: units.gu(3) }
                    Item {
                        x: units.gu(2)
                        width: parent.width - units.gu(4)
                        height: descWrap.height + (descMeasure.implicitHeight > units.gu(13) ? units.gu(5.5) : 0)

                        Label {
                            id: descMeasure
                            width: parent.width
                            visible: false
                            text: page.info.description || ""
                            textFormat: Text.PlainText
                            wrapMode: Text.WordWrap
                            font.pixelSize: units.gu(1.95)
                        }

                        Item {
                            id: descWrap
                            width: parent.width
                            height: page.descExpanded ? descMeasure.implicitHeight : Math.min(descMeasure.implicitHeight, units.gu(13))
                            clip: true
                            Behavior on height { NumberAnimation { duration: Pal.slow; easing.type: Easing.OutCubic } }

                            Label {
                                width: parent.width
                                text: page.info.description || ""
                                textFormat: Text.PlainText
                                wrapMode: Text.WordWrap
                                font.pixelSize: units.gu(1.95)
                                color: Pal.text
                            }

                            Rectangle {
                                anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
                                height: units.gu(5)
                                opacity: (page.descExpanded || descMeasure.implicitHeight <= units.gu(13)) ? 0 : 1
                                Behavior on opacity { NumberAnimation { duration: Pal.med } }
                                gradient: Gradient {
                                    GradientStop { position: 0.0; color: "transparent" }
                                    GradientStop { position: 1.0; color: Pal.bg }
                                }
                            }
                        }

                        Ripple {
                            anchors { horizontalCenter: parent.horizontalCenter; top: descWrap.bottom; topMargin: units.gu(0.5) }
                            visible: descMeasure.implicitHeight > units.gu(13)
                            width: moreLabel.width + units.gu(4)
                            height: units.gu(4.5)
                            radius: height / 2
                            onClicked: page.descExpanded = !page.descExpanded

                            Label {
                                id: moreLabel
                                anchors.centerIn: parent
                                text: page.descExpanded ? "Less" : "More"
                                font.pixelSize: units.gu(2)
                                color: Pal.primary
                            }
                        }
                    }

                    Label {
                        x: units.gu(2)
                        width: parent.width - units.gu(4)
                        visible: page.info.hasDetail === false
                        text: "No more information available. Tap refresh on the main screen to download it."
                        font.pixelSize: units.gu(1.7)
                        color: Pal.textDim
                        wrapMode: Text.WordWrap
                    }
                }

                // ----- what's new -----
                Reveal {
                    order: 6
                    active: page.loaded && page.entered
                    visible: (page.info.whatsNew || "") !== ""

                    Item { width: parent.width; height: units.gu(3) }
                    Label {
                        x: units.gu(2)
                        width: parent.width - units.gu(4)
                        text: "What's new"
                        font.pixelSize: units.gu(2.6)
                        color: Pal.text
                    }
                    Item { width: parent.width; height: units.gu(1) }
                    Label {
                        x: units.gu(2)
                        width: parent.width - units.gu(4)
                        text: page.info.whatsNew || ""
                        textFormat: Text.PlainText
                        wrapMode: Text.WordWrap
                        font.pixelSize: units.gu(1.9)
                        color: Pal.textDim
                    }
                }

                // ----- collapsible sections -----
                Reveal {
                    order: 7
                    active: page.loaded && page.entered

                    Item { width: parent.width; height: units.gu(2) }

                    Collapsible {
                        title: "Links"
                        glyph: "🔗"
                        visible: (page.info.webSite || "") !== "" || (page.info.sourceCode || "") !== ""
                                 || (page.info.issueTracker || "") !== "" || (page.info.license || "") !== ""

                        InfoRow {
                            visible: (page.info.webSite || "") !== ""
                            glyph: "🏠"
                            caption: "Website"
                            value: page.info.webSite || ""
                            tappable: true
                            onClicked: Qt.openUrlExternally(page.info.webSite)
                        }
                        InfoRow {
                            visible: (page.info.issueTracker || "") !== ""
                            glyph: "✎"
                            caption: "Issues"
                            value: page.info.issueTracker || ""
                            tappable: true
                            onClicked: Qt.openUrlExternally(page.info.issueTracker)
                        }
                        InfoRow {
                            visible: (page.info.sourceCode || "") !== ""
                            glyph: "‹›"
                            caption: "Source code"
                            value: page.info.sourceCode || ""
                            tappable: true
                            onClicked: Qt.openUrlExternally(page.info.sourceCode)
                        }
                        InfoRow {
                            visible: (page.info.license || "") !== ""
                            glyph: "©"
                            caption: "License"
                            value: page.info.license || ""
                        }
                    }

                    Collapsible {
                        title: "Information"
                        glyph: "ⓘ"

                        InfoRow { glyph: "▣"; caption: "Package"; value: page.pkgId }
                        InfoRow { glyph: "#"; caption: "Version"; value: page.info.version || ""; visible: (page.info.version || "") !== "" }
                        InfoRow { glyph: "⇩"; caption: "Size"; value: page.formatSize(page.info.size); visible: !!page.info.size }
                        InfoRow {
                            glyph: "⚙"
                            caption: "Code"
                            value: page.info.native ? "Contains native code (may not run under ATL)" : "Java / Kotlin only"
                        }
                        InfoRow {
                            glyph: "▤"
                            caption: "Android API"
                            value: "min " + page.info.minSdk + (page.info.targetSdk ? ", target " + page.info.targetSdk : "")
                            visible: !!page.info.minSdk
                        }
                        InfoRow { glyph: "＋"; caption: "Added"; value: page.formatDate(page.info.added); visible: !!page.info.added }
                        InfoRow { glyph: "↻"; caption: "Last updated"; value: page.formatDate(page.info.lastUpdated); visible: !!page.info.lastUpdated }
                    }

                    Collapsible {
                        title: "Permissions (" + page.permList.length + ")"
                        glyph: "🔑"
                        visible: page.permList.length > 0

                        InfoRow {
                            glyph: ""
                            value: page.permList.join("\n")
                        }
                    }
                }
            }
        }

        Label {
            anchors.centerIn: parent
            opacity: page.loaded ? 0 : 1
            visible: opacity > 0
            Behavior on opacity { NumberAnimation { duration: Pal.med } }
            text: "Loading…"
            font.pixelSize: units.gu(2.2)
            color: Pal.textDim
        }
    }
}
