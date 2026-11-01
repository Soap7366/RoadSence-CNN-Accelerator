# PYNQ-Z2 CNN碰撞预警展示系统：项目全流程交接

日期：2026-10-31（America/Los_Angeles）。本文件为当前软件接力入口；早期报告是对应时点证据，不代表最新界面或实时板端状态。

## 1. 项目简介与定位

项目在PYNQ-Z2（Zynq-7020）上实现INT8双任务CNN加速器。单张图像得到0～1碰撞风险分数和转向标签值。当前用本地行驶视频模拟输入，PC桌面展示风险与建议转向，重点展示真实PL推理及性能；没有实际车辆闭环，不宣称已完成自动驾驶系统。

当前可用链路：连接板卡并初始化 → PC选择/解码视频 → 固定每2帧采样并JPEG编码 → SSH传给PS → PS完整预处理 → PL真实CNN → PS后处理 → PC校验、保存所有采样结果 → 原速播放视频并显示最近采样结果。

只有全部采样处理成功才启用播放。展示允许复用采样值，不把每个显示帧都描述为独立推理。一次SSH初始化支持多批视频。播放、跳转和回看使用当前视频会话映射。

## 2. 工作区目录及依赖关系

| 根目录项目 | 用途 | 当前是否需要 |
|---|---|---|
| obstacle-demo/ | 当前Tk桌面软件：入口、视频处理适配和播放展示 | 主入口 |
| systolic-cnn-stream-v0.3.0/ | 当前PC/PS通信、预处理、PL控制及测量框架 | 必需 |
| systolic-cnn-v0.7.0/ | 配套板端基础软件/硬件环境及既有启动部署 | 板端依赖，不能随意移除 |
| SystolicCNN/ | CNN RTL、仿真、性能证据和硬件交接 | 硬件开发和追溯；桌面播放不直接运行其RTL |
| SystolicCNN-Handoff-v0.9.5/ | 既有运行时、SSH公钥、原依赖和交接包 | 当前仍依赖部分路径 |
| data/ | 本地视频/测试JPEG；check_sync.py用其中DSCN2571_30fps.mp4 | 测试与演示素材，不随软件Git交付 |
| FPGA_CNN-main/ | 训练工程和模型导出，用于追溯标签/量化定义 | 分析参考，不参与桌面运行 |
| model-source-review/ | 本次从同一GitHub仓库取得的源码核查副本 | 参考副本，不参与运行，无需双份同时维护 |
| systolic-cnn-stream-v0.1.0/、v0.2.0/ | 历史流式版本及测试记录 | 保留回退，不是当前入口 |
| SystolicCNN-Desktop-v0.8.0/ | 旧图片/目录客户端 | 历史工具，不是视频展示入口 |
| experiments/、version_archives/ | 历史实验/归档 | 证据和回退 |
| Start-CNN.cmd | 原JPEG批次测量入口 | 与obstacle-demo/Start.cmd用途不同 |
| 软件进度与待办交接_2026-10-31.md | 本轮界面集成前的软件状态 | 历史快照，已被部分新功能更新 |

IDE可能仍显示已不存在的PC发送窗口优化实测.md；当前发送窗口扩展实验已回退，不以残留页签判断功能。

## 3. 桌面文件和运行环境

主文件：desktop.py（Tk界面和播放）、board_adapter.py（后台SSH适配）、Start.cmd（Windows启动器）。详细清单见obstacle-demo/README.md。

Start.cmd优先本机%LOCALAPPDATA%/Programs/Python/Python39/pythonw.exe，找不到再使用PATH中的pythonw。主依赖为NumPy、OpenCV、Pillow、Tk及Paramiko。当前视频依赖来自本地vendor/，Python3.9 SSH依赖来自ssh_py39/。requirements-ssh-py39.txt锁定兼容版本；ssh_vendor/是旧尝试目录，现代码不用，未删除以避免误删资料。

