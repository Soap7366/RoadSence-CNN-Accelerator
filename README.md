# RoadSense

这是 RoadSense 行驶录像碰撞预警演示软件的独立源码与运行交付包。PC端不再读取原工作区的vendor、SSH依赖、Tcl或交接包路径。Windows首次使用运行 `Prepare.exe`，准备完成后运行 `roadsense.exe`；原 `Start.exe`、`start.cmd` 入口保留。命令行统一入口是 `manage.py`，可从任意工作目录调用；Linux/macOS先setup，再执行 `sh start.sh`。

## Windows 一键准备与启动

1. 解压完整文件夹到可写目录。板卡插好已有的 PYNQ 参考系统 SD 卡、通电，用网线直连电脑；电脑可同时通过 Wi-Fi 联网。
2. 双击根目录 `Prepare.exe`（也可双击 `Prepare.cmd`）。已有可用环境会直接打开准备窗口；缺少环境时自动下载依赖，缺少 Python 时从 python.org 下载并验证签名后，在本文件夹的 `.runtime/python312` 中安装 Python 3.12.10，不修改 PATH。
3. 输入板卡实际 IP（默认 `192.168.2.99`）和密码，点击“开始准备”。无需填写电脑 IP。
4. 已能连接板卡或有线网卡已在相同网段时不修改网卡；需要配置时选择直连板卡的有线网卡，程序添加同网段 `/24` 地址，Windows 会请求管理员授权。不修改 Wi-Fi；带默认网关的有线网卡不会自动配置。准备窗口可恢复程序修改前的网卡设置。
5. 等待“准备完成”。以后双击 `roadsense.exe`（也可用 `Start.exe` 或 `start.cmd`）即可使用，软件内连接板卡时再次输入密码。

准备程序会完成首次 SSH 指纹登记、密码验证、独立版本部署、文件读回校验、板端构建、镜像加载与真实图像推理检查。首次登记依赖你输入正确地址并连接可信板卡；已有指纹发生变化时拒绝覆盖。密码只保留在当前进程内存中，不写入配置、环境变量或启动参数。已有可用部署会先检查并复用；部署检查返回错误时创建新版本，网络超时直接停止并提示，不把网络问题当成部署损坏。

窗口显示当前步骤、总用时、上传进度和等待时间，支持取消并终止相关子进程。上传/读回连续30秒无传输会中止，远程命令和每个阶段都有上限；SSH结束通知延迟时会回查板端完成记录。日志位于 `.runtime/prepare.log`。准备期间退出其他推理客户端；首次环境下载时间取决于网速，本轮实测已有部署准备约60秒，全新部署约105秒，详见 report/one_click_validation.md。

`roadsense.exe`、`Start.exe` 和 `Prepare.exe` 是 Windows 启动器，必须与整个文件夹一起保留；不需要安装 Vivado。源码位于 `build/launcher.cs`，图标位于 `build/roadsense.png` 和 `build/roadsense.ico`，可通过 `powershell -ExecutionPolicy Bypass -File build/build_launchers.ps1` 重建启动器。软件显示名为 RoadSense，入口与资源文件名保持原样。安装程序不会制作 SD 卡或替换 PYNQ 系统；板端仍需满足下文的参考环境要求。Linux/macOS 保持原命令行流程。

## 支持范围

- PC：Windows x64、Linux x64、macOS13+ Intel/Apple Silicon；Python 3.9–3.12，含正常的Tcl/Tk。平台通用代码与CI矩阵已提供，实际执行的平台见report/validation_status.md。
- FPGA：PYNQ-Z2 / Zynq-7020，PYNQ 3.1.1参考SD环境。其他板卡必须重新生成镜像和适配合同，不能复制bit直接使用。
- 构建：Vivado 2023.2的Windows/Linux安装，需安装xc7z020器件支持。macOS可运行PC软件，硬件构建需Windows/Linux。
- 需要联网安装Python依赖；安装后可使用同平台wheel缓存离线重建环境。本包不包含Python解释器、Vivado或SD系统。

## 先测试PC功能

在项目目录执行（Linux/macOS将python替换为python3）：

```sh
python manage.py setup
python manage.py test
python manage.py doctor --gui
python manage.py start
```

setup自动创建`.runtime/pc_env`、从官方PyPI下载锁定版本的wheel并安装、执行pip check、检查模块实际来源与Tcl。无须激活虚拟环境。后续命令自动使用该环境。GUI正常打开后，仍需完成下节部署才能真实推理。

