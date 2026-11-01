# RoadSense 文件整理记录

日期：2026-11-01（America/Los_Angeles）。整理基线：Git `cd1cd95`。

## 更新内容

- README统一RoadSense名称、Prepare与roadsense.exe使用入口，保留Start兼容入口，说明录像按钮、每4帧采样及当前上传校验流程。
- build、sim、board与report导航补齐准备任务、图标、验收和诊断脚本的用途；文件取舍及验证状态更新为当前结果。
- validation_status.md移除已经过时的“尚未真板部署/ARM构建/录像端到端”判断，保留“新综合镜像未上板”和物理网卡配网未实测等边界。
- source_inventory.json更新desktop.py与test_board_adapter.py的当前副本SHA；原始来源SHA、adapted及历史省略项保持原样。
- sim/evidence/current_validation.json保存当前五组离线检查；one_click_validation.json汇总此前已完成的真实板卡结果，不表示本次整理重新执行了板卡推理。
- package_manifest.json按当前交付内容更新。

## 清理内容

共删除28个可再生成或已失效的文件，8,791,953字节（约8.8 MB）：

| 内容 | 数量 | 理由 |
|---|---:|---|
| 应用目录的__pycache__字节码 | 22 | Python可自动重建，不包含虚拟环境缓存 |
| .runtime/launcher_test.pid、roadsense_launcher.pid | 2 | 仅属于已经结束的启动器测试 |
| .runtime/verify_roadsense_ui.py | 1 | 一次性UI检查脚本，仍断言旧小写名称；正式gui_smoke.py及结果保留 |
| .runtime/pynq_cnn_delivery.zip | 1 | 旧687项导出包，桌面内容早于当前版本，避免误交付 |
| .runtime/acceptance.avi、prepare_check.avi | 2 | 由验收/Prepare从随包JPEG生成的测试录像；所需时自动重建 |

这些文件不在Git或交付清单中；删除前核对过完整路径、跟踪状态及内容哈希。详细删除清单保存在本机sim/results/cleanup_removed.json。测试可能再次自动生成缓存，此类缓存不进入导出。

## 保留内容

目录结构不变。所有Python/C/RTL/Tcl/PowerShell/CMD源码、启动EXE、图标、依赖锁定、模型、golden、bit/HWH及配置合同保持整理前字节；src/desktop/records、板卡配置和known_hosts保留。

.runtime/pc_env、wheels、wheel_manifest.json保留，不让本机重新安装环境。Vivado工作目录、路由检查点、日志及诊断工具保留，支持恢复导出。sim/evidence原记录、board/evidence、build/reports、build/rebuilt_overlay及固定上游副本保留，不用新结论覆盖历史证据。

以后交付新电脑请先verify，再用manage.py export导出到新目录；不要直接分发带本机.runtime的工作目录，也不要复制本机venv作为新电脑运行环境。

## 本次验证

五组离线回归通过（skill 2项、automation 9项、prepare 13项、stream 10项通过且18项ARM专用跳过、desktop 4项），真实RoadSense窗口构造通过。本机交付清单与临时新目录导出检查均通过，覆盖708个清单条目；导出测试副本已清理。

697个非文档/清单文件与整理基线逐字节相同；622项来源记录的当前副本哈希一致，原始来源SHA未改。当前入口文档本地链接有效。核对记录在sim/results/cleanup_verification.json、cleanup_export_validation.json。本次没有重新部署板卡、加载镜像或改变网卡设置。
