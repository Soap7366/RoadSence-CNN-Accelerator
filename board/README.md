# PYNQ-Z2部署与证据

Windows首次使用可直接运行根目录Prepare.exe，输入板卡IP和密码，自动完成身份登记、部署或复用、构建、镜像加载及真实图像检查；准备完成后运行RoadSense.exe。命令行仍可通过manage.py configure、trust、deploy、board-check --boot、smoke完成流程。远程默认基目录/home/xilinx/pynq_cnn_delivery；deploy只接管带本包标记的目录或创建全新目录，拒绝已有未标记目录和旧项目目录。每次上传一个新release，失败保留原运行配置。

前提：正常PYNQ-Z2 SD系统（参考PYNQ3.1.1）、SSH和网络、/usr/local/share/pynq-venv/bin/python3、PYNQ/XRT、NumPy、Pillow9.0.1。不得将PC端requirements_pc.txt安装到板卡。

板端缺少编译工具时，由板卡管理员一次性安装：

```sh
sudo apt-get update
sudo apt-get install -y build-essential libjpeg-dev
```

board/setup.py检查实际架构、Python依赖、模型和镜像。Pillow版本不一致会明确失败；不要在全局PYNQ环境随意升级以绕过检查，应按该SD环境准备或先做逐字节等价验证。SD系统、网络和SSH身份属于目标机器前提，无法由PC离线脚本凭空提供。

board-check --boot会加载PL，需要sudo，并先退出其他使用PL的程序；Prepare向导也会调用该步骤。脚本不安装systemd服务、不改旧版本启动配置。板卡重启后可重新运行Prepare或board-check --boot检查加载。

base包含现有镜像与启动基础。evidence保留历史成功、失败和慢会话；其中旧路径/版本保持原样供追溯，不是新包部署命令。manifest的board_verified=false是原构建声明，不改写历史以冒充新验收。

新镜像由manage.py overlay build生成在.runtime独立目录，需保持bit、HWH和manifest配对并执行真板golden，不自动替换base。
