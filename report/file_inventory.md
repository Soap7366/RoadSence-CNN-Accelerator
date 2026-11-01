# 最终文件取舍与复现入口

本包软件名为RoadSense，Windows入口为Prepare.exe准备、RoadSense.exe使用，Start.exe/start.cmd保留；命令行入口是manage.py。运行文件来自最终桌面、stream v0.3.0、v0.7.0镜像启动基础、SystolicCNN计算核心，以及旧交接包中的pynq_axi_integration_20261029板级集成源码。原目录不修改。

| 内容 | 新位置 | 取舍理由 |
|---|---|---|
| 最终Tk界面与后台适配 | src/desktop | 保留RoadSense界面、后台适配与4项mock测试；去掉网页原型和本机vendor搜索 |
| PS服务、协议、量化、原生库 | src/stream | 保留最终算法、合同和测试；共享配置连接桌面、测量与部署 |
| 计算核心RTL、向量、testbench | src/core | 支持整网、GAP、双FC及接口回归；保持原始golden文件字节 |
| AXI桥、PS7预设、BD构建与AXI仿真 | src/integration | 本轮补查原交接包后找到，已收录；先前“缺少板级入口”的判断作废 |
| 固定上游17个文件 | src/integration/upstream | 原来源SHA需逐字节保留；与src/core的对应文件仅换行差异，但不可用归一化文件冒充原SHA |
| 板级历史实现rpt | build/reports/overlay | 从同一集成工程复制原始时序、资源和DRC报告，不再只有Markdown摘要 |
| 本轮板级构建镜像及报告 | build/rebuilt_overlay | 由当前源码实际综合/布线后生成，单独保存；尚未上板，不替换原镜像 |
| bit/HWH、30条硬件golden、boot基础 | board/base | 使用现有固定镜像运行；重建镜像先独立验证，不自动替换 |
| 历史测量JSON及失败/慢会话 | board/evidence | 为已有结论保留证据，不属于本次复测 |
| 3张JPEG、模型结构/量化/来源 | data | 支持快速smoke，不带全量训练/视频数据集 |
| 工作流、校验脚本、提示词、踩坑表 | skill | 提炼为其他PYNQ题目可适配的方法 |
| 自动环境、构建、部署、检查 | manage.py、build、sim | 不要求手动设置旧路径，不要求WinSCP逐文件部署 |
| 启动器与品牌资源 | 根目录EXE、build/roadsense.png与.ico | 同时保留RoadSense入口、旧Start兼容入口与Prepare；图标为用户指定图片 |

不收录：旧桌面网页、stream v0.1/v0.2整包、全部候选模型、旧vendor/ssh_vendor/ssh_py39、解释器、历史回退整包、已有桌面records和大视频。旧目录原地保留。

source_inventory.json记录复制来源、原始SHA、当前副本SHA及adapted字段。package_manifest.json覆盖交付文件。机器状态、密码、公钥信任配置、虚拟环境和生成结果不进入导出包。Git禁用自动换行转换，避免跨系统checkout破坏固定来源和golden校验。

适配点包括：统一运行配置、移除旧依赖注入、跨平台Python调度、远程release路径、Vivado工作目录及集成源码英文路径。硬件/量化逻辑保持原定义；集成RTL默认参数路径改名，参数文件内容不改。

本机.runtime/pc_env、wheels和wheel_manifest.json保留以便直接运行和离线复建；板卡本地配置与known_hosts保留。已有路由工程及构建日志保留，支持overlay finish恢复导出。仅删除确认过时的导出ZIP、一次性测试脚本、失效PID文件、自动生成的测试录像和应用字节码缓存，清单见maintenance.md。诊断/验收脚本、失败记录、原始报告和第三方固定副本不因“重复”而删除。
