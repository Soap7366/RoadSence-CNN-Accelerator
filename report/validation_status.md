# 当前验证状态与边界

日期2026-11-01（America/Los_Angeles）。本轮重新建立独立环境并验证自动化；此前借用旧vendor的测试不再作为独立复现的证明。

| 项目 | 本轮结果 | 证据范围 |
|---|---|---|
| 独立PC安装 | PASS | 新建.runtime/pc_env，Python3.12.7，官方PyPI锁定wheel，pip check通过 |
| 依赖来源 | PASS | NumPy/Pillow/OpenCV/Paramiko均位于新venv，无原项目搜索路径 |
| Tk及实际桌面构造 | PASS | 新环境创建真实Tk及Demo窗口后退出；未连接板卡 |
| skill测试 | 2项通过 | 字节相等/首差异/长度与日志损坏拒绝 |
| 自动化测试 | 9项通过 | 运行产物排除、命令引用、环境隔离、部署路径保护、异目录CLI、失败部署不激活、指纹拒绝、配置原子发布与无效新镜像在联网前拒绝 |
| stream | 28项中10通过、18跳过 | 跳过ARM专用项，不当成硬件验证 |
| 桌面适配 | 3项通过 | mock验证采样、摘要拒绝和stdin生命周期 |
| 核心整网RTL | PASS | Vivado2023.2新路径运行30样本、60个INT32，65116周期，复位/中止测试通过 |
| 核心控制RTL | PASS | 新自动工作目录通过控制顺序、故障与复位测试 |
| AXI桥协议RTL | PASS | 新自动工作目录9组测试通过 |
| PS7预设探测 | PASS | Vivado2023.2、xc7z020、PS7 5.5及50MHz，无综合/实现 |
| Linux/macOS依赖解析 | PASS | Python3.12，Linux x64、macOS13+ Intel/ARM的9项库wheel解析成功；另固定pip24.2 |
| 完整AXI整网仿真 | PASS；30样本、60个INT32、完整AXI写入/回读 | 以最终PASS tb_axi_network标记为准 |
| 完整PS7/AXI/CNN构建 | PASS | Vivado2023.2完成综合、布局布线和bit/HWH生成；setup +4.600ns，hold +0.023ns；本轮产物和报告在build/rebuilt_overlay |
| 导出后异目录重装复验 | PASS；新目录离线安装、测试和真实桌面构造通过 | 重新建venv，以原wheel缓存离线安装，不复制旧venv |

本机PC依赖：NumPy1.26.4、Pillow11.3.0、OpenCV4.11.0.86、Paramiko3.5.1、cryptography44.0.3，其他传递依赖见build/requirements_pc.txt。未使用旧obstacle-demo依赖。Python/Tcl来自正常安装的本机解释器，交付包不捆绑解释器。

平台状态：Windows x64实际执行；Linux/macOS已验证依赖解析、提供CI矩阵，尚未实际执行这些OS的测试。macOS不支持Vivado；FPGA硬件目标固定PYNQ-Z2，不能宣称任意板卡即插即用。

尚未执行：真板release部署/ARM原生库重编译/新视频端到端、新构建镜像上板、第二题目或第二板卡迁移。完整源级重建入口已找到并补入src/integration，先前报告的“缺少AXI系统重建入口”结论已修正。旧交接包包含的原始实现rpt在build/reports/overlay；本轮新报告在build/rebuilt_overlay，二者明确区分。

PS7官方预设出现DDR负DQS延迟的工具警告，预设保持官方值。布线DRC为0错误、30警告，bit写入有8条critical warning；日志完整保留，构建通过不代表DDR/板卡实测。新镜像不自动替换已使用的原镜像。

构建环境经历两次恢复：最初受限沙箱阻止Windows Script Host，已停止该工作进程并补上前置检查；随后在获准环境完成综合/布局布线，PowerShell直接捕获Vivado stderr使外层命令呈现非零，Vivado内部已输出成功并生成文件。增加overlay finish后，以Python直接捕获子进程日志，重查已有路由工程并重新导出、生成manifest，真实退出码为0，记录于sim/evidence/overlay_build.json。没有把前两次命令包装成零退出码。

本次现场输出在sim/results，构建工作目录在.runtime；两者属于机器状态，导出包排除。审阅用输出副本与摘要放在sim/evidence随包提供。完整golden/镜像及原始历史证据仍随包保留。