不要将Python3.12/3.13的二进制扩展混入Python3.9。此前_rust DLL错误就是环境不兼容。board_connection.py通过PSCNN_SSH_VENDOR可选路径选择SSH依赖，桌面适配器设置为ssh_py39；原命令行默认依赖保持原路径。Tcl可使用交接包中的既有库作为本工作区补充。

vendor、ssh_py39、records及数据未入桌面Git。当前修改和新增文件尚未提交，需接手后审核保存；不要认为仅复制Git仓库就包含全部可运行依赖。尚无独立EXE发布包。

## 4. 运行步骤

1. 板卡上电、网络可达并已加载对应PL镜像；当前地址192.168.2.99，SSH用户xilinx，端口22。
2. 双击obstacle-demo/Start.cmd。填写IP和SSH/sudo密码，连接；密码不落盘，不强制切换输入法。
3. 等待连接与板端初始化。进度条分别展示活动动画，完成阶段才填满；动画不代表真实百分比。
4. 选择视频并处理。先扫描实际可解码帧数，再重新解码，每2帧选一帧，以JPEG质量90编码。PC不执行模型灰度/缩放/量化。
5. PS/PL处理所有采样图，PC显示百分比并保存完整记录。收到run_complete后启用播放。
6. 进入展示：视频左下角转向仪表，右侧风险条；可暂停/跳转/返回。未采样帧沿用它之前最近的采样结果。
7. 返回入口，选择另一视频，再处理；复用同一次初始化。SSH启用20秒保活，无60秒空闲读取超时，但逐次等待响应仍有超时。
8. 取消、任务故障或实际断网后重新连接。关闭程序会关闭通道；当前不是完整的正常BYE报告下载流程，勿假设每次关闭均有板端成功汇总报告。

## 5. PC—PS—PL接口与关键文件

PC复用stream的tools/board_connection.py和tools/client_images.py中的Receiver。板端脚本为/home/xilinx/systolic-cnn-stream-v0.3.0/tools/serve_images.py，解释器/usr/local/share/pynq-venv/bin/python3，经sudo启动，保留stdin/stdout/stderr整个会话。

桌面参数：golden=0、ack-words=1、workers=2、preprocess=native、full-wire-report启用。当前桌面单帧在途，原测量客户端窗口2不能作为桌面吞吐承诺。

协议不是HTTP：SSH stdin传44字节!4sII32s头及JPEG正文，stdout为UTF-8 JSON Lines。时序为ready → BEG1/run_start → 连续IMG1/image_result → END1/run_complete，可再开始下一run。协议帧号每批从0连续，视频帧号单独记录。不能把跳帧的视频帧号直接作为协议编号。

PS使用pscnn/fast_preprocess.py及native/exact_gray.c、exact_resize.c：原尺寸JPEG解码、灰度、Pillow等价BILINEAR至64×64、归一化和INT8量化。输入4096字节、单通道NCHW，模型固定。不要擅自改变预处理、量化、bit/HWH或寄存器约定。

PL控制：pscnn/native_session.py、native/native_frame.c；默认1024次逐字写入计数确认、4096字节完整回读、身份及结果序号验证。桌面检查run/frame、源SHA、回读、计数、字节数、连续序号与数值有效性。后台线程通过事件队列通知Tk主线程，不能直接操作Tk。

## 6. 输出语义与性能口径

模型dual_task_baseline2_int8_qat@619308c77fd9。风险已由PS sigmoid，不再重复sigmoid。转向INT32乘0.000048765890998470586为标签弧度值，再乘180/pi显示度；用户确认正左负右。显示默认±90度，超范围限幅，原始日志不改。高风险不要求大转角：道路驾驶转向和碰撞是不同标签任务，不是完整绕障规划器。

模型源码核查仓库：https://github.com/Soap7366/FPGA_CNN 。本地核查提交619308c77fd9af7d03bc89c494a33dda5d07386d；量化参数、网络结构和golden_samples.csv与SystolicCNN清单SHA匹配。训练SteeringDataset直接读取angle；评估以rad计算后转换deg，不能套原DroNet归一化值乘90度。quantization_spec.md与硬件旧清单哈希不一致，不能说所有导出文件完全相同。

