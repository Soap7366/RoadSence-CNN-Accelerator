# 验证入口

推荐通过python manage.py test调用，自动使用项目隔离环境。verify_package.py仅依赖标准库，检查交付SHA、额外文件、英文命名、Python语法、shell LF、模型/bit/HWH/30条golden/RTL向量身份。

离线测试包括skill脚本、自动化边界、prepare超时/取消/身份与网络分支、stream协议/预处理以及桌面mock；ARM专用测试在PC明确跳过。gui_smoke.py创建实际RoadSense桌面后退出，不连接板卡。执行输出写sim/results，导出时不包含机器生成结果；审阅用副本位于sim/evidence，摘要在report/validation_status.md。sim/evidence/execution_summary.json保留早期四组测试快照，current_validation.json记录当前五组检查，不覆盖历史记录。

acceptance.py是真实Tk与板卡验收入口：`python sim/acceptance.py --package <项目绝对路径> --mode prepare`执行准备按钮回调，`--mode desktop`执行板卡连接、16帧录像检测及播放，确认检测帧0/4/8/12和播放推进。compare_board_results.py通过`--baseline <原板端release路径>`逐项比较JPEG/INT8哈希、INT32及解码值；monitor_board.py读取板端进程以排查等待。以上需要实际板卡并交互输入密码，不能当作离线mock测试，也不属于日常启动所需步骤。已有实测摘要在sim/evidence/one_click_validation.json，详细说明在report/one_click_validation.md。

计算核心RTL通过manage.py rtl sim调用；完整AXI接口通过manage.py overlay sim调用；PS7预设通过overlay check检查。CLI返回非零表示失败，不以进程启动成功代替仿真PASS标记。

README.md和SKILL.md保留固定入口名，Windows兼容入口保留RoadSense.exe?Start.exe、Prepare.exe、Prepare.cmd；其余自有文件名采用小写英文/数字/下划线/连字符与扩展名。第三方工具产生的本机文件只留.runtime，不进入交付命名检查。
