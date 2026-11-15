pragma Singleton
import QtQuick 2.12

// Colours + animation timings shared by every component.
// Main.qml binds `dark` to the system theme, so light mode works too.
QtObject {
    property bool dark: true

    readonly property color bg:        dark ? "#12131A" : "#FAF8FF"
    readonly property color surface:   dark ? "#1B1D27" : "#EEECF4"
    readonly property color surfaceHi: dark ? "#272A38" : "#E2E1EA"
    readonly property color outline:   dark ? "#3C3F4C" : "#C4C6D0"
    readonly property color text:      dark ? "#E3E2E9" : "#1A1B21"
    readonly property color textDim:   dark ? "#C4C6D0" : "#44474F"
    readonly property color primary:   dark ? "#A8C8FF" : "#2F5FA7"
    readonly property color onPrimary: dark ? "#0A305F" : "#FFFFFF"
    readonly property color chipSel:   dark ? "#34517D" : "#D6E3FF"
    readonly property color onChipSel: dark ? "#D6E3FF" : "#0A305F"
    readonly property color tile:      dark ? "#24487A" : "#D6E3FF"
    readonly property color onTile:    dark ? "#D6E3FF" : "#0A305F"
    readonly property color ok:        dark ? "#8FD694" : "#1B7A3A"
    readonly property color danger:    dark ? "#FFB4AB" : "#BA1A1A"
    readonly property color warnBg:    dark ? "#DADDEC" : "#E1E2EC"
    readonly property color warnText:  "#1A1B21"
    readonly property color overlay:   dark ? "#33FFFFFF" : "#22000000"

    readonly property int fast: 140
    readonly property int med: 280
    readonly property int slow: 460

    // A symbol for a category name (the names change between F-Droid versions, so match on keywords)
    function glyph(name) {
        var n = (name || "").toLowerCase();
        var map = [
            [/game/, "🎮"], [/music|audio|sound|podcast|media|video|cast/, "🎵"],
            [/book|read|writ|note/, "📖"], [/browser|internet|network|dns|vpn|connect|bookmark/, "🌐"],
            [/calendar|clock|alarm|time/, "⏰"], [/camera|photo|graphic|draw|gallery/, "🖼"],
            [/chat|messag|sms|phone|contact|mail|social/, "💬"], [/file|storage|cloud|download|sync/, "📁"],
            [/money|financ|wallet|bank/, "💳"], [/secur|password|encrypt|privacy/, "🔒"],
            [/health|sport|fitness|diet|food|recipe/, "❤"], [/dev|code|forge|terminal/, "⌨"],
            [/launcher|keyboard|theme|device|system|battery/, "📱"], [/scien|educat|learn/, "🔬"],
            [/navig|map|weather|travel/, "🧭"], [/tool|calculat|dice|convert/, "🛠"]
        ];
        for (var i = 0; i < map.length; i++) {
            if (map[i][0].test(n)) return map[i][1];
        }
        return n.length ? n.charAt(0).toUpperCase() : "?";
    }
}
