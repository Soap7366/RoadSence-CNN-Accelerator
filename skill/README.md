# PYNQ 加速器正确性校验与分层性能诊断技能包

目录 `pynq-accelerator-validation/` 可整体复制到支持 SKILL.md 的工具技能目录，或直接阅读并执行脚本。所有脚本仅需Python标准库，方法不限于CNN。

| 项目 | 适用场景 | 使用方式 | 已验证效果 | 失效条件 |
|---|---|---|---|---|
| 工作流与提示词 | 联调、等价优化、性能分析 | 读SKILL.md，再按references选择任务模板 | 来自现有工程合同和测试经验；提示词尚无独立多任务稳定性验证 | 缺少参考、合同或原始日志时结论受限 |
| 字节比较脚本 | 替换预处理、量化实现 | `python pynq-accelerator-validation/scripts/compare_bytes.py reference.bin candidate.bin` | 本包自动测试覆盖相等、首差异和长度不同 | 仅证明两个文件一致，不证明参考正确 |
| 日志检查脚本 | 单个会话单批桌面JSONL | `python pynq-accelerator-validation/scripts/check_records.py records.jsonl --profile pynq-accelerator-validation/assets/cnn_profile.json --expected-count 100` | 本包自动测试覆盖有效、乱序、混批、回读失败、数值异常 | 需适配schema；输入样本数要独立取得；日志不含run_complete，须另证整批成功 |
| 踩坑表与报告模板 | 诊断和交付 | references/pitfalls.md及assets/report_template.md | 每项标明观察来源及复验方法 | 症状相关性不能当作唯一根因 |

经验出处与已验证案例见references/cnn_case.md；本项目原始证据在 `../board/evidence/`，当前包装测试在 `../sim/results/`。技能针对通用PYNQ验证方法，附带运行案例仍绑定本CNN；尚未完成换板迁移，不声称已取得通用加分资格。
