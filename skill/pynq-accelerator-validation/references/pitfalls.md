# 从实际观察提炼的排查表

| 症状/触发 | 排查顺序 | 修复与复验 | 来源及边界 |
|---|---|---|---|
| Python3.9导入SSH依赖报_rust DLL错误 | 解释器版本→wheel ABI→搜索路径 | 使用匹配解释器安装依赖，再执行导入及连接检查 | 桌面交接的依赖混用；不是所有DLL错误都同因 |
| SSH服务意外收到EOF | exec_command返回的stdin对象生命周期→通道关闭位置 | 保留stdin/stdout/stderr整个会话；mock复验 | 桌面适配器及其生命周期测试 |
| 视频抽帧后协议拒绝 | 视频源帧号→连续协议帧号→run映射 | 分别保存源帧号与协议号，协议从0连续 | 当前IMG1合同，其他协议可不同 |
| 优化预处理后结果改变 | 解码→灰度→resize舍入→量化逐级比较 | 最终输入逐字节一致后再测速度 | native/reference测试；不能随意换成OpenCV resize |
| 风险或转向显示不合理 | 原始INT32→scale→单位→sigmoid次数→符号 | 风险只sigmoid一次；本案例rad转deg，正左负右 | 本模型核查，不适用于任意DroNet标签 |
| 声称PL为固定1.30232ms | 检查数值来自RTL周期还是当前回包 | 分开报告仿真换算和PS启动检测计时 | 交接修正的性能口径 |
| 发送长时间阻塞 | PS处理时间→发送窗口→网口CRC/RX→失败日志 | 受控改变单项并复验；保留慢会话 | 历史job_58d4ffe66e1d；相关性不是唯一因果证据 |
| 重连后结果串批 | 会话ID→run/frame→旧线程/通道→完成事件 | 关闭旧会话，使用新应用会话标识 | 桌面/stream合同；没有实现断点续传 |
| 复制仓库仍不能运行 | 相邻路径→未跟踪依赖→板端部署→known_hosts | 显式依赖和部署步骤，干净环境复验 | 原工作区存在外部vendor、公钥及Tcl依赖 |

证据在提交包 report/source_handoff.md、report/stream_test_report.md、src/stream/tests/ 和 board/evidence/。skill独立分发时应同时附相关实验摘要或在报告中标明证据未随包提供。
