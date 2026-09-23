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

### 5.1 本机 Ubuntu 打包目录

打包代码和依赖锁文件保留在 `easyqc/` Git 仓库中，大的构建环境放在它旁边的
工作区 `build/`，不再依赖 `Tmp/` 中的运行环境：

```text
ProjectEASYQC/
  easyqc/
    build.py                    # 既有构建实现
    build_linux.sh              # 本机 Linux x86_64 便捷入口
    packaging/locks/            # 版本化、带哈希的依赖锁文件
    dist/                      # 构建成品，位置不变
  build/linux-x86_64/
    venv/                      # Python 构建虚拟环境
    uv-cache/                  # 依赖下载/解包缓存
    deps/                      # 官方 libxcb-cursor0 deb 输入
    mplconfig/                 # 构建环境配置缓存
```

在这台机器上重新生成 Linux 可执行程序目录：

```bash
cd /home/ubuntu/homes/LuoChongjing/ProjectEASYQC/easyqc
bash build_linux.sh --version 1.0.0
```

无需激活环境。入口从自身位置定位源码和旁边的 `build/`，检查环境和 deb 后
调用原有 `build.py`；不自动安装依赖，不默认传入 `--clean` 或 `--skip-smoke`。
输出仍为 `easyqc/dist/EasyQC-v1.0.0-linux-x86_64/`，不是 `.deb` 安装包。
构建仍使用原有的短期工作目录 `easyqc/build/`，它与保存环境的工作区
`ProjectEASYQC/build/` 是两个不同目录。

**注意：同版本构建会替换同名输出目录，必要时先备份旧产物。**
`--clean` 还会清空整个 `easyqc/dist/`，日常重建不要随意添加。
这个便捷入口用于本机常规构建；严格发布的 `--release-input` 路线仍直接调用
`build.py`，不能混用该入口自动提供的 `--linux-cursor-deb` 参数。

环境与缓存不纳入 `easyqc/` Git 仓库。2026-09-16 本机迁移后，两者连同
deb/配置缓存的磁盘占用合计约 384 MiB（验证运行后、同文件系统硬链接去重的 `du` 结果）；
后续缓存增长会改变这个数值。该虚拟环境仍使用本机已有的 CPython 3.10.17
基础解释器，不是可复制到其他电脑直接使用的独立 Python 安装。

### 5.2 在新的 Linux x86_64 工作区准备环境

先准备 `uv` 和 **CPython 3.10.17**。从 `easyqc/` 目录运行以下步骤；将示例
解释器路径替换为实际路径。仅在目标虚拟环境尚不存在时创建，不直接覆盖已有
环境。所有新环境和依赖缓存均位于旁边的 `build/`：

```bash
mkdir -p ../build/linux-x86_64/deps ../build/linux-x86_64/mplconfig
uv --cache-dir ../build/linux-x86_64/uv-cache venv \
  --python /absolute/path/to/python3.10 --no-python-downloads --relocatable \
  ../build/linux-x86_64/venv
uv --cache-dir ../build/linux-x86_64/uv-cache pip sync \
  --python ../build/linux-x86_64/venv/bin/python --require-hashes \
  packaging/locks/python-3.10.17/linux-x86_64/build.txt
curl --fail --location \
  https://archive.ubuntu.com/ubuntu/pool/universe/x/xcb-util-cursor/libxcb-cursor0_0.1.1-4ubuntu1_amd64.deb \
  --output ../build/linux-x86_64/deps/libxcb-cursor0_0.1.1-4ubuntu1_amd64.deb
sha256sum ../build/linux-x86_64/deps/libxcb-cursor0_0.1.1-4ubuntu1_amd64.deb
```

deb 的预期 SHA-256 为
`c9b5d1ad4af57397b1bd77e0a92750e34419def134c0282a0836ae9efc07cf64`；
`build.py` 构建前还会强制校验。这里不将 deb 安装进系统，也不使用 `sudo`。
先用 `bash build_linux.sh --help` 检查入口，再执行实际构建命令。
其他机器仍需满足前文的系统动态库和原生验证要求。

## 6. 验证层级

1. 单元/合同测试：版本、路径、Debian 布局、Inno 文本、DMG 命令和清单。
2. 冻结应用 smoke：`--help`、Qt offscreen、原生事件循环。
3. 安装包 smoke：安装或挂载、运行已安装二进制、卸载/分离。
4. 人工发布验收：目标机器上的字体、DPI、窗口布局和真实外部查看器；外部
   查看器测试必须使用安装包启动的 EasyQC，而不能用源码模式代替，并检查
   FreeSurfer/Qt 类程序未继承 EasyQC 私有动态库和插件路径。
5. 公开发行：签名、公证、下载渠道和最低版本机器证据。

Hosted CI 的第 1–3 层通过，不等于第 4–5 层自动完成。发布说明必须报告真实
完成的层级。
