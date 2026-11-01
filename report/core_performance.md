# 完整双任务 CNN 性能、资源与功耗估算

对象：baseline_2 QAT INT8 双任务模型，顶层 `cnn_accelerator`，器件 `xc7z020clg400-1`，50 MHz。计算范围是三个 Conv/Bias/Requant/ReLU/Pool、在线 GAP 和两个 Linear，最终输出两个 INT32。

## 已验证的速度和资源

| 指标 | 结果 | 依据 |
|---|---:|---|
| LUT | 20,017，即 **20.017 kLUT** | 布局布线，37.63% |
| FF | 18,191，即18.191 kFF | 布局布线，17.10% |
| DSP48E1 | **20** | 布局布线，9.09% |
| BRAM | **6.5 BRAM Tile** | 2 RAMB36＋9 RAMB18，4.64% |
| PE 数量 | 128，8×16 | 共用一套阵列执行卷积和双Linear |
| 时钟 | **50 MHz** | 20 ns周期，静态时序通过 |
| 建立/保持裕量 | +1.920 / +0.050 ns | 新整网 routed checkpoint |
| 完整推理周期 | **65,116** | 30个样本均相同 |
| 完整推理延迟 | **1.30232 ms** | RTL周期换算 |
| 推理吞吐率 | **767.86次/秒** | 不含主机及PS开销 |
| 有效运算吞吐率 | **4.07622 GOPS / 0.00407622 TOPS** | 1 MAC=2 operations |
| 理论阵列峰值 | 12.8 GOPS | 128 MAC/拍×50 MHz×2 |
| 整网有效PE利用率 | **31.85%** | 有效MAC / 全阵列全部运行周期 |

每次推理 Conv/Linear 共2,654,272 MAC，GAP求和、重定标、ReLU、Pool等逐元素操作不计入该MAC吞吐口径。吞吐包含内部模型参数加载和控制交接，不含图片上传、验证回读、PS反量化和sigmoid，也不是整板摄像头到输出的实测FPS。

128个PE的MAC主要映射到LUT，DSP主要用于重定标乘法；不能将20 DSP理解为只有20个PE。

| 阶段 | 周期 | 时间 |
|---|---:|---:|
| Conv1 | 11,375 | 0.22750 ms |
| Conv2 | 19,538 | 0.39076 ms |
| Conv3，GAP与其重叠 | 33,860 | 0.67720 ms |
| FC参数加载、特征复制和阵列计算 | 333 | 6.66 μs |
| 网络启动和各阶段交接 | 10 | 0.20 μs |
| 总计 | 65,116 | 1.30232 ms |

## 功耗与能效：工具估算，非实测

新增一次完整有效推理的RTL活动采集：先装载样本0，开始推理时才打开SAIF，捕获1,302,340 ns（包含末尾20 ns控制余量），图像装载不在活动窗口内。该推理仍核对两个最终INT32：collision=-16265、steering=1202，与golden一致。

读取已布线的 `build/full_network/cnn_route.dcp`，使用这份SAIF执行 Vivado `report_power`：

| 指标 | 活动辅助估算 | 无活动文件的默认估算 |
|---|---:|---:|
| 片上总功耗 | **约0.218 W** | 约0.230 W |
| 动态功耗 | 约0.110 W | 约0.122 W |
| 器件静态功耗 | 约0.108 W | 约0.108 W |
| 总功耗口径能效 | **约18.7 GOPS/W** | 约17.7 GOPS/W |
| 仅动态功耗口径能效 | 约37.1 GOPS/W | 约33.4 GOPS/W |
| 总功耗口径单次推理能量 | **约283.9 μJ** | 约299.5 μJ |
| 仅动态功耗口径单次推理能量 | 约143.3 μJ | 约158.9 μJ |
| 总功耗口径推理能效 | 约3522次/J | 约3339次/J |
| Vivado置信度 | Medium | Low |

重要边界：活动仅直接匹配6308/59951条布线网，约11%；其余网由Vivado进行概率活动推算。RTL活动不包含布线后毛刺。环境使用工具默认值，如25°C环境温度、typical工艺。采集的是一个样本，不是30个样本的平均功耗。

因此0.218 W和18.7 GOPS/W只能作为初步片上估算，不是板卡实测，不包含活跃PS、DDR、稳压损耗和其他平台功耗。仅动态功耗口径明确排除了静态功耗，不能与总功耗口径混用。精确能效需要更充分的物理活动覆盖或板级测量。

计算公式：

```
Throughput = 2 × 2,654,272 / 0.00130232 = 4.07622 GOPS
PE utilization = 2,654,272 / (128 × 65,116) = 31.85%
Estimated energy efficiency = 4.07622 / 0.218 ≈ 18.7 GOPS/W
Estimated energy/inference = 0.218 × 0.00130232 ≈ 283.9 μJ
```

## 正确性与物理验证范围

已有30次整网硬件仿真通过：1,720,320个卷积INT32、430,080个池化写值、960个GAP求和、960个GAP INT8、60个最终INT32均与golden逐项一致，另检查9,953,280个窗口字节和61,440个最终特征BRAM回读值。复位、busy隔离、结果保持和自动调度测试均通过。

静态时序已通过；当前整网尚未执行动态SDF回标，没有可上板bitstream或板级功耗测量。

## 复现和证据

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_power_activity.ps1
& 'D:\Vivado\2023.2\bin\vivado.bat' -mode batch -source scripts/report_network_activity_power.tcl -log build/full_network/power_activity.log
& 'D:\Vivado\2023.2\bin\vivado.bat' -mode batch -source scripts/report_network_power.tcl -log build/full_network/power_vectorless.log
& 'D:\programm\anaconda\envs\vision_attention\python.exe' scripts/summarize_performance.py
```

- 机器可读汇总：[performance_summary.json](../build/full_network/performance_summary.json)
- 活动辅助功耗：[power_activity.rpt](../build/full_network/power_activity.rpt)
- 默认功耗：[power_vectorless.rpt](../build/full_network/power_vectorless.rpt)
- 全网验证：[verification_summary.json](../build/full_network/verification_summary.json)
- 资源：[utilization_route.rpt](../build/full_network/utilization_route.rpt)
- 更完整的架构和控制说明：[整网实现报告](full_network.md)
