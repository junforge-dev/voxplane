pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic as Basic
import "../components" as Components
import "../theme"

Components.AppDialog {
    id: preview
    required property var controller
    required property string fileFormat
    property int frameIndex: 0
    readonly property int frameCount: fileFormat === "dicom"
        ? (controller?.sourceFrameCount ?? 0) : (controller?.selectedFrameCount ?? 0)
    objectName: "exportFramePreview"
    parent: Basic.Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(1100, (parent?.width ?? 1132) - 32)
    height: Math.min(900, (parent?.height ?? 932) - 32)
    title: qsTrId("export.reviewFrames")
    Components.DelayedActivity { id: feedback; active: preview.visible && preview.controller?.previewStatus === "loading" }
    onAboutToShow: {
        frameIndex = 0
        actualSize.checked = false
        fullRange.checked = false
        controller.previewFrame(fileFormat, frameIndex)
    }
    onClosed: controller?.clearPreview()

    function setFrame(index) {
        if (index < 0 || index >= frameCount || index === frameIndex)
            return
        frameIndex = index
        controller.previewFrame(fileFormat, index, fullRange.checked)
    }

    contentItem: ColumnLayout {
        spacing: 8
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: Theme.canvasBackground
            Flickable {
                id: viewport
                anchors.fill: parent
                clip: true
                contentWidth: Math.max(width, frame.width)
                contentHeight: Math.max(height, frame.height)
                boundsBehavior: Flickable.StopAtBounds
                Image {
                    id: frame
                    objectName: "exportPreviewImage"
                    readonly property bool ready: status === Image.Ready
                    width: actualSize.checked ? implicitWidth : viewport.width
                    height: actualSize.checked ? implicitHeight : viewport.height
                    x: (viewport.contentWidth - width) / 2
                    y: (viewport.contentHeight - height) / 2
                    source: preview.controller?.previewUrl ?? ""
                    fillMode: Image.PreserveAspectFit
                    cache: false
                }
            }
        }
        Text {
            objectName: "exportPreviewFlag"
            Layout.fillWidth: true
            text: {
                switch (preview.controller?.previewStatus) {
                case "yes": return qsTrId("export.flagYes")
                case "no": return qsTrId("export.flagNo")
                case "unknown": return qsTrId("export.flagUnknown")
                case "failed": return qsTrId("export.previewFailed")
                case "loading": return feedback.showing ? qsTrId("text.0594") : ""
                default: return ""
                }
            }
            color: Theme.textPrimary
            wrapMode: Text.Wrap
        }
        RowLayout {
            Layout.fillWidth: true
            Components.AppButton {
                objectName: "exportPreviewPrevious"
                text: "‹"
                Accessible.name: qsTrId("export.previousFrame")
                enabled: preview.frameIndex > 0
                onClicked: preview.setFrame(preview.frameIndex - 1)
            }
            Components.AppSlider {
                Layout.fillWidth: true
                from: 0
                to: Math.max(0, preview.frameCount - 1)
                stepSize: 1
                value: preview.frameIndex
                enabled: preview.frameCount > 1
                Accessible.name: qsTrId("export.reviewFrames")
                onMoved: preview.setFrame(Math.round(value))
            }
            Text { text: (preview.frameIndex + 1) + " / " + preview.frameCount; color: Theme.textPrimary }
            Components.AppButton {
                objectName: "exportPreviewNext"
                text: "›"
                Accessible.name: qsTrId("export.nextFrame")
                enabled: preview.frameIndex + 1 < preview.frameCount
                onClicked: preview.setFrame(preview.frameIndex + 1)
            }
            Components.AppCheckBox {
                id: fullRange
                objectName: "exportPreviewFullRange"
                visible: preview.controller?.previewCanWindow ?? false
                text: qsTrId("export.fullRange")
                onClicked: preview.controller.previewFrame(preview.fileFormat, preview.frameIndex, checked)
            }
            Components.AppCheckBox {
                id: actualSize
                text: "1:1"
                Accessible.name: qsTrId("initialDisplay.pixel")
            }
        }
    }
}
