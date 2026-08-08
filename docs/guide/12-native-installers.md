# EasyQC 原生安装包

## 1. 目标与边界

原生安装包解决的是“用户无需准备 Python，下载后按操作系统习惯安装”。三套
产物来自同一 git revision、同一 `easyqc.py` 和同一 Qt/Core/Models 代码；
平台差异只存在于冻结和安装容器中。

| 目标 | 产物 | 原生构建工具 | 安装范围 |
|---|---|---|---|
| Ubuntu 22.04/24.04 x86_64 | `.deb` | PyInstaller + `dpkg-deb` | 系统 `/opt/easyqc` + `/usr/bin/easyqc` |
| Windows 11 x86_64 | `Setup.exe` | PyInstaller + Inno Setup 6 | 当前用户 `%LOCALAPPDATA%\Programs\EasyQC` |
| macOS 13+ arm64 | `.dmg` | PyInstaller + `hdiutil` | 用户拖动 `EasyQC.app` |

macOS Intel、Microsoft Store、Mac App Store、Snap、Flatpak 和自动更新服务不在
当前范围。源码/私有 Python 安装路线继续存在。

## 2. 下载后验证

每个平台目录必须只有一个安装包，并带有：

```text
artifact-manifest.json
SHA256SUMS
EasyQC-<version>-<target>.<deb|exe|dmg>
```

Linux/macOS 可在该目录执行：

```bash
sha256sum --check SHA256SUMS
```

Windows PowerShell 可读取 `artifact-manifest.json` 的 `sha256`，再比较：

```powershell
(Get-FileHash .\EasyQC-*-setup.exe -Algorithm SHA256).Hash.ToLower()
```

哈希匹配只能证明下载字节与构建输出相同。当前 manifest 的 `signed` 为
`false`，意味着没有 Windows Authenticode 或 Apple Developer ID/公证保证。

## 3. 安装与卸载

### 3.1 Ubuntu

```bash
sudo apt install ./EasyQC-1.0.0-linux-x86_64.deb
easyqc --version
sudo apt remove easyqc
```

卸载只移除 `/opt/easyqc`、启动器和桌面条目。项目目录、名单和 RatingFiles
不在安装目录中时不会被删除。若依赖检查失败，apt 会显示缺少的系统动态库；
不要用 `dpkg --force-*` 绕过。

### 3.2 Windows

双击 `EasyQC-...-setup.exe`，默认安装到当前用户目录，不要求管理员权限。
开始菜单包含 EasyQC，桌面快捷方式为可选项。可在“已安装的应用”中卸载。

CI 使用的静默合同是：

```powershell
EasyQC-...-setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-
```

### 3.3 macOS

打开 DMG，把 `EasyQC.app` 拖入 Applications。卸载时退出 EasyQC，再把应用移
到废纸篓；项目数据应存放在独立项目目录中。

未签名测试包可能被 Gatekeeper 阻止。正式方案是对 `.app` 进行 Developer ID
签名、notarize 和 staple，而不是让用户永久关闭系统安全设置。

## 4. 安装状态与项目数据

原生包的程序文件可能位于只读目录，不能把 `projects.json`、模板和命令设置写
入 `/opt/easyqc`、Program Files 或 `EasyQC.app`。冻结应用因此使用每用户、
按版本隔离的状态目录：

| 系统 | 默认目录示意 |
|---|---|
| Linux | `~/.local/share/EasyQC/native-1.0.0/` |
| Windows | `%LOCALAPPDATA%\EasyQC\native-1.0.0\` |
| macOS | `~/Library/Application Support/EasyQC/native-1.0.0/` |

该目录保存 `projects.json`、`constant_templates.json`、`app_settings.json` 和模板
`modules/`。实际 `easyqc_<project>/` 可以位于用户选择的任意可写位置，不会被
复制到状态目录。不同 EasyQC 版本默认不共享登记表和模板，避免并行安装互相
污染；既有项目可在新版本中重新“导入项目”登记。

受管理部署可在启动前设置绝对、规范化的 `EASYQC_DATA_ROOT`，把这些安装状态
定向到指定目录。相对路径和包含 `..` 的非规范路径会明确失败。源码检出不设
该变量时仍把安装状态保存在源码根目录，保持现有工作方式。

## 5. 维护者构建

原生包只能在对应系统生成。发布工作流：

```text
.github/workflows/native-installers.yml
```

它使用 CPython 3.10.17 和各目标的 hashed build lock，先运行包装合同测试，再
调用 `build.py` 冻结应用，最后调用：

```bash
python scripts/build_native_installer.py \
  --version 1.0.0 \
  --target <linux-x86_64|windows-x86_64|macos-arm64> \
  --app-path <native PyInstaller output> \
  --output-dir dist/native/<target> \
  --source-revision <40-character-git-sha>
```

Linux 构建还使用哈希固定的 Ubuntu `libxcb-cursor0` 输入。Windows runner 必须
提供 Inno Setup 6；macOS runner 必须提供系统 `hdiutil`。缺少工具时工作流直接
失败，不降级成 ZIP 或把其他平台产物改名。

## 6. 验证层级

1. 单元/合同测试：版本、路径、Debian 布局、Inno 文本、DMG 命令和清单。
2. 冻结应用 smoke：`--help`、Qt offscreen、原生事件循环。
3. 安装包 smoke：安装或挂载、运行已安装二进制、卸载/分离。
4. 人工发布验收：目标机器上的字体、DPI、窗口布局和真实外部查看器。
5. 公开发行：签名、公证、下载渠道和最低版本机器证据。

Hosted CI 的第 1–3 层通过，不等于第 4–5 层自动完成。发布说明必须报告真实
完成的层级。