虚拟环境在目标机器重新创建，不跨机器复制，符合[Python venv文档](https://docs.python.org/3/library/venv.html)的运行方式；离线wheel缓存安装依据[pip download文档](https://pip.pypa.io/en/stable/cli/pip_download/)。

若默认Python安装不完整，指定一个包含正常Tk的解释器：

```sh
python manage.py setup --python /path/to/python
```

本机已准备好独立运行环境，可直接启动；新电脑按上方 Prepare 流程准备。Python基础解释器路径属于本机状态，不是源码中的固定依赖。无Python分支也已在独立副本中实测安装私有Python3.12.10。

Linux需预先安装发行版的Tk和OpenCV运行库，例如Ubuntu：`sudo apt-get install python3-venv python3-tk libgl1 libglib2.0-0`。macOS建议使用python.org的完整Python安装。无图形桌面的服务器可运行test，不能打开Tk窗口。

离线安装（wheel目录必须匹配目标操作系统、架构和Python版本）：

```sh
python manage.py setup --offline --wheelhouse /path/to/wheels
```

已下载wheel位于`.runtime/wheels`，校验摘要位于`.runtime/wheel_manifest.json`及wheel目录内。可运行platform-check核对其他平台wheel解析，实际OS运行由CI或目标机器验证。

## 自动部署与真实推理

先准备PYNQ-Z2的正常SD系统、网络和SSH。板端Python需要已有PYNQ/XRT、NumPy和Pillow9.0.1；C编译需要gcc和libjpeg开发包。脚本先检查条件，缺失时明确失败，不改写全局PYNQ环境。系统依赖安装说明见board/README.md。

```sh
python manage.py configure --host 192.168.2.99 --username xilinx
python manage.py trust --fingerprint SHA256:实际核对过的目标主机指纹
python manage.py deploy
python manage.py board-check --boot
python manage.py smoke
python manage.py start
```

首次信任所需指纹可在板卡本地控制台执行 `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub -E sha256` 取得；trust仅在网络取得的公钥与给定指纹一致时保存。不能填示例指纹。

configure写入`board/local_config.json`，密码只交互输入，不落盘。deploy打包上传到独立release，完整压缩包SHA256读回比对后解压，再逐文件校验清单、验证模型/镜像、构建ARM原生库并运行板端测试；全部通过后才更新本地运行路径。board-check --boot会加载本包镜像，先退出其他推理客户端。smoke执行30条golden及两批JPEG推理，下载板端报告。失败会返回非零退出码并保留证据。

桌面中“连接板子”→“选择录像…”→“处理录像”→等待全部采样推理成功→“开始播放展示”→再选第二段录像。记录保存在src/desktop/records。每4帧采样1帧（从0计为0、4、8……），录像按原帧率播放，未采样帧复用最近采样结果，跳转播放也按原视频帧号匹配；界面同时显示当前帧和采样帧。该演示不提供车辆闭环控制。

## RTL与完整板级镜像构建

命令自动发现PATH中的Vivado工具，也可显式指定 `--vivado-bin /path/to/vivado/bin`：

```sh
python manage.py rtl sim --tests network gap linear control
python manage.py overlay sim
python manage.py overlay check
python manage.py overlay build
```

rtl是计算核心仿真；overlay sim包含9组AXI协议/故障测试及AXI整网30条golden。overlay check检查官方PS7预设和器件安装，不综合。overlay build先做AXI仿真，再构建PS7/AXI/CNN系统、检查路由时序、导出成对bit/HWH与manifest。所有构建在`.runtime`的新工作目录运行，命令结束会打印产物位置，不污染源目录。Vivado不支持macOS。

已有可运行镜像固定在board/base/overlays/cnn_v1。重建不承诺bit逐字节相同；重建镜像必须通过golden与真板验收，不能将新bit与旧HWH/manifest混用。新构建不会自动替换现有已验证镜像。

要验证新构建镜像，运行 `python manage.py deploy --overlay /path/to/build_workspace/artifacts`，再执行board-check --boot与smoke。deploy先验证成对镜像与模型身份，在新release中替换，原包base和旧板端release保留。

本轮实际重建的成对镜像及路由报告另存于`build/rebuilt_overlay`，可用`python manage.py deploy --overlay build/rebuilt_overlay`单独部署验收。该镜像已通过构建、时序及文件身份检查，尚未上板；默认deploy仍使用board/base内的原镜像。

Windows构建需要可用的Windows Script Host，受限沙箱可能拦截Vivado的launch_runs。脚本会预先检查并报错。若已有成功布线的本项目工作目录，导出阶段中断后可恢复，无需再次综合：

```sh
python manage.py overlay finish --workspace /path/to/this_project/.runtime/o_existing
```

finish仍会检查路由状态、setup/hold、成对文件、仿真记录与来源身份；失败返回非零。完整操作及本轮遇到的工具环境问题见[验证状态](report/validation_status.md)。

## 交付目录与导出

src含桌面、PS/native、计算核心和AXI/PS7集成源码；sim含检查与测试；build含环境/构建/部署自动化及历史原始实现报告；board含镜像、启动代码与实测证据；data含样本和模型合同；skill含复用技能；report含设计、文件取舍与协作记录。

```sh
python manage.py verify
python manage.py export --output /path/to/new_delivery
```

export按 `build/package_files.py` 定义的交付范围复制文件，`verify` 用manifest检查这一范围的完整性。不携带虚拟环境、机器配置、公钥信任文件和本机运行产物。导出前先运行verify；Windows新电脑可在导出目录双击Prepare.exe准备环境，其他平台按setup流程操作。Git保留原始字节，避免换行转换破坏golden与来源SHA。

维护时可用`python sim/rehearse_relocation.py --python /path/to/base/python`自动导出到临时新目录，离线重装、测试并创建真实桌面。需先setup准备当前平台的wheel缓存；该检查验证同平台迁移，跨操作系统实跑另由CI或目标机器完成。

入口文档：[文件取舍](report/file_inventory.md)、[验证状态](report/validation_status.md)、[一键准备实测](report/one_click_validation.md)、[本次文件整理](report/maintenance.md)、[技能包](skill/README.md)。历史性能不冒充当前实测；纯PL周期、PS检测耗时、批吞吐和视频展示帧率分开报告。
