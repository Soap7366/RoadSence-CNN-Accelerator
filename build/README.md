# 自动环境、构建与部署

统一调用项目根manage.py，可从其他工作目录用其绝对路径调用。

- setup：检查Python/Tk，创建项目内隔离环境，下载锁定wheel，以no-index安装，再做pip check和模块来源检查。
- doctor / doctor --gui：验证实际运行环境；有GUI时检查真实Tk窗口。
- test：manifest、skill、自动化、stream及桌面mock；不访问板卡。
- configure / trust：目标配置与指纹匹配信任，不存密码。
- deploy：新release上传、完整读回、身份检查、ARM库构建及测试，成功后激活配置。
- board-check --boot / smoke：显式加载镜像、执行golden与两批JPEG端到端；需真实板卡。
- rtl / overlay：跨Windows/Linux调度Vivado；产物进入.runtime新目录。
- export：导出交付文件，排除虚拟环境、配置、公钥与生成结果。

requirements_pc.txt固定PC端9项库及pip工具版本。wheel_manifest.json由setup生成，后续使用缓存前检查SHA；离线wheel必须匹配目标系统和Python版本，不能复制venv跨机器使用。完整说明见根README。

reports/overlay为找到的原始板级路由时序、资源与DRC报告；本次新构建产物不覆盖历史报告。计算核心与板级系统的资源数值不得混用。

rebuilt_overlay为本轮Vivado2023.2实际生成的bit/HWH、manifest及新路由报告。它与board/base内原镜像分开保存，尚未验证新镜像上板。`deploy --overlay build/rebuilt_overlay`先验证成对文件再部署至新release。

`overlay finish --workspace <本项目.runtime内已完成路由的目录>`用于恢复导出，不重新综合或布线，仍须通过时序、仿真、固定来源和镜像身份检查。

refresh_manifest.py仅供维护者在审核修改后更新交付清单，不作为运行时自动修复工具。正常verify发现修改应失败，不能静默重新生成清单。

Windows板级构建会先检查Windows Script Host是否可用，再启动Vivado launch_runs。综合和实现等待分别设30/60分钟上限，超时不会标记成功；超时参数单位依据[AMD wait_on_run文档](https://docs.amd.com/r/2020.2-English/ug835-vivado-tcl-commands/wait_on_run)。
