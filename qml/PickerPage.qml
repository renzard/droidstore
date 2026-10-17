import QtQuick 2.12
import Lomiri.Components 1.3
import Lomiri.Content 1.3

// Opens the system "which app?" dialog (Content Hub) and receives the APK you pick
Page {
    id: page

    // passed in from ImportPage
    property var importPage

    property var activeTransfer: null

    header: PageHeader {
        id: header
        title: "Choose APK from an app"
    }

    function localPath(url) {
        return decodeURIComponent(String(url).replace(/^file:\/\//, ""));
    }

    ContentPeerPicker {
        id: picker
        anchors {
            top: header.bottom
            left: parent.left
            right: parent.right
            bottom: parent.bottom
        }
        visible: page.activeTransfer === null
        showTitle: false
        contentType: ContentType.All
        handler: ContentHandler.Source

        onPeerSelected: {
            peer.selectionType = ContentTransfer.Single;
            page.activeTransfer = peer.request();
        }
        onCancelPressed: pageStack.pop()
    }

    ContentTransferHint {
        anchors.fill: parent
        activeTransfer: page.activeTransfer
    }

    Connections {
        target: page.activeTransfer
        onStateChanged: {
            var transfer = page.activeTransfer;
            if (transfer.state === ContentTransfer.Charged) {
                if (transfer.items.length > 0) {
                    var path = page.localPath(transfer.items[0].url);
                    // the temporary file is removed (finalize) only after it has been copied
                    page.importPage.importPath(path, function() { transfer.finalize(); });
                } else {
                    transfer.finalize();
                }
                pageStack.pop();
            } else if (transfer.state === ContentTransfer.Aborted) {
                page.activeTransfer = null;
            }
        }
    }
}
