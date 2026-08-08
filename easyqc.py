#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EasyQC 主启动程序
负责组装唯一的 PySide6/Qt GUI，不包含具体的界面和数据处理逻辑。
支持命令行参数直接打开 Qt QC 页面。
"""

import argparse
import sys
from pathlib import Path

EASYQC_VERSION = "1.0.0"

# Managed-runtime smoke requires exact stdout and must not initialize logging
# or any GUI module.  Keep this direct-script fast path before those imports.
if __name__ == "__main__" and sys.argv[1:] == ["--version"]:
    print(EASYQC_VERSION)
    raise SystemExit(0)

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from core.application_paths import resolve_application_state_root


# Source checkouts keep their established installation-scoped registry and
# templates beside easyqc.py.  Frozen native packages can be installed into a
# read-only application directory, so their mutable state is per-user and
# version-isolated instead of being written into /opt, Program Files or an app
# bundle.
application_state_root = resolve_application_state_root(
    source_root=project_root,
    version=EASYQC_VERSION,
    frozen=bool(getattr(sys, "frozen", False)),
)
registry_path = application_state_root / "projects.json"

# 导入日志系统
from utils.logger import clear_old_logs, log_error, log_exception, log_info

def parse_arguments(argv=None):
    """
    解析命令行参数
    """
    parser = argparse.ArgumentParser(
        description='EasyQC - 医学影像质量控制工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python3 easyqc.py                                    # 启动GUI界面
  python3 easyqc.py project module rater easyqcid      # 直接打开Qt QC页面
  
参数说明:
  project   - 项目名称
  module    - 模块名称  
  rater     - 评分者名称
  easyqcid  - 质控条目ID
        """
    )

    parser.add_argument(
        '--version',
        action='store_true',
        help='显示 EasyQC 版本并退出',
    )

    parser.add_argument(
        'args', 
        nargs='*', 
        help='可选参数：project module rater easyqcid'
    )
    
    return parser.parse_args(argv)

def main(argv=None):
    """
    主启动函数
    负责初始化和启动EasyQC应用程序
    """
    try:
        # 解析命令行参数
        args = parse_arguments(argv)

        if getattr(args, 'version', False):
            print(EASYQC_VERSION)
            return 0

        from gui_qt.application import launch_qt, launch_qt_qc

        clear_old_logs()
        launch_argv = sys.argv if argv is None else [sys.argv[0], *argv]

        if len(args.args) == 4:
            project, module, rater, easyqcid = args.args
            log_info(
                "启动 Qt QC 页面: "
                f"project={project}, module={module}, rater={rater}, easyqcid={easyqcid}"
            )
            return launch_qt_qc(
                launch_argv,
                project=project,
                module=module,
                rater=rater,
                easyqcid=easyqcid,
                registry_path=registry_path,
            )
        elif len(args.args) > 0:
            print("错误：参数数量不正确")
            print("用法：python3 easyqc.py [project module rater easyqcid]")
            print("或者：python3 easyqc.py  # 启动GUI界面")
            return 2

        log_info("启动 EasyQC Qt 应用程序")
        return launch_qt(
            launch_argv,
            registry_path=registry_path,
        )
        
    except ImportError as e:
        log_error(f"无法导入必要的模块: {e}")
        print(f"错误：无法导入必要的模块 - {e}")
        print("请确保所有依赖模块都已正确安装")
        return 1
    except Exception as e:
        log_exception(f"启动应用程序时发生错误: {e}")
        print(f"启动应用程序时发生错误：{e}")
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
