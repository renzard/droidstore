import QtQuick 2.12
import Lomiri.Components 1.3
import io.thp.pyotherside 1.4
import "."

MainView {
    id: root
    objectName: "mainView"
    applicationName: "droidstore.renz21"
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
    property string lastRunPkg: ""   // last app launched: its log can be opened
    property var detailsPage: null   // the open details page (if any)
    property int updatesCount: 0     // installed apps with a newer version in the catalog
    readonly property int pageSize: 60
    property bool storeHasMore: false   // the last page was full: there may be more apps
    property bool storeLoading: false
    property int searchToken: 0         // ignores answers that belong to an older search
    property bool compatOnly: false     // "No native code" filter chip
    property string searchCategory: ""  // active category filter
    property string searchSort: ""      // "", "new" or "updated" (Discover's "see all")
    property int tab: 0                 // 0 Discover, 1 Search, 2 My apps
    property var searchHistory: []
    property bool discoverLoaded: false
    property int totalApps: 0
    property bool menuOpen: false
    property bool toastDismissed: false
    property string toastText: ""
    readonly property bool browsing: searchField.text === "" && searchCategory === "" && searchSort === ""

    onStatusTextChanged: {
        if (statusText !== "") {
            toastText = statusText;
        }
        toastDismissed = false;
        toastTimer.restart();
    }
    onTabChanged: {
        menuOpen = false;
        if (tab !== 1) {
            searchField.focus = false;
        }
    }

    ListModel { id: storeModel }
    ListModel { id: libraryModel }
    ListModel { id: newModel }
    ListModel { id: updatedModel }
    ListModel { id: verifiedModel }
    ListModel { id: categoryModel }

    // ---------- backend ----------
    Python {
        id: py

        Component.onCompleted: {
            addImportPath(Qt.resolvedUrl("../src/"));

            setHandler("progress", function(pkg, fraction) {
                root.updateItem(pkg, { busy: true, progress: fraction });
            });
            setHandler("installed", function(pkg) {
                root.updateItem(pkg, { busy: false, installed: true, updateAvailable: false, newVersion: "", progress: 0 });
                root.refreshLibrary();
                root.statusText = "Installation complete";
                call("store.add_shortcut", [pkg], function(r) {
                    if (!r.ok && r.error.indexOf("turned off") < 0 && r.error.indexOf("not allowed") < 0)
                        root.statusText = "Installed, but not added to the app drawer: " + r.error;
                });
            });
            setHandler("shortcuts_synced", function(ok, created, removed, error) {
                if (!ok && error.indexOf("not allowed") < 0) root.statusText = "App drawer: " + error;
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
                root.loadDiscover();
                root.refreshLibrary();  // new catalog: the update badges may change
            });
            setHandler("catalog_error", function(message) {
                root.catalogBusy = false;
                root.statusText = "Catalog error: " + message;
            });

            setHandler("app_exited", function(pkg, code, runtime, description, tail) {
                root.lastRunPkg = pkg;
                if (code !== 0) {
                    root.statusText = "The app closed after " + runtime + "s (" + description + "). "
                        + "Tap here to see the log.";
                }
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
                        root.loadDiscover();
                    } else {
                        root.updateCatalog();
                    }
                });
                root.refreshLibrary();
                root.loadHistory();
                call("store.sync_shortcuts_async", []);
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

    // Brings a model in line with a new list WITHOUT clearing it: rows that stay are updated in place,
    // so list animations only play for rows that really appear or disappear.
    function syncModel(model, items) {
        var ids = {};
        var i, j, k;
        for (i = 0; i < items.length; i++) {
            ids[items[i].pkgId] = true;
        }
        for (i = model.count - 1; i >= 0; i--) {
            if (!ids[model.get(i).pkgId]) {
                model.remove(i);
            }
        }
        for (i = 0; i < items.length; i++) {
            if (i < model.count && model.get(i).pkgId === items[i].pkgId) {
                for (k in items[i]) {
                    model.setProperty(i, k, items[i][k]);
                }
                continue;
            }
            var found = -1;
            for (j = i + 1; j < model.count; j++) {
                if (model.get(j).pkgId === items[i].pkgId) {
                    found = j;
                    break;
                }
            }
            if (found >= 0) {
                model.move(found, i, 1);
                for (k in items[i]) {
                    model.setProperty(i, k, items[i][k]);
                }
            } else {
                model.insert(i, items[i]);
            }
        }
    }

    function loadDiscover() {
        py.call("store.discover", [12], function(d) {
            syncModel(newModel, d["new"]);
            syncModel(updatedModel, d["updated"]);
            syncModel(verifiedModel, d["verified"]);
            var cats = [];
            for (var c = 0; c < d["categories"].length; c++) {
                cats.push({ name: d["categories"][c].name, apps: d["categories"][c].count });
            }
            fillModel(categoryModel, cats);
            root.totalApps = d["total"];
            root.discoverLoaded = true;
        });
    }

    function loadHistory() {
        py.call("store.get_search_history", [], function(h) { root.searchHistory = h; });
    }

    function addHistory(query) {
        if ((query || "").trim().length < 2) {
            return;
        }
        py.call("store.add_search_history", [query], function(h) { root.searchHistory = h; });
    }

    function clearHistory() {
        py.call("store.clear_search_history", [], function(h) { root.searchHistory = h; });
    }

    function openCategory(name) {
        searchCategory = name;
        searchSort = "";
        tab = 1;
        doSearch();
    }

    function openSort(mode) {
        searchSort = mode;
        searchCategory = "";
        tab = 1;
        doSearch();
    }

    function setCategory(name) {
        searchCategory = name;
        doSearch();
    }

    function setSort(mode) {
        searchSort = mode;
        doSearch();
    }

    function doSearch() {
        var token = ++searchToken;
        if (browsing) {  // nothing typed, no filter: the Search tab shows history + categories instead
            storeModel.clear();
            storeHasMore = false;
            storeLoading = false;
            return;
        }
        storeLoading = true;
        py.call("store.search", [searchField.text, compatOnly, pageSize, 0, searchCategory, searchSort], function(items) {
            if (token !== root.searchToken) {
                return;
            }
            fillModel(storeModel, items);
            root.storeHasMore = items.length === root.pageSize;
            root.storeLoading = false;
        });
    }

    // next page of the same search, when the end of the list is reached
    function loadMore() {
        if (!storeHasMore || storeLoading || browsing) {
            return;
        }
        var token = searchToken;
        storeLoading = true;
        py.call("store.search", [searchField.text, compatOnly, pageSize, storeModel.count, searchCategory, searchSort], function(items) {
            if (token !== root.searchToken) {
                return;
            }
            for (var i = 0; i < items.length; i++) {
                storeModel.append(items[i]);
            }
            root.storeHasMore = items.length === root.pageSize;
            root.storeLoading = false;
        });
    }

    function refreshLibrary() {
        py.call("store.list_installed", [], function(items) {
            syncModel(libraryModel, items);
            var n = 0;
            for (var i = 0; i < items.length; i++) {
                if (items[i].updateAvailable) {
                    n++;
                }
            }
            root.updatesCount = n;
        });
    }

    function updateAll() {
        for (var i = 0; i < libraryModel.count; i++) {
            if (libraryModel.get(i).updateAvailable) {
                updateItem(libraryModel.get(i).pkgId, { busy: true, progress: 0 });
            }
        }
        py.call("store.update_all", [], function(count) {
            root.statusText = count > 0 ? "Updating " + count + " app(s)…" : "Everything is up to date";
        });
    }

    function updateItem(pkg, props) {
        var models = [storeModel, libraryModel, newModel, updatedModel, verifiedModel];
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

    function goBack() {
        stack.pop();
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
        lastRunPkg = pkg;
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

    function addToDrawer(pkg) {
        py.call("store.add_shortcut", [pkg, true], function(r) {
            statusText = r.ok ? "Added to the app drawer. Swipe up from the left edge / open the app list to find it."
                              : "App drawer: " + r.error;
        });
    }

    function removeApp(pkg) {
        py.call("store.remove", [pkg], function() {
            updateItem(pkg, { installed: false });
            refreshLibrary();
        });
    }

    // ---------- UI ----------
    // follow the system theme (dark/light) for all custom components
    Binding {
        target: Pal
        property: "dark"
        value: theme.palette.normal.background.hslLightness < 0.5
    }

    Timer {
        id: toastTimer
        interval: 8000
        onTriggered: root.toastDismissed = true
    }

    Timer {
        id: searchTimer
        interval: 350
        onTriggered: root.doSearch()
    }

    PageStack {
        id: stack
        Component.onCompleted: push(mainPage)

        Page {
            id: mainPage
            visible: false
            header: PageHeader { visible: false }

            function menuAction(id) {
                if (id === "add") {
                    stack.push(Qt.resolvedUrl("ImportPage.qml"), { appRoot: root, py: py });
                } else if (id === "log") {
                    stack.push(Qt.resolvedUrl("LogPage.qml"), { py: py, pkgId: root.lastRunPkg });
                } else if (id === "settings") {
                    stack.push(Qt.resolvedUrl("SettingsPage.qml"), { py: py });
                }
            }

            Rectangle { anchors.fill: parent; color: Pal.bg }

            // =============== top bar ===============
            Item {
                id: topBar
                anchors { top: parent.top; left: parent.left; right: parent.right }
                height: units.gu(8)
                z: 5

                // title bar: Discover / My apps
                Item {
                    anchors.fill: parent
                    opacity: root.tab === 1 ? 0 : 1
                    visible: opacity > 0
                    Behavior on opacity { NumberAnimation { duration: Pal.med } }

                    Label {
                        anchors { left: parent.left; leftMargin: units.gu(2); verticalCenter: parent.verticalCenter }
                        text: root.tab === 2 ? "My apps" : "DroidStore"
                        font.pixelSize: units.gu(3.6)
                        color: Pal.text
                    }

                    Row {
                        anchors { right: parent.right; rightMargin: units.gu(1); verticalCenter: parent.verticalCenter }
                        spacing: units.gu(0.5)

                        Ripple {
                            width: units.gu(5.6)
                            height: width
                            radius: width / 2
                            enabled: !root.catalogBusy
                            opacity: enabled ? 1 : 0.6
                            onClicked: root.updateCatalog()

                            Icon {
                                anchors.centerIn: parent
                                width: units.gu(3)
                                height: width
                                name: "reload"
                                color: Pal.text
                                NumberAnimation on rotation {
                                    from: 0
                                    to: 360
                                    duration: 900
                                    loops: Animation.Infinite
                                    running: root.catalogBusy
                                    alwaysRunToEnd: true
                                }
                            }
                        }

                        Ripple {
                            width: units.gu(5.6)
                            height: width
                            radius: width / 2
                            onClicked: root.menuOpen = !root.menuOpen

                            Label {
                                anchors.centerIn: parent
                                text: "⋮"
                                font.pixelSize: units.gu(3.2)
                                color: Pal.text
                            }
                        }
                    }
                }

                // search bar: Search tab
                Item {
                    anchors.fill: parent
                    opacity: root.tab === 1 ? 1 : 0
                    visible: opacity > 0
                    Behavior on opacity { NumberAnimation { duration: Pal.med } }

                    Rectangle {
                        id: searchPill
                        anchors { left: parent.left; right: parent.right; leftMargin: units.gu(2); rightMargin: units.gu(2); verticalCenter: parent.verticalCenter }
                        height: units.gu(5.8)
                        radius: height / 2
                        color: Pal.surface
                        border.width: searchField.activeFocus ? units.dp(2) : 0
                        border.color: Pal.primary
                        Behavior on border.width { NumberAnimation { duration: Pal.fast } }

                        MouseArea { anchors.fill: parent; onClicked: searchField.forceActiveFocus() }

                        Icon {
                            id: searchIcon
                            anchors { left: parent.left; leftMargin: units.gu(2); verticalCenter: parent.verticalCenter }
                            width: units.gu(2.6)
                            height: width
                            name: "search"
                            color: searchField.activeFocus ? Pal.primary : Pal.textDim
                        }

                        Label {
                            anchors { left: searchField.left; verticalCenter: parent.verticalCenter }
                            text: "Search apps…"
                            font.pixelSize: units.gu(2)
                            color: Pal.textDim
                            opacity: searchField.text === "" ? 1 : 0
                            Behavior on opacity { NumberAnimation { duration: Pal.fast } }
                        }

                        TextInput {
                            id: searchField
                            anchors { left: searchIcon.right; leftMargin: units.gu(1.5); right: clearButton.left; verticalCenter: parent.verticalCenter }
                            color: Pal.text
                            font.pixelSize: units.gu(2)
                            selectionColor: Pal.primary
                            selectedTextColor: Pal.onPrimary
                            clip: true
                            inputMethodHints: Qt.ImhNoPredictiveText
                            onTextChanged: searchTimer.restart()
                            onAccepted: {
                                root.addHistory(text);
                                focus = false;
                            }
                        }

                        Ripple {
                            id: clearButton
                            anchors { right: parent.right; rightMargin: units.gu(0.6); verticalCenter: parent.verticalCenter }
                            width: units.gu(4.6)
                            height: width
                            radius: width / 2
                            opacity: searchField.text === "" ? 0 : 1
                            enabled: searchField.text !== ""
                            Behavior on opacity { NumberAnimation { duration: Pal.fast } }
                            onClicked: { searchField.text = ""; searchField.forceActiveFocus(); }

                            Label {
                                anchors.centerIn: parent
                                text: "×"
                                font.pixelSize: units.gu(3)
                                color: Pal.textDim
                            }
                        }
                    }
                }
            }

            // =============== ATL banner ===============
            Item {
                id: sdkWrap
                anchors { top: topBar.bottom; left: parent.left; right: parent.right }
                height: (root.sdkMissing || root.sdkBusy) ? sdkCard.height + units.gu(1) : 0
                clip: true
                z: 4
                Behavior on height { NumberAnimation { duration: Pal.med; easing.type: Easing.OutCubic } }

                Rectangle {
                    id: sdkCard
                    anchors { left: parent.left; right: parent.right; margins: units.gu(2) }
                    height: sdkColumn.height + units.gu(3)
                    radius: units.gu(1.5)
                    color: Pal.surface

                    Column {
                        id: sdkColumn
                        anchors { left: parent.left; right: parent.right; top: parent.top; margins: units.gu(1.5) }
                        spacing: units.gu(1.2)

                        Label {
                            width: parent.width
                            wrapMode: Text.WordWrap
                            font.pixelSize: units.gu(1.8)
                            color: Pal.text
                            text: root.sdkBusy
                                  ? (root.sdkPhase === "extract" ? "Extracting ATL… " : "Downloading ATL… ")
                                    + Math.round(root.sdkProgress * 100) + "%"
                                  : "ATL (about 50 MB) is needed to open apps."
                        }
                        ProgressLine {
                            width: parent.width
                            visible: root.sdkBusy
                            value: root.sdkProgress
                        }
                        ActionButton {
                            visible: !root.sdkBusy
                            label: "Install ATL"
                            onClicked: root.installSdk()
                        }
                    }
                }
            }

            // =============== tabs ===============
            Item {
                id: tabs
                anchors { top: sdkWrap.bottom; left: parent.left; right: parent.right; bottom: nav.top }
                clip: true

                // ---------- Discover ----------
                Item {
                    id: discoverTab
                    width: parent.width   // (no anchors: x is animated)
                    height: parent.height
                    opacity: root.tab === 0 ? 1 : 0
                    visible: opacity > 0
                    enabled: root.tab === 0
                    x: root.tab === 0 ? 0 : -units.gu(5)
                    Behavior on opacity { NumberAnimation { duration: Pal.med; easing.type: Easing.OutCubic } }
                    Behavior on x { NumberAnimation { duration: Pal.med; easing.type: Easing.OutCubic } }

                    Flickable {
                        anchors.fill: parent
                        contentHeight: discoverColumn.height + units.gu(3)
                        clip: true

                        Column {
                            id: discoverColumn
                            width: parent.width

                            Reveal {
                                order: 0
                                active: root.discoverLoaded
                                visible: newModel.count > 0
                                SectionHeader { title: "New apps"; onArrowClicked: root.openSort("new") }
                                HList { model: newModel; onAppClicked: root.openDetails(pkgId) }
                            }

                            Reveal {
                                order: 1
                                active: root.discoverLoaded
                                visible: updatedModel.count > 0
                                SectionHeader { title: "Recently updated"; onArrowClicked: root.openSort("updated") }
                                HList { model: updatedModel; onAppClicked: root.openDetails(pkgId) }
                            }

                            Reveal {
                                order: 2
                                active: root.discoverLoaded
                                visible: verifiedModel.count > 0
                                SectionHeader { title: "Works with ATL"; showArrow: false }
                                HList { model: verifiedModel; onAppClicked: root.openDetails(pkgId) }
                            }

                            Reveal {
                                order: 3
                                active: root.discoverLoaded
                                visible: categoryModel.count > 0
                                SectionHeader { title: "Categories"; showArrow: false }
                                Item {
                                    width: parent.width
                                    height: categoryBox.height + units.gu(2)

                                    Rectangle {
                                        id: categoryBox
                                        x: units.gu(2)
                                        width: parent.width - units.gu(4)
                                        height: categoryColumn.height
                                        radius: units.gu(2.2)
                                        color: "transparent"
                                        border.width: units.dp(1)
                                        border.color: Pal.outline
                                        clip: true

                                        Column {
                                            id: categoryColumn
                                            width: parent.width
                                            Repeater {
                                                model: categoryModel
                                                delegate: CategoryRow {
                                                    name: model.name
                                                    count: model.apps
                                                    divider: index < categoryModel.count - 1
                                                    onClicked: root.openCategory(model.name)
                                                }
                                            }
                                        }
                                    }
                                }
                            }

                            Reveal {
                                order: 4
                                active: root.discoverLoaded
                                Item {
                                    width: parent.width
                                    height: footerLabel.implicitHeight + units.gu(2)

                                    Label {
                                        id: footerLabel
                                        y: units.gu(1)
                                        width: parent.width
                                        horizontalAlignment: Text.AlignHCenter
                                        text: root.totalApps > 0 ? root.totalApps + " apps in the catalog"
                                              : (root.catalogBusy ? "" : "No catalog yet. Tap the refresh button.")
                                        font.pixelSize: units.gu(1.6)
                                        color: Pal.textDim
                                    }
                                }
                            }
                        }
                    }
                }

                // ---------- Search ----------
                Item {
                    id: searchTab
                    width: parent.width   // (no anchors: x is animated)
                    height: parent.height
                    opacity: root.tab === 1 ? 1 : 0
                    visible: opacity > 0
                    enabled: root.tab === 1
                    x: root.tab === 1 ? 0 : (root.tab < 1 ? units.gu(5) : -units.gu(5))
                    Behavior on opacity { NumberAnimation { duration: Pal.med; easing.type: Easing.OutCubic } }
                    Behavior on x { NumberAnimation { duration: Pal.med; easing.type: Easing.OutCubic } }

                    // filter chips
                    Flickable {
                        id: chipFlick
                        anchors { top: parent.top; left: parent.left; right: parent.right }
                        height: units.gu(7)
                        contentWidth: chipRow.width + units.gu(4)
                        flickableDirection: Flickable.HorizontalFlick
                        clip: true

                        Row {
                            id: chipRow
                            x: units.gu(2)
                            y: units.gu(1.2)
                            spacing: units.gu(1)

                            Chip {
                                text: "No native code"
                                glyph: "✔"
                                selected: root.compatOnly
                                onClicked: { root.compatOnly = !root.compatOnly; root.doSearch(); }
                            }
                            Chip {
                                visible: root.searchCategory !== ""
                                text: root.searchCategory
                                selected: true
                                closable: true
                                onClicked: root.setCategory("")
                            }
                            Chip {
                                visible: root.searchSort !== ""
                                text: root.searchSort === "new" ? "New apps" : "Recently updated"
                                selected: true
                                closable: true
                                onClicked: root.setSort("")
                            }
                        }
                    }

                    // history + categories (nothing typed yet)
                    Flickable {
                        id: browseFlick
                        anchors { top: chipFlick.bottom; left: parent.left; right: parent.right; bottom: parent.bottom }
                        contentHeight: browseColumn.height + units.gu(3)
                        clip: true
                        opacity: root.browsing ? 1 : 0
                        visible: opacity > 0
                        Behavior on opacity { NumberAnimation { duration: Pal.med } }

                        Column {
                            id: browseColumn
                            width: parent.width

                            Item {
                                width: parent.width
                                height: root.searchHistory.length > 0 ? units.gu(6) : 0
                                visible: height > 0

                                Label {
                                    anchors { left: parent.left; leftMargin: units.gu(2); verticalCenter: parent.verticalCenter }
                                    text: "Recent searches"
                                    font.pixelSize: units.gu(1.9)
                                    color: Pal.textDim
                                }
                                Ripple {
                                    anchors { right: parent.right; rightMargin: units.gu(1.5); verticalCenter: parent.verticalCenter }
                                    width: clearLabel.width + units.gu(3)
                                    height: units.gu(4.2)
                                    radius: height / 2
                                    onClicked: root.clearHistory()
                                    Label {
                                        id: clearLabel
                                        anchors.centerIn: parent
                                        text: "Clear"
                                        font.pixelSize: units.gu(1.8)
                                        color: Pal.primary
                                    }
                                }
                            }

                            Repeater {
                                model: root.searchHistory
                                delegate: Ripple {
                                    width: browseColumn.width
                                    height: units.gu(6.6)
                                    onClicked: {
                                        searchField.text = modelData;
                                        searchField.cursorPosition = searchField.text.length;
                                        root.addHistory(modelData);
                                    }

                                    Rectangle {
                                        id: histTile
                                        anchors { left: parent.left; leftMargin: units.gu(2); verticalCenter: parent.verticalCenter }
                                        width: units.gu(4.6)
                                        height: width
                                        radius: width / 2
                                        color: Pal.surface
                                        Label { anchors.centerIn: parent; text: "↺"; font.pixelSize: units.gu(2.6); color: Pal.textDim }
                                    }
                                    Label {
                                        anchors { left: histTile.right; leftMargin: units.gu(2); right: parent.right; rightMargin: units.gu(2); verticalCenter: parent.verticalCenter }
                                        text: modelData
                                        font.pixelSize: units.gu(2.1)
                                        color: Pal.text
                                        elide: Text.ElideRight
                                    }
                                }
                            }

                            Item {
                                width: parent.width
                                height: categoryModel.count > 0 ? units.gu(7) : 0
                                visible: height > 0
                                Label {
                                    anchors { left: parent.left; leftMargin: units.gu(2); verticalCenter: parent.verticalCenter }
                                    text: "Categories"
                                    font.pixelSize: units.gu(1.9)
                                    color: Pal.textDim
                                }
                            }

                            Flow {
                                x: units.gu(2)
                                width: parent.width - units.gu(4)
                                spacing: units.gu(1)

                                Repeater {
                                    model: categoryModel
                                    delegate: Chip {
                                        text: model.name
                                        glyph: Pal.glyph(model.name)
                                        onClicked: root.openCategory(model.name)
                                    }
                                }
                            }
                        }
                    }

                    // results
                    ListView {
                        id: resultsList
                        anchors { top: chipFlick.bottom; left: parent.left; right: parent.right; bottom: parent.bottom }
                        clip: true
                        model: storeModel
                        cacheBuffer: units.gu(40)
                        opacity: root.browsing ? 0 : 1
                        visible: opacity > 0
                        Behavior on opacity { NumberAnimation { duration: Pal.med } }
                        onAtYEndChanged: if (atYEnd) root.loadMore()
                        onMovementStarted: Qt.inputMethod.hide()

                        delegate: AppRow {
                            pkgId: model.pkgId
                            name: model.name
                            summary: model.summary
                            icon: model.icon
                            version: model.version
                            size: model.size
                            isNative: model.native
                            verified: model.verified
                            installed: model.installed
                            updateAvailable: model.updateAvailable
                            newVersion: model.newVersion
                            busy: model.busy
                            progress: model.progress
                            onOpened: { root.addHistory(searchField.text); root.openDetails(model.pkgId); }
                            onAction: (model.installed && !model.updateAvailable)
                                      ? root.launchApp(model.pkgId) : root.installApp(model.pkgId)
                        }

                        add: Transition {
                            ParallelAnimation {
                                NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Pal.med }
                                NumberAnimation { property: "x"; from: units.gu(4); to: 0; duration: Pal.med; easing.type: Easing.OutCubic }
                            }
                        }

                        Label {
                            anchors.centerIn: parent
                            opacity: (storeModel.count === 0 && !root.storeLoading) ? 1 : 0
                            Behavior on opacity { NumberAnimation { duration: Pal.med } }
                            text: "No apps found"
                            font.pixelSize: units.gu(2.2)
                            color: Pal.textDim
                        }
                    }
                }

                // ---------- My apps ----------
                Item {
                    id: myAppsTab
                    width: parent.width   // (no anchors: x is animated)
                    height: parent.height
                    opacity: root.tab === 2 ? 1 : 0
                    visible: opacity > 0
                    enabled: root.tab === 2
                    x: root.tab === 2 ? 0 : units.gu(5)
                    Behavior on opacity { NumberAnimation { duration: Pal.med; easing.type: Easing.OutCubic } }
                    Behavior on x { NumberAnimation { duration: Pal.med; easing.type: Easing.OutCubic } }

                    ListView {
                        id: libraryList
                        anchors.fill: parent
                        clip: true
                        model: libraryModel

                        header: Item {
                            width: libraryList.width
                            height: units.gu(6.5) + (root.updatesCount > 0 ? units.gu(7.5) : 0)
                            clip: true
                            Behavior on height { NumberAnimation { duration: Pal.med; easing.type: Easing.OutCubic } }

                            Label {
                                anchors { left: parent.left; leftMargin: units.gu(2); top: parent.top; topMargin: units.gu(1.5) }
                                text: "Installed apps"
                                font.pixelSize: units.gu(2.6)
                                color: Pal.text
                            }

                            Ripple {
                                x: units.gu(2)
                                y: units.gu(6.5)
                                width: parent.width - units.gu(4)
                                height: units.gu(5.6)
                                radius: height / 2
                                opacity: root.updatesCount > 0 ? 1 : 0
                                Behavior on opacity { NumberAnimation { duration: Pal.med } }
                                onClicked: root.updateAll()

                                Rectangle { anchors.fill: parent; radius: parent.radius; color: Pal.primary }
                                Label {
                                    anchors.centerIn: parent
                                    text: "Update all (" + root.updatesCount + ")"
                                    font.pixelSize: units.gu(2)
                                    font.bold: true
                                    color: Pal.onPrimary
                                }
                            }
                        }

                        delegate: AppRow {
                            pkgId: model.pkgId
                            name: model.name
                            icon: model.icon
                            version: model.version
                            newVersion: model.newVersion
                            local: model.local
                            installed: true
                            showKind: false
                            updateAvailable: model.updateAvailable
                            busy: model.busy
                            progress: model.progress
                            expandable: true
                            onOpened: root.openDetails(model.pkgId)
                            onAction: model.updateAvailable ? root.installApp(model.pkgId) : root.launchApp(model.pkgId)
                            onAddToDrawer: root.addToDrawer(model.pkgId)
                            onRemoved: root.removeApp(model.pkgId)
                        }

                        add: Transition {
                            ParallelAnimation {
                                NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Pal.med }
                                NumberAnimation { property: "x"; from: units.gu(4); to: 0; duration: Pal.med; easing.type: Easing.OutCubic }
                            }
                        }
                        remove: Transition {
                            ParallelAnimation {
                                NumberAnimation { property: "opacity"; to: 0; duration: Pal.med }
                                NumberAnimation { property: "x"; to: units.gu(6); duration: Pal.med; easing.type: Easing.InCubic }
                            }
                        }
                        displaced: Transition {
                            NumberAnimation { properties: "y"; duration: Pal.med; easing.type: Easing.OutCubic }
                        }

                        Label {
                            anchors.centerIn: parent
                            width: parent.width - units.gu(8)
                            horizontalAlignment: Text.AlignHCenter
                            wrapMode: Text.WordWrap
                            opacity: libraryModel.count === 0 ? 1 : 0
                            Behavior on opacity { NumberAnimation { duration: Pal.slow } }
                            text: "You haven't installed any apps yet.\nTap ⋮ → Add APK to add your own APK"
                            font.pixelSize: units.gu(2)
                            color: Pal.textDim
                        }
                    }
                }
            }

            // =============== bottom navigation ===============
            Rectangle {
                id: nav
                anchors { bottom: parent.bottom; left: parent.left; right: parent.right }
                height: units.gu(9.5)
                color: Pal.surface
                z: 6

                Item {
                    id: navRow
                    anchors.fill: parent

                    // the pill that slides behind the selected icon
                    Rectangle {
                        width: units.gu(10.5)
                        height: units.gu(4.6)
                        radius: height / 2
                        y: units.gu(1.2)
                        x: root.tab * navRow.width / 3 + (navRow.width / 3 - width) / 2
                        color: Pal.primary
                        Behavior on x { NumberAnimation { duration: 360; easing.type: Easing.OutBack; easing.overshoot: 0.9 } }
                    }

                    Repeater {
                        model: [
                            { icon: "home", label: "Discover" },
                            { icon: "search", label: "Search" },
                            { icon: "view-grid-symbolic", label: "My apps" }
                        ]

                        delegate: Ripple {
                            x: index * navRow.width / 3
                            width: navRow.width / 3
                            height: navRow.height
                            pressScale: false
                            onClicked: root.tab = index

                            Icon {
                                id: navIcon
                                anchors.horizontalCenter: parent.horizontalCenter
                                y: units.gu(2.5)
                                width: units.gu(2.8)
                                height: width
                                name: modelData.icon
                                color: root.tab === index ? Pal.onPrimary : Pal.textDim
                                scale: root.tab === index ? 1.12 : 1
                                Behavior on color { ColorAnimation { duration: Pal.med } }
                                Behavior on scale { NumberAnimation { duration: Pal.med; easing.type: Easing.OutBack } }
                            }

                            // update count badge on "My apps"
                            Rectangle {
                                visible: index === 2
                                x: navIcon.x + navIcon.width - units.gu(0.4)
                                y: units.gu(1.4)
                                width: units.gu(2.4)
                                height: width
                                radius: width / 2
                                color: Pal.danger
                                scale: (index === 2 && root.updatesCount > 0) ? 1 : 0
                                Behavior on scale { NumberAnimation { duration: Pal.med; easing.type: Easing.OutBack } }
                                Label {
                                    anchors.centerIn: parent
                                    text: root.updatesCount
                                    font.pixelSize: units.gu(1.4)
                                    font.bold: true
                                    color: "#FFFFFF"
                                }
                            }

                            Label {
                                anchors.horizontalCenter: parent.horizontalCenter
                                y: units.gu(6.6)
                                text: modelData.label
                                font.pixelSize: units.gu(1.7)
                                font.bold: root.tab === index
                                color: root.tab === index ? Pal.text : Pal.textDim
                            }
                        }
                    }
                }
            }

            // =============== toast (status messages) ===============
            Rectangle {
                id: toast
                readonly property bool shown: root.statusText !== "" && !root.toastDismissed
                anchors { left: parent.left; right: parent.right; bottom: nav.top; leftMargin: units.gu(2); rightMargin: units.gu(2); bottomMargin: units.gu(1.5) }
                height: toastLabel.implicitHeight + units.gu(3)
                radius: units.gu(1.5)
                color: Pal.dark ? "#E3E2E9" : "#2F3036"
                opacity: shown ? 1 : 0
                visible: opacity > 0
                z: 10
                Behavior on opacity { NumberAnimation { duration: Pal.med } }
                transform: Translate {
                    y: toast.shown ? 0 : units.gu(4)
                    Behavior on y { NumberAnimation { duration: Pal.med; easing.type: Easing.OutCubic } }
                }

                Label {
                    id: toastLabel
                    anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; leftMargin: units.gu(2); rightMargin: units.gu(2) }
                    text: root.toastText
                    wrapMode: Text.WordWrap
                    maximumLineCount: 4
                    elide: Text.ElideRight
                    font.pixelSize: units.gu(1.7)
                    color: Pal.dark ? "#1A1B21" : "#F1F0F7"
                }

                MouseArea {
                    anchors.fill: parent
                    onClicked: {
                        if (root.lastRunPkg !== "" && root.statusText.indexOf("log") !== -1) {
                            stack.push(Qt.resolvedUrl("LogPage.qml"), { py: py, pkgId: root.lastRunPkg });
                        }
                        root.toastDismissed = true;
                    }
                }
            }

            // =============== ⋮ menu ===============
            MouseArea {
                anchors.fill: parent
                z: 20
                visible: root.menuOpen
                onClicked: root.menuOpen = false
            }

            Rectangle {
                id: menu
                anchors { top: parent.top; right: parent.right; topMargin: units.gu(6.5); rightMargin: units.gu(1.5) }
                width: units.gu(24)
                height: menuColumn.height + units.gu(2)
                radius: units.gu(1.5)
                color: Pal.surfaceHi
                border.width: units.dp(1)
                border.color: Pal.outline
                z: 21
                opacity: root.menuOpen ? 1 : 0
                scale: root.menuOpen ? 1 : 0.85
                transformOrigin: Item.TopRight
                visible: opacity > 0
                Behavior on opacity { NumberAnimation { duration: Pal.fast } }
                Behavior on scale { NumberAnimation { duration: Pal.med; easing.type: Easing.OutBack; easing.overshoot: 1.2 } }

                Column {
                    id: menuColumn
                    y: units.gu(1)
                    width: parent.width

                    Repeater {
                        model: [
                            { id: "add", text: "Add APK" },
                            { id: "log", text: "Last log" },
                            { id: "settings", text: "Settings" }
                        ]
                        delegate: Ripple {
                            width: menu.width
                            height: units.gu(5.8)
                            enabled: modelData.id !== "log" || root.lastRunPkg !== ""
                            opacity: enabled ? 1 : 0.4
                            onClicked: { root.menuOpen = false; mainPage.menuAction(modelData.id); }

                            Label {
                                anchors { left: parent.left; leftMargin: units.gu(2.2); verticalCenter: parent.verticalCenter }
                                text: modelData.text
                                font.pixelSize: units.gu(2)
                                color: Pal.text
                            }
                        }
                    }
                }
            }
        }
    }
}
