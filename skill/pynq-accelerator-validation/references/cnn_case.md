# 已验证案例与适配边界

PYNQ-Z2 / Zynq-7020，模型 dual_task_baseline2_int8_qat@619308c77fd9。

PC传JPEG，PS原尺寸解码、灰度、Pillow等价BILINEAR至64×64、归一化和INT8量化；最终NCHW 4096字节。PL输出碰撞与转向INT32。风险由PS sigmoid，转向是标签弧度值。以上均为本案例常量。

SSH输入为44字节 `!4sII32s` 头与正文，输出JSONL。顺序 ready→BEG1/run_start→IMG1/image_result→END1/run_complete。每批协议号从0递增，视频源帧号另存。当前桌面单帧在途；历史测量窗口2。

历史验证摘要：stream的VALIDATION记录180张次真实PL推理；最终job_9cc72f8c869f批次528.377ms、18.93图/秒为历史图片框架窗口2结果。不是新目录或桌面性能承诺。预处理测试包含256灰度穷举、多尺寸RGB/L、progressive JPEG、CMYK回退及截断拒绝。ARM原生测试在PC跳过。

核心报告记录65116周期、50MHz换算1.30232ms；它是RTL计算范围。原生start_wait_ns是PS观察启动至完成，含检测开销。两者不互相替代。完整核心未完成动态SDF；功耗为工具估算。

配套脚本 profile检查的是桌面 inference.jsonl内的 event、run、frame等字段。必须先查看实际日志schema，不能把pc_summary.json直接传给JSONL检查器。

失效/重验：改变量化、预处理库、bit/HWH、寄存器、模型、输出标签定义均需重验；不把显示限幅后的角度当成原始输出。长视频内存、断网、取消重连、闲置后的第二段视频仍需最新板端验收。
