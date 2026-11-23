# VoxPlane 2.8.0 发布验收

安装包来自提交 `eb99c78fe61f53c74081110edbf5448d8381bb6f`。[公开版本](https://github.com/junforge-dev/voxplane/releases/tag/v2.8.0) · [Windows 构建与原生验收](https://github.com/junforge-dev/voxplane/actions/runs/45115884884) · [详细结果与条件跳过原因](release-2.8.0-results.json)

| 验收范围 | 结果 |
| --- | --- |
| macOS 完整回归 | 317 个模块，4301 通过，96 条件跳过，0 失败 |
| macOS 原生界面 | ROI 原位粘贴、导出画面、更新弹窗、分割交换、工作区、3D 等共 209 项通过，0 跳过 |
| macOS 影像样例 | 增强 CT、MR、多时相、PET 与压缩像素等 51 项通过，0 跳过 |
| macOS 成品 | Apple Silicon、DMG 完整性、只读挂载内容、深度签名完整性与四种封装像素解码通过；使用 ad-hoc 签名，未经公证 |
| Windows 完整回归 | 4275 通过，122 条件跳过，0 失败 |
| Windows 成品 | x64 安装版与便携版均来自上述 Windows 运行，已对照构建来源与下载文件校验；无 Authenticode 签名 |

macOS 从隔离安装的 2.7.0 通过更新辅助程序替换为 2.8.0 并重新启动。旧工作区中的像素、测量、QA、CPR 及设置已恢复。Windows 的安装版和便携版分别通过固定 2.5.0 基线安装迁移；另从 2.7.0 通过更新辅助程序升级，两种包均验证启动、设置和旧工作区恢复。Windows 验收使用 GitHub 托管环境。发布后又从隔离安装的 macOS、Windows 安装版及 Windows 便携版 2.7.0 客户端启动，均自动发现公开的 2.8.0。“下载并更新”按钮触发下载与安装，三个客户端都从原安装位置重新启动并确认新版本。[Windows 旧版界面升级记录](https://github.com/junforge-dev/voxplane/actions/runs/45126760818)

Windows 的页签往返验收复现了二维网格在销毁时重新绑定单元格模型引发的崩溃。[原生调用栈](https://github.com/junforge-dev/voxplane/actions/runs/45110050884)记录了 Qt 在重建网格时清理未完工对象的调用。二维页面现固定使用创建它的页签，回归同时检查新旧网格各自的所有者。[同一提交的重复验收](https://github.com/junforge-dev/voxplane/actions/runs/45115924502)在 Windows 原生窗口和离屏环境各执行 12 轮，每轮 11 项。

Windows 原生验收还复现了在 3D 载入期间切换到手册再返回时无法绘制首帧的问题。Qt 会将被 VTK 子窗口完全覆盖的中间窗口报告为未暴露。现在用实际阅片窗口与绘制子窗口的可见状态判断是否可以渲染。回归同时要求最终截图成功，并检查最小化期间不会绘制。

Windows 原生验收发现，关闭 3D 视口后到达的窗口事件可能触发已销毁控制器的信号。新增回归在修复前复现异常，修复后本机和 Windows 测试通过。macOS 原生分割画笔测试改为等待所需布局和控件状态后再读数。

本次还在原生窗口检查了中英文 ROI 与逐帧导出界面。分割 NRRD 从界面导出、读回并重新导入；核对源像素、空间几何、掩膜与区域列表。GPU 灰阶测试使用 Metal，另以打包后的程序检查无损及有损压缩 DICOM 解码。

三个安装包及其校验文件在草稿与公开状态分别下载并核验。安装包 SHA-256 如下。

| 安装包 | SHA-256 |
| --- | --- |
| VoxPlane-2.8.0-macos-arm64.dmg | `768a40aa1fbfcdab32523b92887e331610003699382924d2101e0dc490309305` |
| VoxPlane-2.8.0-windows-x64-portable.exe | `0602f72af06171e1e2a23a660fb122d4eeb68245be9afa406a8d8db8bb993915` |
| VoxPlane-2.8.0-windows-x64-setup.exe | `a9815265c60c3998a7cdc66ff486111fb35ae8bdaae1b28000a02ead44d25ffd` |
