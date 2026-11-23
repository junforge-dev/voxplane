pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window

Item {
    id: root
    required property var controller
    property real screenScaleX: 1
    property real screenScaleY: 1
    readonly property var scalar: controller?.scalarDisplay ?? ({})
    readonly property bool available: !!scalar.source
    readonly property bool gpu: GraphicsInfo.api !== GraphicsInfo.Software && GraphicsInfo.api !== GraphicsInfo.Unknown
    // 24-bit texture packing can move a value exactly on a binary threshold
    // across it. Use the original scalar samples for this discontinuous VOI.
    readonly property bool binaryWindow: scalar.voiFunction === "LINEAR" && scalar.width <= 1
    readonly property bool useShader: available && gpu && !binaryWindow && effect.status !== ShaderEffect.Error
    Image {
        id: texture
        source: root.useShader ? root.scalar.source : ""
        visible: false
        cache: false
        smooth: false
    }
    Image {
        id: lutTexture
        source: root.useShader ? root.scalar.lut : ""
        visible: false
        smooth: false
    }
    ShaderEffect {
        id: effect
        objectName: "scalarDisplayShader"
        anchors.fill: parent
        visible: root.useShader
        property var source: texture
        property var colorLut: lutTexture
        property vector4d dataRange: Qt.vector4d(root.scalar.minimum ?? 0,root.scalar.span ?? 1,
                                               root.scalar.center ?? 0,root.scalar.width ?? 1)
        property vector2d imageSize: Qt.vector2d(root.width,root.height)
        property real inverted: root.scalar.inverted ? 1 : 0
        property int windowFunction: root.scalar.voiFunction === "LINEAR" ? 1
                                   : root.scalar.voiFunction === "SIGMOID" ? 2 : 0
        fragmentShader: "../../../shaders/scalar_display.frag.qsb"
        blending: false
    }
    Image {
        objectName: "scalarDisplayFallback"
        anchors.fill: parent
        visible: !root.useShader
        source: root.useShader ? "" : root.available ? root.scalar.cpu : root.controller?.imageSource ?? ""
        cache: false
        // Software fallback also interpolates scalar values before windowing.
        // Image applies the window DPR to sourceSize before calling the provider.
        // Specify logical pixels here, while keeping the physical allocation cap.
        sourceSize: root.available ? Qt.size(Math.max(1,Math.min(4096/Screen.devicePixelRatio,Math.ceil(root.width*root.screenScaleX))),
                                             Math.max(1,Math.min(4096/Screen.devicePixelRatio,Math.ceil(root.height*root.screenScaleY)))) : Qt.size(-1,-1)
        fillMode: Image.Stretch
        smooth: !root.available
    }
}