性能详情可折叠、纵向滚动：

| 显示项 | 来源 | 边界 |
|---|---|---|
| 板端初始化 | ready.startup_ns | 板端进程启动到ready，不含SSH连接 |
| 数据传输 | timing.body_read_ns均值及JPEG总字节/总正文读取时间 | 含SSH缓冲影响，非物理网速/跨机单向延迟 |
| PS预处理 | timing.preprocess_ns逐图均值 | 现有完整预处理实现 |
| PL 计算耗时（含检测开销） | native.start_wait_ns逐图均值 | PS侧实测，含启动检查和完成检测，排除输入写入/回读/最终输出读取 |

不再显示固定1.30232ms。该历史值是65116周期/50MHz的RTL折算参考，不能冒充本视频实测。当前未新增PL周期计数器、未改硬件镜像；纯PL不含PS的逐图实测仍需硬件计数支持。历史18.93图/秒为原图片测量框架窗口2结果，不是当前桌面性能。

## 7. 数据保存与版本

obstacle-demo/records/<session_id>_inference.jsonl：完整板端回包、视频/协议帧号、run、SHA、角度、风险、JPEG字节量及PC发送计时。换视频产生新会话。日志立即逐条写入，不仅保存显示的帧。update_data外部测试接口还可能生成<session_id>.jsonl，两者用途不同。

当前内存保存当次全部采样结果供回看；长视频仍会占较大内存。接收器不再重复保留全部历史image_result。未实现跨进程日志重放、取消后续传、摄像头和独立打包。日志可能包含失败批次部分结果，不应当作完整成功会话。

obstacle-demo和stream是不同本地Git仓库。源码依赖修改在stream/tools/board_connection.py：支持内存密码参数和可选SSH依赖路径。此次未修改模型或板端算法部署；本地修改不等于已经部署上板。

## 8. 检查结果和接力事项

本次检查并修正：README及说明与功能不一致、旧固定PL性能描述、接收器重复历史回包占用。保留早期网页和不使用的依赖目录，不做无依据删除。

已执行：Python语法检查；test_board_adapter.py三项通过；check_sync.py通过（真实桌面窗口、相邻data测试视频）。此前真实视频结果日志可用于回溯，但本次未重新连接板卡，不构成最新端到端硬件验收。

建议下一轮：

1. 板端连续处理两段视频，覆盖播放闲置超过60秒后再次处理、快速换视频、取消重连、退出后设备锁释放。
2. 测试长视频内存、磁盘满/无权限、网络中断和批次失败；保留错误与慢会话。
3. 检查不同DPI和小窗口下表格、滚动、仪表位置以及各阶段进度；未知阶段动画不是精确任务比例。
4. 将接收器从测量脚本抽成独立模块，完善EOF快速失败和正常BYE退出/报告下载；进一步限制服务全会话记录内存。
5. 打包匹配Python/SSH/视频依赖及公钥，消除相邻交接包路径依赖，增加日志重放和单独诊断日志。
6. 审核并提交本地改动，安排远程或离线备份。根目录旧版本标记可能仍指较早提交，应另行对齐实际Git与部署哈希。

## 9. 进一步阅读

- obstacle-demo/README.md：当前桌面文件与操作。
- obstacle-demo/转向与性能说明.md：标签与计时定义。
- systolic-cnn-stream-v0.3.0/软件设计交接文档.md：详细协议/异常/硬件合同。
- systolic-cnn-stream-v0.3.0/最新测试报告.md：历史原版真实推理证据。
- SystolicCNN/docs/full_network.md、performance_summary.md：硬件计算与RTL证据。

发生文档冲突时，先对照当前代码、当次原始回包和部署身份，不把历史性能清单当成实时板卡状态。


