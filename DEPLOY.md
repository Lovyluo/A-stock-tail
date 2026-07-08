# A Stock Data Deployment

本包用于把当前 `a-stock-data` 项目迁移到另一台设备运行。项目不会自动下单，所有输出仅用于人工观察和复盘。

## 1. 环境要求

- Windows 10/11 或可运行 Python 的系统
- Python 3.11 或更高版本
- 可访问腾讯、东财、同花顺、百度等公开行情接口的网络

## 2. 安装

在新设备上解压部署包后，进入项目根目录：

```powershell
cd a-stock-data
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-deploy.txt
```

如果只使用命令行 demo 流程，部分可选依赖缺失也能运行；如果要打开本地仪表盘，请确保 `streamlit` 和 `pandas` 安装成功。

## 3. 快速验证

```powershell
python -m pytest overnight_quant/tests -q
python overnight_quant/scripts/run_after_close_analysis.py --mode demo
python overnight_quant/scripts/run_scan.py --mode demo --dry-run
```

## 4. 常用命令

盘后观察池：

```powershell
python overnight_quant/scripts/run_after_close_analysis.py --mode live
```

早盘用前一交易日收盘数据回放：

```powershell
python overnight_quant/scripts/run_after_close_analysis.py --mode live --replay-previous-close
```

尾盘扫描 dry-run：

```powershell
python overnight_quant/scripts/run_scan.py --mode live --dry-run
```

启动本地仪表盘：

```powershell
python overnight_quant/scripts/run_dashboard.py
```

也可以双击根目录的 `start_dashboard.bat`。

## 5. 输出目录

运行后会自动生成这些本机目录：

- `overnight_quant/records/`
- `overnight_quant/reports/`
- `overnight_quant/backtest_outputs/`
- `overnight_quant/backtest_data/cache/`

`demo` 模式会把演示报告写入 `overnight_quant/examples/reports/` 和 `overnight_quant/examples/records/`；`live` 模式会写入正式 `records/reports` 目录。

这些目录里的内容是运行结果或缓存，不是部署必需文件。

## 6. 网络说明

东财 `push2` / `push2his` 接口偶尔会出现 `RemoteDisconnected` 或连接被关闭。项目内置了多级数据源和重试逻辑，但如果上游连续断连，系统会降级或保留缺失提示，这是为了避免把不确定数据当成可靠信号。
