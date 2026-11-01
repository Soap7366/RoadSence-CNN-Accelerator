# 验证入口

推荐通过python manage.py test调用，自动使用项目隔离环境。verify_package.py仅依赖标准库，检查交付SHA、额外文件、英文命名、Python语法、shell LF、模型/bit/HWH/30条golden/RTL向量身份。

离线测试包括skill脚本、自动化边界、stream协议/预处理以及桌面mock；ARM专用测试在PC明确跳过。gui_smoke.py创建实际桌面后退出，不连接板卡。执行输出写sim/results，导出时不包含机器生成结果；审阅用副本位于sim/evidence，摘要在report/validation_status.md。

计算核心RTL通过manage.py rtl sim调用；完整AXI接口通过manage.py overlay sim调用；PS7预设通过overlay check检查。CLI返回非零表示失败，不以进程启动成功代替仿真PASS标记。

README.md和SKILL.md保留要求的固定入口名，其余自有文件名采用小写英文/数字/下划线/连字符与扩展名。第三方工具产生的本机文件只留.runtime，不进入交付命名检查。
