---
name: pynq-accelerator-validation
description: Validate PYNQ accelerator input and output correctness, diagnose PC/PS/PL bottlenecks, and build evidence-backed reports. Use for accelerator integration or optimization; adapt contracts for each model and board.
---

# PYNQ accelerator validation

将一次联调或优化变成可追溯的正确性与性能证据。适用于通过 PYNQ 控制 PL 的项目；不要求使用 CNN、碰撞预警或本项目寄存器布局。

## 开始前

先读 [工作流](references/workflow.md)。向用户已有材料中提取板卡、模型、预处理、传输接口、部署版本、参考结果和计时边界；缺失项标记 unknown，不猜测地址或 scale。

把任务分成离线比较、软件 mock、RTL 仿真和真实板卡测量。分别报告执行、跳过和失败；mock 通过不能作为 PL 正确性证据。只有用户任务涉及部署或上板时才操作设备。

## 执行

1. 固定输入与参考：记录模型/量化/bit/HWH 身份、输入形状、布局、舍入、输出单位。CNN 案例见 [案例](references/cnn_case.md)，其中常量仅适用于该模型。
2. 先证明正确性：优化前后最终输入逐字节比较；再比较原始输出与参考。浮点误差容限由任务明确给出，不用显示值近似替代整数一致。
3. 证明结果归属：检查批次、帧号、源摘要、输入摘要、硬件结果序号及完整结束事件。若接口没有回读或序号能力，记录缺口并选择可行替代验证，不虚构检查已通过。
4. 按 [计时口径](references/workflow.md#性能诊断) 分析同一台机器上的时间差。保留失败、慢会话和异常计数；一次只改一个主要因素。
5. 输出使用 assets/report_template.md 的报告：适用范围、实际命令、身份、参考、结果、失败、限制与复验步骤。

## 可运行工具

- `python scripts/compare_bytes.py reference.bin candidate.bin`：独立的逐字节比较，失败退出码为1。
- `python scripts/check_records.py records.jsonl --profile assets/cnn_profile.json --expected-count 100`：检查该案例的标准化逐帧合同；数量从源视频独立取得，使用者必须按自己日志字段修改 profile。缺少字段明确失败。
- 日志检查证明记录内部合同成立，不单独证明 FPGA 执行或模型准确率。对照原始回包、部署身份和 golden 才形成完整证据。

遇到异常按 [踩坑表](references/pitfalls.md) 定位。需要与模型协作时使用 [提示词模板](references/prompts.md)，不要把历史观察写成所有板卡都必须遵守的规则。
