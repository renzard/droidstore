import QtQuick 2.12
import Lomiri.Components 1.3
import io.thp.pyotherside 1.4

MainView {
    id: root
    objectName: "mainView"
    applicationName: "atlstore.renz21"
    automaticOrientation: true

    width: units.gu(45)
    height: units.gu(75)

    property string statusText: ""
    property bool catalogBusy: false
    property bool sdkMissing: false
    property bool sdkBusy: false
    property real sdkProgress: 0
    property string sdkPhase: ""
    property string pendingLaunch: ""
    property var detailsPage: null   // the open details page (if any)

    ListModel { id: storeModel }
    ListModel { id: libraryModel }

    // ---------- backend ----------
    Python {
        id: py

        Component.onCompleted: {
            addImportPath(Qt.resolvedUrl("../src/"));

            setHandler("progress", function(pkg, fraction) {
                root.updateItem(pkg, { busy: true, progress: fraction });
            });
            setHandler("installed", function(pkg) {
                root.updateItem(pkg, { busy: false, installed: true, progress: 0 });
                root.refreshLibrary();
                root.statusText = "Installation complete";
            });
            setHandler("failed", function(pkg, message) {
                root.updateItem(pkg, { busy: false, progress: 0 });
                root.statusText = message;
            });
            setHandler("catalog_progress", function(fraction) {
                root.statusText = "Downloading catalog… " + Math.round(fraction * 100) + "%";
            });
            setHandler("catalog_ready", function(count) {
                root.catalogBusy = false;
                root.statusText = count + " apps in the catalog";
                root.doSearch();
            });
            setHandler("catalog_error", function(message) {
                root.catalogBusy = false;
                root.statusText = "Catalog error: " + message;
            });

            setHandler("sdk_progress", function(phase, fraction) {
                root.sdkPhase = phase;
                root.sdkProgress = fraction;
            });
            setHandler("sdk_ready", function() {
                root.sdkBusy = false;
                root.sdkMissing = false;
                root.statusText = "ATL installed";
                if (root.pendingLaunch !== "") {
                    var pkg = root.pendingLaunch;
                    root.pendingLaunch = "";
                    root.launchApp(pkg);
                }
            });
            setHandler("sdk_error", function(message) {
                root.sdkBusy = false;
                root.sdkMissing = true;
                root.pendingLaunch = "";
                root.statusText = "ATL installation error: " + message;
            });

            importModule("store", function() {
                call("store.sdk_status", [], function(st) {
                    root.sdkMissing = !st.installed;
                    root.sdkBusy = st.busy;
                });
                call("store.has_catalog", [], function(has) {
                    if (has) {
                        root.doSearch();
                    } else {
                        root.updateCatalog();
                    }
                });
                root.refreshLibrary();
            });
        }

        onError: console.log("python error: " + traceback)
    }

    function updateCatalog() {
        catalogBusy = true;
        statusText = "Downloading catalog…";
        py.call("store.update_catalog", []);
    }

    function fillModel(model, items) {
        model.clear();
        for (var i = 0; i < items.length; i++) {
            model.append(items[i]);
        }
    }

    function doSearch() {
        py.call("store.search", [searchField.text, pureOnly.checked], function(items) {
            fillModel(storeModel, items);
        });
    }

    function refreshLibrary() {
        py.call("store.list_installed", [], function(items) {
            fillModel(libraryModel, items);
        });
    }

    function updateItem(pkg, props) {
        var models = [storeModel, libraryModel];
        for (var m = 0; m < models.length; m++) {
            for (var i = 0; i < models[m].count; i++) {
                if (models[m].get(i).pkgId === pkg) {
                    for (var key in props) {
                        models[m].setProperty(i, key, props[key]);
                    }
                }
            }
        }
        if (detailsPage && detailsPage.pkgId === pkg) {
            detailsPage.applyProps(props);
        }
    }

    function openDetails(pkg) {
        stack.push(Qt.resolvedUrl("AppDetailsPage.qml"), { appRoot: root, py: py, pkgId: pkg });
    }

    function installApp(pkg) {
        updateItem(pkg, { busy: true, progress: 0 });
        py.call("store.install", [pkg]);
    }

    function installSdk() {
        if (sdkBusy) {
            return;
        }
        sdkBusy = true;
        sdkMissing = true;
        sdkProgress = 0;
        sdkPhase = "download";
        statusText = "";
        py.call("store.install_sdk", []);
    }

    function launchApp(pkg) {
        if (sdkMissing) {
            // ATL is missing: download it automatically, then open the app
            pendingLaunch = pkg;
            installSdk();
            return;
        }
        statusText = "Launching… (the first time, system libraries are downloaded and it may take a while)";
        py.call("store.launch", [pkg], function(result) {
            if (!result.ok && result.needs_sdk) {
                root.sdkMissing = true;
                root.pendingLaunch = pkg;
                root.installSdk();
                return;
            }
            statusText = result.ok ? "" : result.error;
        });
    }

    function removeApp(pkg) {
        py.call("store.remove", [pkg], function() {
            updateItem(pkg, { installed: false });
            refreshLibrary();
        });
    }

    // ---------- UI ----------
    PageStack {
        id: stack
        Component.onCompleted: push(mainPage)

        Page {
            id: mainPage
            visible: false

            header: PageHeader {
                id: header
                title: "ATL Store"

                trailingActionBar.actions: [
                    Action {
                        iconName: "add"
                        text: "Add APK"
                        onTriggered: stack.push(Qt.resolvedUrl("ImportPage.qml"), { appRoot: root, py: py })
                    },
                    Action {
                        iconName: "reload"
                        text: "Refresh catalog"
                        enabled: !root.catalogBusy
                        onTriggered: root.updateCatalog()
                    },
                    Action {
                        iconName: "settings"
                        text: "Settings"
                        onTriggered: stack.push(Qt.resolvedUrl("SettingsPage.qml"), { py: py })
                    }
                ]

                extension: Sections {
                    id: sections
                    anchors {
                        left: parent.left
                        leftMargin: units.gu(2)
                        bottom: parent.bottom
                    }
                    model: ["Store", "Installed"]
                }
            }

            // status line + ATL message
            Column {
                id: topInfo
                anchors {
                    top: header.bottom
                    left: parent.left
                    right: parent.right
                    margins: units.gu(1)
                }
                spacing: units.gu(1)

                Label {
                    id: statusLabel
                    width: parent.width
                    visible: text !== ""
                    text: root.statusText
                    wrapMode: Text.WordWrap
                    fontSize: "small"
                    color: theme.palette.normal.backgroundSecondaryText
                }

                Column {
                    id: sdkBanner
                    width: parent.width
                    visible: root.sdkMissing || root.sdkBusy
                    spacing: units.gu(1)

                    Label {
                        width: parent.width
                        wrapMode: Text.WordWrap
                        text: root.sdkBusy
                              ? (root.sdkPhase === "extract" ? "Extracting ATL… " : "Downloading ATL… ")
                                + Math.round(root.sdkProgress * 100) + "%"
                              : "ATL (about 50 MB) is needed to open apps."
                    }
                    ProgressBar {
                        width: parent.width
                        visible: root.sdkBusy
                        minimumValue: 0
                        maximumValue: 1
                        value: root.sdkProgress
                    }
                    Button {
                        visible: !root.sdkBusy
                        text: "Install ATL"
                        color: theme.palette.normal.positive
                        onClicked: root.installSdk()
                    }
                }
            }

            // ----- Store -----
            Item {
                id: storeTab
                visible: sections.selectedIndex === 0
                anchors {
                    top: topInfo.bottom
                    left: parent.left
                    right: parent.right
                    bottom: parent.bottom
                }

                TextField {
                    id: searchField
                    anchors {
                        top: parent.top
                        left: parent.left
                        right: parent.right
                        margins: units.gu(1)
                    }
                    placeholderText: "Search apps…"
                    inputMethodHints: Qt.ImhNoPredictiveText
                    onTextChanged: searchTimer.restart()
                }

                Row {
                    id: filterRow
                    anchors {
                        top: searchField.bottom
                        left: parent.left
                        margins: units.gu(1)
                    }
                    spacing: units.gu(1)

                    CheckBox {
                        id: pureOnly
                        onCheckedChanged: root.doSearch()
                    }
                    Label {
                        anchors.verticalCenter: pureOnly.verticalCenter
                        text: "Only without native code (more compatible)"
                        fontSize: "small"
                    }
                }

                ListView {
                    anchors {
                        top: filterRow.bottom
                        left: parent.left
                        right: parent.right
                        bottom: parent.bottom
                        topMargin: units.gu(1)
                    }
                    clip: true
                    model: storeModel

                    delegate: ListItem {
                        height: units.gu(9)
                        onClicked: root.openDetails(model.pkgId)

                        ListItemLayout {
                            title.text: model.name + (model.verified ? "  ✔" : "")
                            subtitle.text: model.summary
                            summary.text: model.version + (model.native ? " • native" : " • Java")

                            Image {
                                SlotsLayout.position: SlotsLayout.Leading
                                width: units.gu(5)
                                height: width
                                source: model.icon
                                fillMode: Image.PreserveAspectFit
                                asynchronous: true
                            }

                            Button {
                                SlotsLayout.position: SlotsLayout.Trailing
                                width: units.gu(11)
                                text: model.installed ? "Open"
                                      : (model.busy ? Math.round(model.progress * 100) + "%" : "Get")
                                enabled: !model.busy
                                color: model.installed ? theme.palette.normal.positive : theme.palette.normal.activity
                                onClicked: model.installed ? root.launchApp(model.pkgId) : root.installApp(model.pkgId)
                            }
                        }
                    }

                    Label {
                        anchors.centerIn: parent
                        visible: storeModel.count === 0 && !root.catalogBusy
                        text: "No apps found"
                    }
                }
            }

            // ----- Installed -----
            ListView {
                id: libraryList
                visible: sections.selectedIndex === 1
                anchors {
                    top: topInfo.bottom
                    left: parent.left
                    right: parent.right
                    bottom: parent.bottom
                }
                clip: true
                model: libraryModel

                delegate: ListItem {
                    height: units.gu(9)
                    onClicked: root.openDetails(model.pkgId)

                    ListItemLayout {
                        title.text: model.name
                        subtitle.text: model.version + (model.local ? " • your own APK" : "")

                        Image {
                            SlotsLayout.position: SlotsLayout.Leading
                            width: units.gu(5)
                            height: width
                            source: model.icon
                            fillMode: Image.PreserveAspectFit
                            asynchronous: true
                        }

                        Row {
                            SlotsLayout.position: SlotsLayout.Trailing
                            spacing: units.gu(1)

                            Button {
                                width: units.gu(11)
                                text: "Open"
                                color: theme.palette.normal.positive
                                onClicked: root.launchApp(model.pkgId)
                            }
                            Button {
                                width: units.gu(5)
                                iconName: "delete"
                                color: theme.palette.normal.negative
                                onClicked: root.removeApp(model.pkgId)
                            }
                        }
                    }
                }

                Label {
                    anchors.centerIn: parent
                    visible: libraryModel.count === 0
                    text: "You haven't installed any apps yet.\nTap + above to add your own APK"
                }
            }

            Timer {
                id: searchTimer
                interval: 350
                onTriggered: root.doSearch()
            }
        }
    }
}
